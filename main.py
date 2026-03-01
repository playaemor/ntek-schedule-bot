import telebot
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
import re
import time
import threading
import os
from datetime import datetime
import hashlib
import json
from telebot.handler_backends import CancelUpdate
from telebot import apihelper
apihelper.ENABLE_MIDDLEWARE = True
try:
    from config import (
        BOT_TOKEN, SUPER_ADMIN_ID, INITIAL_ADMIN_IDS,
        NTЕK_SCHEDULE_URL, CHECK_INTERVAL, MESSAGE_COOLDOWN,
        DATA_FOLDER, ENABLE_AUDIT_LOG, BROADCAST_COOLDOWN
    )
except ImportError:
    print("❌ Ошибка: Файл config.py не найден!")
    print("Создайте файл config.py на основе config.example.py и заполните настройки")
    exit(1)

if BOT_TOKEN == "ВАШ_ТОКЕН_БОТА" or not BOT_TOKEN:
    print("❌ Ошибка: BOT_TOKEN не заполнен в config.py")
    exit(1)

if SUPER_ADMIN_ID == "ВАШ_TELEGRAM_ID" or not SUPER_ADMIN_ID:
    print("❌ Ошибка: SUPER_ADMIN_ID не заполнен в config.py")
    exit(1)

bot = telebot.TeleBot(BOT_TOKEN)

if not os.path.exists(DATA_FOLDER):
    os.makedirs(DATA_FOLDER)

ADMIN_IDS = {SUPER_ADMIN_ID}.union(set(map(str, INITIAL_ADMIN_IDS)))
ADMIN_FILE = os.path.join(DATA_FOLDER, "admins.json")
BANNED_USERS_FILE = os.path.join(DATA_FOLDER, "banned_users.json")
banned_users = set()

ntek_url = NTЕK_SCHEDULE_URL

USER_NAMES_FILE = os.path.join(DATA_FOLDER, "user_names.json")
schedule_file = os.path.join(DATA_FOLDER, "last_schedule.jpg")
teachers_schedule_file = os.path.join(DATA_FOLDER, "last_schedule_teachers.jpg")
bells_schedule_file = os.path.join(DATA_FOLDER, "bells_schedule.jpg")
student_schedule_file = os.path.join(DATA_FOLDER, "student_schedule.jpg")
messages_file = os.path.join(DATA_FOLDER, "user_messages.json")
last_message_time_file = os.path.join(DATA_FOLDER, "last_message_time.json")
AUDIT_FILE = os.path.join(DATA_FOLDER, "audit_log.json")

user_ids = set()
user_names_data = {}
last_schedule_hash = None
last_teachers_schedule_hash = None
last_student_schedule_hash = None
is_first_check = True
last_broadcast_time = 0
broadcast_lock = threading.Lock()
broadcast_in_progress = False

schedule_file_ids = {
    "учащихся": None,
    "преподавателей": None,
    "звонков": None,
    "от учащихся": None
}

user_states = {}
ADMIN_CHAT_MODE = "admin_chat"
admin_reply_states = {}
audit_log = []

headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    'Accept-Language': 'ru-RU,ru;q=0.8,en-US;q=0.5,en;q=0.3',
    'Connection': 'keep-alive',
}

admin_chat_keyboard = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
admin_chat_keyboard.row('❌ Завершить общение')

cancel_keyboard = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
cancel_keyboard.row('Отмена')

admin_keyboard = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
admin_keyboard.row('📊 Статистика', '📢 Рассылка')
admin_keyboard.row('🔄 Обновить расписание звонков', '🔄 Обновить расписание от учащихся')
admin_keyboard.row('📨 Просмотреть сообщения', '📨 Ответить пользователю')
admin_keyboard.row('➕ Добавить админа', '➖ Удалить админа')
admin_keyboard.row('📊 Аудит', '📁 Файлы')
admin_keyboard.row('🔙 Главное меню')

admin_reply_states = {}

AUDIT_FILE = "audit_log.json"
audit_log = []


def is_admin(user_id):
    return str(user_id) in ADMIN_IDS

def load_banned_users():
    global banned_users
    try:
        if os.path.exists(BANNED_USERS_FILE):
            with open(BANNED_USERS_FILE, 'r', encoding='utf-8') as f:
                banned_users = set(json.load(f))
    except Exception as e:
        print(f"Ошибка загрузки забаненных пользователей: {e}")

def save_banned_users():
    try:
        with open(BANNED_USERS_FILE, 'w', encoding='utf-8') as f:
            json.dump(list(banned_users), f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Ошибка сохранения забаненных пользователей: {e}")

def ban_user(identifier):
    """
    Блокирует пользователя по ID или @username.
    Возвращает кортеж (успех: bool, сообщение: str)
    """
    identifier = str(identifier).strip()
    target_id = None

    # Если передан ID (состоит только из цифр)
    if identifier.isdigit():
        target_id = int(identifier)
    # Если передан @username
    elif identifier.startswith('@'):
        # Ищем пользователя в вашем словаре user_names_data
        for uid_str, uname in user_names_data.items():
            if uname.lower() == identifier.lower():
                target_id = int(uid_str)
                break
                
        if not target_id:
            return False, f"❌ Пользователь {identifier} не найден в базе бота."
    else:
        return False, "❌ Неверный формат. Укажите ID (число) или @username."

    # Защита от бана администраторов
    if is_admin(target_id):
        return False, "❌ Нельзя забанить администратора."

    # Добавляем в бан-лист и сохраняем
    banned_users.add(target_id)
    save_banned_users()
    
    # Если пользователь был в режиме чата с админом, сбрасываем его
    clear_user_state(target_id)
    
    return True, f"✅ Пользователь {identifier} (ID: {target_id}) успешно заблокирован."

# Команда для вызова функции админом (например: /ban @username)


def get_main_keyboard(user_id):
    keyboard = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.row('📅 Расписание с сайта', '👨‍🏫 Расписание для преподавателей')
    keyboard.row('🔔 Расписание звонков', '📝 Расписание от учащихся')
    keyboard.row('📩 Написать админу', '🌟 Поддержать проект')
    if is_admin(user_id):
        keyboard.row('ℹ️ Помощь', '👨‍💻 Админ-панель')
    else:
        keyboard.row('ℹ️ Помощь')
    return keyboard


def set_user_state(user_id, state):
    user_states[user_id] = state


def get_user_state(user_id):
    return user_states.get(user_id)


def clear_user_state(user_id):
    if user_id in user_states:
        del user_states[user_id]


def handle_cancellation(message, message_text):
    if message.text == 'Отмена':
        if message.chat.id in admin_reply_states:
            del admin_reply_states[message.chat.id]
        reply_markup = admin_keyboard if is_admin(message.chat.id) else get_main_keyboard(message.chat.id)
        bot.send_message(message.chat.id, message_text, reply_markup=reply_markup)
        return True
    return False


def save_admins():
    try:
        with open(ADMIN_FILE, 'w', encoding='utf-8') as f:
            json.dump(list(ADMIN_IDS), f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Ошибка сохранения администраторов: {e}")


def load_admins():
    global ADMIN_IDS
    try:
        if os.path.exists(ADMIN_FILE):
            with open(ADMIN_FILE, 'r', encoding='utf-8') as f:
                loaded_ids = set(json.load(f))
                # Гарантируем, что суперадмин всегда в списке
                loaded_ids.add(SUPER_ADMIN_ID)
                ADMIN_IDS = loaded_ids
        else:
            # Если файла нет, используем начальных админов из config.py
            ADMIN_IDS = {SUPER_ADMIN_ID}.union(set(map(str, INITIAL_ADMIN_IDS)))
            save_admins()
    except Exception as e:
        print(f"Ошибка загрузки администраторов: {e}")


def save_schedule_file_ids():
    try:
        with open('schedule_file_ids.json', 'w', encoding='utf-8') as f:
            json.dump(schedule_file_ids, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Ошибка сохранения file_id расписаний: {e}")


def load_schedule_file_ids():
    global schedule_file_ids
    try:
        if os.path.exists('schedule_file_ids.json'):
            with open('schedule_file_ids.json', 'r', encoding='utf-8') as f:
                loaded_ids = json.load(f)
                schedule_file_ids.update(loaded_ids)
    except Exception as e:
        print(f"Ошибка загрузки file_id расписаний: {e}")


def save_user_names():
    try:
        with open(USER_NAMES_FILE, 'w', encoding='utf-8') as f:
            json.dump(user_names_data, f, ensure_ascii=False, indent=4)
    except Exception as e:
        print(f"Ошибка сохранения юзернеймов: {e}")


def load_user_names():
    global user_names_data
    try:
        if os.path.exists(USER_NAMES_FILE):
            with open(USER_NAMES_FILE, 'r', encoding='utf-8') as f:
                user_names_data = json.load(f)
                user_names_data = {str(k): v for k, v in user_names_data.items()}
    except Exception as e:
        print(f"Ошибка загрузки юзернеймов: {e}")


def update_user_name_info(user):
    user_id_str = str(user.id)
    current_data = user_names_data.get(user_id_str)
    if user.username:
        new_data = f"@{user.username}"
    else:
        full_name = f"{user.first_name or ''} {user.last_name or ''}".strip()
        new_data = full_name if full_name else "Без username"
    if current_data != new_data:
        user_names_data[user_id_str] = new_data
        save_user_names()


def load_last_message_times():
    if not os.path.exists(last_message_time_file):
        return {}
    try:
        with open(last_message_time_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"Ошибка загрузки времени сообщений: {e}")
        return {}


def save_last_message_times(data):
    try:
        with open(last_message_time_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Ошибка сохранения времени сообщений: {e}")


def can_send_message(user_id):
    if get_user_state(user_id) == ADMIN_CHAT_MODE:
        return True
    last_message_times = load_last_message_times()
    user_id_str = str(user_id)
    if user_id_str not in last_message_times:
        return True
    return (time.time() - last_message_times[user_id_str]) >= MESSAGE_COOLDOWN


def get_cooldown_remaining(user_id):
    last_message_times = load_last_message_times()
    user_id_str = str(user_id)
    if user_id_str not in last_message_times:
        return 0
    elapsed = time.time() - last_message_times[user_id_str]
    return max(0, MESSAGE_COOLDOWN - elapsed)


def update_last_message_time(user_id):
    last_message_times = load_last_message_times()
    last_message_times[str(user_id)] = time.time()
    save_last_message_times(last_message_times)


def calculate_file_hash(filename):
    if not os.path.exists(filename):
        return None
    sha256_hash = hashlib.sha256()
    with open(filename, "rb") as f:
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)
    return sha256_hash.hexdigest()


def load_messages():
    if not os.path.exists(messages_file):
        return []
    try:
        with open(messages_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"Ошибка загрузки сообщений: {e}")
        return []


def save_message(user_id, username, message_text, message_type="text", file_id=None):
    try:
        messages = load_messages()
        new_message = {
            'id': len(messages) + 1,
            'user_id': user_id, 'username': username, 'message': message_text,
            'type': message_type, 'file_id': file_id,
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'), 'replied': False
        }
        messages.append(new_message)
        with open(messages_file, 'w', encoding='utf-8') as f:
            json.dump(messages, f, ensure_ascii=False, indent=2)
        return new_message['id']
    except Exception as e:
        print(f"Ошибка сохранения сообщения: {e}")
        return False


def mark_message_as_replied(message_id):
    try:
        messages = load_messages()
        for message in messages:
            if message['id'] == message_id:
                message['replied'] = True
                break
        with open(messages_file, 'w', encoding='utf-8') as f:
            json.dump(messages, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f"Ошибка отметки сообщения: {e}")
        return False


def check_schedule_updates():
    global last_schedule_hash, last_teachers_schedule_hash, is_first_check
    try:
        response = requests.get(ntek_url, headers=headers, timeout=30)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        schedule_link = soup.find('a', string=re.compile(r'Расписание для учащихся', re.IGNORECASE))
        teachers_schedule_link = soup.find('a', string=re.compile(r'Расписание для преподавателей', re.IGNORECASE))
        updates = []
        if schedule_link and schedule_link.get('href'):
            url = urljoin(ntek_url, schedule_link['href'])
            if download_and_check_update(url, "temp_schedule.jpg", schedule_file, last_schedule_hash, "учащихся"):
                last_schedule_hash = calculate_file_hash(schedule_file)
                schedule_file_ids["учащихся"] = None
                save_schedule_file_ids()
                if not is_first_check: updates.append("учащихся (с сайта)")
        if teachers_schedule_link and teachers_schedule_link.get('href'):
            url = urljoin(ntek_url, teachers_schedule_link['href'])
            if download_and_check_update(url, "temp_teachers.jpg", teachers_schedule_file, last_teachers_schedule_hash,
                                         "преподавателей"):
                last_teachers_schedule_hash = calculate_file_hash(teachers_schedule_file)
                schedule_file_ids["преподавателей"] = None
                save_schedule_file_ids()
                if not is_first_check: updates.append("преподавателей")
        if updates and not is_first_check:
            send_update_notification(updates)
        return bool(updates)
    except Exception as e:
        print(f"Ошибка при проверке расписания: {e}")
        return False


def download_and_check_update(url, temp_file, target_file, last_hash, schedule_type):
    try:
        img_response = requests.get(url, headers=headers, timeout=30)
        img_response.raise_for_status()
        with open(temp_file, 'wb') as f:
            f.write(img_response.content)
        current_hash = calculate_file_hash(temp_file)
        if current_hash != last_hash:
            if os.path.exists(target_file): os.remove(target_file)
            os.rename(temp_file, target_file)
            return True
        else:
            os.remove(temp_file)
            return False
    except Exception as e:
        print(f"Ошибка при обработке расписания {schedule_type}: {e}")
        if os.path.exists(temp_file): os.remove(temp_file)
        return False


def send_update_notification(updates):
    if not user_ids: return
    update_text = "🔄 Обновлено расписание: " + ", ".join(updates)
    success_count = 0
    for user_id in list(user_ids):
        try:
            bot.send_message(user_id, update_text)
            success_count += 1
        except Exception as e:
            print(f"Ошибка отправки уведомления пользователю {user_id}: {e}")
    print(f"Уведомления отправлены: {success_count}/{len(user_ids)}")


def send_schedule_to_user(user_id, schedule_type):
    try:
        file_map = {
            "учащихся": (schedule_file, "📅 Расписание с сайта"),
            "преподавателей": (teachers_schedule_file, "👨‍🏫 Расписание для преподавателей"),
            "звонков": (bells_schedule_file, "🔔 Расписание звонков"),
            "от учащихся": (student_schedule_file, "📝 Расписание от учащихся")
        }
        if schedule_type not in file_map:
            bot.send_message(user_id, "❌ Неизвестный тип расписания")
            return
        file_path, caption = file_map[schedule_type]
        if not os.path.exists(file_path):
            bot.send_message(user_id, "📭 Расписание еще не загружено")
            return
        file_id = schedule_file_ids.get(schedule_type)
        if file_id:
            try:
                bot.send_photo(user_id, file_id, caption=caption)
                return
            except Exception as e:
                print(f"File_id недействителен для {schedule_type}: {e}. Загружаю файл заново.")
                schedule_file_ids[schedule_type] = None
                save_schedule_file_ids()
        with open(file_path, 'rb') as photo:
            msg = bot.send_photo(user_id, photo, caption=caption)
            if msg.photo:
                schedule_file_ids[schedule_type] = msg.photo[-1].file_id
                save_schedule_file_ids()
    except Exception as e:
        print(f"Ошибка отправки расписания {schedule_type}: {e}")
        bot.send_message(user_id, "❌ Не удалось загрузить расписание")


def schedule_checker():
    global is_first_check
    if is_first_check:
        is_first_check = False
    while True:
        check_schedule_updates()
        time.sleep(CHECK_INTERVAL)


def load_audit_log():
    global audit_log
    if os.path.exists(AUDIT_FILE):
        try:
            with open(AUDIT_FILE, 'r', encoding='utf-8') as f:
                audit_log = json.load(f)
        except Exception as e:
            print(f"Ошибка загрузки аудита: {e}")
            audit_log = []
    else:
        audit_log = []


def save_audit_log():
    try:
        with open(AUDIT_FILE, 'w', encoding='utf-8') as f:
            json.dump(audit_log, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Ошибка сохранения аудита: {e}")


def log_admin_action(admin_id, action, details):
    if not ENABLE_AUDIT_LOG:
        return
    
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    admin_username = ""
    try:
        admin_username = f"@{bot.get_chat(admin_id).username}" if bot.get_chat(admin_id) and bot.get_chat(
            admin_id).username else ""
    except Exception:
        admin_username = ""
    entry = {
        "timestamp": timestamp,
        "admin_id": int(admin_id) if str(admin_id).isdigit() else admin_id,
        "admin_username": admin_username,
        "action": action,
        "details": details
    }
    audit_log.append(entry)
    save_audit_log()


def get_recent_audit_events(limit=20):
    return audit_log[-limit:] if len(audit_log) >= limit else audit_log[:]


@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    user_id = message.chat.id
    user_ids.add(user_id)
    save_users()
    update_user_name_info(message.from_user)
    welcome_text = """
    👋 Добро пожаловать! Я неофициальный бот расписания НТЭК.

    📋 Выберите нужное расписание из меню ниже:
    📅 Расписание с сайта
    👨‍🏫 Расписание для преподавателей
    🔔 Расписание звонков
    📝 Расписание от учащихся
    📩 Написать админу (можно отправлять текст и фото)
    🌟 Поддержать проект (с помощью Telegram Stars)
    ℹ️ Помощь

    ⚡ Я автоматически проверяю обновления каждые 10 минут и пришлю уведомление!
    ⏰ Ограничение: писать админу можно не чаще чем раз в минуту.
    """
    bot.send_message(user_id, welcome_text, reply_markup=get_main_keyboard(user_id))


@bot.message_handler(commands=['donate'])
def donate_command(message):
    send_donation_invoice(message.chat.id)


@bot.message_handler(func=lambda message: message.text == '🌟 Поддержать проект')
def support_project_button(message):
    send_donation_invoice(message.chat.id)


def send_donation_invoice(chat_id):
    title = "Поддержка проекта"
    description = "Ваше пожертвование поможет поддерживать и развивать бота. Спасибо!"
    payload = "donation-payload"
    provider_token = None
    currency = "XTR"
    prices = [telebot.types.LabeledPrice(label="Пожертвование", amount=20)]
    try:
        bot.send_invoice(
            chat_id, title, description, payload,
            provider_token, currency, prices,
            start_parameter="donation"
        )
    except Exception as e:
        print(f"Ошибка создания счета: {e}")
        bot.send_message(chat_id, "❌ Не удалось создать счет для пожертвования. Попробуйте позже.")


@bot.pre_checkout_query_handler(func=lambda query: True)
def pre_checkout_query(pre_checkout_q):
    bot.answer_pre_checkout_query(pre_checkout_q.id, ok=True)


@bot.message_handler(content_types=['successful_payment'])
def successful_payment(message):
    user_id = message.chat.id
    stars_amount = message.successful_payment.total_amount
    bot.send_message(user_id, f"🎉 Огромное спасибо за вашу поддержку в размере {stars_amount} 🌟!")
    for admin_id in ADMIN_IDS:
        try:
            bot.send_message(admin_id, f"💰 Получено новое пожертвование: {stars_amount} 🌟 от пользователя {user_id}")
        except Exception as e:
            print(f"Ошибка уведомления админа о пожертвовании: {e}")


@bot.message_handler(func=lambda message: message.text == '📅 Расписание с сайта')
def send_student_schedule(message):
    send_schedule_to_user(message.chat.id, "учащихся")


@bot.message_handler(func=lambda message: message.text == '👨‍🏫 Расписание для преподавателей')
def send_teacher_schedule(message):
    send_schedule_to_user(message.chat.id, "преподавателей")


@bot.message_handler(func=lambda message: message.text == '🔔 Расписание звонков')
def send_bells_schedule(message):
    send_schedule_to_user(message.chat.id, "звонков")


@bot.message_handler(func=lambda message: message.text == '📝 Расписание от учащихся')
def send_student_created_schedule(message):
    send_schedule_to_user(message.chat.id, "от учащихся")


@bot.message_handler(func=lambda message: message.text == '📩 Написать админу')
def write_to_admin(message):
    update_user_name_info(message.from_user)
    user_id = message.chat.id
    if not can_send_message(user_id):
        cooldown = get_cooldown_remaining(user_id)
        minutes, seconds = int(cooldown // 60), int(cooldown % 60)
        time_left = f"{minutes} мин {seconds} сек" if minutes > 0 else f"{seconds} сек"
        bot.send_message(user_id,
                         f"⏰ Вы можете отправлять сообщения админу не чаще чем раз в минуту.\n\nПопробуйте через {time_left}.",
                         reply_markup=get_main_keyboard(user_id))
        return
    set_user_state(user_id, ADMIN_CHAT_MODE)
    update_last_message_time(user_id)
    welcome_text = """
    💬 Режим общения с администратором

    Теперь вы можете отправлять сообщения администратору.
    Поддерживаются текстовые сообщения, фото, голосовые сообщения и видеокружки.

    ❌ Нажмите «Завершить общение», чтобы выйти из этого режима.
    """
    bot.send_message(user_id, welcome_text, reply_markup=admin_chat_keyboard)


@bot.message_handler(func=lambda message: message.text == '❌ Завершить общение')
def end_admin_chat(message):
    user_id = message.chat.id
    if get_user_state(user_id) == ADMIN_CHAT_MODE:
        clear_user_state(user_id)
        bot.send_message(user_id, "✅ Общение с администратором завершено.", reply_markup=get_main_keyboard(user_id))
    else:
        bot.send_message(user_id, "Вы не находитесь в режиме общения с администратором.",
                         reply_markup=get_main_keyboard(user_id))


def process_admin_chat_message(message):
    user_id = message.chat.id
    username = message.from_user.username or f"{message.from_user.first_name or ''} {message.from_user.last_name or ''}".strip() or "Без имени"
    message_text, message_type, file_id_val = "", "", None

    if message.text:
        message_text, message_type = message.text, "text"
    elif message.photo:
        file_id_val = message.photo[-1].file_id
        message_text, message_type = message.caption if message.caption else "Фото без описания", "photo"
    elif message.voice:
        file_id_val = message.voice.file_id
        message_text, message_type = message.caption if message.caption else "Голосовое сообщение", "voice"
    elif message.video_note:
        file_id_val = message.video_note.file_id
        message_text, message_type = "Видеокружок", "video_note"

    message_id = save_message(user_id, username, message_text, message_type, file_id_val)
    if message_id:
        bot.send_message(user_id,
                         f"✅ {'Сообщение' if message_type == 'text' else 'Фото' if message_type == 'photo' else 'Голосовое сообщение' if message_type == 'voice' else 'Видеокружок'} отправлено администратору!")
        update_user_name_info(message.from_user)
        display_name = user_names_data.get(str(user_id), f"Пользователь {user_id}")

        for admin_id in ADMIN_IDS:
            try:
                admin_notification = f"📨 {'Сообщение' if message_type == 'text' else '📸 Фото' if message_type == 'photo' else '🎤 Голосовое сообщение' if message_type == 'voice' else '🎥 Видеокружок'} от пользователя {display_name} (ID: {user_id})\n\n💬: {message_text}\n🔢 ID сообщения: {message_id}"
                bot.send_message(admin_id, admin_notification)

                if message_type == 'photo':
                    bot.send_photo(admin_id, file_id_val, caption=f"Сообщение #{message_id} от {display_name}")
                elif message_type == 'voice':
                    bot.send_voice(admin_id, file_id_val, caption=f"Сообщение #{message_id} от {display_name}")
                elif message_type == 'video_note':
                    bot.send_video_note(admin_id, file_id_val)
                    bot.send_message(admin_id, f"Сообщение #{message_id} от {display_name}")
            except Exception as e:
                print(f"Ошибка отправки уведомления админу {admin_id}: {e}")
    else:
        bot.send_message(user_id, "❌ Произошла ошибка при отправке. Попробуйте позже.")


@bot.message_handler(content_types=['photo', 'voice', 'video_note', 'sticker', 'video', 'document', 'audio'])
def handle_media_content(message):
    update_user_name_info(message.from_user)
    user_id = message.chat.id

    if get_user_state(user_id) == ADMIN_CHAT_MODE:
        if message.photo or message.voice or message.video_note:
            process_admin_chat_message(message)
        else:
            bot.send_message(user_id,
                             "❌ В режиме общения с админом поддерживаются только текст, фото, голосовые сообщения и видеокружки.")
    else:
        bot.send_message(user_id, "ℹ️ Чтобы отправить медиафайл администратору, сначала нажмите '📩 Написать админу'",
                         reply_markup=get_main_keyboard(user_id))


@bot.message_handler(commands=['admin'])
def admin_panel_command(message):
    admin_panel(message)


@bot.message_handler(func=lambda message: message.text == '👨‍💻 Админ-панель')
def admin_panel_button(message):
    admin_panel(message)


def admin_panel(message):
    if not is_admin(message.chat.id):
        bot.send_message(message.chat.id, "❌ Доступ запрещен")
        return
    bot.send_message(message.chat.id, "👨‍💻 Панель администратора", reply_markup=admin_keyboard)


def send_help(message):
    help_text = """
    ℹ️ Справка по боту:
    - 📅 Расписание с сайта: текущее расписание занятий.
    - 👨‍🏫 Расписание для преподавателей: расписание для преподавателей.
    - 🔔 Расписание звонков: время занятий и перерывов.
    - 📝 Расписание от учащихся: расписание, которое вы нам скидываете.
    - 📩 Написать админу: перейти в режим общения с администратором (поддерживаются текст, фото, голосовые, видеокружки).
    - 🌟 Поддержать проект: помочь в развитии бота с помощью Telegram Stars.

    ⏰ Бот автоматически проверяет обновления каждые 10 минут.
    """
    bot.send_message(message.chat.id, help_text)


@bot.message_handler(func=lambda message: message.text == '📊 Статистика' and is_admin(message.chat.id))
def show_stats(message):
    messages = load_messages()
    unanswered_count = len([m for m in messages if not m.get('replied', False)])
    stats_text = f"""
    📊 Статистика бота:
    - 👥 Пользователей (ID): {len(user_ids)}
    - 👥 Пользователей (Names): {len(user_names_data)}
    - 👮‍♂️ Администраторов: {len(ADMIN_IDS)}
    - 📨 Всего сообщений: {len(messages)}
    - ❓ Неотвеченных: {unanswered_count}
    - 📅 Расписание учащихся: {'✅' if os.path.exists(schedule_file) else '❌'}
    - 👨‍🏫 Расписание преподавателей: {'✅' if os.path.exists(teachers_schedule_file) else '❌'}
    - 🔔 Расписание звонков: {'✅' if os.path.exists(bells_schedule_file) else '❌'}
    - 📝 Расписание от учащихся: {'✅' if os.path.exists(student_schedule_file) else '❌'}
    """
    bot.send_message(message.chat.id, stats_text)

@bot.message_handler(commands=['ban'])
def handle_ban_command(message):
    if not is_admin(message.chat.id):
        return
    
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        bot.send_message(message.chat.id, "⚠️ Использование: /ban <ID или @username>")
        return
    
    success, reply_text = ban_user(args[1])
    bot.send_message(message.chat.id, reply_text)

@bot.middleware_handler(update_types=['message', 'callback_query'])
def block_banned_users(bot_instance, update):
    user_id = update.from_user.id
    if user_id in banned_users:
        # CancelUpdate() мгновенно прерывает обработку события для этого пользователя
        return CancelUpdate()

@bot.message_handler(func=lambda message: message.text == '📢 Рассылка' and is_admin(message.chat.id))
def request_broadcast(message):
    global broadcast_in_progress, last_broadcast_time

    # Проверяем блокировку
    with broadcast_lock:
        current_time = time.time()

        # Проверяем, не идет ли уже рассылка
        if broadcast_in_progress:
            bot.send_message(message.chat.id,
                             "⏳ Рассылка уже выполняется другим администратором. Пожалуйста, подождите.",
                             reply_markup=admin_keyboard)
            return

        # Проверяем время с последней рассылки
        if current_time - last_broadcast_time < BROADCAST_COOLDOWN:
            remaining_time = BROADCAST_COOLDOWN - int(current_time - last_broadcast_time)
            bot.send_message(message.chat.id,
                             f"⏳ Рассылку можно делать не чаще чем раз в 15 секунд. Подождите еще {remaining_time} секунд.",
                             reply_markup=admin_keyboard)
            return

        # Устанавливаем флаг "рассылка в процессе"
        broadcast_in_progress = True

    try:
        msg = bot.send_message(message.chat.id,
                               "📝 Введите текст для рассылки или отправьте фото с подписью. Или нажмите 'Отмена'.",
                               reply_markup=cancel_keyboard)
        bot.register_next_step_handler(msg, process_broadcast)
    except Exception as e:
        # Если произошла ошибка, снимаем блокировку
        with broadcast_lock:
            broadcast_in_progress = False
        raise e


def process_broadcast(message):
    global last_broadcast_time, broadcast_in_progress

    try:
        if handle_cancellation(message, "Отмена рассылки."):
            return

        # Обновляем время последней рассылки
        current_time = time.time()
        last_broadcast_time = current_time

        success_count = 0
        total_users = len(user_ids)

        # Отправляем сообщение о начале рассылки
        bot.send_message(message.chat.id, f"🚀 Начинаю рассылку для {total_users} пользователей...",
                         reply_markup=admin_keyboard)

        # Подготовка медиа для отправки
        if message.photo:
            # Рассылка фото с подписью
            photo_id = message.photo[-1].file_id
            caption = message.caption if message.caption else ""

            for user_id in list(user_ids):
                try:
                    bot.send_photo(user_id, photo_id, caption=caption)
                    success_count += 1
                except Exception as e:
                    print(f"Ошибка рассылки фото пользователю {user_id}: {e}")
        else:
            # Рассылка текста
            text = message.text

            for user_id in list(user_ids):
                try:
                    bot.send_message(user_id, text)
                    success_count += 1
                except Exception as e:
                    print(f"Ошибка рассылки пользователю {user_id}: {e}")

        bot.send_message(message.chat.id,
                         f"✅ Рассылка завершена!\n📊 Результат: {success_count}/{total_users}",
                         reply_markup=admin_keyboard)
        try:
            log_admin_action(message.chat.id, "рассылка",
                             f"Тип: {'фото' if message.photo else 'текст'}, охват: {success_count}/{total_users}")
        except Exception:
            pass

    finally:
        # В любом случае снимаем блокировку
        with broadcast_lock:
            broadcast_in_progress = False


def request_new_schedule(message, schedule_type):
    handler_map = {
        "звонков": process_bells_schedule,
        "от учащихся": process_student_schedule
    }
    msg = bot.send_message(message.chat.id,
                           f"📤 Отправьте новое расписание {schedule_type} (фото). Или нажмите 'Отмена'.",
                           reply_markup=cancel_keyboard)
    bot.register_next_step_handler(msg, handler_map[schedule_type])


def process_new_schedule(message, file_path, schedule_type):
    if not message.photo and not handle_cancellation(message, f"Отмена обновления расписания {schedule_type}."):
        msg = bot.send_message(message.chat.id, "❌ Пожалуйста, отправьте фото. Или нажмите 'Отмена'.",
                               reply_markup=cancel_keyboard)
        handler_map = {"звонков": process_bells_schedule, "от учащихся": process_student_schedule}
        bot.register_next_step_handler(msg, handler_map[schedule_type])
        return
    if message.photo:
        try:
            file_info = bot.get_file(message.photo[-1].file_id)
            downloaded_file = bot.download_file(file_info.file_path)
            with open(file_path, 'wb') as f:
                f.write(downloaded_file)
            schedule_file_ids[schedule_type] = None
            save_schedule_file_ids()
            if schedule_type == "от учащихся":
                global last_student_schedule_hash
                last_student_schedule_hash = calculate_file_hash(file_path)
            bot.send_message(message.chat.id, f"✅ Расписание '{schedule_type}' обновлено!", reply_markup=admin_keyboard)
            try:
                log_admin_action(message.chat.id, f"обновление расписания ({schedule_type})",
                                 f"Обновлён файл {file_path}")
            except Exception:
                pass
        except Exception as e:
            bot.send_message(message.chat.id, f"❌ Ошибка: {e}", reply_markup=admin_keyboard)


@bot.message_handler(func=lambda message: message.text == '🔄 Обновить расписание звонков' and is_admin(message.chat.id))
def request_bells_schedule(message):
    request_new_schedule(message, "звонков")


def process_bells_schedule(message):
    if handle_cancellation(message, "Отмена обновления расписания звонков."): return
    process_new_schedule(message, bells_schedule_file, "звонков")


@bot.message_handler(
    func=lambda message: message.text == '🔄 Обновить расписание от учащихся' and is_admin(message.chat.id))
def request_student_schedule(message):
    request_new_schedule(message, "от учащихся")


def process_student_schedule(message):
    if handle_cancellation(message, "Отмена обновления расписания от учащихся."): return
    process_new_schedule(message, student_schedule_file, "от учащихся")


@bot.message_handler(func=lambda message: message.text == '➕ Добавить админа' and is_admin(message.chat.id))
def request_add_admin(message):
    if str(message.chat.id) != SUPER_ADMIN_ID:
        bot.send_message(message.chat.id, "❌ Только главный администратор может управлять списком.")
        return
    msg = bot.send_message(message.chat.id,
                           "🆔 Отправьте ID пользователя (число) для добавления в администраторы. Или нажмите 'Отмена'.",
                           reply_markup=cancel_keyboard)
    bot.register_next_step_handler(msg, process_add_admin)


def process_add_admin(message):
    if handle_cancellation(message, "Отмена операции добавления админа."):
        return
    try:
        new_admin_id = str(message.text).strip()
        if not new_admin_id.isdigit():
            msg = bot.send_message(message.chat.id, "❌ ID должен быть числом. Попробуйте еще раз или нажмите 'Отмена'.",
                                   reply_markup=cancel_keyboard)
            bot.register_next_step_handler(msg, process_add_admin)
            return
        if new_admin_id in ADMIN_IDS:
            bot.send_message(message.chat.id, f"✅ Пользователь {new_admin_id} уже является администратором.",
                             reply_markup=admin_keyboard)
        else:
            ADMIN_IDS.add(new_admin_id)
            save_admins()
            bot.send_message(message.chat.id, f"✅ Пользователь {new_admin_id} добавлен в администраторы.",
                             reply_markup=admin_keyboard)
            try:
                bot.send_message(new_admin_id,
                                 "🎉 Вы назначены администратором бота! Теперь вам доступна панель по команде /admin.")
            except Exception:
                pass
            try:
                log_admin_action(message.chat.id, "добавление админа", f"Добавлен админ {new_admin_id}")
            except Exception:
                pass
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ Произошла ошибка при добавлении: {e}", reply_markup=admin_keyboard)


@bot.message_handler(func=lambda message: message.text == '➖ Удалить админа' and is_admin(message.chat.id))
def request_remove_admin(message):
    if str(message.chat.id) != SUPER_ADMIN_ID:
        bot.send_message(message.chat.id, "❌ Только главный администратор может управлять списком.")
        return
    admins_list = [a for a in ADMIN_IDS if a != SUPER_ADMIN_ID]
    if not admins_list:
        bot.send_message(message.chat.id, "ℹ️ Кроме вас, других администраторов нет.", reply_markup=admin_keyboard)
        return
    admin_list_text = "Выберите ID администратора для удаления:\n" + "\n".join(admins_list)
    msg = bot.send_message(message.chat.id, admin_list_text, reply_markup=cancel_keyboard)
    bot.register_next_step_handler(msg, process_remove_admin)


def process_remove_admin(message):
    if handle_cancellation(message, "Отмена операции удаления админа."):
        return
    try:
        remove_admin_id = str(message.text).strip()
        if not remove_admin_id.isdigit():
            msg = bot.send_message(message.chat.id, "❌ ID должен быть числом. Попробуйте еще раз или нажмите 'Отмена'.",
                                   reply_markup=cancel_keyboard)
            bot.register_next_step_handler(msg, process_remove_admin)
            return
        if remove_admin_id == SUPER_ADMIN_ID:
            bot.send_message(message.chat.id, "❌ Нельзя удалить главного администратора.", reply_markup=admin_keyboard)
            return
        if remove_admin_id in ADMIN_IDS:
            ADMIN_IDS.remove(remove_admin_id)
            save_admins()
            bot.send_message(message.chat.id, f"✅ Администратор {remove_admin_id} удален.", reply_markup=admin_keyboard)
            try:
                bot.send_message(remove_admin_id, "🚫 Вы были удалены из списка администраторов бота.")
            except Exception:
                pass
            try:
                log_admin_action(message.chat.id, "удаление админа", f"Удалён админ {remove_admin_id}")
            except Exception:
                pass
        else:
            msg = bot.send_message(message.chat.id,
                                   "❌ Администратор с таким ID не найден. Попробуйте еще раз или нажмите 'Отмена'.",
                                   reply_markup=cancel_keyboard)
            bot.register_next_step_handler(msg, process_remove_admin)
            return
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ Произошла ошибка при удалении: {e}", reply_markup=admin_keyboard)


@bot.message_handler(func=lambda message: message.text == '📨 Просмотреть сообщения' and is_admin(message.chat.id))
def view_user_messages(message):
    messages = load_messages()
    if not messages:
        bot.send_message(message.chat.id, "📭 Сообщений от пользователей пока нет.", reply_markup=admin_keyboard)
        return
    message_text = "📨 Последние 10 сообщений:\n\n"
    for msg in messages[-10:]:
        status = "✅ Отвечено" if msg.get('replied') else "❓ Не отвечено"
        msg_type = "📸 Фото" if msg['type'] == 'photo' else "🎤 Голосовое" if msg[
                                                                                'type'] == 'voice' else "🎥 Видеокружок" if \
        msg['type'] == 'video_note' else "💬 Текст"
        preview = (msg['message'][:100] + '..') if len(msg['message']) > 100 else msg['message']
        display_name = user_names_data.get(str(msg['user_id']), msg['username'])
        message_text += (f"🔸 ID: {msg['id']} ({status})\n"
                         f"👤 {display_name} (ID: {msg['user_id']})\n"
                         f"🕒 {msg['timestamp']}\n"
                         f"💬 {preview}\n" + "─" * 20 + "\n")
    try:
        bot.send_message(message.chat.id, message_text, reply_markup=admin_keyboard)
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ Ошибка при отправке: {e}", reply_markup=admin_keyboard)


@bot.message_handler(func=lambda message: message.text == '📨 Ответить пользователю' and is_admin(message.chat.id))
def reply_to_user_start(message):
    messages = load_messages()
    if not messages:
        bot.send_message(message.chat.id, "📭 Сообщений от пользователей пока нет.", reply_markup=admin_keyboard)
        return

    # Собираем уникальных пользователей, которые писали
    user_messages = {}
    for msg in messages:
        user_id = msg['user_id']
        if user_id not in user_messages:
            user_messages[user_id] = {
                'last_time': msg['timestamp'],
                'username': user_names_data.get(str(user_id), msg['username']),
                'unanswered': not msg.get('replied', False)
            }

    # Сортируем: сначала пользователи с непрочитанными, затем по времени последнего сообщения (новые сверху)
    sorted_users = sorted(user_messages.items(),
                          key=lambda x: (not x[1]['unanswered'], x[1]['last_time']),
                          reverse=True)

    reply_text = "👥 Пользователи, которые писали:\n\n"
    for i, (user_id, info) in enumerate(sorted_users[:15], 1):  # Показываем до 15 пользователей
        status = "❓" if info['unanswered'] else "✅"
        reply_text += f"{status} ID {user_id} - {info['username']}\n"

    reply_text += "\n📝 Введите ID пользователя для ответа. Или нажмите 'Отмена'."
    msg = bot.send_message(message.chat.id, reply_text, reply_markup=cancel_keyboard)
    bot.register_next_step_handler(msg, process_reply_choice)


def process_reply_choice(message):
    if handle_cancellation(message, "Отмена выбора пользователя."):
        return
    try:
        target_user_id = int(message.text)
        messages = load_messages()

        # Проверяем, есть ли такой пользователь в списке
        user_exists = any(msg['user_id'] == target_user_id for msg in messages)
        if not user_exists:
            msg = bot.send_message(message.chat.id,
                                   "❌ Пользователь с таким ID не найден. Попробуйте еще раз или нажмите 'Отмена'.",
                                   reply_markup=cancel_keyboard)
            bot.register_next_step_handler(msg, process_reply_choice)
            return

        display_name = user_names_data.get(str(target_user_id), f"Пользователь {target_user_id}")
        admin_reply_states[message.chat.id] = {'target_user_id': target_user_id}

        msg = bot.send_message(message.chat.id,
                               f"💬 Вы отвечаете пользователю {display_name}.\n"
                               f"Отправьте текст ответа, фото, голосовое сообщение или видеокружок. Или нажмите 'Отмена'.",
                               reply_markup=cancel_keyboard)
        bot.register_next_step_handler(msg, process_admin_reply)
    except ValueError:
        msg = bot.send_message(message.chat.id, "❌ Пожалуйста, введите корректный ID (число). Или нажмите 'Отмена'.",
                               reply_markup=cancel_keyboard)
        bot.register_next_step_handler(msg, process_reply_choice)


def process_admin_reply(message):
    if handle_cancellation(message, "Отмена ответа пользователю."):
        return

    admin_id = message.chat.id
    if admin_id not in admin_reply_states:
        return

    reply_data = admin_reply_states[admin_id]
    target_user_id = reply_data['target_user_id']

    admin_username = message.from_user.username or ""
    admin_display = f"@{admin_username}" if admin_username else str(admin_id)

    try:
        # Отправляем ответ пользователю
        if message.text:
            text_to_send = f"👨‍💼 Ответ от администратора {admin_display}:\n\n{message.text}"
            bot.send_message(target_user_id, text_to_send)
        elif message.photo:
            caption = f"👨‍💼 Ответ от администратора {admin_display}:\n\n{message.caption if message.caption else ''}"
            bot.send_photo(target_user_id, message.photo[-1].file_id, caption=caption)
        elif message.voice:
            caption = f"👨‍💼 Ответ от администратора {admin_display}"
            bot.send_voice(target_user_id, message.voice.file_id, caption=caption)
        elif message.video_note:
            bot.send_video_note(target_user_id, message.video_note.file_id)
            bot.send_message(target_user_id, f"👨‍💼 Ответ от администратора {admin_display}")
        else:
            bot.send_message(admin_id, "❌ Поддерживаются только текст, фото, голосовые сообщения и видеокружки.",
                             reply_markup=admin_keyboard)
            return

        # Помечаем все сообщения этого пользователя как отвеченные
        messages = load_messages()
        for msg in messages:
            if msg['user_id'] == target_user_id:
                msg['replied'] = True
        with open(messages_file, 'w', encoding='utf-8') as f:
            json.dump(messages, f, ensure_ascii=False, indent=2)

        bot.send_message(admin_id, "✅ Ответ успешно отправлен!", reply_markup=admin_keyboard)
        try:
            log_admin_action(admin_id, "ответ пользователю", f"ID пользователя: {target_user_id}")
        except Exception:
            pass
    except Exception as e:
        bot.send_message(admin_id, f"❌ Ошибка отправки: {e}", reply_markup=admin_keyboard)
    finally:
        del admin_reply_states[admin_id]


@bot.message_handler(
    func=lambda message: message.text in ['📅 Обычное', '👨‍🏫 Преподаватели'] and is_admin(message.chat.id))
def force_check_schedule(message):
    schedule_type = "учащихся" if message.text == '📅 Обычное' else "преподавателей"
    bot.send_message(message.chat.id, f"🔍 Проверяю {schedule_type} расписание...")
    if check_schedule_updates():
        bot.send_message(message.chat.id, "✅ Проверка завершена. Найдено обновление.", reply_markup=admin_keyboard)
    else:
        bot.send_message(message.chat.id, "✅ Проверка завершена. Обновлений нет.", reply_markup=admin_keyboard)


@bot.message_handler(func=lambda message: message.text == '🔙 Главное меню')
def back_to_main(message):
    clear_user_state(message.chat.id)
    if is_admin(message.chat.id) and message.chat.id in admin_reply_states:
        del admin_reply_states[message.chat.id]
    bot.send_message(message.chat.id, "Главное меню:", reply_markup=get_main_keyboard(message.chat.id))


@bot.message_handler(content_types=['text'])
def handle_text_messages(message):
    update_user_name_info(message.from_user)

    if get_user_state(message.chat.id) == ADMIN_CHAT_MODE:
        process_admin_chat_message(message)
        return

    if message.text == 'ℹ️ Помощь':
        send_help(message)

    if message.text == '📊 Аудит' and is_admin(message.chat.id):
        events = get_recent_audit_events(20)
        if not events:
            bot.send_message(message.chat.id, "📭 Аудит пока пуст.", reply_markup=admin_keyboard)
            return
        formatted = "📊 Последние действия админов:\n\n"
        for ev in events[-20:]:
            uname = ev.get('admin_username') or ""
            formatted += (f"🕒 {ev.get('timestamp')} | {uname} ({ev.get('admin_id')})\n"
                          f"➡️ {ev.get('action')}\n{ev.get('details')}\n" + "─" * 20 + "\n")
        bot.send_message(message.chat.id, formatted)
        audit_options = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
        audit_options.row('📁 ЭКСПОРТ АУДИТА', '🔙 Главное меню')
        bot.send_message(message.chat.id, "Доступные действия:", reply_markup=audit_options)

    if message.text == '📁 ЭКСПОРТ АУДИТА' and is_admin(message.chat.id):
        if os.path.exists(AUDIT_FILE):
            try:
                with open(AUDIT_FILE, 'rb') as f:
                    bot.send_document(message.chat.id, f, reply_markup=admin_keyboard)
                try:
                    log_admin_action(message.chat.id, "экспорт аудита", "Отправлен файл audit_log.json")
                except Exception:
                    pass
            except Exception as e:
                bot.send_message(message.chat.id, f"❌ Ошибка отправки файла: {e}", reply_markup=admin_keyboard)
        else:
            bot.send_message(message.chat.id, "❌ Файл аудита не найден.", reply_markup=admin_keyboard)

    if message.text == '📁 Файлы' and is_admin(message.chat.id):
        if str(message.chat.id) != SUPER_ADMIN_ID:
            bot.send_message(message.chat.id, "❌ Доступ к файловому меню только у главного администратора.",
                             reply_markup=admin_keyboard)
            return
        files_menu = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
        files_menu.row('📄 audit_log.json', '👥 user_names.json')
        files_menu.row('📋 admins.json', '🔙 Главное меню')
        bot.send_message(message.chat.id, "Выберите файл для отправки:", reply_markup=files_menu)

    if message.text == '📄 audit_log.json' and str(message.chat.id) == SUPER_ADMIN_ID:
        if os.path.exists(AUDIT_FILE):
            try:
                with open(AUDIT_FILE, 'rb') as f:
                    bot.send_document(message.chat.id, f, reply_markup=admin_keyboard)
                try:
                    log_admin_action(message.chat.id, "скачать файл", "audit_log.json")
                except Exception:
                    pass
            except Exception as e:
                bot.send_message(message.chat.id, f"❌ Ошибка отправки: {e}", reply_markup=admin_keyboard)
        else:
            bot.send_message(message.chat.id, "❌ Файл audit_log.json не найден.", reply_markup=admin_keyboard)

    if message.text == '👥 user_names.json' and str(message.chat.id) == SUPER_ADMIN_ID:
        if os.path.exists(USER_NAMES_FILE):
            try:
                with open(USER_NAMES_FILE, 'rb') as f:
                    bot.send_document(message.chat.id, f, reply_markup=admin_keyboard)
                try:
                    log_admin_action(message.chat.id, "скачать файл", "user_names.json")
                except Exception:
                    pass
            except Exception as e:
                bot.send_message(message.chat.id, f"❌ Ошибка отправки: {e}", reply_markup=admin_keyboard)
        else:
            bot.send_message(message.chat.id, "❌ Файл user_names.json не найден.", reply_markup=admin_keyboard)

    if message.text == '📋 admins.json' and str(message.chat.id) == SUPER_ADMIN_ID:
        if os.path.exists(ADMIN_FILE):
            try:
                with open(ADMIN_FILE, 'rb') as f:
                    bot.send_document(message.chat.id, f, reply_markup=admin_keyboard)
                try:
                    log_admin_action(message.chat.id, "скачать файл", "admins.json")
                except Exception:
                    pass
            except Exception as e:
                bot.send_message(message.chat.id, f"❌ Ошибка отправки: {e}", reply_markup=admin_keyboard)


def save_users():
    try:
        users_file = os.path.join(DATA_FOLDER, "users.txt")
        with open(users_file, 'w', encoding='utf-8') as f:
            for user_id in user_ids:
                f.write(f"{user_id}\n")
    except Exception as e:
        print(f"Ошибка сохранения пользователей: {e}")


def load_users():
    global user_ids
    try:
        users_file = os.path.join(DATA_FOLDER, "users.txt")
        if os.path.exists(users_file):
            with open(users_file, 'r', encoding='utf-8') as f:
                user_ids = set(int(line.strip()) for line in f if line.strip())
    except Exception as e:
        print(f"Ошибка загрузки пользователей: {e}")


def load_last_hashes():
    global last_schedule_hash, last_teachers_schedule_hash, last_student_schedule_hash
    if os.path.exists(schedule_file):
        last_schedule_hash = calculate_file_hash(schedule_file)
    if os.path.exists(teachers_schedule_file):
        last_teachers_schedule_hash = calculate_file_hash(teachers_schedule_file)
    if os.path.exists(student_schedule_file):
        last_student_schedule_hash = calculate_file_hash(student_schedule_file)


def main():
    load_users()
    load_admins()
    load_last_hashes()
    load_schedule_file_ids()
    load_user_names()
    load_audit_log()
    check_schedule_updates()
    load_banned_users()
    global is_first_check
    is_first_check = False
    scheduler_thread = threading.Thread(target=schedule_checker, daemon=True)
    scheduler_thread.start()
    bot.infinity_polling()


if __name__ == "__main__":
    main()