import ccxt.async_support as ccxt
from typing import Dict, List, Set
import asyncio

class ExchangeManager:
    def __init__(self, config: Dict):
        self.exchanges: Dict[str, ccxt.Exchange] = {}
        self.markets: Dict[str, Dict] = {}
        
        for ex_id, ex_config in config["exchanges"].items():
            if ex_config.get("enabled", False):
                exchange_class = getattr(ccxt, ex_id)
                ex_params = {
                    "apiKey": ex_config.get("apiKey", ""),
                    "secret": ex_config.get("secret", ""),
                    "enableRateLimit": True,
                }
                if "password" in ex_config:
                    ex_params["password"] = ex_config["password"]
                
                self.exchanges[ex_id] = exchange_class(ex_params)

    async def load_markets(self):
        tasks = [ex.load_markets() for ex in self.exchanges.values()]
        await asyncio.gather(*tasks)
        
        for ex_id, ex in self.exchanges.items():
            self.markets[ex_id] = ex.markets

    def get_common_symbols(self, use_all: bool, include: List[str], exclude: List[str]) -> Set[str]:
        if not self.exchanges:
            return set()

        # Получаем множества символов для каждой биржи
        symbols_per_exchange = [
            set(ex.symbols) for ex in self.exchanges.values()
        ]
        
        # Пересечение всех множеств (пар, которые есть на всех биржах)
        common_symbols = set.intersection(*symbols_per_exchange) if symbols_per_exchange else set()
        
        if not use_all:
            common_symbols = set(include)
        else:
            # Если исключаем конкретные пары
            common_symbols = common_symbols - set(exclude)
            
        # Оставляем только спотовые пары (тип 'spot') для безопасности
        valid_symbols = set()
        for symbol in common_symbols:
            is_spot_everywhere = True
            for ex_id in self.exchanges.keys():
                market = self.markets[ex_id].get(symbol)
                if not market or market.get('spot') is not True:
                    is_spot_everywhere = False
                    break
            if is_spot_everywhere:
                valid_symbols.add(symbol)
                
        return valid_symbols

    async def close_all(self):
        close_tasks = [ex.close() for ex in self.exchanges.values()]
        await asyncio.gather(*close_tasks)