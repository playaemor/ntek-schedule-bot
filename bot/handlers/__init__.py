from aiogram import Router

from bot.handlers.common import router as common_router
from bot.handlers.schedule import router as schedule_router
from bot.handlers.archive import router as archive_router
from bot.handlers.feedback import router as feedback_router
from bot.handlers.donation import router as donation_router
from bot.handlers.admin import router as admin_router
from bot.handlers.inline_query import router as inline_query_router
from bot.handlers.casino import router as casino_router


def setup_routers() -> Router:
    """Объединяет все роутеры приложения в единый корневой роутер."""
    main_router = Router(name="main_router")

    # Порядок регистрации важен:
    # 1. Административная панель и модерация
    main_router.include_router(admin_router)
    # 2. Обратная связь (FSM перехват сообщений)
    main_router.include_router(feedback_router)
    # 3. Расписание и архив
    main_router.include_router(schedule_router)
    main_router.include_router(archive_router)
    # 4. Inline-режим (@bot)
    main_router.include_router(inline_query_router)
    # 5. Донаты
    main_router.include_router(donation_router)
    # 6. Секретные пасхалки
    main_router.include_router(casino_router)
    # 7. Общие команды (/start, /help, отмена)
    main_router.include_router(common_router)

    return main_router
