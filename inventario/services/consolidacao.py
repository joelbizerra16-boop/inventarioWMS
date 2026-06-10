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





def _queryset_inventarios_finalizados():

    return (

        Inventario.objects.filter(

            status=Inventario.Status.FINALIZADO,

            itens__isnull=False,

        )

        .exclude(usuario__nome='Homologação')

        .exclude(usuario__login__startswith='homolog-')

        .distinct()

    )





def obter_id_inventario_finalizado_mais_recente() -> int | None:

    return (

        _queryset_inventarios_finalizados()

        .order_by('-pk')

        .values_list('pk', flat=True)

        .first()

    )





def _validar_pode_publicar(inventario: Inventario) -> None:

    if inventario.status != Inventario.Status.FINALIZADO:

        raise ConsolidacaoError(

            'Somente inventários finalizados podem ser publicados.',

        )



    ultimo_id = obter_id_inventario_finalizado_mais_recente()

    if ultimo_id is None or inventario.pk != ultimo_id:

        raise ConsolidacaoError(

            'Somente o último inventário finalizado pode publicar o estoque físico.',

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





@transaction.atomic

def publicar_estoque_fisico(inventario: Inventario) -> ResultadoConsolidacao:

    """Substitui integralmente o EstoqueFisico pelos itens do inventário finalizado."""

    _validar_pode_publicar(inventario)



    agregados = _agregar_itens_inventario(inventario.pk)

    data_hora = timezone.now()



    EstoqueFisico.objects.all().delete()



    registros = [

        EstoqueFisico(

            produto_id=item['produto_id'],

            posicao_id=item['posicao_id'],

            quantidade=item['quantidade'] or Decimal('0'),

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


