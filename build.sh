#!/usr/bin/env bash
set -o errexit
set -o pipefail

echo "==> Instalando dependências"
pip install --upgrade pip
pip install -r requirements.txt

echo "==> Coletando arquivos estáticos"
python manage.py collectstatic --noinput

echo "==> Aplicando migrations"
python manage.py migrate --noinput

echo "==> Build concluído"
