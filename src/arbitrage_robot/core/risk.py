"""Риск-менеджер и учёт открытых позиций."""

from __future__ import annotations

import time
from collections.abc import Iterator
from dataclasses import dataclass, field

from ..config import RiskConfig, StrategyConfig
from ..logging_setup import get_logger
from ..models import Opportunity, Position

__all__ = ["PortfolioStats", "RiskManager"]


@dataclass(slots=True)
class PortfolioStats:
    """Накопительная статистика работы робота."""

    opportunities_found: int = 0
    positions_opened: int = 0
    positions_closed: int = 0
    failed_executions: int = 0
    realized_pnl: float = 0.0
    best_spread_pct: float = 0.0
    started_at: float = field(default_factory=time.monotonic)

    @property
    def uptime_sec(self) -> float:
        """Время работы робота, сек."""
        return time.monotonic() - self.started_at


class RiskManager:
    """Контролирует лимиты и хранит открытые позиции."""

    def __init__(self, risk: RiskConfig, strategy: StrategyConfig) -> None:
        self._risk: RiskConfig = risk
        self._strategy: StrategyConfig = strategy
        self._positions: dict[str, Position] = {}
        self._cooldown_until: dict[str, float] = {}
        self.stats: PortfolioStats = PortfolioStats()
        self._daily_loss: float = 0.0
        self._halted: bool = False
        self.log = get_logger("risk")

    # ------------------------------------------------------------------ #
    # Позиции
    # ------------------------------------------------------------------ #

    @property
    def positions(self) -> tuple[Position, ...]:
        """Все открытые позиции."""
        return tuple(self._positions.values())

    @property
    def open_count(self) -> int:
        """Количество открытых позиций."""
        return len(self._positions)

    @property
    def total_notional(self) -> float:
        """Суммарный объём открытых позиций в котируемой валюте."""
        return sum(position.notional for position in self._positions.values())

    @property
    def halted(self) -> bool:
        """Остановлен ли робот по дневному стоп-лоссу."""
        return self._halted

    def iter_positions(self) -> Iterator[Position]:
        """Итератор по копии списка позиций (безопасно для изменений во время обхода)."""
        return iter(list(self._positions.values()))

    def register_open(self, position: Position) -> None:
        """Зарегистрировать открытую позицию."""
        self._positions[position.id] = position
        self.stats.positions_opened += 1
        self._set_cooldown(position.symbol)

    def register_close(self, position: Position, realized_pnl: float) -> None:
        """Снять позицию с учёта и записать финансовый результат."""
        self._positions.pop(position.id, None)
        self.stats.positions_closed += 1
        self.stats.realized_pnl += realized_pnl
        if realized_pnl < 0:
            self._daily_loss += -realized_pnl
            if self._daily_loss >= self._risk.max_daily_loss_quote:
                self._halted = True
                self.log.critical(
                    "Достигнут дневной лимит убытка (%.2f). Новые входы заблокированы.",
                    self._daily_loss,
                )
        self._set_cooldown(position.symbol)

    def register_failure(self) -> None:
        """Учесть неудачную попытку исполнения."""
        self.stats.failed_executions += 1

    # ------------------------------------------------------------------ #
    # Проверки перед входом
    # ------------------------------------------------------------------ #

    def can_open(self, opportunity: Opportunity) -> str | None:
        """Проверить, можно ли открыть позицию. Возвращает причину отказа или ``None``."""
        if self._halted:
            return "робот остановлен по дневному стоп-лоссу"
        if self.open_count >= self._risk.max_open_positions:
            return "достигнут лимит открытых позиций"
        if self._symbol_positions(opportunity.symbol) >= self._risk.max_positions_per_symbol:
            return f"по {opportunity.symbol} уже есть позиция"
        if self.total_notional + opportunity.notional > self._risk.max_notional_quote:
            return "превышен лимит суммарного объёма"
        if self._in_cooldown(opportunity.symbol):
            return f"{opportunity.symbol} в режиме охлаждения"
        if self._pair_busy(opportunity):
            return "эта пара бирж уже задействована по данному символу"
        return None

    def _symbol_positions(self, symbol: str) -> int:
        """Сколько позиций открыто по символу."""
        return sum(1 for position in self._positions.values() if position.symbol == symbol)

    def _pair_busy(self, opportunity: Opportunity) -> bool:
        """Уже есть позиция по этому символу на тех же биржах?"""
        exchanges = {opportunity.buy_exchange, opportunity.sell_exchange}
        return any(
            position.symbol == opportunity.symbol
            and {position.long_exchange, position.short_exchange} == exchanges
            for position in self._positions.values()
        )

    def _in_cooldown(self, symbol: str) -> bool:
        """Действует ли пауза по символу."""
        until = self._cooldown_until.get(symbol)
        return until is not None and time.monotonic() < until

    def _set_cooldown(self, symbol: str) -> None:
        """Включить паузу по символу после сделки."""
        if self._strategy.cooldown_sec > 0:
            self._cooldown_until[symbol] = time.monotonic() + self._strategy.cooldown_sec

    # ------------------------------------------------------------------ #
    # Проверки выхода
    # ------------------------------------------------------------------ #

    def exit_reason(self, position: Position, current_spread_pct: float) -> str | None:
        """Определить, нужно ли закрывать позицию, и по какой причине."""
        if current_spread_pct <= self._strategy.exit_spread_pct:
            return f"спред сузился до {current_spread_pct:.3f}%"
        if current_spread_pct <= self._strategy.exit_on_reverse_pct:
            return f"спред развернулся ({current_spread_pct:.3f}%)"
        if position.age_sec() >= self._strategy.max_position_hold_sec:
            return f"истекло максимальное время удержания ({position.age_sec():.0f} с)"
        return None
