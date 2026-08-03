"""Тесты загрузки и валидации конфигурации."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from arbitrage_robot.config import AppConfig, StrategyConfig, SymbolsConfig, load_config
from arbitrage_robot.models import TradeMode

YAML = """
mode: paper
exchanges:
  - id: binance
    enabled: true
    market_type: swap
  - id: bybit
    enabled: true
  - id: okx
    enabled: false
strategy:
  min_spread_pct: 0.5
  exit_spread_pct: 0.1
"""


def test_load_config_from_file(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(YAML, encoding="utf-8")
    config = load_config(path)
    assert config.mode is TradeMode.PAPER
    assert [ex.id for ex in config.enabled_exchanges] == ["binance", "bybit"]
    assert config.strategy.min_spread_pct == 0.5


def test_cli_mode_has_priority(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(YAML, encoding="utf-8")
    config = load_config(path, mode=TradeMode.LIVE)
    assert config.is_live is True


def test_missing_file_gives_defaults(tmp_path: Path) -> None:
    config = load_config(tmp_path / "absent.yaml")
    assert config.mode is TradeMode.PAPER
    assert config.strategy.order_amount_quote > 0


def test_exit_threshold_must_be_below_entry() -> None:
    with pytest.raises(ValidationError):
        StrategyConfig(min_spread_pct=0.2, exit_spread_pct=0.5)


def test_duplicate_exchanges_rejected() -> None:
    with pytest.raises(ValidationError):
        AppConfig.model_validate(
            {"exchanges": [{"id": "binance"}, {"id": "binance"}]}
        )


def test_manual_symbols_require_include() -> None:
    with pytest.raises(ValidationError):
        SymbolsConfig(mode="manual")


def test_unknown_key_rejected() -> None:
    with pytest.raises(ValidationError):
        AppConfig.model_validate({"unknown_option": 1})


def test_credentials_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    config = AppConfig.model_validate({"exchanges": [{"id": "binance"}]})
    exchange = config.exchanges[0]
    monkeypatch.setenv("BINANCE_API_KEY", "key")
    monkeypatch.setenv("BINANCE_SECRET", "secret")
    assert exchange.has_credentials() is True
    assert exchange.credentials()["apiKey"] == "key"
