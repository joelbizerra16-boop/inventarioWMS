from django.db.models import Q

from inventario.models import InventarioItem


def queryset_contagens(inventario, termo: str = ''):
    """Mesma consulta da tela de contagem, sem paginação.

    A ordem é a do model (inventário, produto): a view da tela não aplica
    outro `order_by`, e a exportação precisa repetir essa lista.
    """
    queryset = InventarioItem.objects.filter(
        inventario=inventario,
    ).select_related(
        'posicao',
        'produto',
        'usuario_contagem',
        'usuario_contagem__perfil_operacional',
    )

    termo = (termo or '').strip()
    if termo:
        queryset = queryset.filter(
            Q(produto__codigo_produto__icontains=termo)
            | Q(produto__descricao__icontains=termo)
            | Q(posicao__codigo__icontains=termo)
            | Q(posicao__posicao__icontains=termo)
        )

    return queryset
