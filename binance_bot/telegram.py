"""One message pipeline and thread-safe Telegram notifications."""
import asyncio
import logging
from pathlib import Path
import threading
import time
from .signal_parser import parse_signal

log = logging.getLogger(__name__)


class MessagePipeline:
    def __init__(self, config, symbols, dispatch, notify=lambda message: None, *, clock=time.time):
        self.config, self.symbols, self.dispatch = config, symbols, dispatch
        self.notify, self.clock = notify, clock
        self._seen, self._dispatched = {}, {}
        self._lock = threading.Lock()

    def process(self, chat_id, message_id, text, created_at, *, edited=False, revision=0):
        if chat_id != self.config.telegram_channel_id or not text or not text.strip():
            return False
        now = self.clock()
        if edited and now - created_at > 300:
            return False
        identity = (chat_id, message_id)
        key = (*identity, revision if edited else 0)
        with self._lock:
            self._seen = {k: v for k, v in self._seen.items() if now - v < 3600}
            self._dispatched = {k: v for k, v in self._dispatched.items() if now - v < 3600}
            if key in self._seen or identity in self._dispatched:
                return False
            self._seen[key] = now
            signal = parse_signal(text)
            if signal is None:
                if self.config.verbose_alerts:
                    self.notify("Ignored message: no complete trading signal")
                if self.config.dump_ignored:
                    with Path("ignored_samples.log").open("a", encoding="utf-8") as output:
                        output.write(f"\n---\n{text}\n")
                    if self.config.dump_ignored_to_me:
                        self.notify(f"Ignored sample: {text[:3000]}")
                return False
            try:
                if signal.symbol not in self.symbols():
                    self.notify(f"[{signal.symbol}] Unavailable in configured environment")
                    return False
                self.dispatch(signal)
            except Exception:
                self._seen.pop(key, None)
                raise
            self._dispatched[identity] = now
            return True


class TelegramNotifier:
    def __init__(self, client, loop, config):
        self.client, self.loop, self.config = client, loop, config

    async def _send(self, message):
        try:
            target = self.config.alert_target
            if target.lstrip("-").isdigit():
                target = int(target)
            await self.client.send_message(target, message[:3500])
        except Exception:
            log.warning("Telegram notification unavailable; see local event log")

    def __call__(self, message):
        if not self.config.alert_enabled:
            return
        coroutine = self._send(message)
        try:
            asyncio.run_coroutine_threadsafe(coroutine, self.loop)
        except Exception:
            coroutine.close()
            log.warning("Telegram notification loop unavailable")


def register_handlers(client, pipeline, channel):
    from telethon import events

    async def handle(event):
        try:
            edited = isinstance(event, events.MessageEdited.Event)
            date = event.message.edit_date if edited else event.message.date
            await asyncio.to_thread(pipeline.process, event.chat_id, event.id, event.raw_text,
                                    event.message.date.timestamp(), edited=edited,
                                    revision=date.timestamp() if date else 0)
        except Exception as error:
            log.error("Message processing failed (%s)", type(error).__name__)

    client.add_event_handler(handle, events.NewMessage(chats=channel))
    client.add_event_handler(handle, events.MessageEdited(chats=channel))
