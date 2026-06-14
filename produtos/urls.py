from django.urls import path

from produtos.views import (
    ProdutoCreateView,
    ProdutoDeleteView,
    ProdutoImportarView,
    ProdutoListView,
    ProdutoUpdateView,
)

app_name = 'produtos'

urlpatterns = [
    path('', ProdutoListView.as_view(), name='lista'),
    path('novo/', ProdutoCreateView.as_view(), name='criar'),
    path('importar/', ProdutoImportarView.as_view(), name='importar'),
    path('<int:pk>/editar/', ProdutoUpdateView.as_view(), name='editar'),
    path('<int:pk>/excluir/', ProdutoDeleteView.as_view(), name='excluir'),
]
