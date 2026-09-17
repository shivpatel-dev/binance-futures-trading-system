# Automated Binance Futures Trading System

An event-driven Python automation system integrating Telegram trading signals with Binance Futures. Repository: [shivpatel-dev/binance-futures-trading-system](https://github.com/shivpatel-dev/binance-futures-trading-system).

The system accepts LONG/SHORT signals, waits for the requested entry conditions, sizes a market order from configured margin and leverage, and calculates TP/SL from the actual filled average. It does not claim profitability.

## Architecture

```text
Telegram new/edited message
  -> message extraction / deduplication / edit policy
  -> pure signal parser -> environment symbol validation
  -> trading service -> Binance exchange adapter
  -> market entry / filled-price protection / monitoring / flat cleanup
```

- `binance_bot/config.py`: one validated configuration object; no client construction.
- `models.py`: typed signals, sides, entry modes, positions, active orders.
- `signal_parser.py`: extracted tolerant signal grammar with no integration imports.
- `exchange.py`: python-binance transport, retries, filters, positions, orders and websocket lifecycle.
- `trading.py`: entry decisions, fill reconciliation, protection verification and cleanup.
- `telegram.py`: one pipeline for new/edited messages; thread-safe notification dispatch.
- `app.py`: configuration, logging, clients, bounded trading worker pool, monitor and shutdown.
- `binance_futures_bot.py`: compatibility launcher only.

Imports start no clients, threads, logging files or network connections. Telegram uses asyncio; blocking exchange work runs in worker threads. A runtime-owned monitor checks protection every five seconds (up to ten minutes after fill) and continues flat-position cleanup for tracked symbols. Websocket events wake that same monitor; they do not independently create orders. Shutdown stops workers, the monitor, websocket and clients; it does not liquidate positions or cancel protection.

## Safety modes

The defaults are `DRY_RUN=true` and `BINANCE_TESTNET=true`.

- **Dry run:** evaluates entry and sizing and reports hypothetical protection; all exchange mutations are blocked, including leverage, entry, protection and cancellation. Connected startup still reads Binance data and authenticates Telegram.
- **Testnet:** selects Binance Futures testnet endpoints. With dry run disabled, orders are submitted to testnet.
- **Live:** requires user-provided live credentials and explicitly disabling both safer defaults. Live trading is never a development verification method.

Position-query failures remain errors, never a flat position. Both position and open-order inspection must succeed before protection changes. Cleanup requires explicit zero positions on both sides. Missing/incomplete snapshots remain unknown. Ordinary and conditional/algo orders are inspected and cleaned together. The adapter uses explicit V2 position rows to distinguish missing state from zero positions.

Read retries are limited to transient transport/server/rate/time errors (three attempts, 0.6s then 1.2s backoff). Authentication, signature and programming errors are not retried. Mutations are not blindly replayed after an ambiguous response. An entry submission whose response is lost requires operator inspection; do not resubmit the signal blindly. No persistent recovery database is introduced.

## Setup

Python 3.10+ is required (verified on Python 3.12). From PowerShell:

```powershell
git clone https://github.com/shivpatel-dev/binance-futures-trading-system.git
cd binance-futures-trading-system
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
# Edit .env privately; keep DRY_RUN=true and BINANCE_TESTNET=true.
.\.venv\Scripts\python.exe -m binance_bot.app --check-config
.\.venv\Scripts\python.exe -m binance_bot.app
```

On POSIX, use `python3 -m venv .venv`, `cp .env.example .env` and `.venv/bin/python` for subsequent commands. The compatibility launcher remains `python binance_futures_bot.py`.

`--check-config` validates without constructing clients or placing orders. Real connected startup needs Telegram user API ID/hash, phone, session and channel ID, plus Binance API key/secret; a Telegram bot token is not used. Placeholders validate format only and cannot authenticate. Configuration errors name settings without printing their values.

All supported variables are listed in `.env.example`. Key settings:

| Setting | Meaning |
| --- | --- |
| `TRADE_AMOUNT` | Margin in USDT; requested notional is margin multiplied by `DEFAULT_LEVERAGE` |
| `TPSL_PCT` | Fraction of filled average; `0.01` means 1% on each side |
| `ORDER_LIFETIME_SEC` | Range wait and initial fill-confirmation window |
| `PARTIAL_POLICY` | `ATTACH_AND_CANCEL` only; partial entries are canceled, re-read, then protected |
| `WORKING_TYPE` | `MARK_PRICE` or `CONTRACT_PRICE` for stop trigger |
| `WEBSOCKET_ENABLED` | User-data wakeups outside dry-run; polling remains available |
| `ALERT_ENABLED`, `ALERT_TARGET`, `VERBOSE_ALERTS` | Optional Telegram notifications and extra decision messages |
| `PROBE_SYMBOLS` | Optional comma-separated symbols to inspect at startup |
| `LOG_LEVEL`, `LOG_FILE` | Local runtime logging; default `INFO`, `bot.log` |
| `DUMP_IGNORED`, `DUMP_IGNORED_TO_ME` | Opt-in private raw-message diagnostics; disabled by default |

Minimum quantity/notional constraints may increase requested size, rounded upward to the quantity step. TP is a closing GTC limit; SL is a close-position stop-market with quantity fallback only for explicit incompatible-parameter rejection. Hedge orders omit unsupported `reduceOnly`. Signal-provided TP/SL values are parsed for compatibility, but the fixed configured percentage strategy determines actual protection.

## Signals and preserved behavior

```text
Long Setup #PEOPLEUSDT Entry: 0.01-0.02 TP: 0.03 SL: 0.009
Short Setup: BTCUSDT | Entry: CMP | SL 111000 | TP 99000
Coin: #BNBUSDT Long Entry 1140–1165 SL 1110 TP 1180
Long Set-Up people/usdt Entry 0.01
```

The parser retains LONG/SHORT, setup spelling variants, slash/hyphen/space symbol separators, USD-to-USDT normalization, reversed ranges, single prices, `Entry: CMP`, optional TP/SL and order-independent fallback. A side, symbol and entry are required; the old abbreviated heading without entry is not a complete signal.

CMP enters at market immediately; ranges wait for the mark to fall inside inclusive bounds. Single-price messages retain the actual old runtime behavior: an exact-price range. The old ±0.3% branch was unreachable for parser-produced signals and has been removed, rather than silently changing entry rules.

The old registered handler bypassed the separate dedupe/validation handlers. The unified policy now dispatches a message at most once within its one-hour cache: duplicates and edits after successful dispatch are ignored. An edit may complete a previously rejected/incomplete signal within five minutes of original creation. New and edited messages receive identical symbol checks. The cache is in memory and resets on restart.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Tests need no real Binance or Telegram credentials and block external socket connections (Windows asyncio's internal loopback socketpair is allowed). Coverage includes parser formats, configuration, Decimal precision/minimums, fake-exchange trading, API failure semantics, retry classification, dry-run write guards, deterministic edits/deduplication, notifications, import safety, and mocked startup/shutdown. A local dry-run message-to-trade flow is included. Tests never place external orders. The pinned python-binance version has upstream websocket deprecation warnings; these do not fail the suite.

## Historical deployment

An earlier version of the system was deployed on a Hetzner cloud server and exercised against a Binance Futures demo/test environment.

## Runtime data and limits

`.env`, environment variants, Telegram sessions, logs, virtual environments and test caches are ignored. `bot.log` is generated locally when the application starts; the previously tracked runtime log is removed. Raw ignored-message dumps are private and must not be published. No production credentials or session files are needed for tests.

Exchange acceptance, permissions and websocket delivery have not been verified against a real account during this refactor. Use testnet, if needed, after offline tests. Restart recovery, durable deduplication, external/manual trading coordination and multiple overlapping signals for the same symbol retain limitations of the original in-memory design. Flat cleanup cancels all orders for a tracked symbol, so this system should not share those symbols with independent order managers. Persistent recovery, new strategies, exchanges, dashboards and infrastructure are outside issue #1.
