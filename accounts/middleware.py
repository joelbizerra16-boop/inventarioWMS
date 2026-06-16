from django.shortcuts import redirect
from django.urls import reverse

from accounts.services.perfil import url_permitida_para_operador, usuario_e_operador_pocket
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
