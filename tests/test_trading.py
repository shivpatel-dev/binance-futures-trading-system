from dataclasses import replace
from decimal import Decimal as D
import pytest
from binance_bot.config import Config
from binance_bot.exchange import ExchangeError, retry_call
from binance_bot.models import ActiveOrder, Position
from binance_bot.signal_parser import parse_signal
from binance_bot.trading import TradingService
from .fakes import FakeExchange


def setup(dry_run=False, side="long"):
    exchange = FakeExchange()
    service = TradingService(replace(Config(), dry_run=dry_run), exchange)
    return service, exchange, parse_signal(f"{side} BTCUSDT entry CMP")


def test_dry_run_never_mutates():
    service, exchange, signal = setup(True)
    service.execute(signal)
    service.cleanup_if_flat(signal.symbol)
    service.ensure_protection(ActiveOrder(signal, 1, average=D("100")))
    assert not exchange.created and not exchange.protections and not exchange.cancellations


@pytest.mark.parametrize("side,tp,sl", [("long", "111.1", "108.9"), ("short", "108.9", "111.1")])
def test_uses_actual_average_and_no_duplicate_ws_protection(side, tp, sl):
    service, exchange, signal = setup(side=side)
    context = service.execute(signal)
    assert context.average == 110
    assert exchange.protections[0][1:3] == (D(tp), D(sl))
    service.on_user_update({"e": "ORDER_TRADE_UPDATE"})
    service.monitor_once()
    assert len(exchange.protections) == 1


def test_confirmed_flat_cleanup():
    service, exchange, signal = setup()
    exchange.snapshot = Position(D(0), D(0))
    assert service.cleanup_if_flat(signal.symbol)
    assert exchange.cancellations == [signal.symbol]


def test_position_failure_never_cleans():
    service, exchange, signal = setup()
    exchange.position_error = ExchangeError("offline")
    with pytest.raises(ExchangeError):
        service.cleanup_if_flat(signal.symbol)
    assert not exchange.cancellations


@pytest.mark.parametrize("failure", ["position_error", "orders_error"])
def test_unknown_state_never_recreates_protection(failure):
    service, exchange, signal = setup()
    setattr(exchange, failure, ExchangeError("offline"))
    with pytest.raises(ExchangeError):
        service.ensure_protection(ActiveOrder(signal, 1, average=D("110")))
    assert not exchange.protections and not exchange.cancellations


def test_missing_sl_does_not_duplicate_tp():
    service, exchange, signal = setup()
    exchange.orders = [dict(side="SELL", type="LIMIT", price="111.1")]
    service.ensure_protection(ActiveOrder(signal, 1, average=D("110")))
    assert exchange.protections[0][3:] == (False, True)


def test_partial_fill_canceled_before_protection():
    service, exchange, signal = setup()
    exchange.fill.update(status="PARTIALLY_FILLED", executedQty="2")
    context = service.execute(signal)
    assert context.filled == 2 and exchange.cancellations == [1]
    assert exchange.protections[0][0] == 2


def test_zero_average_uses_executed_quote_not_mark():
    service, exchange, signal = setup()
    exchange.fill.update(avgPrice="0", cumQuote="550")
    assert service.execute(signal).average == 110


def test_missing_average_preserves_pending_context_without_protection():
    service, exchange, signal = setup()
    exchange.fill.update(avgPrice="0")
    context = ActiveOrder(signal, 1)
    with pytest.raises(ExchangeError):
        service.reconcile(context)
    assert not exchange.protections


def test_failed_monitor_lookup_does_not_cleanup_or_reprotect():
    service, exchange, signal = setup()
    service.execute(signal)
    exchange.position_error = ExchangeError("offline")
    service.monitor_once()
    assert len(exchange.protections) == 1
    assert not exchange.cancellations


def test_pending_entry_blocks_flat_cleanup():
    service, exchange, signal = setup()
    service._active[1] = ActiveOrder(signal, 1)
    exchange.snapshot = Position(D(0), D(0))
    assert not service.cleanup_if_flat(signal.symbol)
    assert not exchange.cancellations


def test_range_timeout_creates_no_order():
    service, exchange, _ = setup()
    clock = iter([0, 121])
    service.clock = lambda: next(clock)
    service.execute(parse_signal("long BTCUSDT entry 200-210"))
    assert not exchange.created


def test_waiting_notifications_are_throttled_to_thirty_seconds():
    exchange = FakeExchange()
    now = 0
    notifications = []
    waits = []

    def wait(seconds):
        nonlocal now
        waits.append(seconds)
        now += seconds

    service = TradingService(
        replace(Config(), verbose_alerts=True, order_lifetime_sec=65),
        exchange,
        lambda message: notifications.append((now, message)),
        clock=lambda: now,
        wait=wait,
    )
    service.execute(parse_signal("long BTCUSDT entry 200-210"))

    assert waits == [1] * 65
    waiting_times = [timestamp for timestamp, message in notifications if "Waiting for entry range" in message]
    assert waiting_times == [0, 30, 60]
    assert notifications[-1] == (65, "[BTCUSDT] Entry range timed out")
    assert not exchange.created


def test_retry_only_transient_and_exhaustion():
    sleeps, calls = [], []
    def fail():
        calls.append(1)
        raise TimeoutError()
    with pytest.raises(TimeoutError):
        retry_call(fail, retryable=lambda e: isinstance(e, TimeoutError), sleep=sleeps.append)
    assert len(calls) == 3 and sleeps == [0.6, 1.2]
    calls.clear()
    with pytest.raises(TimeoutError):
        retry_call(fail, retryable=lambda e: False, sleep=sleeps.append)
    assert len(calls) == 1


def test_programming_errors_surface():
    service, exchange, signal = setup()
    exchange.position_error = TypeError("bug")
    with pytest.raises(TypeError):
        service.cleanup_if_flat(signal.symbol)


def test_range_wait_and_single_exact_price():
    service, exchange, _ = setup(True)
    exchange.marks = [D("99.9"), D("100")]
    waits = []
    service.wait = waits.append
    service.execute(parse_signal("long BTCUSDT entry 100"))
    assert waits == [1]


def test_notification_failure_does_not_change_trade():
    service, exchange, signal = setup()
    def fail(message):
        raise RuntimeError()
    service.notify = fail
    assert service.execute(signal).filled == 5
