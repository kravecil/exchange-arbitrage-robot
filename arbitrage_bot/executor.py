from .scanner import ArbitrageOpportunity
from .config import Settings
from .logger import logger
from rich.console import Console
from rich.table import Table

console = Console()

class Executor:
    def __init__(self, settings: Settings):
        self.settings = settings

    async def execute(self, opp: ArbitrageOpportunity):
        if self.settings.mode == "paper":
            self._print_paper_trade(opp)
        elif self.settings.mode == "live":
            await self._execute_live_trade(opp)
        else:
            logger.error(f"Неизвестный режим: {self.settings.mode}")

    def _print_paper_trade(self, opp: ArbitrageOpportunity):
        table = Table(title="📄 PAPER TRADE (Симуляция)", show_header=True, header_style="bold magenta")
        table.add_column("Пара", style="cyan")
        table.add_column("Покупка", style="green")
        table.add_column("Продажа", style="red")
        table.add_column("Спред", justify="right")
        table.add_column("Чистая прибыль", justify="right", style="bold green")

        table.add_row(
            opp.symbol,
            f"{opp.buy_exchange} @ {opp.buy_price:.4f}",
            f"{opp.sell_exchange} @ {opp.sell_price:.4f}",
            f"{opp.spread_percent:.3f}%",
            f"{opp.net_profit_percent:.3f}%"
        )
        console.print(table)
        logger.info(f"[dim]💡 В режиме 'live' здесь произошла бы одновременная покупка и продажа.[/dim]")

    async def _execute_live_trade(self, opp: ArbitrageOpportunity):
        # ВНИМАНИЕ: Для реальной торговли у вас должны быть средства (например, USDT) 
        # на обеих биржах, чтобы совершить сделки ОДНОВРЕМЕННО. 
        # Иначе вы рискуете попасть в "legs risk" (одна биржа исполнит ордер, а вторая нет).
        
        logger.info(f"🚀 [bold red]LIVE TRADE[/] | {opp.symbol} | Buy: {opp.buy_exchange} | Sell: {opp.sell_exchange} | Profit: {opp.net_profit_percent:.2f}%")
        
        # Здесь должен быть код размещения ордеров.
        # Для MVP мы просто логируем, но в продакшене нужно использовать asyncio.gather 
        # для одновременной отправки create_market_buy_order и create_market_sell_order.
        
        # Пример (раскомментировать и доработать для продакшена):
        # try:
        #     buy_task = self.manager.exchanges[opp.buy_exchange].create_market_buy_order(opp.symbol, amount)
        #     sell_task = self.manager.exchanges[opp.sell_exchange].create_market_sell_order(opp.symbol, amount)
        #     await asyncio.gather(buy_task, sell_task)
        # except Exception as e:
        #     logger.error(f"Ошибка исполнения LIVE сделки: {e}")
        
        logger.warning("⚠️ [bold yellow]LIVE режим активен, но логика исполнения ордеров закомментирована в целях безопасности. Раскомментируйте в executor.py[/]")