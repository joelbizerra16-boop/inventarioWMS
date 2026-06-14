from dataclasses import asdict

from django.shortcuts import redirect
from django.urls import reverse
from django.views.generic import TemplateView

from accounts.mixins import AcessoOperacionalMixin
from accounts.services.perfil import usuario_e_operador_pocket
from dashboard.services.dashboard import obter_indicadores_dashboard


def _serializar_grafico(grafico) -> dict:
    dados = asdict(grafico)
    dados['valores'] = [int(valor) for valor in dados.get('valores', [])]
    return dados


class HomeView(AcessoOperacionalMixin, TemplateView):
    template_name = 'dashboard/home.html'

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and usuario_e_operador_pocket(request.user):
            return redirect(reverse('pocket:selecionar'))
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        indicadores = obter_indicadores_dashboard()
        context['indicadores'] = indicadores
        context['graficos_payload'] = {
            'geral': [_serializar_grafico(grafico) for grafico in indicadores.graficos_geral],
            'ciclico': [_serializar_grafico(grafico) for grafico in indicadores.graficos_ciclico],
        }
        return context