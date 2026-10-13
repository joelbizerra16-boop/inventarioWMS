from django.urls import path

from inventario.comparativo_views import (
    ComparativoAcompanhamentoView,
    ComparativoAcuracidadeSalvarView,
    ComparativoExportarView,
    ComparativoFormularioView,
    ComparativoListaView,
)

app_name = 'comparativo'

urlpatterns = [
    path('', ComparativoListaView.as_view(), name='lista'),
    path('nova/', ComparativoFormularioView.as_view(), name='criar'),
    path('<int:pk>/', ComparativoAcompanhamentoView.as_view(), name='acompanhamento'),
    path('<int:pk>/configurar/', ComparativoFormularioView.as_view(), name='editar'),
    path('<int:pk>/exportar/', ComparativoExportarView.as_view(), name='exportar'),
    path(
        '<int:pk>/acuracidade/salvar/',
        ComparativoAcuracidadeSalvarView.as_view(),
        name='acuracidade_salvar',
    ),
]
