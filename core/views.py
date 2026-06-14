from django.contrib import messages
from django.http import HttpResponseServerError
from django.shortcuts import redirect
from django.urls import reverse

from accounts.services.perfil import usuario_e_operador_pocket
from core.services.exclusao import MENSAGEM_ERRO_INESPERADO, MENSAGEM_NAO_ENCONTRADO


def _destino_pos_erro(request) -> str:
    if request.user.is_authenticated and usuario_e_operador_pocket(request.user):
        return reverse('pocket:selecionar')
    return reverse('home')


def handler404(request, exception):
    messages.error(request, MENSAGEM_NAO_ENCONTRADO)
    if request.user.is_authenticated and usuario_e_operador_pocket(request.user):
        return redirect(reverse('pocket:selecionar'))
    referer = request.META.get('HTTP_REFERER')
    if referer:
        return redirect(referer)
    return redirect(_destino_pos_erro(request))


def handler500(request):
    messages.error(request, MENSAGEM_ERRO_INESPERADO)
    if request.user.is_authenticated and usuario_e_operador_pocket(request.user):
        if request.path.startswith('/pocket/'):
            return HttpResponseServerError('Erro interno. Contate o suporte.')
        return redirect(reverse('pocket:selecionar'))
    return redirect(_destino_pos_erro(request))
