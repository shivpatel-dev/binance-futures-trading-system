from decimal import Decimal
import pytest
from binance_bot.models import EntryMode, Side
from binance_bot.signal_parser import parse_signal


@pytest.mark.parametrize("text,side,symbol,mode,bounds", [
    ("Long Setup #PEOPLEUSDT Entry: 0.01-0.02 TP: 0.03 SL: 0.009", Side.LONG, "PEOPLEUSDT", EntryMode.RANGE, ("0.01", "0.02")),
    ("Short Setup: BTCUSDT | Entry: CMP | SL 111000 | TP 99000", Side.SHORT, "BTCUSDT", EntryMode.CMP, None),
    ("Long Set-Up people/usdt entry 2–1", Side.LONG, "PEOPLEUSDT", EntryMode.RANGE, ("1", "2")),
    ("long setp people-usdt entry 1.25", Side.LONG, "PEOPLEUSDT", EntryMode.SINGLE, ("1.25", "1.25")),
    ("Coin: #BNBUSDT Long Entry 1140–1165 SL 1110 TP 1180", Side.LONG, "BNBUSDT", EntryMode.RANGE, ("1140", "1165")),
    ("Entry 100-110\nCoin: btc usd\nSHORT", Side.SHORT, "BTCUSDT", EntryMode.RANGE, ("100", "110")),
    ("long BTCUSDT entry $100,000-$101,000", Side.LONG, "BTCUSDT", EntryMode.RANGE, ("100000", "101000")),
])
def test_supported_formats(text, side, symbol, mode, bounds):
    signal = parse_signal(text)
    assert signal is not None
    assert (signal.side, signal.symbol, signal.mode) == (side, symbol, mode)
    assert signal.bounds == (tuple(map(Decimal, bounds)) if bounds else None)


def test_optional_targets():
    signal = parse_signal("long BTCUSDT entry 100 TP 110 SL 90")
    assert signal.tp == 110 and signal.sl == 90


def test_comma_separated_target_is_not_truncated():
    signal = parse_signal("long BTCUSDT entry 100,000 TP 110,000 SL 90,000")
    assert signal.tp == 110000 and signal.sl == 90000


def test_entry_precision_does_not_pass_through_float():
    signal = parse_signal("long BTCUSDT entry 0.1234567890123456789")
    assert signal.bounds[0] == Decimal("0.1234567890123456789")


@pytest.mark.parametrize("text", ["", "hello world", "BTCUSDT Entry 100", "Long Entry 100", "Long Setup #PEOPLEUSDT", "long BTCUSDT Entry bananas", "long BTCUSDT entry 0"])
def test_rejects_incomplete(text):
    assert parse_signal(text) is None
