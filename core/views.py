from urllib.parse import urlparse

from django.contrib import messages
from django.http import HttpResponseServerError
from django.shortcuts import redirect
from django.urls import reverse

from accounts.services.perfil import usuario_e_operador_pocket
from core.services.exclusao import MENSAGEM_ERRO_INESPERADO, MENSAGEM_NAO_ENCONTRADO

MENSAGEM_ERRO_HTTP = 'Erro interno. Contate o suporte.'


def _paths_equivalent(path_a: str, path_b: str) -> bool:
    return (path_a or '/').rstrip('/') == (path_b or '/').rstrip('/')


def _referer_seguro(request) -> str | None:
    referer = request.META.get('HTTP_REFERER')
    if not referer:
        return None
    referer_path = urlparse(referer).path or '/'
    if _paths_equivalent(referer_path, request.path):
        return None
    return referer


def _destino_pos_erro(request) -> str:
    if request.user.is_authenticated and usuario_e_operador_pocket(request.user):
        return reverse('pocket:selecionar')
    return reverse('home')


def handler404(request, exception):
    messages.error(request, MENSAGEM_NAO_ENCONTRADO)
    if request.user.is_authenticated and usuario_e_operador_pocket(request.user):
        return redirect(reverse('pocket:selecionar'))
    referer = _referer_seguro(request)
    if referer:
        return redirect(referer)
    destino = _destino_pos_erro(request)
    if _paths_equivalent(request.path, destino):
        return redirect(reverse('accounts:login'))
    return redirect(destino)


def handler500(request):
    messages.error(request, MENSAGEM_ERRO_INESPERADO)
    destino = _destino_pos_erro(request)
    if _paths_equivalent(request.path, destino):
        return HttpResponseServerError(MENSAGEM_ERRO_HTTP)
    return redirect(destino)
