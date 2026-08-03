"""Робот межбиржевого арбитража фьючерсов.

Пакет содержит:
* :mod:`arbitrage_robot.config` — конфигурация (YAML + переменные окружения);
* :mod:`arbitrage_robot.exchanges` — подключение бирж через ccxt / ccxt.pro;
* :mod:`arbitrage_robot.core` — поток котировок, поиск спредов, исполнение сделок;
* :mod:`arbitrage_robot.cli` — интерфейс командной строки.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__: str = "0.1.0"
