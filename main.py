import sys
import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage

from aiogram.types import BotCommand, BotCommandScopeDefault, MenuButtonCommands

from bot.config import BOT_TOKEN
from bot.storage import init_storage
from bot.middlewares import BanAndUserMiddleware
from bot.handlers import setup_routers
from bot.services.parser import schedule_checker_loop

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("ntek_bot")


async def setup_bot_commands(bot: Bot) -> None:
    """Устанавливает список команд и нативную кнопку меню в Telegram."""
    commands = [
        BotCommand(command="schedule", description="📅 Расписание занятий"),
        BotCommand(command="bells", description="🔔 Расписание звонков"),
        BotCommand(command="archive", description="📦 Архив расписаний"),
        BotCommand(command="feedback", description="✉️ Связь с администрацией"),
        BotCommand(command="help", description="ℹ️ Справка по боту"),
    ]
    try:
        await bot.set_my_commands(commands=commands, scope=BotCommandScopeDefault())
        await bot.set_chat_menu_button(menu_button=MenuButtonCommands())
        logger.info("Нативная кнопка меню и команды успешно зарегистрированы.")
    except Exception as e:
        logger.warning(f"Не удалось установить команды меню: {e}")


async def main() -> None:
    logger.info("Инициализация локальных хранилищ данных...")
    init_storage()

    logger.info("Запуск бота Telegram...")
    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())

    # Регистрация middleware
    middleware = BanAndUserMiddleware()
    dp.message.outer_middleware(middleware)
    dp.callback_query.outer_middleware(middleware)

    # Регистрация маршрутов
    dp.include_router(setup_routers())

    # Установка команд в интерфейсе Telegram
    await setup_bot_commands(bot)

    # Запуск фонового планировщика проверки расписания
    scheduler_task = asyncio.create_task(schedule_checker_loop(bot))

    try:
        # Сброс накопившихся вебхуков и запуск polling
        await bot.delete_webhook(drop_pending_updates=True)
        logger.info("Бот успешно запущен в режиме polling.")
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        logger.info("Остановка бота...")
        scheduler_task.cancel()
        try:
            await scheduler_task
        except asyncio.CancelledError:
            pass
        await bot.session.close()
        logger.info("Бот остановлен.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Выход из программы.")
