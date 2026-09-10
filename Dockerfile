# InventarioWMS — imagem de produção (Gunicorn + WhiteNoise)
FROM python:3.12-slim-bookworm AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DJANGO_SETTINGS_MODULE=core.settings

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libpq5 \
        curl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 1000 app \
    && useradd --system --uid 1000 --gid app --home /app --shell /usr/sbin/nologin app

COPY requirements.txt .
RUN pip install --upgrade pip \
    && pip install -r requirements.txt

COPY --chown=app:app . .

RUN mkdir -p /app/media /app/staticfiles /app/logs \
    && chown -R app:app /app/media /app/staticfiles /app/logs \
    && SECRET_KEY=build-only-collectstatic-key-not-for-runtime-use-xxxxxxxx \
       DEBUG=False \
       SECURE_SSL_REDIRECT=False \
       ALLOWED_HOSTS=localhost \
       DATABASE_URL=sqlite:////tmp/build.sqlite3 \
       python manage.py collectstatic --noinput

COPY --chown=app:app deploy/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/accounts/login/ >/dev/null || exit 1

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["gunicorn", "core.wsgi:application", "--config", "deploy/gunicorn.conf.py"]
