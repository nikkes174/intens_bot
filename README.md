# Telegram monitor

## Local Windows setup with uv

1. Install uv.
2. Copy `.env.example` to `.env` and fill `BOT_TOKEN`, `ADMIN_ID`, `API_ID`, `API_HASH`.
3. Sync dependencies:

```powershell
uv sync
```

4. Authorize Telethon:

```powershell
uv run python -m scripts.login
```

5. Run the application:

```powershell
uv run python -m app.main
```

Checks:

```powershell
uv run python -m compileall -q app scripts tests
uv run pytest -q
```

## Docker (VPS)

```powershell
docker compose up -d --build
```

Send `/start` to the bot from the configured `ADMIN_ID` account.
