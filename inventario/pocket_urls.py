from django.urls import path

from inventario.pocket_precadastro_views import (
    OperadorPrecadastroPosicaoView,
    OperadorPrecadastroProdutoView,
    PocketCiclicoPrecadastroPosicaoView,
    PocketPrecadastroPosicaoView,
    PocketPrecadastroProdutoView,
)
from inventario.pocket_views import (
    PocketContagemCiclicoView,
    PocketContagemView,
    PocketSelecionarView,
)

app_name = 'pocket'

urlpatterns = [
    path('', PocketSelecionarView.as_view(), name='selecionar'),
    path('operador/precadastro/produto/', OperadorPrecadastroProdutoView.as_view(), name='operador_precadastro_produto'),
    path('operador/precadastro/posicao/', OperadorPrecadastroPosicaoView.as_view(), name='operador_precadastro_posicao'),
    path('ciclico/', PocketContagemCiclicoView.as_view(), name='contagem_ciclico'),
    path('ciclico/precadastro/posicao/', PocketCiclicoPrecadastroPosicaoView.as_view(), name='precadastro_posicao_ciclico'),
    path('<int:inventario_id>/', PocketContagemView.as_view(), name='contagem'),
    path('<int:inventario_id>/precadastro/produto/', PocketPrecadastroProdutoView.as_view(), name='precadastro_produto'),
    path('<int:inventario_id>/precadastro/posicao/', PocketPrecadastroPosicaoView.as_view(), name='precadastro_posicao'),
]
