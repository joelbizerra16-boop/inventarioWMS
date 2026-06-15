import logging
import time
from urllib.parse import urlparse

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError
from django.db import connection
from django.db.models.deletion import ProtectedError
from django.http import Http404, HttpResponseServerError, JsonResponse
from django.shortcuts import redirect
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from accounts.services.perfil import usuario_e_operador_pocket
from core.services.perf_diagnostico import log_resumo_view
from core.services.exclusao import (
    MENSAGEM_ERRO_INESPERADO,
    MENSAGEM_NAO_ENCONTRADO,
    MENSAGEM_PERMISSAO,
    mensagem_de_excecao_operacional,
)

logger = logging.getLogger(__name__)


class DiagnosticoPerformanceMiddleware:
    """Medição temporária de performance por requisição HTTP."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        inicio = time.perf_counter()
        force_debug_anterior = connection.force_debug_cursor
        connection.force_debug_cursor = True
        with CaptureQueriesContext(connection) as contexto_queries:
            response = self.get_response(request)
        connection.force_debug_cursor = force_debug_anterior

        nome_view = getattr(getattr(request, 'resolver_match', None), 'view_name', None)
        nome_view = nome_view or request.path
        fim = time.perf_counter()

        log_resumo_view(
            nome_view=nome_view,
            captured_queries=list(contexto_queries.captured_queries),
            duracao_segundos=fim - inicio,
        )
        return response


class TratamentoExcecaoUsuarioMiddleware:
    """Converte exceções operacionais em mensagens amigáveis para o usuário."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_exception(self, request, exception):
        if not self._deve_tratar(request, exception):
            return None

        mensagem = self._mensagem_para_usuario(exception)
        destino = self._destino_seguro(request)

        if self._requisicao_ajax(request):
            status = 404 if isinstance(exception, Http404) else 400
            if isinstance(exception, PermissionDenied):
                status = 403
            return JsonResponse({'ok': False, 'message': mensagem}, status=status)

        messages.error(request, mensagem)
        destino_path = urlparse(destino).path if '://' in destino else destino
        if (destino_path or '/').rstrip('/') == (request.path or '/').rstrip('/'):
            return HttpResponseServerError('Erro interno. Contate o suporte.')
        return redirect(destino)

    def _deve_tratar(self, request, exception) -> bool:
        if request.path.startswith('/admin/'):
            return False

        return isinstance(
            exception,
            (
                ProtectedError,
                IntegrityError,
                ValidationError,
                Http404,
                PermissionDenied,
            ),
        )

    def _requisicao_ajax(self, request) -> bool:
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return True
        accept = request.headers.get('Accept', '')
        return 'application/json' in accept and 'text/html' not in accept

    def _mensagem_para_usuario(self, exception) -> str:
        if isinstance(exception, Http404):
            return MENSAGEM_NAO_ENCONTRADO
        if isinstance(exception, PermissionDenied):
            return MENSAGEM_PERMISSAO

        if isinstance(exception, (ProtectedError, IntegrityError, ValidationError)):
            logger.warning('Exceção operacional tratada: %s', exception, exc_info=exception)
            return mensagem_de_excecao_operacional(exception)

        logger.exception('Erro inesperado na requisição')
        return MENSAGEM_ERRO_INESPERADO

    def _destino_seguro(self, request) -> str:
        if request.user.is_authenticated and usuario_e_operador_pocket(request.user):
            return reverse('pocket:selecionar')
        referer = request.META.get('HTTP_REFERER')
        if referer and referer.startswith(request.build_absolute_uri('/')[:-1]):
            referer_path = urlparse(referer).path or '/'
            if referer_path.rstrip('/') != request.path.rstrip('/'):
                return referer
        return reverse('home')
