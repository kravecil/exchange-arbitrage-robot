"""Главный движок робота: подключение бирж, потоки котировок, торговый цикл."""

from __future__ import annotations

import asyncio
from collections import Counter
from typing import Any

from ..config import AppConfig
from ..exchanges import ExchangeClient, create_clients
from ..logging_setup import console, get_logger
from ..models import Opportunity, Position, Quote, TradeMode, now_ms
from .executor import TradeExecutor
from .market_data import QuoteBook
from .notifications import OpportunityInfo, TelegramNotifier
from .risk import RiskManager
from .spread import SpreadFinder

__all__ = ["ArbitrageEngine"]


class ArbitrageEngine:
    """Оркестратор: связывает биржи, котировки, поиск спредов и исполнение."""

    def __init__(self, config: AppConfig) -> None:
        self.config: AppConfig = config
        self.log = get_logger("engine")
        self.clients: dict[str, ExchangeClient] = create_clients(
            config.enabled_exchanges, config.fees
        )
        self.book: QuoteBook = QuoteBook(max_age_ms=config.strategy.max_quote_age_ms)
        self.finder: SpreadFinder = SpreadFinder(self.clients, config.strategy, config.fees)
        self.risk: RiskManager = RiskManager(config.risk, config.strategy)
        self.executor: TradeExecutor = TradeExecutor(config, self.clients)
        self.notifier: TelegramNotifier = TelegramNotifier(config.telegram)
        self.symbols_by_exchange: dict[str, list[str]] = {}
        self.common_symbols: list[str] = []
        self.trading_enabled: bool = True
        """Если ``False`` — робот только мониторит спреды и не открывает позиции."""
        self._stop_event: asyncio.Event = asyncio.Event()

    # ------------------------------------------------------------------ #
    # Подготовка
    # ------------------------------------------------------------------ #

    async def prepare(self) -> None:
        """Загрузить рынки, отобрать общие символы, проверить ключи."""
        if len(self.clients) < 2:
            raise RuntimeError(
                "Для межбиржевого арбитража нужно минимум две включённые биржи в config.yaml"
            )

        self.log.info("Подключение к биржам: %s", ", ".join(self.clients))
        await asyncio.gather(*(client.load_markets() for client in self.clients.values()))
        self._select_symbols()
        self._check_credentials()
        await self._prepare_notifier()

    def _select_symbols(self) -> None:
        """Отобрать пары, доступные минимум на двух биржах."""
        rules = self.config.symbols
        per_exchange: dict[str, set[str]] = {
            exchange_id: client.select_symbols(rules)
            for exchange_id, client in self.clients.items()
        }
        for exchange_id, symbols in per_exchange.items():
            self.log.info("%s: подходящих фьючерсных пар — %d", exchange_id, len(symbols))

        counter: Counter[str] = Counter()
        for symbols in per_exchange.values():
            counter.update(symbols)
        common = [symbol for symbol, count in counter.items() if count >= 2]

        if rules.mode == "manual":
            ordered = [symbol for symbol in rules.include if symbol in set(common)]
        else:
            priority = {symbol: index for index, symbol in enumerate(rules.include)}
            ordered = sorted(common, key=lambda s: (priority.get(s, len(priority)), s))

        self.common_symbols = ordered[: rules.max_symbols]
        if not self.common_symbols:
            raise RuntimeError(
                "Не найдено ни одной пары, доступной минимум на двух биржах. "
                "Проверьте секцию symbols в config.yaml."
            )

        common_set = set(self.common_symbols)
        self.symbols_by_exchange = {
            exchange_id: sorted(symbols & common_set)[
                : self.clients[exchange_id].config.ws_symbol_limit
            ]
            for exchange_id, symbols in per_exchange.items()
        }
        self.log.info(
            "Отслеживаем %d общих пар (лимит %d). Примеры: %s",
            len(self.common_symbols),
            rules.max_symbols,
            ", ".join(self.common_symbols[:5]),
        )

    def _check_credentials(self) -> None:
        """Проверить наличие ключей для боевого режима."""
        if not self.config.is_live:
            self.log.warning(
                "Режим PAPER: реальные ордера не отправляются, сделки только логируются"
            )
            return
        missing = [
            client.id for client in self.clients.values() if not client.config.has_credentials()
        ]
        if missing:
            raise RuntimeError(
                "Режим LIVE требует API-ключей. Не заданы ключи для: " + ", ".join(missing)
            )
        self.log.warning("Режим LIVE: робот будет отправлять РЕАЛЬНЫЕ ордера!")

    # ------------------------------------------------------------------ #
    # Запуск
    # ------------------------------------------------------------------ #

    async def run(self) -> None:
        """Запустить робота: потоки котировок + торговый цикл + статус."""
        await self.prepare()

        tasks: list[asyncio.Task[None]] = [
            asyncio.create_task(self._stream(client), name=f"stream:{client.id}")
            for client in self.clients.values()
        ]
        tasks.append(asyncio.create_task(self._trading_loop(), name="trading"))
        tasks.append(asyncio.create_task(self._status_loop(), name="status"))

        try:
            await self._stop_event.wait()
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await self.shutdown()

    def stop(self) -> None:
        """Запросить корректную остановку робота."""
        self._stop_event.set()

    async def shutdown(self) -> None:
        """Закрыть соединения и вывести итоговую статистику."""
        await asyncio.gather(
            *(client.close() for client in self.clients.values()), return_exceptions=True
        )
        await self._stop_notifier()
        self.print_summary()

    async def _prepare_notifier(self) -> None:
        """Подготовить notifier для отправки уведомлений."""
        try:
            await self.notifier.connect()
            await self.notifier.notify_start()
        except ValueError as exc:
            self.log.warning("Не удалось настроить уведомления: %s", exc)
        except RuntimeError as exc:
            self.log.error("Ошибка подключения к Telegram: %s", exc)

    async def _stop_notifier(self) -> None:
        """Остановить notifier."""
        await self.notifier.notify_stop()
        await self.notifier.disconnect()

    async def _notify_opportunities(self, opportunities: list[Opportunity]) -> None:
        """
        Отправить уведомления о найденных возможностях.

        :param opportunities: Список найденных возможностей.
        """
        for opportunity in opportunities:
            info = OpportunityInfo(
                symbol=opportunity.symbol,
                buy_exchange=opportunity.buy_exchange,
                sell_exchange=opportunity.sell_exchange,
                net_spread_pct=opportunity.net_spread_pct,
                gross_spread_pct=opportunity.gross_spread_pct,
                fees_pct=opportunity.fees_pct,
                amount=opportunity.amount,
                notional=opportunity.notional,
                route=opportunity.route,
            )
            await self.notifier.notify_opportunity(info)

    async def _stream(self, client: ExchangeClient) -> None:
        """Поток котировок одной биржи."""
        symbols = self.symbols_by_exchange.get(client.id, [])
        self.log.info("[%s] Подписка на %d пар по websocket", client.id, len(symbols))
        try:
            await client.stream_quotes(symbols, self._on_quote)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            self.log.error("[%s] Поток котировок остановлен: %s", client.id, exc)

    def _on_quote(self, quote: Quote) -> None:
        """Коллбэк обновления котировки."""
        self.book.update(quote)

    # ------------------------------------------------------------------ #
    # Торговый цикл
    # ------------------------------------------------------------------ #

    async def _trading_loop(self) -> None:
        """Периодически ищет сигналы и управляет позициями."""
        interval = self.config.scan_interval_sec
        while not self._stop_event.is_set():
            try:
                await self._manage_positions()
                await self._scan_and_trade()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self.log.exception("Ошибка торгового цикла: %s", exc)
            await asyncio.sleep(interval)

    async def _scan_and_trade(self) -> None:
        """Найти лучшие возможности и попытаться войти."""
        reference_ms = now_ms()
        quotes_by_symbol: dict[str, dict[str, Quote]] = {}
        for symbol in self.common_symbols:
            quotes = self.book.fresh_by_symbol(symbol, reference_ms)
            if len(quotes) >= 2:
                quotes_by_symbol[symbol] = quotes

        opportunities = self.finder.scan(quotes_by_symbol.keys(), quotes_by_symbol)
        if not opportunities:
            return

        self.risk.stats.opportunities_found += len(opportunities)
        best = opportunities[0].net_spread_pct
        self.risk.stats.best_spread_pct = max(self.risk.stats.best_spread_pct, best)

        # Уведомление о найденных возможностях
        await self._notify_opportunities(opportunities)

        if not self.trading_enabled:
            for opportunity in opportunities[:3]:
                self.log.info(
                    "[МОНИТОРИНГ] %s | %s | чистый спред %.3f%% (комиссии %.3f%%)",
                    opportunity.symbol,
                    opportunity.route,
                    opportunity.net_spread_pct,
                    opportunity.fees_pct,
                )
            return

        for opportunity in opportunities:
            reason = self.risk.can_open(opportunity)
            if reason is not None:
                self.log.debug("Пропуск %s: %s", opportunity.symbol, reason)
                continue
            if not await self._has_balance(opportunity):
                continue
            await self._open(opportunity)

    async def _open(self, opportunity: Opportunity) -> None:
        """Открыть позицию по возможности."""
        report, position = await self.executor.open_position(opportunity)
        if report.success and position is not None:
            self.risk.register_open(position)
            self.log.info(
                "Позиция %s открыта: %s, объём %.8f, вход по спреду %.3f%%",
                position.id,
                position.symbol,
                position.amount,
                position.entry_net_spread_pct,
            )
        else:
            self.risk.register_failure()

    async def _has_balance(self, opportunity: Opportunity) -> bool:
        """Проверить свободный баланс на обеих биржах (только live-режим)."""
        if not self.config.is_live or not self.config.risk.require_balance_check:
            return True
        required = max(
            opportunity.notional / max(1, self._leverage(opportunity.buy_exchange)),
            self.config.risk.min_free_balance_quote,
        )
        for exchange_id in (opportunity.buy_exchange, opportunity.sell_exchange):
            client = self.clients[exchange_id]
            free = await client.fetch_free_balance(self.config.quote_currency)
            if free < required:
                self.log.warning(
                    "Недостаточно средств на %s: %.2f < %.2f %s",
                    exchange_id,
                    free,
                    required,
                    self.config.quote_currency,
                )
                return False
        return True

    def _leverage(self, exchange_id: str) -> int:
        """Плечо биржи из конфигурации."""
        client = self.clients.get(exchange_id)
        return client.config.leverage if client is not None else 1

    async def _manage_positions(self) -> None:
        """Проверить открытые позиции и закрыть те, что достигли условий выхода."""
        reference_ms = now_ms()
        for position in self.risk.iter_positions():
            long_quote = self.book.fresh(position.symbol, position.long_exchange, reference_ms)
            short_quote = self.book.fresh(position.symbol, position.short_exchange, reference_ms)
            if long_quote is None or short_quote is None:
                continue

            current_spread = position.current_spread_pct(long_quote, short_quote)
            reason = self.risk.exit_reason(position, current_spread)
            if reason is None:
                continue
            await self._close(position, long_quote, short_quote, reason)

    async def _close(
        self, position: Position, long_quote: Quote, short_quote: Quote, reason: str
    ) -> None:
        """Закрыть позицию и учесть результат."""
        report = await self.executor.close_position(
            position, long_quote.bid, short_quote.ask, reason
        )
        if not report.success:
            self.risk.register_failure()
            return

        pnl_pct = position.unrealized_pnl_pct(long_quote, short_quote)
        fees = position.entry_fees_pct
        realized = position.notional * (pnl_pct - fees) / 100.0
        self.risk.register_close(position, realized)
        self.log.info(
            "Позиция %s закрыта: %s | результат %.4f %s (%.3f%% до комиссий)",
            position.id,
            position.symbol,
            realized,
            self.config.quote_currency,
            pnl_pct,
        )

    # ------------------------------------------------------------------ #
    # Статус
    # ------------------------------------------------------------------ #

    async def _status_loop(self) -> None:
        """Периодически печатать краткий статус в консоль."""
        interval = self.config.status_interval_sec
        while not self._stop_event.is_set():
            await asyncio.sleep(interval)
            if self.config.enable_status_logging:
                self.print_status()

    def print_status(self) -> None:
        """Вывести текущее состояние робота."""
        stats = self.risk.stats
        best = self._best_current_spread()
        best_text = (
            f"{best['symbol']} {best['route']} {best['net']:.3f}%" if best is not None else "—"
        )
        self.log.info(
            "Статус [%s]: пар %d | обновлений %d | сигналов %d | позиций %d | PnL %.4f %s "
            "| лучший спред сейчас: %s",
            self.config.mode.value.upper(),
            self.book.symbols_count,
            self.book.updates,
            stats.opportunities_found,
            self.risk.open_count,
            stats.realized_pnl,
            self.config.quote_currency,
            best_text,
        )

    def _best_current_spread(self) -> dict[str, Any] | None:
        """Найти лучший текущий спред для отображения (без фильтров входа)."""
        reference_ms = now_ms()
        best: dict[str, Any] | None = None
        for symbol in self.common_symbols:
            quotes = self.book.fresh_by_symbol(symbol, reference_ms)
            if len(quotes) < 2:
                continue
            for snapshot in self.finder.snapshots(symbol, quotes):
                if best is None or snapshot.net_spread_pct > best["net"]:
                    best = {
                        "symbol": symbol,
                        "route": f"{snapshot.buy_exchange}→{snapshot.sell_exchange}",
                        "net": snapshot.net_spread_pct,
                    }
        return best

    def print_summary(self) -> None:
        """Итоговый отчёт при остановке."""
        stats = self.risk.stats
        mode = "РЕАЛЬНЫЙ (LIVE)" if self.config.mode is TradeMode.LIVE else "ТЕСТОВЫЙ (PAPER)"
        console.print(
            "\n[bold]Итоги сессии[/bold]\n"
            f"  Режим:                {mode}\n"
            f"  Время работы:         {stats.uptime_sec:.0f} с\n"
            f"  Обновлений котировок: {self.book.updates}\n"
            f"  Найдено сигналов:     {stats.opportunities_found}\n"
            f"  Открыто позиций:      {stats.positions_opened}\n"
            f"  Закрыто позиций:      {stats.positions_closed}\n"
            f"  Ошибок исполнения:    {stats.failed_executions}\n"
            f"  Лучший чистый спред:  {stats.best_spread_pct:.3f} %\n"
            f"  Результат:            {stats.realized_pnl:.4f} {self.config.quote_currency}\n"
        )
        if self.risk.open_count:
            console.print(
                f"[yellow]Внимание: осталось открытых позиций — {self.risk.open_count}. "
                "Проверьте биржи вручную.[/yellow]"
            )
