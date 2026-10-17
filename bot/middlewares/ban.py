from typing import Any, Awaitable, Callable, Dict
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, User

from bot import storage


class BanAndUserMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        user: User = data.get("event_from_user")
        if user:
            # Жесткая блокировка забаненных пользователей
            if user.id in storage.banned_users:
                return None

            # Автоматическая регистрация пользователя и сохранение актуального username
            storage.register_user(user.id)
            storage.update_user_name_info(user)

        return await handler(event, data)
