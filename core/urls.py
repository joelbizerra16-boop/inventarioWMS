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

from core.pwa import manifest_pwa, service_worker_pwa
from dashboard.views import HomeView
from dashboard.views_operacional import DashboardOperacionalApiView
from inventario.views import (
    AprovacaoView,
    ConfrontoListView,
    ConsolidacaoView,
)
from inventario.historico_views import (
    HistoricoDetalheView,
    HistoricoExportarView,
    HistoricoUnificadoView,
)

urlpatterns = [
    path('manifest.webmanifest', manifest_pwa, name='pwa_manifest'),
    path('sw.js', service_worker_pwa, name='pwa_service_worker'),
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
    path('produtos/', include('produtos.urls')),
    path('posicoes/', include('posicoes.urls')),
    path('estoque-sap/', include('estoque_sap.urls')),
    path('estoque-fisico/', include('estoque_fisico.urls')),
    path('inventarios/', include('inventario.urls')),
    path('pocket/', include('inventario.pocket_urls')),
]

handler404 = 'core.views.handler404'
handler500 = 'core.views.handler500'
