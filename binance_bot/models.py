from dataclasses import dataclass
from decimal import Decimal
from enum import Enum


class Side(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class EntryMode(str, Enum):
    CMP = "CMP"
    RANGE = "RANGE"
    SINGLE = "SINGLE"


@dataclass(frozen=True)
class TradeSignal:
    symbol: str
    side: Side
    mode: EntryMode
    bounds: tuple[Decimal, Decimal] | None = None
    tp: Decimal | None = None
    sl: Decimal | None = None


@dataclass(frozen=True)
class Position:
    long: Decimal
    short: Decimal

    @property
    def flat(self) -> bool:
        return self.long == 0 and self.short == 0

    def quantity(self, side: Side) -> Decimal:
        return self.long if side == Side.LONG else self.short


@dataclass
class ActiveOrder:
    signal: TradeSignal
    order_id: int
    filled: Decimal = Decimal(0)
    average: Decimal = Decimal(0)
    protection_until: float = 0
