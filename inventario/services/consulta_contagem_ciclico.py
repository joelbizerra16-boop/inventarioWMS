"""Consulta operacional de contagem cíclica — rastreabilidade por SKU (sem logs técnicos)."""

from dataclasses import dataclass
from decimal import Decimal

from django.db.models import Q

from inventario.models import CicloInventario, CicloInventarioItem, CicloInventarioSku
from inventario.services.ciclico import (
    CODIGO_POSICAO_GENERICO,
    StatusItemCiclico,
    _decimal,
    _sku_para_dto,
    obter_ciclo_consulta,
)


@dataclass
class ConsultaContagemResumo:
    codigo_produto: str
    descricao: str
    sap: Decimal
    contado: Decimal
    diferenca: Decimal | None
    faltam: Decimal
    acuracia: Decimal | None
    status_label: str
    status_classe: str


@dataclass
class ConsultaContagemPosicao:
    posicao: str
    quantidade: Decimal


@dataclass
class ConsultaContagemOperador:
    operador: str
    quantidade: Decimal


@dataclass
class ConsultaContagemDetalhe:
    sku_id: int
    ciclo_id: int
    resumo: ConsultaContagemResumo
    posicoes: list[ConsultaContagemPosicao]
    operadores: list[ConsultaContagemOperador]


@dataclass
class ConsultaContagemResultado:
    sku: ConsultaContagemDetalhe | None
    sugestoes: list[tuple[int, str, str]]


def _posicao_contada(item: CicloInventarioItem) -> bool:
    if item.quantidade_fisica is None:
        return False
    if (
        item.codigo_posicao == CODIGO_POSICAO_GENERICO
        and item.quantidade_fisica is None
    ):
        return False
    return True


def _faltam_sap(sap: Decimal, contado: Decimal) -> Decimal:
    restante = sap - contado
    return restante if restante > 0 else Decimal('0')


def _acuracia_sku(sap: Decimal, contado: Decimal, diferenca: Decimal | None) -> Decimal | None:
    if contado is None:
        return None
    if sap == 0:
        return Decimal('100') if contado == 0 else Decimal('0')
    diff = diferenca if diferenca is not None else (contado - sap)
    if diff == 0:
        return Decimal('100')
    pct = Decimal('100') - (abs(diff) / sap * Decimal('100'))
    if pct < 0:
        pct = Decimal('0')
    return pct.quantize(Decimal('0.01'))


def _sku_tem_contagem(sku: CicloInventarioSku) -> bool:
    return sku.posicoes.filter(quantidade_fisica__isnull=False).exists()


def buscar_skus_contagem(
    ciclo_id: int | None,
    termo: str,
    *,
    limite: int = 20,
) -> list[tuple[int, str, str]]:
    termo = (termo or '').strip()
    if not termo:
        return []

    ciclo = obter_ciclo_consulta(ciclo_id)
    if ciclo is None:
        return []

    queryset = (
        CicloInventarioSku.objects.filter(ciclo=ciclo)
        .exclude(status_contagem=StatusItemCiclico.EXCLUIDO)
        .filter(
            Q(codigo_produto__icontains=termo)
            | Q(descricao__icontains=termo)
            | Q(produto__codigo_ean__icontains=termo),
        )
        .filter(posicoes__quantidade_fisica__isnull=False)
        .distinct()
        .order_by('codigo_produto')[:limite]
    )
    return [(sku.pk, sku.codigo_produto, sku.descricao) for sku in queryset]


def obter_consulta_contagem(
    ciclo_id: int | None,
    *,
    sku_id: int | None = None,
    termo: str = '',
) -> ConsultaContagemResultado:
    ciclo = obter_ciclo_consulta(ciclo_id)
    if ciclo is None:
        return ConsultaContagemResultado(sku=None, sugestoes=[])

    sku: CicloInventarioSku | None = None
    if sku_id:
        sku = (
            CicloInventarioSku.objects.filter(pk=sku_id, ciclo=ciclo)
            .prefetch_related(
                'posicoes',
                'posicoes__usuario_contagem',
                'posicoes__usuario_contagem__perfil_operacional',
            )
            .first()
        )
    elif termo.strip():
        sugestoes = buscar_skus_contagem(ciclo.pk, termo)
        if len(sugestoes) == 1:
            sku = (
                CicloInventarioSku.objects.filter(pk=sugestoes[0][0], ciclo=ciclo)
                .prefetch_related(
                    'posicoes',
                    'posicoes__usuario_contagem',
                    'posicoes__usuario_contagem__perfil_operacional',
                )
                .first()
            )
            return ConsultaContagemResultado(
                sku=_montar_detalhe(sku) if sku and _sku_tem_contagem(sku) else None,
                sugestoes=[],
            )
        return ConsultaContagemResultado(sku=None, sugestoes=sugestoes)

    if sku is None or not _sku_tem_contagem(sku):
        return ConsultaContagemResultado(sku=None, sugestoes=[])

    return ConsultaContagemResultado(sku=_montar_detalhe(sku), sugestoes=[])


def _montar_detalhe(sku: CicloInventarioSku) -> ConsultaContagemDetalhe:
    dto = _sku_para_dto(sku, incluir_posicoes=True)
    sap = _decimal(sku.quantidade_sap)
    contado = _decimal(sku.quantidade_fisica)
    diferenca = sku.diferenca
    if diferenca is None and contado is not None:
        diferenca = contado - sap

    posicoes: list[ConsultaContagemPosicao] = []
    por_operador: dict[str, Decimal] = {}

    for posicao in sku.posicoes.order_by('codigo_posicao'):
        if not _posicao_contada(posicao):
            continue
        rotulo = (posicao.alocacao or posicao.codigo_posicao).strip()
        qtd = _decimal(posicao.quantidade_fisica)
        posicoes.append(ConsultaContagemPosicao(posicao=rotulo, quantidade=qtd))
        nome = posicao.usuario_contagem_nome
        por_operador[nome] = por_operador.get(nome, Decimal('0')) + qtd

    operadores = sorted(
        (
            ConsultaContagemOperador(operador=nome, quantidade=qtd)
            for nome, qtd in por_operador.items()
        ),
        key=lambda item: (-item.quantidade, item.operador),
    )

    resumo = ConsultaContagemResumo(
        codigo_produto=dto.codigo_produto,
        descricao=dto.descricao,
        sap=sap,
        contado=contado,
        diferenca=diferenca,
        faltam=_faltam_sap(sap, contado),
        acuracia=_acuracia_sku(sap, contado, diferenca),
        status_label=dto.status_label,
        status_classe=dto.status_classe,
    )

    return ConsultaContagemDetalhe(
        sku_id=sku.pk,
        ciclo_id=sku.ciclo_id,
        resumo=resumo,
        posicoes=posicoes,
        operadores=operadores,
    )


def resolver_ciclo_consulta_contagem(ciclo_id: int | None) -> CicloInventario | None:
    ciclo = obter_ciclo_consulta(ciclo_id)
    if ciclo is not None:
        return ciclo
    from inventario.services.ciclico import listar_ciclos_historico

    historico = listar_ciclos_historico()
    return historico[0] if historico else None
