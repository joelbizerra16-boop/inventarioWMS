from django.http import JsonResponse
from django.views import View

from accounts.mixins import AcessoOperacionalMixin
from dashboard.services.dashboard_operacional import (
    obter_indicadores_ciclico,
    obter_indicadores_geral,
    serializar_indicadores,
)


class DashboardOperacionalApiView(AcessoOperacionalMixin, View):
    """Endpoint JSON para atualização operacional em tempo real (polling)."""

    def get(self, request):
        tipo = request.GET.get('tipo', 'CICLICO').upper()
        referencia_id = request.GET.get('id')

        ref_pk = int(referencia_id) if referencia_id and referencia_id.isdigit() else None

        if tipo == 'GERAL':
            indicadores = obter_indicadores_geral(ref_pk)
        else:
            indicadores = obter_indicadores_ciclico(ref_pk)

        return JsonResponse({
            'ok': True,
            'indicadores': serializar_indicadores(indicadores),
        })
