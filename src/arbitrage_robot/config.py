"""Конфигурация робота: YAML-файл + переменные окружения (.env).

Приоритет источников (по возрастанию):

1. значения по умолчанию в моделях;
2. ``config.yaml`` (путь задаётся аргументом или ``ARB_CONFIG``);
3. переменные окружения ``ARB_*``;
4. явные аргументы командной строки.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Final, Self, cast

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .models import TradeMode

__all__ = [
    "AppConfig",
    "ExchangeConfig",
    "FeesConfig",
    "RiskConfig",
    "StrategyConfig",
    "SymbolsConfig",
    "TelegramConfig",
    "load_config",
]

DEFAULT_CONFIG_PATH: Final[str] = "config.yaml"


class _Base(BaseModel):
    """Базовая модель: запрещает опечатки в ключах конфигурации."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class ExchangeConfig(_Base):
    """Описание одной биржи.

    Чтобы добавить новую биржу, достаточно дописать секцию в ``config.yaml``:

    .. code-block:: yaml

        exchanges:
          - id: bybit
            enabled: true
            market_type: swap
    """

    id: str = Field(description="Идентификатор биржи в ccxt, например binance / bybit / okx")
    enabled: bool = Field(default=True, description="Участвует ли биржа в арбитраже")
    market_type: str = Field(
        default="swap",
        description="Тип рынка ccxt: swap (бессрочные фьючерсы) или future (срочные)",
    )
    sandbox: bool = Field(default=False, description="Использовать testnet/sandbox биржи")
    api_key_env: str | None = Field(default=None, description="Имя переменной окружения с API key")
    secret_env: str | None = Field(default=None, description="Имя переменной окружения с secret")
    password_env: str | None = Field(
        default=None, description="Имя переменной окружения с passphrase (OKX и др.)"
    )
    maker_fee: float | None = Field(
        default=None,
        ge=0.0,
        description="Комиссия мейкера в долях (0.0002 = 0.02 %); None — брать из ccxt",
    )
    taker_fee: float | None = Field(
        default=None,
        ge=0.0,
        description="Комиссия тейкера в долях (0.0005 = 0.05 %); None — брать из ccxt",
    )
    leverage: int = Field(default=1, ge=1, le=125, description="Плечо, выставляемое перед сделкой")
    margin_mode: str | None = Field(
        default=None, description="Режим маржи: cross / isolated; None — не менять"
    )
    default_type_override: str | None = Field(
        default=None, description="Переопределение options.defaultType в ccxt"
    )
    options: dict[str, Any] = Field(
        default_factory=dict, description="Произвольные options, передаваемые в конструктор ccxt"
    )
    ws_symbol_limit: int = Field(
        default=120,
        ge=1,
        description="Максимум символов, на которые подписываемся по websocket на этой бирже",
    )

    @property
    def key_env(self) -> str:
        """Имя переменной окружения с API-ключом."""
        return self.api_key_env or f"{self.id.upper()}_API_KEY"

    @property
    def sec_env(self) -> str:
        """Имя переменной окружения с секретом."""
        return self.secret_env or f"{self.id.upper()}_SECRET"

    @property
    def pass_env(self) -> str:
        """Имя переменной окружения с паролем/passphrase."""
        return self.password_env or f"{self.id.upper()}_PASSWORD"

    def credentials(self) -> dict[str, str]:
        """Прочитать ключи из окружения; отсутствующие поля пропускаются."""
        creds: dict[str, str] = {}
        for field_name, env_name in (
            ("apiKey", self.key_env),
            ("secret", self.sec_env),
            ("password", self.pass_env),
        ):
            value = os.getenv(env_name, "").strip()
            if value:
                creds[field_name] = value
        return creds

    def has_credentials(self) -> bool:
        """Есть ли пара ключ+секрет для торговли в боевом режиме."""
        creds = self.credentials()
        return "apiKey" in creds and "secret" in creds


class SymbolsConfig(_Base):
    """Правила отбора торговых пар.

    По умолчанию берутся все бессрочные фьючерсы с котировкой USDT,
    доступные одновременно минимум на двух включённых биржах.
    """

    mode: str = Field(
        default="auto",
        description="auto — автоподбор общих пар; manual — только список include",
    )
    quote_currencies: tuple[str, ...] = Field(
        default=("USDT",), description="Допустимые котируемые валюты"
    )
    include: tuple[str, ...] = Field(
        default=(),
        description="Явный белый список пар (BTC/USDT:USDT). В режиме manual — единственный источник",
    )
    exclude: tuple[str, ...] = Field(
        default=(), description="Чёрный список пар, исключается всегда"
    )
    exclude_bases: tuple[str, ...] = Field(
        default=("USDC", "BUSD", "TUSD", "FDUSD", "DAI"),
        description="Базовые валюты, которые не торгуем (стейблы и т.п.)",
    )
    max_symbols: int = Field(
        default=100, ge=1, description="Ограничение на количество отслеживаемых пар"
    )
    require_active: bool = Field(
        default=True, description="Брать только активные (торгуемые) рынки"
    )
    linear_only: bool = Field(default=True, description="Только linear-контракты (USDT-margined)")

    @model_validator(mode="after")
    def _check_mode(self) -> Self:
        if self.mode not in {"auto", "manual"}:
            raise ValueError("symbols.mode должен быть 'auto' или 'manual'")
        if self.mode == "manual" and not self.include:
            raise ValueError("symbols.mode = manual требует непустой список symbols.include")
        return self


class FeesConfig(_Base):
    """Настройки учёта комиссий."""

    use_exchange_fees: bool = Field(
        default=True,
        description="Брать комиссии из данных рынка ccxt (если доступны)",
    )
    default_taker_fee: float = Field(
        default=0.0005, ge=0.0, description="Комиссия тейкера по умолчанию (0.0005 = 0.05 %)"
    )
    default_maker_fee: float = Field(
        default=0.0002, ge=0.0, description="Комиссия мейкера по умолчанию"
    )
    fee_multiplier: float = Field(
        default=1.0,
        ge=0.0,
        description="Множитель комиссий: >1 — консервативная оценка, <1 — скидки VIP/BNB",
    )
    include_funding: bool = Field(
        default=False,
        description="Учитывать ставку финансирования при оценке удержания позиции",
    )
    funding_pct_per_period: float = Field(
        default=0.01,
        ge=0.0,
        description="Оценка ставки финансирования за период, % (если include_funding = true)",
    )


class StrategyConfig(_Base):
    """Пороги входа/выхода и параметры расчёта спреда."""

    min_spread_pct: float = Field(
        default=0.35,
        description="Порог входа: минимальный ЧИСТЫЙ спред (после комиссий), % — «превышение вверх»",
    )
    max_spread_pct: float = Field(
        default=5.0,
        gt=0.0,
        description="Верхняя отсечка: спред больше — считаем аномалией/ошибкой данных и пропускаем",
    )
    exit_spread_pct: float = Field(
        default=0.05,
        description="Порог выхода: закрываем позицию, когда спред сузился до этого значения, %",
    )
    exit_on_reverse_pct: float = Field(
        default=-0.5,
        description="Аварийный выход при развороте спреда ниже этого значения, % («вниз»)",
    )
    slippage_pct: float = Field(
        default=0.02, ge=0.0, description="Заложенное проскальзывание на обе ноги, %"
    )
    order_amount_quote: float = Field(
        default=100.0, gt=0.0, description="Объём одной ноги в котируемой валюте (USDT)"
    )
    max_quote_age_ms: int = Field(
        default=3000, gt=0, description="Максимальный возраст котировки для расчётов, мс"
    )
    min_top_volume_quote: float = Field(
        default=0.0,
        ge=0.0,
        description="Минимальный объём на лучшем уровне стакана в USDT (0 — не проверять)",
    )
    cooldown_sec: float = Field(
        default=30.0, ge=0.0, description="Пауза по паре после сделки, сек"
    )
    max_position_hold_sec: float = Field(
        default=3600.0,
        gt=0.0,
        description="Максимальное время удержания позиции, после чего закрываем принудительно",
    )
    order_type: str = Field(default="market", description="Тип ордера: market или limit")
    limit_price_offset_pct: float = Field(
        default=0.02,
        ge=0.0,
        description="Смещение лимитной цены от текущей для повышения шанса исполнения, %",
    )

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.order_type not in {"market", "limit"}:
            raise ValueError("strategy.order_type должен быть 'market' или 'limit'")
        if self.max_spread_pct <= self.min_spread_pct:
            raise ValueError("strategy.max_spread_pct должен быть больше min_spread_pct")
        if self.exit_spread_pct >= self.min_spread_pct:
            raise ValueError("strategy.exit_spread_pct должен быть меньше min_spread_pct")
        return self


class RiskConfig(_Base):
    """Ограничения риска."""

    max_open_positions: int = Field(
        default=3, ge=1, description="Максимум одновременно открытых арбитражных позиций"
    )
    max_positions_per_symbol: int = Field(
        default=1, ge=1, description="Максимум позиций по одной паре"
    )
    max_notional_quote: float = Field(
        default=1000.0, gt=0.0, description="Максимальный суммарный объём открытых позиций, USDT"
    )
    max_daily_loss_quote: float = Field(
        default=100.0, gt=0.0, description="Дневной стоп-лосс в USDT: при достижении робот встаёт"
    )
    min_free_balance_quote: float = Field(
        default=0.0,
        ge=0.0,
        description="Минимальный свободный баланс на каждой бирже для входа (live-режим)",
    )
    require_balance_check: bool = Field(
        default=True, description="Проверять баланс перед входом в live-режиме"
    )


class TelegramConfig(_Base):
    """Настройки уведомлений через Telegram."""

    enabled: bool = Field(
        default=False,
        description="Включить отправку уведомлений в Telegram",
    )
    api_token_env: str = Field(
        default="TELEGRAM_API_TOKEN",
        description="Имя переменной окружения с API токеном бота",
    )
    chat_id_env: str = Field(
        default="TELEGRAM_CHAT_ID",
        description="Имя переменной окружения с ID получателя сообщений",
    )
    min_spread_pct: float = Field(
        default=0.35,
        description="Порог входа: минимальный ЧИСТЫЙ спред (после комиссий), % — уведомление при превышении",
    )
    max_spread_pct: float = Field(
        default=5.0,
        gt=0.0,
        description="Верхняя отсечка: спред больше — не отправляем уведомление (аномалия)",
    )
    cooldown_sec: float = Field(
        default=60.0,
        gt=0.0,
        description="Минимальный интервал между уведомлениями по одной паре, сек",
    )


class AppConfig(_Base):
    """Корневая конфигурация приложения."""

    mode: TradeMode = Field(
        default=TradeMode.PAPER,
        description="paper — тестовые сделки (только лог), live — реальные ордера",
    )
    log_level: str = Field(default="INFO", description="Уровень логирования")
    log_file: str | None = Field(default="logs/robot.log", description="Файл логов (или null)")
    quote_currency: str = Field(default="USDT", description="Основная котируемая валюта")
    enable_status_logging: bool = Field(
        default=True,
        description="Включить вывод периодических статусных сообщений",
    )
    refresh_markets_sec: float = Field(
        default=3600.0, gt=0.0, description="Период переоткрытия списка рынков, сек"
    )
    status_interval_sec: float = Field(
        default=15.0, gt=0.0, description="Период вывода статуса в консоль, сек"
    )
    scan_interval_sec: float = Field(
        default=0.2,
        gt=0.0,
        description="Период пересчёта спредов по накопленным котировкам, сек",
    )
    exchanges: tuple[ExchangeConfig, ...] = Field(
        default=(ExchangeConfig(id="binance"),), description="Список бирж"
    )
    symbols: SymbolsConfig = Field(default_factory=SymbolsConfig)
    fees: FeesConfig = Field(default_factory=FeesConfig)
    strategy: StrategyConfig = Field(default_factory=StrategyConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    telegram: TelegramConfig = Field(default_factory=TelegramConfig, description="Настройки Telegram уведомлений")

    @model_validator(mode="after")
    def _check_exchanges(self) -> Self:
        ids = [ex.id for ex in self.exchanges if ex.enabled]
        if len(ids) != len(set(ids)):
            raise ValueError("Идентификаторы бирж в config.exchanges не должны повторяться")
        return self

    @property
    def enabled_exchanges(self) -> tuple[ExchangeConfig, ...]:
        """Только включённые биржи."""
        return tuple(ex for ex in self.exchanges if ex.enabled)

    @property
    def is_live(self) -> bool:
        """Работает ли робот в боевом режиме."""
        return self.mode is TradeMode.LIVE


def _read_yaml(path: Path) -> dict[str, Any]:
    """Прочитать YAML-конфиг; пустой/отсутствующий файл — пустой словарь."""
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        data: Any = yaml.safe_load(fh)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"Некорректный формат {path}: ожидался словарь на верхнем уровне")
    typed_data: dict[str, Any] = cast("dict[str, Any]", data)
    return typed_data


def _apply_env_overrides(data: dict[str, Any]) -> dict[str, Any]:
    """Наложить переменные окружения ``ARB_*`` поверх данных из YAML."""
    result = dict(data)
    strategy: dict[str, Any] = dict(result.get("strategy") or {})
    risk: dict[str, Any] = dict(result.get("risk") or {})

    def env_float(name: str) -> float | None:
        raw = os.getenv(name)
        return float(raw) if raw not in (None, "") else None

    def env_int(name: str) -> int | None:
        raw = os.getenv(name)
        return int(raw) if raw not in (None, "") else None

    if mode := os.getenv("ARB_MODE"):
        result["mode"] = mode.strip().lower()
    if level := os.getenv("ARB_LOG_LEVEL"):
        result["log_level"] = level.strip().upper()
    if (value := env_float("ARB_MIN_SPREAD")) is not None:
        strategy["min_spread_pct"] = value
    if (value := env_float("ARB_MAX_SPREAD")) is not None:
        strategy["max_spread_pct"] = value
    if (value := env_float("ARB_EXIT_SPREAD")) is not None:
        strategy["exit_spread_pct"] = value
    if (value := env_float("ARB_ORDER_AMOUNT")) is not None:
        strategy["order_amount_quote"] = value
    if (value := env_int("ARB_MAX_POSITIONS")) is not None:
        risk["max_open_positions"] = value
    if enable_status := os.getenv("ARB_ENABLE_STATUS_LOGGING"):
        result["enable_status_logging"] = enable_status.strip().lower() in ("1", "true", "yes")

    if strategy:
        result["strategy"] = strategy
    if risk:
        result["risk"] = risk
    return result


def load_config(
    path: str | Path | None = None,
    *,
    mode: TradeMode | None = None,
    overrides: dict[str, Any] | None = None,
) -> AppConfig:
    """Загрузить конфигурацию робота.

    :param path: путь к YAML-файлу; по умолчанию ``ARB_CONFIG`` либо ``config.yaml``.
    :param mode: принудительный режим торговли (аргумент CLI имеет высший приоритет).
    :param overrides: дополнительные переопределения верхнего уровня.
    :raises ValueError: если конфигурация некорректна.
    """
    load_dotenv(override=False)

    config_path = Path(path or os.getenv("ARB_CONFIG") or DEFAULT_CONFIG_PATH)
    data = _read_yaml(config_path)
    data = _apply_env_overrides(data)

    if overrides:
        data.update({k: v for k, v in overrides.items() if v is not None})
    if mode is not None:
        data["mode"] = mode

    return AppConfig.model_validate(data)
