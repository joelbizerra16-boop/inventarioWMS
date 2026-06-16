"""Middleware de proteção contra redirects indevidos no fluxo Pocket."""

import logging

from core.pocket_http import (
    json_erro_pocket,
    log_pocket_redirect_debug,
    post_pocket_monitorado,
)

logger = logging.getLogger(__name__)


class PocketPostRedirectGuardMiddleware:
    """Bloqueia qualquer redirect HTTP em POST /pocket/* e registra forense."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if post_pocket_monitorado(request):
            log_pocket_redirect_debug(request, fase='request')

        response = self.get_response(request)

        if post_pocket_monitorado(request):
            log_pocket_redirect_debug(request, fase='response', response=response)
            if 300 <= response.status_code < 400:
                location = response.get('Location', '')
                logger.error(
                    'POCKET_REDIRECT_BLOCKED status=%s location=%s path=%s acao=%s user=%s',
                    response.status_code,
                    location,
                    request.path,
                    request.POST.get('acao'),
                    getattr(request.user, 'pk', None),
                )
                return json_erro_pocket(
                    request,
                    f'Redirect indevido bloqueado ({response.status_code} → {location})',
                    status=500,
                    redirect_location=location,
                )
        return response
