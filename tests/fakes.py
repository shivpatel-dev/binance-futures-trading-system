from decimal import Decimal as D
from binance_bot.exchange import Filters
from binance_bot.models import Position


class FakeExchange:
    def __init__(self):
        self.created, self.protections, self.cancellations = [], [], []
        self.orders = []
        self.snapshot = Position(D("5"), D("5"))
        self.fill = dict(orderId=1, status="FILLED", executedQty="5", avgPrice="110")
        self.position_error = self.orders_error = None
        self.marks = [D("100")]

    def filters(self, symbol):
        return Filters(D("0.01"), D("0.001"), D("0.001"), D("5"))

    def symbols(self):
        return {"BTCUSDT"}

    def mark_price(self, symbol):
        return self.marks.pop(0) if len(self.marks) > 1 else self.marks[0]

    def position(self, symbol):
        if self.position_error:
            raise self.position_error
        return self.snapshot

    def open_orders(self, symbol):
        if self.orders_error:
            raise self.orders_error
        return list(self.orders)

    def leverage(self, symbol, leverage):
        self.created.append(("leverage", leverage))

    def entry(self, signal, quantity):
        self.created.append(("entry", quantity))
        return {"orderId": 1}

    def order(self, symbol, order_id):
        return self.fill.copy()

    def cancel_order(self, symbol, order_id):
        self.fill["status"] = "CANCELED"
        self.cancellations.append(order_id)

    def cancel_all(self, symbol):
        self.cancellations.append(symbol)

    def protect(self, signal, quantity, tp, sl, *, place_tp=True, place_sl=True):
        self.protections.append((quantity, tp, sl, place_tp, place_sl))
        side = "SELL" if signal.side == "LONG" else "BUY"
        if place_tp:
            self.orders.append(dict(type="LIMIT", side=side, price=str(tp)))
        if place_sl:
            self.orders.append(dict(type="STOP_MARKET", side=side, stopPrice=str(sl)))
