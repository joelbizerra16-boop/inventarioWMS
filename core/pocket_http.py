"""Utilitários HTTP compartilhados do fluxo Pocket (views, mixins, middleware)."""

import logging

from django.http import JsonResponse

logger = logging.getLogger(__name__)


def is_pocket_ajax(request) -> bool:
    """Detecção única de requisições Pocket que devem receber JSON."""
    if not request.path.startswith('/pocket/'):
        return False
    if request.method == 'POST' and post_pocket_ciclico(request):
        return True
    return requisicao_ajax_pocket(request)


def requisicao_ajax_pocket(request) -> bool:
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return True
    if request.POST.get('pocket_ajax') == '1':
        return True
    if request.GET.get('pocket_ajax') == '1':
        return True
    accept = (request.headers.get('Accept') or '').lower()
    return 'application/json' in accept and 'text/html' not in accept


def post_pocket_ciclico(request) -> bool:
    return request.method == 'POST' and request.path.rstrip('/').endswith('/pocket/ciclico')


def deve_responder_json_pocket(request) -> bool:
    return is_pocket_ajax(request)


def log_pocket_post(request) -> None:
    logger.info(
        'POCKET_POST user=%s ajax_header=%s pocket_ajax=%s path=%s method=%s acao=%s',
        getattr(request.user, 'pk', None) if getattr(request.user, 'is_authenticated', False) else None,
        request.headers.get('X-Requested-With'),
        request.POST.get('pocket_ajax'),
        request.path,
        request.method,
        request.POST.get('acao'),
    )


def resposta_json_pocket(request, payload, *, status=200):
    response = JsonResponse(payload, status=status)
    logger.info(
        'POCKET_AJAX_RESPONSE status=%s content_type=%s ajax=%s usuario=%s path=%s acao=%s',
        response.status_code,
        response.get('Content-Type'),
        deve_responder_json_pocket(request),
        getattr(request.user, 'pk', None) if getattr(request.user, 'is_authenticated', False) else None,
        request.path,
        request.POST.get('acao'),
    )
    return response


def json_erro_pocket(request, mensagem: str, *, status: int = 400, **extra):
    payload = {'ok': False, 'message': mensagem, **extra}
    return resposta_json_pocket(request, payload, status=status)


def post_pocket_monitorado(request) -> bool:
    return request.method == 'POST' and is_pocket_ajax(request)


def log_pocket_redirect_debug(request, *, fase='request', response=None) -> None:
    dados = {
        'fase': fase,
        'user_id': getattr(request.user, 'pk', None) if getattr(request.user, 'is_authenticated', False) else None,
        'authenticated': getattr(request.user, 'is_authenticated', False),
        'path': request.path,
        'method': request.method,
        'ajax': request.headers.get('X-Requested-With'),
        'pocket_ajax': request.POST.get('pocket_ajax'),
        'acao': request.POST.get('acao'),
        'referer': request.META.get('HTTP_REFERER', ''),
    }
    if response is not None:
        dados['status'] = response.status_code
        dados['content_type'] = response.get('Content-Type', '')
        dados['location'] = response.get('Location', '')
    logger.error('POCKET_REDIRECT_DEBUG %s', dados)
