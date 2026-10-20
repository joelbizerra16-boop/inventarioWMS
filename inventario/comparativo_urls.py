from django.urls import path

from inventario.auditor_config_views import (
    ComparativoAuditoriaPreviaConteudoView,
    ComparativoAuditoriaPreviaExportarView,
    ComparativoAuditoriaPreviaView,
    ComparativoAuditoriaView,
)

from inventario.comparativo_views import (
    ComparativoAcompanhamentoView,
    ComparativoAcuracidadeSalvarView,
    ComparativoExportarView,
    ComparativoExcluirView,
    ComparativoFinalizarView,
    ComparativoFormularioView,
    ComparativoReabrirView,
    ComparativoListaView,
)

app_name = 'comparativo'

urlpatterns = [
    path('<int:pk>/auditoria/', ComparativoAuditoriaView.as_view(), name='auditoria'),
    path('<int:pk>/auditoria/visualizar/', ComparativoAuditoriaPreviaView.as_view(), name='auditoria_previa'),
    path('<int:pk>/auditoria/visualizar/conteudo/', ComparativoAuditoriaPreviaConteudoView.as_view(), name='auditoria_previa_conteudo'),
    path('<int:pk>/auditoria/visualizar/exportar/', ComparativoAuditoriaPreviaExportarView.as_view(), name='auditoria_previa_exportar'),
    path('', ComparativoListaView.as_view(), name='lista'),
    path('nova/', ComparativoFormularioView.as_view(), name='criar'),
    path('<int:pk>/', ComparativoAcompanhamentoView.as_view(), name='acompanhamento'),
    path('<int:pk>/configurar/', ComparativoFormularioView.as_view(), name='editar'),
    path('<int:pk>/exportar/', ComparativoExportarView.as_view(), name='exportar'),
    path('<int:pk>/finalizar/', ComparativoFinalizarView.as_view(), name='finalizar'),
    path('<int:pk>/reabrir/', ComparativoReabrirView.as_view(), name='reabrir'),
    path('<int:pk>/excluir/', ComparativoExcluirView.as_view(), name='excluir'),
    path(
        '<int:pk>/acuracidade/salvar/',
        ComparativoAcuracidadeSalvarView.as_view(),
        name='acuracidade_salvar',
    ),
]
