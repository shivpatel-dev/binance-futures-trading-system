import asyncio
from dataclasses import replace
import subprocess
import sys
import pytest
from unittest.mock import Mock, AsyncMock

from binance_bot.config import Config
from binance_bot.telegram import TelegramNotifier, MessagePipeline
from binance_bot.trading import TradingService
from .fakes import FakeExchange


def test_imports_do_not_start_operational_work():
    code = '''
import importlib, threading, logging, socket
def forbidden(*args, **kwargs):
    raise AssertionError("Import started operational work")
socket.socket.connect = forbidden
socket.create_connection = forbidden
import binance.client, telethon
threading.Thread.start = forbidden
binance.client.Client = forbidden
telethon.TelegramClient = forbidden
logging.basicConfig = forbidden
for module in ("config", "models", "signal_parser", "exchange", "trading", "telegram", "app"):
    importlib.import_module("binance_bot." + module)
importlib.import_module("binance_futures_bot")
'''
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_thread_notification_dispatch_and_failure():
    async def scenario():
        client = Mock()
        client.send_message = AsyncMock()
        notifier = TelegramNotifier(client, asyncio.get_running_loop(), replace(Config(), alert_enabled=True))
        await asyncio.to_thread(notifier, "test notification")
        await asyncio.sleep(0)
        client.send_message.assert_awaited_once_with("me", "test notification")
        client.send_message.side_effect = RuntimeError("unavailable")
        await notifier._send("still safe")
    asyncio.run(scenario())


def test_offline_dry_run_message_to_trade_flow():
    config = replace(Config(), telegram_channel_id=-123)
    exchange, alerts = FakeExchange(), []
    service = TradingService(config, exchange, alerts.append)
    pipeline = MessagePipeline(config, exchange.symbols, service.execute, clock=lambda: 100)
    assert pipeline.process(-123, 1, "Long Setup BTCUSDT Entry CMP", 100)
    assert "DRY_RUN" in alerts[0]
    assert not exchange.created and not exchange.protections


@pytest.mark.parametrize("dry_run", [True, False])
def test_runtime_startup_and_shutdown(monkeypatch, dry_run):
    import binance
    import binance.client
    import telethon
    from binance_bot.app import run
    client = Mock()
    client.futures_get_position_mode.return_value = {"dualSidePosition": False}
    telegram = Mock()
    telegram.start = AsyncMock()
    telegram.get_entity = AsyncMock(return_value=Mock())
    telegram.run_until_disconnected = AsyncMock()
    telegram.disconnect = AsyncMock()
    monkeypatch.setattr(binance.client, "Client", Mock(return_value=client))
    monkeypatch.setattr(telethon, "TelegramClient", Mock(return_value=telegram))
    websocket = Mock()
    monkeypatch.setattr(binance, "ThreadedWebsocketManager", Mock(return_value=websocket))
    asyncio.run(run(replace(Config(), dry_run=dry_run)))
    assert telegram.add_event_handler.call_count == 2
    telegram.disconnect.assert_awaited_once()
    client.close_connection.assert_called_once()
    client.futures_create_order.assert_not_called()
    if dry_run:
        websocket.start.assert_not_called()
    else:
        websocket.start.assert_called_once()
        websocket.stop.assert_called_once()
        websocket.join.assert_called_once()
