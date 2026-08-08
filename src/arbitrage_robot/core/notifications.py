"""Модуль для отправки уведомлений в Telegram при обнаружении арбитражных возможностей."""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from ...config import TelegramConfig

if TYPE_CHECKING:
    from aiogram import Bot
    from aiogram.client.default import DefaultBotProperties
    from aiogram.enums import ParseMode

__all__ = ["TelegramNotifier"]

# Кэш времени последнего уведомления для каждой пары
LastNotification = dict[str, float]


@dataclass(frozen=True)
class OpportunityInfo:
    """Информация об арбитражной возможности для уведомления."""

    symbol: str
    buy_exchange: str
    sell_exchange: str
    net_spread_pct: float
    gross_spread_pct: float
    fees_pct: float
    amount: float
    notional: float
    route: str


class TelegramNotifier:
    """Отправка уведомлений в Telegram при обнаружении арбитражных возможностей."""

    def __init__(self, config: TelegramConfig) -> None:
        self._config = config
        self._bot: Bot | None = None
        self._last_notification: LastNotification = defaultdict(float)
        self._lock = asyncio.Lock()

    @property
    def is_enabled(self) -> bool:
        """Проверка, включены ли уведомления."""
        return self._config.enabled

    @property
    def api_token(self) -> str:
        """Получение API токена из переменных окружения."""
        return self._get_env_var(self._config.api_token_env)

    @property
    def chat_id(self) -> str:
        """Получение chat_id из переменных окружения."""
        return self._get_env_var(self._config.chat_id_env)

    def _get_env_var(self, env_name: str) -> str:
        """Получение переменной окружения."""
        import os
        value = os.getenv(env_name, "").strip()
        if not value:
            raise ValueError(f"Переменная окружения {env_name} не установлена")
        return value

    async def connect(self) -> None:
        """Инициализация бота Telegram."""
        if not self._config.enabled:
            return

        from aiogram import Bot
        from aiogram.client.default import DefaultBotProperties

        try:
            token = self.api_token
            chat_id = self.chat_id

            self._bot = Bot(
                token=token,
                default=DefaultBotProperties(parse_mode="HTML")
            )

            # Проверка подключения (попытка получить информацию о боте)
            await self._bot.get_me()

        except ValueError as exc:
            raise exc
        except Exception as exc:
            raise RuntimeError(f"Не удалось подключиться к Telegram: {exc}") from exc

    async def disconnect(self) -> None:
        """Закрытие соединения с Telegram."""
        if self._bot:
            await self._bot.session.close()
            self._bot = None

    async def notify_opportunity(self, info: OpportunityInfo) -> bool:
        """
        Отправка уведомления об арбитражной возможности, если прошло достаточно времени.

        :param info: Информация об арбитражной возможности.
        :return: True, если уведомление было отправлено, False иначе.
        """
        if not self._config.enabled or not self._bot:
            return False

        async with self._lock:
            now = time.time()
            last_time = self._last_notification.get(info.symbol, 0)
            elapsed = now - last_time

            # Проверка cooldown
            if elapsed < self._config.cooldown_sec:
                return False

            # Проверка порогов спреда (отсечение аномалий)
            if info.net_spread_pct < self._config.min_spread_pct:
                return False
            if info.net_spread_pct > self._config.max_spread_pct:
                return False

            # Отправка уведомления
            try:
                await self._send_notification(info)
                self._last_notification[info.symbol] = now
                return True
            except Exception as exc:
                from ..logging_setup import get_logger
                logger = get_logger("notifications")
                logger.error("Ошибка отправки уведомления: %s", exc)
                return False

    async def _send_notification(self, info: OpportunityInfo) -> None:
        """Отправка formatted сообщения в Telegram."""
        message = self._format_message(info)
        await self._bot.send_message(chat_id=self.chat_id, text=message)

    def _format_message(self, info: OpportunityInfo) -> str:
        """Формирование HTML-сообщения для Telegram."""
        now = datetime.now().strftime("%d.%m.%Y %H:%M:%S")

        # Определение цвета и эмодзи в зависимости от спреда
        spread = info.net_spread_pct
        if spread >= 2.0:
            arrow = "🚀"
            color = "red"
        elif spread >= 1.0:
            arrow = "📈"
            color = "orange"
        else:
            arrow = "📉"
            color = "blue"

        # Более подробное сообщение с информацией о каждой бирже
        message = (
            f"{arrow} <b>АРБИТРАЖНАЯ ВОЗМОЖНОСТЬ</b> {arrow}\n\n"
            f"<b> 时间:</b> {now}\n"
            f"<b>Пара:</b> <code>{info.symbol}</code>\n\n"
            f"┌─ <b>КУПИТЬ</b> ({info.buy_exchange})\n"
            f"│  📥 <b>Цена:</b> {info.amount:.8f} {info.symbol.split('/')[0]}\n"
            f"│  💰 <b>Сумма:</b> {info.notional:.2f} USDT\n"
            f"│  📊 <b>Спрос:</b> {info.gross_spread_pct:.3f}%\n"
            f"└─ <b>ПРОДАТЬ</b> ({info.sell_exchange})\n"
            f"   📥 <b>Цена:</b> {info.amount:.8f} {info.symbol.split('/')[0]}\n"
            f"   💰 <b>Сумма:</b> {info.notional:.2f} USDT\n"
            f"   📊 <b>Предложение:</b> {info.gross_spread_pct:.3f}%\n\n"
            f"════════════════════════════════════\n"
            f"📈 <b>Чистый спред:</b> <font color='{color}'>{spread:.3f}%</font>\n"
            f"💸 <b>Комиссии:</b> {info.fees_pct:.3f}%\n"
            f"════════════════════════════════════\n"
            f"📦 <b>Маршрут:</b> {info.route}\n"
            f"🔍 <b>Грязный спред:</b> {info.gross_spread_pct:.3f}%\n"
            f"💰 <b>Потенциал:</b> {info.amount:.4f} {info.symbol.split('/')[0]}\n"
            f"💬 <b>Трейдинг:</b> {'LIVE' if spread >= 2.0 else 'PAPER'}\n"
        )
        return message

    async def notify_error(self, error_message: str) -> None:
        """
        Отправка уведомления об ошибке.

        :param error_message: Сообщение об ошибке.
        """
        if not self._config.enabled or not self._bot:
            return

        try:
            message = (
                f"⚠️ <b>ОШИБКА РОБОТА</b> ⚠️\n\n"
                f"<pre>{error_message}</pre>"
            )
            await self._bot.send_message(chat_id=self.chat_id, text=message)
        except Exception as exc:
            from ..logging_setup import get_logger
            logger = get_logger("notifications")
            logger.error("Ошибка отправки уведомления об ошибке: %s", exc)

    async def notify_start(self) -> None:
        """Отправка уведомления о запуске робота."""
        if not self._config.enabled or not self._bot:
            return

        try:
            message = (
                f"🚀 <b>АРБИТРАЖНЫЙ РОБОТ ЗАПУЩЕН</b> 🚀\n\n"
                f"📅 <b>Время:</b> {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}\n"
                f"🤖 <b>Статус:</b> Работает в режиме мониторинга арбитражных возможностей"
            )
            await self._bot.send_message(chat_id=self.chat_id, text=message)
        except Exception as exc:
            from ..logging_setup import get_logger
            logger = get_logger("notifications")
            logger.error("Ошибка отправки уведомления о запуске: %s", exc)

    async def notify_stop(self) -> None:
        """Отправка уведомления об остановке робота."""
        if not self._config.enabled or not self._bot:
            return

        try:
            message = (
                f"🛑 <b>АРБИТРАЖНЫЙ РОБОТ ОСТАНОВЛЕН</b> 🛑\n\n"
                f"📅 <b>Время:</b> {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}\n"
                f"🤖 <b>Статус:</b> Работа завершена"
            )
            await self._bot.send_message(chat_id=self.chat_id, text=message)
        except Exception as exc:
            from ..logging_setup import get_logger
            logger = get_logger("notifications")
            logger.error("Ошибка отправки уведомления об остановке: %s", exc)


# Для удобства импорта
__all__ = ["TelegramNotifier", "OpportunityInfo"]