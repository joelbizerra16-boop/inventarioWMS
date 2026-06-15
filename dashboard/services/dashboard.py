from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal

from django.db.models import Count
from django.db.models.functions import TruncMonth
from django.utils import timezone

from core.services.perf_diagnostico import medir_etapa
from estoque_fisico.models import EstoqueFisico
from estoque_sap.models import EstoqueSAP
from inventario.models import CicloInventario, Inventario, InventarioItem
from inventario.services.ciclico import (
    calcular_resumo_ciclo,
    obter_indicadores_ciclico_dashboard,
)
from inventario.services.confronto import executar_confronto
from posicoes.models import Posicao
from produtos.models import Produto


@dataclass
class GraficoDashboard:
    id: str
    titulo: str
    tipo: str
    labels: list[str]
    valores: list[int]
    cores: list[str] = field(default_factory=list)


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


def _obter_confronto_ultimo_inventario_finalizado() -> tuple[int, int, Decimal]:
    inventario = (
        Inventario.objects.filter(status=Inventario.Status.FINALIZADO)
        .order_by('-data_criacao')
        .first()
    )

    if inventario is None:
        return 0, 0, Decimal('0')

    resultado = executar_confronto(inventario.pk)
    return (
        resultado.resumo.produtos_corretos,
        resultado.resumo.produtos_divergentes,
        resultado.resumo.acuracidade,
    )


def _montar_graficos_geral(
    abertos: int,
    andamento: int,
    finalizados: int,
) -> list[GraficoDashboard]:
    evolucao_qs = (
        Inventario.objects.filter(
            data_criacao__gte=timezone.now() - timedelta(days=180),
        )
        .annotate(mes=TruncMonth('data_criacao'))
        .values('mes')
        .annotate(total=Count('id'))
        .order_by('mes')
    )
    evolucao_labels = [
        timezone.localtime(item['mes']).strftime('%m/%y') for item in evolucao_qs
    ] or ['—']
    evolucao_valores = [item['total'] for item in evolucao_qs] or [0]

    itens_ativos = InventarioItem.objects.filter(
        inventario__status__in=(
            Inventario.Status.ABERTO,
            Inventario.Status.EM_ANDAMENTO,
        ),
    )
    planejados = itens_ativos.count()
    contados = itens_ativos.filter(quantidade_fisica__gt=0).count()
    if planejados == 0:
        planejados = Produto.objects.filter(ativo=True).count()
        contados = (
            EstoqueFisico.objects.values('produto_id').distinct().count()
        )

    ranking = list(
        Inventario.objects.values('usuario__nome')
        .annotate(total=Count('id'))
        .order_by('-total')[:5],
    )
    ranking_labels = [item['usuario__nome'] or '—' for item in ranking] or ['—']
    ranking_valores = [item['total'] for item in ranking] or [0]

    return [
        GraficoDashboard(
            id='status_inventarios',
            titulo='Status Inventários',
            tipo='doughnut',
            labels=['Abertos', 'Em Andamento', 'Finalizados'],
            valores=[abertos, andamento, finalizados],
            cores=[COR_CINZA, COR_AZUL, COR_VERDE],
        ),
        GraficoDashboard(
            id='evolucao_inventarios',
            titulo='Evolução Inventários',
            tipo='line',
            labels=evolucao_labels,
            valores=evolucao_valores,
            cores=[COR_AZUL],
        ),
        GraficoDashboard(
            id='planejado_contado',
            titulo='Planejado x Contado',
            tipo='bar',
            labels=['Planejados', 'Contados'],
            valores=[planejados, contados],
            cores=[COR_CINZA, COR_AZUL],
        ),
        GraficoDashboard(
            id='ranking_usuarios',
            titulo='Ranking Usuários',
            tipo='bar',
            labels=ranking_labels,
            valores=ranking_valores,
            cores=[COR_AZUL],
        ),
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
            EstoqueSAP.objects.values('produto_id').distinct().count()
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
        produtos_corretos, produtos_divergentes, acuracidade = (
            _obter_confronto_ultimo_inventario_finalizado()
        )
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
    )
