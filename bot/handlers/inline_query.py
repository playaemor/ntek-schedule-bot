import os
from typing import List
from aiogram import Router
from aiogram.types import (
    InlineQuery,
    InlineQueryResultCachedPhoto,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)

from bot import storage
from bot.handlers.schedule import SCHEDULE_CONFIG

router = Router(name="inline_query_router")

INLINE_ITEMS = [
    {
        "id": "students",
        "key": "учащихся",
        "title": "📅 Расписание занятий (учащиеся)",
        "caption": "📅 <b>Расписание занятий (для учащихся)</b>",
        "desc": "Актуальное расписание занятий с сайта НТЭК",
        "keywords": ["учащиеся", "студенты", "пары", "расписание", "занятия"],
    },
    {
        "id": "teachers",
        "key": "преподавателей",
        "title": "👨‍🏫 Расписание для преподавателей",
        "caption": "👨‍🏫 <b>Расписание для преподавателей</b>",
        "desc": "Расписание преподавательского состава НТЭК",
        "keywords": ["преподаватели", "учителя", "педагоги"],
    },
    {
        "id": "bells",
        "key": "звонков",
        "title": "🔔 Расписание звонков",
        "caption": "🔔 <b>Расписание звонков</b>",
        "desc": "Время пар и перемен (1 и 2 смены)",
        "keywords": ["звонки", "время", "перемены", "звонок"],
    },
    {
        "id": "student_made",
        "key": "от учащихся",
        "title": "📝 Расписание от учащихся",
        "caption": "📝 <b>Расписание от учащихся</b>",
        "desc": "Пользовательские и альтернативные расписания",
        "keywords": ["от учащихся", "студенческое", "альтернативное"],
    },
]


@router.inline_query()
async def handle_inline_query(inline_query: InlineQuery) -> None:
    query_text = inline_query.query.strip().lower()
    bot_user = await inline_query.bot.get_me()
    open_bot_markup = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🤖 Открыть бота", url=f"https://t.me/{bot_user.username}")]]
    )

    results: List[InlineQueryResultCachedPhoto] = []

    for item in INLINE_ITEMS:
        # Фильтрация по поисковому запросу, если указан
        if query_text:
            matches_title = query_text in item["title"].lower()
            matches_desc = query_text in item["desc"].lower()
            matches_kw = any(query_text in kw for kw in item["keywords"])
            if not (matches_title or matches_desc or matches_kw):
                continue

        cfg = SCHEDULE_CONFIG.get(item["key"])
        file_path = cfg["file"] if cfg else None
        current_hash = storage.calculate_file_hash(file_path) if file_path and os.path.exists(file_path) else None
        file_id = storage.get_schedule_file_id(item["key"], expected_hash=current_hash)
        if not file_id:
            # Отображаем только реально загруженные расписания с валидным file_id
            continue

        results.append(
            InlineQueryResultCachedPhoto(
                id=item["id"],
                photo_file_id=file_id,
                title=item["title"],
                description=item["desc"],
                caption=item["caption"],
                reply_markup=open_bot_markup,
            )
        )

    await inline_query.answer(
        results=results,
        cache_time=5,
        is_personal=False,
    )
