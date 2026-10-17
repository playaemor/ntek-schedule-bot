from aiogram.types import ReplyKeyboardMarkup, KeyboardButton


def get_main_keyboard(is_admin: bool = False) -> ReplyKeyboardMarkup:
    """Главная клавиатура бота."""
    keyboard = [
        [
            KeyboardButton(text="📅 Расписание"),
            KeyboardButton(text="🔔 Звонки"),
        ],
        [
            KeyboardButton(text="📦 Архив расписаний"),
            KeyboardButton(text="✉️ Связь с админом"),
        ],
        [
            KeyboardButton(text="⭐ Поддержать"),
        ],
    ]
    if is_admin:
        keyboard.append([KeyboardButton(text="⚙️ Админ-панель")])

    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)


def get_cancel_keyboard() -> ReplyKeyboardMarkup:
    """Клавиатура с кнопкой отмены текущего действия."""
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="Отмена")]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def get_admin_keyboard(is_super_admin: bool = False) -> ReplyKeyboardMarkup:
    """Клавиатура панели управления администратора."""
    keyboard = [
        [
            KeyboardButton(text="📊 Статистика"),
            KeyboardButton(text="📢 Рассылка"),
        ],
        [
            KeyboardButton(text="🔄 Обновить звонки"),
            KeyboardButton(text="🔄 Расписание от учащихся"),
        ],
        [
            KeyboardButton(text="📨 Просмотреть сообщения"),
            KeyboardButton(text="📨 Ответить пользователю"),
        ],
        [
            KeyboardButton(text="🚫 Забанить"),
            KeyboardButton(text="✅ Разбанить"),
        ],
        [
            KeyboardButton(text="🔇 Выдать мут"),
            KeyboardButton(text="🔊 Снять мут"),
        ],
    ]

    if is_super_admin:
        keyboard.append([
            KeyboardButton(text="➕ Добавить админа"),
            KeyboardButton(text="➖ Удалить админа"),
        ])

    keyboard.append([
        KeyboardButton(text="📁 Файлы данных"),
        KeyboardButton(text="🔙 Главное меню"),
    ])

    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)


def get_files_keyboard() -> ReplyKeyboardMarkup:
    """Клавиатура для скачивания файлов данных."""
    keyboard = [
        [
            KeyboardButton(text="📄 audit_log.json"),
            KeyboardButton(text="👥 user_names.json"),
        ],
        [
            KeyboardButton(text="📋 admins.json"),
            KeyboardButton(text="👥 users.txt"),
        ],
        [
            KeyboardButton(text="🚫 banned_users.json"),
            KeyboardButton(text="🔇 muted_users.json"),
        ],
        [
            KeyboardButton(text="⚙️ Админ-панель"),
            KeyboardButton(text="🔙 Главное меню"),
        ],
    ]
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)
