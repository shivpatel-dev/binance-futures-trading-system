"""Binance transport and exchange precision. No clients are created on import."""
from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN, ROUND_UP
import threading
import time

from .models import Position, Side


class ExchangeError(RuntimeError):
    """Remote failure or untrustworthy remote state (never an empty success)."""


class OrderRejected(ExchangeError):
    def __init__(self, code):
        self.code = code
        super().__init__(f"Order rejected (code {code})")


def retry_call(operation, *, retryable, attempts=3, backoff=0.6, sleep=None):
    sleep = sleep or time.sleep
    for attempt in range(attempts):
        try:
            return operation()
        except Exception as error:
            if not retryable(error) or attempt == attempts - 1:
                raise
            sleep(backoff * 2 ** attempt)


def round_to_step(value: Decimal, step: Decimal, *, up=False) -> Decimal:
    if step <= 0:
        raise ValueError("Exchange step must be positive")
    return (value / step).to_integral_value(rounding=ROUND_UP if up else ROUND_DOWN) * step


@dataclass(frozen=True)
class Filters:
    tick: Decimal
    step: Decimal
    min_qty: Decimal
    min_notional: Decimal

    def price(self, value) -> str:
        return format(round_to_step(Decimal(str(value)), self.tick), f".{max(0, -self.tick.normalize().as_tuple().exponent)}f")

    def quantity(self, value) -> str:
        return format(round_to_step(Decimal(str(value)), self.step), f".{max(0, -self.step.normalize().as_tuple().exponent)}f")

    def size(self, notional: Decimal, price: Decimal) -> Decimal:
        if price <= 0 or notional <= 0:
            raise ValueError("Sizing requires positive price and notional")
        requested = round_to_step(notional / price, self.step)
        # Minimums must round UP; rounding down can still violate the filter.
        minimum = round_to_step(max(self.min_qty, self.min_notional / price), self.step, up=True)
        return max(requested, minimum)


class BinanceExchange:
    def __init__(self, client, config):
        self.client = client
        self.config = config
        self._metadata = None
        self._dual = None
        self._lock = threading.RLock()
        self._websocket = None

    def _call(self, method, *, mutation=False, **kwargs):
        from binance.exceptions import BinanceAPIException, BinanceOrderException, BinanceRequestException
        from requests.exceptions import ConnectionError, Timeout

        if mutation and self.config.dry_run:
            raise ExchangeError("Exchange mutation blocked by DRY_RUN")

        def retryable(error):
            if isinstance(error, BinanceAPIException):
                return error.code in {-1001, -1007, -1021} or error.status_code in {429, 500, 502, 503, 504}
            return isinstance(error, (ConnectionError, Timeout))

        try:
            # Never blindly replay writes after ambiguous network/timeout results.
            return retry_call(lambda: getattr(self.client, method)(**kwargs),
                              retryable=retryable, attempts=1 if mutation else 3)
        except BinanceAPIException as error:
            if mutation and error.code in {-1106, -4136}:
                raise OrderRejected(error.code) from None
            raise ExchangeError(f"{method} failed ({type(error).__name__})") from None
        except (BinanceOrderException, BinanceRequestException, ConnectionError, Timeout) as error:
            raise ExchangeError(f"{method} failed ({type(error).__name__})") from None

    def metadata(self):
        with self._lock:
            if self._metadata is None:
                data = self._call("futures_exchange_info")
                if not isinstance(data, dict) or not isinstance(data.get("symbols"), list):
                    raise ExchangeError("Invalid exchange metadata")
                self._metadata = {row["symbol"]: row for row in data["symbols"]}
            return self._metadata

    def symbols(self):
        return set(self.metadata())

    def filters(self, symbol):
        try:
            filters = {f["filterType"]: f for f in self.metadata()[symbol]["filters"]}
            minimum = filters.get("MIN_NOTIONAL", filters.get("NOTIONAL", {}))
            result = Filters(Decimal(filters["PRICE_FILTER"]["tickSize"]),
                             Decimal(filters["LOT_SIZE"]["stepSize"]),
                             Decimal(filters["LOT_SIZE"]["minQty"]),
                             Decimal(str(minimum.get("notional", minimum.get("minNotional", "0")))))
            if result.tick <= 0 or result.step <= 0:
                raise ValueError()
            return result
        except (KeyError, ValueError):
            raise ExchangeError("Missing or invalid symbol filters") from None

    def mark_price(self, symbol):
        price = Decimal(self._call("futures_mark_price", symbol=symbol)["markPrice"])
        if not price.is_finite() or price <= 0:
            raise ExchangeError("Invalid mark price")
        return price

    def dual_side(self):
        with self._lock:
            if self._dual is None:
                value = self._call("futures_get_position_mode").get("dualSidePosition")
                if not isinstance(value, bool):
                    raise ExchangeError("Unknown position mode")
                self._dual = value
            return self._dual

    def position(self, symbol):
        # V2 returns explicit zero rows; V3 omits inactive positions. Missing rows
        # remain unknown rather than being guessed flat.
        rows = self._call("futures_position_information", symbol=symbol, version=2)
        if not isinstance(rows, list):
            raise ExchangeError("Unknown position response")
        rows = [row for row in rows if row.get("symbol") == symbol]
        sides = {row.get("positionSide"): Decimal(row["positionAmt"]) for row in rows}
        if any(not amount.is_finite() for amount in sides.values()):
            raise ExchangeError("Invalid position amount")
        if self.dual_side():
            if not {"LONG", "SHORT"} <= sides.keys():
                raise ExchangeError("Incomplete hedge position snapshot")
            return Position(abs(sides["LONG"]), abs(sides["SHORT"]))
        if "BOTH" not in sides:
            raise ExchangeError("Missing one-way position snapshot")
        amount = sides["BOTH"]
        return Position(max(amount, Decimal(0)), max(-amount, Decimal(0)))

    def open_orders(self, symbol):
        orders = self._call("futures_get_open_orders", symbol=symbol)
        conditional = self._call("futures_get_open_algo_orders", symbol=symbol)
        if not isinstance(orders, list) or not isinstance(conditional, list):
            raise ExchangeError("Unknown open-order response")
        return orders + [dict(row, type=row.get("orderType", row.get("type")),
                              stopPrice=row.get("triggerPrice", row.get("stopPrice", "0")))
                         for row in conditional]

    def leverage(self, symbol, leverage):
        return self._call("futures_change_leverage", mutation=True, symbol=symbol, leverage=leverage)

    def entry(self, signal, quantity):
        args = dict(symbol=signal.symbol, side="BUY" if signal.side == Side.LONG else "SELL",
                    type="MARKET", quantity=self.filters(signal.symbol).quantity(quantity))
        if self.dual_side():
            args["positionSide"] = signal.side.value
        return self._call("futures_create_order", mutation=True, **args)

    def order(self, symbol, order_id):
        return self._call("futures_get_order", symbol=symbol, orderId=order_id)

    def cancel_order(self, symbol, order_id):
        return self._call("futures_cancel_order", mutation=True, symbol=symbol, orderId=order_id)

    def cancel_all(self, symbol):
        self._call("futures_cancel_all_open_orders", mutation=True, symbol=symbol)
        self._call("futures_cancel_all_algo_open_orders", mutation=True, symbol=symbol)

    def protect(self, signal, quantity, tp, sl, *, place_tp=True, place_sl=True):
        filters = self.filters(signal.symbol)
        args = dict(symbol=signal.symbol, side="SELL" if signal.side == Side.LONG else "BUY")
        if self.dual_side():
            args["positionSide"] = signal.side.value
        else:
            args["reduceOnly"] = True
        if place_tp:
            self._call("futures_create_order", mutation=True, **args, type="LIMIT", timeInForce="GTC",
                       price=filters.price(tp), quantity=filters.quantity(quantity))
        if place_sl:
            # closePosition works in both modes; reduceOnly is forbidden in hedge mode.
            args.pop("reduceOnly", None)
            try:
                self._call("futures_create_order", mutation=True, **args, type="STOP_MARKET",
                           stopPrice=filters.price(sl), closePosition=True, workingType=self.config.working_type)
            except OrderRejected:
                # Preserve quantity fallback only for explicit incompatible-parameter
                # rejection, never for ambiguous transport failures or trigger errors.
                if not self.dual_side():
                    args["reduceOnly"] = True
                self._call("futures_create_order", mutation=True, **args, type="STOP_MARKET",
                           stopPrice=filters.price(sl), quantity=filters.quantity(quantity),
                           workingType=self.config.working_type)

    def start_stream(self, callback):
        if self.config.dry_run or not self.config.websocket_enabled:
            return
        from binance import ThreadedWebsocketManager
        self._websocket = ThreadedWebsocketManager(api_key=self.config.binance_api_key,
                                                   api_secret=self.config.binance_api_secret,
                                                   testnet=self.config.binance_testnet)
        self._websocket.start()
        self._websocket.start_futures_user_socket(callback=callback)

    def close(self):
        try:
            if self._websocket:
                self._websocket.stop()
                self._websocket.join(timeout=10)
        finally:
            self.client.close_connection()
