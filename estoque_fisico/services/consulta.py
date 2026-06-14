from django.db.models import Q, QuerySet

from estoque_fisico.models import EstoqueFisico
from produtos.models import Produto


def queryset_base() -> QuerySet:
    return EstoqueFisico.objects.select_related(
        'produto',
        'posicao',
        'inventario_origem',
    ).order_by('-data_atualizacao')


def obter_info_publicacao() -> dict | None:
    registro = (
        EstoqueFisico.objects
        .select_related('inventario_origem')
        .exclude(inventario_origem__isnull=True)
        .order_by('-data_publicacao')
        .first()
    )
    if registro is None:
        return None
    return {
        'inventario_id': registro.inventario_origem_id,
        'data_publicacao': registro.data_publicacao,
    }


def aplicar_filtros(
    queryset: QuerySet,
    termo_busca: str = '',
    setor: str = '',
    somente_positivo: bool = False,
) -> QuerySet:
    if termo_busca:
        queryset = queryset.filter(
            Q(produto__codigo_produto__icontains=termo_busca)
            | Q(produto__descricao__icontains=termo_busca)
            | Q(posicao__codigo__icontains=termo_busca)
            | Q(posicao__posicao__icontains=termo_busca)
        )

    if setor:
        queryset = queryset.filter(produto__setor=setor)

    if somente_positivo:
        queryset = queryset.filter(quantidade__gt=0)

    return queryset


def obter_parametros_filtro(request) -> tuple[str, str, bool]:
    termo_busca = request.GET.get('q', '').strip()
    setor = request.GET.get('setor', '').strip()
    somente_positivo = request.GET.get('somente_positivo') == '1'
    return termo_busca, setor, somente_positivo


def obter_queryset_filtrado(request) -> QuerySet:
    termo_busca, setor, somente_positivo = obter_parametros_filtro(request)
    return aplicar_filtros(
        queryset_base(),
        termo_busca=termo_busca,
        setor=setor,
        somente_positivo=somente_positivo,
    )


def obter_setores_disponiveis() -> list[str]:
    return list(
        Produto.objects.filter(
            estoques_fisicos__isnull=False,
        ).values_list('setor', flat=True).distinct().order_by('setor')
    )
