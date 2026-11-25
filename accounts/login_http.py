"""Respostas do login operacional: credencial, CSRF e falha técnica."""

import logging
import secrets
import sys

from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import render
from django.utils.html import escape

from accounts.forms import OperacionalAuthenticationForm
from core.logging_auditoria import registrar_excecao

logger = logging.getLogger('inventario.auditoria')

MENSAGEM_CSRF = (
    'A sessão do formulário expirou. Atualize a página e tente novamente.'
)
MENSAGEM_REDE = (
    'Não foi possível conectar. Verifique a conexão e tente novamente.'
)


def identificador_atendimento() -> str:
    return f'ATD-{secrets.token_hex(4).upper()}'


def mensagem_erro_interno(identificador: str) -> str:
    return (
        f'Ocorreu um erro interno. Anote o código {identificador} '
        'e contate o suporte.'
    )


def _username_seguro(request) -> str:
    try:
        valor = request.POST.get('username', '')
    except Exception:
        return ''
    return str(valor).replace('\n', ' ').replace('\r', ' ')[:150]


def _render_login(request, *, status: int, form=None, texto_fallback: str = ''):
    if form is None:
        try:
            form = OperacionalAuthenticationForm(request)
        except Exception:
            form = None
    try:
        if form is None:
            raise RuntimeError('formulario de login indisponivel')
        return render(
            request,
            'accounts/login.html',
            {'form': form},
            status=status,
        )
    except Exception:
        logger.exception('Falha ao renderizar a tela de login')
        texto = escape(texto_fallback or 'Não foi possível exibir o login.')
        html = (
            '<!DOCTYPE html><html lang="pt-br"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            '<title>Login</title></head><body>'
            f'<p>{texto}</p>'
            '<p><a href="/accounts/login/">Atualizar a página</a></p>'
            '</body></html>'
        )
        return HttpResponse(html, status=status, content_type='text/html; charset=utf-8')


def resposta_login_mensagem(request, mensagem: str, *, status: int, form=None):
    try:
        messages.error(request, mensagem)
    except Exception:
        logger.exception('Falha ao registrar mensagem de login')
    return _render_login(
        request,
        status=status,
        form=form,
        texto_fallback=mensagem,
    )


def resposta_erro_interno_login(request, exc: BaseException | None = None):
    identificador = identificador_atendimento()
    if exc is None:
        exc = sys.exc_info()[1] or RuntimeError('excecao ausente')
    registrar_excecao(
        'login_erro_interno',
        identificador=identificador,
        exc=exc,
        usuario=_username_seguro(request),
        path=getattr(request, 'path', ''),
    )
    return resposta_login_mensagem(
        request,
        mensagem_erro_interno(identificador),
        status=500,
    )


def csrf_failure(request, reason=''):
    if (getattr(request, 'path', '') or '').startswith('/accounts/login'):
        logger.warning('login_csrf path=%s', request.path)
        return resposta_login_mensagem(request, MENSAGEM_CSRF, status=403)
    from django.views.csrf import csrf_failure as falha_padrao

    return falha_padrao(request, reason=reason)
