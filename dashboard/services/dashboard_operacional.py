"""Indicadores operacionais em tempo real para inventário multiusuário."""

from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal

from django.db.models import Count
from django.utils import timezone

from inventario.models import (
    CicloInventario,
    CicloInventarioItem,
    CicloInventarioSku,
    Inventario,
    InventarioItem,
)
from inventario.models_operacional import InventarioLock, InventarioTarefa


@dataclass
class ProdutividadeOperador:
    operador_id: int
    operador_nome: str
    itens_contados: int
    tempo_medio_segundos: float | None
    tempo_inatividade_segundos: float | None


@dataclass
class IndicadoresOperacionais:
    tipo: str
    referencia_id: int | None
    total_posicoes: int
    pendentes: int
    em_contagem: int
    contadas: int
    divergentes: int
    finalizadas: int
    locks_ativos: int
    produtividade: list[ProdutividadeOperador] = field(default_factory=list)
    atualizado_em: str = ''


def _tempo_inatividade_operador(operador_id: int) -> float | None:
    ultima_tarefa = (
        InventarioTarefa.objects.filter(operador_id=operador_id)
        .exclude(finalizado_em__isnull=True)
        .order_by('-finalizado_em')
        .values_list('finalizado_em', flat=True)
        .first()
    )
    if ultima_tarefa is None:
        return None
    return (timezone.now() - ultima_tarefa).total_seconds()


def _produtividade_tarefas(tarefas_qs) -> list[ProdutividadeOperador]:
    operadores = (
        tarefas_qs.filter(
            status__in=(
                InventarioTarefa.Status.CONTADA,
                InventarioTarefa.Status.APROVADA,
                InventarioTarefa.Status.FINALIZADA,
            ),
        )
        .values('operador_id', 'operador__perfil_operacional__nome')
        .annotate(itens=Count('id'))
    )

    resultado: list[ProdutividadeOperador] = []
    for row in operadores:
        tarefas_op = tarefas_qs.filter(
            operador_id=row['operador_id'],
            iniciado_em__isnull=False,
            finalizado_em__isnull=False,
        )
        tempos = [
            (t.finalizado_em - t.iniciado_em).total_seconds()
            for t in tarefas_op
            if t.finalizado_em and t.iniciado_em
        ]
        tempo_medio = sum(tempos) / len(tempos) if tempos else None
        resultado.append(ProdutividadeOperador(
            operador_id=row['operador_id'],
            operador_nome=row['operador__perfil_operacional__nome'] or f'#{row["operador_id"]}',
            itens_contados=row['itens'],
            tempo_medio_segundos=tempo_medio,
            tempo_inatividade_segundos=_tempo_inatividade_operador(row['operador_id']),
        ))
    return resultado


def obter_indicadores_geral(inventario_id: int | None = None) -> IndicadoresOperacionais:
    inventario = None
    if inventario_id:
        inventario = Inventario.objects.filter(pk=inventario_id).first()

    tarefas_qs = InventarioTarefa.objects.filter(
        tipo_inventario=InventarioTarefa.TipoInventario.GERAL,
    )
    if inventario:
        tarefas_qs = tarefas_qs.filter(inventario=inventario)

    if tarefas_qs.exists():
        total = tarefas_qs.count()
        pendentes = tarefas_qs.filter(status=InventarioTarefa.Status.PENDENTE).count()
        em_contagem = tarefas_qs.filter(status=InventarioTarefa.Status.EM_CONTAGEM).count()
        contadas = tarefas_qs.filter(status=InventarioTarefa.Status.CONTADA).count()
        divergentes = tarefas_qs.filter(status=InventarioTarefa.Status.DIVERGENTE).count()
        finalizadas = tarefas_qs.filter(
            status__in=(
                InventarioTarefa.Status.APROVADA,
                InventarioTarefa.Status.FINALIZADA,
            ),
        ).count()
        produtividade = _produtividade_tarefas(tarefas_qs)
    else:
        itens = InventarioItem.objects.all()
        if inventario:
            itens = itens.filter(inventario=inventario)
        total = itens.values('posicao_id').distinct().count()
        contadas = itens.filter(quantidade_fisica__gt=0).values('posicao_id').distinct().count()
        pendentes = max(total - contadas, 0)
        em_contagem = InventarioLock.objects.filter(
            ativo=True,
            tipo_inventario=InventarioLock.TipoInventario.GERAL,
            inventario=inventario,
        ).count() if inventario else InventarioLock.objects.filter(
            ativo=True, tipo_inventario=InventarioLock.TipoInventario.GERAL,
        ).count()
        divergentes = 0
        finalizadas = contadas if inventario and inventario.status == Inventario.Status.FINALIZADO else 0
        produtividade = []

    locks = InventarioLock.objects.filter(
        ativo=True,
        tipo_inventario=InventarioLock.TipoInventario.GERAL,
    )
    if inventario:
        locks = locks.filter(inventario=inventario)

    return IndicadoresOperacionais(
        tipo='GERAL',
        referencia_id=inventario.pk if inventario else None,
        total_posicoes=total,
        pendentes=pendentes,
        em_contagem=em_contagem,
        contadas=contadas,
        divergentes=divergentes,
        finalizadas=finalizadas,
        locks_ativos=locks.count(),
        produtividade=produtividade,
        atualizado_em=timezone.localtime(timezone.now()).isoformat(),
    )


def obter_indicadores_ciclico(ciclo_id: int | None = None) -> IndicadoresOperacionais:
    ciclo = CicloInventario.objects.filter(
        status_ciclo=CicloInventario.StatusCiclo.ATIVO,
    ).order_by('-pk').first()
    if ciclo_id:
        ciclo = CicloInventario.objects.filter(pk=ciclo_id).first() or ciclo

    if ciclo is None:
        return IndicadoresOperacionais(
            tipo='CICLICO',
            referencia_id=None,
            total_posicoes=0,
            pendentes=0,
            em_contagem=0,
            contadas=0,
            divergentes=0,
            finalizadas=0,
            locks_ativos=0,
            atualizado_em=timezone.localtime(timezone.now()).isoformat(),
        )

    itens = CicloInventarioItem.objects.filter(ciclo=ciclo)
    total = itens.count()
    pendentes = itens.filter(status_contagem=CicloInventarioItem.StatusContagem.PENDENTE).count()
    em_contagem = itens.filter(
        status_contagem__in=(
            CicloInventarioItem.StatusContagem.EM_CONTAGEM,
            CicloInventarioItem.StatusContagem.RECONTAGEM,
        ),
    ).count()
    contadas = itens.filter(
        status_contagem__in=(
            CicloInventarioItem.StatusContagem.CONTADO,
            CicloInventarioItem.StatusContagem.VALIDADO,
            CicloInventarioItem.StatusContagem.APROVADA,
        ),
    ).count()
    divergentes = itens.filter(
        status_contagem=CicloInventarioItem.StatusContagem.DIVERGENTE,
    ).count()
    finalizadas = itens.filter(
        status_contagem__in=(
            CicloInventarioItem.StatusContagem.FINALIZADA,
            CicloInventarioItem.StatusContagem.APROVADA,
            CicloInventarioItem.StatusContagem.VALIDADO,
        ),
    ).count()

    tarefas_qs = InventarioTarefa.objects.filter(
        tipo_inventario=InventarioTarefa.TipoInventario.CICLICO,
        ciclo=ciclo,
    )
    produtividade = _produtividade_tarefas(tarefas_qs) if tarefas_qs.exists() else []

    locks = InventarioLock.objects.filter(
        ativo=True,
        tipo_inventario=InventarioLock.TipoInventario.CICLICO,
        ciclo=ciclo,
    ).count()

    return IndicadoresOperacionais(
        tipo='CICLICO',
        referencia_id=ciclo.pk,
        total_posicoes=total,
        pendentes=pendentes,
        em_contagem=em_contagem,
        contadas=contadas,
        divergentes=divergentes,
        finalizadas=finalizadas,
        locks_ativos=locks,
        produtividade=produtividade,
        atualizado_em=timezone.localtime(timezone.now()).isoformat(),
    )


def serializar_indicadores(ind: IndicadoresOperacionais) -> dict:
    return {
        'tipo': ind.tipo,
        'referencia_id': ind.referencia_id,
        'total_posicoes': ind.total_posicoes,
        'pendentes': ind.pendentes,
        'em_contagem': ind.em_contagem,
        'contadas': ind.contadas,
        'divergentes': ind.divergentes,
        'finalizadas': ind.finalizadas,
        'locks_ativos': ind.locks_ativos,
        'atualizado_em': ind.atualizado_em,
        'produtividade': [
            {
                'operador_id': p.operador_id,
                'operador_nome': p.operador_nome,
                'itens_contados': p.itens_contados,
                'tempo_medio_segundos': (
                    round(p.tempo_medio_segundos, 1)
                    if p.tempo_medio_segundos is not None else None
                ),
                'tempo_inatividade_segundos': (
                    round(p.tempo_inatividade_segundos, 1)
                    if p.tempo_inatividade_segundos is not None else None
                ),
            }
            for p in ind.produtividade
        ],
    }
