import ccxt.async_support as ccxt
from typing import Optional
from .config import ExchangeConfig
from .logger import logger
from .symbol_normalizer import SymbolNormalizer

class ExchangeManager:
    def __init__(self, configs: list[ExchangeConfig]):
        self.exchanges: dict[str, ccxt.Exchange] = {}
        self.configs = {cfg.id: cfg for cfg in configs}
        self._normalized_markets: dict[str, dict[str, dict]] = {}  # {exchange_id: {normalized_symbol: market_data}}

    async def initialize(self):
        for cfg in self.configs.values():
            try:
                exchange_class = getattr(ccxt, cfg.id)
                exchange = exchange_class({
                    'apiKey': cfg.apiKey,
                    'secret': cfg.secret,
                    'enableRateLimit': True,
                    'options': {'defaultType': 'future'},
                })
                await exchange.load_markets()
                self.exchanges[cfg.id] = exchange
                
                # Нормализуем торговые пары для этой биржи
                normalized_markets = {}
                for symbol, market in exchange.markets.items():
                    if market['active']:
                        norm_symbol = SymbolNormalizer.normalize_symbol_from_market(market)
                        if norm_symbol:
                            normalized_markets[norm_symbol] = market
                
                self._normalized_markets[cfg.id] = normalized_markets
                logger.info(f"✅ Биржа [bold green]{cfg.id}[/] успешно подключена. Активных пар: {len(normalized_markets)}")
            except Exception as e:
                logger.error(f"❌ Ошибка подключения к {cfg.id}: {e}")

    def get_taker_fee(self, exchange_id: str) -> float:
        """Возвращает комиссию тейкера в процентах"""
        cfg = self.configs[exchange_id]
        if cfg.taker_fee_percent is not None:
            return cfg.taker_fee_percent
        
        ex = self.exchanges[exchange_id]
        # В ccxt комиссии хранятся в долях (0.001 = 0.1%), переводим в проценты
        # Берем taker комиссию из trading fees
        fee = ex.fees.get('trading', {}).get('taker')
        if fee is None:
            # Если комиссия неизвестна, используем стандартную ставку 0.1%
            return 0.1
        return fee * 100.0
    
    def get_markets_by_exchange(self, exchange_id: str) -> dict[str, dict]:
        """Возвращает нормализованные торговые пары для биржи"""
        return self._normalized_markets.get(exchange_id, {})

    async def close_all(self):
        for ex in self.exchanges.values():
            await ex.close()
