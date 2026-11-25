from dataclasses import dataclass

from datetime import datetime

from decimal import Decimal



from django.db import transaction

from django.db.models import Sum

from django.utils import timezone



from estoque_fisico.models import EstoqueFisico

from inventario.models import Inventario, InventarioItem





class ConsolidacaoError(Exception):

    pass





@dataclass

class PreviewConsolidacao:

    inventario_id: int

    total_produtos: int

    total_posicoes: int

    quantidade_consolidada: Decimal





@dataclass

class ResultadoConsolidacao:

    inventario_id: int

    data_hora: datetime

    registros_processados: int

    registros_atualizados: int

    registros_criados: int

    total_produtos: int

    total_posicoes: int

    quantidade_consolidada: Decimal





_auditoria_consolidacao: dict[int, ResultadoConsolidacao] = {}





def limpar_estado_consolidacao() -> None:

    _auditoria_consolidacao.clear()





def obter_auditoria_consolidacao(inventario_id: int) -> ResultadoConsolidacao | None:

    if inventario_id in _auditoria_consolidacao:

        return _auditoria_consolidacao[inventario_id]

    return _reconstruir_auditoria_do_banco(inventario_id)


def inventario_foi_consolidado(inventario_id: int) -> bool:

    return EstoqueFisico.objects.filter(inventario_origem_id=inventario_id).exists()


def limpar_auditoria_inventario(inventario_id: int) -> None:

    _auditoria_consolidacao.pop(inventario_id, None)


def _reconstruir_auditoria_do_banco(inventario_id: int) -> ResultadoConsolidacao | None:

    agregados = list(

        EstoqueFisico.objects.filter(

            inventario_origem_id=inventario_id,

        ).values(

            'produto_id',

            'posicao_id',

        ).annotate(

            quantidade=Sum('quantidade'),

        )

    )

    if not agregados:

        return None

    total_produtos, total_posicoes, quantidade = _calcular_totais(agregados)

    data_hora = (

        EstoqueFisico.objects.filter(inventario_origem_id=inventario_id)

        .order_by('-data_publicacao')

        .values_list('data_publicacao', flat=True)

        .first()

    ) or timezone.now()

    return ResultadoConsolidacao(

        inventario_id=inventario_id,

        data_hora=data_hora,

        registros_processados=len(agregados),

        registros_atualizados=0,

        registros_criados=len(agregados),

        total_produtos=total_produtos,

        total_posicoes=total_posicoes,

        quantidade_consolidada=quantidade,

    )





def _queryset_inventarios_finalizados(categoria: str | None = None):

    queryset = (

        Inventario.objects.filter(

            status=Inventario.Status.FINALIZADO,

            itens__isnull=False,

        )

        .exclude(usuario__nome='Homologação')

        .exclude(usuario__login__startswith='homolog-')

        .distinct()

    )

    if categoria:

        queryset = queryset.filter(categoria=categoria)

    return queryset





def obter_id_inventario_finalizado_mais_recente(categoria: str | None = None) -> int | None:

    return (

        _queryset_inventarios_finalizados(categoria)

        .order_by('-pk')

        .values_list('pk', flat=True)

        .first()

    )





def _validar_pode_publicar(inventario: Inventario) -> None:

    if inventario.status != Inventario.Status.FINALIZADO:

        raise ConsolidacaoError(

            'Somente inventários finalizados podem ser publicados.',

        )



    # A regra "último finalizado" vale dentro da categoria do inventário:
    # um inventário de Filtros não bloqueia a publicação de Lubrificantes.
    ultimo_id = obter_id_inventario_finalizado_mais_recente(inventario.categoria)

    if ultimo_id is None or inventario.pk != ultimo_id:

        raise ConsolidacaoError(

            'Somente o último inventário finalizado desta categoria '
            f'({inventario.categoria_rotulo}) pode publicar o estoque físico.',

        )





def _agregar_itens_inventario(inventario_id: int) -> list[dict]:

    return list(

        InventarioItem.objects.filter(

            inventario_id=inventario_id,

        ).values(

            'produto_id',

            'posicao_id',

        ).annotate(

            quantidade=Sum('quantidade_fisica'),

        )

    )





def _calcular_totais(agregados: list[dict]) -> tuple[int, int, Decimal]:

    produtos = {item['produto_id'] for item in agregados}

    posicoes = {item['posicao_id'] for item in agregados}

    quantidade = sum(

        (item['quantidade'] or Decimal('0')) for item in agregados

    )

    return len(produtos), len(posicoes), quantidade





def obter_preview_consolidacao(inventario: Inventario) -> PreviewConsolidacao:

    _validar_pode_publicar(inventario)



    agregados = _agregar_itens_inventario(inventario.pk)

    total_produtos, total_posicoes, quantidade = _calcular_totais(agregados)



    return PreviewConsolidacao(

        inventario_id=inventario.pk,

        total_produtos=total_produtos,

        total_posicoes=total_posicoes,

        quantidade_consolidada=quantidade,

    )





def _validar_sem_conflito_outra_categoria(agregados: list[dict], categoria: str) -> None:
    """Impede sobrescrever, sem aviso, saldo físico publicado por outra categoria."""
    if not agregados:
        return
    pares = {(item['produto_id'], item['posicao_id']) for item in agregados}
    produto_ids = {produto_id for produto_id, _ in pares}
    conflitos = [
        registro
        for registro in EstoqueFisico.objects.filter(produto_id__in=produto_ids)
        .exclude(categoria=categoria)
        .select_related('produto', 'posicao')
        if (registro.produto_id, registro.posicao_id) in pares
    ]
    if conflitos:
        exemplo = conflitos[0]
        raise ConsolidacaoError(
            f'Publicação bloqueada: {len(conflitos)} saldo(s) físico(s) já pertencem a outra '
            f'categoria (ex.: produto {exemplo.produto.codigo_produto} na posição '
            f'{exemplo.posicao.codigo}, {exemplo.get_categoria_display()}). '
            'Nenhum estoque foi alterado.'
        )


@transaction.atomic

def publicar_estoque_fisico(inventario: Inventario) -> ResultadoConsolidacao:

    """Substitui o EstoqueFisico *da categoria do inventário* pelos itens dele.

    Somente os registros da mesma categoria são apagados e recriados; o
    estoque físico das demais categorias permanece intacto.
    """

    _validar_pode_publicar(inventario)



    agregados = _agregar_itens_inventario(inventario.pk)

    data_hora = timezone.now()

    categoria = inventario.categoria

    # Toda a validação acontece antes de apagar qualquer saldo (atômico).
    from inventario.services.categoria import CategoriaError, validar_publicacao_categoria

    try:
        validar_publicacao_categoria(inventario, agregados)
    except CategoriaError as exc:
        raise ConsolidacaoError(str(exc)) from exc

    _validar_sem_conflito_outra_categoria(agregados, categoria)



    EstoqueFisico.objects.filter(categoria=categoria).delete()



    registros = [

        EstoqueFisico(

            produto_id=item['produto_id'],

            posicao_id=item['posicao_id'],

            quantidade=item['quantidade'] or Decimal('0'),

            categoria=categoria,

            inventario_origem=inventario,

            data_publicacao=data_hora,

            data_contagem=data_hora,

        )

        for item in agregados

    ]

    if registros:

        EstoqueFisico.objects.bulk_create(registros)



    registros_processados = len(registros)

    total_produtos, total_posicoes, quantidade = _calcular_totais(agregados)



    resultado = ResultadoConsolidacao(

        inventario_id=inventario.pk,

        data_hora=data_hora,

        registros_processados=registros_processados,

        registros_atualizados=0,

        registros_criados=registros_processados,

        total_produtos=total_produtos,

        total_posicoes=total_posicoes,

        quantidade_consolidada=quantidade,

    )

    _auditoria_consolidacao[inventario.pk] = resultado

    return resultado





def consolidar_estoque_fisico(inventario: Inventario) -> ResultadoConsolidacao:

    return publicar_estoque_fisico(inventario)


