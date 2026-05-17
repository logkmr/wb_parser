import asyncio
import logging
import os
import re
import time
from datetime import timedelta

import httpx
import requests
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes

# Загружаем переменные окружения из .env
load_dotenv()

# --- НАСТРОЙКИ ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
WB_API_TOKEN = os.getenv("WB_API_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

COEF_URL = "https://common-api.wildberries.ru/api/tariffs/v1/acceptance/coefficients"
WH_URL = "https://supplies-api.wildberries.ru/api/v1/warehouses"
HEADERS = {"Authorization": WB_API_TOKEN}

FREE_COEFFS = {0, 1}
MAX_SLOTS_PER_MESSAGE = 5
CHECK_INTERVAL_MINUTES = 5

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)


# Специальный класс ошибки для отлова лимитов ВБ
class RateLimitException(Exception):
    def __init__(self, wait_time):
        self.wait_time = wait_time


def http_request(method, url, **kwargs):
    for attempt in range(5):
        try:
            resp = requests.request(method, url, headers=HEADERS, timeout=15, **kwargs)
            if resp.status_code == 429:
                # Читаем точное время блокировки от Wildberries
                wait_str = resp.headers.get("X-Ratelimit-Retry") or resp.headers.get("Retry-After")
                wait = int(wait_str) if wait_str else 60
                raise RateLimitException(wait)  # Пробрасываем ошибку наверх вместо жесткого слипа

            resp.raise_for_status()
            return resp
        except requests.exceptions.RequestException as exc:
            logging.warning(f"Попытка {attempt+1}/5: {exc}")
            time.sleep(2 ** attempt)
    raise Exception("Не удалось выполнить запрос после 5 попыток")


def fetch_russian_wh_ids():
    data = http_request("GET", WH_URL).json()
    non_ru = re.compile(
        r"(казахстан|алматы|астана|беларусь|минск|армения|ереван|грузия|tbilisi|азерб|baku|узбек|ташкент|киргиз|bishkek|таджик|dushanbe)",
        re.I,
    )
    return {
        w["ID"]
        for w in data
        if not non_ru.search((w.get("address", "") or "") + (w.get("name", "") or ""))
    }


def get_sort_key(slot):
    """Сортировка: сначала Москва/МО, затем остальные по дате"""
    name = slot.get("warehouseName", "").lower()
    moscow_keywords = [
        "моск",
        "коледино",
        "электросталь",
        "подольск",
        "белая дача",
        "пушкино",
        "домодедово",
        "тула",
        "чехов",
    ]

    priority = 0 if any(kw in name for kw in moscow_keywords) else 1
    date = slot.get("date", "")
    return (priority, date)


class SupabaseDB:
    """Асинхронная обертка над Supabase REST API (через httpx)"""

    def __init__(self, url: str, key: str):
        self.url = url
        self.key = key
        self.headers = {
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Prefer": "return=minimal",
        }
        self.client = httpx.AsyncClient(timeout=30.0)

    async def get_chat_ids(self) -> list:
        """Возвращает список chat_id всех подписанных пользователей"""
        try:
            resp = await self.client.get(
                f"{self.url}/rest/v1/users?select=chat_id",
                headers=self.headers,
            )
            resp.raise_for_status()
            data = resp.json()
            return [r["chat_id"] for r in data]
        except Exception as e:
            logging.error(f"Ошибка получения пользователей из Supabase: {e}")
            return []

    async def add_user(self, chat_id: int) -> bool:
        """Добавляет пользователя. Возвращает True если успешно или уже существует."""
        try:
            resp = await self.client.post(
                f"{self.url}/rest/v1/users",
                headers=self.headers,
                json={"chat_id": chat_id},
            )
            if resp.status_code == 409:
                # Уже существует — не ошибка
                return True
            resp.raise_for_status()
            return True
        except Exception as e:
            logging.error(f"Ошибка добавления пользователя {chat_id}: {e}")
            return False

    async def close(self):
        await self.client.aclose()


class SlotChecker:
    def __init__(self, app):
        self.app = app
        self.iteration = 0
        self.sent = set()
        self.ru_ids = set()
        self.locked_until = 0  # Хранит timestamp, до которого ВБ нас заблокировал

    def _get_coeffs(self):
        resp = http_request("GET", COEF_URL)
        return resp.json()

    def _filter(self, rows):
        filtered = []
        if not rows:
            return filtered
        for r in rows:
            if r.get("coefficient") not in FREE_COEFFS:
                continue
            if not r.get("allowUnload"):
                continue
            wh_id = r.get("warehouseID")
            if self.ru_ids and wh_id not in self.ru_ids:
                continue
            filtered.append(r)

        filtered.sort(key=get_sort_key)
        return filtered

    async def tick(self, _ctx):
        # Если время блокировки еще не вышло, просто пропускаем этот тик парсера
        if time.time() < self.locked_until:
            rem = int(self.locked_until - time.time())
            logging.info(f"⏳ Ожидание снятия лимитов ВБ... Осталось {rem} сек.")
            return

        try:
            # Загружаем склады при первом удачном тике
            if not self.ru_ids:
                self.ru_ids = await asyncio.to_thread(fetch_russian_wh_ids)
                logging.info(f"Загружено складов: {len(self.ru_ids)}")

            self.iteration += 1
            logging.info(f"Tick #{self.iteration}")

            rows = await asyncio.to_thread(self._get_coeffs)
            filtered = self._filter(rows)
            logging.info(f"Найдено новых слотов: {len(filtered)}")

            new_slots = []
            seen_in_tick = set()

            # Фильтрация дублей
            for s in filtered:
                key = (s["warehouseID"], s["date"])
                if key not in self.sent and key not in seen_in_tick:
                    new_slots.append(s)
                    seen_in_tick.add(key)

            if not new_slots:
                return

            to_send = new_slots[:MAX_SLOTS_PER_MESSAGE]

            # Форматирование по ТЗ
            msg = f"*{len(to_send)} новых свободных слота* (коэф 0/1)\n"
            for s in to_send:
                self.sent.add((s["warehouseID"], s["date"]))
                msg += f"\n✅ {s.get('warehouseName')} (ID {s['warehouseID']})\n"
                msg += f"📅 {s['date'][:10]}\n"
                msg += f"📊 Коэффициент: {s['coefficient']}\n"

            # Кнопка для комиссии
            keyboard = [[InlineKeyboardButton("📦 Забронировать", callback_data="book_dummy")]]
            reply_markup = InlineKeyboardMarkup(keyboard)

            # Рассылка всем подписанным пользователям из Supabase
            db: SupabaseDB = self.app.bot_data.get("db")
            if not db:
                logging.warning("Supabase DB не инициализирована, рассылка невозможна")
                return

            chat_ids = await db.get_chat_ids()
            if not chat_ids:
                logging.info("Нет подписанных пользователей для рассылки")
                return

            for chat_id in chat_ids:
                try:
                    await self.app.bot.send_message(
                        chat_id=chat_id,
                        text=msg,
                        parse_mode="Markdown",
                        reply_markup=reply_markup,
                    )
                except Exception as exc:
                    logging.error(f"Ошибка отправки пользователю {chat_id}: {exc}")

            logging.info(f"Отправлено {len(to_send)} слотов {len(chat_ids)} пользователям")

        except RateLimitException as e:
            # Устанавливаем таймер блокировки, но сам бот не зависает
            self.locked_until = time.time() + e.wait_time
            logging.warning(f"🛑 Ошибка 429. Парсинг ВБ приостановлен на {e.wait_time} сек...")
        except Exception as e:
            logging.error(f"Ошибка в tick: {e}", exc_info=True)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Приветствие + сохранение chat_id в Supabase"""
    chat_id = update.effective_chat.id
    db: SupabaseDB = context.bot_data.get("db")

    if db:
        ok = await db.add_user(chat_id)
        if ok:
            logging.info(f"Новый пользователь сохранён: {chat_id}")
    else:
        logging.warning("Supabase DB не инициализирована при /start")

    welcome_text = (
        "👋 Приветствую! Я Telegram-бот для автоматизации аналитики продаж на маркетплейсе Wildberries.\n\n"
        "Моя задача: автоматический поиск дешёвых (бесплатных) слотов для поставок на склады.\n"
        f"⏱ Текущий интервал проверки: каждые {CHECK_INTERVAL_MINUTES} минут.\n\n"
        "Ожидайте уведомлений о новых слотах!"
    )
    await update.message.reply_text(welcome_text)


async def dummy_book_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик нажатия на кнопку для комиссии"""
    query = update.callback_query
    await query.answer("✅ Слот успешно забронирован! (Демо-режим)")


async def main():
    # Проверка обязательных переменных
    if not TELEGRAM_TOKEN:
        raise ValueError("TELEGRAM_TOKEN не задан. Проверьте файл .env")
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise ValueError("SUPABASE_URL и SUPABASE_KEY должны быть заданы в .env")

    db = SupabaseDB(SUPABASE_URL, SUPABASE_KEY)

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.bot_data["db"] = db

    checker = SlotChecker(app)
    app.bot_data["checker"] = checker

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(dummy_book_callback, pattern="^book_dummy$"))

    # Бот сразу начинает работать в ТГ, а парсер проверяет ВБ раз в интервал
    app.job_queue.run_repeating(
        checker.tick,
        interval=timedelta(minutes=CHECK_INTERVAL_MINUTES),
        first=1,
    )

    await app.initialize()
    await app.start()
    await app.updater.start_polling()

    logging.info("🚀 Бот запущен. Ожидаем пользователей...")

    try:
        while True:
            await asyncio.sleep(3600)
    except KeyboardInterrupt:
        pass
    finally:
        await app.stop()
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
