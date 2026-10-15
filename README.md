# Intens Bot — Telegram-бот для продажи тренировок

Telegram-бот для продажи пяти отдельных тренировок с оплатой через YooKassa,
автоматической выдачей материалов и защищённым доступом в приватные группы.

## Возможности

- Каталог из 5 тренировок с описаниями и ценами
- Оплата через YooKassa с webhook-подтверждением
- Автоматическая выдача материалов (видео, PDF, документы, фото, аудио, текст)
- Приватные Telegram-группы с заявками на вступление
- Админ-панель внутри Telegram (/admin)
- Статистика продаж
- Надёжная очередь задач (transactional outbox)
- Повторная выдача материалов

## Установка

### Локальный запуск

1. Клонируйте репозиторий:

```bash
git clone https://github.com/nikkes174/intens_bot.git
cd intens_bot
git checkout bulki_bot
```

2. Создайте виртуальное окружение:

```bash
python -m venv .venv
source .venv/bin/activate  # Linux/Mac
# или
.venv\Scripts\activate  # Windows
```

3. Установите зависимости:

```bash
pip install -r requirements-dev.txt
```

4. Создайте файл `.env`:

```bash
cp .env.example .env
```

5. Заполните `.env` своими значениями:

```env
BOT_TOKEN=ваш_токен_бота
ADMINS=ваш_telegram_id
DB=sqlite+aiosqlite:///bot.db
YOOKASSA_SHOP_ID=ваш_shop_id
YOOKASSA_SECRET_KEY=ваш_secret_key
YOOKASSA_RETURN_URL=https://t.me/your_bot_username
YOOKASSA_VAT_CODE=1
WEBHOOK_HOST=0.0.0.0
WEBHOOK_PORT=8000
LOG_LEVEL=INFO
```

6. Запустите бота:

```bash
python -m bot
```

### Docker

1. Создайте файл `.env` (см. выше)

2. Запустите через Docker Compose:

```bash
docker-compose up -d
```

3. Проверьте логи:

```bash
docker-compose logs -f bot
```

## YooKassa Webhook

1. В личном кабинете YooKassa укажите URL webhook:

```
https://ваш_домен/webhooks/yookassa
```

2. Убедитесь, что бот имеет публичный HTTPS-адрес (например, через ngrok или сервер)

3. YooKassa будет отправлять уведомления о событиях:
   - `payment.succeeded` — успешная оплата
   - `payment.canceled` — отмена платежа

## Telegram-группы

1. Создайте приватную группу для каждой тренировки
2. Добавьте бота администратором с правами:
   - Приглашение пользователей
   - Одобрение заявок на вступление
3. В админ-панели бота выберите "Привязать группу"
4. Отправьте `/chatid` в группу, чтобы получить chat_id
5. Введите chat_id в админ-панели

## Админ-панель

1. Отправьте `/admin` в бота
2. Доступные разделы:
   - Управление тренировками — изменение названий, описаний, цен
   - Материалы тренировок — загрузка и управление файлами
   - Пользователи и покупки — поиск пользователей, история заказов
   - Статистика — продажи, выручка, отменённые заказы

## Тестирование

```bash
# Все тесты
pytest -v

# Конкретный модуль
pytest tests/test_payments.py -v

# С покрытием
pytest --cov=tgbot tests/
```

## Структура проекта

```
tgbot/
├── config.py          # Конфигурация
├── db/
│   ├── models.py      # SQLAlchemy модели
│   ├── db.py          # Подключение к БД
│   ├── repositories/  # Репозитории для работы с данными
│   └── seed.py        # Начальные данные
├── handlers/
│   ├── user.py        # Пользовательские обработчики
│   └── admin.py       # Админ-панель
├── keyboards/         # Клавиатуры
├── services/          # Бизнес-логика
│   ├── payment.py     # Интеграция с YooKassa
│   ├── delivery.py    # Выдача материалов
│   ├── access.py      # Управление доступом
│   └── outbox.py      # Очередь задач
├── webhooks/          # Webhook API
└── middlewares/       # Middleware
tests/                 # Тесты
alembic/               # Миграции БД
bot.py                 # Точка входа
docker-compose.yml     # Docker Compose
Dockerfile             # Docker образ
```

## Миграции БД

```bash
# Создать миграцию
alembic revision --autogenerate -m "описание"

# Применить миграции
alembic upgrade head

# Откатить миграцию
alembic downgrade -1
```

## Переменные окружения

| Переменная | Описание |
|---|---|
| BOT_TOKEN | Токен Telegram-бота |
| ADMINS | ID администраторов (через запятую) |
| DB | URL базы данных |
| YOOKASSA_SHOP_ID | ID магазина YooKassa |
| YOOKASSA_SECRET_KEY | Секретный ключ YooKassa |
| YOOKASSA_RETURN_URL | URL возврата после оплаты |
| YOOKASSA_VAT_CODE | Код НДС |
| WEBHOOK_HOST | Хост webhook-сервера |
| WEBHOOK_PORT | Порт webhook-сервера |
| LOG_LEVEL | Уровень логирования |

## Лицензия

MIT
