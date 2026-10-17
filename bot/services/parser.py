import os
import re
import asyncio
from typing import List, Optional
from urllib.parse import urljoin
import aiohttp
import aiofiles
from bs4 import BeautifulSoup
from aiogram import Bot
from aiogram.types import FSInputFile
from aiogram.exceptions import TelegramRetryAfter, TelegramForbiddenError, TelegramBadRequest

from bot.config import (
    NTЕK_SCHEDULE_URL,
    CHECK_INTERVAL,
    HTTP_HEADERS,
    SCHEDULE_STUDENTS_FILE,
    SCHEDULE_TEACHERS_FILE,
    SCHEDULE_BELLS_FILE,
)
from bot import storage
from bot.services.archive import archive_schedule

MAX_FILE_SIZE = 5 * 1024 * 1024  # 5 МБ
is_first_check = True


async def download_and_check_update(
    session: aiohttp.ClientSession,
    url: str,
    temp_file: str,
    target_file: str,
    schedule_type: str,
) -> bool:
    """Асинхронная потоковая загрузка и проверка изменений файла расписания."""
    try:
        async with session.get(url, headers=HTTP_HEADERS, timeout=aiohttp.ClientTimeout(total=30)) as response:
            if response.status != 200:
                print(f"Ошибка загрузки расписания {schedule_type}: статус {response.status}")
                return False

            downloaded_size = 0
            async with aiofiles.open(temp_file, "wb") as f:
                async for chunk in response.content.iter_chunked(8192):
                    downloaded_size += len(chunk)
                    if downloaded_size > MAX_FILE_SIZE:
                        raise ValueError(f"Размер расписания превышает 5 МБ. Загрузка прервана.")
                    await f.write(chunk)

        current_hash = storage.calculate_file_hash(temp_file)
        last_hash = storage.last_hashes.get(schedule_type)

        if current_hash and current_hash != last_hash:
            if os.path.exists(target_file):
                try:
                    os.remove(target_file)
                except Exception:
                    pass
            os.replace(temp_file, target_file)

            # Сохранение в архив
            archive_schedule(target_file, schedule_type)

            storage.last_hashes[schedule_type] = current_hash
            storage.invalidate_schedule_cache(schedule_type)
            return True
        else:
            if os.path.exists(temp_file):
                os.remove(temp_file)
            return False

    except Exception as e:
        print(f"Ошибка при обработке {schedule_type}: {e}")
        if os.path.exists(temp_file):
            try:
                os.remove(temp_file)
            except Exception:
                pass
        return False


async def broadcast_update(bot: Bot, updates: List[str]) -> None:
    """Асинхронная рассылка уведомлений об обновлении расписания."""
    if not storage.user_ids:
        return

    update_text = "🔄 <b>Обновлено расписание:</b> " + ", ".join(updates)
    dead_users = set()
    success_count = 0

    for user_id in list(storage.user_ids):
        try:
            await bot.send_message(user_id, update_text)
            success_count += 1
            await asyncio.sleep(0.04)  # ~25 сообщений в секунду (лимит Telegram API)
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after)
            try:
                await bot.send_message(user_id, update_text)
                success_count += 1
            except Exception:
                pass
        except (TelegramForbiddenError, TelegramBadRequest) as e:
            err_str = str(e).lower()
            if any(term in err_str for term in ["blocked", "deactivated", "chat not found", "user is deactivated"]):
                dead_users.add(user_id)
        except Exception as e:
            print(f"Ошибка уведомления пользователя {user_id}: {e}")

    if dead_users:
        storage.user_ids.difference_update(dead_users)
        storage.save_users()
        print(f"Удалено {len(dead_users)} неактивных пользователей.")

    print(f"Уведомления об обновлениях доставлены: {success_count}/{len(storage.user_ids)}")


async def check_schedule_updates(bot: Bot) -> bool:
    """Проверяет страницу расписания НТЭК на наличие обновлений."""
    global is_first_check
    updates = []

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                NTЕK_SCHEDULE_URL,
                headers=HTTP_HEADERS,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as response:
                if response.status != 200:
                    print(f"Сайт НТЭК вернул код {response.status}")
                    return False
                html = await response.text()

            soup = BeautifulSoup(html, "html.parser")

            # Поиск ссылок на расписания с приоритетом по шаблону имени файла (href regex) и поиском по тексту
            link_students = (
                soup.find("a", href=re.compile(r"s_\d+.*\.jpe?g", re.IGNORECASE))
                or soup.find(lambda t: t.name == "a" and "учащ" in t.get_text().lower())
                or soup.find("a", string=re.compile(r"Расписание для учащихся", re.IGNORECASE))
            )

            link_teachers = (
                soup.find("a", href=re.compile(r"t_\d+.*\.jpe?g", re.IGNORECASE))
                or soup.find(lambda t: t.name == "a" and "препод" in t.get_text().lower())
                or soup.find("a", string=re.compile(r"Расписание для преподавателей", re.IGNORECASE))
            )

            link_bells = (
                soup.find("a", href=re.compile(r"zvonki.*\.jpe?g", re.IGNORECASE))
                or soup.find(lambda t: t.name == "a" and "звонк" in t.get_text().lower())
                or soup.find("a", string=re.compile(r"звонк", re.IGNORECASE))
            )

            if link_students and link_students.get("href"):
                url = urljoin(NTЕK_SCHEDULE_URL, link_students["href"])
                temp_file = os.path.join(os.path.dirname(SCHEDULE_STUDENTS_FILE), "temp_students.jpg")
                if await download_and_check_update(session, url, temp_file, SCHEDULE_STUDENTS_FILE, "учащихся"):
                    if not is_first_check:
                        updates.append("для учащихся")

            if link_teachers and link_teachers.get("href"):
                url = urljoin(NTЕK_SCHEDULE_URL, link_teachers["href"])
                temp_file = os.path.join(os.path.dirname(SCHEDULE_TEACHERS_FILE), "temp_teachers.jpg")
                if await download_and_check_update(session, url, temp_file, SCHEDULE_TEACHERS_FILE, "преподавателей"):
                    if not is_first_check:
                        updates.append("для преподавателей")

            if link_bells and link_bells.get("href"):
                url = urljoin(NTЕK_SCHEDULE_URL, link_bells["href"])
                temp_file = os.path.join(os.path.dirname(SCHEDULE_BELLS_FILE), "temp_bells.jpg")
                if await download_and_check_update(session, url, temp_file, SCHEDULE_BELLS_FILE, "звонков"):
                    if not is_first_check:
                        updates.append("звонков")

        # Проверяем, чтобы для всех актуальных файлов расписаний были закэшированы file_id
        await ensure_schedule_cache(bot)

        if updates and not is_first_check:
            await broadcast_update(bot, updates)

        return bool(updates)

    except Exception as e:
        print(f"Ошибка при проверке расписания: {e}")
        return False
    finally:
        if is_first_check:
            is_first_check = False


async def ensure_schedule_cache(bot: Bot) -> None:
    """Гарантирует наличие валидного file_id для всех файлов расписаний для моментальной отправки в Inline."""
    targets = [
        ("учащихся", SCHEDULE_STUDENTS_FILE),
        ("преподавателей", SCHEDULE_TEACHERS_FILE),
        ("звонков", SCHEDULE_BELLS_FILE),
    ]
    chat_id = None
    if storage.admin_ids:
        for aid in storage.admin_ids:
            if aid and str(aid) != "0":
                chat_id = int(aid)
                break
    if not chat_id and storage.user_ids:
        chat_id = next(iter(storage.user_ids))

    if not chat_id:
        return

    for stype, path in targets:
        if not os.path.exists(path):
            continue
        current_hash = storage.calculate_file_hash(path)
        cached_id = storage.get_schedule_file_id(stype, expected_hash=current_hash)
        if not cached_id:
            try:
                msg = await bot.send_photo(chat_id, FSInputFile(path), disable_notification=True)
                if msg.photo:
                    storage.set_schedule_file_id(stype, msg.photo[-1].file_id, current_hash)
            except Exception:
                # Если пользователь/админ еще не нажал /start в этом боте — кэш обновится при первом запросе
                pass


async def schedule_checker_loop(bot: Bot) -> None:
    """Фоновый цикл периодической проверки расписания."""
    # Первая проверка без рассылки (кэширование состояния)
    await check_schedule_updates(bot)

    while True:
        try:
            await asyncio.sleep(CHECK_INTERVAL)
            await check_schedule_updates(bot)
        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"Ошибка в цикле schedule_checker: {e}")
            await asyncio.sleep(60)
