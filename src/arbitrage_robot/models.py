"""Доменные модели робота: котировки, комиссии, возможности, позиции, отчёты."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

__all__ = [
    "ExecutionReport",
    "FeeSchedule",
    "LegReport",
    "Opportunity",
    "OrderSide",
    "Position",
    "Quote",
    "TradeMode",
    "now_ms",
]


def now_ms() -> int:
    """Текущее время в миллисекундах (UTC)."""
    return int(time.time() * 1000)


class TradeMode(StrEnum):
    """Режим работы робота."""

    PAPER = "paper"
    """Тестовый режим: сделки только логируются, деньги не тратятся."""

    LIVE = "live"
    """Боевой режим: ордера реально отправляются на биржу."""


class OrderSide(StrEnum):
    """Направление ордера."""

    BUY = "buy"
    SELL = "sell"

    @property
    def opposite(self) -> OrderSide:
        """Противоположная сторона (нужна для закрытия позиции)."""
        return OrderSide.SELL if self is OrderSide.BUY else OrderSide.BUY


@dataclass(frozen=True, slots=True)
class FeeSchedule:
    """Комиссии биржи по конкретному рынку (в долях: 0.0004 == 0.04 %)."""

    maker: float
    taker: float

    @property
    def taker_pct(self) -> float:
        """Комиссия тейкера в процентах."""
        return self.taker * 100.0


@dataclass(frozen=True, slots=True)
class Quote:
    """Лучшая цена покупки/продажи (top of book) по символу на конкретной бирже."""

    exchange: str
    symbol: str
    bid: float
    ask: float
    timestamp_ms: int
    bid_volume: float | None = None
    ask_volume: float | None = None

    @property
    def mid(self) -> float:
        """Средняя цена между bid и ask."""
        return (self.bid + self.ask) / 2.0

    @property
    def spread_pct(self) -> float:
        """Внутренний спред инструмента в процентах."""
        return (self.ask - self.bid) / self.mid * 100.0 if self.mid > 0 else 0.0

    def age_ms(self, reference_ms: int | None = None) -> int:
        """Возраст котировки в миллисекундах."""
        return max(0, (reference_ms if reference_ms is not None else now_ms()) - self.timestamp_ms)

    def is_valid(self) -> bool:
        """Котировка пригодна к расчётам (положительные цены, ask >= bid)."""
        return self.bid > 0.0 and self.ask > 0.0 and self.ask >= self.bid


@dataclass(frozen=True, slots=True)
class Opportunity:
    """Найденная арбитражная возможность: купить дешевле, продать дороже."""

    symbol: str
    buy_exchange: str
    sell_exchange: str
    buy_price: float
    """Цена входа в лонг (ask на «дешёвой» бирже)."""
    sell_price: float
    """Цена входа в шорт (bid на «дорогой» бирже)."""
    gross_spread_pct: float
    """«Грязный» спред без учёта комиссий и проскальзывания, %."""
    fees_pct: float
    """Суммарные комиссии обеих ног, %."""
    slippage_pct: float
    """Заложенное проскальзывание, %."""
    net_spread_pct: float
    """Чистый ожидаемый спред: gross - fees - slippage, %."""
    amount: float
    """Размер сделки в базовой валюте (одинаковый на обеих ногах)."""
    notional: float
    """Объём сделки в котируемой валюте (USDT)."""
    detected_at_ms: int = field(default_factory=now_ms)

    @property
    def expected_profit(self) -> float:
        """Ожидаемая прибыль в котируемой валюте."""
        return self.notional * self.net_spread_pct / 100.0

    @property
    def route(self) -> str:
        """Человекочитаемый маршрут сделки."""
        return f"{self.buy_exchange} → {self.sell_exchange}"


@dataclass(frozen=True, slots=True)
class LegReport:
    """Результат исполнения одной ноги арбитражной сделки."""

    exchange: str
    symbol: str
    side: OrderSide
    amount: float
    price: float
    order_id: str | None = None
    simulated: bool = False
    raw: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class ExecutionReport:
    """Итог попытки исполнить арбитраж (вход или выход)."""

    success: bool
    action: str
    """``open`` — вход в позицию, ``close`` — закрытие."""
    symbol: str
    legs: tuple[LegReport, ...] = ()
    error: str | None = None
    mode: TradeMode = TradeMode.PAPER


@dataclass(slots=True)
class Position:
    """Открытая дельта-нейтральная арбитражная позиция (лонг + шорт)."""

    symbol: str
    long_exchange: str
    short_exchange: str
    amount: float
    entry_long_price: float
    entry_short_price: float
    entry_net_spread_pct: float
    entry_fees_pct: float
    opened_at_ms: int = field(default_factory=now_ms)
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    @property
    def notional(self) -> float:
        """Объём позиции в котируемой валюте по цене входа."""
        return self.amount * self.entry_long_price

    def current_spread_pct(self, long_quote: Quote, short_quote: Quote) -> float:
        """Текущий «грязный» спред между биржами позиции, %."""
        buy_price = long_quote.ask
        sell_price = short_quote.bid
        if buy_price <= 0.0:
            return 0.0
        return (sell_price - buy_price) / buy_price * 100.0

    def unrealized_pnl_pct(self, long_quote: Quote, short_quote: Quote) -> float:
        """Нереализованный результат позиции в процентах от объёма входа."""
        long_pnl = (long_quote.bid - self.entry_long_price) / self.entry_long_price * 100.0
        short_pnl = (self.entry_short_price - short_quote.ask) / self.entry_short_price * 100.0
        return long_pnl + short_pnl

    def age_sec(self, reference_ms: int | None = None) -> float:
        """Время жизни позиции в секундах."""
        ref = reference_ms if reference_ms is not None else now_ms()
        return max(0.0, (ref - self.opened_at_ms) / 1000.0)
