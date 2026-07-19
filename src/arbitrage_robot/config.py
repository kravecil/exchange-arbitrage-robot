from __future__ import annotations

import logging
import os
from pathlib import Path
import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator

from arbitrage_robot.models import TradeMode

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = Path("config.yaml")
EXAMPLE_CONFIG_PATH = Path("config.example.yaml")


class ScannerConfig(BaseModel):
    poll_interval_seconds: float = Field(default=10, gt=0)
    min_spread_percent_up: float = Field(default=0.5, ge=0)
    min_spread_percent_down: float = Field(default=0.5, ge=0)
    include_fees: bool = True
    quote_currencies: list[str] = Field(default_factory=lambda: ["USDT", "USDC"])

    @field_validator("quote_currencies", mode="before")
    @classmethod
    def normalize_quote_currencies(cls, value: list[str] | None) -> list[str]:
        if not value:
            return []
        return [item.strip().upper() for item in value if item.strip()]


class SymbolsConfig(BaseModel):
    include: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=list)

    @field_validator("include", "exclude", mode="before")
    @classmethod
    def normalize_symbols(cls, value: list[str] | None) -> list[str]:
        if not value:
            return []
        return [item.strip().upper() for item in value if item.strip()]


class ExchangeConfig(BaseModel):
    id: str
    enabled: bool = True
    options: dict = Field(default_factory=dict)

    @field_validator("id")
    @classmethod
    def normalize_id(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not normalized:
            raise ValueError("id биржи не может быть пустым")
        return normalized


class AppConfig(BaseModel):
    mode: TradeMode = TradeMode.PAPER
    scanner: ScannerConfig = Field(default_factory=ScannerConfig)
    symbols: SymbolsConfig = Field(default_factory=SymbolsConfig)
    exchanges: list[ExchangeConfig] = Field(default_factory=list)

    @property
    def enabled_exchanges(self) -> list[ExchangeConfig]:
        return [exchange for exchange in self.exchanges if exchange.enabled]


def load_config(config_path: Path | None = None) -> AppConfig:
    load_dotenv()

    path = config_path or DEFAULT_CONFIG_PATH
    if not path.exists():
        if EXAMPLE_CONFIG_PATH.exists():
            raise FileNotFoundError(
                f"Файл {path} не найден. Скопируйте {EXAMPLE_CONFIG_PATH} в {path} "
                "и настройте параметры."
            )
        raise FileNotFoundError(f"Файл конфигурации {path} не найден.")

    with path.open(encoding="utf-8") as config_file:
        raw = yaml.safe_load(config_file) or {}

    config = AppConfig.model_validate(raw)
    logger.info(
        "Конфигурация загружена: mode=%s, бирж=%d",
        config.mode.value,
        len(config.enabled_exchanges),
    )
    return config


def get_exchange_credentials(exchange_id: str) -> dict[str, str]:
    """Читает API-ключи из переменных окружения для указанной биржи."""
    prefix = exchange_id.upper()
    credentials: dict[str, str] = {}

    key = os.getenv(f"{prefix}_API_KEY")
    secret = os.getenv(f"{prefix}_API_SECRET")
    passphrase = os.getenv(f"{prefix}_API_PASSPHRASE")

    if key:
        credentials["apiKey"] = key
    if secret:
        credentials["secret"] = secret
    if passphrase:
        credentials["password"] = passphrase

    return credentials
