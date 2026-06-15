"""Smoke test de rotas principais (go-live). Uso: python scripts/smoke_test_routes.py"""
import os
import sys

import django

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
django.setup()

from django.test import Client
from django.urls import reverse

from accounts.test_utils import criar_usuario_teste

ROUTES = [
    ('LOGIN', 'accounts:login'),
    ('DASHBOARD', 'home'),
    ('PRODUTOS', 'produtos:lista'),
    ('POSICOES', 'posicoes:lista'),
    ('ESTOQUE SAP', 'estoque_sap:lista'),
    ('ESTOQUE FISICO', 'estoque_fisico:lista'),
    ('INVENTARIO', 'inventario:lista'),
    ('POCKET', 'pocket:selecionar'),
    ('CICLICO', 'ciclico'),
    ('HISTORICO', 'historico_unificado'),
    ('CONFRONTO', 'confronto'),
    ('APROVACAO', 'aprovacao'),
]

STATIC_FILES = [
    '/static/css/dashboard.css',
    '/static/js/pocket-bipagem.js',
]


def main():
    client = Client()
    print('=== SEM LOGIN ===')
    for label, name in ROUTES:
        path = reverse(name)
        response = client.get(path)
        print(f'{label:16} {path:28} -> {response.status_code}')

    user, _ = criar_usuario_teste(username='smoke_admin', password='SmokeTest123!')
    client.force_login(user)

    print('\n=== COM LOGIN (admin) ===')
    issues = []
    for label, name in ROUTES:
        path = reverse(name)
        response = client.get(path)
        status = response.status_code
        body = response.content.decode('utf-8', errors='replace')
        problem = None
        if status >= 500:
            problem = 'SERVER ERROR'
        elif status == 404:
            problem = 'NOT FOUND'
        elif status not in (200, 302):
            problem = f'HTTP {status}'
        elif status == 200 and ('Traceback' in body or 'TemplateDoesNotExist' in body):
            problem = 'ERROR IN BODY'
        flag = 'OK' if problem is None else 'FAIL'
        print(f'{label:16} {path:28} -> {status} {flag}' + (f' ({problem})' if problem else ''))
        if problem:
            issues.append((label, path, status, problem))

    print('\n=== STATIC (via staticfiles finders em DEBUG) ===')
    for static_path in STATIC_FILES:
        response = client.get(static_path)
        ok = response.status_code == 200
        print(f'{static_path:40} -> {response.status_code} {"OK" if ok else "FAIL"}')
        if not ok:
            issues.append(('STATIC', static_path, response.status_code, 'MISSING'))

    print('\n=== RESUMO ===')
    if issues:
        print(f'FALHAS: {len(issues)}')
        for item in issues:
            print(' -', item)
        sys.exit(1)
    print('Todas as rotas principais OK')
    sys.exit(0)


if __name__ == '__main__':
    main()
