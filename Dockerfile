# Runtime image for the TG-Threader Telegram bot.
# Published to GitHub Container Registry by .github/workflows/publish-image.yml.
#
# Required environment: BOT_TOKEN, DATABASE_URL.
# Optional: OWNER_ID, LOCALE (default en), LOG_LEVEL (default INFO),
# RUN_MIGRATIONS (default 1; set to 0 to skip `alembic upgrade head`).

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    RUN_MIGRATIONS=1

WORKDIR /app

RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin bot

COPY pyproject.toml README.md ./
COPY bot ./bot
RUN pip install --upgrade pip \
    && pip install --root-user-action=ignore . \
    && rm -rf build *.egg-info

COPY alembic.ini ./
COPY migrations ./migrations
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod 755 /entrypoint.sh && chown -R bot:bot /app

USER bot

ENTRYPOINT ["/entrypoint.sh"]
