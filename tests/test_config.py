from dataclasses import fields
from decimal import Decimal
from pathlib import Path
import pytest
from dotenv import dotenv_values
from binance_bot.config import Config

ENV = dict(TELEGRAM_API_ID="123", TELEGRAM_API_HASH="fake-hash", TELEGRAM_PHONE="fake-phone",
           TELEGRAM_CHANNEL_ID="-123", BINANCE_API_KEY="fake-key", BINANCE_API_SECRET="fake-secret")


def test_mapping_defaults_and_secret_repr():
    config = Config.from_env(ENV | {"TRADE_AMOUNT": "12.5", "DEFAULT_LEVERAGE": "3", "PROBE_SYMBOLS": "BTCUSDT"})
    assert config.trade_amount == Decimal("12.5")
    assert config.default_leverage == 3
    assert config.probe_symbols == "BTCUSDT"
    assert config.dry_run and config.binance_testnet
    assert "fake-key" not in repr(config) and "fake-hash" not in repr(config)


@pytest.mark.parametrize("value,expected", [("true", True), ("YES", True), ("1", True), ("on", True), ("false", False), ("0", False), ("off", False), ("no", False)])
def test_booleans(value, expected):
    assert Config.from_env(ENV | {"VERBOSE_ALERTS": value}).verbose_alerts is expected


@pytest.mark.parametrize("key,value", [("TRADE_AMOUNT", "zero"), ("TRADE_AMOUNT", "NaN"), ("TRADE_AMOUNT", "-1"), ("DEFAULT_LEVERAGE", "0"), ("DEFAULT_LEVERAGE", "126"), ("TPSL_PCT", "0"), ("TPSL_PCT", "1"), ("ORDER_LIFETIME_SEC", "0"), ("DRY_RUN", "maybe"), ("WORKING_TYPE", "bad"), ("PARTIAL_POLICY", "bad"), ("LOG_LEVEL", "bad")])
def test_invalid(key, value):
    with pytest.raises(ValueError):
        Config.from_env(ENV | {key: value})


@pytest.mark.parametrize("key", list(ENV))
def test_required(key):
    env = ENV.copy()
    del env[key]
    with pytest.raises(ValueError):
        Config.from_env(env)


def test_example_maps_exactly_to_consumed_settings():
    example = dotenv_values(Path(__file__).parents[1] / ".env.example")
    assert set(example) == {field.name.upper() for field in fields(Config)}
    assert Config.from_env(example).dry_run
