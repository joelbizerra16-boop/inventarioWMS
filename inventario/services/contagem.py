from django.db import transaction
from django.utils import timezone

from inventario.models import Inventario, InventarioItem


def _atualizar_status_inventario(inventario: Inventario) -> None:
    if inventario.status == Inventario.Status.ABERTO:
        inventario.status = Inventario.Status.EM_ANDAMENTO
        inventario.save(update_fields=['status'])


@transaction.atomic
def salvar_contagem(
    inventario: Inventario,
    posicao,
    produto,
    quantidade_fisica,
    item_existente: InventarioItem | None = None,
    usuario_contagem=None,
    origem_contagem: str = '',
) -> InventarioItem:
    Inventario.objects.select_for_update().get(pk=inventario.pk)
    if item_existente and (
        item_existente.posicao_id != posicao.pk
        or item_existente.produto_id != produto.pk
    ):
        item_existente.delete()

    defaults = {
        'quantidade_fisica': quantidade_fisica,
        'data_contagem': timezone.now(),
    }
    if usuario_contagem is not None:
        defaults['usuario_contagem'] = usuario_contagem
    if origem_contagem:
        defaults['origem_contagem'] = origem_contagem

    item, _ = InventarioItem.objects.update_or_create(
        inventario=inventario,
        posicao=posicao,
        produto=produto,
        defaults=defaults,
    )

    _atualizar_status_inventario(inventario)

    return item


@transaction.atomic
def excluir_contagem(item: InventarioItem) -> None:
    item.delete()
