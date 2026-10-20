# Telegram monitor

## Environment

Copy `.env.example` to `.env` and fill it.

Required:

- `BOT_TOKEN`
- `ADMIN_ID`
- `API_ID`
- `API_HASH`

## First authorization

With Python installed locally:

```bash
python scripts/login.py
```

Without Python on the server, run authorization in Docker:

```bash
docker compose run --rm app python scripts/login.py
```

## Run

```bash
docker compose up -d --build
```

Restart after reboot is handled by `restart: unless-stopped`.

## Admin bot

Send `/start` to the bot from the configured `ADMIN_ID` account.
