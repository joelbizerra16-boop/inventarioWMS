"""Sincronização do Estoque Físico após finalização de SKU no inventário cíclico."""

from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from estoque_fisico.models import EstoqueFisico
from inventario.models import CicloEstoqueFisicoAjuste, CicloInventarioItem, CicloInventarioSku

CODIGO_POSICAO_GENERICO = 'CICLICO-SEM-POS'

STATUS_ATUALIZA_ESTOQUE = frozenset({
    CicloInventarioSku.StatusContagem.VALIDADO,
    CicloInventarioSku.StatusContagem.VALIDADO_DIVERGENCIA,
})


def _decimal(valor) -> Decimal:
    if valor is None or valor == '':
        return Decimal('0')
    return Decimal(str(valor))


def _posicao_generica_sem_contagem(item: CicloInventarioItem) -> bool:
    return (
        item.codigo_posicao == CODIGO_POSICAO_GENERICO
        and item.quantidade_fisica is None
    )


def _posicoes_auditadas_sku(sku: CicloInventarioSku) -> list[CicloInventarioItem]:
    return [
        item for item in sku.posicoes.select_related('produto', 'posicao').all()
        if item.quantidade_fisica is not None
        and not _posicao_generica_sem_contagem(item)
        and item.codigo_posicao != CODIGO_POSICAO_GENERICO
    ]


def _resolver_usuario_ajuste(sku: CicloInventarioSku, usuario) -> object | None:
    if usuario is not None:
        return usuario
    for item in sorted(
        _posicoes_auditadas_sku(sku),
        key=lambda registro: registro.data_contagem or timezone.now(),
        reverse=True,
    ):
        if item.usuario_contagem_id:
            return item.usuario_contagem
    return None


def _montar_motivo(sku: CicloInventarioSku) -> str:
    motivo = f'Ajuste automático após validação do SKU {sku.codigo_produto} no ciclo #{sku.ciclo_id}.'
    if sku.status_contagem == CicloInventarioSku.StatusContagem.VALIDADO_DIVERGENCIA:
        motivo += ' Divergência aceita.'
    return motivo


@transaction.atomic
def sincronizar_estoque_fisico_sku_finalizado(
    sku: CicloInventarioSku,
    usuario,
    *,
    status_anterior: str,
) -> list[CicloEstoqueFisicoAjuste]:
    sku.refresh_from_db()
    if sku.status_contagem not in STATUS_ATUALIZA_ESTOQUE:
        return []
    if status_anterior in STATUS_ATUALIZA_ESTOQUE:
        return []

    usuario_ajuste = _resolver_usuario_ajuste(sku, usuario)
    agora = timezone.now()
    motivo = _montar_motivo(sku)
    ajustes: list[CicloEstoqueFisicoAjuste] = []

    for item in _posicoes_auditadas_sku(sku):
        quantidade_nova = _decimal(item.quantidade_fisica)
        estoque = EstoqueFisico.objects.filter(
            produto_id=item.produto_id,
            posicao_id=item.posicao_id,
        ).first()
        quantidade_anterior = (
            _decimal(estoque.quantidade) if estoque is not None else Decimal('0')
        )
        diferenca = quantidade_nova - quantidade_anterior

        if estoque is None:
            EstoqueFisico.objects.create(
                produto=item.produto,
                posicao=item.posicao,
                quantidade=quantidade_nova,
                data_contagem=agora,
            )
        elif estoque.quantidade != quantidade_nova:
            estoque.quantidade = quantidade_nova
            estoque.data_contagem = agora
            estoque.save(update_fields=['quantidade', 'data_contagem', 'data_atualizacao'])

        ajustes.append(CicloEstoqueFisicoAjuste.objects.create(
            ciclo_id=sku.ciclo_id,
            ciclo_sku=sku,
            item=item,
            produto=item.produto,
            posicao=item.posicao,
            codigo_produto=item.codigo_produto,
            codigo_posicao=item.codigo_posicao,
            quantidade_anterior=quantidade_anterior,
            quantidade_nova=quantidade_nova,
            diferenca=diferenca,
            usuario=usuario_ajuste,
            data_hora=agora,
            origem=CicloEstoqueFisicoAjuste.OrigemAjuste.INVENTARIO_CICLICO,
            motivo=motivo,
        ))

    return ajustes


def tentar_sincronizar_estoque_fisico_pos_finalizacao(
    sku: CicloInventarioSku,
    status_anterior: str,
    usuario,
) -> list[CicloEstoqueFisicoAjuste]:
    return sincronizar_estoque_fisico_sku_finalizado(
        sku,
        usuario,
        status_anterior=status_anterior,
    )
