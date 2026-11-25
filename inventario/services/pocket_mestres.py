from posicoes.models import Posicao
from produtos.models import Produto


def obter_mapas_mestres_pocket(inventario=None) -> dict:
    """Mapas de posições/produtos/EAN usados pelo Pocket (cache no aparelho).

    Com ``inventario``: somente posições da categoria gravada nele e produtos
    da mesma categoria (mais os sem classificação incluídos explicitamente).
    Sem ``inventario``: comportamento original (todos os ativos).
    """
    posicoes = Posicao.objects.filter(ativo=True)
    produtos = Produto.objects.elegiveis_para_inventario()
    if inventario is not None:
        from inventario.services.categoria import produtos_contaveis_no_inventario

        posicoes = posicoes.filter(categoria=inventario.categoria)
        produtos = produtos_contaveis_no_inventario(inventario, produtos)

    mapa_posicoes = {
        posicao['codigo']: posicao['posicao']
        for posicao in posicoes.values('codigo', 'posicao')
    }
    mapa_produtos: dict[str, str] = {}
    mapa_ean: dict[str, dict] = {}
    mapa_embalagens: dict[str, str] = {}
    for produto in produtos.values(
        'codigo_produto',
        'descricao',
        'codigo_ean',
        'embalagem',
    ):
        emb = produto['embalagem'] or '—'
        mapa_produtos[produto['codigo_produto']] = produto['descricao']
        mapa_embalagens[produto['codigo_produto']] = emb
        if produto['codigo_ean']:
            mapa_ean[produto['codigo_ean']] = {
                'descricao': produto['descricao'],
                'codigo_produto': produto['codigo_produto'],
            }
            mapa_embalagens[produto['codigo_ean']] = emb
    return {
        'mapa_posicoes': mapa_posicoes,
        'mapa_produtos': mapa_produtos,
        'mapa_ean': mapa_ean,
        'mapa_embalagens': mapa_embalagens,
    }
