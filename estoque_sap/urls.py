from django.urls import path

from estoque_sap.views import EstoqueSAPImportarView, EstoqueSAPListView

app_name = 'estoque_sap'

urlpatterns = [
    path('', EstoqueSAPListView.as_view(), name='lista'),
    path('importar/', EstoqueSAPImportarView.as_view(), name='importar'),
]
