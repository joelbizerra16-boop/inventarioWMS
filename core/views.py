from django.contrib import messages
from django.shortcuts import redirect
from django.urls import reverse

from core.services.exclusao import MENSAGEM_ERRO_INESPERADO, MENSAGEM_NAO_ENCONTRADO


def handler404(request, exception):
    messages.error(request, MENSAGEM_NAO_ENCONTRADO)
    referer = request.META.get('HTTP_REFERER')
    if referer:
        return redirect(referer)
    return redirect(reverse('home'))


def handler500(request):
    messages.error(request, MENSAGEM_ERRO_INESPERADO)
    return redirect(reverse('home'))
