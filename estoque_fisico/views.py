from django.views import View
from django.views.generic import ListView

from accounts.mixins import (
    AcessoOperacionalMixin,
    PaginacaoContextMixin,
    PaginacaoMixin,
)
from estoque_fisico.models import EstoqueFisico
from estoque_fisico.services.consulta import (
    obter_info_publicacao,
    obter_parametros_filtro,
    obter_queryset_filtrado,
    obter_setores_disponiveis,
)
from estoque_fisico.services.exportacao import exportar_estoque_fisico_excel


class EstoqueFisicoListView(
    AcessoOperacionalMixin,
    PaginacaoMixin,
    PaginacaoContextMixin,
    ListView,
):
    model = EstoqueFisico
    template_name = 'estoque_fisico/lista.html'
    context_object_name = 'registros'

    def get_queryset(self):
        return obter_queryset_filtrado(self.request)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        termo_busca, setor, somente_positivo = obter_parametros_filtro(self.request)
        context['setor_selecionado'] = setor
        context['somente_positivo'] = somente_positivo
        context['setores'] = obter_setores_disponiveis()
        context['termo_busca'] = termo_busca
        context['info_publicacao'] = obter_info_publicacao()
        return context


class EstoqueFisicoExportarView(AcessoOperacionalMixin, View):
    def get(self, request):
        queryset = obter_queryset_filtrado(request)
        return exportar_estoque_fisico_excel(queryset)
