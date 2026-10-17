import os
import sys
import importlib.util

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

# 1. Загрузка из корневого файла config.py напрямую по пути (без проблем с sys.path)
local_config = None
config_path = os.path.join(BASE_DIR, "config.py")

if os.path.exists(config_path):
    try:
        spec = importlib.util.spec_from_file_location("root_config", config_path)
        if spec and spec.loader:
            local_config = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(local_config)
    except Exception as e:
        print(f"⚠️ Предупреждение: Не удалось прочитать config.py: {e}")

# 2. ПРИОРИТЕТ:
# Если в config.py указан реальный токен — используем его!
# Переменная окружения BOT_TOKEN используется только если в config.py токена нет.
local_token = getattr(local_config, "BOT_TOKEN", None) if local_config else None
if local_token and local_token != "ВАШ_ТОКЕН_БОТА":
    BOT_TOKEN = str(local_token).strip()
else:
    BOT_TOKEN = os.getenv("BOT_TOKEN")

local_admin = getattr(local_config, "SUPER_ADMIN_ID", None) if local_config else None
if local_admin and str(local_admin) not in {"0", "ВАШ_TELEGRAM_ID"}:
    SUPER_ADMIN_ID = str(local_admin).strip()
else:
    SUPER_ADMIN_ID = os.getenv("SUPER_ADMIN_ID", "0")

INITIAL_ADMIN_IDS = getattr(local_config, "INITIAL_ADMIN_IDS", [])
NTЕK_SCHEDULE_URL = getattr(
    local_config,
    "NTЕK_SCHEDULE_URL",
    "https://ntek.by/index.php/uchashchemusya/raspisaniya",
)
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", getattr(local_config, "CHECK_INTERVAL", 600)))
MESSAGE_COOLDOWN = int(os.getenv("MESSAGE_COOLDOWN", getattr(local_config, "MESSAGE_COOLDOWN", 60)))
BROADCAST_COOLDOWN = int(os.getenv("BROADCAST_COOLDOWN", getattr(local_config, "BROADCAST_COOLDOWN", 15)))
DATA_FOLDER = os.getenv("DATA_FOLDER", getattr(local_config, "DATA_FOLDER", "data"))
ENABLE_AUDIT_LOG = bool(getattr(local_config, "ENABLE_AUDIT_LOG", True))
DONATION_TEXT = getattr(
    local_config,
    "DONATION_TEXT",
    "Поддержать проект:\n\nВаша поддержка помогает развивать бота.\nСпасибо за ваш вклад!",
)

if not BOT_TOKEN or BOT_TOKEN == "ВАШ_ТОКЕН_БОТА":
    print("❌ Ошибка: BOT_TOKEN не найден!")
    print("Укажите токен в файле config.py или в переменных окружения хостинга (BOT_TOKEN).")
    sys.exit(1)

# Папки
DATA_DIR = os.path.join(BASE_DIR, DATA_FOLDER)
ARCHIVE_DIR = os.path.join(DATA_DIR, "archive")

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(ARCHIVE_DIR, exist_ok=True)

# Пути к файлам данных
ADMIN_FILE = os.path.join(DATA_DIR, "admins.json")
BANNED_USERS_FILE = os.path.join(DATA_DIR, "banned_users.json")
MUTED_USERS_FILE = os.path.join(DATA_DIR, "muted_users.json")
USER_NAMES_FILE = os.path.join(DATA_DIR, "user_names.json")
USERS_FILE = os.path.join(DATA_DIR, "users.txt")
MESSAGES_FILE = os.path.join(DATA_DIR, "user_messages.json")
LAST_MESSAGE_TIME_FILE = os.path.join(DATA_DIR, "last_message_time.json")
AUDIT_FILE = os.path.join(DATA_DIR, "audit_log.json")
SCHEDULE_FILE_IDS_FILE = os.path.join(DATA_DIR, "schedule_file_ids.json")

# Пути к изображениям расписаний
SCHEDULE_STUDENTS_FILE = os.path.join(DATA_DIR, "last_schedule.jpg")
SCHEDULE_TEACHERS_FILE = os.path.join(DATA_DIR, "last_schedule_teachers.jpg")
SCHEDULE_BELLS_FILE = os.path.join(DATA_DIR, "bells_schedule.jpg")
SCHEDULE_STUDENT_MADE_FILE = os.path.join(DATA_DIR, "student_schedule.jpg")

# HTTP заголовки для парсинга
HTTP_HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/120.0.0.0 Safari/537.36'
    ),
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
}
