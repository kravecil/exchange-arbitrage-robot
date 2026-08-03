"""Исполнение арбитражных сделок в двух режимах.

* ``paper`` — тестовый режим: реальные ордера НЕ отправляются, робот просто
  печатает в консоль/лог сообщение о том, что мог бы сделать ставку.
* ``live`` — боевой режим: ордера отправляются на биржи через ccxt.

Обе ноги отправляются одновременно (``asyncio.gather``). Если одна нога
исполнилась, а вторая упала — робот немедленно откатывает исполненную ногу
обратным ордером, чтобы не остаться с направленной позицией.
"""

from __future__ import annotations

import asyncio
from typing import Any

from ..config import AppConfig
from ..exchanges import ExchangeClient
from ..logging_setup import get_logger
from ..models import (
    ExecutionReport,
    LegReport,
    Opportunity,
    OrderSide,
    Position,
    TradeMode,
)

__all__ = ["TradeExecutor"]


class TradeExecutor:
    """Открывает и закрывает арбитражные позиции согласно выбранному режиму."""

    def __init__(self, config: AppConfig, clients: dict[str, ExchangeClient]) -> None:
        self._config: AppConfig = config
        self._clients: dict[str, ExchangeClient] = clients
        self.log = get_logger("executor")

    @property
    def mode(self) -> TradeMode:
        """Текущий режим торговли."""
        return self._config.mode

    @property
    def is_live(self) -> bool:
        """Отправляются ли реальные ордера."""
        return self._config.mode is TradeMode.LIVE

    # ------------------------------------------------------------------ #
    # Вход в позицию
    # ------------------------------------------------------------------ #

    async def open_position(
        self, opportunity: Opportunity
    ) -> tuple[ExecutionReport, Position | None]:
        """Войти в арбитраж: лонг на дешёвой бирже, шорт на дорогой."""
        buy_client = self._clients.get(opportunity.buy_exchange)
        sell_client = self._clients.get(opportunity.sell_exchange)
        if buy_client is None or sell_client is None:
            return (
                ExecutionReport(
                    success=False,
                    action="open",
                    symbol=opportunity.symbol,
                    error="Не найден клиент одной из бирж",
                    mode=self.mode,
                ),
                None,
            )

        amount = self._normalize_amount(buy_client, sell_client, opportunity)
        if amount <= 0.0:
            return (
                ExecutionReport(
                    success=False,
                    action="open",
                    symbol=opportunity.symbol,
                    error="Объём после округления равен нулю",
                    mode=self.mode,
                ),
                None,
            )

        if (error := self._check_limits(buy_client, sell_client, opportunity, amount)) is not None:
            return (
                ExecutionReport(
                    success=False,
                    action="open",
                    symbol=opportunity.symbol,
                    error=error,
                    mode=self.mode,
                ),
                None,
            )

        self._log_signal(opportunity, amount)

        if not self.is_live:
            legs = (
                self._simulated_leg(buy_client, opportunity.symbol, OrderSide.BUY, amount,
                                    opportunity.buy_price),
                self._simulated_leg(sell_client, opportunity.symbol, OrderSide.SELL, amount,
                                    opportunity.sell_price),
            )
            report = ExecutionReport(
                success=True,
                action="open",
                symbol=opportunity.symbol,
                legs=legs,
                mode=self.mode,
            )
            return report, self._build_position(opportunity, amount)

        await asyncio.gather(
            buy_client.prepare_symbol(opportunity.symbol),
            sell_client.prepare_symbol(opportunity.symbol),
        )

        buy_result, sell_result = await asyncio.gather(
            self._place(buy_client, opportunity.symbol, OrderSide.BUY, amount,
                        opportunity.buy_price),
            self._place(sell_client, opportunity.symbol, OrderSide.SELL, amount,
                        opportunity.sell_price),
            return_exceptions=True,
        )

        buy_ok = isinstance(buy_result, LegReport)
        sell_ok = isinstance(sell_result, LegReport)

        if buy_ok and sell_ok:
            report = ExecutionReport(
                success=True,
                action="open",
                symbol=opportunity.symbol,
                legs=(buy_result, sell_result),
                mode=self.mode,
            )
            return report, self._build_position(opportunity, amount)

        # Частичное исполнение — откатываем удавшуюся ногу.
        error_parts: list[str] = []
        if not buy_ok:
            error_parts.append(f"BUY {opportunity.buy_exchange}: {buy_result}")
        if not sell_ok:
            error_parts.append(f"SELL {opportunity.sell_exchange}: {sell_result}")
        error = "; ".join(error_parts)
        self.log.error("Ошибка входа %s — %s", opportunity.symbol, error)

        if buy_ok:
            await self._rollback(buy_client, opportunity.symbol, OrderSide.BUY, amount)
        if sell_ok:
            await self._rollback(sell_client, opportunity.symbol, OrderSide.SELL, amount)

        return (
            ExecutionReport(
                success=False,
                action="open",
                symbol=opportunity.symbol,
                error=error,
                mode=self.mode,
            ),
            None,
        )

    # ------------------------------------------------------------------ #
    # Выход из позиции
    # ------------------------------------------------------------------ #

    async def close_position(
        self, position: Position, long_price: float, short_price: float, reason: str
    ) -> ExecutionReport:
        """Закрыть обе ноги позиции (обратные ордера с reduceOnly)."""
        long_client = self._clients.get(position.long_exchange)
        short_client = self._clients.get(position.short_exchange)
        if long_client is None or short_client is None:
            return ExecutionReport(
                success=False,
                action="close",
                symbol=position.symbol,
                error="Не найден клиент одной из бирж",
                mode=self.mode,
            )

        self.log.info(
            "[%s] Закрытие %s (%s → %s), причина: %s",
            self.mode.value.upper(),
            position.symbol,
            position.long_exchange,
            position.short_exchange,
            reason,
        )

        if not self.is_live:
            legs = (
                self._simulated_leg(long_client, position.symbol, OrderSide.SELL,
                                    position.amount, long_price),
                self._simulated_leg(short_client, position.symbol, OrderSide.BUY,
                                    position.amount, short_price),
            )
            return ExecutionReport(
                success=True,
                action="close",
                symbol=position.symbol,
                legs=legs,
                mode=self.mode,
            )

        long_result, short_result = await asyncio.gather(
            self._place(long_client, position.symbol, OrderSide.SELL, position.amount,
                        long_price, reduce_only=True),
            self._place(short_client, position.symbol, OrderSide.BUY, position.amount,
                        short_price, reduce_only=True),
            return_exceptions=True,
        )

        legs = tuple(leg for leg in (long_result, short_result) if isinstance(leg, LegReport))
        errors = [str(res) for res in (long_result, short_result) if not isinstance(res, LegReport)]
        if errors:
            self.log.error("Ошибка закрытия %s: %s", position.symbol, "; ".join(errors))
        return ExecutionReport(
            success=not errors,
            action="close",
            symbol=position.symbol,
            legs=legs,
            error="; ".join(errors) or None,
            mode=self.mode,
        )

    # ------------------------------------------------------------------ #
    # Вспомогательные методы
    # ------------------------------------------------------------------ #

    async def _place(
        self,
        client: ExchangeClient,
        symbol: str,
        side: OrderSide,
        amount: float,
        reference_price: float,
        *,
        reduce_only: bool = False,
    ) -> LegReport:
        """Отправить реальный ордер и вернуть отчёт по ноге."""
        strategy = self._config.strategy
        price: float | None = None
        if strategy.order_type == "limit":
            offset = strategy.limit_price_offset_pct / 100.0
            raw_price = (
                reference_price * (1.0 + offset)
                if side is OrderSide.BUY
                else reference_price * (1.0 - offset)
            )
            price = client.price_to_precision(symbol, raw_price)

        order: dict[str, Any] = await client.create_order(
            symbol,
            side,
            amount,
            order_type=strategy.order_type,
            price=price,
            reduce_only=reduce_only,
        )
        filled_price = order.get("average") or order.get("price") or reference_price
        return LegReport(
            exchange=client.id,
            symbol=symbol,
            side=side,
            amount=float(order.get("amount") or amount),
            price=float(filled_price),
            order_id=str(order.get("id")) if order.get("id") is not None else None,
            simulated=False,
            raw=order,
        )

    async def _rollback(
        self, client: ExchangeClient, symbol: str, side: OrderSide, amount: float
    ) -> None:
        """Аварийно закрыть одну исполненную ногу обратным ордером."""
        self.log.warning("Откат ноги %s %s на %s", side.value, symbol, client.id)
        try:
            await client.create_order(
                symbol, side.opposite, amount, order_type="market", reduce_only=True
            )
        except Exception as exc:  # noqa: BLE001 - критично, но продолжать работу нужно
            self.log.critical(
                "НЕ УДАЛОСЬ откатить ногу %s на %s: %s. Закройте позицию вручную!",
                symbol,
                client.id,
                exc,
            )

    def _simulated_leg(
        self,
        client: ExchangeClient,
        symbol: str,
        side: OrderSide,
        amount: float,
        price: float,
    ) -> LegReport:
        """Сформировать «виртуальную» ногу для тестового режима."""
        self.log.info(
            "[PAPER] %s %s %.8f @ %.8f на %s (ордер не отправлен)",
            side.value.upper(),
            symbol,
            amount,
            price,
            client.id,
        )
        return LegReport(
            exchange=client.id,
            symbol=symbol,
            side=side,
            amount=amount,
            price=price,
            order_id=None,
            simulated=True,
        )

    def _normalize_amount(
        self,
        buy_client: ExchangeClient,
        sell_client: ExchangeClient,
        opportunity: Opportunity,
    ) -> float:
        """Согласовать объём с точностью обеих бирж (берём минимальный общий)."""
        amount = min(
            buy_client.amount_to_precision(opportunity.symbol, opportunity.amount),
            sell_client.amount_to_precision(opportunity.symbol, opportunity.amount),
        )
        return max(0.0, amount)

    def _check_limits(
        self,
        buy_client: ExchangeClient,
        sell_client: ExchangeClient,
        opportunity: Opportunity,
        amount: float,
    ) -> str | None:
        """Проверить лимиты рынков обеих бирж."""
        for client, price in (
            (buy_client, opportunity.buy_price),
            (sell_client, opportunity.sell_price),
        ):
            if (error := client.check_limits(opportunity.symbol, amount, price)) is not None:
                return error
        return None

    def _build_position(self, opportunity: Opportunity, amount: float) -> Position:
        """Создать объект позиции по исполненной возможности."""
        return Position(
            symbol=opportunity.symbol,
            long_exchange=opportunity.buy_exchange,
            short_exchange=opportunity.sell_exchange,
            amount=amount,
            entry_long_price=opportunity.buy_price,
            entry_short_price=opportunity.sell_price,
            entry_net_spread_pct=opportunity.net_spread_pct,
            entry_fees_pct=opportunity.fees_pct,
        )

    def _log_signal(self, opportunity: Opportunity, amount: float) -> None:
        """Вывести подробную информацию о сигнале."""
        prefix = "[LIVE]" if self.is_live else "[PAPER]"
        self.log.info(
            "%s Сигнал %s | %s | спред %.3f%% - комиссии %.3f%% - проскальзывание %.3f%% "
            "= чистыми %.3f%% | объём %.8f (%.2f %s) | ожидаемая прибыль %.4f %s",
            prefix,
            opportunity.symbol,
            opportunity.route,
            opportunity.gross_spread_pct,
            opportunity.fees_pct,
            opportunity.slippage_pct,
            opportunity.net_spread_pct,
            amount,
            opportunity.notional,
            self._config.quote_currency,
            opportunity.expected_profit,
            self._config.quote_currency,
        )
