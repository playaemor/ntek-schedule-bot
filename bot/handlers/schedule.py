import os
from typing import Union
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, FSInputFile
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext

from bot.config import (
    SCHEDULE_STUDENTS_FILE,
    SCHEDULE_TEACHERS_FILE,
    SCHEDULE_BELLS_FILE,
    SCHEDULE_STUDENT_MADE_FILE,
)
from bot import storage
from bot.keyboards import get_schedule_menu_inline, get_schedule_actions_inline

router = Router(name="schedule_router")

SCHEDULE_CONFIG = {
    "учащихся": {
        "file": SCHEDULE_STUDENTS_FILE,
        "caption": "📅 <b>Расписание занятий</b>",
    },
    "преподавателей": {
        "file": SCHEDULE_TEACHERS_FILE,
        "caption": "👨‍🏫 <b>Расписание для преподавателей</b>",
    },
    "звонков": {
        "file": SCHEDULE_BELLS_FILE,
        "caption": "🔔 <b>Расписание звонков</b>",
    },
    "от учащихся": {
        "file": SCHEDULE_STUDENT_MADE_FILE,
        "caption": "📝 <b>Расписание от учащихся</b>",
    },
}

ALIAS_TO_TYPE = {
    "students": "учащихся",
    "teachers": "преподавателей",
    "bells": "звонков",
    "student_made": "от учащихся",
}


async def deliver_schedule(event: Union[Message, CallbackQuery], schedule_type: str) -> None:
    """Отправляет фотографию расписания с кэшированием file_id."""
    cfg = SCHEDULE_CONFIG.get(schedule_type)
    if not cfg:
        if isinstance(event, CallbackQuery):
            await event.answer("Неизвестный тип расписания.", show_alert=True)
        else:
            await event.answer("Неизвестный тип расписания.")
        return

    file_path = cfg["file"]
    caption = cfg["caption"]
    chat_id = event.from_user.id
    reply_markup = get_schedule_actions_inline(schedule_type)

    if not os.path.exists(file_path):
        text = "📭 Расписание еще не загружено на сервер."
        if isinstance(event, CallbackQuery):
            await event.answer(text, show_alert=True)
        else:
            await event.answer(text)
        return

    bot = event.bot
    file_id = storage.schedule_file_ids.get(schedule_type)

    # 1. Попытка отправить через сохраненный file_id (мгновенно, без повторной загрузки)
    if file_id:
        try:
            await bot.send_photo(
                chat_id=chat_id,
                photo=file_id,
                caption=caption,
                reply_markup=reply_markup,
            )
            if isinstance(event, CallbackQuery):
                await event.answer()
            return
        except Exception:
            # File ID устарел или сброшен
            storage.set_schedule_file_id(schedule_type, None)

    # 2. Отправка напрямую из локального файла
    try:
        input_file = FSInputFile(file_path)
        msg = await bot.send_photo(
            chat_id=chat_id,
            photo=input_file,
            caption=caption,
            reply_markup=reply_markup,
        )
        if msg.photo:
            new_file_id = msg.photo[-1].file_id
            storage.set_schedule_file_id(schedule_type, new_file_id)

        if isinstance(event, CallbackQuery):
            await event.answer()
    except Exception as e:
        print(f"Ошибка отправки расписания ({schedule_type}): {e}")
        text = "❌ Не удалось отправить расписание. Попробуйте позже."
        if isinstance(event, CallbackQuery):
            await event.answer(text, show_alert=True)
        else:
            await event.answer(text)


@router.message(Command("schedule"))
@router.message(F.text == "📅 Расписание")
async def handle_schedule_menu(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "Выберите нужное расписание:",
        reply_markup=get_schedule_menu_inline(),
    )


@router.message(Command("bells"))
@router.message(F.text == "🔔 Звонки")
async def handle_bells_button(message: Message, state: FSMContext) -> None:
    await state.clear()
    await deliver_schedule(message, "звонков")


@router.callback_query(F.data.startswith("sched:"))
async def handle_schedule_callback(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    code = call.data.split(":")[1]
    schedule_type = ALIAS_TO_TYPE.get(code, "учащихся")
    await deliver_schedule(call, schedule_type)
