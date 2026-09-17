"""Pure extraction of the original tolerant signal grammar."""
import re
from decimal import Decimal
from .models import TradeSignal, Side, EntryMode

# Numbers
NUM = r"\$?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?\$?"
RANGE = rf"(?:{NUM}\s*[-–—]\s*{NUM})|(?:{NUM})|(?:CMP\b)"

# Symbol pattern: base + (USDT or USD), with flexible separators and optional leading 'Coin:'
# Examples: "#PEOPLEUSDT", "PEOPLE/USDT", "people-usdt", "people usd"
SYM = r"\#?(?P<base>[A-Z0-9]{2,})\s*(?:[\/\-\s]?\s*(?:USDT|USD))\b"

# Entry synonyms are already broad; keep them
ENTRY_WORDS = r"(?:entry(?:\s*zone)?|entries|entry\s*range|buy\s*between|buy\s*range|cmp)"
TP_WORDS    = r"(?:tp|targets?|take\s*profit(?:s)?|tps?)"
SL_WORDS    = r"(?:sl|stop\s*loss|stoploss|stop\s*loss\s*at|stop\s*at|stop)"

# Side tokens: allow "Long Setup", "Long Set-Up", "Long Setp", and plain LONG/SHORT
SIDE_WORDS  = r"(?:\b(long|short)\b|(?:enter\s+(?:long|short))|(?:long|short)\s*set[\-\s]?up|(?:long|short)\s*setup|(?:long|short)\s*set-?p)"

SIG_RE_A = re.compile(
    rf"""(?P<side>{SIDE_WORDS}).*?(?P<symbol>{SYM}).*?(?:{ENTRY_WORDS})\s*[:\-]?\s*(?P<entry>{RANGE})
        (?:.*?(?:{TP_WORDS})\s*[:\-]?\s*(?P<tp_list>{NUM}(?:\s*[,\-\s]\s*{NUM})*))?
        (?:.*?(?:{SL_WORDS})\s*[:\-]?\s*(?P<sl>{NUM}))?
    """, re.I|re.S|re.X)

SIG_RE_B = re.compile(
    rf"""(?P<symbol>{SYM}).*?(?P<side>{SIDE_WORDS}).*?(?:{ENTRY_WORDS})\s*[:\-]?\s*(?P<entry>{RANGE})
        (?:.*?(?:{TP_WORDS})\s*[:\-]?\s*(?P<tp_list>{NUM}(?:\s*[,\-\s]\s*{NUM})*))?
        (?:.*?(?:{SL_WORDS})\s*[:\-]?\s*(?P<sl>{NUM}))?
    """, re.I|re.S|re.X)

# Orderless fallback chunks (for robustness)
ANY_ORDER = re.compile(
    rf"""(?P<symbol>{SYM})|(?P<side>{SIDE_WORDS})|
        (?P<entry_label>{ENTRY_WORDS})\s*[:\-]?\s*(?P<entry>{RANGE})|
        (?P<tp_label>{TP_WORDS})\s*[:\-]?\s*(?P<tp_list>{NUM}(?:\s*[,\-\s]\s*{NUM})*)|
        (?P<sl_label>{SL_WORDS})\s*[:\-]?\s*(?P<sl>{NUM})
    """, re.I|re.S|re.X)

# Leading "Coin:" fallback (optional)
COIN_LINE_RE = re.compile(r"coin\s*[:\-]?\s*#?([A-Z0-9]{2,})\s*(?:[\/\-\s]?(?:USDT|USD))\b", re.I)


def _number(raw: str) -> Decimal:
    return Decimal(raw.replace(",", "").replace("$", "").strip())


def _first_target(raw: str | None) -> Decimal | None:
    if not raw:
        return None
    # Use the original number grammar, including thousands separators.
    match = re.search(NUM, raw)
    return _number(match.group()) if match else None


def _signal(side, base, entry, targets, stop) -> TradeSignal | None:
    if not side or not base or not entry:
        return None
    normalized_side = Side.SHORT if "short" in side.lower() else Side.LONG
    value = entry.strip().upper().replace("–", "-").replace("—", "-")
    bounds = None
    if value == "CMP":
        mode = EntryMode.CMP
    elif "-" in value:
        first, second = value.split("-", 1)
        bounds = tuple(sorted((_number(first), _number(second))))
        mode = EntryMode.RANGE
    else:
        price = _number(value)
        bounds = (price, price)
        mode = EntryMode.SINGLE
    if bounds and any(not price.is_finite() or price <= 0 for price in bounds):
        return None
    return TradeSignal(base.upper() + "USDT", normalized_side, mode, bounds,
                       _first_target(targets), _number(stop) if stop else None)


def parse_signal(text: str) -> TradeSignal | None:
    """Preserve ordered matching precedence, then the original orderless fallback."""
    text = text or ""
    match = SIG_RE_A.search(text) or SIG_RE_B.search(text)
    if match:
        return _signal(match.group("side"), match.group("base"), match.group("entry"),
                       match.group("tp_list"), match.group("sl"))

    side = base = entry = targets = stop = None
    for match in ANY_ORDER.finditer(text):
        side = side or match.group("side")
        base = base or match.group("base")
        entry = entry or match.group("entry")
        targets = targets or match.group("tp_list")
        stop = stop or match.group("sl")
    if not base:
        coin = COIN_LINE_RE.search(text)
        if coin:
            base = coin.group(1)
    return _signal(side, base, entry, targets, stop)
