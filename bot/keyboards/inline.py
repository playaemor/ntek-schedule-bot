from typing import List, Tuple
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton


def get_schedule_menu_inline() -> InlineKeyboardMarkup:
    """Инлайн-меню выбора типа расписания."""
    keyboard = [
        [
            InlineKeyboardButton(text="🎓 Расписание для учащихся", callback_data="sched:students"),
        ],
        [
            InlineKeyboardButton(text="👨‍🏫 Для преподавателей", callback_data="sched:teachers"),
            InlineKeyboardButton(text="📝 От учащихся", callback_data="sched:student_made"),
        ],
        [
            InlineKeyboardButton(text="🔔 Расписание звонков", callback_data="sched:bells"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_schedule_actions_inline(current_type: str) -> InlineKeyboardMarkup:
    """Быстрые действия под фотографией расписания."""
    buttons = []
    if current_type != "звонков":
        buttons.append(InlineKeyboardButton(text="🔔 Звонки", callback_data="sched:bells"))
    if current_type != "учащихся":
        buttons.append(InlineKeyboardButton(text="🎓 Учащиеся", callback_data="sched:students"))
    buttons.append(InlineKeyboardButton(text="📦 Архив", callback_data="arch_pg:0"))

    return InlineKeyboardMarkup(inline_keyboard=[buttons])


def get_archive_inline(page_items: List[Tuple[str, int]], page: int, total_pages: int) -> InlineKeyboardMarkup:
    """Инлайн-клавиатура для навигации по архиву расписаний."""
    keyboard = []
    for display_label, original_index in page_items:
        keyboard.append([
            InlineKeyboardButton(
                text=f"📷 {display_label}",
                callback_data=f"arch_get:{original_index}"
            )
        ])

    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"arch_pg:{page - 1}"))
    nav_row.append(InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="arch_noop"))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton(text="Вперед ➡️", callback_data=f"arch_pg:{page + 1}"))

    if nav_row:
        keyboard.append(nav_row)

    keyboard.append([InlineKeyboardButton(text="Закрыть", callback_data="arch_close")])

    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_feedback_cancel_inline() -> InlineKeyboardMarkup:
    """Кнопка отмены при отправке обращения."""
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Отмена", callback_data="feedback_cancel")]]
    )


def get_admin_reply_inline(ticket_id: int) -> InlineKeyboardMarkup:
    """Кнопка быстрого ответа для администратора под входящим тикетом."""
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="✍️ Ответить", callback_data=f"adm_rep:{ticket_id}")]]
    )


def get_admin_reply_cancel_inline() -> InlineKeyboardMarkup:
    """Кнопка отмены ответа администратора."""
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Отмена", callback_data="adm_rep_cancel")]]
    )
