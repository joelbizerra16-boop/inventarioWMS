#!/usr/bin/env bash
set -o errexit
set -o pipefail

echo "==> Instalando dependências"
pip install --upgrade pip
pip install -r requirements.txt

echo "==> Coletando arquivos estáticos"
export DJANGO_SETTINGS_MODULE=core.settings
python manage.py collectstatic --noinput --clear

if [ ! -d staticfiles ] || [ -z "$(ls -A staticfiles 2>/dev/null)" ]; then
  echo "ERRO: collectstatic não gerou arquivos em staticfiles/"
  exit 1
fi

echo "==> staticfiles OK ($(find staticfiles -type f | wc -l | tr -d ' ') arquivos)"

echo "==> Aplicando migrations"
python manage.py migrate --noinput

echo "==> Build concluído"
