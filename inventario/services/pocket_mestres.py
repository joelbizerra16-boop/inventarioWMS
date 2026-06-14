from posicoes.models import Posicao
from produtos.models import Produto


def obter_mapas_mestres_pocket() -> dict:
    mapa_posicoes = {
        posicao['codigo']: posicao['posicao']
        for posicao in Posicao.objects.filter(ativo=True).values('codigo', 'posicao')
    }
    mapa_produtos: dict[str, str] = {}
    mapa_ean: dict[str, dict] = {}
    mapa_embalagens: dict[str, str] = {}
    for produto in Produto.objects.filter(ativo=True).values(
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
