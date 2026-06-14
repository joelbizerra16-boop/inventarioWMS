from django.contrib import messages
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views import View

from accounts.mixins import AcessoOperacionalMixin
from inventario.services.historico_exportacao import (
    exportar_historico_csv,
    exportar_historico_excel,
)
from inventario.services.historico_unificado import (
    StatusHistoricoUnificado,
    TipoHistorico,
    calcular_indicadores_historico,
    listar_historico_unificado,
    obter_detalhe_historico_unificado,
)


class HistoricoUnificadoView(AcessoOperacionalMixin, View):
    template_name = 'inventario/historico_unificado.html'

    def get(self, request):
        filtros = {
            'periodo_inicio': request.GET.get('periodo_inicio', '').strip(),
            'periodo_fim': request.GET.get('periodo_fim', '').strip(),
            'status_filtro': request.GET.get('status', '').strip(),
            'tipo_filtro': request.GET.get('tipo', '').strip(),
            'usuario_filtro': request.GET.get('usuario', '').strip(),
        }
        linhas = listar_historico_unificado(**filtros)
        return render(request, self.template_name, {
            'linhas': linhas,
            'indicadores': calcular_indicadores_historico(linhas),
            'filtros': filtros,
            'tipo_opcoes': TipoHistorico.FILTROS,
            'status_opcoes': StatusHistoricoUnificado.FILTROS,
        })


class HistoricoDetalheView(AcessoOperacionalMixin, View):
    template_name = 'inventario/historico_detalhe.html'

    def get(self, request, tipo: str, pk: int):
        tipo = tipo.upper()
        if tipo not in (TipoHistorico.GERAL, TipoHistorico.CICLICO):
            messages.error(request, 'Tipo de histórico inválido.')
            return redirect('historico_unificado')

        detalhe = obter_detalhe_historico_unificado(tipo, pk)
        if detalhe is None:
            messages.error(request, 'Registro não encontrado no histórico.')
            return redirect('historico_unificado')

        return render(request, self.template_name, {
            'detalhe': detalhe,
            'produtos_com_posicoes': [
                produto for produto in detalhe.produtos if produto.posicoes
            ],
        })


class HistoricoExportarView(AcessoOperacionalMixin, View):
    def get(self, request, tipo: str, pk: int):
        tipo = tipo.upper()
        formato = request.GET.get('formato', 'excel').strip().lower()

        if formato == 'pdf' and tipo == TipoHistorico.CICLICO:
            url = reverse('ciclico_relatorio') + f'?ciclo={pk}'
            return redirect(url)

        if formato == 'csv':
            return exportar_historico_csv(tipo, pk)
        return exportar_historico_excel(tipo, pk)
