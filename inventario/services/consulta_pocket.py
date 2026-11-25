from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from django.core.paginator import Paginator
from django.db.models import QuerySet

from inventario.models import Inventario, InventarioItem
from inventario.services.pocket import buscar_posicao_por_codigo, buscar_produto_por_codigo


TIPO_SKU = 'sku'
TIPO_POSICAO = 'posicao'
POR_PAGINA = 20
MENSAGEM_SEM_RESULTADO = 'Nenhuma contagem encontrada para esta pesquisa.'
MENSAGEM_CODIGO_OBRIGATORIO = 'Informe o código para consultar.'


class ConsultaPocketError(Exception):
    pass


@dataclass
class LinhaConsultaPocket:
    codigo_produto: str
    descricao: str
    codigo_posicao: str
    alocacao: str
    quantidade: Decimal
    operador: str
    data_hora: datetime | None


@dataclass
class ResultadoConsultaPocket:
    linhas: list[LinhaConsultaPocket]
    page_obj: object
    mensagem: str


def _normalizar_tipo(tipo: str) -> str:
    tipo = (tipo or '').strip().lower()
    if tipo == TIPO_POSICAO:
        return TIPO_POSICAO
    return TIPO_SKU


def _queryset_vigente(inventario: Inventario) -> QuerySet:
    """Contagem atual do inventário.

    Cada par posição/produto tem um único registro vigente. Histórico de
    auditoria, contagem rejeitada e recontagem substituída ficam fora daqui.
    """
    return (
        InventarioItem.objects.filter(inventario=inventario)
        .select_related(
            'posicao',
            'produto',
            'usuario_contagem',
            'usuario_contagem__perfil_operacional',
        )
        .order_by('posicao__codigo', 'produto__codigo_produto', 'pk')
    )


def _linha(item: InventarioItem) -> LinhaConsultaPocket:
    operador = ''
    if item.usuario_contagem_id:
        operador = item.usuario_contagem_nome
    return LinhaConsultaPocket(
        codigo_produto=item.produto.codigo_produto,
        descricao=item.produto.descricao,
        codigo_posicao=item.posicao.codigo,
        alocacao=item.posicao.posicao,
        quantidade=item.quantidade_fisica,
        operador=operador,
        data_hora=item.data_contagem,
    )


def _vazio(page_obj) -> ResultadoConsultaPocket:
    return ResultadoConsultaPocket(
        linhas=[],
        page_obj=page_obj,
        mensagem=MENSAGEM_SEM_RESULTADO,
    )


def consultar_contagens_pocket(
    inventario: Inventario,
    tipo: str,
    termo: str,
    pagina: int | str = 1,
    por_pagina: int = POR_PAGINA,
) -> ResultadoConsultaPocket:
    termo = (termo or '').strip()
    if not termo:
        raise ConsultaPocketError(MENSAGEM_CODIGO_OBRIGATORIO)

    queryset = _queryset_vigente(inventario)
    tipo = _normalizar_tipo(tipo)
    if tipo == TIPO_POSICAO:
        posicao = buscar_posicao_por_codigo(termo)
        queryset = queryset.filter(posicao=posicao) if posicao is not None else queryset.none()
    else:
        produto = buscar_produto_por_codigo(termo)
        queryset = queryset.filter(produto=produto) if produto is not None else queryset.none()

    paginator = Paginator(queryset, por_pagina)
    page_obj = paginator.get_page(pagina)
    if paginator.count == 0:
        return _vazio(page_obj)

    return ResultadoConsultaPocket(
        linhas=[_linha(item) for item in page_obj.object_list],
        page_obj=page_obj,
        mensagem='',
    )
