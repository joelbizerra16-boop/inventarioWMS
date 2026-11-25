from django.contrib import messages
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views import View

from accounts.mixins import AcessoOperacionalMixin
from core.categorias import definir_categoria_sessao, rotulo_categoria
from core.logging_auditoria import registrar_evento


class SelecionarCategoriaView(AcessoOperacionalMixin, View):
    """Troca a categoria de inventário da sessão (não altera inventários gravados)."""

    http_method_names = ['post']

    def post(self, request):
        destino = request.POST.get('next') or request.META.get('HTTP_REFERER') or ''
        if not url_has_allowed_host_and_scheme(
            destino,
            allowed_hosts={request.get_host()},
            require_https=request.is_secure(),
        ):
            destino = reverse('home')
        try:
            categoria = definir_categoria_sessao(request, request.POST.get('categoria'))
        except ValueError:
            messages.error(request, 'Categoria de inventário inválida.')
            return redirect(destino)
        registrar_evento(
            'categoria_sessao_alterada',
            usuario=request.user,
            categoria=categoria,
        )
        messages.info(request, f'Categoria selecionada: {rotulo_categoria(categoria)}.')
        return redirect(destino)
