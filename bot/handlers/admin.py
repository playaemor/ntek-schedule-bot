import os
import time
import asyncio
from typing import Optional
from aiogram import Router, F, Bot
from aiogram.types import Message, FSInputFile
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.exceptions import TelegramRetryAfter, TelegramForbiddenError, TelegramBadRequest

from bot.config import (
    BROADCAST_COOLDOWN,
    SCHEDULE_STUDENTS_FILE,
    SCHEDULE_TEACHERS_FILE,
    SCHEDULE_BELLS_FILE,
    SCHEDULE_STUDENT_MADE_FILE,
    AUDIT_FILE,
    USER_NAMES_FILE,
    ADMIN_FILE,
    USERS_FILE,
    BANNED_USERS_FILE,
    MUTED_USERS_FILE,
)
from bot import storage
from bot.services.archive import archive_schedule
from bot.keyboards import (
    get_main_keyboard,
    get_admin_keyboard,
    get_cancel_keyboard,
    get_files_keyboard,
    get_admin_reply_cancel_inline,
)
from bot.handlers.feedback import AdminReplyStates

router = Router(name="admin_router")

last_broadcast_timestamp = 0.0
broadcast_lock = asyncio.Lock()


class AdminStates(StatesGroup):
    waiting_for_ban = State()
    waiting_for_unban = State()
    waiting_for_mute = State()
    waiting_for_unmute = State()
    waiting_for_broadcast = State()
    waiting_for_bells = State()
    waiting_for_student_schedule = State()
    waiting_for_add_admin = State()
    waiting_for_remove_admin = State()
    waiting_for_reply_choice = State()


# --- Главная панель администратора ---

@router.message(Command("admin"))
@router.message(F.text.in_({"⚙️ Админ-панель", "👨‍💻 Админ-панель"}))
async def handle_admin_panel(message: Message, state: FSMContext) -> None:
    if not storage.is_admin(message.from_user.id):
        await message.answer("❌ Доступ запрещен.")
        return

    await state.clear()
    is_super = storage.is_super_admin(message.from_user.id)
    text = (
        "⚙️ <b>Панель управления администратора</b>\n\n"
        "<b>Быстрые команды:</b>\n"
        "• <code>/ban &lt;ID/@user&gt;</code> — блокировка\n"
        "• <code>/unban &lt;ID/@user&gt;</code> — разбан\n"
        "• <code>/mute &lt;ID/@user&gt; [время: 30m, 2h, 1d]</code> — мут\n"
        "• <code>/unmute &lt;ID/@user&gt;</code> — снятие мута\n"
        "• <code>/stats</code> — статистика бота"
    )
    await message.answer(text, reply_markup=get_admin_keyboard(is_super))


# --- Статистика ---

@router.message(Command("stats"))
@router.message(F.text == "📊 Статистика")
async def handle_stats(message: Message) -> None:
    if not storage.is_admin(message.from_user.id):
        return

    messages = storage.load_messages()
    unanswered = sum(1 for m in messages if not m.get("replied", False))

    stats_text = (
        "📊 <b>Статистика бота</b>\n\n"
        f"• 👥 Пользователей в базе: <b>{len(storage.user_ids)}</b>\n"
        f"• 👤 С сохраненными именами: <b>{len(storage.user_names_data)}</b>\n"
        f"• 👮‍♂️ Администраторов: <b>{len(storage.admin_ids)}</b>\n"
        f"• 🚫 В бане: <b>{len(storage.banned_users)}</b>\n"
        f"• 🔇 В муте: <b>{len(storage.muted_users)}</b>\n"
        f"• 📨 Всего обращений: <b>{len(messages)}</b>\n"
        f"• ❓ Неотвеченных обращений: <b>{unanswered}</b>\n\n"
        "<b>Наличие файлов расписаний:</b>\n"
        f"• 📅 Учащихся: {'✅' if os.path.exists(SCHEDULE_STUDENTS_FILE) else '❌'}\n"
        f"• 👨‍🏫 Преподавателей: {'✅' if os.path.exists(SCHEDULE_TEACHERS_FILE) else '❌'}\n"
        f"• 🔔 Звонков: {'✅' if os.path.exists(SCHEDULE_BELLS_FILE) else '❌'}\n"
        f"• 📝 От учащихся: {'✅' if os.path.exists(SCHEDULE_STUDENT_MADE_FILE) else '❌'}"
    )
    await message.answer(stats_text)


# --- Рассылка сообщений ---

@router.message(F.text == "📢 Рассылка")
async def start_broadcast(message: Message, state: FSMContext) -> None:
    if not storage.is_admin(message.from_user.id):
        return

    global last_broadcast_timestamp
    now = time.time()
    elapsed = now - last_broadcast_timestamp
    if elapsed < BROADCAST_COOLDOWN:
        rem = int(BROADCAST_COOLDOWN - elapsed)
        await message.answer(f"⏳ Рассылку можно делать не чаще раза в 15 секунд. Подождите {rem} сек.")
        return

    await state.set_state(AdminStates.waiting_for_broadcast)
    await message.answer(
        "📢 <b>Массовая рассылка</b>\n\n"
        "Отправьте текст или фото с подписью, которое получат все пользователи.\n"
        "Для отмены нажмите 'Отмена'.",
        reply_markup=get_cancel_keyboard(),
    )


@router.message(AdminStates.waiting_for_broadcast)
async def process_broadcast(message: Message, state: FSMContext) -> None:
    if not storage.is_admin(message.from_user.id):
        await state.clear()
        return

    if message.text == "Отмена":
        await state.clear()
        is_super = storage.is_super_admin(message.from_user.id)
        await message.answer("Рассылка отменена.", reply_markup=get_admin_keyboard(is_super))
        return

    global last_broadcast_timestamp
    async with broadcast_lock:
        last_broadcast_timestamp = time.time()

    await state.clear()
    is_super = storage.is_super_admin(message.from_user.id)
    total_users = len(storage.user_ids)
    await message.answer(f"🚀 Запуск рассылки для {total_users} пользователей...", reply_markup=get_admin_keyboard(is_super))

    is_photo = bool(message.photo)
    photo_id = message.photo[-1].file_id if is_photo else None
    caption = message.caption if is_photo else None
    text_content = message.text if not is_photo else None

    success_count = 0
    dead_users = set()
    bot = message.bot

    for uid in list(storage.user_ids):
        try:
            if is_photo:
                await bot.send_photo(uid, photo_id, caption=caption)
            else:
                await bot.send_message(uid, text_content)
            success_count += 1
            await asyncio.sleep(0.04)
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after)
            try:
                if is_photo:
                    await bot.send_photo(uid, photo_id, caption=caption)
                else:
                    await bot.send_message(uid, text_content)
                success_count += 1
            except Exception:
                pass
        except (TelegramForbiddenError, TelegramBadRequest) as e:
            err = str(e).lower()
            if any(term in err for term in ["blocked", "deactivated", "chat not found"]):
                dead_users.add(uid)
        except Exception as e:
            print(f"Ошибка рассылки пользователю {uid}: {e}")

    if dead_users:
        storage.user_ids.difference_update(dead_users)
        storage.save_users()

    admin_username = f"@{message.from_user.username}" if message.from_user.username else ""
    storage.log_admin_action(
        admin_id=message.from_user.id,
        admin_username=admin_username,
        action="рассылка",
        details=f"Тип: {'фото' if is_photo else 'текст'}, охват: {success_count}/{total_users}",
    )

    await message.answer(f"✅ Рассылка завершена!\n📊 Доставлено: <b>{success_count}/{total_users}</b>")


# --- Ручное обновление расписаний (Звонки и От учащихся) ---

@router.message(F.text == "🔄 Обновить звонки")
async def start_update_bells(message: Message, state: FSMContext) -> None:
    if not storage.is_admin(message.from_user.id):
        return
    await state.set_state(AdminStates.waiting_for_bells)
    await message.answer("📤 Отправьте новое фото расписания звонков. Или нажмите 'Отмена'.", reply_markup=get_cancel_keyboard())


@router.message(AdminStates.waiting_for_bells)
async def process_update_bells(message: Message, state: FSMContext) -> None:
    if not storage.is_admin(message.from_user.id):
        await state.clear()
        return

    if message.text == "Отмена":
        await state.clear()
        await message.answer("Обновление отменено.", reply_markup=get_admin_keyboard(storage.is_super_admin(message.from_user.id)))
        return

    if not message.photo:
        await message.answer("❌ Пожалуйста, отправьте фотографию расписания. Или нажмите 'Отмена'.")
        return

    try:
        photo = message.photo[-1]
        file_info = await message.bot.get_file(photo.file_id)
        await message.bot.download_file(file_info.file_path, destination=SCHEDULE_BELLS_FILE)

        archive_schedule(SCHEDULE_BELLS_FILE, "звонков")
        storage.set_schedule_file_id("звонков", photo.file_id)

        admin_username = f"@{message.from_user.username}" if message.from_user.username else ""
        storage.log_admin_action(message.from_user.id, "обновление звонков", "Загружен новый файл", admin_username)

        await state.clear()
        await message.answer("✅ Расписание звонков успешно обновлено!", reply_markup=get_admin_keyboard(storage.is_super_admin(message.from_user.id)))
    except Exception as e:
        await message.answer(f"❌ Ошибка обновления: {e}")


@router.message(F.text == "🔄 Расписание от учащихся")
async def start_update_student_schedule(message: Message, state: FSMContext) -> None:
    if not storage.is_admin(message.from_user.id):
        return
    await state.set_state(AdminStates.waiting_for_student_schedule)
    await message.answer("📤 Отправьте фото расписания от учащихся. Или нажмите 'Отмена'.", reply_markup=get_cancel_keyboard())


@router.message(AdminStates.waiting_for_student_schedule)
async def process_update_student_schedule(message: Message, state: FSMContext) -> None:
    if not storage.is_admin(message.from_user.id):
        await state.clear()
        return

    if message.text == "Отмена":
        await state.clear()
        await message.answer("Обновление отменено.", reply_markup=get_admin_keyboard(storage.is_super_admin(message.from_user.id)))
        return

    if not message.photo:
        await message.answer("❌ Пожалуйста, отправьте фотографию. Или нажмите 'Отмена'.")
        return

    try:
        photo = message.photo[-1]
        file_info = await message.bot.get_file(photo.file_id)
        await message.bot.download_file(file_info.file_path, destination=SCHEDULE_STUDENT_MADE_FILE)

        archive_schedule(SCHEDULE_STUDENT_MADE_FILE, "от учащихся")
        storage.last_hashes["от учащихся"] = storage.calculate_file_hash(SCHEDULE_STUDENT_MADE_FILE)
        storage.set_schedule_file_id("от учащихся", photo.file_id)

        admin_username = f"@{message.from_user.username}" if message.from_user.username else ""
        storage.log_admin_action(message.from_user.id, "обновление расписания от учащихся", "Загружен новый файл", admin_username)

        await state.clear()
        await message.answer("✅ Расписание от учащихся успешно обновлено!", reply_markup=get_admin_keyboard(storage.is_super_admin(message.from_user.id)))
    except Exception as e:
        await message.answer(f"❌ Ошибка обновления: {e}")


# --- Модерация (Бан и Разбан) ---

@router.message(Command("ban"))
async def handle_ban_command(message: Message) -> None:
    if not storage.is_admin(message.from_user.id):
        return
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer("⚠️ Использование: <code>/ban &lt;ID или @username&gt;</code>")
        return
    success, reply = storage.ban_user(args[1])
    if success:
        storage.log_admin_action(message.from_user.id, "бан", f"Цель: {args[1]}")
    await message.answer(reply)


@router.message(F.text == "🚫 Забанить")
async def start_ban_flow(message: Message, state: FSMContext) -> None:
    if not storage.is_admin(message.from_user.id):
        return
    await state.set_state(AdminStates.waiting_for_ban)
    await message.answer("🚫 Введите ID (число) или @username пользователя для блокировки:", reply_markup=get_cancel_keyboard())


@router.message(AdminStates.waiting_for_ban)
async def process_ban_step(message: Message, state: FSMContext) -> None:
    if message.text == "Отмена":
        await state.clear()
        await message.answer("Блокировка отменена.", reply_markup=get_admin_keyboard(storage.is_super_admin(message.from_user.id)))
        return

    success, reply = storage.ban_user(message.text.strip())
    if success:
        storage.log_admin_action(message.from_user.id, "бан", f"Цель: {message.text.strip()}")
    await state.clear()
    await message.answer(reply, reply_markup=get_admin_keyboard(storage.is_super_admin(message.from_user.id)))


@router.message(Command("unban"))
async def handle_unban_command(message: Message) -> None:
    if not storage.is_admin(message.from_user.id):
        return
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer("⚠️ Использование: <code>/unban &lt;ID или @username&gt;</code>")
        return
    success, reply = storage.unban_user(args[1])
    if success:
        storage.log_admin_action(message.from_user.id, "разбан", f"Цель: {args[1]}")
    await message.answer(reply)


@router.message(F.text == "✅ Разбанить")
async def start_unban_flow(message: Message, state: FSMContext) -> None:
    if not storage.is_admin(message.from_user.id):
        return
    if not storage.banned_users:
        await message.answer("ℹ️ Список заблокированных пользователей пуст.")
        return

    text = "🚫 <b>Заблокированные пользователи:</b>\n"
    for uid in list(storage.banned_users)[:15]:
        uname = storage.user_names_data.get(str(uid), "Без имени")
        text += f"• <code>{uid}</code> ({uname})\n"
    text += "\nВведите ID или @username для разблокировки:"

    await state.set_state(AdminStates.waiting_for_unban)
    await message.answer(text, reply_markup=get_cancel_keyboard())


@router.message(AdminStates.waiting_for_unban)
async def process_unban_step(message: Message, state: FSMContext) -> None:
    if message.text == "Отмена":
        await state.clear()
        await message.answer("Разблокировка отменена.", reply_markup=get_admin_keyboard(storage.is_super_admin(message.from_user.id)))
        return

    success, reply = storage.unban_user(message.text.strip())
    if success:
        storage.log_admin_action(message.from_user.id, "разбан", f"Цель: {message.text.strip()}")
    await state.clear()
    await message.answer(reply, reply_markup=get_admin_keyboard(storage.is_super_admin(message.from_user.id)))


# --- Модерация (Мут и Размут) ---

@router.message(Command("mute"))
async def handle_mute_command(message: Message) -> None:
    if not storage.is_admin(message.from_user.id):
        return
    parts = message.text.split(maxsplit=2)
    if len(parts) < 2:
        await message.answer(
            "⚠️ Использование: <code>/mute &lt;ID/@user&gt; [время: 30m, 2h, 1d]</code>\n"
            "Пример: <code>/mute @username 2h</code>"
        )
        return
    target = parts[1]
    duration = parts[2] if len(parts) > 2 else None
    success, reply = storage.mute_user(target, duration)
    if success:
        storage.log_admin_action(message.from_user.id, "мут", f"Цель: {target}, срок: {duration or 'навсегда'}")
    await message.answer(reply)


@router.message(F.text == "🔇 Выдать мут")
async def start_mute_flow(message: Message, state: FSMContext) -> None:
    if not storage.is_admin(message.from_user.id):
        return
    await state.set_state(AdminStates.waiting_for_mute)
    prompt = (
        "🔇 <b>Выдача ограничения (мута)</b>\n\n"
        "Введите ID или @username, а затем через пробел время (опционально):\n"
        "• <code>30m</code> — 30 минут\n"
        "• <code>2h</code> — 2 часа\n"
        "• <code>1d</code> — 1 день\n\n"
        "Пример: <code>@username 2h</code> (или просто ID без времени для мута навсегда)."
    )
    await message.answer(prompt, reply_markup=get_cancel_keyboard())


@router.message(AdminStates.waiting_for_mute)
async def process_mute_step(message: Message, state: FSMContext) -> None:
    if message.text == "Отмена":
        await state.clear()
        await message.answer("Выдача мута отменена.", reply_markup=get_admin_keyboard(storage.is_super_admin(message.from_user.id)))
        return

    parts = message.text.strip().split(maxsplit=1)
    target = parts[0]
    duration = parts[1] if len(parts) > 1 else None
    success, reply = storage.mute_user(target, duration)
    if success:
        storage.log_admin_action(message.from_user.id, "мут", f"Цель: {target}, срок: {duration or 'навсегда'}")

    await state.clear()
    await message.answer(reply, reply_markup=get_admin_keyboard(storage.is_super_admin(message.from_user.id)))


@router.message(Command("unmute"))
async def handle_unmute_command(message: Message) -> None:
    if not storage.is_admin(message.from_user.id):
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("⚠️ Использование: <code>/unmute &lt;ID или @username&gt;</code>")
        return
    success, reply = storage.unmute_user(parts[1])
    if success:
        storage.log_admin_action(message.from_user.id, "размут", f"Цель: {parts[1]}")
    await message.answer(reply)


@router.message(F.text == "🔊 Снять мут")
async def start_unmute_flow(message: Message, state: FSMContext) -> None:
    if not storage.is_admin(message.from_user.id):
        return
    active_mutes = [uid for uid in storage.muted_users.keys() if storage.is_muted(int(uid))]
    if not active_mutes:
        await message.answer("ℹ️ Пользователей с активным мутом нет.")
        return

    text = "🔇 <b>Пользователи с мутом:</b>\n"
    for uid in active_mutes[:15]:
        uname = storage.user_names_data.get(uid, "Без имени")
        info = storage.get_mute_info(int(uid))
        text += f"• <code>{uid}</code> ({uname}) — {info}\n"
    text += "\nВведите ID или @username для снятия мута:"

    await state.set_state(AdminStates.waiting_for_unmute)
    await message.answer(text, reply_markup=get_cancel_keyboard())


@router.message(AdminStates.waiting_for_unmute)
async def process_unmute_step(message: Message, state: FSMContext) -> None:
    if message.text == "Отмена":
        await state.clear()
        await message.answer("Снятие мута отменено.", reply_markup=get_admin_keyboard(storage.is_super_admin(message.from_user.id)))
        return

    success, reply = storage.unmute_user(message.text.strip())
    if success:
        storage.log_admin_action(message.from_user.id, "размут", f"Цель: {message.text.strip()}")

    await state.clear()
    await message.answer(reply, reply_markup=get_admin_keyboard(storage.is_super_admin(message.from_user.id)))


# --- Просмотр и ответ на сообщения ---

@router.message(F.text == "📨 Просмотреть сообщения")
async def view_messages(message: Message) -> None:
    if not storage.is_admin(message.from_user.id):
        return
    messages = storage.load_messages()
    if not messages:
        await message.answer("📭 Сообщений от пользователей пока нет.")
        return

    text = "📨 <b>Последние сообщения:</b>\n\n"
    for msg in messages[-8:]:
        status = "✅ Отвечено" if msg.get("replied") else "❓ Не отвечено"
        msg_type = "📸 Фото" if msg["type"] == "photo" else "🎤 Голос" if msg["type"] == "voice" else "🎥 Кружок" if msg["type"] == "video_note" else "💬 Текст"
        preview = (msg["message"][:80] + "..") if len(msg["message"]) > 80 else msg["message"]
        display_name = storage.user_names_data.get(str(msg["user_id"]), msg.get("username", "Пользователь"))

        text += (
            f"🔸 <b>#{msg['id']}</b> ({status})\n"
            f"👤 {display_name} (ID: <code>{msg['user_id']}</code>)\n"
            f"🕒 {msg['timestamp']}\n"
            f"Тип: {msg_type}\n"
            f"💬 {preview}\n"
            "─────────────\n"
        )
    await message.answer(text)


@router.message(F.text == "📨 Ответить пользователю")
async def choose_user_to_reply(message: Message, state: FSMContext) -> None:
    if not storage.is_admin(message.from_user.id):
        return

    messages = storage.load_messages()
    if not messages:
        await message.answer("📭 Сообщений от пользователей пока нет.")
        return

    user_map = {}
    for m in messages:
        uid = m["user_id"]
        if uid not in user_map:
            user_map[uid] = {
                "unanswered": not m.get("replied", False),
                "last_time": m.get("timestamp", ""),
                "username": storage.user_names_data.get(str(uid), m.get("username", "Без имени")),
            }

    sorted_users = sorted(user_map.items(), key=lambda x: (not x[1]["unanswered"], x[1]["last_time"]), reverse=True)

    text = "👥 <b>Пользователи, писавшие в бот:</b>\n\n"
    for uid, info in sorted_users[:12]:
        mark = "❓" if info["unanswered"] else "✅"
        text += f"{mark} <code>{uid}</code> — {info['username']}\n"
    text += "\nВведите ID пользователя для ответа. Или нажмите 'Отмена':"

    await state.set_state(AdminStates.waiting_for_reply_choice)
    await message.answer(text, reply_markup=get_cancel_keyboard())


@router.message(AdminStates.waiting_for_reply_choice)
async def process_reply_choice_step(message: Message, state: FSMContext) -> None:
    if message.text == "Отмена":
        await state.clear()
        await message.answer("Отмена выбора.", reply_markup=get_admin_keyboard(storage.is_super_admin(message.from_user.id)))
        return

    raw = message.text.strip()
    if not raw.isdigit():
        await message.answer("❌ ID должен быть числом. Попробуйте еще раз или нажмите 'Отмена'.")
        return

    target_id = int(raw)
    display_name = storage.user_names_data.get(str(target_id), f"ID {target_id}")

    await state.set_state(AdminReplyStates.waiting_for_reply)
    await state.update_data(target_user_id=target_id, ticket_id=None)

    await message.answer(
        f"💬 <b>Ответ пользователю {display_name}</b> (<code>{target_id}</code>)\n\n"
        "Отправьте текст ответа, фото, голос или кружок:",
        reply_markup=get_admin_reply_cancel_inline(),
    )


# --- Управление администраторами (только суперадмин) ---

@router.message(F.text == "➕ Добавить админа")
async def start_add_admin(message: Message, state: FSMContext) -> None:
    if not storage.is_super_admin(message.from_user.id):
        await message.answer("❌ Только главный администратор может изменять список админов.")
        return
    await state.set_state(AdminStates.waiting_for_add_admin)
    await message.answer("🆔 Отправьте числовой ID пользователя для назначения администратором:", reply_markup=get_cancel_keyboard())


@router.message(AdminStates.waiting_for_add_admin)
async def process_add_admin(message: Message, state: FSMContext) -> None:
    if message.text == "Отмена":
        await state.clear()
        await message.answer("Добавление отменено.", reply_markup=get_admin_keyboard(True))
        return

    raw = message.text.strip()
    if not raw.isdigit():
        await message.answer("❌ ID должен содержать только цифры. Попробуйте снова или нажмите 'Отмена'.")
        return

    new_admin_id = raw
    if new_admin_id in storage.admin_ids:
        await message.answer(f"Пользователь <code>{new_admin_id}</code> уже является администратором.")
    else:
        storage.admin_ids.add(new_admin_id)
        storage.save_admins()
        storage.log_admin_action(message.from_user.id, "добавление админа", f"ID: {new_admin_id}")
        await message.answer(f"✅ Пользователь <code>{new_admin_id}</code> назначен администратором.")
        try:
            await message.bot.send_message(
                int(new_admin_id),
                "🎉 Вы назначены администратором бота! Теперь вам доступна /admin панель.",
            )
        except Exception:
            pass

    await state.clear()


@router.message(F.text == "➖ Удалить админа")
async def start_remove_admin(message: Message, state: FSMContext) -> None:
    if not storage.is_super_admin(message.from_user.id):
        await message.answer("❌ Только главный администратор может изменять список админов.")
        return

    other_admins = [a for a in storage.admin_ids if not storage.is_super_admin(a)]
    if not other_admins:
        await message.answer("ℹ️ Других администраторов нет.")
        return

    text = "👥 <b>Список администраторов:</b>\n" + "\n".join(f"• <code>{a}</code>" for a in other_admins)
    text += "\n\nВведите ID администратора для удаления:"

    await state.set_state(AdminStates.waiting_for_remove_admin)
    await message.answer(text, reply_markup=get_cancel_keyboard())


@router.message(AdminStates.waiting_for_remove_admin)
async def process_remove_admin(message: Message, state: FSMContext) -> None:
    if message.text == "Отмена":
        await state.clear()
        await message.answer("Удаление отменено.", reply_markup=get_admin_keyboard(True))
        return

    target_id = message.text.strip()
    if storage.is_super_admin(target_id):
        await message.answer("❌ Нельзя удалить главного администратора.")
        return

    if target_id in storage.admin_ids:
        storage.admin_ids.remove(target_id)
        storage.save_admins()
        storage.log_admin_action(message.from_user.id, "удаление админа", f"ID: {target_id}")
        await message.answer(f"✅ Администратор <code>{target_id}</code> удален.")
        try:
            await message.bot.send_message(int(target_id), "🚫 Ваши права администратора были сняты.")
        except Exception:
            pass
    else:
        await message.answer("❌ Администратор с таким ID не найден.")

    await state.clear()


# --- Файлы данных ---

@router.message(F.text.in_({"📁 Файлы данных", "📁 Файлы"}))
async def show_files_menu(message: Message) -> None:
    if not storage.is_admin(message.from_user.id):
        return
    await message.answer("📁 Выберите файл данных для скачивания:", reply_markup=get_files_keyboard())


FILE_DISPATCH = {
    "📄 audit_log.json": AUDIT_FILE,
    "👥 user_names.json": USER_NAMES_FILE,
    "📋 admins.json": ADMIN_FILE,
    "👥 users.txt": USERS_FILE,
    "🚫 banned_users.json": BANNED_USERS_FILE,
    "🔇 muted_users.json": MUTED_USERS_FILE,
}


@router.message(F.text.in_(FILE_DISPATCH.keys()))
async def download_data_file(message: Message) -> None:
    if not storage.is_admin(message.from_user.id):
        return

    filepath = FILE_DISPATCH[message.text]
    if os.path.exists(filepath):
        try:
            await message.answer_document(FSInputFile(filepath))
            storage.log_admin_action(message.from_user.id, "скачать файл", os.path.basename(filepath))
        except Exception as e:
            await message.answer(f"❌ Ошибка отправки файла: {e}")
    else:
        await message.answer(f"Файл {os.path.basename(filepath)} еще не создан на сервере.")
