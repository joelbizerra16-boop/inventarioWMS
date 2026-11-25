"""Liberação de contagens físicas ao auditor, contagem por contagem.

Cada contagem (C1, C2, C3) de uma conferência é liberada explicitamente e de
forma independente (`LiberacaoContagemAuditor`); por padrão nenhuma é. Cada
liberação tem o próprio modo:
- Tempo real: registros existentes, novos e correções aparecem conforme
  gravados no sistema.
- Liberação programada: os registros existentes no momento da liberação são
  congelados e disponibilizados em lotes pequenos ao longo da duração
  escolhida (ex.: 60 min). Registros gravados depois do início da liberação
  são reais e novos, então aparecem conforme gravados.

Regras de integridade:
- Nada é copiado nem alterado nos lançamentos originais (`InventarioItem`):
  a agenda só referencia os IDs. `publicado_em` registra apenas quando o item
  ficou visível ao auditor; captura e autor continuam os originais.
- O processamento é idempotente: cada lote é reivindicado por um UPDATE
  condicional (PENDENTE → PUBLICADO), então dois processos ou um reinício
  nunca publicam o mesmo item duas vezes; o evento do lote tem restrição
  única e cada lote tem número único dentro da conferência.
- Cada nova liberação de uma contagem abre um novo ciclo; ciclos anteriores
  ficam preservados como evidência.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.db.models import Count, Exists, F, Max, OuterRef, Q
from django.utils import timezone

from accounts.services.perfil import usuario_e_auditor
from inventario.models import ComparativoContagem, ComparativoContagemInventario, InventarioItem
from inventario.models_auditor import (
    AuditorAcesso,
    EventoAuditoria,
    LiberacaoContagemAuditor,
    PublicacaoAuditoria,
    PublicacaoAuditoriaItem,
)

logger = logging.getLogger('inventario.auditoria')

# Intervalo-alvo entre lotes: progressão contínua sem uma tarefa por linha.
INTERVALO_LOTE_SEGUNDOS = 15
DURACAO_PADRAO_MINUTOS = 60
DURACAO_MAXIMA_MINUTOS = 24 * 60

ItemStatus = PublicacaoAuditoriaItem.Status
Tipo = EventoAuditoria.Tipo
Liberacao = LiberacaoContagemAuditor.Status
Modo = LiberacaoContagemAuditor.Modo
Programacao = LiberacaoContagemAuditor.Programacao
Ordem = PublicacaoAuditoria.Ordem
MOTIVO_MINIMO = 5
PROGRAMACAO_ATIVA = (Programacao.AGENDADA, Programacao.EM_LIBERACAO, Programacao.PAUSADA)


class PublicacaoError(Exception):
    pass


def _usuario(usuario):
    return usuario if getattr(usuario, 'is_authenticated', False) else None


def registrar_evento(comparativo, tipo, *, publicacao=None, usuario=None, agora=None, lote=None, **dados):
    try:
        with transaction.atomic():
            return EventoAuditoria.objects.create(
                comparativo=comparativo,
                publicacao=publicacao,
                tipo=tipo,
                usuario=_usuario(usuario),
                data_hora=agora or timezone.now(),
                lote=lote,
                dados=dados,
            )
    except IntegrityError:
        # Evento único já registrado (ex.: lote publicado por outro processo).
        return None


def obter_publicacao(comparativo: ComparativoContagem, usuario=None) -> PublicacaoAuditoria:
    """Contêiner da conferência (auditores e trilha); a liberação é por contagem."""
    publicacao, _ = PublicacaoAuditoria.objects.get_or_create(
        comparativo=comparativo,
        defaults={'criado_por': _usuario(usuario)},
    )
    return publicacao


# ---------------------------------------------------------------------------
# Acesso do auditor (escopo por conferência)
# ---------------------------------------------------------------------------


def _liberacoes_validas():
    """Liberações ativas cujo inventário ainda é o associado à etapa.

    Trocar o inventário de uma etapa no cadastro nunca herda a liberação.
    """
    associacao = ComparativoContagemInventario.objects.filter(
        comparativo_id=OuterRef('comparativo_id'),
        ordem=OuterRef('etapa'),
        inventario_id=OuterRef('inventario_id_liberado'),
    )
    return LiberacaoContagemAuditor.objects.filter(status=Liberacao.LIBERADA).filter(Exists(associacao))


def liberacoes_visiveis(comparativo) -> dict[int, LiberacaoContagemAuditor]:
    """{etapa: liberação} das contagens visíveis ao auditor nesta conferência."""
    return {l.etapa: l for l in _liberacoes_validas().filter(comparativo=comparativo)}


def etapas_liberadas(comparativo) -> dict[int, datetime]:
    """{etapa: liberado_em} das contagens visíveis ao auditor."""
    return {e: l.liberado_em for e, l in liberacoes_visiveis(comparativo).items()}


def conferencias_do_auditor(user):
    """Conferências atribuídas ao auditor com ao menos uma contagem liberada."""
    if not usuario_e_auditor(user):
        return ComparativoContagem.objects.none()
    return ComparativoContagem.objects.filter(
        Exists(AuditorAcesso.objects.filter(comparativo_id=OuterRef('pk'), auditor=user, ativo=True)),
        Exists(_liberacoes_validas().filter(comparativo_id=OuterRef('pk'))),
    )


def obter_conferencia_auditor(user, pk, request_path: str = '') -> ComparativoContagem:
    """Conferência do auditor ou PermissionDenied (registra a tentativa)."""
    comparativo = conferencias_do_auditor(user).select_related('publicacao_auditoria').filter(pk=pk).first()
    if comparativo is None:
        existente = ComparativoContagem.objects.filter(pk=pk).first()
        if existente is not None:
            registrar_evento(
                existente, Tipo.ACESSO_NEGADO, usuario=user,
                path=(request_path or '')[:200],
            )
        logger.warning('auditor_acesso_negado usuario=%s conferencia=%s', getattr(user, 'pk', None), pk)
        raise PermissionDenied('Conferência não liberada para este auditor.')
    return comparativo


# ---------------------------------------------------------------------------
# Auditores autorizados
# ---------------------------------------------------------------------------


@transaction.atomic
def configurar(comparativo: ComparativoContagem, usuario, *, auditores, mostrar_horario_real: bool | None = None) -> PublicacaoAuditoria:
    publicacao = obter_publicacao(comparativo, usuario)
    if mostrar_horario_real is not None and publicacao.mostrar_horario_real != mostrar_horario_real:
        registrar_evento(
            comparativo, Tipo.CONFIGURADA, publicacao=publicacao, usuario=usuario,
            alteracoes={'mostrar_horario_real': [publicacao.mostrar_horario_real, mostrar_horario_real]},
        )
        publicacao.mostrar_horario_real = mostrar_horario_real
        publicacao.save(update_fields=['mostrar_horario_real', 'atualizado_em'])
    selecionados = {u.pk for u in auditores if usuario_e_auditor(u)}
    for acesso in AuditorAcesso.objects.select_for_update().filter(comparativo=comparativo):
        deve_ativo = acesso.auditor_id in selecionados
        if acesso.ativo != deve_ativo:
            acesso.ativo = deve_ativo
            acesso.save(update_fields=['ativo'])
            registrar_evento(
                comparativo, Tipo.ACESSO_CONCEDIDO if deve_ativo else Tipo.ACESSO_REVOGADO,
                publicacao=publicacao, usuario=usuario, auditor=acesso.auditor_id,
            )
        selecionados.discard(acesso.auditor_id)
    for auditor_id in selecionados:
        AuditorAcesso.objects.create(comparativo=comparativo, auditor_id=auditor_id, concedido_por=_usuario(usuario))
        registrar_evento(comparativo, Tipo.ACESSO_CONCEDIDO, publicacao=publicacao, usuario=usuario, auditor=auditor_id)
    return publicacao


@transaction.atomic
def definir_conferencias_do_auditor(auditor, comparativos, usuario) -> None:
    """Define, pelo cadastro do usuário, quais conferências o auditor acompanha.

    Usa o mesmo acesso da tela do Painel do Auditor (com evento na trilha de
    cada conferência). Usuário que deixa de ser auditor perde os acessos ativos.
    O que ele vê continua dependendo da liberação de cada contagem.
    """
    selecionados = {c.pk for c in comparativos} if usuario_e_auditor(auditor) else set()
    for acesso in AuditorAcesso.objects.select_for_update().filter(auditor=auditor).select_related('comparativo'):
        deve_ativo = acesso.comparativo_id in selecionados
        if acesso.ativo != deve_ativo:
            acesso.ativo = deve_ativo
            acesso.save(update_fields=['ativo'])
            registrar_evento(
                acesso.comparativo, Tipo.ACESSO_CONCEDIDO if deve_ativo else Tipo.ACESSO_REVOGADO,
                publicacao=obter_publicacao(acesso.comparativo, usuario), usuario=usuario,
                auditor=auditor.pk, origem='cadastro de usuário',
            )
        selecionados.discard(acesso.comparativo_id)
    for comparativo in ComparativoContagem.objects.filter(pk__in=selecionados):
        AuditorAcesso.objects.create(comparativo=comparativo, auditor=auditor, concedido_por=_usuario(usuario))
        registrar_evento(
            comparativo, Tipo.ACESSO_CONCEDIDO, publicacao=obter_publicacao(comparativo, usuario), usuario=usuario,
            auditor=auditor.pk, origem='cadastro de usuário',
        )


# ---------------------------------------------------------------------------
# Liberação por contagem
# ---------------------------------------------------------------------------


def _associacao(comparativo, etapa):
    return comparativo.associacoes.filter(ordem=etapa).select_related('inventario').first()


def _lancamentos_da_etapa(inventario_id: int, etapa: int) -> list[dict]:
    """Lançamentos físicos de uma contagem (somente referências)."""
    resultado = []
    for r in InventarioItem.objects.filter(inventario_id=inventario_id).values(
        'id', 'inventario_id', 'posicao__codigo', 'posicao__posicao', 'data_registro', 'data_contagem',
    ):
        resultado.append({
            'id': r['id'],
            'inventario_id': r['inventario_id'],
            'etapa': etapa,
            'posicao': (r['posicao__posicao'] or r['posicao__codigo'] or '', r['posicao__codigo'] or ''),
            'capturado_em': r['data_registro'] or r['data_contagem'],
        })
    return resultado


def _ordenar(registros: list[dict], ordem: str) -> list[dict]:
    minimo = timezone.make_aware(datetime(1970, 1, 1))
    if ordem == Ordem.CRONOLOGICA:
        return sorted(registros, key=lambda r: (r['capturado_em'] or minimo, r['id']))
    por_posicao = sorted(registros, key=lambda r: (r['posicao'], r['etapa'], r['id']))
    if ordem == Ordem.EQUILIBRADA:
        # Uma rodada por posição: o avanço se espalha por todo o armazém.
        grupos: dict = {}
        for r in por_posicao:
            grupos.setdefault(r['posicao'], []).append(r)
        filas = list(grupos.values())
        intercalado = []
        indice = 0
        while any(filas):
            for fila in filas:
                if len(fila) > indice:
                    intercalado.append(fila[indice])
            indice += 1
            filas = [f for f in filas if len(f) > indice]
        return intercalado
    return por_posicao


def _numero_lotes(total: int, duracao_minutos: int) -> int:
    return max(1, min(total, (duracao_minutos * 60) // INTERVALO_LOTE_SEGUNDOS))


def _programar(liberacao, publicacao, inventario_id: int, inicio: datetime) -> int:
    """Congela os registros atuais da contagem e distribui os lotes na duração."""
    registros = _ordenar(_lancamentos_da_etapa(inventario_id, liberacao.etapa), liberacao.ordem)
    total = len(registros)
    lotes = _numero_lotes(total, liberacao.duracao_minutos)
    # Numeração de lote única na conferência (o evento do lote é único por número).
    base = (PublicacaoAuditoriaItem.objects.filter(publicacao=publicacao).aggregate(m=Max('lote'))['m'] or -1) + 1
    duracao = timedelta(minutes=liberacao.duracao_minutos)
    itens = []
    for i, r in enumerate(registros):
        relativo = (i * lotes) // total
        itens.append(PublicacaoAuditoriaItem(
            publicacao=publicacao, liberacao=liberacao, ciclo=liberacao.ciclo,
            item_id=r['id'], item_original_id=r['id'], inventario_id_original=r['inventario_id'],
            etapa=liberacao.etapa, ordem=i, lote=base + relativo,
            previsto_para=inicio + duracao * (relativo + 1) / lotes,
        ))
    PublicacaoAuditoriaItem.objects.bulk_create(itens, batch_size=500)
    liberacao.total_programado = total
    liberacao.total_publicado = 0
    liberacao.termino_previsto = inicio + duracao
    return lotes


@transaction.atomic
def liberar_contagem(
    comparativo: ComparativoContagem,
    etapa: int,
    usuario,
    *,
    modo: str = Modo.TEMPO_REAL,
    duracao_minutos: int = DURACAO_PADRAO_MINUTOS,
    ordem: str = Ordem.POSICAO,
    inicio_previsto: datetime | None = None,
    agora=None,
) -> LiberacaoContagemAuditor:
    """Libera somente a contagem indicada; as demais não são afetadas."""
    agora = agora or timezone.now()
    if etapa not in (1, 2, 3):
        raise PublicacaoError('Contagem inválida.')
    if modo not in Modo.values:
        raise PublicacaoError('Modo de liberação inválido.')
    if ordem not in Ordem.values:
        raise PublicacaoError('Ordem de liberação inválida.')
    if modo == Modo.PROGRAMADA and not (duracao_minutos and 1 <= duracao_minutos <= DURACAO_MAXIMA_MINUTOS):
        raise PublicacaoError('Informe uma duração entre 1 e 1440 minutos.')
    associacao = _associacao(comparativo, etapa)
    if associacao is None:
        raise PublicacaoError(f'A conferência não tem C{etapa} associada.')
    publicacao = obter_publicacao(comparativo, usuario)
    liberacao, _ = LiberacaoContagemAuditor.objects.select_for_update().get_or_create(comparativo=comparativo, etapa=etapa)
    if liberacao.status == Liberacao.LIBERADA and liberacao.inventario_id_liberado == associacao.inventario_id:
        raise PublicacaoError(f'C{etapa} já está liberada.')
    anterior = liberacao.status
    liberacao.status = Liberacao.LIBERADA
    liberacao.inventario_id_liberado = associacao.inventario_id
    liberacao.liberado_em = agora
    liberacao.liberado_por = _usuario(usuario)
    liberacao.modo = modo
    liberacao.ordem = ordem
    liberacao.duracao_minutos = duracao_minutos or DURACAO_PADRAO_MINUTOS
    liberacao.ciclo += 1
    liberacao.pausado_em = None
    liberacao.termino_real = None
    liberacao.inicio_previsto = liberacao.inicio_real = liberacao.termino_previsto = None
    liberacao.total_programado = liberacao.total_publicado = 0
    lotes = 0
    if modo == Modo.PROGRAMADA:
        inicio = inicio_previsto if inicio_previsto and inicio_previsto > agora else agora
        liberacao.inicio_previsto = inicio
        lotes = _programar(liberacao, publicacao, associacao.inventario_id, inicio)
        if liberacao.total_programado == 0:
            liberacao.programacao = Programacao.CONCLUIDA
            liberacao.inicio_real = liberacao.termino_real = agora
        elif inicio > agora:
            liberacao.programacao = Programacao.AGENDADA
        else:
            liberacao.programacao = Programacao.EM_LIBERACAO
            liberacao.inicio_real = agora
    else:
        liberacao.programacao = Programacao.SEM
        liberacao.inicio_real = agora
    liberacao.save()
    registrar_evento(
        comparativo, Tipo.CONTAGEM_LIBERADA, publicacao=publicacao, usuario=usuario, agora=agora,
        etapa=etapa, inventario=associacao.inventario_id, status_anterior=anterior, modo=modo,
        ciclo=liberacao.ciclo, total=liberacao.total_programado, lotes=lotes,
        duracao_minutos=liberacao.duracao_minutos if modo == Modo.PROGRAMADA else None,
        inicio=liberacao.inicio_previsto.isoformat() if liberacao.inicio_previsto else None,
        auditores=list(AuditorAcesso.objects.filter(comparativo=comparativo, ativo=True).values_list('auditor_id', flat=True)),
    )
    if liberacao.programacao == Programacao.EM_LIBERACAO:
        processar(agora=agora, liberacao_id=liberacao.pk)
        liberacao.refresh_from_db()
    return liberacao


def _obter_liberacao(comparativo, etapa) -> LiberacaoContagemAuditor:
    liberacao = LiberacaoContagemAuditor.objects.select_for_update().filter(comparativo=comparativo, etapa=etapa).first()
    if liberacao is None or liberacao.status != Liberacao.LIBERADA:
        raise PublicacaoError(f'C{etapa} não está liberada.')
    return liberacao


def _pendentes(liberacao):
    return PublicacaoAuditoriaItem.objects.filter(liberacao=liberacao, ciclo=liberacao.ciclo, status=ItemStatus.PENDENTE)


@transaction.atomic
def suspender_contagem(comparativo: ComparativoContagem, etapa: int, usuario, motivo: str, agora=None) -> LiberacaoContagemAuditor:
    """Bloqueia novas consultas da contagem; dados e histórico ficam intactos."""
    agora = agora or timezone.now()
    motivo = (motivo or '').strip()
    if len(motivo) < MOTIVO_MINIMO:
        raise PublicacaoError('Informe o motivo da suspensão (mínimo de 5 caracteres).')
    liberacao = _obter_liberacao(comparativo, etapa)
    canceladas = _pendentes(liberacao).update(status=ItemStatus.CANCELADO)
    if liberacao.programacao in PROGRAMACAO_ATIVA:
        liberacao.programacao = Programacao.INTERROMPIDA
        liberacao.termino_real = agora
    liberacao.status = Liberacao.SUSPENSA
    liberacao.suspenso_em = agora
    liberacao.suspenso_por = _usuario(usuario)
    liberacao.motivo_suspensao = motivo[:255]
    liberacao.save()
    registrar_evento(
        comparativo, Tipo.CONTAGEM_SUSPENSA, publicacao=obter_publicacao(comparativo, usuario), usuario=usuario,
        agora=agora, etapa=etapa, inventario=liberacao.inventario_id_liberado, motivo=liberacao.motivo_suspensao,
        ciclo=liberacao.ciclo, pendentes_cancelados=canceladas,
    )
    return liberacao


def _exigir_programada(liberacao, estados, mensagem):
    if liberacao.modo != Modo.PROGRAMADA or liberacao.programacao not in estados:
        raise PublicacaoError(mensagem)


@transaction.atomic
def pausar_contagem(comparativo, etapa, usuario, agora=None) -> LiberacaoContagemAuditor:
    agora = agora or timezone.now()
    liberacao = _obter_liberacao(comparativo, etapa)
    _exigir_programada(liberacao, (Programacao.AGENDADA, Programacao.EM_LIBERACAO), f'C{etapa} não tem liberação programada em andamento.')
    liberacao.programacao = Programacao.PAUSADA
    liberacao.pausado_em = agora
    liberacao.save()
    registrar_evento(comparativo, Tipo.PAUSADA, publicacao=obter_publicacao(comparativo), usuario=usuario, agora=agora, etapa=etapa, ciclo=liberacao.ciclo)
    return liberacao


@transaction.atomic
def retomar_contagem(comparativo, etapa, usuario, agora=None) -> LiberacaoContagemAuditor:
    """Continua de onde parou: desloca a agenda pendente pelo tempo pausado."""
    agora = agora or timezone.now()
    liberacao = _obter_liberacao(comparativo, etapa)
    _exigir_programada(liberacao, (Programacao.PAUSADA,), f'C{etapa} não está pausada.')
    deslocamento = agora - (liberacao.pausado_em or agora)
    _pendentes(liberacao).filter(previsto_para__isnull=False).update(previsto_para=F('previsto_para') + deslocamento)
    if liberacao.termino_previsto:
        liberacao.termino_previsto += deslocamento
    if liberacao.inicio_real is None and liberacao.inicio_previsto:
        liberacao.inicio_previsto += deslocamento
    liberacao.programacao = Programacao.EM_LIBERACAO if liberacao.inicio_real else Programacao.AGENDADA
    liberacao.pausado_em = None
    liberacao.save()
    registrar_evento(
        comparativo, Tipo.RETOMADA, publicacao=obter_publicacao(comparativo), usuario=usuario, agora=agora,
        etapa=etapa, ciclo=liberacao.ciclo, pausa_segundos=int(deslocamento.total_seconds()),
    )
    processar(agora=agora, liberacao_id=liberacao.pk)
    liberacao.refresh_from_db()
    return liberacao


@transaction.atomic
def concluir_contagem(comparativo, etapa, usuario, agora=None) -> LiberacaoContagemAuditor:
    """Disponibiliza agora todos os registros restantes da programação."""
    agora = agora or timezone.now()
    liberacao = _obter_liberacao(comparativo, etapa)
    _exigir_programada(liberacao, PROGRAMACAO_ATIVA, f'C{etapa} não tem liberação programada em andamento.')
    _pendentes(liberacao).update(previsto_para=agora)
    if liberacao.inicio_real is None:
        liberacao.inicio_real = agora
    liberacao.programacao = Programacao.EM_LIBERACAO
    liberacao.pausado_em = None
    liberacao.save()
    _publicar_vencidos(liberacao, agora)
    liberacao.programacao = Programacao.CONCLUIDA
    liberacao.termino_real = agora
    liberacao.save()
    registrar_evento(comparativo, Tipo.CONCLUIDA, publicacao=obter_publicacao(comparativo), usuario=usuario, agora=agora, etapa=etapa, ciclo=liberacao.ciclo, manual=True)
    return liberacao


@transaction.atomic
def interromper_contagem(comparativo, etapa, usuario, agora=None) -> LiberacaoContagemAuditor:
    """Cancela os lotes restantes; o que já foi disponibilizado continua visível."""
    agora = agora or timezone.now()
    liberacao = _obter_liberacao(comparativo, etapa)
    _exigir_programada(liberacao, PROGRAMACAO_ATIVA, f'C{etapa} não tem liberação programada em andamento.')
    canceladas = _pendentes(liberacao).update(status=ItemStatus.CANCELADO)
    if liberacao.inicio_real is None:
        liberacao.inicio_real = agora
    liberacao.programacao = Programacao.INTERROMPIDA
    liberacao.termino_real = agora
    liberacao.pausado_em = None
    liberacao.save()
    registrar_evento(comparativo, Tipo.CANCELADA, publicacao=obter_publicacao(comparativo), usuario=usuario, agora=agora, etapa=etapa, ciclo=liberacao.ciclo, canceladas=canceladas)
    return liberacao


def estado_contagens(comparativo: ComparativoContagem) -> list[dict]:
    """Situação de C1/C2/C3 para o gestor (inclui dados que o auditor não vê)."""
    from inventario.services.auditor_painel import contar_visiveis_por_etapa

    processar(comparativo_id=comparativo.pk)
    liberacoes = {
        l.etapa: l for l in LiberacaoContagemAuditor.objects.filter(comparativo=comparativo)
        .select_related('liberado_por__perfil_operacional', 'suspenso_por__perfil_operacional')
    }
    visiveis = contar_visiveis_por_etapa(comparativo, liberacoes_visiveis(comparativo))
    validas = set(liberacoes_visiveis(comparativo))
    resultado = []
    for etapa in (1, 2, 3):
        associacao = _associacao(comparativo, etapa)
        liberacao = liberacoes.get(etapa)
        status = liberacao.status if liberacao else Liberacao.NAO_LIBERADA
        inventario_trocado = bool(
            liberacao and status == Liberacao.LIBERADA
            and (associacao is None or liberacao.inventario_id_liberado != associacao.inventario_id)
        )
        if inventario_trocado:
            status = Liberacao.NAO_LIBERADA
        info = {
            'etapa': etapa, 'rotulo': f'C{etapa}', 'associacao': associacao, 'liberacao': liberacao,
            'status': status, 'status_rotulo': Liberacao(status).label, 'inventario_trocado': inventario_trocado,
            'liberada': etapa in validas, 'visiveis': visiveis.get(etapa, 0),
            'registros': 0, 'ultima_atualizacao': None, 'em_execucao': False,
            'programacao_ativa': bool(liberacao and etapa in validas and liberacao.modo == Modo.PROGRAMADA
                                      and liberacao.programacao in PROGRAMACAO_ATIVA),
        }
        if associacao is not None:
            inv = associacao.inventario
            agregado = InventarioItem.objects.filter(inventario=inv).aggregate(
                n=Count('id'), registro=Max('data_registro'), alteracao=Max('data_alteracao'),
            )
            info['registros'] = agregado['n']
            info['ultima_atualizacao'] = max(filter(None, [agregado['registro'], agregado['alteracao']]), default=None)
            info['em_execucao'] = inv.status != inv.Status.FINALIZADO
            info['inventario_status'] = inv.get_status_display()
        resultado.append(info)
    return resultado


# ---------------------------------------------------------------------------
# Processamento (background / leitura do painel)
# ---------------------------------------------------------------------------


def _publicar_vencidos(liberacao: LiberacaoContagemAuditor, agora: datetime) -> int:
    itens = PublicacaoAuditoriaItem.objects.filter(liberacao=liberacao, ciclo=liberacao.ciclo)
    lotes = list(
        itens.filter(status=ItemStatus.PENDENTE, previsto_para__lte=agora)
        .values('lote').annotate(n=Count('id')).order_by('lote').values_list('lote', flat=True)
    )
    publicados = 0
    publicacao = None
    for lote in lotes:
        # Reivindicação atômica: só um processo vence o UPDATE condicional.
        n = itens.filter(status=ItemStatus.PENDENTE, lote=lote, previsto_para__lte=agora).update(
            status=ItemStatus.PUBLICADO, publicado_em=agora, tentativas=F('tentativas') + 1,
        )
        if n:
            publicados += n
            publicacao = publicacao or obter_publicacao(liberacao.comparativo)
            registrar_evento(
                liberacao.comparativo, Tipo.LOTE_PUBLICADO, publicacao=publicacao, agora=agora, lote=lote,
                itens=n, etapa=liberacao.etapa, ciclo=liberacao.ciclo,
            )
    if publicados:
        total = itens.filter(status=ItemStatus.PUBLICADO).count()
        LiberacaoContagemAuditor.objects.filter(pk=liberacao.pk).update(total_publicado=total)
        liberacao.total_publicado = total
    return publicados


def processar(agora=None, liberacao_id=None, comparativo_id=None) -> int:
    """Libera os lotes vencidos das contagens programadas (idempotente).

    Seguro para reinício e para execução simultânea; após indisponibilidade,
    libera apenas o que venceu e ainda não foi processado.
    """
    agora = agora or timezone.now()
    filtro = Q(status=Liberacao.LIBERADA, modo=Modo.PROGRAMADA, programacao__in=[Programacao.AGENDADA, Programacao.EM_LIBERACAO])
    if liberacao_id is not None:
        filtro &= Q(pk=liberacao_id)
    if comparativo_id is not None:
        filtro &= Q(comparativo_id=comparativo_id)
    total = 0
    for liberacao in LiberacaoContagemAuditor.objects.filter(filtro).select_related('comparativo'):
        with transaction.atomic():
            if liberacao.programacao == Programacao.AGENDADA:
                if not liberacao.inicio_previsto or liberacao.inicio_previsto > agora:
                    continue
                virou = LiberacaoContagemAuditor.objects.filter(pk=liberacao.pk, programacao=Programacao.AGENDADA).update(
                    programacao=Programacao.EM_LIBERACAO, inicio_real=agora,
                )
                if not virou:
                    continue
                liberacao.programacao = Programacao.EM_LIBERACAO
                liberacao.inicio_real = agora
                registrar_evento(
                    liberacao.comparativo, Tipo.INICIADA, publicacao=obter_publicacao(liberacao.comparativo),
                    agora=agora, etapa=liberacao.etapa, ciclo=liberacao.ciclo, automatica=True,
                )
            total += _publicar_vencidos(liberacao, agora)
            if not _pendentes(liberacao).exists():
                concluiu = LiberacaoContagemAuditor.objects.filter(
                    pk=liberacao.pk, programacao=Programacao.EM_LIBERACAO,
                ).update(programacao=Programacao.CONCLUIDA, termino_real=agora)
                if concluiu:
                    registrar_evento(
                        liberacao.comparativo, Tipo.CONCLUIDA, publicacao=obter_publicacao(liberacao.comparativo),
                        agora=agora, etapa=liberacao.etapa, ciclo=liberacao.ciclo, automatica=True,
                    )
    return total


def exigir_gestor(user):
    from accounts.services.perfil import usuario_pode_escrever_inventario

    if not usuario_pode_escrever_inventario(user):
        raise PermissionDenied('Somente administradores e equipe de inventário configuram a publicação.')
