from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
import pytest
from binance_bot.config import Config
from binance_bot.telegram import MessagePipeline


def pipeline():
    dispatched = []
    processor = MessagePipeline(replace(Config(), telegram_channel_id=-123), lambda: {"BTCUSDT"},
                                dispatched.append, clock=lambda: 1000)
    return processor, dispatched


def test_duplicate_and_edit_after_trade_do_not_dispatch_twice():
    processor, dispatched = pipeline()
    args = (-123, 1, "long BTCUSDT entry CMP", 900)
    assert processor.process(*args)
    assert not processor.process(*args)
    assert not processor.process(*args, edited=True, revision=950)
    assert len(dispatched) == 1


def test_edit_can_complete_previously_incomplete_signal():
    processor, dispatched = pipeline()
    assert not processor.process(-123, 1, "long BTCUSDT", 900)
    assert processor.process(-123, 1, "long BTCUSDT entry CMP", 900, edited=True, revision=950)
    assert not processor.process(-123, 1, "long BTCUSDT entry CMP", 900, edited=True, revision=950)
    assert len(dispatched) == 1


@pytest.mark.parametrize("chat,text,created,edited", [(-456, "long BTCUSDT entry CMP", 900, False), (-123, "hello", 900, False), (-123, "long XYZUSDT entry CMP", 900, False), (-123, "long BTCUSDT entry CMP", 600, True)])
def test_ignored_messages(chat, text, created, edited):
    processor, dispatched = pipeline()
    assert not processor.process(chat, 1, text, created, edited=edited)
    assert not dispatched


def test_concurrent_deliveries_dispatch_once():
    processor, dispatched = pipeline()
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: processor.process(-123, 1, "long BTCUSDT entry CMP", 900), range(20)))
    assert sum(results) == 1 and len(dispatched) == 1


def test_symbol_query_failure_is_not_unavailable_symbol():
    processor, dispatched = pipeline()
    def fail():
        raise RuntimeError("unavailable")
    processor.symbols = fail
    with pytest.raises(RuntimeError):
        processor.process(-123, 1, "long BTCUSDT entry CMP", 900)
    processor.symbols = lambda: {"BTCUSDT"}
    assert processor.process(-123, 1, "long BTCUSDT entry CMP", 900)
