"""Поиск арбитражных возможностей с учётом комиссий бирж.

Логика расчёта для пары бирж A и B по одному символу:

* покупаем на бирже с меньшей ценой предложения (``ask``);
* продаём (открываем шорт) на бирже с большей ценой спроса (``bid``);
* «грязный» спред: ``(bid_sell - ask_buy) / ask_buy * 100``;
* из него вычитаем комиссии тейкера обеих бирж (вход) и столько же на выход,
  а также заложенное проскальзывание;
* если оставшийся ЧИСТЫЙ спред больше ``strategy.min_spread_pct`` — это сигнал.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from itertools import combinations

from ..config import FeesConfig, StrategyConfig
from ..exchanges import ExchangeClient
from ..models import Opportunity, Quote, now_ms

__all__ = ["SpreadFinder", "SpreadSnapshot"]


@dataclass(frozen=True, slots=True)
class SpreadSnapshot:
    """Текущее состояние спреда по символу (для мониторинга и отчётов)."""

    symbol: str
    buy_exchange: str
    sell_exchange: str
    gross_spread_pct: float
    net_spread_pct: float
    fees_pct: float


class SpreadFinder:
    """Считает спреды между всеми парами бирж и отбирает сигналы."""

    def __init__(
        self,
        clients: dict[str, ExchangeClient],
        strategy: StrategyConfig,
        fees: FeesConfig,
    ) -> None:
        self._clients: dict[str, ExchangeClient] = clients
        self._strategy: StrategyConfig = strategy
        self._fees: FeesConfig = fees

    # ------------------------------------------------------------------ #
    # Комиссии
    # ------------------------------------------------------------------ #

    def round_trip_fees_pct(self, symbol: str, buy_exchange: str, sell_exchange: str) -> float:
        """Суммарные комиссии полного цикла (вход + выход) на двух биржах, %.

        Учитывается тип ордера: для ``market`` берётся комиссия тейкера,
        для ``limit`` — мейкера. Дополнительно можно заложить финансирование.
        """
        buy_client = self._clients.get(buy_exchange)
        sell_client = self._clients.get(sell_exchange)
        if buy_client is None or sell_client is None:
            return float("inf")

        buy_fees = buy_client.fees(symbol)
        sell_fees = sell_client.fees(symbol)
        use_maker = self._strategy.order_type == "limit"
        buy_rate = buy_fees.maker if use_maker else buy_fees.taker
        sell_rate = sell_fees.maker if use_maker else sell_fees.taker

        # Комиссия платится на входе и на выходе на каждой из двух бирж.
        total = (buy_rate + sell_rate) * 2.0 * 100.0
        if self._fees.include_funding:
            total += self._fees.funding_pct_per_period
        return total

    # ------------------------------------------------------------------ #
    # Расчёт спредов
    # ------------------------------------------------------------------ #

    def evaluate_pair(
        self, symbol: str, first: Quote, second: Quote
    ) -> SpreadSnapshot | None:
        """Оценить спред между двумя котировками одного символа."""
        # Направление 1: покупаем на first, продаём на second.
        forward = _gross_spread_pct(buy_ask=first.ask, sell_bid=second.bid)
        # Направление 2: покупаем на second, продаём на first.
        backward = _gross_spread_pct(buy_ask=second.ask, sell_bid=first.bid)

        if forward >= backward:
            buy_quote, sell_quote, gross = first, second, forward
        else:
            buy_quote, sell_quote, gross = second, first, backward

        fees_pct = self.round_trip_fees_pct(symbol, buy_quote.exchange, sell_quote.exchange)
        if fees_pct == float("inf"):
            return None
        net = gross - fees_pct - self._strategy.slippage_pct
        return SpreadSnapshot(
            symbol=symbol,
            buy_exchange=buy_quote.exchange,
            sell_exchange=sell_quote.exchange,
            gross_spread_pct=gross,
            net_spread_pct=net,
            fees_pct=fees_pct,
        )

    def snapshots(self, symbol: str, quotes: dict[str, Quote]) -> list[SpreadSnapshot]:
        """Все спреды по символу между всеми комбинациями бирж."""
        result: list[SpreadSnapshot] = []
        for first, second in combinations(quotes.values(), 2):
            snapshot = self.evaluate_pair(symbol, first, second)
            if snapshot is not None:
                result.append(snapshot)
        return result

    def find(
        self,
        symbol: str,
        quotes: dict[str, Quote],
        *,
        amount_quote: float | None = None,
    ) -> list[Opportunity]:
        """Найти сигналы по символу: чистый спред выше порога входа."""
        opportunities: list[Opportunity] = []
        notional = amount_quote or self._strategy.order_amount_quote
        reference_ms = now_ms()

        for first, second in combinations(quotes.values(), 2):
            snapshot = self.evaluate_pair(symbol, first, second)
            if snapshot is None:
                continue
            if not self._passes_thresholds(snapshot):
                continue

            buy_quote = quotes[snapshot.buy_exchange]
            sell_quote = quotes[snapshot.sell_exchange]
            if not self._passes_liquidity(buy_quote, sell_quote, notional):
                continue

            amount = notional / buy_quote.ask if buy_quote.ask > 0 else 0.0
            if amount <= 0.0:
                continue

            opportunities.append(
                Opportunity(
                    symbol=symbol,
                    buy_exchange=snapshot.buy_exchange,
                    sell_exchange=snapshot.sell_exchange,
                    buy_price=buy_quote.ask,
                    sell_price=sell_quote.bid,
                    gross_spread_pct=snapshot.gross_spread_pct,
                    fees_pct=snapshot.fees_pct,
                    slippage_pct=self._strategy.slippage_pct,
                    net_spread_pct=snapshot.net_spread_pct,
                    amount=amount,
                    notional=notional,
                    detected_at_ms=reference_ms,
                )
            )

        opportunities.sort(key=lambda opp: opp.net_spread_pct, reverse=True)
        return opportunities

    def scan(
        self,
        symbols: Iterable[str],
        quotes_by_symbol: dict[str, dict[str, Quote]],
    ) -> list[Opportunity]:
        """Просканировать набор символов и вернуть сигналы по убыванию спреда."""
        found: list[Opportunity] = []
        for symbol in symbols:
            quotes = quotes_by_symbol.get(symbol) or {}
            if len(quotes) < 2:
                continue
            found.extend(self.find(symbol, quotes))
        found.sort(key=lambda opp: opp.net_spread_pct, reverse=True)
        return found

    # ------------------------------------------------------------------ #
    # Фильтры
    # ------------------------------------------------------------------ #

    def _passes_thresholds(self, snapshot: SpreadSnapshot) -> bool:
        """Проверить пороги входа и защиту от аномальных значений."""
        if snapshot.gross_spread_pct > self._strategy.max_spread_pct:
            return False
        return snapshot.net_spread_pct >= self._strategy.min_spread_pct

    def _passes_liquidity(self, buy_quote: Quote, sell_quote: Quote, notional: float) -> bool:
        """Проверить минимальный объём на лучших уровнях стакана."""
        min_volume = self._strategy.min_top_volume_quote
        if min_volume <= 0.0:
            return True
        required = max(min_volume, notional)
        buy_volume = (buy_quote.ask_volume or 0.0) * buy_quote.ask
        sell_volume = (sell_quote.bid_volume or 0.0) * sell_quote.bid
        # Если биржа не отдаёт объёмы, не блокируем сделку.
        if buy_volume <= 0.0 and sell_volume <= 0.0:
            return True
        return buy_volume >= required and sell_volume >= required


def _gross_spread_pct(*, buy_ask: float, sell_bid: float) -> float:
    """«Грязный» спред в процентах для конкретного направления."""
    if buy_ask <= 0.0:
        return float("-inf")
    return (sell_bid - buy_ask) / buy_ask * 100.0


def top_snapshots(snapshots: Sequence[SpreadSnapshot], limit: int = 5) -> list[SpreadSnapshot]:
    """Лучшие спреды для вывода в консоль."""
    return sorted(snapshots, key=lambda s: s.net_spread_pct, reverse=True)[:limit]
