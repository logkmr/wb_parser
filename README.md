# WB Parser Bot

Telegram-бот для автоматического мониторинга бесплатных слотов поставок на склады Wildberries (Россия).

## Возможности

- **Автоматический поиск слотов** с коэффициентом 0 или 1 (бесплатные/дешёвие)
- **Фильтрация по региону** — только склады в России (исключаются Казахстан, Беларусь и др.)
- **Рассылка уведомлений** всем подписанным пользователям через Telegram
- **Хранение подписчиков** в Supabase (PostgreSQL)
- **Обработка rate limit** от Wildberries с автоматической паузой
- **Приоритизация складов** — Москва и МО показываются первыми

## Стек технологий

- **python-telegram-bot** — работа с Telegram Bot API
- **httpx** — асинхронные запросы к Supabase
- **requests** — синхронные запросы к API Wildberries
- **Supabase** — облачная PostgreSQL БД для хранения `chat_id` пользователей

## Установка

1. Клонируйте репозиторий:

```bash
git clone https://github.com/username/wb_parser.git
cd wb_parser
```

2. Создайте виртуальное окружение и установите зависимости:

```bash
python -m venv venv
# Windows:
venv\Scripts\activate
# Linux/macOS:
source venv/bin/activate

pip install -r requirements.txt
```

3. Создайте файл `.env` на основе `.env.example`:

```bash
cp .env.example .env
```

4. Отредактируйте `.env`, добавив свои токены:

```env
TELEGRAM_TOKEN=your_telegram_bot_token
WB_API_TOKEN=your_wildberries_api_token
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_KEY=your_supabase_anon_key
```

### Получение токенов

- **TELEGRAM_TOKEN** — создайте бота через [@BotFather](https://t.me/BotFather)
- **WB_API_TOKEN** — личный кабинет продавца WB → Настройки → Доступ к API
- **SUPABASE_URL / SUPABASE_KEY** — создайте проект на [supabase.com](https://supabase.com), затем в разделе **Table Editor** создайте таблицу `users` с колонкой `chat_id` (тип `bigint`, primary key)

## Запуск

```bash
python bot.py
```

Бот начнёт работу в режиме polling. При первом запуске парсер подгрузит список российских складов, а затем будет проверять API Wildberries каждые 5 минут.

## Использование

1. Найдите своего бота в Telegram и отправьте команду `/start`
2. Бот сохранит ваш `chat_id` в Supabase и начнёт присылать уведомления о новых свободных слотах
3. При появлении слота вы получите сообщение вида:

```
3 новых свободных слота (коэф 0/1)

✅ Коледино (ID 123)
📅 2026-05-18
📊 Коэффициент: 0

✅ Электросталь (ID 456)
📅 2026-05-19
📊 Коэффициент: 1
```

## Тестирование лимитов WB

Если основной бот не запускается из-за ошибки 429 (rate limit), используйте `test.py` для проверки текущего статуса:

```bash
python test.py
```

> Не забудьте вставить свой `WB_TOKEN` в файл `test.py` перед запуском.

## Структура проекта

```
wb_parser/
├── bot.py              # Основной бот
├── test.py             # Проверка rate limit Wildberries
├── requirements.txt    # Зависимости Python
├── .env.example        # Шаблон переменных окружения
├── .gitignore          # Исключения для Git
├── LICENSE             # MIT License
└── README.md           # Документация
```

## Переменные окружения

| Переменная | Описание | Обязательная |
|------------|----------|--------------|
| `TELEGRAM_TOKEN` | Токен Telegram бота от @BotFather | Да |
| `WB_API_TOKEN` | Токен API Wildberries | Да |
| `SUPABASE_URL` | URL проекта Supabase | Да |
| `SUPABASE_KEY` | anon/public ключ Supabase | Да |

## Лицензия

[MIT](LICENSE)
