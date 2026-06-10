from dataclasses import dataclass
from decimal import Decimal

from django.db.models import Sum

from estoque_sap.models import EstoqueSAP
from inventario.models import InventarioItem


@dataclass
class LinhaConfronto:
    produto_id: int
    codigo_produto: str
    descricao: str
    embalagem: str
    setor: str
    fisico: Decimal
    canal_0: Decimal
    canal_1: Decimal
    canal_2: Decimal
    canal_66: Decimal
    canal_80: Decimal
    canal_81: Decimal
    canal_82: Decimal
    canal_99: Decimal
    canal_110: Decimal
    total_contabil: Decimal
    diferenca: Decimal
    status_classe: str
    status_label: str
    possui_divergencia: bool


@dataclass
class ResumoConfronto:
    total_produtos: int
    produtos_corretos: int
    produtos_divergentes: int
    produtos_excesso_fisico: int
    produtos_deficit_fisico: int
    acuracidade: Decimal


@dataclass
class ResultadoConfronto:
    linhas: list[LinhaConfronto]
    resumo: ResumoConfronto


def _decimal(valor) -> Decimal:
    if valor is None:
        return Decimal('0')
    return Decimal(valor)


def _obter_estoque_sap_por_produto() -> dict[int, EstoqueSAP]:
    estoques = {}
    registros = EstoqueSAP.objects.select_related('produto').order_by(
        'produto_id',
        '-data_importacao',
    )

    for registro in registros:
        if registro.produto_id not in estoques:
            estoques[registro.produto_id] = registro

    return estoques


def _calcular_status(fisico: Decimal, total_contabil: Decimal) -> tuple[str, str, bool]:
    diferenca = fisico - total_contabil

    if diferenca == 0:
        return diferenca, 'success', 'Correto', False

    if diferenca > 0:
        return diferenca, 'warning', 'Excesso físico', True

    return diferenca, 'danger', 'Déficit físico', True


def _montar_linha(
    produto_id,
    codigo,
    descricao,
    embalagem,
    setor,
    fisico,
    sap,
) -> LinhaConfronto:
    if sap:
        total_contabil = _decimal(sap.total)
        linha = LinhaConfronto(
            produto_id=produto_id,
            codigo_produto=codigo,
            descricao=descricao,
            embalagem=embalagem,
            setor=setor,
            fisico=fisico,
            canal_0=_decimal(sap.canal_0),
            canal_1=_decimal(sap.canal_1),
            canal_2=_decimal(sap.canal_2),
            canal_66=_decimal(sap.canal_66),
            canal_80=_decimal(sap.canal_80),
            canal_81=_decimal(sap.canal_81),
            canal_82=_decimal(sap.canal_82),
            canal_99=_decimal(sap.canal_99),
            canal_110=_decimal(sap.canal_110),
            total_contabil=total_contabil,
            diferenca=Decimal('0'),
            status_classe='',
            status_label='',
            possui_divergencia=False,
        )
    else:
        linha = LinhaConfronto(
            produto_id=produto_id,
            codigo_produto=codigo,
            descricao=descricao,
            embalagem=embalagem,
            setor=setor,
            fisico=fisico,
            canal_0=Decimal('0'),
            canal_1=Decimal('0'),
            canal_2=Decimal('0'),
            canal_66=Decimal('0'),
            canal_80=Decimal('0'),
            canal_81=Decimal('0'),
            canal_82=Decimal('0'),
            canal_99=Decimal('0'),
            canal_110=Decimal('0'),
            total_contabil=Decimal('0'),
            diferenca=Decimal('0'),
            status_classe='',
            status_label='',
            possui_divergencia=False,
        )

    diferenca, status_classe, status_label, possui_divergencia = _calcular_status(
        linha.fisico,
        linha.total_contabil,
    )
    linha.diferenca = diferenca
    linha.status_classe = status_classe
    linha.status_label = status_label
    linha.possui_divergencia = possui_divergencia

    return linha


def _calcular_resumo(linhas: list[LinhaConfronto]) -> ResumoConfronto:
    total = len(linhas)
    divergentes = sum(1 for linha in linhas if linha.possui_divergencia)
    corretos = total - divergentes
    excesso = sum(1 for linha in linhas if linha.status_label == 'Excesso físico')
    deficit = sum(1 for linha in linhas if linha.status_label == 'Déficit físico')

    if total == 0:
        acuracidade = Decimal('0')
    else:
        acuracidade = (Decimal(corretos) / Decimal(total) * Decimal('100')).quantize(
            Decimal('0.01')
        )

    return ResumoConfronto(
        total_produtos=total,
        produtos_corretos=corretos,
        produtos_divergentes=divergentes,
        produtos_excesso_fisico=excesso,
        produtos_deficit_fisico=deficit,
        acuracidade=acuracidade,
    )


def _ordenar_linhas(linhas: list[LinhaConfronto]) -> list[LinhaConfronto]:
    return sorted(
        linhas,
        key=lambda linha: (not linha.possui_divergencia, linha.codigo_produto),
    )


def _aplicar_filtro_status(
    linhas: list[LinhaConfronto],
    filtro_status: str,
) -> list[LinhaConfronto]:
    if filtro_status == 'divergencias':
        return [linha for linha in linhas if linha.possui_divergencia]
    if filtro_status == 'corretos':
        return [linha for linha in linhas if not linha.possui_divergencia]
    return linhas


def _aplicar_pesquisa(
    linhas: list[LinhaConfronto],
    termo_busca: str,
) -> list[LinhaConfronto]:
    if not termo_busca:
        return linhas

    termo = termo_busca.lower()
    return [
        linha for linha in linhas
        if termo in linha.codigo_produto.lower()
        or termo in linha.descricao.lower()
    ]


def executar_confronto(
    inventario_id: int,
    filtro_status: str = 'todos',
    termo_busca: str = '',
) -> ResultadoConfronto:
    fisico_agregado = InventarioItem.objects.filter(
        inventario_id=inventario_id,
    ).values(
        'produto_id',
        'produto__codigo_produto',
        'produto__descricao',
        'produto__embalagem',
        'produto__setor',
    ).annotate(
        fisico=Sum('quantidade_fisica'),
    )

    sap_por_produto = _obter_estoque_sap_por_produto()
    produtos_processados: set[int] = set()
    linhas: list[LinhaConfronto] = []

    for item in fisico_agregado:
        produto_id = item['produto_id']
        produtos_processados.add(produto_id)
        linhas.append(_montar_linha(
            produto_id=produto_id,
            codigo=item['produto__codigo_produto'],
            descricao=item['produto__descricao'] or 'PRODUTO NÃO CADASTRADO',
            embalagem=item['produto__embalagem'] or '',
            setor=item['produto__setor'] or '',
            fisico=_decimal(item['fisico']),
            sap=sap_por_produto.get(produto_id),
        ))

    for produto_id, sap in sap_por_produto.items():
        if produto_id in produtos_processados:
            continue
        linhas.append(_montar_linha(
            produto_id=produto_id,
            codigo=sap.produto.codigo_produto,
            descricao=sap.produto.descricao or 'PRODUTO NÃO CADASTRADO',
            embalagem=sap.produto.embalagem or '',
            setor=sap.produto.setor or '',
            fisico=Decimal('0'),
            sap=sap,
        ))

    linhas = _ordenar_linhas(linhas)
    resumo = _calcular_resumo(linhas)
    linhas = _aplicar_filtro_status(linhas, filtro_status)
    linhas = _aplicar_pesquisa(linhas, termo_busca.strip())

    return ResultadoConfronto(linhas=linhas, resumo=resumo)
