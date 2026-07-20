import asyncio
from .config import Settings
from .exchanges import ExchangeManager
from .scanner import MarketScanner
from .executor import Executor
from .logger import logger

async def run_bot():
    settings = Settings.load()
    
    if not settings.exchanges:
        logger.error("В config.yaml не указано ни одной биржи!")
        return

    manager = ExchangeManager(settings.exchanges)
    scanner = MarketScanner(manager, settings)
    executor = Executor(settings)

    try:
        await manager.initialize()
        
        if len(manager.exchanges) < 2:
            logger.error("Для арбитража необходимо подключить минимум 2 биржи.")
            return

        logger.info(f"🤖 Бот запущен в режиме: [bold cyan]{settings.mode.upper()}[/]")
        logger.info(f"🎯 Целевая прибыль: {settings.min_profit_percent}% | Интервал: {settings.scan_interval_seconds}с")
        
        while True:
            try:
                async for opportunity in scanner.find_opportunities():
                    await executor.execute(opportunity)
            except Exception as e:
                logger.error(f"Ошибка в цикле сканирования: {e}")
                
            await asyncio.sleep(settings.scan_interval_seconds)
            
    except KeyboardInterrupt:
        logger.info("🛑 Остановка бота по команде пользователя...")
    finally:
        await manager.close_all()
        logger.info("👋 Соединения закрыты. До свидания!")

def main():
    try:
        asyncio.run(run_bot())
    except Exception as e:
        logger.critical(f"Критическая ошибка: {e}")

if __name__ == "__main__":
    main()