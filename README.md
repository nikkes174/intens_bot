# Telegram-бот «Что я ем?»

Простой Telegram-бот, который принимает фотографию еды, отправляет подготовленное изображение в OpenRouter и возвращает приблизительную оценку блюд, ингредиентов, веса, калорий и БЖУ.

Бот не требует регистрации и не использует базу данных. Изображение хранится только в оперативной памяти во время запроса. Результат для кнопки уточнения веса временно хранится в `MemoryStorage` и исчезает после перезапуска.

## Переменные окружения

Скопируйте `.env.example` в `.env` и заполните:

```env
BOT_TOKEN=токен_бота
OPENROUTER_API_KEY=ключ_openrouter
OPENROUTER_MODEL=provider/model:free
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
OPENROUTER_TIMEOUT=45
LOG_LEVEL=INFO
```

`OPENROUTER_MODEL` обязателен и нигде не зашит в коде. Можно указать любую доступную мультимодальную модель OpenRouter, в том числе бесплатную. Если `BOT_TOKEN`, `OPENROUTER_API_KEY` или `OPENROUTER_MODEL` не заданы, бот завершится с понятным сообщением конфигурации.

## Локальный запуск

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
python -m bot
```

Бот работает через Telegram long polling. Порты не открываются.

## Docker

```bash
cp .env.example .env
# заполните .env
docker compose up -d --build
docker compose logs -f eat_bot
```

Используется один контейнер `eat_bot`; PostgreSQL, Redis, SQLite, volumes и webhook-сервер не нужны.

## Как это работает

1. Пользователь отправляет фото напрямую или нажимает «📸 Распознать еду».
2. Бот загружает файл в память, проверяет его и преобразует Pillow в JPEG с максимальной стороной 1536 px.
3. OpenRouter получает мультимодальный запрос с JSON-инструкцией.
4. Pydantic проверяет ответ модели.
5. Бот показывает понятный текст на русском языке и кнопки для нового фото или пропорционального уточнения веса.

Оценки веса, калорий и БЖУ приблизительные. Скрытые ингредиенты, масло, соусы и способ приготовления могут быть неизвестны.

## Тесты

```bash
pytest -q
ruff check .
```

Тесты используют mock HTTP-клиент и не обращаются к Telegram или OpenRouter.
