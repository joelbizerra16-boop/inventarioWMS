"""
URL configuration for core project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import include, path

from dashboard.views import HomeView
from dashboard.views_operacional import DashboardOperacionalApiView
from inventario.views import (
    AprovacaoView,
    CiclicoConsultaView,
    CiclicoConsultaContagemView,
    CiclicoContagemSkuView,
    CiclicoExecutarView,
    CiclicoExportarView,
    CiclicoHistoricoAuditoriaView,
    CiclicoHistoricoDetalheView,
    CiclicoHistoricoView,
    CiclicoListView,
    CiclicoRelatorioView,
    CiclicoSkuDetalheView,
    CiclicoSkuEditarView,
    CiclicoSkuExcluirView,
    ConfrontoListView,
    ConsolidacaoView,
)
from inventario.historico_views import (
    HistoricoDetalheView,
    HistoricoExportarView,
    HistoricoUnificadoView,
)

urlpatterns = [
    path('admin/', admin.site.urls),
    path('accounts/', include('accounts.urls')),
    path('', HomeView.as_view(), name='home'),
    path(
        'dashboard/operacional/api/',
        DashboardOperacionalApiView.as_view(),
        name='dashboard_operacional_api',
    ),
    path('confronto/', ConfrontoListView.as_view(), name='confronto'),
    path('aprovacao/', AprovacaoView.as_view(), name='aprovacao'),
    path('consolidacao/', ConsolidacaoView.as_view(), name='consolidacao'),
    path('historico/', HistoricoUnificadoView.as_view(), name='historico_unificado'),
    path(
        'historico/<str:tipo>/<int:pk>/',
        HistoricoDetalheView.as_view(),
        name='historico_detalhe',
    ),
    path(
        'historico/<str:tipo>/<int:pk>/exportar/',
        HistoricoExportarView.as_view(),
        name='historico_exportar',
    ),
    path('ciclico/', CiclicoListView.as_view(), name='ciclico'),
    path('ciclico/consulta/', CiclicoConsultaView.as_view(), name='ciclico_consulta'),
    path(
        'ciclico/consulta-contagem/',
        CiclicoConsultaContagemView.as_view(),
        name='ciclico_consulta_contagem',
    ),
    path('ciclico/relatorio/', CiclicoRelatorioView.as_view(), name='ciclico_relatorio'),
    path('ciclico/exportar/', CiclicoExportarView.as_view(), name='ciclico_exportar'),
    path('ciclico/executar/', CiclicoExecutarView.as_view(), name='ciclico_executar'),
    path('ciclico/historico/', CiclicoHistoricoView.as_view(), name='ciclico_historico'),
    path(
        'ciclico/historico/<int:ciclo_id>/',
        CiclicoHistoricoDetalheView.as_view(),
        name='ciclico_historico_detalhe',
    ),
    path(
        'ciclico/historico/<int:ciclo_id>/auditoria/',
        CiclicoHistoricoAuditoriaView.as_view(),
        name='ciclico_historico_auditoria',
    ),
    path(
        'ciclico/sku/<int:sku_id>/detalhe/',
        CiclicoSkuDetalheView.as_view(),
        name='ciclico_sku_detalhe',
    ),
    path(
        'ciclico/sku/<int:sku_id>/editar/',
        CiclicoSkuEditarView.as_view(),
        name='ciclico_sku_editar',
    ),
    path(
        'ciclico/sku/<int:sku_id>/excluir/',
        CiclicoSkuExcluirView.as_view(),
        name='ciclico_sku_excluir',
    ),
    path(
        'ciclico/contagem/<int:sku_id>/',
        CiclicoContagemSkuView.as_view(),
        name='ciclico_contagem_sku',
    ),
    path('produtos/', include('produtos.urls')),
    path('posicoes/', include('posicoes.urls')),
    path('estoque-sap/', include('estoque_sap.urls')),
    path('estoque-fisico/', include('estoque_fisico.urls')),
    path('inventarios/', include('inventario.urls')),
    path('pocket/', include('inventario.pocket_urls')),
]

handler404 = 'core.views.handler404'
handler500 = 'core.views.handler500'
