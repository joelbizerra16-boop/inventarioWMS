"""Serviços exclusivos do Pocket Cíclico (fila operacional). Não afeta Pocket do Inventário Geral."""



from dataclasses import dataclass

from decimal import Decimal



from django.db import transaction

from django.utils import timezone



from inventario.models import CicloAuditoriaHistorico, CicloInventario, CicloInventarioSku

from inventario.services.ciclico import (

    CiclicoError,

    SkuCicloDetalhe,

    StatusItemCiclico,

    _calcular_indicador_confronto,

    _obter_ciclo_ativo,

    _obter_skus_lote_sessao,

    _sku_esta_no_lote_ativo,

    _sku_para_dto,

    calcular_resumo_ciclo,

    encerrar_ciclo_automatico,

    limpar_pocket_sessao_contagem,

    obter_lote_sessao,

    registrar_contagem_pocket_ciclico,

)

from posicoes.models import Posicao





FILA_STATUSES = StatusItemCiclico.EM_CONTAGEM

FINALIZADOS_STATUSES = StatusItemCiclico.FINALIZADOS

VALIDADOS_STATUSES = frozenset({

    StatusItemCiclico.VALIDADO,

    StatusItemCiclico.VALIDADO_DIVERGENCIA,

})





def _decimal_pocket(valor) -> Decimal:

    if valor is None or valor == '':

        return Decimal('0')

    return Decimal(str(valor))





def sku_status_finalizado(status: str) -> bool:

    return status in FINALIZADOS_STATUSES





def calcular_indicadores_pocket_contagem(

    bipado,

    cosan,

    brida,

) -> dict[str, str]:

    bipado_val = _decimal_pocket(bipado)

    cosan_val = _decimal_pocket(cosan)

    brida_val = _decimal_pocket(brida)

    total_val = cosan_val + brida_val

    if bipado_val == total_val:

        indicador = 'verde'

    elif bipado_val > total_val:

        indicador = 'laranja'

    else:

        indicador = 'vermelho'

    return {

        'bipado': str(bipado_val),

        'cosan': str(cosan_val),

        'brida': str(brida_val),

        'total': str(total_val),

        'indicador': indicador,

    }





def _sku_na_fila_pocket(sku: CicloInventarioSku, session) -> bool:

    del session

    if sku_status_finalizado(sku.status_contagem):

        return False

    return sku.status_contagem in FILA_STATUSES





def _sku_na_divergencia_pocket(sku: CicloInventarioSku, session) -> bool:

    del session

    return sku.status_contagem == StatusItemCiclico.DIVERGENTE





def _sku_pode_contar_pocket(sku: CicloInventarioSku, session) -> bool:

    del session

    if sku.status_contagem == StatusItemCiclico.EXCLUIDO:

        return False

    if sku_status_finalizado(sku.status_contagem):

        return False

    return sku.status_contagem in FILA_STATUSES





def _registrar_saida_fila_operacional(

    sku: CicloInventarioSku,

    status_anterior: str,

    usuario,

) -> None:

    if sku_status_finalizado(status_anterior):

        return

    if sku.status_contagem in FILA_STATUSES:

        return



    label_anterior = StatusItemCiclico.LABELS.get(status_anterior, status_anterior)

    label_novo = StatusItemCiclico.LABELS.get(sku.status_contagem, sku.status_contagem)

    CicloAuditoriaHistorico.objects.create(

        ciclo_sku=sku,

        tipo=CicloAuditoriaHistorico.TipoRegistro.VALIDACAO,

        usuario=usuario,

        data_hora=timezone.now(),

        quantidade_sap_momento=sku.quantidade_sap,

        quantidade_fisica=sku.quantidade_fisica or Decimal('0'),

        diferenca=sku.diferenca or Decimal('0'),

        motivo=(

            f'Status:\n{label_anterior} → {label_novo}'

            f'\n\nSKU removido da fila operacional do Pocket.'

        ),

    )





def _calcular_resumo_lote(skus: list[CicloInventarioSku], session) -> 'PocketCiclicoResumoLote':

    total = len(skus)

    contados = sum(1 for sku in skus if sku_status_finalizado(sku.status_contagem))

    validados = sum(1 for sku in skus if sku.status_contagem in VALIDADOS_STATUSES)

    faltam = max(total - contados, 0)

    divergentes = sum(

        1 for sku in skus if _sku_na_divergencia_pocket(sku, session)

    )

    if total == 0:

        percentual = Decimal('0')

    else:

        percentual = (Decimal(contados) / Decimal(total) * Decimal('100')).quantize(

            Decimal('0.01'),

        )

    return PocketCiclicoResumoLote(

        total_lote=total,

        contados=contados,

        validados=validados,

        pendentes=faltam,

        divergentes=divergentes,

        percentual_executado=percentual,

    )





@dataclass

class PocketCiclicoResumoLote:

    total_lote: int

    contados: int

    validados: int

    pendentes: int

    divergentes: int

    percentual_executado: Decimal





@dataclass

class PocketCiclicoSkuFila:

    pk: int

    codigo_produto: str

    descricao: str

    embalagem: str

    quantidade_sap: Decimal

    quantidade_cosan: Decimal | None

    quantidade_brida: Decimal | None

    status_contagem: str

    status_label: str





@dataclass

class PocketCiclicoDivergencia:

    pk: int

    codigo_produto: str

    sap: Decimal

    fisico: Decimal

    diferenca: Decimal

    indicador: str

    indicador_tooltip: str





@dataclass

class PocketCiclicoPainel:

    resumo: PocketCiclicoResumoLote

    fila: list[PocketCiclicoSkuFila]

    divergencias: list[PocketCiclicoDivergencia]

    skus_json: list[dict]





def _skus_do_lote(session) -> list[CicloInventarioSku]:

    return _obter_skus_lote_sessao(session)





def _sku_para_fila(sku: CicloInventarioSku) -> PocketCiclicoSkuFila:

    cosan = sku.quantidade_cosan

    brida = sku.quantidade_brida

    return PocketCiclicoSkuFila(

        pk=sku.pk,

        codigo_produto=sku.codigo_produto,

        descricao=sku.descricao,

        embalagem=sku.embalagem or '—',

        quantidade_sap=sku.quantidade_sap,

        quantidade_cosan=cosan,

        quantidade_brida=brida,

        status_contagem=sku.status_contagem,

        status_label=StatusItemCiclico.LABELS[sku.status_contagem],

    )





def obter_painel_pocket_ciclico(session) -> PocketCiclicoPainel:

    skus = _skus_do_lote(session)

    resumo = _calcular_resumo_lote(skus, session)



    fila = [

        _sku_para_fila(sku)

        for sku in skus

        if _sku_na_fila_pocket(sku, session)

    ]

    divergencias_lista: list[PocketCiclicoDivergencia] = []

    for sku in skus:

        if not _sku_na_divergencia_pocket(sku, session):

            continue

        if sku.quantidade_fisica is None:

            continue

        diferenca, indicador, tooltip = _calcular_indicador_confronto(

            sku.quantidade_fisica,

            sku.quantidade_sap,

        )

        divergencias_lista.append(PocketCiclicoDivergencia(

            pk=sku.pk,

            codigo_produto=sku.codigo_produto,

            sap=sku.quantidade_sap,

            fisico=sku.quantidade_fisica,

            diferenca=diferenca or Decimal('0'),

            indicador=indicador or '',

            indicador_tooltip=tooltip,

        ))



    skus_json = [

        serializar_sku_pocket_json(sku)

        for sku in skus

        if _sku_na_fila_pocket(sku, session)

    ]



    return PocketCiclicoPainel(

        resumo=resumo,

        fila=fila,

        divergencias=divergencias_lista,

        skus_json=skus_json,

    )





def serializar_ciclo_encerrado_pocket(ciclo: CicloInventario) -> dict:

    resumo = calcular_resumo_ciclo(ciclo)

    return {

        'ciclo_id': ciclo.pk,

        'total_planejado': resumo.total_skus,

        'total_contado': resumo.skus_contados,

        'validados': resumo.skus_validados,

        'divergentes': resumo.skus_divergentes,

        'acuracidade': (

            str(ciclo.taxa_acuracidade)

            if ciclo.taxa_acuracidade is not None

            else '—'

        ),

    }





def serializar_resumo_pocket(

    resumo: PocketCiclicoResumoLote,

    *,

    ciclo_encerrado: dict | None = None,

) -> dict[str, str | int | bool | dict]:

    payload: dict[str, str | int | bool | dict] = {

        'total_lote': resumo.total_lote,

        'contados': resumo.contados,

        'validados': resumo.validados,

        'pendentes': resumo.pendentes,

        'divergentes': resumo.divergentes,

        'percentual_executado': str(resumo.percentual_executado),

        'lote_concluido': resumo.pendentes == 0 and resumo.divergentes == 0,

        'ciclo_concluido': ciclo_encerrado is not None,

    }

    if ciclo_encerrado is not None:

        payload['ciclo_encerrado'] = ciclo_encerrado

    return payload





def serializar_sku_pocket_json(sku: CicloInventarioSku) -> dict:

    indicadores = calcular_indicadores_pocket_contagem(

        sku.quantidade_fisica,

        sku.quantidade_cosan,

        sku.quantidade_brida,

    )

    return {

        'pk': sku.pk,

        'codigo_produto': sku.codigo_produto,

        'descricao': sku.descricao,

        'embalagem': sku.embalagem or '—',

        **indicadores,

    }





def _processar_pos_contagem_pocket(

    session,

    sku: CicloInventarioSku,

    status_anterior: str,

    usuario,

) -> CicloInventario | None:

    sku.refresh_from_db()

    if (

        not sku_status_finalizado(status_anterior)

        and sku_status_finalizado(sku.status_contagem)

    ):

        _registrar_saida_fila_operacional(sku, status_anterior, usuario)

    return encerrar_ciclo_automatico(usuario)





def obter_resposta_contagem_pocket(
    session,
    sku_id: int,
    usuario,
    *,
    ciclo_encerrado: CicloInventario | None = None,
) -> dict:

    painel = obter_painel_pocket_ciclico(session)

    sku = CicloInventarioSku.objects.get(pk=sku_id)

    ciclo_payload = (
        serializar_ciclo_encerrado_pocket(ciclo_encerrado)
        if ciclo_encerrado is not None
        else None
    )



    proximo_sku_id = painel.fila[0].pk if painel.fila else None

    return {

        'indicadores': calcular_indicadores_pocket_contagem(

            sku.quantidade_fisica,

            sku.quantidade_cosan,

            sku.quantidade_brida,

        ),

        'status_contagem': sku.status_contagem,

        'sku_removido_fila': not _sku_na_fila_pocket(sku, session),

        'proximo_sku_id': proximo_sku_id,

        'resumo': serializar_resumo_pocket(

            painel.resumo,

            ciclo_encerrado=ciclo_payload,

        ),

        'fila_ids': [item.pk for item in painel.fila],

        'skus_lote': painel.skus_json,

    }





def _obter_sku_lote(session, sku_id: int) -> CicloInventarioSku:

    ciclo = _obter_ciclo_ativo()

    if ciclo is None:

        raise CiclicoError('Nenhum ciclo cíclico ativo.')

    if sku_id not in set(obter_lote_sessao(session)):

        raise CiclicoError('SKU fora do lote diário de execução.')

    try:

        return CicloInventarioSku.objects.select_related('produto').get(

            pk=sku_id,

            ciclo=ciclo,

        )

    except CicloInventarioSku.DoesNotExist as exc:

        raise CiclicoError('SKU não encontrado no lote.') from exc





@transaction.atomic

def registrar_contagem_pocket_ciclico_por_sku(

    session,

    sku_id: int,

    posicao: Posicao,

    quantidade: Decimal,

    usuario,

    dispositivo: str = '',

) -> tuple[SkuCicloDetalhe, CicloInventario | None]:

    sku = _obter_sku_lote(session, sku_id)

    if sku.status_contagem == StatusItemCiclico.EXCLUIDO:

        raise CiclicoError('SKU excluído do ciclo não pode receber contagem.')

    if not _sku_pode_contar_pocket(sku, session):

        raise CiclicoError('Selecione um SKU pendente ou em recontagem.')

    if not _sku_esta_no_lote_ativo(sku, session):

        raise CiclicoError('SKU fora do lote diário de execução.')



    status_anterior = sku.status_contagem

    dto = registrar_contagem_pocket_ciclico(

        session,

        posicao,

        sku.produto,

        quantidade,

        usuario,

        dispositivo=dispositivo,

    )

    ciclo_encerrado = _processar_pos_contagem_pocket(session, sku, status_anterior, usuario)

    sku.refresh_from_db()

    return _sku_para_dto(sku, incluir_posicoes=True), ciclo_encerrado





@transaction.atomic

def solicitar_recontagem_pocket(session, sku_id: int, usuario) -> CicloInventarioSku:

    sku = _obter_sku_lote(session, sku_id)

    if sku.status_contagem != StatusItemCiclico.DIVERGENTE:

        raise CiclicoError('Somente SKUs divergentes podem ser recontados.')



    agora = timezone.now()

    sku.posicoes.update(

        quantidade_fisica=None,

        quantidade_recontagem=None,

        diferenca=None,

        data_contagem=None,

        origem_contagem='',

        dispositivo_contagem='',

    )

    sku.quantidade_fisica = None

    sku.diferenca = None

    sku.status_contagem = StatusItemCiclico.RECONTAGEM

    sku.usuario_recontagem = usuario

    sku.data_recontagem = agora

    sku.save(update_fields=[

        'quantidade_fisica',

        'diferenca',

        'status_contagem',

        'usuario_recontagem',

        'data_recontagem',

    ])

    CicloAuditoriaHistorico.objects.create(

        ciclo_sku=sku,

        tipo=CicloAuditoriaHistorico.TipoRegistro.RECONTAGEM,

        usuario=usuario,

        data_hora=agora,

        quantidade_sap_momento=sku.quantidade_sap,

        quantidade_fisica=Decimal('0'),

        diferenca=Decimal('0'),

    )

    limpar_pocket_sessao_contagem(session)

    return sku





@transaction.atomic

def aceitar_divergencia_pocket(

    session,

    sku_id: int,

    usuario,

) -> tuple[CicloInventarioSku, CicloInventario | None]:

    sku = _obter_sku_lote(session, sku_id)

    if sku.status_contagem != StatusItemCiclico.DIVERGENTE:

        raise CiclicoError('Somente SKUs divergentes podem ser aceitos.')

    if sku.quantidade_fisica is None:

        raise CiclicoError('SKU sem contagem física registrada.')



    agora = timezone.now()

    status_anterior = sku.status_contagem

    sku.status_contagem = StatusItemCiclico.VALIDADO_DIVERGENCIA

    sku.save(update_fields=['status_contagem'])

    from inventario.services.ciclico_estoque_fisico import (
        tentar_sincronizar_estoque_fisico_pos_finalizacao,
    )

    tentar_sincronizar_estoque_fisico_pos_finalizacao(sku, status_anterior, usuario)

    CicloAuditoriaHistorico.objects.create(

        ciclo_sku=sku,

        tipo=CicloAuditoriaHistorico.TipoRegistro.VALIDACAO,

        usuario=usuario,

        data_hora=agora,

        quantidade_sap_momento=sku.quantidade_sap,

        quantidade_fisica=sku.quantidade_fisica,

        diferenca=sku.diferenca or Decimal('0'),

        motivo='Divergência aceita pelo supervisor.',

    )

    ciclo_encerrado = _processar_pos_contagem_pocket(session, sku, status_anterior, usuario)

    return sku, ciclo_encerrado


