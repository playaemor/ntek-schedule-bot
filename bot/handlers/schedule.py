import os
from typing import Union
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, FSInputFile
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.exceptions import TelegramBadRequest

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

    # Проверка целостности файла расписания
    if not storage.is_valid_schedule_file(file_path):
        print(f"Файл {file_path} поврежден или не является валидным изображением/документом. Удаляем.")
        storage.invalidate_schedule_cache(schedule_type)
        try:
            os.remove(file_path)
        except Exception:
            pass
        text = "📭 Файл расписания обновляется на сервере. Пожалуйста, попробуйте через пару минут."
        if isinstance(event, CallbackQuery):
            await event.answer(text, show_alert=True)
        else:
            await event.answer(text)
        return

    bot = event.bot
    current_hash = storage.calculate_file_hash(file_path)
    file_id = storage.get_schedule_file_id(schedule_type, expected_hash=current_hash)

    # 1. Попытка отправить через сохраненный file_id (только если хэш совпадает с файлом на диске)
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
            # File ID устарел или отклонен сервером Telegram
            storage.invalidate_schedule_cache(schedule_type)

    # 2. Отправка напрямую из локального файла
    try:
        input_file = FSInputFile(file_path)
        is_pdf = file_path.lower().endswith(".pdf")

        if not is_pdf:
            try:
                msg = await bot.send_photo(
                    chat_id=chat_id,
                    photo=input_file,
                    caption=caption,
                    reply_markup=reply_markup,
                )
                if msg.photo:
                    new_file_id = msg.photo[-1].file_id
                    storage.set_schedule_file_id(schedule_type, new_file_id, current_hash)
            except TelegramBadRequest as photo_err:
                err_text = str(photo_err).lower()
                # Если Telegram не смог обработать фото (IMAGE_PROCESS_FAILED, лимит размеров и т.д.),
                # отправляем исходный файл как документ
                if "image_process_failed" in err_text or "photo_invalid_dimensions" in err_text or "wrong file identifier" in err_text:
                    print(f"Telegram отклонил send_photo ({photo_err}), резервная отправка send_document...")
                    await bot.send_document(
                        chat_id=chat_id,
                        document=input_file,
                        caption=caption,
                        reply_markup=reply_markup,
                    )
                else:
                    raise
        else:
            await bot.send_document(
                chat_id=chat_id,
                document=input_file,
                caption=caption,
                reply_markup=reply_markup,
            )

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
