from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.filters import Command

from bot import storage
from bot.keyboards import (
    get_main_keyboard,
    get_feedback_cancel_inline,
    get_admin_reply_inline,
    get_admin_reply_cancel_inline,
)

router = Router(name="feedback_router")

MAIN_MENU_BUTTONS = {
    "📅 Расписание",
    "🔔 Звонки",
    "📦 Архив расписаний",
    "📦 Архив",
    "⭐ Поддержать",
    "⚙️ Админ-панель",
    "🔙 Главное меню",
    "Отмена",
    "❌ Завершить общение",
}


class FeedbackStates(StatesGroup):
    waiting_for_message = State()


class AdminReplyStates(StatesGroup):
    waiting_for_reply = State()


# --- Обращение пользователя к администрации ---

@router.message(F.text.in_({"✉️ Связь с админом", "📩 Написать админу", "/feedback"}))
async def start_feedback(message: Message, state: FSMContext) -> None:
    user_id = message.from_user.id

    # Проверка на мут
    if storage.is_muted(user_id):
        mute_info = storage.get_mute_info(user_id) or "навсегда"
        await message.answer(
            f"🔇 Вы ограничены в отправке сообщений администрации ({mute_info}).",
            reply_markup=get_main_keyboard(storage.is_admin(user_id)),
        )
        return

    # Проверка на задержку (cooldown)
    if not storage.can_send_message(user_id):
        remaining = storage.get_cooldown_remaining(user_id)
        minutes, seconds = remaining // 60, remaining % 60
        time_str = f"{minutes} мин {seconds} сек" if minutes > 0 else f"{seconds} сек"
        await message.answer(
            f"⏳ Отправлять обращения можно не чаще одного раза в минуту.\nПопробуйте через {time_str}.",
            reply_markup=get_main_keyboard(storage.is_admin(user_id)),
        )
        return

    await state.set_state(FeedbackStates.waiting_for_message)
    prompt_text = (
        "✉️ <b>Обращение к администрации</b>\n\n"
        "Отправьте ваш вопрос, отзыв или расписание.\n"
        "<i>Поддерживаются текст, фото, голосовые сообщения и видеосообщения.</i>"
    )
    await message.answer(prompt_text, reply_markup=get_feedback_cancel_inline())


@router.callback_query(F.data == "feedback_cancel")
async def cancel_feedback_callback(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    is_adm = storage.is_admin(call.from_user.id)
    await call.message.edit_text("Отправка обращения отменена.")
    await call.bot.send_message(
        chat_id=call.from_user.id,
        text="Главное меню:",
        reply_markup=get_main_keyboard(is_adm),
    )
    await call.answer()


@router.message(FeedbackStates.waiting_for_message)
async def process_feedback_message(message: Message, state: FSMContext) -> None:
    user_id = message.from_user.id
    text = message.text or ""

    # КРИТИЧЕСКОЕ ИСПРАВЛЕНИЕ:
    # Если пользователь нажал кнопку меню или отправил команду,
    # не отправляем это админу, а сбрасываем состояние и выполняем действие!
    if text.startswith("/") or text in MAIN_MENU_BUTTONS:
        await state.clear()
        is_adm = storage.is_admin(user_id)
        if text == "Отмена" or text == "❌ Завершить общение":
            await message.answer("Ввод отменен.", reply_markup=get_main_keyboard(is_adm))
            return
        # Для других кнопок бот отправит меню и подскажет
        await message.answer("Режим отправки сообщения закрыт.", reply_markup=get_main_keyboard(is_adm))
        return

    # Определение типа контента
    msg_type = "text"
    file_id = None
    content_text = text

    if message.photo:
        msg_type = "photo"
        file_id = message.photo[-1].file_id
        content_text = message.caption or "Фотография без подписи"
    elif message.voice:
        msg_type = "voice"
        file_id = message.voice.file_id
        content_text = message.caption or "Голосовое сообщение"
    elif message.video_note:
        msg_type = "video_note"
        file_id = message.video_note.file_id
        content_text = "Видеосообщение (кружок)"
    elif not text:
        await message.answer(
            "❌ Неподдерживаемый тип сообщения. Отправьте текст, фото, голос или видеокружок.\nИли нажмите 'Отмена'.",
            reply_markup=get_feedback_cancel_inline(),
        )
        return

    username = (
        f"@{message.from_user.username}"
        if message.from_user.username
        else f"{message.from_user.first_name or ''} {message.from_user.last_name or ''}".strip() or "Без имени"
    )

    ticket_id = storage.save_message(
        user_id=user_id,
        username=username,
        message_text=content_text,
        message_type=msg_type,
        file_id=file_id,
    )
    storage.update_last_message_time(user_id)
    await state.clear()

    is_adm = storage.is_admin(user_id)
    await message.answer(
        "✅ Ваше сообщение передано администрации. Ответ поступит в этот чат.",
        reply_markup=get_main_keyboard(is_adm),
    )

    # Уведомление администраторам
    display_name = storage.user_names_data.get(str(user_id), username)
    admin_header = (
        f"📨 <b>Новое обращение #{ticket_id}</b>\n"
        f"От: <b>{display_name}</b> (ID: <code>{user_id}</code>)\n\n"
        f"💬: {content_text}"
    )
    reply_markup = get_admin_reply_inline(ticket_id)

    for admin_id in storage.admin_ids:
        try:
            target_chat = int(admin_id)
            if msg_type == "photo":
                await message.bot.send_photo(
                    target_chat,
                    file_id,
                    caption=admin_header,
                    reply_markup=reply_markup,
                )
            elif msg_type == "voice":
                await message.bot.send_voice(
                    target_chat,
                    file_id,
                    caption=admin_header,
                    reply_markup=reply_markup,
                )
            elif msg_type == "video_note":
                await message.bot.send_video_note(target_chat, file_id)
                await message.bot.send_message(
                    target_chat,
                    admin_header,
                    reply_markup=reply_markup,
                )
            else:
                await message.bot.send_message(
                    target_chat,
                    admin_header,
                    reply_markup=reply_markup,
                )
        except Exception as e:
            print(f"Ошибка уведомления админа {admin_id}: {e}")


# --- Ответ администратора на обращение ---

@router.callback_query(F.data.startswith("adm_rep:"))
async def handle_admin_reply_callback(call: CallbackQuery, state: FSMContext) -> None:
    if not storage.is_admin(call.from_user.id):
        await call.answer("Доступ только для администраторов.", show_alert=True)
        return

    ticket_id = int(call.data.split(":")[1])
    messages = storage.load_messages()
    target_msg = next((m for m in messages if m.get("id") == ticket_id), None)

    if not target_msg:
        await call.answer("Обращение не найдено.", show_alert=True)
        return

    target_user_id = target_msg["user_id"]
    display_name = storage.user_names_data.get(str(target_user_id), f"ID {target_user_id}")

    await state.set_state(AdminReplyStates.waiting_for_reply)
    await state.update_data(target_user_id=target_user_id, ticket_id=ticket_id)

    await call.message.answer(
        f"💬 <b>Ответ пользователю {display_name}</b> (Тикет #{ticket_id})\n\n"
        "Отправьте текст ответа, фото, голосовое или видеокружок:",
        reply_markup=get_admin_reply_cancel_inline(),
    )
    await call.answer()


@router.callback_query(F.data == "adm_rep_cancel")
async def cancel_admin_reply(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await call.message.edit_text("Ответ пользователю отменен.")
    await call.answer()


@router.message(AdminReplyStates.waiting_for_reply)
async def process_admin_reply_message(message: Message, state: FSMContext) -> None:
    if not storage.is_admin(message.from_user.id):
        await state.clear()
        return

    if message.text in {"Отмена", "🔙 Главное меню"} or (message.text and message.text.startswith("/")):
        await state.clear()
        await message.answer("Ответ пользователю отменен.", reply_markup=get_main_keyboard(True))
        return

    data = await state.get_data()
    target_user_id = data.get("target_user_id")
    ticket_id = data.get("ticket_id")

    admin_username = f"@{message.from_user.username}" if message.from_user.username else "Администратор"
    header = f"👨‍💼 <b>Ответ администрации ({admin_username}):</b>\n\n"

    try:
        if message.photo:
            caption = header + (message.caption or "")
            await message.bot.send_photo(target_user_id, message.photo[-1].file_id, caption=caption)
        elif message.voice:
            await message.bot.send_voice(target_user_id, message.voice.file_id, caption=header)
        elif message.video_note:
            await message.bot.send_video_note(target_user_id, message.video_note.file_id)
            await message.bot.send_message(target_user_id, header)
        elif message.text:
            await message.bot.send_message(target_user_id, header + message.text)
        else:
            await message.answer("Неподдерживаемый тип ответа. Отправьте текст, фото, голос или кружок.")
            return

        if ticket_id:
            storage.mark_message_as_replied(ticket_id)
        else:
            storage.mark_user_messages_as_replied(target_user_id)

        storage.log_admin_action(
            admin_id=message.from_user.id,
            admin_username=admin_username,
            action="ответ пользователю",
            details=f"Пользователь ID: {target_user_id}, тикет #{ticket_id}",
        )
        await state.clear()
        await message.answer("✅ Ответ успешно доставлен пользователю.", reply_markup=get_main_keyboard(True))

    except Exception as e:
        print(f"Ошибка при отправке ответа пользователю {target_user_id}: {e}")
        await message.answer(f"❌ Не удалось отправить ответ: {e}", reply_markup=get_main_keyboard(True))
        await state.clear()
