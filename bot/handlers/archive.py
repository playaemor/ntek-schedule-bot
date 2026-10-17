import os
from typing import Union
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, FSInputFile
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext

from bot.services.archive import get_archived_items
from bot.keyboards import get_archive_inline

router = Router(name="archive_router")

PAGE_SIZE = 5


def format_archive_label(folder_name: str) -> str:
    """Форматирует название папки архива (например, 2026-10-15_14-30-00_учащихся) в читаемый вид."""
    parts = folder_name.split("_", 2)
    if len(parts) >= 3:
        date_part = parts[0]
        type_part = parts[2]
        return f"{date_part} ({type_part})"
    return folder_name.replace("_", " ")


async def show_archive(event: Union[Message, CallbackQuery], page: int = 0) -> None:
    items = get_archived_items()
    if not items:
        text = "📭 В архиве пока нет сохраненных расписаний."
        if isinstance(event, CallbackQuery):
            await event.answer(text, show_alert=True)
        else:
            await event.answer(text)
        return

    total_pages = (len(items) + PAGE_SIZE - 1) // PAGE_SIZE
    page = max(0, min(page, total_pages - 1))
    start_idx = page * PAGE_SIZE
    page_items = items[start_idx : start_idx + PAGE_SIZE]

    formatted_items = [
        (format_archive_label(entry_name), items.index((entry_name, path)))
        for entry_name, path in page_items
    ]

    text = (
        f"📦 <b>Архив расписаний</b> (Страница {page + 1} из {total_pages})\n\n"
        "Нажмите на дату, чтобы открыть снимок расписания:"
    )
    keyboard = get_archive_inline(formatted_items, page, total_pages)

    if isinstance(event, CallbackQuery):
        try:
            await event.message.edit_text(text, reply_markup=keyboard)
        except Exception:
            await event.message.answer(text, reply_markup=keyboard)
        await event.answer()
    else:
        await event.answer(text, reply_markup=keyboard)


@router.message(Command("archive"))
@router.message(F.text.in_({"📦 Архив расписаний", "📦 Архив"}))
async def handle_archive_command(message: Message, state: FSMContext) -> None:
    await state.clear()
    await show_archive(message, page=0)


@router.callback_query(F.data.startswith("arch_pg:"))
async def handle_archive_page(call: CallbackQuery, state: FSMContext) -> None:
    page = int(call.data.split(":")[1])
    await show_archive(call, page=page)


@router.callback_query(F.data == "arch_noop")
async def handle_archive_noop(call: CallbackQuery) -> None:
    await call.answer()


@router.callback_query(F.data == "arch_close")
async def handle_archive_close(call: CallbackQuery) -> None:
    await call.message.delete()
    await call.answer()


@router.callback_query(F.data.startswith("arch_get:"))
async def handle_archive_get(call: CallbackQuery) -> None:
    idx = int(call.data.split(":")[1])
    items = get_archived_items()

    if 0 <= idx < len(items):
        entry_name, file_path = items[idx]
        if os.path.exists(file_path):
            await call.answer("Отправляю снимок...")
            label = format_archive_label(entry_name)
            caption = f"📦 <b>Архив:</b> {label}"
            await call.bot.send_photo(
                chat_id=call.from_user.id,
                photo=FSInputFile(file_path),
                caption=caption,
            )
        else:
            await call.answer("Файл не найден на диске.", show_alert=True)
    else:
        await call.answer("Запись не найдена.", show_alert=True)
