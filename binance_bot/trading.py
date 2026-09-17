"""Trading decisions and lifecycle, independent of Binance and Telegram clients."""
from decimal import Decimal
import logging
import threading
import time

from .exchange import ExchangeError
from .models import ActiveOrder, EntryMode, Side

log = logging.getLogger(__name__)
TERMINAL = {"FILLED", "CANCELED", "REJECTED", "EXPIRED", "EXPIRED_IN_MATCH"}


def protection_prices(average: Decimal, side: Side, percent: Decimal):
    distance = percent if side == Side.LONG else -percent
    return average * (1 + distance), average * (1 - distance)


class TradingService:
    def __init__(self, config, exchange, notify=lambda message: None, *, clock=time.monotonic, wait=None):
        self.config = config
        self.exchange = exchange
        self.notify = notify
        self.clock = clock
        self.stop = threading.Event()
        self.wait = wait or self.stop.wait
        self.wake = threading.Event()
        self._lock = threading.RLock()
        self._symbol_locks = {}
        self._active = {}
        self._touched = set()

    def _symbol_lock(self, symbol):
        with self._lock:
            return self._symbol_locks.setdefault(symbol, threading.RLock())

    def alert(self, message):
        log.info(message)
        try:
            self.notify(message)
        except Exception:
            log.warning("Notification delivery unavailable")

    def execute(self, signal):
        with self._symbol_lock(signal.symbol):
            return self._execute(signal)

    def _execute(self, signal):
        if self.stop.is_set():
            return None
        with self._lock:
            if any(ctx.signal.symbol == signal.symbol and ctx.filled == 0 for ctx in self._active.values()):
                self.alert(f"[{signal.symbol}] Entry still awaiting confirmation; skipped")
                return None
        filters = self.exchange.filters(signal.symbol)
        deadline = self.clock() + self.config.order_lifetime_sec
        next_wait_alert = None
        while not self.stop.is_set():
            mark = self.exchange.mark_price(signal.symbol)
            if signal.mode == EntryMode.CMP or signal.bounds[0] <= mark <= signal.bounds[1]:
                break
            now = self.clock()
            if now >= deadline:
                self.alert(f"[{signal.symbol}] Entry range timed out")
                return None
            if self.config.verbose_alerts and (next_wait_alert is None or now >= next_wait_alert):
                self.alert(f"[{signal.symbol}] Waiting for entry range")
                next_wait_alert = now + 30
            self.wait(1)
        else:
            return None
        price = Decimal(filters.price(mark))
        quantity = filters.size(self.config.trade_amount * self.config.default_leverage, price)
        if self.config.dry_run:
            tp, sl = protection_prices(price, signal.side, self.config.tpsl_pct)
            self.alert(f"[{signal.symbol}] DRY_RUN MARKET qty={quantity} near {price}; TP={filters.price(tp)} SL={filters.price(sl)}")
            return tp, sl
        if self.stop.is_set():
            return None
        self.exchange.leverage(signal.symbol, self.config.default_leverage)
        result = self.exchange.entry(signal, quantity)
        context = ActiveOrder(signal, result["orderId"])
        with self._lock:
            self._active[context.order_id] = context
            self._touched.add(signal.symbol)
        deadline = self.clock() + self.config.order_lifetime_sec
        while not self.stop.is_set() and self.clock() < deadline:
            try:
                if self.reconcile(context):
                    return context
            except ExchangeError:
                self.alert(f"[{signal.symbol}] Fill/protection query unavailable; retaining order context")
            self.wait(1)
        # The runtime monitor retains unresolved orders rather than losing a late fill.
        self.alert(f"[{signal.symbol}] Entry confirmation pending; monitor will reconcile")
        return context

    def reconcile(self, context):
        """One serialized fill path for polling and websocket-triggered reconciliation."""
        symbol = context.signal.symbol
        with self._symbol_lock(symbol):
            order = self.exchange.order(symbol, context.order_id)
            quantity = Decimal(str(order["executedQty"]))
            status = order["status"]
            if quantity > 0 and status not in TERMINAL:
                self.exchange.cancel_order(symbol, context.order_id)
                order = self.exchange.order(symbol, context.order_id)
                quantity = Decimal(str(order["executedQty"]))
                status = order["status"]
            if status not in TERMINAL:
                return False
            if quantity <= 0:
                with self._lock:
                    self._active.pop(context.order_id, None)
                self.alert(f"[{symbol}] Entry {status}, no fill")
                return True
            average = Decimal(str(order.get("avgPrice", "0")))
            if average <= 0:
                quote = Decimal(str(order.get("cumQuote", "0")))
                average = quote / quantity
            if not average.is_finite() or average <= 0:
                raise ExchangeError("Filled average is unavailable; refusing mark-price substitution")
            context.average = average
            # Query before any attachment, including after an ambiguous prior write.
            self.ensure_protection(context, filled_quantity=quantity)
            context.filled = quantity
            context.protection_until = self.clock() + 600
            self.alert(f"[{symbol}] Filled qty={quantity} average={average}")
            return True

    def cleanup_if_flat(self, symbol):
        if self.config.dry_run:
            return False
        with self._symbol_lock(symbol):
            with self._lock:
                if any(ctx.signal.symbol == symbol and ctx.filled == 0 for ctx in self._active.values()):
                    return False
            snapshot = self.exchange.position(symbol)
            if snapshot.flat:
                self.exchange.cancel_all(symbol)
                with self._lock:
                    for key in [key for key, ctx in self._active.items() if ctx.signal.symbol == symbol]:
                        del self._active[key]
                    self._touched.discard(symbol)
                return True
            return False

    def ensure_protection(self, context, *, filled_quantity=None):
        if self.config.dry_run:
            return False
        signal = context.signal
        with self._symbol_lock(signal.symbol):
            position = self.exchange.position(signal.symbol)
            orders = self.exchange.open_orders(signal.symbol)
            # Both reads must succeed. No mutation follows unknown position/orders.
            quantity = position.quantity(signal.side)
            if filled_quantity is not None:
                quantity = min(quantity, filled_quantity)
            if quantity <= 0:
                return False
            tp, sl = protection_prices(context.average, signal.side, self.config.tpsl_pct)
            filters = self.exchange.filters(signal.symbol)
            close_side = "SELL" if signal.side == Side.LONG else "BUY"
            matching = [order for order in orders if order.get("side") == close_side
                        and order.get("positionSide", "BOTH") in {"BOTH", signal.side.value}]
            has_tp = any(order.get("type") == "LIMIT" and filters.price(order.get("price", 0)) == filters.price(tp)
                         for order in matching)
            has_sl = any(order.get("type") in {"STOP", "STOP_MARKET"} for order in matching)
            if not (has_tp and has_sl):
                self.exchange.protect(signal, quantity, tp, sl, place_tp=not has_tp, place_sl=not has_sl)
                # Re-query to verify, but never repeat an unconfirmed write in this call.
                orders = self.exchange.open_orders(signal.symbol)
                matching = [order for order in orders if order.get("side") == close_side
                            and order.get("positionSide", "BOTH") in {"BOTH", signal.side.value}]
                has_tp = any(order.get("type") == "LIMIT" and filters.price(order.get("price", 0)) == filters.price(tp)
                             for order in matching)
                has_sl = any(order.get("type") in {"STOP", "STOP_MARKET"} for order in matching)
            if not (has_tp and has_sl):
                self.alert(f"[{signal.symbol}] Protection not confirmed")
            return has_tp and has_sl

    def on_user_update(self, message):
        # Websocket is a wakeup hint, never a second order-writing path.
        if message.get("e") in {"ORDER_TRADE_UPDATE", "ACCOUNT_UPDATE", "ALGO_UPDATE"}:
            self.wake.set()

    def monitor_once(self):
        with self._lock:
            contexts = list(self._active.values())
            symbols = list(self._touched)
        for context in contexts:
            if self.stop.is_set():
                return
            try:
                with self._symbol_lock(context.signal.symbol):
                    if context.filled == 0:
                        self.reconcile(context)
                    elif self.clock() < context.protection_until:
                        self.ensure_protection(context)
            except ExchangeError:
                self.alert(f"[{context.signal.symbol}] Monitor state unknown; no inferred cleanup")
        for symbol in symbols:
            if self.stop.is_set():
                return
            try:
                self.cleanup_if_flat(symbol)
            except ExchangeError:
                self.alert(f"[{symbol}] Cleanup deferred: exchange state unavailable")

    def monitor(self):
        while not self.stop.is_set():
            self.wake.wait(5)
            self.wake.clear()
            if not self.stop.is_set():
                self.monitor_once()

    def shutdown(self):
        self.stop.set()
        self.wake.set()
