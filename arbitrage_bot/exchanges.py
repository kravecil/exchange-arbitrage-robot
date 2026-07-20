import ccxt.async_support as ccxt
from .config import ExchangeConfig
from .logger import logger

class ExchangeManager:
    def __init__(self, configs: list[ExchangeConfig]):
        self.exchanges: dict[str, ccxt.Exchange] = {}
        self.configs = {cfg.id: cfg for cfg in configs}

    async def initialize(self):
        for cfg in self.configs.values():
            try:
                exchange_class = getattr(ccxt, cfg.id)
                exchange = exchange_class({
                    'apiKey': cfg.apiKey,
                    'secret': cfg.secret,
                    'enableRateLimit': True,
                    'options': {'defaultType': 'spot'},
                })
                await exchange.load_markets()
                self.exchanges[cfg.id] = exchange
                logger.info(f"✅ Биржа [bold green]{cfg.id}[/] успешно подключена. Активных пар: {len([m for m in exchange.markets.values() if m['active']])}")
            except Exception as e:
                logger.error(f"❌ Ошибка подключения к {cfg.id}: {e}")

    def get_taker_fee(self, exchange_id: str) -> float:
        """Возвращает комиссию мейкера/тейкера в процентах"""
        cfg = self.configs[exchange_id]
        if cfg.taker_fee_percent is not None:
            return cfg.taker_fee_percent
        
        ex = self.exchanges[exchange_id]
        # В ccxt комиссии хранятся в долях (0.001 = 0.1%), переводим в проценты
        fee = ex.fees['trading']['taker']
        return fee * 100.0 

    async def close_all(self):
        for ex in self.exchanges.values():
            await ex.close()