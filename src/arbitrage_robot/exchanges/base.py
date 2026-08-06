"""Универсальный адаптер биржи поверх ccxt / ccxt.pro.

Класс :class:`ExchangeClient` рассчитан на то, чтобы работать с любой биржей,
поддерживаемой ccxt, без написания кода: достаточно добавить секцию в
``config.yaml``. Если для конкретной биржи нужны нестандартные действия
(особые параметры ордера, режим позиций и т.п.) — наследуйтесь от этого класса
и зарегистрируйте наследника в :mod:`arbitrage_robot.exchanges.registry`.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable, Sequence
from typing import Any, Final

import ccxt.pro as ccxtpro
from ccxt.base.errors import NotSupported

from ..config import ExchangeConfig, FeesConfig, SymbolsConfig
from ..logging_setup import get_logger
from ..models import FeeSchedule, OrderSide, Quote, now_ms

__all__ = ["ExchangeClient", "QuoteCallback"]

QuoteCallback = Callable[[Quote], Awaitable[None] | None]

_RECONNECT_DELAY_SEC: Final[float] = 2.0
_MAX_RECONNECT_DELAY_SEC: Final[float] = 30.0


class ExchangeClient:
    """Асинхронный клиент одной биржи (фьючерсы).

    Отвечает за:

    * создание и настройку экземпляра ccxt.pro;
    * загрузку рынков и отбор торговых пар;
    * получение комиссий (из ccxt или из конфигурации);
    * real-time поток котировок по websocket;
    * размещение ордеров (или их симуляцию — этим занимается executor).
    """

    def __init__(self, config: ExchangeConfig, fees_config: FeesConfig) -> None:
        self.config: ExchangeConfig = config
        self.fees_config: FeesConfig = fees_config
        self.log = get_logger(f"exchange.{config.id}")
        self._exchange: ccxtpro.Exchange = self._create_exchange()
        self._markets: dict[str, Any] = {}
        self._leverage_done: set[str] = set()
        self._closed: bool = False

    # ------------------------------------------------------------------ #
    # Инициализация
    # ------------------------------------------------------------------ #

    @property
    def id(self) -> str:
        """Идентификатор биржи."""
        return self.config.id

    @property
    def exchange(self) -> ccxtpro.Exchange:
        """Низкоуровневый объект ccxt.pro (на случай специфичных вызовов)."""
        return self._exchange

    @property
    def markets(self) -> dict[str, Any]:
        """Загруженные рынки биржи."""
        return self._markets

    def _create_exchange(self) -> ccxtpro.Exchange:
        """Создать экземпляр ccxt.pro по конфигурации."""
        exchange_cls = getattr(ccxtpro, self.config.id, None)
        if exchange_cls is None:
            raise ValueError(
                f"Биржа '{self.config.id}' не поддерживается ccxt.pro. "
                "Проверьте идентификатор в config.yaml."
            )

        options: dict[str, Any] = {
            "defaultType": self.config.default_type_override or self.config.market_type,
            **self.config.options,
        }
        params: dict[str, Any] = {
            "enableRateLimit": True,
            "newUpdates": True,
            "options": options,
            **self.config.credentials(),
        }

        exchange: ccxtpro.Exchange = exchange_cls(params)
        if self.config.sandbox:
            exchange.set_sandbox_mode(True)
            self.log.warning("Включён sandbox/testnet режим")
        return exchange

    async def load_markets(self, reload: bool = False) -> dict[str, Any]:
        """Загрузить (или перезагрузить) список рынков биржи."""
        markets: dict[str, Any] = await self._exchange.load_markets(reload)
        self._markets = markets
        self.log.debug("Загружено рынков: %d", len(markets))
        return markets

    # ------------------------------------------------------------------ #
    # Рынки, символы, комиссии
    # ------------------------------------------------------------------ #

    def select_symbols(self, rules: SymbolsConfig) -> set[str]:
        """Отобрать подходящие фьючерсные пары по правилам конфигурации."""
        selected: set[str] = set()
        for symbol, market in self._markets.items():
            if not self._is_tradable_future(market, rules):
                continue
            selected.add(symbol)

        if rules.mode == "manual":
            selected &= set(rules.include)
        elif rules.include:
            selected |= {s for s in rules.include if s in self._markets}

        selected -= set(rules.exclude)
        return selected

    def _is_tradable_future(self, market: dict[str, Any], rules: SymbolsConfig) -> bool:
        """Проверить, что рынок — это подходящий фьючерс/бессрочный контракт."""
        if not market.get("contract", False):
            return False
        if market.get("option", False):
            return False
        expected_type = self.config.market_type
        if expected_type and market.get("type") != expected_type:
            return False
        if rules.require_active and market.get("active") is False:
            return False
        if rules.linear_only and not market.get("linear", False):
            return False
        if market.get("quote") not in rules.quote_currencies:
            return False
        if market.get("base") in rules.exclude_bases:
            return False
        return True

    def market(self, symbol: str) -> dict[str, Any] | None:
        """Данные рынка по символу."""
        return self._markets.get(symbol)

    def fees(self, symbol: str) -> FeeSchedule:
        """Комиссии для символа с учётом настроек и множителя.

        Приоритет: комиссии из ``config.yaml`` для биржи → данные ccxt → значения по умолчанию.
        """
        cfg = self.config
        fees_cfg = self.fees_config
        maker: float | None = cfg.maker_fee
        taker: float | None = cfg.taker_fee

        if (maker is None or taker is None) and fees_cfg.use_exchange_fees:
            market = self._markets.get(symbol) or {}
            market_maker = market.get("maker")
            market_taker = market.get("taker")
            if maker is None and isinstance(market_maker, (int, float)):
                maker = float(market_maker)
            if taker is None and isinstance(market_taker, (int, float)):
                taker = float(market_taker)

        maker = fees_cfg.default_maker_fee if maker is None else maker
        taker = fees_cfg.default_taker_fee if taker is None else taker
        multiplier = fees_cfg.fee_multiplier
        return FeeSchedule(maker=maker * multiplier, taker=taker * multiplier)

    def amount_to_precision(self, symbol: str, amount: float) -> float:
        """Округлить объём под требования биржи."""
        try:
            return float(self._exchange.amount_to_precision(symbol, amount))
        except Exception:  # noqa: BLE001 - точность не критична, откатываемся к исходному
            return amount

    def price_to_precision(self, symbol: str, price: float) -> float:
        """Округлить цену под требования биржи."""
        try:
            return float(self._exchange.price_to_precision(symbol, price))
        except Exception:  # noqa: BLE001
            return price

    def check_limits(self, symbol: str, amount: float, price: float) -> str | None:
        """Проверить минимальные лимиты рынка. Возвращает текст ошибки или ``None``."""
        market = self._markets.get(symbol)
        if market is None:
            return f"{self.id}: рынок {symbol} не найден"
        limits: dict[str, Any] = market.get("limits") or {}
        amount_limits: dict[str, Any] = limits.get("amount") or {}
        cost_limits: dict[str, Any] = limits.get("cost") or {}

        min_amount = amount_limits.get("min")
        if isinstance(min_amount, (int, float)) and amount < float(min_amount):
            return f"{self.id}: объём {amount} меньше минимального {min_amount} для {symbol}"

        min_cost = cost_limits.get("min")
        cost = amount * price
        if isinstance(min_cost, (int, float)) and cost < float(min_cost):
            return f"{self.id}: стоимость {cost:.2f} меньше минимальной {min_cost}"

        # Отладочный вывод лимитов
        self.log.debug(
            "%s: amount=%.8f, price=%.8f, cost=%.2f, minAmount=%s, minCost=%s",
            self.id,
            amount,
            price,
            cost,
            min_amount,
            min_cost,
        )
        return None

    # ------------------------------------------------------------------ #
    # Real-time котировки (websocket)
    # ------------------------------------------------------------------ #

    def supports_watch_tickers(self) -> bool:
        """Поддерживает ли биржа пакетную подписку ``watchTickers``."""
        return bool(self._exchange.has.get("watchTickers"))

    def supports_watch_order_book(self) -> bool:
        """Поддерживает ли биржа ``watchOrderBook``."""
        return bool(self._exchange.has.get("watchOrderBook"))

    async def stream_quotes(self, symbols: Sequence[str], on_quote: QuoteCallback) -> None:
        """Бесконечно транслировать котировки по websocket.

        Выбирается наиболее эффективный способ: ``watchTickers`` одним
        соединением, иначе — отдельный стакан на каждый символ.
        Соединение автоматически восстанавливается при обрывах.
        """
        if not symbols:
            self.log.warning("Нет символов для подписки")
            return

        if self.supports_watch_tickers():
            await self._stream_via_tickers(list(symbols), on_quote)
        elif self.supports_watch_order_book():
            await asyncio.gather(
                *(self._stream_order_book(symbol, on_quote) for symbol in symbols)
            )
        else:
            raise NotSupported(
                f"Биржа {self.id} не поддерживает websocket-подписку на котировки в ccxt.pro"
            )

    async def _stream_via_tickers(self, symbols: list[str], on_quote: QuoteCallback) -> None:
        """Подписка на пакет тикеров одним websocket-соединением."""
        delay = _RECONNECT_DELAY_SEC
        while not self._closed:
            try:
                tickers: dict[str, Any] = await self._exchange.watch_tickers(symbols)
                delay = _RECONNECT_DELAY_SEC
                for symbol, ticker in tickers.items():
                    quote = self._ticker_to_quote(symbol, ticker)
                    if quote is not None:
                        await _invoke(on_quote, quote)
            except asyncio.CancelledError:
                raise
            except NotSupported:
                self.log.warning("watchTickers недоступен, переключаюсь на стаканы")
                await asyncio.gather(
                    *(self._stream_order_book(symbol, on_quote) for symbol in symbols)
                )
                return
            except Exception as exc:  # noqa: BLE001 - сеть/биржа: логируем и переподключаемся
                self.log.warning("Ошибка потока тикеров (%s), переподключение через %.0f с", exc, delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, _MAX_RECONNECT_DELAY_SEC)

    async def _stream_order_book(self, symbol: str, on_quote: QuoteCallback) -> None:
        """Подписка на стакан одного символа."""
        delay = _RECONNECT_DELAY_SEC
        while not self._closed:
            try:
                order_book: dict[str, Any] = await self._exchange.watch_order_book(symbol, limit=5)
                delay = _RECONNECT_DELAY_SEC
                quote = self._order_book_to_quote(symbol, order_book)
                if quote is not None:
                    await _invoke(on_quote, quote)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self.log.warning(
                    "Ошибка стакана %s (%s), переподключение через %.0f с", symbol, exc, delay
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, _MAX_RECONNECT_DELAY_SEC)

    def _ticker_to_quote(self, symbol: str, ticker: dict[str, Any]) -> Quote | None:
        """Преобразовать тикер ccxt в :class:`Quote`."""
        bid = ticker.get("bid")
        ask = ticker.get("ask")
        if not isinstance(bid, (int, float)) or not isinstance(ask, (int, float)):
            return None
        timestamp = ticker.get("timestamp")
        quote = Quote(
            exchange=self.id,
            symbol=symbol,
            bid=float(bid),
            ask=float(ask),
            timestamp_ms=int(timestamp) if isinstance(timestamp, (int, float)) else now_ms(),
            bid_volume=_opt_float(ticker.get("bidVolume")),
            ask_volume=_opt_float(ticker.get("askVolume")),
        )
        return quote if quote.is_valid() else None

    def _order_book_to_quote(self, symbol: str, order_book: dict[str, Any]) -> Quote | None:
        """Преобразовать стакан ccxt в :class:`Quote` (top of book)."""
        bids: list[list[float]] = order_book.get("bids") or []
        asks: list[list[float]] = order_book.get("asks") or []
        if not bids or not asks:
            return None
        timestamp = order_book.get("timestamp")
        quote = Quote(
            exchange=self.id,
            symbol=symbol,
            bid=float(bids[0][0]),
            ask=float(asks[0][0]),
            timestamp_ms=int(timestamp) if isinstance(timestamp, (int, float)) else now_ms(),
            bid_volume=_opt_float(bids[0][1] if len(bids[0]) > 1 else None),
            ask_volume=_opt_float(asks[0][1] if len(asks[0]) > 1 else None),
        )
        return quote if quote.is_valid() else None

    # ------------------------------------------------------------------ #
    # Торговля
    # ------------------------------------------------------------------ #

    async def prepare_symbol(self, symbol: str) -> None:
        """Настроить плечо и режим маржи перед торговлей символом (идемпотентно)."""
        if symbol in self._leverage_done:
            return
        self._leverage_done.add(symbol)

        if self.config.margin_mode:
            try:
                await self._exchange.set_margin_mode(self.config.margin_mode, symbol)
            except Exception as exc:  # noqa: BLE001 - многие биржи ругаются, если режим уже стоит
                self.log.debug("set_margin_mode(%s): %s", symbol, exc)
        if self.config.leverage > 1:
            try:
                await self._exchange.set_leverage(self.config.leverage, symbol)
            except Exception as exc:  # noqa: BLE001
                self.log.debug("set_leverage(%s): %s", symbol, exc)

    async def create_order(
        self,
        symbol: str,
        side: OrderSide,
        amount: float,
        *,
        order_type: str = "market",
        price: float | None = None,
        reduce_only: bool = False,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Разместить ордер на бирже (реальный вызов API)."""
        request_params: dict[str, Any] = dict(params or {})
        if reduce_only:
            request_params.setdefault("reduceOnly", True)

        order: dict[str, Any] = await self._exchange.create_order(
            symbol=symbol,
            type=order_type,
            side=side.value,
            amount=amount,
            price=price,
            params=request_params,
        )
        return order

    async def fetch_free_balance(self, currency: str) -> float:
        """Свободный баланс по валюте (0.0, если недоступно)."""
        try:
            balance: dict[str, Any] = await self._exchange.fetch_balance()
        except Exception as exc:  # noqa: BLE001
            self.log.warning("Не удалось получить баланс: %s", exc)
            return 0.0
        free: dict[str, Any] = balance.get("free") or {}
        value = free.get(currency)
        return float(value) if isinstance(value, (int, float)) else 0.0

    async def fetch_funding_rate(self, symbol: str) -> float | None:
        """Текущая ставка финансирования по символу (в долях) или ``None``."""
        if not self._exchange.has.get("fetchFundingRate"):
            return None
        try:
            data: dict[str, Any] = await self._exchange.fetch_funding_rate(symbol)
        except Exception as exc:  # noqa: BLE001
            self.log.debug("fetch_funding_rate(%s): %s", symbol, exc)
            return None
        rate = data.get("fundingRate")
        return float(rate) if isinstance(rate, (int, float)) else None

    # ------------------------------------------------------------------ #
    # Завершение работы
    # ------------------------------------------------------------------ #

    async def close(self) -> None:
        """Корректно закрыть websocket-соединения и http-сессию."""
        self._closed = True
        try:
            await self._exchange.close()
        except Exception as exc:  # noqa: BLE001
            self.log.debug("Ошибка при закрытии соединения: %s", exc)


async def _invoke(callback: QuoteCallback, quote: Quote) -> None:
    """Вызвать обработчик котировки, поддерживая sync и async варианты."""
    result = callback(quote)
    if isinstance(result, Awaitable):
        await result


def _opt_float(value: Any) -> float | None:
    """Аккуратно привести значение к float либо вернуть ``None``."""
    return float(value) if isinstance(value, (int, float)) else None


def unique(items: Iterable[str]) -> list[str]:
    """Уникальные элементы с сохранением порядка."""
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result
