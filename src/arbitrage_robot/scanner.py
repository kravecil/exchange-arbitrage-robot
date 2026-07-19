from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal

from arbitrage_robot.config import AppConfig
from arbitrage_robot.exchanges import ExchangeManager
from arbitrage_robot.models import ArbitrageOpportunity, SpreadDirection, TickerSnapshot

logger = logging.getLogger(__name__)


class ArbitrageScanner:
    """Сканирует цены на биржах и находит арбитражные возможности."""

    def __init__(
        self,
        manager: ExchangeManager,
        config: AppConfig,
        symbols: list[str],
    ) -> None:
        self._manager = manager
        self._config = config
        self._symbols = symbols

    async def scan(self) -> list[ArbitrageOpportunity]:
        snapshots = await self._fetch_snapshots()
        return self._find_opportunities(snapshots)

    async def _fetch_snapshots(self) -> dict[str, dict[str, TickerSnapshot]]:
        by_symbol: dict[str, dict[str, TickerSnapshot]] = {}

        for exchange_id, exchange in self._manager.exchanges.items():
            try:
                tickers = await exchange.fetch_tickers(self._symbols)
            except Exception:
                logger.exception("Не удалось получить тикеры с биржи %s", exchange_id)
                continue

            for symbol in self._symbols:
                ticker = tickers.get(symbol)
                if not ticker:
                    continue

                bid = ticker.get("bid")
                ask = ticker.get("ask")
                if bid is None or ask is None or bid <= 0 or ask <= 0:
                    continue

                snapshot = TickerSnapshot(
                    symbol=symbol,
                    exchange_id=exchange_id,
                    bid=Decimal(str(bid)),
                    ask=Decimal(str(ask)),
                    timestamp=datetime.now(UTC),
                )
                by_symbol.setdefault(symbol, {})[exchange_id] = snapshot

        return by_symbol

    def _find_opportunities(
        self,
        snapshots: dict[str, dict[str, TickerSnapshot]],
    ) -> list[ArbitrageOpportunity]:
        opportunities: list[ArbitrageOpportunity] = []
        exchange_ids = self._manager.exchange_ids

        for symbol, exchange_snapshots in snapshots.items():
            if len(exchange_snapshots) < 2:
                continue

            for buy_exchange in exchange_ids:
                buy_snapshot = exchange_snapshots.get(buy_exchange)
                if not buy_snapshot:
                    continue

                for sell_exchange in exchange_ids:
                    if sell_exchange == buy_exchange:
                        continue

                    sell_snapshot = exchange_snapshots.get(sell_exchange)
                    if not sell_snapshot:
                        continue

                    opportunity = self._evaluate_pair(
                        buy_snapshot=buy_snapshot,
                        sell_snapshot=sell_snapshot,
                    )
                    if opportunity:
                        opportunities.append(opportunity)

        opportunities.sort(key=lambda item: item.net_spread_percent, reverse=True)
        return opportunities

    def _evaluate_pair(
        self,
        buy_snapshot: TickerSnapshot,
        sell_snapshot: TickerSnapshot,
    ) -> ArbitrageOpportunity | None:
        buy_price = buy_snapshot.ask
        sell_price = sell_snapshot.bid

        if sell_price <= buy_price:
            return None

        gross_spread = (sell_price - buy_price) / buy_price * Decimal("100")

        buy_fee = Decimal(str(self._manager.get_taker_fee(buy_snapshot.exchange_id, buy_snapshot.symbol)))
        sell_fee = Decimal(str(self._manager.get_taker_fee(sell_snapshot.exchange_id, sell_snapshot.symbol)))
        buy_fee_percent = buy_fee * Decimal("100")
        sell_fee_percent = sell_fee * Decimal("100")

        if self._config.scanner.include_fees:
            net_spread = gross_spread - buy_fee_percent - sell_fee_percent
        else:
            net_spread = gross_spread

        direction = self._resolve_direction(buy_snapshot.exchange_id, sell_snapshot.exchange_id)
        threshold = self._threshold_for_direction(direction)

        if net_spread < Decimal(str(threshold)):
            return None

        return ArbitrageOpportunity(
            symbol=buy_snapshot.symbol,
            buy_exchange=buy_snapshot.exchange_id,
            sell_exchange=sell_snapshot.exchange_id,
            buy_price=buy_price,
            sell_price=sell_price,
            spread_percent=gross_spread.quantize(Decimal("0.0001")),
            net_spread_percent=net_spread.quantize(Decimal("0.0001")),
            direction=direction,
            buy_fee_percent=buy_fee_percent.quantize(Decimal("0.0001")),
            sell_fee_percent=sell_fee_percent.quantize(Decimal("0.0001")),
            timestamp=datetime.now(UTC),
        )

    def _resolve_direction(self, buy_exchange: str, sell_exchange: str) -> SpreadDirection:
        if buy_exchange < sell_exchange:
            return SpreadDirection.UP
        return SpreadDirection.DOWN

    def _threshold_for_direction(self, direction: SpreadDirection) -> float:
        if direction == SpreadDirection.UP:
            return self._config.scanner.min_spread_percent_up
        return self._config.scanner.min_spread_percent_down
