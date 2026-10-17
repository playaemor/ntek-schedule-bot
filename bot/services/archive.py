import os
import shutil
from datetime import datetime
from typing import List, Tuple
from bot.config import ARCHIVE_DIR


def get_archived_items() -> List[Tuple[str, str]]:
    """Возвращает список сохраненных расписаний из архива (от новых к старым)."""
    if not os.path.exists(ARCHIVE_DIR):
        return []

    items = []
    for entry in os.listdir(ARCHIVE_DIR):
        full_dir = os.path.join(ARCHIVE_DIR, entry)
        if os.path.isdir(full_dir):
            files = [f for f in os.listdir(full_dir) if f.lower().endswith((".jpg", ".jpeg", ".png"))]
            if files:
                items.append((entry, os.path.join(full_dir, files[0])))

    items.sort(key=lambda x: x[0], reverse=True)
    return items


def archive_schedule(source_file: str, schedule_type: str) -> str:
    """Архивирует файл расписания с текущей временной меткой."""
    try:
        now_str = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        target_dir = os.path.join(ARCHIVE_DIR, f"{now_str}_{schedule_type}")
        os.makedirs(target_dir, exist_ok=True)

        ext = os.path.splitext(source_file)[1] or ".jpg"
        target_file = os.path.join(target_dir, f"расписание_{schedule_type}{ext}")
        shutil.copy2(source_file, target_file)
        print(f"[Archive] Расписание сохранено в архив: {target_file}")
        return target_file
    except Exception as e:
        print(f"Ошибка архивации ({schedule_type}): {e}")
        return ""
