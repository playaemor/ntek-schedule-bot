import os
import json
import time
import asyncio
import threading
from typing import Dict, Any, Tuple
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command

from bot.config import CASINO_FILE

router = Router(name="secret_casino_router")

# Блокировка для потокобезопасного сохранения
casino_lock = threading.Lock()
active_games: Dict[int, bool] = {}  # Защита от спама кнопками во время анимации броска

STARTING_BALANCE = 100
DEFAULT_BET = 25
LOAN_AMOUNT = 100
LOAN_COOLDOWN = 15 * 60  # 15 минут


def load_casino_data() -> Dict[str, Any]:
    if not os.path.exists(CASINO_FILE):
        return {}
    try:
        with open(CASINO_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"Ошибка загрузки casino.json: {e}")
        return {}


def save_casino_data(data: Dict[str, Any]) -> None:
    temp_file = f"{CASINO_FILE}.tmp"
    with casino_lock:
        try:
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(temp_file, CASINO_FILE)
        except Exception as e:
            if os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                except Exception:
                    pass
            print(f"Ошибка сохранения casino.json: {e}")


def get_user_profile(user_id: int) -> Dict[str, Any]:
    data = load_casino_data()
    uid = str(user_id)
    if uid not in data:
        data[uid] = {
            "balance": STARTING_BALANCE,
            "last_loan_time": 0.0,
            "games_played": 0,
            "wins": 0,
            "jackpots": 0,
        }
        save_casino_data(data)
    return data[uid]


def update_user_profile(user_id: int, **kwargs) -> Dict[str, Any]:
    data = load_casino_data()
    uid = str(user_id)
    if uid not in data:
        data[uid] = {
            "balance": STARTING_BALANCE,
            "last_loan_time": 0.0,
            "games_played": 0,
            "wins": 0,
            "jackpots": 0,
        }
    data[uid].update(kwargs)
    save_casino_data(data)
    return data[uid]


def get_casino_keyboard(balance: int) -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton(text=f"🎰 Слоты ({DEFAULT_BET} к.)", callback_data=f"cas:spin:{DEFAULT_BET}"),
            InlineKeyboardButton(text="🎰 ALL-IN (Вся стипуха)", callback_data="cas:spin:allin"),
        ],
        [
            InlineKeyboardButton(text=f"🎲 Кости с куратором ({DEFAULT_BET} к.)", callback_data=f"cas:dice:{DEFAULT_BET}"),
            InlineKeyboardButton(text=f"🎯 Дартс в ПО-41 ({DEFAULT_BET} к.)", callback_data=f"cas:darts:{DEFAULT_BET}"),
        ],
        [
            InlineKeyboardButton(text="💸 Займ у преподавателя (+100)", callback_data="cas:loan"),
            InlineKeyboardButton(text="🔄 Обновить", callback_data="cas:refresh"),
        ],
        [
            InlineKeyboardButton(text="🕵️‍♂️ Шухер! Спрятать улики", callback_data="cas:hide"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def format_casino_menu(profile: Dict[str, Any]) -> str:
    bal = profile.get("balance", 0)
    played = profile.get("games_played", 0)
    jackpots = profile.get("jackpots", 0)
    return (
        "🎰 <b>Подпольное казино НТЭК</b>\n"
        "<i>ООО «Отмыв денег» — теневой сектор колледжа</i>\n\n"
        f"💼 Твой баланс: <b>{bal}</b> стипендиальных коинов\n"
        f"🏆 Сыграно партий: <b>{played}</b> | Джекпотов: <b>{jackpots}</b>\n\n"
        "Выбирай стол, студент, пока куратор не заметил:"
    )


# --- Команда входа ---

@router.message(Command("777casino"))
async def handle_casino_entry(message: Message) -> None:
    profile = get_user_profile(message.from_user.id)
    text = format_casino_menu(profile)
    await message.answer(text, reply_markup=get_casino_keyboard(profile["balance"]))


# --- Обработчик меню и обновления ---

@router.callback_query(F.data == "cas:refresh")
async def handle_refresh(call: CallbackQuery) -> None:
    profile = get_user_profile(call.from_user.id)
    text = format_casino_menu(profile)
    try:
        await call.message.edit_text(text, reply_markup=get_casino_keyboard(profile["balance"]))
    except Exception:
        pass
    await call.answer("Баланс обновлен!")


@router.callback_query(F.data == "cas:menu")
async def handle_back_to_menu(call: CallbackQuery) -> None:
    profile = get_user_profile(call.from_user.id)
    text = format_casino_menu(profile)
    try:
        await call.message.edit_text(text, reply_markup=get_casino_keyboard(profile["balance"]))
    except Exception:
        await call.message.answer(text, reply_markup=get_casino_keyboard(profile["balance"]))
    await call.answer()


@router.callback_query(F.data == "cas:hide")
async def handle_hide_evidence(call: CallbackQuery) -> None:
    try:
        await call.message.delete()
    except Exception:
        pass
    await call.answer("Куратор прошел мимо. Улики уничтожены!", show_alert=True)


# --- Микрозайм у куратора ---

@router.callback_query(F.data == "cas:loan")
async def handle_loan(call: CallbackQuery) -> None:
    uid = call.from_user.id
    profile = get_user_profile(uid)
    bal = profile.get("balance", 0)
    now = time.time()
    last_loan = profile.get("last_loan_time", 0.0)

    if bal >= 50:
        await call.answer(
            f"Преподаватель говорит: 'У тебя ещё есть {bal} коинов! Иди на пары, никакого займа!'",
            show_alert=True,
        )
        return

    elapsed = now - last_loan
    if elapsed < LOAN_COOLDOWN:
        remaining_min = int((LOAN_COOLDOWN - elapsed) // 60) + 1
        await call.answer(
            f"Преподаватель ещё отходит от прошлой пары. Зайди через {remaining_min} мин.",
            show_alert=True,
        )
        return

    new_bal = bal + LOAN_AMOUNT
    update_user_profile(uid, balance=new_bal, last_loan_time=now)
    await call.answer(
        f"🤝 Преподаватель вздохнул и выдал тебе {LOAN_AMOUNT} коинов под честное слово сдать сессию!",
        show_alert=True,
    )
    profile = get_user_profile(uid)
    try:
        await call.message.edit_text(format_casino_menu(profile), reply_markup=get_casino_keyboard(new_bal))
    except Exception:
        pass


# --- Игра 1: Слоты (🎰) ---

@router.callback_query(F.data.startswith("cas:spin:"))
async def handle_slots(call: CallbackQuery) -> None:
    uid = call.from_user.id
    if active_games.get(uid):
        await call.answer("Подожди, барабан ещё крутится!", show_alert=False)
        return

    profile = get_user_profile(uid)
    bal = profile.get("balance", 0)
    bet_type = call.data.split(":")[2]

    bet = bal if bet_type == "allin" else DEFAULT_BET
    if bet <= 0:
        await call.answer("Ты на мели! Возьми займ у преподавателя.", show_alert=True)
        return
    if bal < bet:
        await call.answer(f"Недостаточно коинов (нужно {bet}, а у тебя {bal})! Возьми займ у преподавателя.", show_alert=True)
        return

    active_games[uid] = True
    await call.answer()

    # Списываем ставку
    bal -= bet
    profile["balance"] = bal
    profile["games_played"] = profile.get("games_played", 0) + 1
    update_user_profile(uid, balance=bal, games_played=profile["games_played"])

    bot = call.bot
    chat_id = call.message.chat.id

    spin_msg = await bot.send_dice(chat_id=chat_id, emoji="🎰")
    dice_value = spin_msg.dice.value

    # Ждём анимацию слотов Telegram (около 2.3 сек)
    await asyncio.sleep(2.3)

    # Декодирование слота Telegram:
    # value от 1 до 64
    v = dice_value - 1
    r1 = v % 4
    r2 = (v // 4) % 4
    r3 = (v // 16)
    symbols = ["BAR", "🍇", "🍋", "7️⃣"]

    win = 0
    multiplier = 0
    title = ""

    if dice_value == 64:  # 7-7-7 (JACKPOT)
        multiplier = 10
        win = bet * multiplier
        title = "💥 <b>ДЖЕКПОТ! ТРИ СЕМЁРКИ! (777)</b>\nЗавуч в слезах, вся стипендия твоя!"
        profile["jackpots"] = profile.get("jackpots", 0) + 1
        profile["wins"] = profile.get("wins", 0) + 1
    elif dice_value == 43:  # 🍋-🍋-🍋
        multiplier = 5
        win = bet * multiplier
        title = "🍋 <b>Три лимона!</b>\nВитаминный заряд стипухи!"
        profile["wins"] = profile.get("wins", 0) + 1
    elif dice_value == 22:  # 🍇-🍇-🍇
        multiplier = 4
        win = bet * multiplier
        title = "🍇 <b>Три винограда!</b>\nКрасиво жить не запретишь!"
        profile["wins"] = profile.get("wins", 0) + 1
    elif dice_value == 1:  # BAR-BAR-BAR
        multiplier = 3
        win = bet * multiplier
        title = "🍫 <b>Три BAR!</b>\nШоколадная жизнь студента!"
        profile["wins"] = profile.get("wins", 0) + 1
    elif [r1, r2, r3].count(3) == 2:  # Две семёрки
        multiplier = 2
        win = bet * multiplier
        title = "✨ <b>Две семёрки!</b>\nПочти сорвал куш!"
        profile["wins"] = profile.get("wins", 0) + 1
    elif r1 == r2 or r2 == r3 or r1 == r3:  # Любые два одинаковых
        multiplier = 1
        win = bet
        title = "😐 <b>Два совпадения.</b>\nВышли в ноль, ставка вернулась."
    else:
        title = "💀 <b>Мимо кассы!</b>\nКоины ушли на ремонт актового зала."

    bal += win
    profile["balance"] = bal
    update_user_profile(
        uid,
        balance=bal,
        wins=profile.get("wins", 0),
        jackpots=profile.get("jackpots", 0),
    )
    active_games[uid] = False

    res_text = (
        f"{title}\n\n"
        f"Ставка: <b>{bet}</b> к. | Результат: <b>{'+' + str(win) if win > 0 else '0'}</b> к.\n"
        f"💼 Твой баланс: <b>{bal}</b> коинов"
    )

    action_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🎰 Крутить ещё (25)", callback_data=f"cas:spin:{DEFAULT_BET}"),
                InlineKeyboardButton(text="🎰 ALL-IN", callback_data="cas:spin:allin"),
            ],
            [
                InlineKeyboardButton(text="🔙 В казино", callback_data="cas:menu"),
            ],
        ]
    )
    await bot.send_message(chat_id=chat_id, text=res_text, reply_markup=action_kb)


# --- Игра 2: Кости против завхоза (🎲) ---

@router.callback_query(F.data.startswith("cas:dice:"))
async def handle_dice(call: CallbackQuery) -> None:
    uid = call.from_user.id
    if active_games.get(uid):
        await call.answer("Игра ещё идёт!", show_alert=False)
        return

    profile = get_user_profile(uid)
    bal = profile.get("balance", 0)
    bet = DEFAULT_BET

    if bal < bet:
        await call.answer("Недостаточно коинов на ставку! Возьми займ у преподавателя.", show_alert=True)
        return

    active_games[uid] = True
    await call.answer()

    bal -= bet
    profile["balance"] = bal
    profile["games_played"] = profile.get("games_played", 0) + 1
    update_user_profile(uid, balance=bal, games_played=profile["games_played"])

    bot = call.bot
    chat_id = call.message.chat.id

    await bot.send_message(chat_id=chat_id, text="👩‍🏫 <b>Куратор</b> бросает кость...")
    dice_bot = await bot.send_dice(chat_id=chat_id, emoji="🎲")
    bot_val = dice_bot.dice.value

    await asyncio.sleep(2.5)

    await bot.send_message(chat_id=chat_id, text="🎓 <b>Твой бросок!</b>")
    dice_user = await bot.send_dice(chat_id=chat_id, emoji="🎲")
    user_val = dice_user.dice.value

    await asyncio.sleep(2.5)

    win = 0
    if user_val > bot_val:
        win = bet * 2
        title = f"🎉 <b>Ты обыграл куратора!</b> ({user_val} против {bot_val})\nКуратор в шоке обещает автомат за семестр."
        profile["wins"] = profile.get("wins", 0) + 1
    elif user_val == bot_val:
        win = bet
        title = f"🤝 <b>Ничья!</b> ({user_val}:{bot_val})\nКуратор вернул ставку: 'На пересдаче так легко не отделаешься'."
    else:
        title = f"🤦‍♂️ <b>Куратор победил!</b> ({bot_val} против {user_val})\nКоины пошли на покупку бумаги для отчётов."

    bal += win
    profile["balance"] = bal
    update_user_profile(uid, balance=bal, wins=profile.get("wins", 0))
    active_games[uid] = False

    res_text = (
        f"{title}\n\n"
        f"Ставка: <b>{bet}</b> к. | Выигрыш: <b>{win}</b> к.\n"
        f"💼 Баланс: <b>{bal}</b> коинов"
    )

    action_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🎲 Бросить ещё (25)", callback_data=f"cas:dice:{DEFAULT_BET}"),
                InlineKeyboardButton(text="🔙 В казино", callback_data="cas:menu"),
            ],
        ]
    )
    await bot.send_message(chat_id=chat_id, text=res_text, reply_markup=action_kb)


# --- Игра 3: Дартс в ПО-41 (🎯) ---

@router.callback_query(F.data.startswith("cas:darts:"))
async def handle_darts(call: CallbackQuery) -> None:
    uid = call.from_user.id
    if active_games.get(uid):
        await call.answer("Дротик ещё летит!", show_alert=False)
        return

    profile = get_user_profile(uid)
    bal = profile.get("balance", 0)
    bet = DEFAULT_BET

    if bal < bet:
        await call.answer("Недостаточно коинов на ставку! Возьми займ у преподавателя.", show_alert=True)
        return

    active_games[uid] = True
    await call.answer()

    bal -= bet
    profile["balance"] = bal
    profile["games_played"] = profile.get("games_played", 0) + 1
    update_user_profile(uid, balance=bal, games_played=profile["games_played"])

    bot = call.bot
    chat_id = call.message.chat.id

    await bot.send_message(chat_id=chat_id, text="🎯 <i>Прицеливаешься в расписание группы ПО-41...</i>")
    dart_msg = await bot.send_dice(chat_id=chat_id, emoji="🎯")
    val = dart_msg.dice.value

    await asyncio.sleep(2.5)

    win = 0
    if val == 6:  # Bullseye (центр)
        win = bet * 3
        title = "🎯 <b>В ЯБЛОЧКО!</b>\nПрямо в центр! Группа ПО-41 аплодирует стоя!"
        profile["wins"] = profile.get("wins", 0) + 1
    elif val == 5:  # Красное внутреннее кольцо
        win = int(bet * 1.5)
        title = "🎯 <b>Красный сектор!</b>\nОтличный бросок, куш x1.5!"
        profile["wins"] = profile.get("wins", 0) + 1
    elif val in [2, 3, 4]:  # Доска
        win = bet
        title = "🎯 <b>Попал в доску.</b>\nЗацепил край мишени, возврат ставки."
    else:  # Мимо
        title = "💨 <b>Дротик улетел в форточку!</b>\nПроигрыш."

    bal += win
    profile["balance"] = bal
    update_user_profile(uid, balance=bal, wins=profile.get("wins", 0))
    active_games[uid] = False

    res_text = (
        f"{title}\n\n"
        f"Ставка: <b>{bet}</b> к. | Выигрыш: <b>{win}</b> к.\n"
        f"💼 Баланс: <b>{bal}</b> коинов"
    )

    action_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🎯 Бросить ещё (25)", callback_data=f"cas:darts:{DEFAULT_BET}"),
                InlineKeyboardButton(text="🔙 В казино", callback_data="cas:menu"),
            ],
        ]
    )
    await bot.send_message(chat_id=chat_id, text=res_text, reply_markup=action_kb)
