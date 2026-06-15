from dataclasses import asdict

from django.views.generic import TemplateView

from accounts.mixins import AcessoOperacionalMixin
from core.services.perf_diagnostico import medir_etapa
from dashboard.services.dashboard import obter_indicadores_dashboard


def _serializar_grafico(grafico) -> dict:
    dados = asdict(grafico)
    dados['valores'] = [int(valor) for valor in dados.get('valores', [])]
    return dados


class HomeView(AcessoOperacionalMixin, TemplateView):
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
