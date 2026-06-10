from django.urls import path

from inventario.views import (
    ContagemCreateView,
    ContagemDeleteView,
    ContagemListView,
    ContagemUpdateView,
    InventarioCreateView,
    InventarioDeleteView,
    InventarioFinalizarView,
    InventarioListView,
    InventarioUpdateView,
)

app_name = 'inventario'

urlpatterns = [
    path('', InventarioListView.as_view(), name='lista'),
    path('novo/', InventarioCreateView.as_view(), name='criar'),
    path('<int:pk>/editar/', InventarioUpdateView.as_view(), name='editar'),
    path('<int:pk>/excluir/', InventarioDeleteView.as_view(), name='excluir'),
    path('<int:pk>/finalizar/', InventarioFinalizarView.as_view(), name='finalizar'),
    path('<int:pk>/contagem/', ContagemListView.as_view(), name='contagem_lista'),
    path('<int:pk>/contagem/novo/', ContagemCreateView.as_view(), name='contagem_criar'),
    path(
        '<int:pk>/contagem/<int:item_id>/editar/',
        ContagemUpdateView.as_view(),
        name='contagem_editar',
    ),
    path(
        '<int:pk>/contagem/<int:item_id>/excluir/',
        ContagemDeleteView.as_view(),
        name='contagem_excluir',
    ),
]
