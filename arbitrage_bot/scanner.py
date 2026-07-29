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
        self._symbol_mapping = self._build_symbol_mapping()

    def _build_symbol_mapping(self) -> dict[str, dict[str, str]]:
        """
        Строит маппинг нестандартных символов бирж на канонический формат ccxt.
        Возвращает словарь: {канонический_символ: {биржа: нестандартный_символ}}
        """
        mapping = dict(self.settings.pairs.symbol_mapping)
        
        # Добавляем встроенные маппинги для известных проблемных пар
        builtin = {
            "SHIB/USDT:USDT": {
                "binance": "1000SHIB/USDT:USDT",
                "bybit": "SHIB1000/USDT:USDT",
            },
            "PEPE/USDT:USDT": {
                "binance": "1000PEPE/USDT:USDT",
                "bybit": "1000PEPE/USDT:USDT",
            },
        }
        for canonical, exchanges in builtin.items():
            if canonical not in mapping:
                mapping[canonical] = exchanges
        
        return mapping

    def _get_common_active_pairs(self) -> list[str]:
        """
        Находит пересечение активных swap-торговых пар на всех биржах.
        Учитывает маппинг нестандартных символов.
        """
        # Собираем swap-рынки с фильтрацией по типу контракта
        swap_sets = []
        for ex_id, ex in self.manager.exchanges.items():
            active_symbols = set()
            for symbol, market in ex.markets.items():
                if not market['active']:
                    continue
                
                # Применяем фильтр по типу контракта
                contract_type = self.settings.pairs.contract_type
                if contract_type:
                    if contract_type == 'swap' and not market.get('swap'):
                        continue
                    elif contract_type == 'future' and not market.get('future'):
                        continue
                else:
                    # По умолчанию используем только swap (перпетуалы)
                    if not market.get('swap'):
                        continue
                
                active_symbols.add(symbol)
            swap_sets.append(active_symbols)
        
        if not swap_sets:
            return []
        
        # Находим пересечение
        common_pairs = set.intersection(*swap_sets)
        
        # Применяем blacklist
        blacklist = set(self.settings.pairs.blacklist)
        common_pairs = common_pairs - blacklist
        
        # Применяем whitelist с расширением через маппинг
        whitelist = set(self.settings.pairs.whitelist)
        if whitelist:
            common_pairs = self._apply_whitelist(common_pairs, whitelist)
        
        return list(common_pairs)

    def _apply_whitelist(self, common_pairs: set, whitelist: set) -> set:
        """
        Применяет whitelist с учётом маппинга нестандартных символов.
        """
        result = set()
        
        for symbol in common_pairs:
            # Если символ есть в whitelist — добавляем
            if symbol in whitelist:
                result.add(symbol)
            # Если символ есть в маппинге и его каноническая версия в whitelist — добавляем
            elif symbol in self._symbol_mapping:
                canonical = symbol
                # Проверяем, есть ли этот символ в маппинге как нестандартный
                for canonical_sym, exchanges in self._symbol_mapping.items():
                    if symbol in exchanges.values() and canonical_sym in whitelist:
                        result.add(symbol)
                        break
        
        # Если whitelist содержит канонические символы, добавляем их
        for wl_symbol in whitelist:
            if wl_symbol not in result:
                # Проверяем, есть ли этот символ в маппинге
                if wl_symbol in self._symbol_mapping:
                    # Проверяем, есть ли хотя бы один вариант из маппинга в common_pairs
                    for ex_id, ex_symbol in self._symbol_mapping[wl_symbol].items():
                        if ex_symbol in common_pairs:
                            result.add(wl_symbol)
                            break
        
        return result

    def normalize_symbol(self, symbol: str, exchange_id: str) -> str:
        """
        Нормализует символ для данной биржи.
        Если символ нестандартный для биржи, возвращает канонический вариант.
        """
        # Проверяем обратный маппинг: {биржа: {нестандартный_символ: канонический}}
        reverse_mapping = {}
        for canonical, exchanges in self._symbol_mapping.items():
            for ex_id, ex_symbol in exchanges.items():
                if ex_id not in reverse_mapping:
                    reverse_mapping[ex_id] = {}
                reverse_mapping[ex_id][ex_symbol] = canonical
        
        if exchange_id in reverse_mapping and symbol in reverse_mapping[exchange_id]:
            return reverse_mapping[exchange_id][symbol]
        
        return symbol

    def get_symbol_for_exchange(self, canonical_symbol: str, exchange_id: str) -> str:
        """
        Получает символ для конкретной биржи из маппинга.
        """
        if canonical_symbol in self._symbol_mapping:
            return self._symbol_mapping[canonical_symbol].get(exchange_id, canonical_symbol)
        return canonical_symbol

    async def fetch_all_tickers(self, symbols: list[str]) -> dict[str, dict[str, dict]]:
        """Асинхронно запрашивает тикеры со всех бирж"""
        tasks = []
        for ex_id, ex in self.manager.exchanges.items():
            tasks.append(self._safe_fetch_tickers(ex, ex_id, symbols))
        
        results = await asyncio.gather(*tasks)
        
        # Формат: { 'BTC/USDT:USDT': { 'binance': {ticker_data}, 'bybit': {ticker_data} } }
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
