import telebot
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup
from urllib.parse import urljoin
import re
import time
import threading
import os
import shutil
from datetime import datetime
import hashlib
import json
from telebot.handler_backends import BaseMiddleware, CancelUpdate
from telebot import apihelper

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

bot = telebot.TeleBot(BOT_TOKEN, use_class_middlewares=True)

class BanMiddleware(BaseMiddleware):
    def __init__(self):
        super().__init__()
        # Указываем, какие типы действий мы хотим перехватывать
        self.update_types = ['message', 'callback_query']

    def pre_process(self, update, data):
        # Если ID пользователя есть в списке забаненных — жестко блокируем
        if update.from_user.id in banned_users:
            return CancelUpdate()

    def post_process(self, update, data, exception):
        pass
bot.setup_middleware(BanMiddleware())
if not os.path.exists(DATA_FOLDER):
    os.makedirs(DATA_FOLDER)

ARCHIVE_FOLDER = os.path.join(DATA_FOLDER, "archive")
if not os.path.exists(ARCHIVE_FOLDER):
    os.makedirs(ARCHIVE_FOLDER)

file_io_lock = threading.Lock()

http_session = requests.Session()
retry_strategy = Retry(total=3, backoff_factor=1, status_forcelist=[500, 502, 503, 504])
http_session.mount('https://', HTTPAdapter(max_retries=retry_strategy))
http_session.mount('http://', HTTPAdapter(max_retries=retry_strategy))

def atomic_save_json(filepath, data, indent=2):
    """Атомарное сохранение JSON: пишет во временный файл и заменяет оригинал.
    Гарантирует целостность данных при перезагрузках хостинга."""
    temp_filepath = f"{filepath}.tmp"
    with file_io_lock:
        try:
            with open(temp_filepath, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=indent)
            os.replace(temp_filepath, filepath)
        except Exception as e:
            if os.path.exists(temp_filepath):
                try:
                    os.remove(temp_filepath)
                except Exception:
                    pass
            print(f"Ошибка сохранения {filepath}: {e}")

def archive_schedule(source_file, schedule_type):
    """Сохраняет новое расписание в исторический архив в отдельную папку."""
    try:
        now_str = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
        archive_dir = os.path.join(ARCHIVE_FOLDER, f"{now_str}_{schedule_type}")
        os.makedirs(archive_dir, exist_ok=True)
        ext = os.path.splitext(source_file)[1] or ".jpg"
        archive_file = os.path.join(archive_dir, f"расписание_{schedule_type}{ext}")
        shutil.copy2(source_file, archive_file)
        print(f"📦 Расписание сохранено в архив: {archive_file}")
        return archive_file
    except Exception as e:
        print(f"Ошибка архивации расписания ({schedule_type}): {e}")
        return None

ADMIN_IDS = {SUPER_ADMIN_ID}.union(set(map(str, INITIAL_ADMIN_IDS)))
ADMIN_FILE = os.path.join(DATA_FOLDER, "admins.json")
BANNED_USERS_FILE = os.path.join(DATA_FOLDER, "banned_users.json")
MUTED_USERS_FILE = os.path.join(DATA_FOLDER, "muted_users.json")
banned_users = set()
muted_users = {}

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
admin_keyboard.row('🚫 Забанить', '✅ Разбанить')
admin_keyboard.row('🔇 Выдать мут', '🔊 Снять мут')
admin_keyboard.row('➕ Добавить админа', '➖ Удалить админа')
admin_keyboard.row('📁 Файлы', '🔙 Главное меню')

files_keyboard = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
files_keyboard.row('📄 audit_log.json', '👥 user_names.json')
files_keyboard.row('📋 admins.json', '👥 users.txt')
files_keyboard.row('🚫 banned_users.json', '🔇 muted_users.json')
files_keyboard.row('📦 Архив расписаний')
files_keyboard.row('👨‍💻 Админ-панель', '🔙 Главное меню')

admin_reply_states = {}

AUDIT_FILE = os.path.join(DATA_FOLDER, "audit_log.json")
audit_log = []


def is_admin(user_id):
    return str(user_id) in ADMIN_IDS

def resolve_user_id(identifier):
    """
    Разрешает ID пользователя по числу или @username из базы.
    Возвращает (user_id: int, identifier_str: str) или (None, error_msg: str).
    """
    if not identifier:
        return None, "❌ Укажите ID или @username пользователя."
    raw = str(identifier).strip()
    if raw.isdigit():
        return int(raw), None
    if raw.startswith('@'):
        target_uname = raw.lower()
        for uid_str, uname in user_names_data.items():
            if uname.lower() == target_uname:
                return int(uid_str), None
        return None, f"❌ Пользователь {raw} не найден в базе бота."
    return None, "❌ Неверный формат. Укажите ID (число) или @username."


def load_banned_users():
    global banned_users
    try:
        if os.path.exists(BANNED_USERS_FILE):
            with open(BANNED_USERS_FILE, 'r', encoding='utf-8') as f:
                banned_users = set(json.load(f))
    except Exception as e:
        print(f"Ошибка загрузки забаненных пользователей: {e}")


def save_banned_users():
    atomic_save_json(BANNED_USERS_FILE, list(banned_users))


def ban_user(identifier):
    """
    Блокирует пользователя по ID или @username.
    Возвращает кортеж (успех: bool, сообщение: str)
    """
    target_id, error = resolve_user_id(identifier)
    if not target_id:
        return False, error

    # Защита от бана администраторов
    if is_admin(target_id):
        return False, "❌ Нельзя забанить администратора."

    if target_id in banned_users:
        return False, f"ℹ️ Пользователь {identifier} (ID: {target_id}) уже заблокирован."

    # Добавляем в бан-лист и сохраняем
    banned_users.add(target_id)
    save_banned_users()

    # Сбрасываем режим общения, если пользователь был в нем
    clear_user_state(target_id)

    return True, f"✅ Пользователь {identifier} (ID: {target_id}) успешно заблокирован."


def unban_user(identifier):
    """
    Разблокирует пользователя по ID или @username.
    Возвращает кортеж (успех: bool, сообщение: str)
    """
    target_id, error = resolve_user_id(identifier)
    if not target_id:
        return False, error

    if target_id not in banned_users:
        return False, f"ℹ️ Пользователь {identifier} (ID: {target_id}) не находится в списке заблокированных."

    banned_users.remove(target_id)
    save_banned_users()

    return True, f"✅ Пользователь {identifier} (ID: {target_id}) успешно разблокирован."


def load_muted_users():
    global muted_users
    try:
        if os.path.exists(MUTED_USERS_FILE):
            with open(MUTED_USERS_FILE, 'r', encoding='utf-8') as f:
                muted_users = json.load(f)
    except Exception as e:
        print(f"Ошибка загрузки замученных пользователей: {e}")
        muted_users = {}


def save_muted_users():
    atomic_save_json(MUTED_USERS_FILE, muted_users)


def parse_duration(time_str):
    """
    Парсит строку длительности: 30m (минуты), 2h (часы), 1d (дни), 1w (недели).
    Либо просто число (интерпретируется как минуты).
    Возвращает количество секунд или None.
    """
    if not time_str:
        return None
    time_str = time_str.strip().lower()
    units = {'m': 60, 'h': 3600, 'd': 86400, 'w': 604800}
    unit = time_str[-1]
    if unit in units and time_str[:-1].isdigit():
        return int(time_str[:-1]) * units[unit]
    if time_str.isdigit():
        return int(time_str) * 60
    return None


def is_muted(user_id):
    """Проверяет, замучен ли пользователь. Автоматически снимает мут, если время вышло."""
    uid_str = str(user_id)
    if uid_str not in muted_users:
        return False
    until = muted_users[uid_str]
    if until is None:
        return True
    if time.time() > until:
        del muted_users[uid_str]
        save_muted_users()
        return False
    return True


def get_mute_info(user_id):
    """Возвращает форматированную строку с информацией о муте."""
    uid_str = str(user_id)
    if uid_str not in muted_users:
        return None
    until = muted_users[uid_str]
    if until is None:
        return "навсегда"
    remaining = int(until - time.time())
    if remaining <= 0:
        return None
    hours = remaining // 3600
    minutes = (remaining % 3600) // 60
    until_str = datetime.fromtimestamp(until).strftime('%d.%m.%Y %H:%M')
    if hours > 0:
        return f"до {until_str} (осталось {hours}ч {minutes}м)"
    return f"до {until_str} (осталось {minutes}м)"


def mute_user(identifier, duration_str=None):
    """
    Ограничивает пользователя в отправке сообщений админам.
    Возвращает кортеж (успех: bool, сообщение: str)
    """
    target_id, error = resolve_user_id(identifier)
    if not target_id:
        return False, error

    if is_admin(target_id):
        return False, "❌ Нельзя выдать мут администратору."

    until = None
    duration_text = "навсегда"
    if duration_str:
        seconds = parse_duration(duration_str)
        if seconds is None:
            return False, "❌ Неверный формат времени. Используйте: 30m, 2h, 1d (или оставьте пустым для мута навсегда)."
        until = time.time() + seconds
        duration_text = f"до {datetime.fromtimestamp(until).strftime('%d.%m.%Y %H:%M')} ({duration_str})"

    muted_users[str(target_id)] = until
    save_muted_users()
    clear_user_state(target_id)

    return True, f"🔇 Пользователю {identifier} (ID: {target_id}) выдан мут: {duration_text}."


def unmute_user(identifier):
    """
    Снимает мут с пользователя.
    Возвращает кортеж (успех: bool, сообщение: str)
    """
    target_id, error = resolve_user_id(identifier)
    if not target_id:
        return False, error

    uid_str = str(target_id)
    if uid_str not in muted_users:
        return False, f"ℹ️ Пользователь {identifier} (ID: {target_id}) не имеет активного мута."

    del muted_users[uid_str]
    save_muted_users()

    return True, f"🔊 С пользователя {identifier} (ID: {target_id}) успешно снят мут."


def get_main_keyboard(user_id):
    keyboard = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.row('📅 Расписание с сайта', '👨‍🏫 Расписание для преподавателей')
    keyboard.row('🔔 Расписание звонков', '📝 Расписание от учащихся')
    keyboard.row('📩 Написать админу', '🌟 Поддержать проект')
    if is_admin(user_id):
        keyboard.row('👨‍💻 Админ-панель')
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
    atomic_save_json(ADMIN_FILE, list(ADMIN_IDS))


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
    atomic_save_json('schedule_file_ids.json', schedule_file_ids)


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
    atomic_save_json(USER_NAMES_FILE, user_names_data, indent=4)


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


last_message_times = {}

def load_last_message_times():
    global last_message_times
    if not os.path.exists(last_message_time_file):
        last_message_times = {}
        return last_message_times
    try:
        with open(last_message_time_file, 'r', encoding='utf-8') as f:
            last_message_times = json.load(f)
            return last_message_times
    except Exception as e:
        print(f"Ошибка загрузки времени сообщений: {e}")
        last_message_times = {}
        return {}


def save_last_message_times(data=None):
    if data is None:
        data = last_message_times
    atomic_save_json(last_message_time_file, data)


def can_send_message(user_id):
    if get_user_state(user_id) == ADMIN_CHAT_MODE:
        return True
    user_id_str = str(user_id)
    if user_id_str not in last_message_times:
        return True
    return (time.time() - last_message_times[user_id_str]) >= MESSAGE_COOLDOWN


def get_cooldown_remaining(user_id):
    user_id_str = str(user_id)
    if user_id_str not in last_message_times:
        return 0
    elapsed = time.time() - last_message_times[user_id_str]
    return max(0, MESSAGE_COOLDOWN - elapsed)


def update_last_message_time(user_id):
    last_message_times[str(user_id)] = time.time()
    save_last_message_times()


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
        atomic_save_json(messages_file, messages)
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
        atomic_save_json(messages_file, messages)
        return True
    except Exception as e:
        print(f"Ошибка отметки сообщения: {e}")
        return False


def check_schedule_updates():
    global last_schedule_hash, last_teachers_schedule_hash, is_first_check
    try:
        response = http_session.get(ntek_url, headers=headers, timeout=30)
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
        # Включаем stream=True для потоковой загрузки (не забивает ОЗУ)
        img_response = http_session.get(url, headers=headers, timeout=30, stream=True)
        img_response.raise_for_status()

        # Лимит размера файла: 5 Мегабайт
        MAX_SIZE = 5 * 1024 * 1024
        downloaded_size = 0

        with open(temp_file, 'wb') as f:
            for chunk in img_response.iter_content(chunk_size=8192):
                downloaded_size += len(chunk)
                if downloaded_size > MAX_SIZE:
                    raise ValueError(f"Файл расписания слишком большой (> 5MB). Загрузка прервана.")
                f.write(chunk)

        current_hash = calculate_file_hash(temp_file)
        if current_hash != last_hash:
            if os.path.exists(target_file):
                os.remove(target_file)
            os.rename(temp_file, target_file)
            # Сохранение в архив в отдельную папку
            archive_schedule(target_file, schedule_type)
            return True
        else:
            os.remove(temp_file)
            return False

    except Exception as e:
        print(f"Ошибка при обработке расписания {schedule_type}: {e}")
        if os.path.exists(temp_file):
            os.remove(temp_file)
        return False


def send_update_notification(updates):
    if not user_ids: return
    update_text = "🔄 Обновлено расписание: " + ", ".join(updates)
    success_count = 0
    dead_users = set()

    for user_id in list(user_ids):
        try:
            bot.send_message(user_id, update_text)
            success_count += 1
            time.sleep(0.04)  # ~25 сообщений в секунду для соблюдения лимитов Telegram API
        except apihelper.ApiTelegramException as e:
            if e.error_code == 429:
                retry_after = e.result_json.get('parameters', {}).get('retry_after', 2)
                time.sleep(retry_after)
                try:
                    bot.send_message(user_id, update_text)
                    success_count += 1
                except Exception:
                    pass
            elif e.error_code in (403, 400) and any(err in str(e).lower() for err in ["blocked", "deactivated", "chat not found"]):
                dead_users.add(user_id)
            else:
                print(f"Ошибка отправки уведомления пользователю {user_id}: {e}")
        except Exception as e:
            print(f"Ошибка отправки уведомления пользователю {user_id}: {e}")

    if dead_users:
        user_ids.difference_update(dead_users)
        save_users()
        print(f"Удалено {len(dead_users)} неактивных/заблокировавших пользователей.")

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
    atomic_save_json(AUDIT_FILE, audit_log)


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
    if user_id not in user_ids:
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
    if is_muted(user_id):
        mute_info = get_mute_info(user_id) or "навсегда"
        bot.send_message(user_id, f"🔇 Вы ограничены в отправке сообщений администраторам ({mute_info}).",
                         reply_markup=get_main_keyboard(user_id))
        return
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
    if is_muted(user_id):
        clear_user_state(user_id)
        mute_info = get_mute_info(user_id) or "навсегда"
        bot.send_message(user_id, f"🔇 Вы ограничены в отправке сообщений администраторам ({mute_info}).",
                         reply_markup=get_main_keyboard(user_id))
        return
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
    text = (
        "👨‍💻 Панель администратора\n\n"
        "Команды модерации:\n"
        "• /ban <ID или @username> — полная блокировка\n"
        "• /unban <ID или @username> — разбан\n"
        "• /mute <ID или @username> [10m, 2h, 1d] — мут в чате с админом\n"
        "• /unmute <ID или @username> — снять мут"
    )
    bot.send_message(message.chat.id, text, reply_markup=admin_keyboard)


def send_help(message):
    help_text = """
    ℹ️ Справка по боту:
    - 📅 Расписание с сайта: текущее расписание занятий.
    - 👨‍🏫 Расписание для преподавателей: расписание для преподавателей.
    - 🔔 Расписание звонков: время занятий и перерывов.
    - 📝 Расписание от учащихся: расписание, которое вы нам скидываете.
    - 📩 Написать админу: перейти в режим общения с администратором (поддерживаются текст, фото, голосовые, видеокружки).
    - 🌟 Поддержать проект.

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
    - 🚫 Заблокированных (бан): {len(banned_users)}
    - 🔇 Ограниченных (мут): {len(muted_users)}
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
        bot.send_message(message.chat.id, "❌ Эта команда доступна только администраторам.")
        return

    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        bot.send_message(message.chat.id, "⚠️ Использование: /ban <ID или @username>")
        return

    success, reply_text = ban_user(args[1])
    if success:
        try:
            log_admin_action(message.chat.id, "бан", f"Пользователь: {args[1]}")
        except Exception:
            pass
    bot.send_message(message.chat.id, reply_text)


@bot.message_handler(commands=['unban'])
def handle_unban_command(message):
    if not is_admin(message.chat.id):
        bot.send_message(message.chat.id, "❌ Эта команда доступна только администраторам.")
        return

    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        bot.send_message(message.chat.id, "⚠️ Использование: /unban <ID или @username>")
        return

    success, reply_text = unban_user(args[1])
    if success:
        try:
            log_admin_action(message.chat.id, "разбан", f"Пользователь: {args[1]}")
        except Exception:
            pass
    bot.send_message(message.chat.id, reply_text)


@bot.message_handler(commands=['mute'])
def handle_mute_command(message):
    if not is_admin(message.chat.id):
        bot.send_message(message.chat.id, "❌ Эта команда доступна только администраторам.")
        return

    parts = message.text.split(maxsplit=2)
    if len(parts) < 2:
        bot.send_message(
            message.chat.id,
            "⚠️ Использование: /mute <ID или @username> [время: 10m, 2h, 1d]\n"
            "Пример: /mute @username 2h (мут на 2 часа)\n"
            "Если время не указано — мут бессрочный."
        )
        return

    target = parts[1]
    duration = parts[2] if len(parts) > 2 else None
    success, reply_text = mute_user(target, duration)
    if success:
        try:
            log_admin_action(message.chat.id, "мут", f"Пользователь: {target}, длительность: {duration or 'навсегда'}")
        except Exception:
            pass
    bot.send_message(message.chat.id, reply_text)


@bot.message_handler(commands=['unmute'])
def handle_unmute_command(message):
    if not is_admin(message.chat.id):
        bot.send_message(message.chat.id, "❌ Эта команда доступна только администраторам.")
        return

    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        bot.send_message(message.chat.id, "⚠️ Использование: /unmute <ID или @username>")
        return

    success, reply_text = unmute_user(args[1])
    if success:
        try:
            log_admin_action(message.chat.id, "размут", f"Пользователь: {args[1]}")
        except Exception:
            pass
    bot.send_message(message.chat.id, reply_text)


@bot.message_handler(func=lambda message: message.text == '🚫 Забанить' and is_admin(message.chat.id))
def request_ban_button(message):
    msg = bot.send_message(
        message.chat.id,
        "🚫 Введите ID (число) или @username пользователя для блокировки. Или нажмите 'Отмена'.",
        reply_markup=cancel_keyboard
    )
    bot.register_next_step_handler(msg, process_ban_step)


def process_ban_step(message):
    if handle_cancellation(message, "Отмена блокировки."):
        return
    target = message.text.strip()
    success, reply_text = ban_user(target)
    if success:
        try:
            log_admin_action(message.chat.id, "бан", f"Пользователь: {target}")
        except Exception:
            pass
    bot.send_message(message.chat.id, reply_text, reply_markup=admin_keyboard)


@bot.message_handler(func=lambda message: message.text == '✅ Разбанить' and is_admin(message.chat.id))
def request_unban_button(message):
    if not banned_users:
        bot.send_message(message.chat.id, "ℹ️ Список заблокированных пользователей пуст.", reply_markup=admin_keyboard)
        return
    banned_list_text = "🚫 Заблокированные пользователи:\n"
    for uid in list(banned_users)[:20]:
        uname = user_names_data.get(str(uid), "")
        banned_list_text += f"• ID: {uid} ({uname})\n"
    banned_list_text += "\nВведите ID или @username для разблокировки. Или нажмите 'Отмена'."
    msg = bot.send_message(message.chat.id, banned_list_text, reply_markup=cancel_keyboard)
    bot.register_next_step_handler(msg, process_unban_step)


def process_unban_step(message):
    if handle_cancellation(message, "Отмена разблокировки."):
        return
    target = message.text.strip()
    success, reply_text = unban_user(target)
    if success:
        try:
            log_admin_action(message.chat.id, "разбан", f"Пользователь: {target}")
        except Exception:
            pass
    bot.send_message(message.chat.id, reply_text, reply_markup=admin_keyboard)


@bot.message_handler(func=lambda message: message.text == '🔇 Выдать мут' and is_admin(message.chat.id))
def request_mute_button(message):
    prompt_text = (
        "🔇 Введите ID или @username пользователя, а также опционально время через пробел.\n\n"
        "Форматы времени:\n"
        "• 30m — 30 минут\n"
        "• 2h — 2 часа\n"
        "• 1d — 1 день\n"
        "• 1w — 1 неделя\n\n"
        "Примеры:\n"
        "• `@username 2h` (мут на 2 часа)\n"
        "• `123456789` (мут навсегда)\n\n"
        "Или нажмите 'Отмена'."
    )
    msg = bot.send_message(message.chat.id, prompt_text, reply_markup=cancel_keyboard)
    bot.register_next_step_handler(msg, process_mute_step)


def process_mute_step(message):
    if handle_cancellation(message, "Отмена выдачи мута."):
        return
    parts = message.text.strip().split(maxsplit=1)
    target = parts[0]
    duration = parts[1] if len(parts) > 1 else None
    success, reply_text = mute_user(target, duration)
    if success:
        try:
            log_admin_action(message.chat.id, "мут", f"Пользователь: {target}, длительность: {duration or 'навсегда'}")
        except Exception:
            pass
    bot.send_message(message.chat.id, reply_text, reply_markup=admin_keyboard)


@bot.message_handler(func=lambda message: message.text == '🔊 Снять мут' and is_admin(message.chat.id))
def request_unmute_button(message):
    active_mutes = {}
    for uid in list(muted_users.keys()):
        if is_muted(uid):
            active_mutes[uid] = muted_users[uid]

    if not active_mutes:
        bot.send_message(message.chat.id, "ℹ️ Пользователей с активным мутом нет.", reply_markup=admin_keyboard)
        return

    mutes_text = "🔇 Пользователи с мутом:\n"
    for uid in list(active_mutes.keys())[:20]:
        uname = user_names_data.get(str(uid), "")
        info = get_mute_info(uid)
        mutes_text += f"• ID: {uid} ({uname}) — {info}\n"
    mutes_text += "\nВведите ID или @username для снятия мута. Или нажмите 'Отмена'."
    msg = bot.send_message(message.chat.id, mutes_text, reply_markup=cancel_keyboard)
    bot.register_next_step_handler(msg, process_unmute_step)


def process_unmute_step(message):
    if handle_cancellation(message, "Отмена снятия мута."):
        return
    target = message.text.strip()
    success, reply_text = unmute_user(target)
    if success:
        try:
            log_admin_action(message.chat.id, "размут", f"Пользователь: {target}")
        except Exception:
            pass
    bot.send_message(message.chat.id, reply_text, reply_markup=admin_keyboard)


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
        dead_users = set()

        # Отправляем сообщение о начале рассылки
        bot.send_message(message.chat.id, f"🚀 Начинаю рассылку для {total_users} пользователей...",
                         reply_markup=admin_keyboard)

        # Подготовка медиа для отправки
        is_photo = bool(message.photo)
        photo_id = message.photo[-1].file_id if is_photo else None
        caption = (message.caption if message.caption else "") if is_photo else None
        text = message.text if not is_photo else None

        for user_id in list(user_ids):
            try:
                if is_photo:
                    bot.send_photo(user_id, photo_id, caption=caption)
                else:
                    bot.send_message(user_id, text)
                success_count += 1
                time.sleep(0.04)  # Ограничение частоты запросов к Telegram API (~25 сообщ./сек)
            except apihelper.ApiTelegramException as e:
                if e.error_code == 429:
                    retry_after = e.result_json.get('parameters', {}).get('retry_after', 2)
                    time.sleep(retry_after)
                    try:
                        if is_photo:
                            bot.send_photo(user_id, photo_id, caption=caption)
                        else:
                            bot.send_message(user_id, text)
                        success_count += 1
                    except Exception:
                        pass
                elif e.error_code in (403, 400) and any(err in str(e).lower() for err in ["blocked", "deactivated", "chat not found"]):
                    dead_users.add(user_id)
                else:
                    print(f"Ошибка рассылки пользователю {user_id}: {e}")
            except Exception as e:
                print(f"Ошибка рассылки пользователю {user_id}: {e}")

        if dead_users:
            user_ids.difference_update(dead_users)
            save_users()
            print(f"Рассылка: удалено {len(dead_users)} неактивных пользователей.")

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
            # Архивация в отдельную папку
            archive_schedule(file_path, schedule_type)
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
        atomic_save_json(messages_file, messages)

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


def get_archived_items():
    """Возвращает список папок архива, отсортированных от новых к старым."""
    if not os.path.exists(ARCHIVE_FOLDER):
        return []
    items = []
    for entry in os.listdir(ARCHIVE_FOLDER):
        full_path = os.path.join(ARCHIVE_FOLDER, entry)
        if os.path.isdir(full_path):
            files = [f for f in os.listdir(full_path) if f.lower().endswith(('.jpg', '.jpeg', '.png'))]
            if files:
                items.append((entry, os.path.join(full_path, files[0])))
    items.sort(key=lambda x: x[0], reverse=True)
    return items


def show_archive_page(chat_id, page=0, message_id=None):
    items = get_archived_items()
    if not items:
        bot.send_message(chat_id, "📭 В архиве пока нет сохраненных расписаний.", reply_markup=files_keyboard)
        return

    PAGE_SIZE = 5
    total_pages = (len(items) + PAGE_SIZE - 1) // PAGE_SIZE
    page = max(0, min(page, total_pages - 1))
    start_idx = page * PAGE_SIZE
    page_items = items[start_idx:start_idx + PAGE_SIZE]

    keyboard = telebot.types.InlineKeyboardMarkup()
    for entry_name, _ in page_items:
        display_label = entry_name.replace("_", " ")
        item_index = items.index((entry_name, _))
        keyboard.add(telebot.types.InlineKeyboardButton(
            text=f"📷 {display_label}",
            callback_data=f"arch_get:{item_index}"
        ))

    nav_buttons = []
    if page > 0:
        nav_buttons.append(telebot.types.InlineKeyboardButton("⬅️ Назад", callback_data=f"arch_pg:{page - 1}"))
    nav_buttons.append(telebot.types.InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="arch_noop"))
    if page < total_pages - 1:
        nav_buttons.append(telebot.types.InlineKeyboardButton("Вперед ➡️", callback_data=f"arch_pg:{page + 1}"))

    if len(nav_buttons) > 1:
        keyboard.row(*nav_buttons)

    text = f"📦 Архив расписаний (Страница {page + 1} из {total_pages}):\nНажмите на расписание, чтобы получить скриншот:"

    if message_id:
        try:
            bot.edit_message_text(text, chat_id, message_id, reply_markup=keyboard)
            return
        except Exception:
            pass
    bot.send_message(chat_id, text, reply_markup=keyboard)


@bot.callback_query_handler(func=lambda call: call.data.startswith(('arch_get:', 'arch_pg:', 'arch_noop')))
def handle_archive_callbacks(call):
    if not is_admin(call.message.chat.id):
        bot.answer_callback_query(call.id, "❌ Доступ только для администраторов.", show_alert=True)
        return

    if call.data == "arch_noop":
        bot.answer_callback_query(call.id)
        return

    if call.data.startswith("arch_pg:"):
        page = int(call.data.split(":")[1])
        bot.answer_callback_query(call.id)
        show_archive_page(call.message.chat.id, page=page, message_id=call.message.message_id)
        return

    if call.data.startswith("arch_get:"):
        idx = int(call.data.split(":")[1])
        items = get_archived_items()
        if 0 <= idx < len(items):
            entry_name, file_path = items[idx]
            bot.answer_callback_query(call.id, "Отправляю скриншот...")
            if os.path.exists(file_path):
                caption = f"📦 Расписание из архива:\n📅 {entry_name.replace('_', ' ')}"
                with open(file_path, 'rb') as photo:
                    bot.send_photo(call.message.chat.id, photo, caption=caption)
            else:
                bot.send_message(call.message.chat.id, f"❌ Файл {file_path} не найден на сервере.")
        else:
            bot.answer_callback_query(call.id, "Расписание не найдено.", show_alert=True)


@bot.message_handler(content_types=['text'])
def handle_text_messages(message):
    update_user_name_info(message.from_user)

    if get_user_state(message.chat.id) == ADMIN_CHAT_MODE:
        process_admin_chat_message(message)
        return

    if message.text == '📁 Файлы' and is_admin(message.chat.id):
        text = (
            "📁 Меню файлов и архива\n\n"
            "Выберите файл для скачивания или откройте архив старых расписаний:"
        )
        bot.send_message(message.chat.id, text, reply_markup=files_keyboard)
        return

    if message.text == '📦 Архив расписаний' and is_admin(message.chat.id):
        show_archive_page(message.chat.id, page=0)
        return

    FILE_TARGETS = {
        '📄 audit_log.json': (AUDIT_FILE, "audit_log.json"),
        '👥 user_names.json': (USER_NAMES_FILE, "user_names.json"),
        '📋 admins.json': (ADMIN_FILE, "admins.json"),
        '👥 users.txt': (os.path.join(DATA_FOLDER, "users.txt"), "users.txt"),
        '🚫 banned_users.json': (BANNED_USERS_FILE, "banned_users.json"),
        '🔇 muted_users.json': (MUTED_USERS_FILE, "muted_users.json"),
    }

    if message.text in FILE_TARGETS and is_admin(message.chat.id):
        file_path, file_name = FILE_TARGETS[message.text]
        if os.path.exists(file_path):
            try:
                with open(file_path, 'rb') as f:
                    bot.send_document(message.chat.id, f, reply_markup=files_keyboard)
                try:
                    log_admin_action(message.chat.id, "скачать файл", file_name)
                except Exception:
                    pass
            except Exception as e:
                bot.send_message(message.chat.id, f"❌ Ошибка отправки: {e}", reply_markup=files_keyboard)
        else:
            bot.send_message(message.chat.id, f"❌ Файл {file_name} еще не создан на сервере.", reply_markup=files_keyboard)
        return


def save_users():
    users_file = os.path.join(DATA_FOLDER, "users.txt")
    temp_file = f"{users_file}.tmp"
    with file_io_lock:
        try:
            with open(temp_file, 'w', encoding='utf-8') as f:
                for user_id in user_ids:
                    f.write(f"{user_id}\n")
            os.replace(temp_file, users_file)
        except Exception as e:
            if os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                except Exception:
                    pass
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
    load_last_message_times()
    load_audit_log()
    load_banned_users()
    load_muted_users()
    check_schedule_updates()
    global is_first_check
    is_first_check = False
    scheduler_thread = threading.Thread(target=schedule_checker, daemon=True)
    scheduler_thread.start()
    bot.infinity_polling()


if __name__ == "__main__":
    main()
