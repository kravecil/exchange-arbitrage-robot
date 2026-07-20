import asyncio
from core.config import load_config
from core.exchange_manager import ExchangeManager
from core.arbitrage import ArbitrageBot

async def main():
    # Загружаем конфигурацию
    config = load_config("config.json")
    
    # Инициализируем биржи
    em = ExchangeManager(config)
    
    console_print = print # fallback
    try:
        from rich.console import Console
        console = Console()
        console.print("[bold green]Загрузка рынков бирж...[/bold green]")
        await em.load_markets()
        console.print("[green]Рынки загружены.[/green]")
        
        # Получаем список рабочих пар
        symbols_cfg = config["symbols"]
        symbols = em.get_common_symbols(
            use_all=symbols_cfg["use_all_common"],
            include=symbols_cfg["include"],
            exclude=symbols_cfg["exclude"]
        )
        
        if not symbols:
            console.print("[red]Не найдено общих торговых пар. Проверьте настройки.[/red]")
            return
            
        console.print(f"Отслеживается пар: {len(symbols)}")
        
        # Запускаем бота
        bot = ArbitrageBot(config, em)
        await bot.run(symbols)
        
    except Exception as e:
        print(f"Фатальная ошибка: {e}")
    finally:
        await em.close_all()

if __name__ == "__main__":
    asyncio.run(main())