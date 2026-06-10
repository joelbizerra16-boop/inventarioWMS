from django.urls import path

from estoque_fisico.views import EstoqueFisicoExportarView, EstoqueFisicoListView

app_name = 'estoque_fisico'

urlpatterns = [
    path('', EstoqueFisicoListView.as_view(), name='lista'),
    path('exportar/', EstoqueFisicoExportarView.as_view(), name='exportar'),
]
