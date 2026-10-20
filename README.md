# Telegram monitor

## First authorization

```bash
cp .env.example .env
# fill .env
python scripts/login.py
```

## Run with Docker

```bash
docker compose up -d --build
```

## Admin bot

Send `/start` to the bot from the configured admin account.
