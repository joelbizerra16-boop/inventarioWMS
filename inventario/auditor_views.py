"""Painel do Auditor: telas, API e exportação (somente contagens físicas).

Toda consulta parte de `conferencias_do_auditor(user)`: o ID da URL nunca é
usado sem esse escopo (evita IDOR). Respostas são privadas e sem cache.
"""

from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.http import HttpResponse, HttpResponseForbidden, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.cache import add_never_cache_headers, patch_cache_control
from django.views import View

from accounts.services.perfil import usuario_e_auditor
from inventario.services.auditor_painel import (
    STATUS_SKU,
    FiltrosAuditor,
    exportar_xlsx,
    montar_historico,
    montar_painel,
    serializar_painel,
)
from inventario.services.auditor_publicacao import (
    conferencias_do_auditor,
    obter_conferencia_auditor,
    processar,
)

LINHAS_POR_PAGINA = 50
EVENTOS_POR_PAGINA = 50
INTERVALO_ATUALIZACAO_SEGUNDOS = 8


def _negado(mensagem: str):
    return _privado(HttpResponseForbidden(mensagem))


def _privado(resposta):
    add_never_cache_headers(resposta)
    patch_cache_control(resposta, private=True)
    return resposta


class AuditorMixin(LoginRequiredMixin):
    login_url = reverse_lazy('accounts:login')

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if not usuario_e_auditor(request.user):
            return _negado('Painel exclusivo do perfil Auditor.')
        try:
            return super().dispatch(request, *args, **kwargs)
        except PermissionDenied as exc:
            # Resposta 403 explícita (o middleware geral converte exceções em redirecionamento).
            return _negado(str(exc) or 'Acesso negado.')

    def conferencia(self, pk):
        return obter_conferencia_auditor(self.request.user, pk, self.request.path)


class AuditorInicioView(AuditorMixin, View):
    def get(self, request):
        conferencias = list(conferencias_do_auditor(request.user).order_by('-pk'))
        if len(conferencias) == 1:
            return redirect('auditor:painel', pk=conferencias[0].pk)
        return _privado(render(request, 'inventario/auditor/inicio.html', {'conferencias': conferencias}))


def logo_brida() -> str | None:
    """Caminho estático do logo oficial BRIDA, se o arquivo existir no projeto."""
    from django.contrib.staticfiles import finders

    for caminho in ('img/brida/logo-brida.svg', 'img/brida/logo-brida.png'):
        if finders.find(caminho):
            return caminho
    return None


def _contexto(request, comparativo, previa=False):
    """Mesmo contexto para o auditor e para a prévia do gestor (mesmas regras de escopo)."""
    # Liberação de lotes vencidos também a cada leitura (idempotente).
    processar(comparativo_id=comparativo.pk)
    filtros = FiltrosAuditor.de_parametros(request.GET)
    painel = montar_painel(comparativo, filtros)
    historico = montar_historico(painel)
    params = request.GET.copy()
    for chave in ('pp', 'ps', 'ph', 'parcial', 'versao', 'forcar', 'aba', 'ge'):
        params.pop(chave, None)
    filtros_qs = params.urlencode()
    sem_operador = params.copy()
    for chave in ('operador', 'etapa'):
        sem_operador.pop(chave, None)
    return painel, {
        'comparativo': comparativo,
        'painel': painel,
        'linhas_pagina': Paginator(painel.linhas_filtradas, LINHAS_POR_PAGINA).get_page(request.GET.get('pp')),
        'skus_pagina': Paginator(painel.skus, LINHAS_POR_PAGINA).get_page(request.GET.get('ps')),
        'historico_pagina': Paginator(historico, EVENTOS_POR_PAGINA).get_page(request.GET.get('ph')),
        'filtros': filtros,
        'filtros_qs': filtros_qs,
        'filtros_sem_operador': sem_operador.urlencode(),
        'status_opcoes': list(STATUS_SKU.items()),
        'aba': request.GET.get('aba') if request.GET.get('aba') in ('posicao', 'sku', 'historico') else 'posicao',
        'intervalo': INTERVALO_ATUALIZACAO_SEGUNDOS,
        'conferencias': [] if previa else conferencias_do_auditor(request.user).order_by('-pk'),
        'agora': timezone.localtime(),
        'logo_brida': logo_brida(),
        'previa': previa,
        'urls': {
            'conteudo': reverse(
                'comparativo:auditoria_previa_conteudo' if previa else 'auditor:conteudo', args=[comparativo.pk],
            ),
            'limpar': reverse('comparativo:auditoria_previa' if previa else 'auditor:painel', args=[comparativo.pk]),
            'exportar': reverse('comparativo:auditoria_previa_exportar' if previa else 'auditor:exportar', args=[comparativo.pk]),
            'voltar': reverse('comparativo:auditoria', args=[comparativo.pk]) if previa else None,
        },
    }


def resposta_conteudo(request, comparativo, previa=False):
    painel, contexto = _contexto(request, comparativo, previa=previa)
    if request.GET.get('versao') == painel.versao and request.GET.get('forcar') != '1':
        resposta = HttpResponse(status=204)
    else:
        resposta = render(request, 'inventario/auditor/_conteudo.html', contexto)
    resposta['X-Auditor-Versao'] = painel.versao
    return _privado(resposta)


class AuditorPainelView(AuditorMixin, View):
    def get(self, request, pk):
        comparativo = self.conferencia(pk)
        _painel, contexto = _contexto(request, comparativo)
        return _privado(render(request, 'inventario/auditor/painel.html', contexto))


class AuditorConteudoView(AuditorMixin, View):
    """Fragmento atualizado pelo polling (mesmo contexto da página)."""

    def get(self, request, pk):
        return resposta_conteudo(request, self.conferencia(pk))


class AuditorApiView(AuditorMixin, View):
    def get(self, request, pk):
        comparativo = self.conferencia(pk)
        processar(comparativo_id=comparativo.pk)
        painel = montar_painel(comparativo, FiltrosAuditor.de_parametros(request.GET))
        pagina = Paginator(painel.linhas_filtradas, LINHAS_POR_PAGINA).get_page(request.GET.get('page'))
        return _privado(JsonResponse(serializar_painel(painel, list(pagina.object_list))))


def resposta_exportacao(request, comparativo):
    processar(comparativo_id=comparativo.pk)
    painel = montar_painel(comparativo, FiltrosAuditor.de_parametros(request.GET))
    visao = 'sku' if request.GET.get('aba') == 'sku' else 'posicao'
    conteudo = exportar_xlsx(painel, request.user, visao)
    nome = f'Auditoria_conferencia_{comparativo.pk}_{"por_sku" if visao == "sku" else "por_posicao"}_{timezone.localtime():%d-%m-%Y_%H%M}.xlsx'
    resposta = HttpResponse(
        conteudo,
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    resposta['Content-Disposition'] = f'attachment; filename="{nome}"'
    return _privado(resposta)


class AuditorExportarView(AuditorMixin, View):
    def get(self, request, pk):
        return resposta_exportacao(request, self.conferencia(pk))
