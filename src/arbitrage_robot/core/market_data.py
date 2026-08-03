"""Хранилище real-time котировок со всех бирж."""

from __future__ import annotations

from collections.abc import Iterator

from ..models import Quote, now_ms

__all__ = ["QuoteBook"]


class QuoteBook:
    """Потокобезопасное (в рамках одного event loop) хранилище лучших цен.

    Структура: ``{символ: {биржа: котировка}}``. Обновляется коллбэками
    websocket-потоков, читается сканером спредов.
    """

    def __init__(self, max_age_ms: int = 3000) -> None:
        self._quotes: dict[str, dict[str, Quote]] = {}
        self._max_age_ms: int = max_age_ms
        self._updates: int = 0

    @property
    def updates(self) -> int:
        """Общее число принятых обновлений котировок."""
        return self._updates

    @property
    def symbols_count(self) -> int:
        """Количество символов, по которым есть хотя бы одна котировка."""
        return len(self._quotes)

    def update(self, quote: Quote) -> None:
        """Записать новую котировку."""
        self._quotes.setdefault(quote.symbol, {})[quote.exchange] = quote
        self._updates += 1

    def get(self, symbol: str, exchange: str) -> Quote | None:
        """Получить котировку по символу и бирже."""
        return self._quotes.get(symbol, {}).get(exchange)

    def fresh(self, symbol: str, exchange: str, reference_ms: int | None = None) -> Quote | None:
        """Получить котировку, если она не устарела."""
        quote = self.get(symbol, exchange)
        if quote is None:
            return None
        ref = reference_ms if reference_ms is not None else now_ms()
        return quote if quote.age_ms(ref) <= self._max_age_ms else None

    def by_symbol(self, symbol: str) -> dict[str, Quote]:
        """Все котировки по символу: ``{биржа: котировка}``."""
        return dict(self._quotes.get(symbol, {}))

    def fresh_by_symbol(self, symbol: str, reference_ms: int | None = None) -> dict[str, Quote]:
        """Только свежие котировки по символу."""
        ref = reference_ms if reference_ms is not None else now_ms()
        return {
            exchange: quote
            for exchange, quote in self._quotes.get(symbol, {}).items()
            if quote.age_ms(ref) <= self._max_age_ms and quote.is_valid()
        }

    def multi_exchange_symbols(self, reference_ms: int | None = None) -> Iterator[str]:
        """Символы, по которым есть свежие котировки минимум с двух бирж."""
        ref = reference_ms if reference_ms is not None else now_ms()
        for symbol in list(self._quotes):
            if len(self.fresh_by_symbol(symbol, ref)) >= 2:
                yield symbol

    def clear(self) -> None:
        """Очистить хранилище (например, при переподборе символов)."""
        self._quotes.clear()
