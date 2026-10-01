"""Manifesto e service worker mínimos para abrir o sistema como app.

O service worker existe para o critério de instalação do Chrome (handler de
fetch). Não há modo offline. O handler não chama event.respondWith e não usa
Cache Storage: contagens, saldos, login e qualquer POST seguem só pela rede.

A marca é um I maiúsculo branco sobre o azul-marinho já usado na barra
(#1E293B). Não há logo da empresa no repositório.
"""

from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

# Cores já usadas na barra do login (sidebar.css / operador.css) e no fundo.
THEME_COLOR = '#1E293B'
BACKGROUND_COLOR = '#F1F5F9'

SERVICE_WORKER_JS = """\
/* Sistema de Inventário — service worker mínimo (instalação PWA).
 * Sem modo offline. Sem cache de contagem, saldo, autenticação ou escrita.
 * O listener de fetch nao intercepta a requisicao: o navegador usa a rede.
 */
self.addEventListener('install', function (event) {
    self.skipWaiting();
});

self.addEventListener('activate', function (event) {
    event.waitUntil(self.clients.claim());
});

self.addEventListener('fetch', function () {
    /* network-only: nao grava nem le Cache Storage */
});
"""


def _url_icone(nome: str) -> str:
    base = settings.STATIC_URL
    if not base.endswith('/'):
        base += '/'
    return f'{base}img/pwa/{nome}'


def icones_pwa():
    return [
        {
            'src': _url_icone('icon-192.png'),
            'sizes': '192x192',
            'type': 'image/png',
            'purpose': 'any',
        },
        {
            'src': _url_icone('icon-512.png'),
            'sizes': '512x512',
            'type': 'image/png',
            'purpose': 'any',
        },
        {
            'src': _url_icone('icon-maskable-512.png'),
            'sizes': '512x512',
            'type': 'image/png',
            'purpose': 'maskable',
        },
    ]


@require_GET
@never_cache
def manifest_pwa(request):
    payload = {
        'name': 'Sistema de Inventário',
        'short_name': 'Inventário',
        'description': 'Sistema de Inventário — Pocket',
        'id': '/',
        'start_url': reverse('accounts:login'),
        'scope': '/',
        'display': 'standalone',
        'background_color': BACKGROUND_COLOR,
        'theme_color': THEME_COLOR,
        'icons': icones_pwa(),
    }
    response = JsonResponse(payload)
    response['Content-Type'] = 'application/manifest+json'
    return response


@require_GET
@never_cache
def service_worker_pwa(request):
    response = HttpResponse(SERVICE_WORKER_JS, content_type='application/javascript')
    response['Service-Worker-Allowed'] = '/'
    return response
