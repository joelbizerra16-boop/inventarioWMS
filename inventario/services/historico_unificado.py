"""Histórico unificado de inventários gerais e cíclicos.

O Inventário Cíclico fica de fora do hotfix de fotografia: a tela está oculta
em produção e o detalhe encerrado ainda lê produtos, posições e SAP atuais.
Não há snapshot de linhas cíclicas para preservar.
"""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from django.utils import timezone

from inventario.models import CicloInventario, Inventario
from inventario.services.ciclico import StatusCiclo, StatusItemCiclico
from inventario.services.ciclico_historico import (
    StatusHistoricoCiclo,
    _linha_de_ciclo,
    _metricas_ciclo,
    obter_detalhe_historico_ciclo,
    obter_status_exibicao_ciclo,
)
from inventario.services.ciclico_relatorio import _nome_usuario, obter_grupos_consulta_ciclo


class TipoHistorico:
    GERAL = 'GERAL'
    CICLICO = 'CICLICO'

    LABELS = {
        GERAL: 'Inventário',
        CICLICO: 'Inventário Cíclico',
    }

    FILTROS = [
        ('', 'Todos'),
        (GERAL, 'Inventário'),
    ]


class StatusHistoricoUnificado:
    FINALIZADO = 'FINALIZADO'
    ENCERRADO = 'ENCERRADO'
    EM_ANDAMENTO = 'EM_ANDAMENTO'
    CANCELADO = 'CANCELADO'
    REABERTO = 'REABERTO'

    LABELS = {
        FINALIZADO: 'Finalizado',
        ENCERRADO: 'Finalizado',
        EM_ANDAMENTO: 'Em andamento',
        CANCELADO: 'Cancelado',
        REABERTO: 'Reaberto',
    }

    BADGES = {
        FINALIZADO: 'success',
        ENCERRADO: 'success',
        EM_ANDAMENTO: 'primary',
        CANCELADO: 'dark',
        REABERTO: 'warning',
    }

    FILTROS = [
        ('', 'Todos'),
        (FINALIZADO, 'Finalizado'),
        (EM_ANDAMENTO, 'Em andamento'),
        (CANCELADO, 'Cancelado'),
        (REABERTO, 'Reaberto'),
    ]


@dataclass
class HistoricoLinhaUnificada:
    pk: int
    tipo: str
    tipo_label: str
    data_referencia: datetime
    status_codigo: str
    status_label: str
    status_badge: str
    acuracidade: Decimal | None
    responsavel: str
    divergentes: int
    quantidade_itens: int
    conciliados: int
    frente: str = ''


@dataclass
class HistoricoIndicadores:
    total_inventarios: int
    total_ciclos: int
    acuracidade_media: Decimal | None
    total_divergencias: int


@dataclass
class HistoricoPosicaoDetalhe:
    alocacao: str
    codigo: str
    quantidade: str


@dataclass
class HistoricoProdutoDetalhe:
    codigo_produto: str
    descricao: str
    embalagem: str
    sap: str
    contado: str
    diferenca: str
    status: str
    posicoes: list[HistoricoPosicaoDetalhe]


@dataclass
class HistoricoDetalheUnificado:
    pk: int
    tipo: str
    tipo_label: str
    titulo: str
    data_referencia: datetime
    responsavel: str
    status_label: str
    status_badge: str
    acuracidade: Decimal | None
    quantidade_itens: int
    conciliados: int
    divergentes: int
    produtos: list[HistoricoProdutoDetalhe]
    aviso: str = ''


def _parse_date(valor: str) -> date | None:
    valor = (valor or '').strip()
    if not valor:
        return None
    try:
        return datetime.strptime(valor, '%Y-%m-%d').date()
    except ValueError:
        return None


def _status_inventario(inventario: Inventario) -> tuple[str, str, str]:
    if inventario.status == Inventario.Status.FINALIZADO:
        return (
            StatusHistoricoUnificado.FINALIZADO,
            StatusHistoricoUnificado.LABELS[StatusHistoricoUnificado.FINALIZADO],
            StatusHistoricoUnificado.BADGES[StatusHistoricoUnificado.FINALIZADO],
        )
    return (
        StatusHistoricoUnificado.EM_ANDAMENTO,
        StatusHistoricoUnificado.LABELS[StatusHistoricoUnificado.EM_ANDAMENTO],
        StatusHistoricoUnificado.BADGES[StatusHistoricoUnificado.EM_ANDAMENTO],
    )


def _status_ciclo_unificado(ciclo: CicloInventario) -> tuple[str, str, str]:
    codigo, label = obter_status_exibicao_ciclo(ciclo)
    mapa = {
        StatusHistoricoCiclo.ENCERRADO: StatusHistoricoUnificado.ENCERRADO,
        StatusHistoricoCiclo.EM_ANDAMENTO: StatusHistoricoUnificado.EM_ANDAMENTO,
        StatusHistoricoCiclo.CANCELADO: StatusHistoricoUnificado.CANCELADO,
        StatusHistoricoCiclo.REABERTO: StatusHistoricoUnificado.REABERTO,
    }
    unificado = mapa.get(codigo, StatusHistoricoUnificado.EM_ANDAMENTO)
    return unificado, label, StatusHistoricoUnificado.BADGES.get(unificado, 'secondary')


def _responsavel_inventario(inventario: Inventario) -> str:
    alvo = inventario.usuario_finalizacao or inventario.usuario
    if alvo is None:
        return 'Não informado'
    return alvo.nome or 'Não informado'


def _linha_inventario(inventario: Inventario) -> HistoricoLinhaUnificada:
    status_codigo, status_label, status_badge = _status_inventario(inventario)
    data_ref = inventario.data_finalizacao or inventario.data_criacao
    responsavel = _responsavel_inventario(inventario)
    return HistoricoLinhaUnificada(
        pk=inventario.pk,
        tipo=TipoHistorico.GERAL,
        tipo_label=TipoHistorico.LABELS[TipoHistorico.GERAL],
        data_referencia=data_ref,
        status_codigo=status_codigo,
        status_label=status_label,
        status_badge=status_badge,
        acuracidade=inventario.taxa_acuracidade,
        responsavel=responsavel,
        divergentes=inventario.quantidade_divergentes or 0,
        quantidade_itens=inventario.quantidade_produtos or inventario.quantidade_itens or 0,
        conciliados=inventario.quantidade_conciliados or 0,
        frente=inventario.categoria_rotulo,
    )


def _linha_ciclo(ciclo: CicloInventario) -> HistoricoLinhaUnificada:
    linha = _linha_de_ciclo(ciclo)
    status_codigo, status_label, status_badge = _status_ciclo_unificado(ciclo)
    data_ref = ciclo.data_encerramento or ciclo.data_criacao
    conciliados = max(linha.validados, 0)
    if ciclo.status_ciclo in (StatusCiclo.ENCERRADO, StatusCiclo.ARQUIVADO):
        conciliados = max((linha.skus_contados or 0) - (linha.divergentes or 0), 0)
    return HistoricoLinhaUnificada(
        pk=ciclo.pk,
        tipo=TipoHistorico.CICLICO,
        tipo_label=TipoHistorico.LABELS[TipoHistorico.CICLICO],
        data_referencia=data_ref,
        status_codigo=status_codigo,
        status_label=status_label,
        status_badge=status_badge,
        acuracidade=linha.acuracidade,
        responsavel=linha.usuario_responsavel,
        divergentes=linha.divergentes,
        quantidade_itens=linha.skus_planejados,
        conciliados=conciliados,
    )


def _filtra_periodo(data_ref: datetime, inicio: date | None, fim: date | None) -> bool:
    local = timezone.localtime(data_ref).date()
    if inicio and local < inicio:
        return False
    if fim and local > fim:
        return False
    return True


def _filtra_usuario(responsavel: str, termo: str) -> bool:
    if not termo:
        return True
    return termo.lower() in responsavel.lower()


def listar_historico_unificado(
    *,
    periodo_inicio: str = '',
    periodo_fim: str = '',
    status_filtro: str = '',
    tipo_filtro: str = '',
    usuario_filtro: str = '',
    categoria: str | None = None,
) -> list[HistoricoLinhaUnificada]:
    """Histórico de inventários finalizados (da categoria, quando informada)."""
    inicio = _parse_date(periodo_inicio)
    fim = _parse_date(periodo_fim)
    usuario_filtro = usuario_filtro.strip()
    linhas: list[HistoricoLinhaUnificada] = []

    if tipo_filtro in ('', TipoHistorico.GERAL):
        inventarios = (
            Inventario.objects.filter(status=Inventario.Status.FINALIZADO)
            .select_related('usuario', 'usuario_finalizacao')
            .order_by('-data_finalizacao', '-data_criacao')
        )
        if categoria:
            inventarios = inventarios.filter(categoria=categoria)
        for inventario in inventarios:
            linha = _linha_inventario(inventario)
            if not _filtra_periodo(linha.data_referencia, inicio, fim):
                continue
            if status_filtro and linha.status_codigo != status_filtro:
                continue
            if not _filtra_usuario(linha.responsavel, usuario_filtro):
                continue
            linhas.append(linha)

    if tipo_filtro == TipoHistorico.CICLICO:
        ciclos = CicloInventario.objects.select_related(
            'usuario_criacao',
            'usuario_criacao__perfil_operacional',
            'usuario_encerramento',
            'usuario_encerramento__perfil_operacional',
        ).order_by('-data_criacao')
        for ciclo in ciclos:
            linha = _linha_ciclo(ciclo)
            if not _filtra_periodo(linha.data_referencia, inicio, fim):
                continue
            if status_filtro and linha.status_codigo != status_filtro:
                continue
            if not _filtra_usuario(linha.responsavel, usuario_filtro):
                continue
            linhas.append(linha)

    linhas.sort(key=lambda item: item.data_referencia, reverse=True)
    return linhas


def calcular_indicadores_historico(linhas: list[HistoricoLinhaUnificada]) -> HistoricoIndicadores:
    inventarios = [l for l in linhas if l.tipo == TipoHistorico.GERAL]
    ciclos = [l for l in linhas if l.tipo == TipoHistorico.CICLICO]
    acuracias = [l.acuracidade for l in linhas if l.acuracidade is not None]
    media = None
    if acuracias:
        media = (sum(acuracias, Decimal('0')) / Decimal(len(acuracias))).quantize(Decimal('0.01'))
    return HistoricoIndicadores(
        total_inventarios=len(inventarios),
        total_ciclos=len(ciclos),
        acuracidade_media=media,
        total_divergencias=sum(l.divergentes for l in linhas),
    )


def _status_ciclo_label(sku_status: str) -> str:
    if sku_status in (
        StatusItemCiclico.VALIDADO,
        StatusItemCiclico.CONTADO,
    ):
        return 'Conciliado'
    if sku_status == StatusItemCiclico.VALIDADO_DIVERGENCIA:
        return 'Divergente aceito'
    if sku_status == StatusItemCiclico.DIVERGENTE:
        return 'Divergente'
    return StatusItemCiclico.LABELS.get(sku_status, sku_status)


def obter_detalhe_historico_unificado(tipo: str, pk: int) -> HistoricoDetalheUnificado | None:
    if tipo == TipoHistorico.GERAL:
        inventario = Inventario.objects.select_related(
            'usuario',
            'usuario_finalizacao',
        ).filter(pk=pk, status=Inventario.Status.FINALIZADO).first()
        if inventario is None:
            return None
        _, status_label, status_badge = _status_inventario(inventario)
        produtos: list[HistoricoProdutoDetalhe] = []
        snapshot = inventario.snapshot_resultado if isinstance(inventario.snapshot_resultado, dict) else None
        aviso = ''
        if not snapshot or 'produtos' not in snapshot:
            aviso = 'Dado não disponível no fechamento'
            acuracidade = None
            quantidade = 0
            conciliados = 0
            divergentes = 0
        else:
            resumo = snapshot.get('resumo') or {}
            acuracidade = (
                Decimal(str(resumo['acuracidade'])) if resumo.get('acuracidade') is not None
                else inventario.taxa_acuracidade
            )
            quantidade = int(resumo.get('total_produtos') or inventario.quantidade_produtos or 0)
            conciliados = int(resumo.get('conciliados') or inventario.quantidade_conciliados or 0)
            divergentes = int(resumo.get('divergentes') or inventario.quantidade_divergentes or 0)
            for item in snapshot.get('produtos', []):
                fotos = item.get('fotos') if isinstance(item.get('fotos'), dict) else {}
                foto_total = fotos.get('total') if isinstance(fotos.get('total'), dict) else {}
                if foto_total.get('status_label'):
                    status = foto_total['status_label']
                elif 'status' in item:
                    status = item['status']
                else:
                    status = 'Dado não disponível no fechamento'
                produtos.append(HistoricoProdutoDetalhe(
                    codigo_produto=item['codigo_produto'] if 'codigo_produto' in item else 'Dado não disponível no fechamento',
                    descricao=item['descricao'] if 'descricao' in item else 'Dado não disponível no fechamento',
                    embalagem=item.get('embalagem', '—'),
                    sap=item['sap'] if 'sap' in item else 'Dado não disponível no fechamento',
                    contado=item['contado'] if 'contado' in item else 'Dado não disponível no fechamento',
                    diferenca=item['diferenca'] if 'diferenca' in item else 'Dado não disponível no fechamento',
                    status=status,
                    posicoes=[
                        HistoricoPosicaoDetalhe(
                            alocacao=p.get('alocacao', '—'),
                            codigo=p.get('codigo', '—'),
                            quantidade=p.get('quantidade', '0'),
                        )
                        for p in item.get('posicoes', [])
                    ],
                ))
        return HistoricoDetalheUnificado(
            pk=inventario.pk,
            tipo=TipoHistorico.GERAL,
            tipo_label=TipoHistorico.LABELS[TipoHistorico.GERAL],
            titulo=f'Inventário #{inventario.pk}',
            data_referencia=inventario.data_finalizacao or inventario.data_criacao,
            responsavel=_responsavel_inventario(inventario),
            status_label=status_label,
            status_badge=status_badge,
            acuracidade=acuracidade,
            quantidade_itens=quantidade,
            conciliados=conciliados,
            divergentes=divergentes,
            produtos=produtos,
            aviso=aviso,
        )

    # Cíclico encerrado continua ao vivo: a funcionalidade está oculta em
    # produção e este hotfix não grava nem reescreve snapshot cíclico.
    if tipo == TipoHistorico.CICLICO:
        detalhe_ciclo = obter_detalhe_historico_ciclo(pk)
        if detalhe_ciclo is None:
            return None
        ciclo = detalhe_ciclo.ciclo
        _, status_label, status_badge = _status_ciclo_unificado(ciclo)
        metricas = _metricas_ciclo(ciclo)
        conciliados = max(int(metricas['contados']) - int(metricas['divergentes']), 0)
        produtos = []
        for grupo in obter_grupos_consulta_ciclo(ciclo.pk, incluir_historico=False):
            fisico = grupo.fisico_total if grupo.fisico_total is not None else Decimal('0')
            diferenca = grupo.diferenca_cosan if grupo.diferenca_cosan is not None else Decimal('0')
            produtos.append(HistoricoProdutoDetalhe(
                codigo_produto=grupo.codigo_produto,
                descricao=grupo.descricao,
                embalagem=grupo.embalagem or '—',
                sap=str(grupo.sap_total),
                contado=str(fisico),
                diferenca=str(diferenca),
                status=_status_ciclo_label(grupo.status_contagem),
                posicoes=[
                    HistoricoPosicaoDetalhe(
                        alocacao=p.alocacao or p.codigo_posicao,
                        codigo=p.codigo_posicao,
                        quantidade=str(p.quantidade_fisica or '0'),
                    )
                    for p in grupo.posicoes
                    if p.quantidade_fisica is not None
                ],
            ))
        return HistoricoDetalheUnificado(
            pk=ciclo.pk,
            tipo=TipoHistorico.CICLICO,
            tipo_label=TipoHistorico.LABELS[TipoHistorico.CICLICO],
            titulo=f'Ciclo #{ciclo.pk}',
            data_referencia=ciclo.data_encerramento or ciclo.data_criacao,
            responsavel=detalhe_ciclo.linha.usuario_responsavel,
            status_label=status_label,
            status_badge=status_badge,
            acuracidade=detalhe_ciclo.acuracidade,
            quantidade_itens=int(metricas['planejados']),
            conciliados=conciliados,
            divergentes=int(metricas['divergentes']),
            produtos=produtos,
        )

    return None
