from decimal import Decimal as D
import pytest
from binance_bot.exchange import Filters, round_to_step
from binance_bot.models import Side
from binance_bot.trading import protection_prices


def test_tick_and_step_rounding():
    filters = Filters(D("0.05"), D("0.001"), D("0.001"), D("5"))
    assert filters.price("12.349") == "12.30"
    assert filters.quantity(D("1.2349")) == "1.234"
    assert round_to_step(D("1.2349"), D("0.001")) == D("1.234")


def test_minimums_round_up_and_notional_sizing():
    filters = Filters(D("0.1"), D("0.1"), D("0.15"), D("5"))
    assert filters.size(D("1"), D("3")) == D("1.7")
    assert filters.size(D("1"), D("100")) == D("0.2")
    assert filters.size(D("100") * 5, D("100")) == D("5")


@pytest.mark.parametrize("side,expected", [(Side.LONG, (D("101"), D("99"))), (Side.SHORT, (D("99"), D("101")))])
def test_protection(side, expected):
    assert protection_prices(D("100"), side, D("0.01")) == expected
