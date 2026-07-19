from __future__ import annotations

import argparse
import asyncio
import logging
import signal
from pathlib import Path

from arbitrage_robot.config import load_config
from arbitrage_robot.executor import create_executor
from arbitrage_robot.exchanges import ExchangeManager
from arbitrage_robot.logging_setup import setup_logging
from arbitrage_robot.scanner import ArbitrageScanner
from arbitrage_robot.symbols import discover_common_symbols

logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Межбиржевой арбитражный робот на базе ccxt",
    )
    parser.add_argument(
        "-c",
        "--config",
        type=Path,
        default=None,
        help="Путь к config.yaml (по умолчанию: ./config.yaml)",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Выполнить один цикл сканирования и завершить работу",
    )
    return parser.parse_args()


async def run_async(config_path: Path | None = None, once: bool = False) -> None:
    config = load_config(config_path)
    manager = ExchangeManager(config)

    if len(config.enabled_exchanges) < 2:
        logger.error(
            "Для арбитража нужно минимум 2 включённые биржи. "
            "Сейчас включено: %d. Добавьте биржи в config.yaml.",
            len(config.enabled_exchanges),
        )
        return

    await manager.connect()
    symbols = discover_common_symbols(manager, config)

    if not symbols:
        logger.error(
            "Не найдено общих торговых пар между включёнными биржами. "
            "Проверьте filters в config.yaml (symbols, quote_currencies)."
        )
        await manager.close()
        return

    scanner = ArbitrageScanner(manager, config, symbols)
    executor = create_executor(config.mode.value, manager)
    stop_event = asyncio.Event()

    def request_stop(*_: object) -> None:
        logger.info("Получен сигнал остановки...")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, request_stop)

    logger.info(
        "Робот запущен: mode=%s, пар=%d, интервал=%ss",
        config.mode.value,
        len(symbols),
        config.scanner.poll_interval_seconds,
    )

    try:
        while not stop_event.is_set():
            opportunities = await scanner.scan()
            if opportunities:
                logger.info("Найдено возможностей: %d", len(opportunities))
                for opportunity in opportunities:
                    await executor.execute(opportunity)
            else:
                logger.debug("Арбитражных возможностей не найдено")

            if once:
                break

            try:
                await asyncio.wait_for(
                    stop_event.wait(),
                    timeout=config.scanner.poll_interval_seconds,
                )
            except TimeoutError:
                continue
    finally:
        await manager.close()
        logger.info("Робот остановлен")


def run() -> None:
    setup_logging()
    args = parse_args()
    asyncio.run(run_async(config_path=args.config, once=args.once))


if __name__ == "__main__":
    run()
