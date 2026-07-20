import asyncio
from typing import Dict, List, Set
import ccxt.async_support as ccxt
from rich.console import Console

console = Console()

class ArbitrageBot:
    def __init__(self, config: Dict, exchange_manager):
        self.config = config
        self.em = exchange_manager
        self.dry_run = config.get("dry_run", True)
        self.min_profit = config.get("min_profit_threshold", 1.0)
        self.trade_amount = config.get("trade_amount_usdt", 100)
        
    async def fetch_tickers(self, symbols: Set[str]) -> Dict[str, Dict]:
        tasks = []
        ex_ids = []
        
        for ex_id, ex in self.em.exchanges.items():
            tasks.append(ex.fetch_tickers(list(symbols)))
            ex_ids.append(ex_id)
            
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        tickers = {}
        for ex_id, res in zip(ex_ids, results):
            if isinstance(res, Exception):
                console.print(f"[red]Ошибка получения тикеров с {ex_id}: {res}[/red]")
                continue
            tickers[ex_id] = res
        return tickers

    async def find_opportunities(self, symbols: Set[str]):
        tickers = await self.fetch_tickers(symbols)
        if len(tickers) < 2:
            return []

        opportunities = []
        
        for symbol in symbols:
            best_buy = None  # (цена, биржа)
            best_sell = None # (цена, биржа)
            
            for ex_id, ticker_dict in tickers.items():
                ticker = ticker_dict.get(symbol)
                if not ticker or not ticker.get('bid') or not ticker.get('ask'):
                    continue
                
                ask = ticker['ask'] # Цена покупки
                bid = ticker['bid'] # Цена продажи
                
                if best_buy is None or ask < best_buy[0]:
                    best_buy = (ask, ex_id)
                    
                if best_sell is None or bid > best_sell[0]:
                    best_sell = (bid, ex_id)
            
            if best_buy and best_sell and best_buy[1] != best_sell[1]:
                buy_price, buy_ex = best_buy
                sell_price, sell_ex = best_sell
                
                # Учитываем комиссии (taker fee по умолчанию ~0.1% на каждой бирже)
                # Покупаем по ask + комиссия, продаем по bid - комиссия
                buy_market = self.em.markets[buy_ex][symbol]
                sell_market = self.em.markets[sell_ex][symbol]
                
                buy_fee = buy_market.get('taker', 0.001)
                sell_fee = sell_market.get('taker', 0.001)
                
                effective_buy_price = buy_price * (1 + buy_fee)
                effective_sell_price = sell_price * (1 - sell_fee)
                
                if effective_buy_price >= effective_sell_price:
                    continue # Нет прибыли после комиссий
                
                profit_pct = ((effective_sell_price - effective_buy_price) / effective_buy_price) * 100
                
                if profit_pct >= self.min_profit:
                    opportunities.append({
                        "symbol": symbol,
                        "buy_exchange": buy_ex,
                        "sell_exchange": sell_ex,
                        "buy_price": buy_price,
                        "sell_price": sell_price,
                        "profit_pct": profit_pct
                    })
                    
        return opportunities

    async def execute_trade(self, opp: Dict):
        symbol = opp["symbol"]
        buy_ex_id = opp["buy_exchange"]
        sell_ex_id = opp["sell_exchange"]
        buy_price = opp["buy_price"]
        
        # Рассчитываем количество актива для покупки на фиксированную сумму USDT
        amount = self.trade_amount / buy_price
        
        buy_ex = self.em.exchanges[buy_ex_id]
        sell_ex = self.em.exchanges[sell_ex_id]
        
        # Округляем количество согласно требованиям бирж
        amount = buy_ex.amount_to_precision(symbol, amount)
        
        if self.dry_run:
            console.print(f"[yellow][ТЕСТОВЫЙ РЕЖИМ] Возможность ставки:[/yellow]")
            console.print(f"Пара: {symbol}")
            console.print(f"Купить на {buy_ex_id} по {buy_price}, Продать на {sell_ex_id} по {opp['sell_price']}")
            console.print(f"Ожидаемая прибыль: {opp['profit_pct']:.2f}%")
            console.print(f"Сумма ставки: {self.trade_amount} USDT ({amount} {symbol.split('/')[0]})\n")
        else:
            console.print(f"[green][РЕАЛЬНАЯ СТАВКА] Выполняем ордера...[/green]")
            try:
                # Покупаем по рынку (taker)
                buy_order = await buy_ex.create_market_buy_order(symbol, amount)
                console.print(f"Куплено на {buy_ex_id}: {buy_order['id']}")
                
                # Продаем по рынку (taker)
                # В реальном арбитраже важно учитывать, что балансы должны быть предварительно распределены
                sell_order = await sell_ex.create_market_sell_order(symbol, amount)
                console.print(f"Продано на {sell_ex_id}: {sell_order['id']}")
            except Exception as e:
                console.print(f"[red]Ошибка при выполнении ордеров: {e}[/red]")

    async def run(self, symbols: Set[str]):
        console.print(f"[cyan]Запуск сканирования. Порог прибыли: {self.min_profit}%[/cyan]")
        while True:
            try:
                opportunities = await self.find_opportunities(symbols)
                if opportunities:
                    # Сортируем по прибыльности
                    opportunities.sort(key=lambda x: x["profit_pct"], reverse=True)
                    for opp in opportunities:
                        await self.execute_trade(opp)
                
                await asyncio.sleep(self.config.get("check_interval_seconds", 10))
            except KeyboardInterrupt:
                console.print("\n[red]Остановка робота...[/red]")
                break
            except Exception as e:
                console.print(f"[red]Непредвиденная ошибка в главном цикле: {e}[/red]")
                await asyncio.sleep(5)