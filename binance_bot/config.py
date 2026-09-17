"""Explicit environment mapping; validation never connects to external services."""
from dataclasses import dataclass, field, fields
from decimal import Decimal, InvalidOperation
import os


@dataclass(frozen=True)
class Config:
    telegram_api_id: int = 0
    telegram_api_hash: str = field(default="", repr=False)
    telegram_phone: str = field(default="", repr=False)
    telegram_session: str = field(default="trading_bot", repr=False)
    telegram_channel_id: int = field(default=0, repr=False)
    binance_api_key: str = field(default="", repr=False)
    binance_api_secret: str = field(default="", repr=False)
    binance_testnet: bool = True
    dry_run: bool = True
    trade_amount: Decimal = Decimal("100")
    default_leverage: int = 5
    order_lifetime_sec: int = 120
    tpsl_pct: Decimal = Decimal("0.01")
    working_type: str = "MARK_PRICE"
    partial_policy: str = "ATTACH_AND_CANCEL"
    websocket_enabled: bool = True
    alert_enabled: bool = False
    alert_target: str = field(default="me", repr=False)
    verbose_alerts: bool = False
    dump_ignored: bool = False
    dump_ignored_to_me: bool = False
    log_level: str = "INFO"
    log_file: str = "bot.log"
    probe_symbols: str = ""

    @classmethod
    def from_env(cls, env=None):
        source = os.environ if env is None else env
        defaults = cls()
        values = {}
        for item in fields(cls):
            key = item.name.upper()
            raw = str(source.get(key, getattr(defaults, item.name))).strip()
            default = getattr(defaults, item.name)
            try:
                if isinstance(default, bool):
                    if raw.lower() not in {"true", "false", "1", "0", "yes", "no", "on", "off"}:
                        raise ValueError()
                    value = raw.lower() in {"true", "1", "yes", "on"}
                elif isinstance(default, int):
                    value = int(raw)
                elif isinstance(default, Decimal):
                    value = Decimal(raw)
                    if not value.is_finite():
                        raise ValueError()
                else:
                    value = raw
                    if item.name in {"working_type", "partial_policy", "log_level"}:
                        value = value.upper()
                values[item.name] = value
            except (ValueError, InvalidOperation):
                raise ValueError(f"Invalid {key}") from None
        config = cls(**values)
        config.validate()
        return config

    def validate(self):
        for name in ("telegram_api_hash", "telegram_phone", "telegram_session",
                     "binance_api_key", "binance_api_secret"):
            if not getattr(self, name):
                raise ValueError(f"Missing {name.upper()}")
        if self.telegram_api_id <= 0 or self.telegram_channel_id == 0:
            raise ValueError("TELEGRAM_API_ID must be positive and TELEGRAM_CHANNEL_ID nonzero")
        if self.trade_amount <= 0 or not 1 <= self.default_leverage <= 125:
            raise ValueError("Invalid TRADE_AMOUNT or DEFAULT_LEVERAGE")
        if self.order_lifetime_sec <= 0 or not 0 < self.tpsl_pct < 1:
            raise ValueError("Invalid ORDER_LIFETIME_SEC or TPSL_PCT")
        if self.working_type not in {"MARK_PRICE", "CONTRACT_PRICE"}:
            raise ValueError("Invalid WORKING_TYPE")
        if self.partial_policy != "ATTACH_AND_CANCEL":
            raise ValueError("Only PARTIAL_POLICY=ATTACH_AND_CANCEL is supported")
        if self.log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("Invalid LOG_LEVEL")
        if not self.log_file or not self.alert_target:
            raise ValueError("LOG_FILE and ALERT_TARGET cannot be empty")
