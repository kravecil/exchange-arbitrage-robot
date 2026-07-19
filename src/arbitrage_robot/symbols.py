from __future__ import annotations

import logging

from arbitrage_robot.config import AppConfig
from arbitrage_robot.exchanges import ExchangeManager

logger = logging.getLogger(__name__)


def discover_common_symbols(
    manager: ExchangeManager,
    config: AppConfig,
) -> list[str]:
    """Находит торговые пары, доступные на всех включённых биржах."""
    exchange_ids = manager.exchange_ids
    if len(exchange_ids) < 2:
        return []

    symbol_sets: list[set[str]] = []
    for exchange_id in exchange_ids:
        exchange = manager.exchanges[exchange_id]
        active_symbols = {
            symbol
            for symbol, market in exchange.markets.items()
            if market.get("active", True) and market.get("spot", True)
        }
        symbol_sets.append(active_symbols)

    common = set.intersection(*symbol_sets) if symbol_sets else set()
    filtered = _apply_symbol_filters(sorted(common), config)
    logger.info("Найдено общих торговых пар: %d", len(filtered))
    return filtered


def _apply_symbol_filters(symbols: list[str], config: AppConfig) -> list[str]:
    result = symbols

    if config.symbols.include:
        include_set = set(config.symbols.include)
        result = [symbol for symbol in result if symbol in include_set]

    if config.symbols.exclude:
        exclude_set = set(config.symbols.exclude)
        result = [symbol for symbol in result if symbol not in exclude_set]

    if config.scanner.quote_currencies:
        quotes = set(config.scanner.quote_currencies)
        result = [
            symbol
            for symbol in result
            if symbol.split("/")[-1].split(":")[0] in quotes
        ]

    return result
