from dataclasses import asdict

from django.views.generic import TemplateView

from accounts.mixins import AcessoOperacionalMixin
from core.services.perf_diagnostico import medir_etapa
from dashboard.services.dashboard import obter_indicadores_dashboard
from dashboard.services.painel_inventario import comparativo_padrao, montar_painel
from inventario.models_comparativo import ComparativoContagem
from inventario.services.comparativo_contagens import FUSO_ROTULO, Canal, sincronizar_referencia_sap


def _serializar_grafico(grafico) -> dict:
    dados = asdict(grafico)
    dados['valores'] = [float(valor) for valor in dados.get('valores', [])]
    return dados


class HomeView(AcessoOperacionalMixin, TemplateView):
    """Tela inicial: Painel geral do inventário (conferência do Comparativo)."""

    template_name = 'dashboard/painel.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        pk = (self.request.GET.get('conferencia') or '').strip()
        comparativo = None
        if pk.isdigit():
            comparativo = ComparativoContagem.objects.select_related('referencia_sap').filter(pk=int(pk)).first()
        if comparativo is None:
            comparativo = comparativo_padrao()
        canal = Canal.normalizar(self.request.GET.get('canal'))
        if comparativo is not None:
            # Conferência aberta acompanha a importação SAP mais recente (auditado).
            sincronizar_referencia_sap(comparativo, self.request.user)
        with medir_etapa('HomeView.montar_painel'):
            painel = montar_painel(comparativo, canal)
        context.update({
            'painel': painel,
            'comparativo': comparativo,
            'canal': canal,
            'canais': [(valor, Canal.ROTULOS[valor]) for valor in (Canal.COSAN, Canal.BRIDA, Canal.AMBAS)],
            'conferencias': ComparativoContagem.objects.only('pk', 'nome', 'data_finalizacao').order_by('-pk')[:50],
            'fuso_rotulo': FUSO_ROTULO,
        })
        return context


class DashboardClassicoView(AcessoOperacionalMixin, TemplateView):
    """Dashboard anterior (gráficos operacionais), mantido fora da tela inicial."""

    template_name = 'dashboard/home.html'

    def get_context_data(self, **kwargs):
        with medir_etapa('HomeView.get_context_data.super'):
            context = super().get_context_data(**kwargs)
        with medir_etapa('HomeView.get_context_data.obter_indicadores_dashboard'):
            indicadores = obter_indicadores_dashboard()
        with medir_etapa('HomeView.get_context_data.serializacao_graficos'):
            context['indicadores'] = indicadores
            context['graficos_payload'] = {
                'geral': [_serializar_grafico(grafico) for grafico in indicadores.graficos_geral],
                'ciclico': [_serializar_grafico(grafico) for grafico in indicadores.graficos_ciclico],
            }
        return context
