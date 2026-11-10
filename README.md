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

## Internet monitoring

The admin menu contains `🌐 Мониторинг интернета`. Web monitoring is independent
of Telegram monitoring and is enabled by default. Its main settings are
`WEB_MONITOR_ENABLED`, `WEB_MONITOR_INTERVAL`, `WEB_MONITOR_TIMEOUT`, and
`WEB_MONITOR_MAX_CONCURRENCY`; direct RSS feeds can be configured with
`WEB_RSS_URLS` as comma-separated `Source name|URL` values.

Webhook/WebSub support is disabled by default. To opt in, set
`WEB_WEBHOOK_ENABLED=true` and configure `WEB_WEBHOOK_SECRETS`; the server binds
to localhost unless `WEB_WEBHOOK_HOST` is changed. Put it behind an HTTPS
reverse proxy when exposing it to a provider.
