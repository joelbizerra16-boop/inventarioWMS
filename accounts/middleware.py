from django.http import HttpResponseForbidden
from django.shortcuts import redirect
from django.urls import reverse

from accounts.services.perfil import (
    url_permitida_para_auditor,
    url_permitida_para_operador,
    usuario_e_auditor,
    usuario_e_operador_pocket,
)
from core.logging_auditoria import ip_do_request, registrar_evento
from core.pocket_http import deve_responder_json_pocket, json_erro_pocket


class OperadorPocketMiddleware:
    """Restringe o perfil OPERADOR ao fluxo Pocket e pré-cadastros."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = request.user
        if user.is_authenticated and usuario_e_operador_pocket(user):
            if not url_permitida_para_operador(request.path):
                if deve_responder_json_pocket(request):
                    return json_erro_pocket(
                        request,
                        'Acesso restrito ao fluxo Pocket.',
                        status=403,
                    )
                return redirect(reverse('pocket:selecionar'))
        return self.get_response(request)


class AuditorMiddleware:
    """Restringe o perfil AUDITOR ao Painel do Auditor.

    Qualquer outra rota (telas internas, APIs, exportações) responde 403 e a
    tentativa é registrada. A raiz leva ao início do painel.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = request.user
        if user.is_authenticated and usuario_e_auditor(user):
            if request.path == '/':
                return redirect(reverse('auditor:inicio'))
            if not url_permitida_para_auditor(request.path):
                registrar_evento(
                    'auditor_acesso_negado',
                    usuario=user,
                    ip=ip_do_request(request),
                    path=request.path[:200],
                    metodo=request.method,
                )
                return HttpResponseForbidden('Acesso restrito ao Painel do Auditor.')
        return self.get_response(request)
