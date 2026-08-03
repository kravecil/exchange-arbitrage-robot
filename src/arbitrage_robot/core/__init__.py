"""Ядро робота: данные, поиск спредов, риски, исполнение, движок."""

from __future__ import annotations

from .engine import ArbitrageEngine
from .executor import TradeExecutor
from .market_data import QuoteBook
from .risk import PortfolioStats, RiskManager
from .spread import SpreadFinder, SpreadSnapshot

__all__ = [
    "ArbitrageEngine",
    "PortfolioStats",
    "QuoteBook",
    "RiskManager",
    "SpreadFinder",
    "SpreadSnapshot",
    "TradeExecutor",
]
