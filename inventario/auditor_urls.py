from django.urls import path

from inventario.auditor_views import (
    AuditorApiView,
    AuditorConteudoView,
    AuditorExportarView,
    AuditorInicioView,
    AuditorPainelView,
)

app_name = 'auditor'

urlpatterns = [
    path('', AuditorInicioView.as_view(), name='inicio'),
    path('<int:pk>/', AuditorPainelView.as_view(), name='painel'),
    path('<int:pk>/conteudo/', AuditorConteudoView.as_view(), name='conteudo'),
    path('<int:pk>/api/', AuditorApiView.as_view(), name='api'),
    path('<int:pk>/exportar/', AuditorExportarView.as_view(), name='exportar'),
]
