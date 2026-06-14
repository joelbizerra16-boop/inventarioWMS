"""Snapshot imutável do inventário geral ao finalizar."""

from collections import defaultdict
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from inventario.models import Inventario, InventarioItem
from inventario.services.confronto import executar_confronto


def _status_exibicao_confronto(label: str) -> str:
    if label == 'Correto':
        return 'Conciliado'
    return 'Divergente'


def _montar_posicoes_por_produto(inventario: Inventario) -> dict[int, list[dict]]:
    agrupado: dict[int, list[dict]] = defaultdict(list)
    itens = (
        InventarioItem.objects.filter(inventario=inventario)
        .select_related('produto', 'posicao')
        .order_by('produto__codigo_produto', 'posicao__posicao')
    )
    for item in itens:
        agrupado[item.produto_id].append({
            'codigo': item.posicao.codigo,
            'alocacao': item.posicao.posicao,
            'quantidade': str(item.quantidade_fisica),
        })
    return agrupado


@transaction.atomic
def congelar_snapshot_inventario(inventario: Inventario, usuario) -> Inventario:
    if inventario.status != Inventario.Status.FINALIZADO:
        raise ValueError('Somente inventários finalizados recebem snapshot.')

    resultado = executar_confronto(inventario_id=inventario.pk)
    posicoes_por_produto = _montar_posicoes_por_produto(inventario)

    produtos_snapshot = []
    for linha in resultado.linhas:
        produtos_snapshot.append({
            'produto_id': linha.produto_id,
            'codigo_produto': linha.codigo_produto,
            'descricao': linha.descricao,
            'embalagem': linha.embalagem or '—',
            'sap': str(linha.total_contabil),
            'contado': str(linha.fisico),
            'diferenca': str(linha.diferenca),
            'status': _status_exibicao_confronto(linha.status_label),
            'posicoes': posicoes_por_produto.get(linha.produto_id, []),
        })

    inventario.data_finalizacao = timezone.now()
    inventario.usuario_finalizacao = usuario
    inventario.quantidade_itens = InventarioItem.objects.filter(inventario=inventario).count()
    inventario.quantidade_produtos = resultado.resumo.total_produtos
    inventario.quantidade_conciliados = resultado.resumo.produtos_corretos
    inventario.quantidade_divergentes = resultado.resumo.produtos_divergentes
    inventario.taxa_acuracidade = resultado.resumo.acuracidade
    inventario.snapshot_resultado = {
        'resumo': {
            'total_produtos': resultado.resumo.total_produtos,
            'conciliados': resultado.resumo.produtos_corretos,
            'divergentes': resultado.resumo.produtos_divergentes,
            'acuracidade': str(resultado.resumo.acuracidade),
        },
        'produtos': produtos_snapshot,
    }
    inventario.save(update_fields=[
        'data_finalizacao',
        'usuario_finalizacao',
        'quantidade_itens',
        'quantidade_produtos',
        'quantidade_conciliados',
        'quantidade_divergentes',
        'taxa_acuracidade',
        'snapshot_resultado',
    ])
    return inventario
