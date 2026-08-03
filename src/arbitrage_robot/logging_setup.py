"""Настройка логирования (консоль через rich + опциональный файл)."""

from __future__ import annotations

import logging
from pathlib import Path

from rich.console import Console
from rich.logging import RichHandler

__all__ = ["console", "get_logger", "setup_logging"]

console: Console = Console(stderr=False)


def setup_logging(level: str = "INFO", log_file: str | None = None) -> None:
    """Инициализировать корневой логгер.

    :param level: уровень логирования (``DEBUG``/``INFO``/``WARNING``/``ERROR``).
    :param log_file: путь к файлу логов; ``None`` — писать только в консоль.
    """
    handlers: list[logging.Handler] = [
        RichHandler(
            console=console,
            rich_tracebacks=True,
            show_path=False,
            omit_repeated_times=False,
            markup=True,
        )
    ]

    if log_file:
        path = Path(log_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(path, encoding="utf-8")
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s")
        )
        handlers.append(file_handler)

    logging.basicConfig(
        level=level.upper(),
        format="%(message)s",
        datefmt="%H:%M:%S",
        handlers=handlers,
        force=True,
    )

    # ccxt очень многословен на уровне DEBUG.
    logging.getLogger("ccxt").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Получить именованный логгер робота."""
    return logging.getLogger(name)
