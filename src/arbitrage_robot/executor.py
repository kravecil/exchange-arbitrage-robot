from __future__ import annotations

import logging
from abc import ABC, abstractmethod

from arbitrage_robot.exchanges import ExchangeManager
from arbitrage_robot.models import ArbitrageOpportunity

logger = logging.getLogger(__name__)


class OpportunityExecutor(ABC):
    @abstractmethod
    async def execute(self, opportunity: ArbitrageOpportunity) -> None:
        """Обрабатывает найденную арбитражную возможность."""


class PaperExecutor(OpportunityExecutor):
    """Тестовый режим: выводит возможность в консоль без реальных сделок."""

    async def execute(self, opportunity: ArbitrageOpportunity) -> None:
        message = (
            f"[PAPER] {opportunity.symbol}: "
            f"купить на {opportunity.buy_exchange} @ {opportunity.buy_price}, "
            f"продать на {opportunity.sell_exchange} @ {opportunity.sell_price} | "
            f"спред {opportunity.spread_percent}% (чистый {opportunity.net_spread_percent}%), "
            f"направление {opportunity.direction_label}, "
            f"комиссии {opportunity.buy_fee_percent}% + {opportunity.sell_fee_percent}%"
        )
        print(message)
        logger.info(message)


class LiveExecutor(OpportunityExecutor):
    """Режим реальной торговли: размещает рыночные ордера на обеих биржах."""

    def __init__(self, manager: ExchangeManager, order_amount_quote: float = 10.0) -> None:
        self._manager = manager
        self._order_amount_quote = order_amount_quote

    async def execute(self, opportunity: ArbitrageOpportunity) -> None:
        buy_exchange = self._manager.exchanges[opportunity.buy_exchange]
        sell_exchange = self._manager.exchanges[opportunity.sell_exchange]

        amount = self._order_amount_quote / float(opportunity.buy_price)

        logger.warning(
            "LIVE: исполнение %s — покупка %.8f на %s, продажа на %s",
            opportunity.symbol,
            amount,
            opportunity.buy_exchange,
            opportunity.sell_exchange,
        )

        try:
            buy_order = await buy_exchange.create_market_buy_order(
                opportunity.symbol,
                amount,
            )
            sell_order = await sell_exchange.create_market_sell_order(
                opportunity.symbol,
                amount,
            )
            logger.info(
                "LIVE: ордера исполнены — buy=%s sell=%s",
                buy_order.get("id"),
                sell_order.get("id"),
            )
        except Exception:
            logger.exception(
                "LIVE: ошибка исполнения арбитража %s (%s -> %s)",
                opportunity.symbol,
                opportunity.buy_exchange,
                opportunity.sell_exchange,
            )


def create_executor(mode: str, manager: ExchangeManager) -> OpportunityExecutor:
    if mode == "live":
        return LiveExecutor(manager)
    return PaperExecutor()
