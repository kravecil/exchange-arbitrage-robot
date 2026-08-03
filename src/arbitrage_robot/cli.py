"""Командный интерфейс робота (Typer)."""

from __future__ import annotations

import asyncio
import signal
from pathlib import Path
from typing import Annotated

import typer
from rich.table import Table

from .config import AppConfig, load_config
from .core import ArbitrageEngine
from .logging_setup import console, setup_logging
from .models import TradeMode

__all__ = ["app", "main"]

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Робот межбиржевого арбитража фьючерсов (real-time, ccxt.pro).",
)

ConfigOption = Annotated[
    Path | None,
    typer.Option("--config", "-c", help="Путь к config.yaml (по умолчанию ./config.yaml)"),
]
ModeOption = Annotated[
    TradeMode | None,
    typer.Option(
        "--mode",
        "-m",
        help="Режим торговли: paper (тест, без затрат) или live (реальные ордера)",
        case_sensitive=False,
    ),
]


@app.command("run")
def run(
    config_path: ConfigOption = None,
    mode: ModeOption = None,
    min_spread: Annotated[
        float | None,
        typer.Option("--min-spread", help="Переопределить порог входа по чистому спреду, %"),
    ] = None,
    amount: Annotated[
        float | None,
        typer.Option("--amount", help="Переопределить объём одной ноги в USDT"),
    ] = None,
    yes: Annotated[
        bool,
        typer.Option("--yes", "-y", help="Не спрашивать подтверждение для режима live"),
    ] = False,
) -> None:
    """Запустить робота в режиме реального времени."""
    config = _load(config_path, mode, min_spread=min_spread, amount=amount)
    setup_logging(config.log_level, config.log_file)
    _print_config(config)

    if config.mode is TradeMode.LIVE and not yes:
        confirmed = typer.confirm(
            "Выбран РЕАЛЬНЫЙ режим (live): будут отправляться настоящие ордера. Продолжить?"
        )
        if not confirmed:
            console.print("[yellow]Отменено пользователем.[/yellow]")
            raise typer.Exit(code=1)

    asyncio.run(_run_engine(config))


@app.command("scan")
def scan(
    config_path: ConfigOption = None,
    duration: Annotated[
        float, typer.Option("--duration", "-d", help="Сколько секунд наблюдать за рынком")
    ] = 60.0,
) -> None:
    """Только мониторинг спредов, без сделок (всегда тестовый режим)."""
    config = _load(config_path, TradeMode.PAPER)
    setup_logging(config.log_level, config.log_file)
    _print_config(config)
    console.print(f"[cyan]Мониторинг рынка {duration:.0f} секунд...[/cyan]")
    asyncio.run(_run_engine(config, duration=duration, trading_enabled=False))


@app.command("symbols")
def symbols(config_path: ConfigOption = None) -> None:
    """Показать список общих торговых пар, которые будет отслеживать робот."""
    config = _load(config_path, TradeMode.PAPER)
    setup_logging(config.log_level, None)
    asyncio.run(_show_symbols(config))


@app.command("fees")
def fees(
    config_path: ConfigOption = None,
    symbol: Annotated[
        str, typer.Option("--symbol", "-s", help="Пара для расчёта комиссий")
    ] = "BTC/USDT:USDT",
) -> None:
    """Показать комиссии бирж и минимальный безубыточный спред по паре."""
    config = _load(config_path, TradeMode.PAPER)
    setup_logging(config.log_level, None)
    asyncio.run(_show_fees(config, symbol))


# ---------------------------------------------------------------------- #
# Внутренние помощники
# ---------------------------------------------------------------------- #


def _load(
    config_path: Path | None,
    mode: TradeMode | None,
    *,
    min_spread: float | None = None,
    amount: float | None = None,
) -> AppConfig:
    """Загрузить конфигурацию с учётом опций CLI."""
    config = load_config(config_path, mode=mode)
    if min_spread is None and amount is None:
        return config

    strategy = config.strategy.model_copy(
        update={
            key: value
            for key, value in (
                ("min_spread_pct", min_spread),
                ("order_amount_quote", amount),
            )
            if value is not None
        }
    )
    return config.model_copy(update={"strategy": strategy})


def _print_config(config: AppConfig) -> None:
    """Показать ключевые параметры запуска."""
    table = Table(title="Параметры робота", show_header=False, title_justify="left")
    table.add_column("Параметр", style="cyan")
    table.add_column("Значение", style="bold")
    mode_text = (
        "[red]LIVE — реальные ордера[/red]"
        if config.mode is TradeMode.LIVE
        else "[green]PAPER — тестовые сделки без затрат[/green]"
    )
    table.add_row("Режим", mode_text)
    table.add_row("Биржи", ", ".join(ex.id for ex in config.enabled_exchanges))
    table.add_row("Тип рынка", "фьючерсы (" + config.enabled_exchanges[0].market_type + ")")
    table.add_row("Порог входа (чистый спред)", f"{config.strategy.min_spread_pct} %")
    table.add_row("Порог выхода", f"{config.strategy.exit_spread_pct} %")
    table.add_row("Аварийный выход", f"{config.strategy.exit_on_reverse_pct} %")
    table.add_row("Объём ноги", f"{config.strategy.order_amount_quote} {config.quote_currency}")
    table.add_row("Множитель комиссий", str(config.fees.fee_multiplier))
    table.add_row("Максимум пар", str(config.symbols.max_symbols))
    console.print(table)


async def _run_engine(
    config: AppConfig, duration: float | None = None, trading_enabled: bool = True
) -> None:
    """Запустить движок с обработкой Ctrl+C и опциональным таймером."""
    engine = ArbitrageEngine(config)
    if not trading_enabled:
        engine.trading_enabled = False

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, engine.stop)
        except NotImplementedError:  # pragma: no cover - Windows
            pass

    if duration is not None:
        loop.call_later(duration, engine.stop)

    try:
        await engine.run()
    except KeyboardInterrupt:  # pragma: no cover
        engine.stop()
    except RuntimeError as exc:
        console.print(f"[red]Ошибка запуска: {exc}[/red]")
        await engine.shutdown()
        raise typer.Exit(code=1) from exc


async def _show_symbols(config: AppConfig) -> None:
    """Вывести таблицу общих пар."""
    engine = ArbitrageEngine(config)
    try:
        await engine.prepare()
        table = Table(title=f"Общие пары ({len(engine.common_symbols)})")
        table.add_column("#", justify="right")
        table.add_column("Пара")
        table.add_column("Биржи")
        for index, symbol in enumerate(engine.common_symbols, start=1):
            exchanges = [
                exchange_id
                for exchange_id, symbols in engine.symbols_by_exchange.items()
                if symbol in symbols
            ]
            table.add_row(str(index), symbol, ", ".join(exchanges))
        console.print(table)
    finally:
        await engine.shutdown()


async def _show_fees(config: AppConfig, symbol: str) -> None:
    """Вывести комиссии бирж и минимальный безубыточный спред."""
    engine = ArbitrageEngine(config)
    try:
        await engine.prepare()
        table = Table(title=f"Комиссии по {symbol}")
        table.add_column("Биржа")
        table.add_column("Maker, %", justify="right")
        table.add_column("Taker, %", justify="right")
        for client in engine.clients.values():
            schedule = client.fees(symbol)
            table.add_row(
                client.id, f"{schedule.maker * 100:.4f}", f"{schedule.taker * 100:.4f}"
            )
        console.print(table)

        ids = list(engine.clients)
        for i, first in enumerate(ids):
            for second in ids[i + 1 :]:
                total = engine.finder.round_trip_fees_pct(symbol, first, second)
                console.print(
                    f"  {first} ↔ {second}: безубыточный спред "
                    f"[bold]{total + config.strategy.slippage_pct:.3f} %[/bold] "
                    f"(комиссии {total:.3f} % + проскальзывание "
                    f"{config.strategy.slippage_pct:.3f} %)"
                )
    finally:
        await engine.shutdown()


def main() -> None:
    """Точка входа консольной команды ``arb-robot``."""
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
