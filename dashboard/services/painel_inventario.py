"""Painel geral do inventário (tela inicial).

Visão consolidada de uma conferência do Comparativo de contagens. Todos os
números saem dos mesmos serviços do Comparativo (`comparativo_contagens`):
nenhuma regra de status ou de consolidação é recalculada aqui.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

from django.utils import timezone

from dashboard.services.dashboard import (
    _montar_maiores_divergencias,
    _obter_confronto_ultimo_inventario_finalizado,
)
from inventario.models_comparativo import ComparativoContagem
from inventario.services.comparativo_contagens import (
    NAO_REGISTRADO,
    ORDENS,
    Canal,
    Cobertura,
    DadosComparativo,
    Indicador,
    LinhaComparativo,
    StatusContagem,
    StatusSAP,
    calcular_cobertura,
    calcular_indicador_posicao,
    calcular_indicador_sku,
    carregar_dados,
    montar_linhas_posicao,
    montar_linhas_sku,
    rotulo_ordem,
)

LIMITE_DIVERGENCIAS = 10
LIMITE_RANKING = 12


def _num(valor: float) -> str:
    """Número para SVG com ponto decimal (independe da localização)."""
    return f'{valor:.2f}'.rstrip('0').rstrip('.')


def _pct(parte: int, total: int) -> Decimal | None:
    if not total:
        return None
    return Decimal(parte) * 100 / Decimal(total)


def comparativo_padrao() -> ComparativoContagem | None:
    """Conferência aberta mais recente; sem aberta, a mais recente."""
    aberta = (
        ComparativoContagem.objects.filter(snapshot_resultado__isnull=True)
        .order_by('-pk').first()
    )
    return aberta or ComparativoContagem.objects.order_by('-pk').first()


# ---------------------------------------------------------------------------
# Situação dos SKUs (rosca)
# ---------------------------------------------------------------------------


@dataclass
class FatiaSituacao:
    chave: str
    rotulo: str
    quantidade: int
    percentual: Decimal | None
    dasharray: str = ''
    dashoffset: str = ''


def _situacao_sku(linha: LinhaComparativo) -> str:
    """Classificação exclusiva para a rosca (a soma fecha o total de SKUs)."""
    contado = any(m.registrada for m in linha.medicoes.values())
    if not contado:
        return 'sem_contagem'
    sap = linha.status_sap.status if linha.status_sap else StatusSAP.NEUTRO
    if sap == StatusSAP.OK:
        return 'confirmado'
    if linha.contagens.status == StatusContagem.DIVERGENTE:
        return 'divergencia'
    if linha.contagens.status == StatusContagem.OK and sap in (StatusSAP.DIVERGENTE, StatusSAP.PARCIAL):
        return 'divergencia'
    return 'aguardando'


ROTULOS_SITUACAO = [
    ('confirmado', 'Confirmados com SAP'),
    ('divergencia', 'Em divergência'),
    ('aguardando', 'Aguardando confirmação'),
    ('sem_contagem', 'Sem contagem'),
]


def montar_situacao(linhas_sku: list[LinhaComparativo]) -> list[FatiaSituacao]:
    contagem = {chave: 0 for chave, _ in ROTULOS_SITUACAO}
    for linha in linhas_sku:
        contagem[_situacao_sku(linha)] += 1
    total = len(linhas_sku)
    raio = 60.0
    circunferencia = 2 * math.pi * raio
    acumulado = 0.0
    fatias = []
    for chave, rotulo in ROTULOS_SITUACAO:
        quantidade = contagem[chave]
        fatia = FatiaSituacao(chave, rotulo, quantidade, _pct(quantidade, total))
        if total and quantidade:
            comprimento = circunferencia * quantidade / total
            fatia.dasharray = f'{_num(comprimento)} {_num(circunferencia - comprimento)}'
            fatia.dashoffset = _num(-acumulado)
            acumulado += comprimento
        fatias.append(fatia)
    return fatias


# ---------------------------------------------------------------------------
# Evolução da conferência (linhas acumuladas)
# ---------------------------------------------------------------------------


@dataclass
class SerieEvolucao:
    chave: str
    rotulo: str
    pontos: str
    area: str
    final: int
    final_x: str
    final_y: str


@dataclass
class Evolucao:
    disponivel: bool
    motivo: str = ''
    series: list[SerieEvolucao] = field(default_factory=list)
    eixo_x: list[dict] = field(default_factory=list)
    eixo_y: list[dict] = field(default_factory=list)
    granularidade: str = ''
    sem_data: int = 0


def _momento_confirmacao(linha: LinhaComparativo) -> datetime | None:
    """Quando a segunda contagem igual ao SAP foi registrada."""
    saldo = linha.saldo_selecionado
    if saldo is None:
        return None
    datas = sorted(
        m.ultima for m in linha.medicoes.values()
        if m.registrada and m.quantidade == saldo and m.ultima is not None
    )
    return datas[1] if len(datas) >= 2 else None


def montar_evolucao(linhas_sku: list[LinhaComparativo], total: int) -> Evolucao:
    inicio_contagem: list[datetime] = []
    confirmacoes: list[datetime] = []
    sem_data = 0
    for linha in linhas_sku:
        datas = [l.data_registro for l in linha.lancamentos if l.data_registro is not None]
        if linha.lancamentos and not datas:
            sem_data += 1
        if datas:
            inicio_contagem.append(min(datas))
        if linha.status_sap is not None and linha.status_sap.status == StatusSAP.OK:
            momento = _momento_confirmacao(linha)
            if momento is not None:
                confirmacoes.append(momento)
    if not inicio_contagem:
        return Evolucao(disponivel=False, motivo='Nenhum lançamento com data registrada', sem_data=sem_data)

    local = [timezone.localtime(d) for d in inicio_contagem + confirmacoes]
    inicio, fim = min(local), max(local)
    duracao_horas = max((fim - inicio).total_seconds() / 3600, 1)
    if duracao_horas <= 48:
        passo = timedelta(hours=max(1, math.ceil(duracao_horas / 16)))
        base = inicio.replace(minute=0, second=0, microsecond=0)
        formato = '%Hh' if duracao_horas <= 20 else '%d/%m %Hh'
        granularidade = 'por hora'
    else:
        dias = math.ceil(duracao_horas / 24)
        passo = timedelta(days=max(1, math.ceil(dias / 16)))
        base = inicio.replace(hour=0, minute=0, second=0, microsecond=0)
        formato = '%d/%m'
        granularidade = 'por dia'
    marcos = [base]
    while marcos[-1] < fim:
        marcos.append(marcos[-1] + passo)
    if len(marcos) < 2:
        marcos.append(marcos[0] + passo)

    largura, altura = 560.0, 190.0
    esq, dir_, topo, base_y = 40.0, 34.0, 12.0, 26.0
    area_l = largura - esq - dir_
    area_a = altura - topo - base_y
    teto = max(total, 1)

    def x(i: int) -> float:
        return esq + area_l * i / (len(marcos) - 1)

    def y(valor: int) -> float:
        return topo + area_a * (1 - valor / teto)

    def serie(chave: str, rotulo: str, datas: list[datetime]) -> SerieEvolucao:
        locais = sorted(timezone.localtime(d) for d in datas)
        valores = []
        indice = 0
        for marco in marcos:
            while indice < len(locais) and locais[indice] <= marco:
                indice += 1
            valores.append(indice)
        # O último marco contém todos os registros (fim pode cair entre marcos).
        valores[-1] = len(locais)
        pontos = ' '.join(f'{_num(x(i))},{_num(y(v))}' for i, v in enumerate(valores))
        area = (
            f'M {_num(x(0))},{_num(y(0))} L '
            + ' L '.join(f'{_num(x(i))},{_num(y(v))}' for i, v in enumerate(valores))
            + f' L {_num(x(len(valores) - 1))},{_num(y(0))} Z'
        )
        return SerieEvolucao(
            chave=chave, rotulo=rotulo, pontos=pontos, area=area, final=valores[-1],
            final_x=_num(x(len(valores) - 1) + 4), final_y=_num(y(valores[-1]) + 4),
        )

    passo_rotulo = max(1, math.ceil(len(marcos) / 6))
    eixo_x = [
        {'x': _num(x(i)), 'y': _num(altura - 8), 'texto': marco.strftime(formato)}
        for i, marco in enumerate(marcos) if i % passo_rotulo == 0 or i == len(marcos) - 1
    ]
    # Marcas inteiras com passo uniforme (no máximo ~5 linhas de grade).
    passo_y = max(1, math.ceil(teto / 4))
    eixo_y = [
        {'y': _num(y(valor)), 'texto': valor, 'x1': _num(esq), 'x2': _num(largura - dir_)}
        for valor in range(0, teto + 1, passo_y)
    ]
    if eixo_y[-1]['texto'] != teto:
        eixo_y.append({'y': _num(y(teto)), 'texto': teto, 'x1': _num(esq), 'x2': _num(largura - dir_)})
    return Evolucao(
        disponivel=True,
        series=[
            serie('contagem', 'Com contagem (acumulado)', inicio_contagem),
            serie('confirmado', 'Confirmados com SAP (acumulado)', confirmacoes),
        ],
        eixo_x=eixo_x,
        eixo_y=eixo_y,
        granularidade=granularidade,
        sem_data=sem_data,
    )


# ---------------------------------------------------------------------------
# Avanço das contagens, ranking, cobertura
# ---------------------------------------------------------------------------


@dataclass
class AvancoContagem:
    rotulo: str
    inventario_id: int | None
    numerador: int
    denominador: int
    percentual: Decimal | None
    nota: str = ''
    associada: bool = True


def montar_avanco(linhas_sku: list[LinhaComparativo], dados: DadosComparativo) -> list[AvancoContagem]:
    total = len(linhas_sku)
    avancos = []
    for ordem in (1, 2):
        feitos = sum(1 for l in linhas_sku if l.medicoes[ordem].registrada)
        contagem = dados.contagens.get(ordem)
        avancos.append(AvancoContagem(
            rotulo=rotulo_ordem(ordem),
            inventario_id=contagem.inventario.pk if contagem else None,
            numerador=feitos,
            denominador=total,
            percentual=_pct(feitos, total),
            associada=contagem is not None,
        ))
    # C3 só nos SKUs que exigem desempate (C1 e C2 registradas e diferentes).
    exigem = [
        l for l in linhas_sku
        if l.medicoes[1].registrada and l.medicoes[2].registrada
        and l.medicoes[1].quantidade != l.medicoes[2].quantidade
    ]
    feitos = sum(1 for l in exigem if l.medicoes[3].registrada)
    contagem = dados.contagens.get(3)
    avancos.append(AvancoContagem(
        rotulo='C3',
        inventario_id=contagem.inventario.pk if contagem else None,
        numerador=feitos,
        denominador=len(exigem),
        percentual=_pct(feitos, len(exigem)),
        nota='Somente itens que exigem desempate',
        associada=contagem is not None,
    ))
    return avancos


@dataclass
class ItemRanking:
    operador: str
    etapa: str
    quantidade: int
    largura: Decimal


def montar_ranking(dados: DadosComparativo) -> tuple[list[ItemRanking], int]:
    contagem: dict[tuple[str, int], int] = {}
    for lancamento in dados.lancamentos:
        chave = (lancamento.operador, lancamento.ordem)
        contagem[chave] = contagem.get(chave, 0) + 1
    ordenado = sorted(contagem.items(), key=lambda par: (-par[1], par[0][1], par[0][0]))
    maior = ordenado[0][1] if ordenado else 0
    itens = [
        ItemRanking(
            operador=operador,
            etapa=rotulo_ordem(ordem),
            quantidade=quantidade,
            largura=(Decimal(quantidade) * 100 / Decimal(maior)) if maior else Decimal(0),
        )
        for (operador, ordem), quantidade in ordenado[:LIMITE_RANKING]
    ]
    return itens, len(dados.lancamentos)


# ---------------------------------------------------------------------------
# Maiores divergências
# ---------------------------------------------------------------------------


@dataclass
class DivergenciaSAP:
    codigo_produto: str
    descricao: str
    fisico: Decimal
    contabil: Decimal
    diferenca: Decimal
    status_classe: str
    status_label: str


@dataclass
class DivergenciaContagens:
    codigo_produto: str
    descricao: str
    posicoes: str
    quantidades: dict[int, Decimal | None]
    amplitude: Decimal
    situacao: str


def montar_divergencias_sap(linhas_sku: list[LinhaComparativo], motivo_sap: str) -> list[DivergenciaSAP]:
    """Físico = consenso das contagens (dupla coincidente) × saldo SAP do canal."""
    if motivo_sap:
        return []
    itens = []
    for linha in linhas_sku:
        consenso = linha.contagens.valor_consenso
        saldo = linha.saldo_selecionado
        if linha.contagens.status != StatusContagem.OK or consenso is None or saldo is None:
            continue
        diferenca = consenso - saldo
        if diferenca == 0:
            continue
        deficit = diferenca < 0
        itens.append(DivergenciaSAP(
            codigo_produto=linha.codigo_produto,
            descricao=linha.descricao,
            fisico=consenso,
            contabil=saldo,
            diferenca=diferenca,
            status_classe='danger' if deficit else 'warning',
            status_label='Déficit físico' if deficit else 'Excesso físico',
        ))
    itens.sort(key=lambda i: (-abs(i.diferenca), i.codigo_produto))
    return itens[:LIMITE_DIVERGENCIAS]


def montar_divergencias_contagens(linhas_sku: list[LinhaComparativo]) -> list[DivergenciaContagens]:
    itens = []
    for linha in linhas_sku:
        if linha.contagens.status != StatusContagem.DIVERGENTE:
            continue
        valores = [m.quantidade for m in linha.medicoes.values() if m.registrada]
        itens.append(DivergenciaContagens(
            codigo_produto=linha.codigo_produto,
            descricao=linha.descricao,
            posicoes=', '.join(linha.nomes_posicao) or '—',
            quantidades={o: linha.medicoes[o].quantidade for o in ORDENS},
            amplitude=max(valores) - min(valores),
            situacao=linha.contagens.orientacao,
        ))
    itens.sort(key=lambda i: (-i.amplitude, i.codigo_produto))
    return itens[:LIMITE_DIVERGENCIAS]


# ---------------------------------------------------------------------------
# Painel
# ---------------------------------------------------------------------------


@dataclass
class PainelInventario:
    comparativo: ComparativoContagem | None
    canal: str
    gerado_em: datetime | None = None
    dados: DadosComparativo | None = None
    totais: dict = field(default_factory=dict)
    indicador_sku: Indicador | None = None
    indicador_posicao: Indicador | None = None
    situacao: list[FatiaSituacao] = field(default_factory=list)
    evolucao: Evolucao | None = None
    avanco: list[AvancoContagem] = field(default_factory=list)
    ranking: list[ItemRanking] = field(default_factory=list)
    total_lancamentos: int = 0
    cobertura: Cobertura | None = None
    cobertura_barras: list[dict] = field(default_factory=list)
    motivo_sap: str = ''
    divergencias_sap: list[DivergenciaSAP] = field(default_factory=list)
    divergencias_contagens: list[DivergenciaContagens] = field(default_factory=list)
    divergencias_inventario: list = field(default_factory=list)
    inventario_divergencias: object = None

    @property
    def provisorio(self) -> bool:
        return self.comparativo is not None and self.comparativo.snapshot_resultado is None

    @property
    def contagens_texto(self) -> str:
        return ' · '.join(f'{a.rotulo} #{a.inventario_id}' for a in self.avanco if a.inventario_id)


def _barras_cobertura(cobertura: Cobertura) -> list[dict]:
    total = cobertura.total
    barras = [
        ('sap', 'SKUs no SAP', cobertura.skus_sap_selecionado),
        ('fisico', 'SKUs no físico', cobertura.skus_fisicos),
        ('ambas', 'Em ambas as bases', cobertura.intersecao),
        ('so_sap', 'Somente no SAP', cobertura.somente_sap),
        ('so_fisico', 'Somente no físico', cobertura.somente_fisico),
    ]
    return [
        {'chave': chave, 'rotulo': rotulo, 'quantidade': qtd, 'percentual': _pct(qtd, total)}
        for chave, rotulo, qtd in barras
    ]


def montar_painel(comparativo: ComparativoContagem | None, canal: str) -> PainelInventario:
    canal = Canal.normalizar(canal)
    inventario, resultado_confronto = _obter_confronto_ultimo_inventario_finalizado()
    painel = PainelInventario(
        comparativo=comparativo,
        canal=canal,
        divergencias_inventario=_montar_maiores_divergencias(resultado_confronto),
        inventario_divergencias=inventario,
    )
    if comparativo is None:
        return painel

    dados = carregar_dados(comparativo)
    linhas_sku = montar_linhas_sku(dados, canal)
    linhas_posicao = montar_linhas_posicao(dados)
    indicador_posicao = calcular_indicador_posicao(linhas_posicao, dados)
    operadores = {l.operador for l in dados.lancamentos if l.operador != NAO_REGISTRADO}
    cobertura = calcular_cobertura(dados, canal)
    ranking, total_lancamentos = montar_ranking(dados)

    painel.dados = dados
    painel.gerado_em = dados.gerado_em
    painel.totais = {
        'skus': len(linhas_sku),
        'posicoes': indicador_posicao.denominador or 0,
        'posicoes_rotulo': indicador_posicao.rotulo_escopo or 'Posições',
        'operadores': len(operadores),
        'lancamentos': len(dados.lancamentos),
    }
    painel.indicador_sku = calcular_indicador_sku(linhas_sku, dados, canal)
    painel.indicador_posicao = indicador_posicao
    painel.situacao = montar_situacao(linhas_sku)
    painel.evolucao = montar_evolucao(linhas_sku, len(linhas_sku))
    painel.avanco = montar_avanco(linhas_sku, dados)
    painel.ranking = ranking
    painel.total_lancamentos = total_lancamentos
    painel.cobertura = cobertura
    painel.cobertura_barras = _barras_cobertura(cobertura)
    painel.motivo_sap = dados.motivo_sap(canal)
    painel.divergencias_sap = montar_divergencias_sap(linhas_sku, painel.motivo_sap)
    painel.divergencias_contagens = montar_divergencias_contagens(linhas_sku)
    return painel
