from dataclasses import dataclass, field
from decimal import Decimal

from django.db.models import Count
from django.utils import timezone

from accounts.models import Usuario
from core.services.perf_diagnostico import medir_etapa
from estoque_fisico.models import EstoqueFisico
from estoque_sap.models import EstoqueSAP
from inventario.models import CicloInventario, Inventario, InventarioItem
from inventario.services.ciclico import (
    calcular_resumo_ciclo,
    obter_indicadores_ciclico_dashboard,
)
from inventario.services.confronto import ResultadoConfronto, executar_confronto
from posicoes.models import Posicao
from produtos.models import Produto


@dataclass
class GraficoDashboard:
    id: str
    titulo: str
    tipo: str
    labels: list[str]
    valores: list[float]
    cores: list[str] = field(default_factory=list)
    mensagem_vazia: str = ''
    subtitulo: str = ''
    sufixo_valor: str = ''
    centro_valor: str = ''
    centro_label: str = ''
    progresso_texto: str = ''
    progresso_percentual: float = 0


@dataclass
class LinhaDivergenciaDashboard:
    codigo_produto: str
    descricao: str
    fisico: Decimal
    total_contabil: Decimal
    diferenca: Decimal
    status_classe: str
    status_label: str


@dataclass
class IndicadoresDashboard:
    total_produtos: int
    total_posicoes: int
    produtos_estoque_sap: int
    produtos_estoque_fisico: int
    inventarios_abertos: int
    inventarios_em_andamento: int
    inventarios_finalizados: int
    produtos_corretos: int
    produtos_divergentes: int
    acuracidade: Decimal
    ciclico_itens_planejados: int
    ciclico_itens_contados: int
    ciclico_percentual_concluido: Decimal
    ciclico_skus_divergentes: int
    ciclico_acuracidade: Decimal | None
    graficos_geral: list[GraficoDashboard]
    graficos_ciclico: list[GraficoDashboard]
    maiores_divergencias: list[LinhaDivergenciaDashboard]
    maiores_divergencias_inventario: Inventario | None

    @property
    def grafico_inventarios_labels(self) -> list[str]:
        grafico = next((g for g in self.graficos_geral if g.id == 'status_inventarios'), None)
        return grafico.labels if grafico else []

    @property
    def grafico_inventarios_valores(self) -> list[int]:
        grafico = next((g for g in self.graficos_geral if g.id == 'status_inventarios'), None)
        return grafico.valores if grafico else []

    @property
    def grafico_confronto_labels(self) -> list[str]:
        grafico = next((g for g in self.graficos_geral if g.id == 'planejado_contado'), None)
        return grafico.labels if grafico else []

    @property
    def grafico_confronto_valores(self) -> list[int]:
        grafico = next((g for g in self.graficos_geral if g.id == 'planejado_contado'), None)
        return grafico.valores if grafico else []


COR_AZUL = '#2563EB'
COR_AZUL_ESCURO = '#1E40AF'
COR_AZUL_CLARO = '#60A5FA'
COR_VERDE = '#16A34A'
COR_LARANJA = '#F97316'
COR_VERMELHO = '#DC2626'
COR_CINZA = '#64748B'
COR_CINZA_CLARO = '#E2E8F0'
PALETA_EMBALAGENS = [COR_AZUL_ESCURO, COR_AZUL, COR_AZUL_CLARO, COR_CINZA, COR_AZUL]


def _obter_inventario_finalizado_mais_recente() -> Inventario | None:
    return (
        Inventario.objects.filter(status=Inventario.Status.FINALIZADO)
        .order_by('-data_criacao')
        .first()
    )


def _obter_confronto_ultimo_inventario_finalizado() -> tuple[Inventario | None, ResultadoConfronto | None]:
    """Confronto do último inventário geral finalizado.

    Reaproveitado pelos cards de Acuracidade Geral/Corretos/Divergentes e
    pela análise "Maiores Divergências", para não duplicar a regra de
    cálculo nem repetir a consulta. Usa `canal=None` (comportamento legado,
    soma total do SAP) — o mesmo conceito que este Dashboard já usa hoje.
    """
    inventario = _obter_inventario_finalizado_mais_recente()
    if inventario is None:
        return None, None
    return inventario, executar_confronto(inventario.pk)


def _montar_evolucao_acuracidade(limite: int = 10) -> GraficoDashboard:
    inventarios = list(
        Inventario.objects.filter(
            status=Inventario.Status.FINALIZADO,
            taxa_acuracidade__isnull=False,
            quantidade_produtos__gt=0,
        )
        .order_by('-data_finalizacao')[:limite]
    )
    inventarios.reverse()

    labels = [
        f'#{inv.pk} · {timezone.localtime(inv.data_finalizacao):%d/%m}'
        for inv in inventarios
    ]
    valores = [float(inv.taxa_acuracidade) for inv in inventarios]

    return GraficoDashboard(
        id='evolucao_acuracidade',
        titulo='Evolução da Acuracidade',
        tipo='line',
        labels=labels,
        valores=valores,
        cores=[COR_AZUL],
        mensagem_vazia='' if valores else 'Nenhum inventário finalizado para exibir evolução.',
        sufixo_valor='%',
        subtitulo='Acuracidade dos últimos inventários finalizados (Geral)',
    )


def _montar_planejado_contado() -> GraficoDashboard:
    inventario = (
        Inventario.objects.filter(status=Inventario.Status.EM_ANDAMENTO)
        .order_by('-data_criacao')
        .first()
    )
    if inventario is None:
        return GraficoDashboard(
            id='planejado_contado',
            titulo='Planejado x Contado',
            tipo='doughnut',
            labels=[],
            valores=[],
            cores=[COR_AZUL, COR_CINZA],
            mensagem_vazia='Nenhum inventário em andamento no momento.',
            subtitulo='Progresso do inventário em andamento (Geral)',
        )

    resultado = executar_confronto(inventario.pk)
    planejado = resultado.resumo.total_produtos
    contado = sum(1 for linha in resultado.linhas if linha.fisico > 0)
    pendente = max(planejado - contado, 0)
    percentual = round(contado / planejado * 100) if planejado else 0

    mensagem_vazia = ''
    if planejado == 0:
        mensagem_vazia = 'Nenhuma contagem registrada neste inventário.'

    return GraficoDashboard(
        id='planejado_contado',
        titulo='Planejado x Contado',
        tipo='doughnut',
        labels=['Contados', 'Pendentes'],
        valores=[contado, pendente],
        cores=[COR_AZUL, COR_CINZA_CLARO],
        mensagem_vazia=mensagem_vazia,
        centro_valor=f'{percentual}%',
        centro_label='Concluído',
        progresso_texto=f'{contado} de {planejado} itens — Inventário #{inventario.pk}',
        progresso_percentual=percentual,
        subtitulo='Progresso do inventário em andamento (Geral)',
    )


def _montar_ranking_contagem(limite: int = 10) -> GraficoDashboard:
    agregados = list(
        InventarioItem.objects.exclude(usuario_contagem__isnull=True)
        .values('usuario_contagem_id')
        .annotate(total_itens=Count('id'))
        .order_by('-total_itens')[:limite]
    )

    nomes = dict(
        Usuario.objects.filter(
            user_id__in=[item['usuario_contagem_id'] for item in agregados],
        ).values_list('user_id', 'nome'),
    )

    labels = [
        nomes.get(item['usuario_contagem_id']) or '—' for item in agregados
    ]
    valores = [item['total_itens'] for item in agregados]

    return GraficoDashboard(
        id='ranking_usuarios',
        titulo='Ranking de Contagem',
        tipo='bar',
        labels=labels,
        valores=valores,
        cores=[COR_AZUL],
        mensagem_vazia='' if valores else 'Nenhuma contagem registrada.',
        subtitulo='Top 10 usuários por itens contados (Geral)',
    )


def _montar_graficos_geral(
    abertos: int,
    andamento: int,
    finalizados: int,
) -> list[GraficoDashboard]:
    return [
        GraficoDashboard(
            id='status_inventarios',
            titulo='Status dos Inventários',
            tipo='doughnut',
            labels=['Abertos', 'Em Andamento', 'Finalizados'],
            valores=[abertos, andamento, finalizados],
            cores=[COR_CINZA, COR_AZUL, COR_VERDE],
            centro_valor=str(abertos + andamento + finalizados),
            centro_label='Total',
            subtitulo='Quantidade de inventários por situação (Geral)',
        ),
        _montar_evolucao_acuracidade(),
        _montar_planejado_contado(),
        _montar_ranking_contagem(),
    ]


def _montar_maiores_divergencias(
    resultado: ResultadoConfronto | None,
    limite: int = 10,
) -> list[LinhaDivergenciaDashboard]:
    if resultado is None:
        return []

    divergentes = [linha for linha in resultado.linhas if linha.possui_divergencia]
    divergentes.sort(key=lambda linha: abs(linha.diferenca), reverse=True)

    return [
        LinhaDivergenciaDashboard(
            codigo_produto=linha.codigo_produto,
            descricao=linha.descricao,
            fisico=linha.fisico,
            total_contabil=linha.total_contabil,
            diferenca=linha.diferenca,
            status_classe=linha.status_classe,
            status_label=linha.status_label,
        )
        for linha in divergentes[:limite]
    ]


def _obter_ciclo_dashboard() -> CicloInventario | None:
    ciclo = CicloInventario.objects.filter(
        status_ciclo=CicloInventario.StatusCiclo.ATIVO,
    ).order_by('-pk').first()
    if ciclo is not None:
        return ciclo
    return CicloInventario.objects.order_by('-pk').first()


def _montar_graficos_ciclico() -> tuple[list[GraficoDashboard], int, Decimal | None]:
    ciclo = _obter_ciclo_dashboard()
    if ciclo is None:
        return [
            GraficoDashboard(
                id='status_ciclos',
                titulo='Status Ciclos',
                tipo='doughnut',
                labels=['Ativo', 'Finalizado', 'Cancelado'],
                valores=[0, 0, 0],
                cores=[COR_AZUL, COR_VERDE, COR_CINZA],
            ),
            GraficoDashboard(
                id='canais',
                titulo='Canais',
                tipo='bar',
                labels=['Cosan', 'Brida'],
                valores=[0, 0],
                cores=[COR_AZUL, COR_AZUL_ESCURO],
            ),
            GraficoDashboard(
                id='acuracidade_ciclico',
                titulo='Acuracidade',
                tipo='doughnut',
                labels=['Conciliado', 'Acima SAP', 'Abaixo SAP'],
                valores=[0, 0, 0],
                cores=[COR_VERDE, COR_LARANJA, COR_VERMELHO],
            ),
            GraficoDashboard(
                id='embalagens',
                titulo='Embalagens',
                tipo='bar',
                labels=['Sem embalagem'],
                valores=[0],
                cores=[COR_AZUL],
            ),
            GraficoDashboard(
                id='divergencias',
                titulo='Divergências',
                tipo='bar',
                labels=['Pendentes', 'Contados', 'Divergentes', 'Validados'],
                valores=[0, 0, 0, 0],
                cores=[COR_CINZA, COR_AZUL, COR_VERMELHO, COR_VERDE],
            ),
        ], 0, None

    with medir_etapa('dashboard._montar_graficos_ciclico.calcular_resumo_ciclo'):
        resumo = calcular_resumo_ciclo(ciclo)

    status_ciclos_qs = (
        CicloInventario.objects.values('status_ciclo')
        .annotate(total=Count('id'))
        .order_by('status_ciclo')
    )
    mapa_status = {
        CicloInventario.StatusCiclo.ATIVO: 'Ativo',
        CicloInventario.StatusCiclo.ENCERRADO: 'Finalizado',
        CicloInventario.StatusCiclo.ARQUIVADO: 'Cancelado',
    }
    if status_ciclos_qs:
        status_labels = [
            mapa_status.get(item['status_ciclo'], item['status_ciclo'])
            for item in status_ciclos_qs
        ]
        status_valores = [item['total'] for item in status_ciclos_qs]
    else:
        status_labels = ['Ativo', 'Finalizado', 'Cancelado']
        status_valores = [0, 0, 0]

    embalagens = sorted(
        resumo.por_embalagem.items(),
        key=lambda par: par[1],
        reverse=True,
    )[:5]
    if embalagens:
        emb_labels = [nome for nome, _ in embalagens]
        emb_valores = [qtd for _, qtd in embalagens]
    else:
        emb_labels = ['Sem embalagem']
        emb_valores = [0]

    canais_labels = ['Cosan', 'Brida']
    canais_valores = [resumo.por_canal_cosan, resumo.por_canal_brida]

    divergentes = resumo.skus_divergentes
    pendentes = resumo.skus_pendentes
    validados = resumo.skus_validados
    contados = resumo.skus_contados

    contados_fisico = (
        resumo.skus_conciliados + resumo.skus_acima_sap + resumo.skus_abaixo_sap
    )
    acuracidade = None
    if contados_fisico > 0:
        acuracidade = (
            Decimal(resumo.skus_conciliados) / Decimal(contados_fisico) * Decimal('100')
        ).quantize(Decimal('0.01'))

    graficos = [
        GraficoDashboard(
            id='status_ciclos',
            titulo='Status Ciclos',
            tipo='doughnut',
            labels=status_labels,
            valores=status_valores,
            cores=[COR_AZUL, COR_VERDE, COR_CINZA],
        ),
        GraficoDashboard(
            id='canais',
            titulo='Canais',
            tipo='bar',
            labels=canais_labels,
            valores=canais_valores,
            cores=[COR_AZUL, COR_AZUL_ESCURO],
        ),
        GraficoDashboard(
            id='acuracidade_ciclico',
            titulo='Acuracidade',
            tipo='doughnut',
            labels=['Conciliado', 'Acima SAP', 'Abaixo SAP'],
            valores=[
                resumo.skus_conciliados,
                resumo.skus_acima_sap,
                resumo.skus_abaixo_sap,
            ],
            cores=[COR_VERDE, COR_LARANJA, COR_VERMELHO],
        ),
        GraficoDashboard(
            id='embalagens',
            titulo='Embalagens',
            tipo='bar',
            labels=emb_labels,
            valores=emb_valores,
            cores=PALETA_EMBALAGENS[:len(emb_labels)],
        ),
        GraficoDashboard(
            id='divergencias',
            titulo='Divergências',
            tipo='bar',
            labels=['Pendentes', 'Contados', 'Divergentes', 'Validados'],
            valores=[pendentes, contados, divergentes, validados],
            cores=[COR_CINZA, COR_AZUL, COR_VERMELHO, COR_VERDE],
        ),
    ]
    return graficos, divergentes, acuracidade


def obter_indicadores_dashboard() -> IndicadoresDashboard:
    with medir_etapa('dashboard.obter_indicadores_dashboard.contadores_basicos'):
        total_produtos = Produto.objects.count()
        total_posicoes = Posicao.objects.count()
        produtos_estoque_sap = (
            EstoqueSAP.objects.filter(
                produto__in=Produto.objects.elegiveis_para_inventario(),
            ).values('produto_id').distinct().count()
        )
        produtos_estoque_fisico = (
            EstoqueFisico.objects.values('produto_id').distinct().count()
        )
        inventarios_abertos = Inventario.objects.filter(
            status=Inventario.Status.ABERTO,
        ).count()
        inventarios_em_andamento = Inventario.objects.filter(
            status=Inventario.Status.EM_ANDAMENTO,
        ).count()
        inventarios_finalizados = Inventario.objects.filter(
            status=Inventario.Status.FINALIZADO,
        ).count()

    with medir_etapa('dashboard.obter_indicadores_dashboard.confronto_ultimo_inventario'):
        maiores_divergencias_inventario, resultado_ultimo_inventario = (
            _obter_confronto_ultimo_inventario_finalizado()
        )
        if resultado_ultimo_inventario is None:
            produtos_corretos, produtos_divergentes, acuracidade = 0, 0, Decimal('0')
        else:
            produtos_corretos = resultado_ultimo_inventario.resumo.produtos_corretos
            produtos_divergentes = resultado_ultimo_inventario.resumo.produtos_divergentes
            acuracidade = resultado_ultimo_inventario.resumo.acuracidade
        maiores_divergencias = _montar_maiores_divergencias(resultado_ultimo_inventario)
    with medir_etapa('dashboard.obter_indicadores_dashboard.obter_resumo_ciclico'):
        ciclico = obter_indicadores_ciclico_dashboard()
    with medir_etapa('dashboard.obter_indicadores_dashboard.obter_resumo_ciclico_graficos'):
        graficos_ciclico, ciclico_divergentes, ciclico_acuracidade = _montar_graficos_ciclico()

    with medir_etapa('dashboard.obter_indicadores_dashboard.graficos_geral'):
        graficos_geral = _montar_graficos_geral(
            inventarios_abertos,
            inventarios_em_andamento,
            inventarios_finalizados,
        )

    return IndicadoresDashboard(
        total_produtos=total_produtos,
        total_posicoes=total_posicoes,
        produtos_estoque_sap=produtos_estoque_sap,
        produtos_estoque_fisico=produtos_estoque_fisico,
        inventarios_abertos=inventarios_abertos,
        inventarios_em_andamento=inventarios_em_andamento,
        inventarios_finalizados=inventarios_finalizados,
        produtos_corretos=produtos_corretos,
        produtos_divergentes=produtos_divergentes,
        acuracidade=acuracidade,
        ciclico_itens_planejados=ciclico.itens_planejados,
        ciclico_itens_contados=ciclico.itens_contados,
        ciclico_percentual_concluido=ciclico.percentual_concluido,
        ciclico_skus_divergentes=ciclico_divergentes,
        ciclico_acuracidade=ciclico_acuracidade,
        graficos_geral=graficos_geral,
        graficos_ciclico=graficos_ciclico,
        maiores_divergencias=maiores_divergencias,
        maiores_divergencias_inventario=maiores_divergencias_inventario,
    )
