#!/bin/sh
set -eu

# Entrypoint de produção.
# NÃO executa migrate/ensure_admin automaticamente (evita race entre workers
# e criação acidental de admin). Migrations: one-shot manual via compose run.

echo "==> InventarioWMS entrypoint: preparando runtime"

mkdir -p /app/media /app/logs

if [ "${RUN_COLLECTSTATIC_ON_START:-0}" = "1" ]; then
  echo "==> collectstatic (sob demanda)"
  python manage.py collectstatic --noinput
fi

echo "==> Iniciando: $*"
exec "$@"
