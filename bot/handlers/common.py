from aiogram import Router, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message
from aiogram.fsm.context import FSMContext

from bot import storage
from bot.keyboards import get_main_keyboard

router = Router(name="common_router")


@router.message(CommandStart())
async def handle_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    user_id = message.from_user.id
    is_adm = storage.is_admin(user_id)

    welcome_text = (
        "👋 <b>Добро пожаловать!</b> Это бот расписания занятий НТЭК.\n\n"
        "<b>Быстрый доступ:</b>\n"
        "• <b>📅 Расписание</b> — просмотр актуального расписания\n"
        "• <b>🔔 Звонки</b> — время занятий и перемен\n"
        "• <b>📦 Архив расписаний</b> — история прошлых дней\n"
        "• <b>✉️ Связь с админом</b> — задать вопрос или отправить расписание\n"
        "• <b>⭐ Поддержать</b> — помочь проекту через Telegram Stars\n\n"
        "⚡ <i>Бот проверяет обновления каждые 10 минут и присылает уведомление при изменении!</i>"
    )
    await message.answer(welcome_text, reply_markup=get_main_keyboard(is_adm))


@router.message(Command("help"))
async def handle_help(message: Message, state: FSMContext) -> None:
    await state.clear()
    is_adm = storage.is_admin(message.from_user.id)
    help_text = (
        "ℹ️ <b>Справка по боту:</b>\n\n"
        "• <b>📅 Расписание</b> — расписание для учащихся, преподавателей и от учащихся.\n"
        "• <b>🔔 Звонки</b> — расписание звонков.\n"
        "• <b>📦 Архив</b> — просмотр расписаний за предыдущие даты.\n"
        "• <b>✉️ Связь с админом</b> — отправка обращения администрации (текст, фото, кружки, голос).\n"
        "• <b>⭐ Поддержать</b> — донат разработчику через Telegram Stars.\n\n"
        "Бот автоматически отслеживает расписание с официального сайта НТЭК."
    )
    await message.answer(help_text, reply_markup=get_main_keyboard(is_adm))


@router.message(F.text == "🔙 Главное меню")
async def handle_back_to_main(message: Message, state: FSMContext) -> None:
    await state.clear()
    is_adm = storage.is_admin(message.from_user.id)
    await message.answer("Главное меню:", reply_markup=get_main_keyboard(is_adm))


@router.message(F.text == "Отмена")
async def handle_cancel_text(message: Message, state: FSMContext) -> None:
    current_state = await state.get_state()
    if current_state:
        await state.clear()
        is_adm = storage.is_admin(message.from_user.id)
        await message.answer("Действие отменено.", reply_markup=get_main_keyboard(is_adm))
