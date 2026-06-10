from django.urls import path

from accounts.views import (
    OperacionalLoginView,
    OperacionalLogoutView,
    UsuarioCreateView,
    UsuarioDeleteView,
    UsuarioDetailView,
    UsuarioListView,
    UsuarioToggleStatusView,
    UsuarioUpdateView,
)
from accounts.views_pendencias import (
    PendenciasOperacionaisView,
    PosicaoPendenciaAprovarView,
    PosicaoPendenciaEditarView,
    PosicaoPendenciaRejeitarView,
    ProdutoPendenciaAprovarView,
    ProdutoPendenciaEditarView,
    ProdutoPendenciaRejeitarView,
)

app_name = 'accounts'

urlpatterns = [
    path('login/', OperacionalLoginView.as_view(), name='login'),
    path('logout/', OperacionalLogoutView.as_view(), name='logout'),
    path('usuarios/', UsuarioListView.as_view(), name='usuarios_lista'),
    path('usuarios/novo/', UsuarioCreateView.as_view(), name='usuarios_criar'),
    path('usuarios/<int:pk>/', UsuarioDetailView.as_view(), name='usuarios_detalhe'),
    path('usuarios/<int:pk>/editar/', UsuarioUpdateView.as_view(), name='usuarios_editar'),
    path('usuarios/<int:pk>/excluir/', UsuarioDeleteView.as_view(), name='usuarios_excluir'),
    path('usuarios/<int:pk>/status/', UsuarioToggleStatusView.as_view(), name='usuarios_toggle_status'),
    path('pendencias/', PendenciasOperacionaisView.as_view(), name='pendencias_operacionais'),
    path('pendencias/produto/<int:pk>/aprovar/', ProdutoPendenciaAprovarView.as_view(), name='pendencia_produto_aprovar'),
    path('pendencias/produto/<int:pk>/rejeitar/', ProdutoPendenciaRejeitarView.as_view(), name='pendencia_produto_rejeitar'),
    path('pendencias/produto/<int:pk>/editar/', ProdutoPendenciaEditarView.as_view(), name='pendencia_produto_editar'),
    path('pendencias/posicao/<int:pk>/aprovar/', PosicaoPendenciaAprovarView.as_view(), name='pendencia_posicao_aprovar'),
    path('pendencias/posicao/<int:pk>/rejeitar/', PosicaoPendenciaRejeitarView.as_view(), name='pendencia_posicao_rejeitar'),
    path('pendencias/posicao/<int:pk>/editar/', PosicaoPendenciaEditarView.as_view(), name='pendencia_posicao_editar'),
]
