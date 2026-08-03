"""Реестр адаптеров бирж.

Добавление новой биржи в 99 % случаев не требует кода — достаточно указать её
идентификатор ccxt в ``config.yaml``. Если же нужна специфика, создайте
наследника :class:`~arbitrage_robot.exchanges.base.ExchangeClient` и повесьте
на него декоратор :func:`register_exchange`.

.. code-block:: python

    @register_exchange("bybit")
    class BybitClient(ExchangeClient):
        ...
"""

from __future__ import annotations

from collections.abc import Callable

from ..config import ExchangeConfig, FeesConfig
from .base import ExchangeClient

__all__ = ["build_client", "create_clients", "register_exchange", "registered_exchanges"]

_REGISTRY: dict[str, type[ExchangeClient]] = {}


def register_exchange(
    exchange_id: str,
) -> Callable[[type[ExchangeClient]], type[ExchangeClient]]:
    """Зарегистрировать специализированный класс клиента для биржи."""

    def decorator(cls: type[ExchangeClient]) -> type[ExchangeClient]:
        _REGISTRY[exchange_id.lower()] = cls
        return cls

    return decorator


def registered_exchanges() -> tuple[str, ...]:
    """Список бирж со специализированными адаптерами."""
    return tuple(sorted(_REGISTRY))


def build_client(config: ExchangeConfig, fees_config: FeesConfig) -> ExchangeClient:
    """Создать клиент биржи: специализированный класс или универсальный."""
    client_cls = _REGISTRY.get(config.id.lower(), ExchangeClient)
    return client_cls(config, fees_config)


def create_clients(
    configs: tuple[ExchangeConfig, ...], fees_config: FeesConfig
) -> dict[str, ExchangeClient]:
    """Создать клиентов для всех включённых бирж."""
    return {cfg.id: build_client(cfg, fees_config) for cfg in configs if cfg.enabled}
