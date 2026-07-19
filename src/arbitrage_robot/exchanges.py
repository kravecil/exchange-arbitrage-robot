from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import ccxt.async_support as ccxt

from arbitrage_robot.config import AppConfig, ExchangeConfig, get_exchange_credentials

if TYPE_CHECKING:
    from ccxt.async_support.base.exchange import Exchange

logger = logging.getLogger(__name__)


class ExchangeManager:
    """Создаёт и управляет экземплярами бирж ccxt."""

    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._exchanges: dict[str, Exchange] = {}

    @property
    def exchanges(self) -> dict[str, Exchange]:
        return dict(self._exchanges)

    @property
    def exchange_ids(self) -> list[str]:
        return list(self._exchanges.keys())

    async def connect(self) -> None:
        for exchange_cfg in self._config.enabled_exchanges:
            exchange = self._create_exchange(exchange_cfg)
            await exchange.load_markets()
            self._exchanges[exchange_cfg.id] = exchange
            logger.info(
                "Биржа %s подключена, доступно рынков: %d",
                exchange_cfg.id,
                len(exchange.markets),
            )

    async def close(self) -> None:
        for exchange_id, exchange in self._exchanges.items():
            await exchange.close()
            logger.debug("Соединение с %s закрыто", exchange_id)
        self._exchanges.clear()

    def _create_exchange(self, exchange_cfg: ExchangeConfig) -> Exchange:
        exchange_id = exchange_cfg.id
        if not hasattr(ccxt, exchange_id):
            supported = ", ".join(sorted(name for name in dir(ccxt) if name.islower())[:20])
            raise ValueError(
                f"Биржа '{exchange_id}' не поддерживается ccxt. "
                f"Примеры поддерживаемых id: {supported}..."
            )

        exchange_class = getattr(ccxt, exchange_id)
        params: dict = {
            "enableRateLimit": True,
            "options": exchange_cfg.options,
        }

        if self._config.mode.value == "live":
            credentials = get_exchange_credentials(exchange_id)
            if not credentials.get("apiKey") or not credentials.get("secret"):
                raise ValueError(
                    f"Для режима live нужны {exchange_id.upper()}_API_KEY и "
                    f"{exchange_id.upper()}_API_SECRET в .env"
                )
            params.update(credentials)

        return exchange_class(params)

    def get_taker_fee(self, exchange_id: str, symbol: str) -> float:
        exchange = self._exchanges[exchange_id]
        market = exchange.markets.get(symbol)
        if not market:
            return 0.001

        taker = market.get("taker")
        if taker is not None:
            return float(taker)

        fees = market.get("fees", {})
        trading_fees = fees.get("trading", {}) if isinstance(fees, dict) else {}
        if trading_fees.get("taker") is not None:
            return float(trading_fees["taker"])

        return 0.001
