"""Explicit startup/shutdown; --check-config validates without external clients."""
import argparse
import asyncio
from concurrent.futures import ThreadPoolExecutor
import logging
import threading
from .config import Config
from .exchange import BinanceExchange
from .telegram import MessagePipeline, TelegramNotifier, register_handlers
from .trading import TradingService

log = logging.getLogger(__name__)


async def run(config):
    from binance.client import Client
    from telethon import TelegramClient

    exchange = BinanceExchange(Client(config.binance_api_key, config.binance_api_secret,
                                      testnet=config.binance_testnet, ping=False,
                                      requests_params={"timeout": 10}), config)
    try:
        client = TelegramClient(config.telegram_session, config.telegram_api_id, config.telegram_api_hash)
    except Exception:
        exchange.close()
        raise
    workers = ThreadPoolExecutor(max_workers=4, thread_name_prefix="trading")
    notifier = TelegramNotifier(client, asyncio.get_running_loop(), config)
    service = TradingService(config, exchange, notifier)
    monitor = None

    def report_failure(future):
        if not future.cancelled() and future.exception() is not None:
            log.error("Trading worker failed (%s)", type(future.exception()).__name__)
            notifier("Trading worker failed; inspect runtime state before further trading")

    def dispatch(signal):
        workers.submit(service.execute, signal).add_done_callback(report_failure)

    try:
        await client.start(phone=config.telegram_phone)
        channel = await client.get_entity(config.telegram_channel_id)
        pipeline = MessagePipeline(config, exchange.symbols, dispatch, notifier)
        register_handlers(client, pipeline, channel)
        await asyncio.to_thread(exchange.dual_side)
        for symbol in config.probe_symbols.split(","):
            symbol = symbol.strip().upper()
            if symbol:
                available = symbol in await asyncio.to_thread(exchange.symbols)
                price = await asyncio.to_thread(exchange.mark_price, symbol) if available else None
                log.info("Probe %s available=%s mark=%s", symbol, available, price)
        if not config.dry_run:
            await asyncio.to_thread(exchange.start_stream, service.on_user_update)
            def monitor_runtime():
                try:
                    service.monitor()
                except Exception as error:
                    log.error("Protection monitor failed (%s)", type(error).__name__)
                    notifier("Protection monitor failed; stopping runtime, inspect existing positions")
                    service.shutdown()
                    asyncio.run_coroutine_threadsafe(client.disconnect(), notifier.loop)
            monitor = threading.Thread(target=monitor_runtime, name="protection-monitor")
            monitor.start()
        log.info("Started testnet=%s dry_run=%s", config.binance_testnet, config.dry_run)
        await client.run_until_disconnected()
    finally:
        service.shutdown()
        try:
            await client.disconnect()
        finally:
            try:
                await asyncio.to_thread(workers.shutdown, wait=True, cancel_futures=True)
                if monitor:
                    await asyncio.to_thread(monitor.join)
            finally:
                await asyncio.to_thread(exchange.close)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-config", action="store_true")
    args = parser.parse_args()
    from dotenv import load_dotenv
    load_dotenv()
    try:
        config = Config.from_env()
    except ValueError as error:
        parser.error(str(error))
    if args.check_config:
        print("Configuration valid (no external clients started)")
        return
    logging.basicConfig(level=config.log_level, format="%(asctime)s | %(levelname)s | %(message)s",
                        handlers=[logging.FileHandler(config.log_file, encoding="utf-8"), logging.StreamHandler()])
    for name in ("binance", "telethon", "urllib3"):
        logging.getLogger(name).setLevel(logging.WARNING)
    try:
        asyncio.run(run(config))
    except KeyboardInterrupt:
        log.info("Stopped")
    except Exception as error:
        log.error("Runtime failed (%s)", type(error).__name__)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
