import asyncio
from typing import AsyncGenerator
from .exchanges import ExchangeManager
from .config import Settings
from .logger import logger

class ArbitrageOpportunity:
    def __init__(self, symbol: str, buy_exchange: str, sell_exchange: str, 
                 buy_price: float, sell_price: float, spread_percent: float, net_profit_percent: float,
                 expiry: str = None):
        self.symbol = symbol
        self.buy_exchange = buy_exchange
        self.sell_exchange = sell_exchange
        self.buy_price = buy_price
        self.sell_price = sell_price
        self.spread_percent = spread_percent
        self.net_profit_percent = net_profit_percent
        self.expiry = expiry  # Дата экспирации для фьючерсов

class MarketScanner:
    def __init__(self, manager: ExchangeManager, settings: Settings):
        self.manager = manager
        self.settings = settings

    def _get_common_active_pairs(self) -> list[str]:
        """Находит пересечение активных фьючерсных торговых пар на всех биржах"""
        active_sets = []
        for ex_id, ex in self.manager.exchanges.items():
            active_symbols = {
                symbol for symbol, market in ex.markets.items() 
                if market['active'] and (market['future'] or market['swap'])
            }
            active_sets.append(active_symbols)
        
        if not active_sets:
            return []
            
        common_pairs = set.intersection(*active_sets)
        
        # Применяем фильтры из конфига
        whitelist = set(self.settings.pairs.whitelist)
        blacklist = set(self.settings.pairs.blacklist)
        
        if whitelist:
            common_pairs = common_pairs.intersection(whitelist)
        
        common_pairs = common_pairs - blacklist
        return list(common_pairs)

    async def fetch_all_tickers(self, symbols: list[str]) -> dict[str, dict[str, dict]]:
        """Асинхронно запрашивает тикеры со всех бирж"""
        tasks = []
        for ex_id, ex in self.manager.exchanges.items():
            tasks.append(self._safe_fetch_tickers(ex, ex_id, symbols))
        
        results = await asyncio.gather(*tasks)
        
        # Формат: { 'BTC/USDT-260920': { 'binance': {ticker_data}, 'bybit': {ticker_data} } }
        tickers_map = {}
        for ex_id, tickers in results:
            for symbol in symbols:
                if symbol in tickers:
                    if symbol not in tickers_map:
                        tickers_map[symbol] = {}
                    tickers_map[symbol][ex_id] = tickers[symbol]
        return tickers_map

    async def _safe_fetch_tickers(self, ex, ex_id, symbols):
        try:
            # Пробуем загрузить все тикеры и отфильтровать локально
            # Это более надежный способ, чем передавать список символов
            all_tickers = await ex.fetch_tickers()
            
            # Фильтруем только нужные символы
            filtered_tickers = {
                symbol: ticker for symbol, ticker in all_tickers.items() 
                if symbol in symbols
            }
            return ex_id, filtered_tickers
        except Exception as e:
            logger.warning(f"⚠️ Ошибка загрузки тикеров с {ex_id}: {e}")
            return ex_id, {}

    async def find_opportunities(self) -> AsyncGenerator[ArbitrageOpportunity, None]:
        symbols = self._get_common_active_pairs()
        if len(symbols) < 5:
            logger.warning("Слишком мало общих фьючерсных пар для анализа.")
            return

        logger.info(f"🔍 Сканирование {len(symbols)} общих фьючерсных пар на {len(self.manager.exchanges)} биржах...")
        tickers_map = await self.fetch_all_tickers(symbols)

        for symbol, exchanges_data in tickers_map.items():
            if len(exchanges_data) < 2:
                continue

            # Ищем самую низкую цену продажи (Ask) и самую высокую цену покупки (Bid)
            best_ask = float('inf')
            best_bid = 0.0
            ask_exchange = ""
            bid_exchange = ""

            for ex_id, ticker in exchanges_data.items():
                if ticker['ask'] and ticker['ask'] < best_ask:
                    best_ask = ticker['ask']
                    ask_exchange = ex_id
                if ticker['bid'] and ticker['bid'] > best_bid:
                    best_bid = ticker['bid']
                    bid_exchange = ex_id

            if ask_exchange == bid_exchange or best_ask == 0:
                continue

            # Считаем спред и комиссии
            spread_percent = ((best_bid - best_ask) / best_ask) * 100.0
            fee_buy = self.manager.get_taker_fee(ask_exchange)
            fee_sell = self.manager.get_taker_fee(bid_exchange)
            
            # Итоговая прибыль = Спред - Комиссия на покупку - Комиссия на продажу
            net_profit = spread_percent - fee_buy - fee_sell

            # Получаем дату экспирации из метаданных рынка
            expiry = None
            for ex_id, ticker in exchanges_data.items():
                market = self.manager.exchanges[ex_id].markets.get(symbol)
                if market:
                    expiry = market.get('expiry')
                    break

            if net_profit >= self.settings.min_profit_percent:
                yield ArbitrageOpportunity(
                    symbol=symbol,
                    buy_exchange=ask_exchange,
                    sell_exchange=bid_exchange,
                    buy_price=best_ask,
                    sell_price=best_bid,
                    spread_percent=spread_percent,
                    net_profit_percent=net_profit,
                    expiry=expiry
                )
