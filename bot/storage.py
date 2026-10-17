import os
import json
import time
import hashlib
import threading
from datetime import datetime
from typing import Optional, Tuple, List, Dict, Set, Any

from bot.config import (
    SUPER_ADMIN_ID,
    INITIAL_ADMIN_IDS,
    MESSAGE_COOLDOWN,
    ENABLE_AUDIT_LOG,
    ADMIN_FILE,
    BANNED_USERS_FILE,
    MUTED_USERS_FILE,
    USER_NAMES_FILE,
    USERS_FILE,
    MESSAGES_FILE,
    LAST_MESSAGE_TIME_FILE,
    AUDIT_FILE,
    SCHEDULE_FILE_IDS_FILE,
    SCHEDULE_STUDENTS_FILE,
    SCHEDULE_TEACHERS_FILE,
    SCHEDULE_STUDENT_MADE_FILE,
)

# Потокобезопасная блокировка для файловых операций
file_io_lock = threading.Lock()

# Глобальные структуры данных в памяти
user_ids: Set[int] = set()
admin_ids: Set[str] = set()
banned_users: Set[int] = set()
muted_users: Dict[str, Optional[float]] = {}
user_names_data: Dict[str, str] = {}
last_message_times: Dict[str, float] = {}
audit_log: List[Dict[str, Any]] = []

schedule_file_ids: Dict[str, Dict[str, Optional[str]]] = {
    "учащихся": {"file_id": None, "hash": None},
    "преподавателей": {"file_id": None, "hash": None},
    "звонков": {"file_id": None, "hash": None},
    "от учащихся": {"file_id": None, "hash": None},
}

last_hashes: Dict[str, Optional[str]] = {
    "учащихся": None,
    "преподавателей": None,
    "от учащихся": None,
}


def atomic_save_json(filepath: str, data: Any, indent: int = 2) -> None:
    """Атомарная запись JSON через временный файл."""
    temp_filepath = f"{filepath}.tmp"
    with file_io_lock:
        try:
            with open(temp_filepath, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=indent)
            os.replace(temp_filepath, filepath)
        except Exception as e:
            if os.path.exists(temp_filepath):
                try:
                    os.remove(temp_filepath)
                except Exception:
                    pass
            print(f"Ошибка сохранения {filepath}: {e}")


def calculate_file_hash(filename: str) -> Optional[str]:
    """Вычисляет sha256 хэш файла."""
    if not os.path.exists(filename):
        return None
    sha256 = hashlib.sha256()
    with open(filename, "rb") as f:
        for block in iter(lambda: f.read(8192), b""):
            sha256.update(block)
    return sha256.hexdigest()


# --- Пользователи ---

def load_users() -> None:
    global user_ids
    if os.path.exists(USERS_FILE):
        try:
            with open(USERS_FILE, "r", encoding="utf-8") as f:
                user_ids = {int(line.strip()) for line in f if line.strip().isdigit()}
        except Exception as e:
            print(f"Ошибка загрузки users.txt: {e}")


def save_users() -> None:
    temp_file = f"{USERS_FILE}.tmp"
    with file_io_lock:
        try:
            with open(temp_file, "w", encoding="utf-8") as f:
                for uid in sorted(user_ids):
                    f.write(f"{uid}\n")
            os.replace(temp_file, USERS_FILE)
        except Exception as e:
            if os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                except Exception:
                    pass
            print(f"Ошибка сохранения users.txt: {e}")


def register_user(user_id: int) -> bool:
    """Добавляет пользователя в базу, если его еще нет."""
    if user_id not in user_ids:
        user_ids.add(user_id)
        save_users()
        return True
    return False


# --- Администраторы ---

def load_admins() -> None:
    global admin_ids
    super_admin_str = str(SUPER_ADMIN_ID).strip()
    initial_admins = {str(uid).strip() for uid in INITIAL_ADMIN_IDS}
    if super_admin_str and super_admin_str != "0":
        initial_admins.add(super_admin_str)

    if os.path.exists(ADMIN_FILE):
        try:
            with open(ADMIN_FILE, "r", encoding="utf-8") as f:
                loaded = set(map(str, json.load(f)))
                if super_admin_str and super_admin_str != "0":
                    loaded.add(super_admin_str)
                admin_ids = loaded
        except Exception as e:
            print(f"Ошибка загрузки admins.json: {e}")
            admin_ids = initial_admins
    else:
        admin_ids = initial_admins
        save_admins()


def save_admins() -> None:
    atomic_save_json(ADMIN_FILE, sorted(list(admin_ids)))


def is_admin(user_id: Any) -> bool:
    return str(user_id).strip() in admin_ids


def is_super_admin(user_id: Any) -> bool:
    super_admin_str = str(SUPER_ADMIN_ID).strip()
    return bool(super_admin_str and super_admin_str != "0" and str(user_id).strip() == super_admin_str)


# --- Юзернеймы и поиск ---

def load_user_names() -> None:
    global user_names_data
    if os.path.exists(USER_NAMES_FILE):
        try:
            with open(USER_NAMES_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                user_names_data = {str(k): str(v) for k, v in data.items()}
        except Exception as e:
            print(f"Ошибка загрузки user_names.json: {e}")


def save_user_names() -> None:
    atomic_save_json(USER_NAMES_FILE, user_names_data, indent=4)


def update_user_name_info(user: Any) -> None:
    if not user:
        return
    uid_str = str(user.id)
    if user.username:
        new_val = f"@{user.username}"
    else:
        full_name = f"{user.first_name or ''} {user.last_name or ''}".strip()
        new_val = full_name if full_name else "Без username"

    if user_names_data.get(uid_str) != new_val:
        user_names_data[uid_str] = new_val
        save_user_names()


def resolve_user_id(identifier: str) -> Tuple[Optional[int], Optional[str]]:
    """Преобразует ID или @username в int ID."""
    if not identifier:
        return None, "Укажите ID или @username пользователя."
    raw = str(identifier).strip()
    if raw.isdigit():
        return int(raw), None
    if raw.startswith("@"):
        target = raw.lower()
        for uid_str, uname in user_names_data.items():
            if uname.lower() == target:
                return int(uid_str), None
        return None, f"Пользователь {raw} не найден в базе бота."
    return None, "Неверный формат. Укажите числовой ID или @username."


# --- Блокировка (Бан) ---

def load_banned_users() -> None:
    global banned_users
    if os.path.exists(BANNED_USERS_FILE):
        try:
            with open(BANNED_USERS_FILE, "r", encoding="utf-8") as f:
                banned_users = {int(x) for x in json.load(f)}
        except Exception as e:
            print(f"Ошибка загрузки banned_users.json: {e}")


def save_banned_users() -> None:
    atomic_save_json(BANNED_USERS_FILE, sorted(list(banned_users)))


def ban_user(identifier: str) -> Tuple[bool, str]:
    target_id, error = resolve_user_id(identifier)
    if not target_id:
        return False, f"❌ {error}"
    if is_admin(target_id):
        return False, "❌ Нельзя забанить администратора."
    if target_id in banned_users:
        return False, f"ℹ️ Пользователь {identifier} (ID: <code>{target_id}</code>) уже заблокирован."

    banned_users.add(target_id)
    save_banned_users()
    return True, f"✅ Пользователь {identifier} (ID: <code>{target_id}</code>) заблокирован."


def unban_user(identifier: str) -> Tuple[bool, str]:
    target_id, error = resolve_user_id(identifier)
    if not target_id:
        return False, f"❌ {error}"
    if target_id not in banned_users:
        return False, f"ℹ️ Пользователь {identifier} (ID: <code>{target_id}</code>) не заблокирован."

    banned_users.remove(target_id)
    save_banned_users()
    return True, f"✅ Пользователь {identifier} (ID: <code>{target_id}</code>) разблокирован."


# --- Ограничение (Мут) ---

def load_muted_users() -> None:
    global muted_users
    if os.path.exists(MUTED_USERS_FILE):
        try:
            with open(MUTED_USERS_FILE, "r", encoding="utf-8") as f:
                muted_users = json.load(f)
        except Exception as e:
            print(f"Ошибка загрузки muted_users.json: {e}")


def save_muted_users() -> None:
    atomic_save_json(MUTED_USERS_FILE, muted_users)


def parse_duration(time_str: Optional[str]) -> Optional[int]:
    if not time_str:
        return None
    time_str = time_str.strip().lower()
    units = {"m": 60, "h": 3600, "d": 86400, "w": 604800}
    unit = time_str[-1]
    if unit in units and time_str[:-1].isdigit():
        return int(time_str[:-1]) * units[unit]
    if time_str.isdigit():
        return int(time_str) * 60
    return None


def is_muted(user_id: int) -> bool:
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


def get_mute_info(user_id: int) -> Optional[str]:
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
    until_str = datetime.fromtimestamp(until).strftime("%d.%m.%Y %H:%M")
    if hours > 0:
        return f"до {until_str} ({hours} ч {minutes} мин)"
    return f"до {until_str} ({minutes} мин)"


def mute_user(identifier: str, duration_str: Optional[str] = None) -> Tuple[bool, str]:
    target_id, error = resolve_user_id(identifier)
    if not target_id:
        return False, f"❌ {error}"
    if is_admin(target_id):
        return False, "❌ Нельзя выдать мут администратору."

    until = None
    duration_text = "навсегда"
    if duration_str:
        seconds = parse_duration(duration_str)
        if seconds is None:
            return False, "❌ Неверный формат времени. Используйте: 30m, 2h, 1d (или оставьте пустым для мута навсегда)."
        until = time.time() + seconds
        until_dt = datetime.fromtimestamp(until).strftime("%d.%m.%Y %H:%M")
        duration_text = f"до {until_dt} ({duration_str})"

    muted_users[str(target_id)] = until
    save_muted_users()
    return True, f"🔇 Пользователю {identifier} (ID: <code>{target_id}</code>) выдан мут: {duration_text}."


def unmute_user(identifier: str) -> Tuple[bool, str]:
    target_id, error = resolve_user_id(identifier)
    if not target_id:
        return False, f"❌ {error}"
    uid_str = str(target_id)
    if uid_str not in muted_users:
        return False, f"ℹ️ У пользователя {identifier} нет активного мута."

    del muted_users[uid_str]
    save_muted_users()
    return True, f"🔊 С пользователя {identifier} (ID: <code>{target_id}</code>) снят мут."


# --- Ограничение частоты сообщений (Cooldown) ---

def load_last_message_times() -> None:
    global last_message_times
    if os.path.exists(LAST_MESSAGE_TIME_FILE):
        try:
            with open(LAST_MESSAGE_TIME_FILE, "r", encoding="utf-8") as f:
                last_message_times = json.load(f)
        except Exception as e:
            print(f"Ошибка загрузки last_message_time.json: {e}")


def save_last_message_times() -> None:
    atomic_save_json(LAST_MESSAGE_TIME_FILE, last_message_times)


def can_send_message(user_id: int) -> bool:
    uid_str = str(user_id)
    if uid_str not in last_message_times:
        return True
    return (time.time() - last_message_times[uid_str]) >= MESSAGE_COOLDOWN


def get_cooldown_remaining(user_id: int) -> int:
    uid_str = str(user_id)
    if uid_str not in last_message_times:
        return 0
    elapsed = time.time() - last_message_times[uid_str]
    return max(0, int(MESSAGE_COOLDOWN - elapsed))


def update_last_message_time(user_id: int) -> None:
    last_message_times[str(user_id)] = time.time()
    save_last_message_times()


# --- Сообщения обратной связи ---

def load_messages() -> List[Dict[str, Any]]:
    if not os.path.exists(MESSAGES_FILE):
        return []
    try:
        with open(MESSAGES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"Ошибка загрузки user_messages.json: {e}")
        return []


def save_message(
    user_id: int,
    username: str,
    message_text: str,
    message_type: str = "text",
    file_id: Optional[str] = None
) -> Optional[int]:
    try:
        messages = load_messages()
        new_id = len(messages) + 1
        new_entry = {
            "id": new_id,
            "user_id": user_id,
            "username": username,
            "message": message_text,
            "type": message_type,
            "file_id": file_id,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "replied": False,
        }
        messages.append(new_entry)
        atomic_save_json(MESSAGES_FILE, messages)
        return new_id
    except Exception as e:
        print(f"Ошибка сохранения сообщения: {e}")
        return None


def mark_message_as_replied(message_id: int) -> bool:
    try:
        messages = load_messages()
        for msg in messages:
            if msg.get("id") == message_id:
                msg["replied"] = True
                break
        atomic_save_json(MESSAGES_FILE, messages)
        return True
    except Exception as e:
        print(f"Ошибка отметки сообщения {message_id}: {e}")
        return False


def mark_user_messages_as_replied(user_id: int) -> None:
    try:
        messages = load_messages()
        for msg in messages:
            if msg.get("user_id") == user_id:
                msg["replied"] = True
        atomic_save_json(MESSAGES_FILE, messages)
    except Exception as e:
        print(f"Ошибка отметки сообщений пользователя {user_id}: {e}")


# --- Журнал аудита ---

def load_audit_log() -> None:
    global audit_log
    if os.path.exists(AUDIT_FILE):
        try:
            with open(AUDIT_FILE, "r", encoding="utf-8") as f:
                audit_log = json.load(f)
        except Exception as e:
            print(f"Ошибка загрузки audit_log.json: {e}")


def save_audit_log() -> None:
    atomic_save_json(AUDIT_FILE, audit_log)


def log_admin_action(admin_id: Any, action: str, details: str, admin_username: str = "") -> None:
    if not ENABLE_AUDIT_LOG:
        return
    entry = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "admin_id": int(admin_id) if str(admin_id).isdigit() else admin_id,
        "admin_username": admin_username,
        "action": action,
        "details": details,
    }
    audit_log.append(entry)
    save_audit_log()


def get_recent_audit_events(limit: int = 20) -> List[Dict[str, Any]]:
    return audit_log[-limit:] if len(audit_log) >= limit else audit_log[:]


# --- Кэш file_id расписаний ---

def load_schedule_file_ids() -> None:
    global schedule_file_ids
    if os.path.exists(SCHEDULE_FILE_IDS_FILE):
        try:
            with open(SCHEDULE_FILE_IDS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    for k, v in data.items():
                        if isinstance(v, dict):
                            schedule_file_ids[k] = {
                                "file_id": v.get("file_id"),
                                "hash": v.get("hash"),
                            }
                        elif isinstance(v, str):
                            # Старый плоский формат (только file_id без хэша) сбрасываем,
                            # чтобы исключить отдачу неактуальных расписаний из кэша
                            schedule_file_ids[k] = {"file_id": None, "hash": None}
        except Exception as e:
            print(f"Ошибка загрузки schedule_file_ids.json: {e}")


def save_schedule_file_ids() -> None:
    atomic_save_json(SCHEDULE_FILE_IDS_FILE, schedule_file_ids)


def get_schedule_file_id(schedule_type: str, expected_hash: Optional[str] = None) -> Optional[str]:
    """Возвращает file_id только если хэш совпадает с текущим файлом на диске."""
    entry = schedule_file_ids.get(schedule_type)
    if not entry or not isinstance(entry, dict):
        return None
    file_id = entry.get("file_id")
    stored_hash = entry.get("hash")
    if not file_id:
        return None
    if expected_hash is not None and stored_hash != expected_hash:
        return None
    return file_id


def set_schedule_file_id(schedule_type: str, file_id: Optional[str], file_hash: Optional[str] = None) -> None:
    """Сохраняет file_id вместе с хэшем файла на диске."""
    if file_id is None:
        schedule_file_ids[schedule_type] = {"file_id": None, "hash": None}
    else:
        schedule_file_ids[schedule_type] = {
            "file_id": file_id,
            "hash": file_hash,
        }
    save_schedule_file_ids()


def invalidate_schedule_cache(schedule_type: Optional[str] = None) -> None:
    """Сбрасывает кэш file_id для указанного или всех типов расписания."""
    if schedule_type:
        schedule_file_ids[schedule_type] = {"file_id": None, "hash": None}
    else:
        for k in schedule_file_ids:
            schedule_file_ids[k] = {"file_id": None, "hash": None}
    save_schedule_file_ids()


# --- Хэши расписаний ---

def load_last_hashes() -> None:
    global last_hashes
    if os.path.exists(SCHEDULE_STUDENTS_FILE):
        last_hashes["учащихся"] = calculate_file_hash(SCHEDULE_STUDENTS_FILE)
    if os.path.exists(SCHEDULE_TEACHERS_FILE):
        last_hashes["преподавателей"] = calculate_file_hash(SCHEDULE_TEACHERS_FILE)
    if os.path.exists(SCHEDULE_STUDENT_MADE_FILE):
        last_hashes["от учащихся"] = calculate_file_hash(SCHEDULE_STUDENT_MADE_FILE)


def init_storage() -> None:
    """Инициализация всех хранилищ данных при старте бота."""
    load_users()
    load_admins()
    load_user_names()
    load_banned_users()
    load_muted_users()
    load_last_message_times()
    load_audit_log()
    load_schedule_file_ids()
    load_last_hashes()
