from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, Field


class TradeMode(str, Enum):
    PAPER = "paper"
    LIVE = "live"


class SpreadDirection(str, Enum):
    UP = "up"
    DOWN = "down"


class ArbitrageOpportunity(BaseModel):
    """Арбитражная возможность между двумя биржами."""

    symbol: str
    buy_exchange: str
    sell_exchange: str
    buy_price: Decimal
    sell_price: Decimal
    spread_percent: Decimal = Field(description="Грубый спред без учёта комиссий, %")
    net_spread_percent: Decimal = Field(description="Спред с учётом комиссий, %")
    direction: SpreadDirection
    buy_fee_percent: Decimal
    sell_fee_percent: Decimal
    timestamp: datetime

    model_config = {"frozen": True}

    @property
    def direction_label(self) -> str:
        if self.direction == SpreadDirection.UP:
            return "вверх"
        return "вниз"


class TickerSnapshot(BaseModel):
    symbol: str
    exchange_id: str
    bid: Decimal
    ask: Decimal
    timestamp: datetime | None = None

    model_config = {"frozen": True}
