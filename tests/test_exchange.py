from dataclasses import replace
from decimal import Decimal as D
from unittest.mock import Mock
import asyncio
import pytest
from requests.exceptions import Timeout
from binance.exceptions import BinanceAPIException
from binance.client import Client
from binance_bot.config import Config
from binance_bot.exchange import BinanceExchange, ExchangeError
from binance_bot.signal_parser import parse_signal


def adapter(dry_run=False, dual=False):
    client = Mock()
    client.futures_get_position_mode.return_value = {"dualSidePosition": dual}
    client.futures_position_information.return_value = [dict(symbol="BTCUSDT", positionSide="BOTH", positionAmt="0")]
    client.futures_get_open_orders.return_value = []
    client.futures_get_open_algo_orders.return_value = []
    client.futures_exchange_info.return_value = {"symbols": [{"symbol": "BTCUSDT", "filters": [
        {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
        {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001"},
        {"filterType": "MIN_NOTIONAL", "notional": "5"}]}]}
    return BinanceExchange(client, replace(Config(), dry_run=dry_run)), client


def test_explicit_zero_position_and_negative_short():
    exchange, client = adapter()
    assert exchange.position("BTCUSDT").flat
    client.futures_position_information.assert_called_with(symbol="BTCUSDT", version=2)
    client.futures_position_information.return_value[0]["positionAmt"] = "-2"
    assert exchange.position("BTCUSDT").short == 2


@pytest.mark.parametrize("response", [[], {}, None])
def test_missing_position_is_unknown(response):
    exchange, client = adapter()
    client.futures_position_information.return_value = response
    with pytest.raises(ExchangeError):
        exchange.position("BTCUSDT")


def test_hedge_snapshot_requires_both_sides_and_normalizes_short():
    exchange, client = adapter(dual=True)
    client.futures_position_information.return_value = [dict(symbol="BTCUSDT", positionSide="LONG", positionAmt="0")]
    with pytest.raises(ExchangeError):
        exchange.position("BTCUSDT")
    client.futures_position_information.return_value.append(dict(symbol="BTCUSDT", positionSide="SHORT", positionAmt="-3"))
    assert exchange.position("BTCUSDT").short == 3


def test_order_query_failure_cannot_be_empty(monkeypatch):
    exchange, client = adapter()
    monkeypatch.setattr("binance_bot.exchange.time.sleep", lambda _: None)
    client.futures_get_open_algo_orders.side_effect = TypeError("bug")
    with pytest.raises(TypeError):
        exchange.open_orders("BTCUSDT")
    assert client.futures_get_open_algo_orders.call_count == 1


def test_algo_order_normalization_and_cleanup():
    exchange, client = adapter()
    client.futures_get_open_algo_orders.return_value = [dict(orderType="STOP_MARKET", triggerPrice="99", side="SELL")]
    assert exchange.open_orders("BTCUSDT")[0]["type"] == "STOP_MARKET"
    exchange.cancel_all("BTCUSDT")
    client.futures_cancel_all_open_orders.assert_called_once()
    client.futures_cancel_all_algo_open_orders.assert_called_once()


def test_dry_run_adapter_blocks_all_writes():
    exchange, client = adapter(dry_run=True)
    signal = parse_signal("long BTCUSDT entry CMP")
    for operation in (lambda: exchange.entry(signal, D(1)), lambda: exchange.leverage("BTCUSDT", 5),
                      lambda: exchange.cancel_all("BTCUSDT"), lambda: exchange.cancel_order("BTCUSDT", 1),
                      lambda: exchange.protect(signal, D(1), D(101), D(99))):
        with pytest.raises(ExchangeError):
            operation()
    client.futures_create_order.assert_not_called()
    client.futures_change_leverage.assert_not_called()
    client.futures_cancel_all_open_orders.assert_not_called()


@pytest.mark.parametrize("dual", [False, True])
def test_protection_parameters(dual):
    exchange, client = adapter(dual=dual)
    exchange.protect(parse_signal("long BTCUSDT entry CMP"), D(1), D(101), D(99))
    tp, sl = [call.kwargs for call in client.futures_create_order.call_args_list]
    assert tp["type"] == "LIMIT" and sl["type"] == "STOP_MARKET"
    assert sl["closePosition"] is True and "reduceOnly" not in sl
    assert ("reduceOnly" in tp) is not dual
    assert (tp.get("positionSide") == "LONG") is dual


def test_ambiguous_write_is_never_replayed():
    exchange, client = adapter()
    client.futures_create_order.side_effect = Timeout()
    with pytest.raises(ExchangeError):
        exchange.entry(parse_signal("long BTCUSDT entry CMP"), D(1))
    assert client.futures_create_order.call_count == 1


def test_nonretryable_auth_error_does_not_leak_message():
    exchange, client = adapter()
    client.futures_get_open_orders.side_effect = BinanceAPIException(Mock(), 401, '{"code": -2015, "msg": "private-detail"}')
    with pytest.raises(ExchangeError) as caught:
        exchange.open_orders("BTCUSDT")
    assert "private-detail" not in str(caught.value)
    assert client.futures_get_open_orders.call_count == 1


def test_transient_read_retries_then_succeeds(monkeypatch):
    exchange, client = adapter()
    sleeps = []
    monkeypatch.setattr("binance_bot.exchange.time.sleep", sleeps.append)
    client.futures_get_open_orders.side_effect = [Timeout(), Timeout(), []]
    assert exchange.open_orders("BTCUSDT") == []
    assert client.futures_get_open_orders.call_count == 3
    assert sleeps == [0.6, 1.2]


def test_transport_position_failure_is_explicit(monkeypatch):
    exchange, client = adapter()
    monkeypatch.setattr("binance_bot.exchange.time.sleep", lambda _: None)
    client.futures_position_information.side_effect = Timeout()
    with pytest.raises(ExchangeError):
        exchange.position("BTCUSDT")
    assert client.futures_position_information.call_count == 3


def test_explicit_stop_rejection_allows_quantity_fallback():
    exchange, client = adapter()
    rejection = BinanceAPIException(Mock(), 400, '{"code": -4136, "msg": "rejected"}')
    client.futures_create_order.side_effect = [{}, rejection, {}]
    exchange.protect(parse_signal("long BTCUSDT entry CMP"), D(1), D(101), D(99))
    fallback = client.futures_create_order.call_args.kwargs
    assert fallback["quantity"] == "1.000" and fallback["reduceOnly"] is True
    assert "closePosition" not in fallback


def test_ambiguous_stop_failure_never_uses_fallback():
    exchange, client = adapter()
    client.futures_create_order.side_effect = [{}, Timeout()]
    with pytest.raises(ExchangeError):
        exchange.protect(parse_signal("long BTCUSDT entry CMP"), D(1), D(101), D(99))
    assert client.futures_create_order.call_count == 2


def test_installed_sdk_routes_conditional_orders_and_v2_without_network():
    async def scenario():
        client = Client("fake", "fake", ping=False, testnet=True)
        try:
            client._request = Mock(return_value=[])
            client.futures_position_information(symbol="BTCUSDT", version=2)
            assert "/fapi/v2/positionRisk" in client._request.call_args.args[1]
            client.futures_create_order(symbol="BTCUSDT", type="STOP_MARKET", stopPrice="99", closePosition=True)
            args, kwargs = client._request.call_args
            assert "/algoOrder" in args[1] and kwargs["data"]["triggerPrice"] == "99"
        finally:
            client.close_connection()
    asyncio.run(scenario())
