from django.urls import path

from posicoes.views import (
    PosicaoCreateView,
    PosicaoDeleteView,
    PosicaoImportarView,
    PosicaoListView,
    PosicaoUpdateView,
)

app_name = 'posicoes'

urlpatterns = [
    path('', PosicaoListView.as_view(), name='lista'),
    path('novo/', PosicaoCreateView.as_view(), name='criar'),
    path('importar/', PosicaoImportarView.as_view(), name='importar'),
    path('<int:pk>/editar/', PosicaoUpdateView.as_view(), name='editar'),
    path('<int:pk>/excluir/', PosicaoDeleteView.as_view(), name='excluir'),
]
