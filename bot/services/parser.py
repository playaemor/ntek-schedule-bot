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
VALID_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".pdf")
is_first_check = True


def is_valid_file_header(header: bytes) -> bool:
    """Проверяет начальные байты файла на сигнатуры допустимых изображений или PDF."""
    if len(header) < 4:
        return False
    if (
        header.startswith(b"\xff\xd8\xff")  # JPEG
        or header.startswith(b"\x89PNG\r\n\x1a\n")  # PNG
        or header.startswith(b"GIF87a")
        or header.startswith(b"GIF89a")  # GIF
        or (header.startswith(b"RIFF") and len(header) >= 12 and header[8:12] == b"WEBP")  # WEBP
        or header.startswith(b"%PDF")  # PDF
    ):
        return True
    return False


def is_valid_schedule_href(href: str) -> bool:
    """Проверяет, что ссылка ведет на файл расписания, а не на страницу сайта (например, /index.php)."""
    if not href:
        return False
    clean = href.split("?")[0].lower()
    if clean.endswith((".php", ".html", ".htm")):
        return False
    if clean.endswith(VALID_EXTS):
        return True
    if "/documents/" in clean or "/raspisanie/" in clean:
        return True
    return False


def find_schedule_link(soup: BeautifulSoup, schedule_type: str) -> Optional[str]:
    """Находит относительный URL ссылки на расписание нужного типа."""
    search_roots = []
    content_area = (
        soup.find("div", itemprop="articleBody")
        or soup.find("article")
        or soup.find("main")
    )
    if content_area:
        search_roots.append(content_area)
    search_roots.append(soup)

    for root in search_roots:
        if schedule_type == "учащихся":
            # 1. Поиск по имени файла: s02112026.jpg, s_02112026.jpg, s-02112026.jpg
            link = root.find("a", href=re.compile(r"s[-_]?\d+.*?\.(?:jpe?g|png|webp|pdf)", re.IGNORECASE))
            if link and link.get("href") and is_valid_schedule_href(link["href"]):
                return link["href"]

            # 2. Поиск по тексту ссылки (расписание + учащиеся), указывающей на файл
            for a in root.find_all("a", href=True):
                text = a.get_text(strip=True).lower()
                href = a["href"]
                if ("расписани" in text or "занят" in text) and "учащ" in text:
                    if is_valid_schedule_href(href):
                        return href

            # 3. Резерв: текст содержит "учащ" и ссылка ведет на валидный файл расписания
            for a in root.find_all("a", href=True):
                text = a.get_text(strip=True).lower()
                href = a["href"]
                if "учащ" in text and is_valid_schedule_href(href):
                    return href

        elif schedule_type == "преподавателей":
            # 1. Поиск по имени файла: t02112026.jpg, t_02112026.jpg, t-02112026.jpg
            link = root.find("a", href=re.compile(r"t[-_]?\d+.*?\.(?:jpe?g|png|webp|pdf)", re.IGNORECASE))
            if link and link.get("href") and is_valid_schedule_href(link["href"]):
                return link["href"]

            # 2. Поиск по тексту ссылки (расписание + преподаватели), указывающей на файл
            for a in root.find_all("a", href=True):
                text = a.get_text(strip=True).lower()
                href = a["href"]
                if ("расписани" in text or "занят" in text) and "препод" in text:
                    if is_valid_schedule_href(href):
                        return href

            # 3. Резерв: текст содержит "препод" и ссылка ведет на валидный файл расписания
            for a in root.find_all("a", href=True):
                text = a.get_text(strip=True).lower()
                href = a["href"]
                if "препод" in text and is_valid_schedule_href(href):
                    return href

        elif schedule_type == "звонков":
            # 1. Поиск по имени файла звонков: zvonki
            link = root.find("a", href=re.compile(r"zvonk.*?\.(?:jpe?g|png|webp|pdf)", re.IGNORECASE))
            if link and link.get("href") and is_valid_schedule_href(link["href"]):
                return link["href"]

            # 2. Поиск по тексту ссылки (звонки)
            for a in root.find_all("a", href=True):
                text = a.get_text(strip=True).lower()
                href = a["href"]
                if "звонк" in text and is_valid_schedule_href(href):
                    return href

    return None


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

            content_type = response.headers.get("Content-Type", "").lower()
            if "text/html" in content_type or "text/plain" in content_type:
                print(f"Ошибка загрузки расписания {schedule_type}: сервер вернул Content-Type '{content_type}' вместо файла расписания")
                return False

            downloaded_size = 0
            first_chunk = True
            async with aiofiles.open(temp_file, "wb") as f:
                async for chunk in response.content.iter_chunked(8192):
                    if first_chunk:
                        first_chunk = False
                        if not is_valid_file_header(chunk):
                            raise ValueError(f"Сервер вернул недопустимый формат данных для {schedule_type} (не изображение/PDF)")
                    downloaded_size += len(chunk)
                    if downloaded_size > MAX_FILE_SIZE:
                        raise ValueError(f"Размер расписания превышает 5 МБ. Загрузка прервана.")
                    await f.write(chunk)

            if downloaded_size < 100:
                raise ValueError(f"Размер загруженного файла для {schedule_type} слишком мал ({downloaded_size} байт)")

        if not storage.is_valid_schedule_file(temp_file):
            raise ValueError(f"Загруженный файл для {schedule_type} не прошел валидацию формата.")

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

            link_students_href = find_schedule_link(soup, "учащихся")
            link_teachers_href = find_schedule_link(soup, "преподавателей")
            link_bells_href = find_schedule_link(soup, "звонков")

            if link_students_href:
                url = urljoin(NTЕK_SCHEDULE_URL, link_students_href)
                temp_file = os.path.join(os.path.dirname(SCHEDULE_STUDENTS_FILE), "temp_students.jpg")
                if await download_and_check_update(session, url, temp_file, SCHEDULE_STUDENTS_FILE, "учащихся"):
                    if not is_first_check:
                        updates.append("для учащихся")
            else:
                print("Предупреждение: Не найдена ссылка на расписание учащихся на сайте НТЭК.")

            if link_teachers_href:
                url = urljoin(NTЕK_SCHEDULE_URL, link_teachers_href)
                temp_file = os.path.join(os.path.dirname(SCHEDULE_TEACHERS_FILE), "temp_teachers.jpg")
                if await download_and_check_update(session, url, temp_file, SCHEDULE_TEACHERS_FILE, "преподавателей"):
                    if not is_first_check:
                        updates.append("для преподавателей")
            else:
                print("Предупреждение: Не найдена ссылка на расписание преподавателей на сайте НТЭК.")

            # Если расписание звонков отсутствует на сервере, загружаем его с сайта
            if link_bells_href and not os.path.exists(SCHEDULE_BELLS_FILE):
                url = urljoin(NTЕK_SCHEDULE_URL, link_bells_href)
                temp_file = os.path.join(os.path.dirname(SCHEDULE_BELLS_FILE), "temp_bells.jpg")
                await download_and_check_update(session, url, temp_file, SCHEDULE_BELLS_FILE, "звонков")

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
        if not storage.is_valid_schedule_file(path):
            continue
        current_hash = storage.calculate_file_hash(path)
        cached_id = storage.get_schedule_file_id(stype, expected_hash=current_hash)
        if not cached_id:
            try:
                if not path.lower().endswith(".pdf"):
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
