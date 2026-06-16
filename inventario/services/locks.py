"""Controle de locks transacionais para inventário multiusuário."""

import logging
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.contrib.sessions.models import Session
from django.db import transaction
from django.utils import timezone

from inventario.models_operacional import InventarioAuditoriaEvento, InventarioLock, InventarioTarefa
from inventario.services.auditoria_operacional import registrar_evento_operacional

logger = logging.getLogger(__name__)


class LockError(Exception):
    def __init__(self, mensagem: str, lock: InventarioLock | None = None):
        super().__init__(mensagem)
        self.lock = lock


@dataclass
class LockInfo:
    lock: InventarioLock
    renovado: bool = False


def obter_timeout_segundos() -> int:
    return int(getattr(settings, 'INVENTARIO_LOCK_TIMEOUT_SECONDS', 900))


def obter_dispositivo(request) -> str:
    if request is None:
        return ''
    return (request.META.get('HTTP_USER_AGENT') or '')[:200]


def obter_session_key(session) -> str:
    if session is None:
        return ''
    if not session.session_key:
        session.save()
    return (session.session_key or '')[:64]


def _nome_usuario(usuario) -> str:
    if usuario is None:
        return 'Operador desconhecido'
    perfil = getattr(usuario, 'perfil_operacional', None)
    if perfil and perfil.nome:
        return perfil.nome
    return usuario.get_full_name() or usuario.get_username() or str(usuario.pk)


def _mensagem_lock_ocupado(lock: InventarioLock, *, resumida: bool = False) -> str:
    if resumida:
        return 'Posição em contagem por outro operador.'
    desde = timezone.localtime(lock.adquirido_em).strftime('%d/%m/%Y %H:%M')
    return (
        f'Posição em contagem por {_nome_usuario(lock.usuario)} '
        f'desde {desde}. Aguarde a liberação ou contate o supervisor.'
    )


def _filtro_escopo_lock(
    qs,
    *,
    tipo_inventario: str,
    inventario=None,
    ciclo=None,
):
    if tipo_inventario == InventarioLock.TipoInventario.GERAL:
        return qs.filter(inventario=inventario)
    return qs.filter(ciclo=ciclo)


def _buscar_locks_posicao_ativos(
    *,
    tipo_inventario: str,
    inventario=None,
    ciclo=None,
    posicao,
):
    qs = InventarioLock.objects.select_for_update().filter(
        ativo=True,
        tipo_inventario=tipo_inventario,
        posicao=posicao,
    )
    return list(_filtro_escopo_lock(
        qs,
        tipo_inventario=tipo_inventario,
        inventario=inventario,
        ciclo=ciclo,
    ))


def _lock_mesmo_operador(lock: InventarioLock, usuario) -> bool:
    return lock.usuario_id == usuario.pk


def _lock_mesma_sessao(lock: InventarioLock, usuario, session_key: str) -> bool:
    return _lock_mesmo_operador(lock, usuario) and lock.session_key == session_key


def _calcular_expiracao(agora=None) -> timezone.datetime:
    agora = agora or timezone.now()
    return agora + timedelta(seconds=obter_timeout_segundos())


def _sessao_lock_ativa(lock: InventarioLock, agora=None) -> bool:
    agora = agora or timezone.now()
    chave = (lock.session_key or '').strip()
    if not chave:
        return True
    if len(chave) != 32:
        return True
    return Session.objects.filter(session_key=chave, expire_date__gt=agora).exists()


@transaction.atomic
def expirar_locks_abandonados(*, ip: str | None = None) -> int:
    agora = timezone.now()
    locks_expirados = list(
        InventarioLock.objects.select_for_update(skip_locked=True).filter(
            ativo=True,
            expira_em__lte=agora,
        )
    )
    locks_sessao_orfa = [
        lock for lock in InventarioLock.objects.select_for_update(skip_locked=True).filter(
            ativo=True,
            expira_em__gt=agora,
        )
        if not _sessao_lock_ativa(lock, agora)
    ]
    locks = locks_expirados + locks_sessao_orfa
    for lock in locks:
        lock.ativo = False
        lock.liberado_em = agora
        lock.motivo_liberacao = (
            InventarioLock.MotivoLiberacao.TIMEOUT
            if lock.expira_em <= agora
            else InventarioLock.MotivoLiberacao.SESSAO
        )
        lock.save(update_fields=['ativo', 'liberado_em', 'motivo_liberacao'])

        if lock.tarefa_id:
            InventarioTarefa.objects.filter(
                pk=lock.tarefa_id,
                status=InventarioTarefa.Status.EM_CONTAGEM,
            ).update(status=InventarioTarefa.Status.PENDENTE)

        registrar_evento_operacional(
            evento=(
                InventarioAuditoriaEvento.Evento.LOCK_TIMEOUT
                if lock.motivo_liberacao == InventarioLock.MotivoLiberacao.TIMEOUT
                else InventarioAuditoriaEvento.Evento.LOCK_LIBERADO
            ),
            tipo_inventario=lock.tipo_inventario,
            inventario=lock.inventario,
            ciclo=lock.ciclo,
            tarefa=lock.tarefa,
            lock=lock,
            usuario=lock.usuario,
            dispositivo=lock.dispositivo,
            ip=ip,
            posicao=lock.posicao,
            produto=lock.produto,
            dados_extras={
                'motivo': lock.motivo_liberacao,
                'adquirido_em': lock.adquirido_em.isoformat(),
            },
        )
    return len(locks)


def _buscar_lock_ativo(
    *,
    tipo_inventario: str,
    inventario=None,
    ciclo=None,
    ciclo_item=None,
    posicao=None,
    produto=None,
) -> InventarioLock | None:
    locks = _buscar_locks_posicao_ativos(
        tipo_inventario=tipo_inventario,
        inventario=inventario,
        ciclo=ciclo,
        posicao=posicao,
    )
    return locks[0] if locks else None


@transaction.atomic
def adquirir_lock(
    *,
    tipo_inventario: str,
    usuario,
    posicao,
    inventario=None,
    ciclo=None,
    ciclo_item=None,
    produto=None,
    tarefa=None,
    dispositivo: str = '',
    session_key: str = '',
    ip: str | None = None,
) -> LockInfo:
    expirar_locks_abandonados(ip=ip)
    agora = timezone.now()
    expira = _calcular_expiracao(agora)

    existente = _buscar_lock_ativo(
        tipo_inventario=tipo_inventario,
        inventario=inventario,
        ciclo=ciclo,
        ciclo_item=ciclo_item,
        posicao=posicao,
        produto=produto,
    )

    if existente:
        if not _sessao_lock_ativa(existente, agora):
            logger.warning(
                'LOCK_ORFAO_SESSAO tipo=%s posicao=%s lock_usuario_id=%s lock_session_key=%s solicitante_id=%s',
                existente.tipo_inventario,
                existente.posicao_id,
                existente.usuario_id,
                existente.session_key,
                getattr(usuario, 'pk', None),
            )
            liberar_lock(existente, motivo=InventarioLock.MotivoLiberacao.SESSAO, ip=ip)
            existente = None
    if existente:
        if _lock_mesmo_operador(existente, usuario):
            existente.renovado_em = agora
            existente.expira_em = expira
            existente.dispositivo = dispositivo[:200]
            campos_renovacao = ['renovado_em', 'expira_em', 'dispositivo', 'tarefa', 'ciclo_item']
            if session_key and existente.session_key != session_key[:64]:
                existente.session_key = session_key[:64]
                campos_renovacao.append('session_key')
            if tarefa and existente.tarefa_id != tarefa.pk:
                existente.tarefa = tarefa
            if ciclo_item and existente.ciclo_item_id != ciclo_item.pk:
                existente.ciclo_item = ciclo_item
            existente.save(update_fields=campos_renovacao)
            registrar_evento_operacional(
                evento=InventarioAuditoriaEvento.Evento.LOCK_RENOVADO,
                tipo_inventario=tipo_inventario,
                inventario=inventario,
                ciclo=ciclo,
                tarefa=tarefa or existente.tarefa,
                lock=existente,
                usuario=usuario,
                dispositivo=dispositivo,
                ip=ip,
                posicao=posicao,
                produto=produto,
            )
            return LockInfo(lock=existente, renovado=True)

        if existente.expira_em <= agora:
            liberar_lock(existente, motivo=InventarioLock.MotivoLiberacao.TIMEOUT, ip=ip)
        else:
            logger.info(
                'LOCK_VALIDACAO_POSICAO tipo=%s posicao=%s operador_logado_id=%s operador_lock_id=%s status=LOCK_ATIVO',
                tipo_inventario,
                posicao.pk,
                getattr(usuario, 'pk', None),
                existente.usuario_id,
            )
            raise LockError(
                _mensagem_lock_ocupado(existente, resumida=True),
                lock=existente,
            )

    lock = InventarioLock.objects.create(
        tipo_inventario=tipo_inventario,
        inventario=inventario,
        ciclo=ciclo,
        ciclo_item=ciclo_item,
        tarefa=tarefa,
        posicao=posicao,
        produto=None,
        usuario=usuario,
        dispositivo=dispositivo[:200],
        session_key=session_key[:64],
        adquirido_em=agora,
        renovado_em=agora,
        expira_em=expira,
        ativo=True,
    )

    registrar_evento_operacional(
        evento=InventarioAuditoriaEvento.Evento.LOCK_ADQUIRIDO,
        tipo_inventario=tipo_inventario,
        inventario=inventario,
        ciclo=ciclo,
        tarefa=tarefa,
        lock=lock,
        usuario=usuario,
        dispositivo=dispositivo,
        ip=ip,
        posicao=posicao,
        produto=produto,
    )
    return LockInfo(lock=lock, renovado=False)


@transaction.atomic
def liberar_lock(
    lock: InventarioLock,
    *,
    motivo: str = InventarioLock.MotivoLiberacao.CONCLUIDO,
    ip: str | None = None,
    usuario=None,
) -> None:
    if not lock.ativo:
        return
    agora = timezone.now()
    lock.ativo = False
    lock.liberado_em = agora
    lock.motivo_liberacao = motivo
    lock.save(update_fields=['ativo', 'liberado_em', 'motivo_liberacao'])

    evento = (
        InventarioAuditoriaEvento.Evento.LOCK_TIMEOUT
        if motivo == InventarioLock.MotivoLiberacao.TIMEOUT
        else InventarioAuditoriaEvento.Evento.LOCK_LIBERADO
    )
    registrar_evento_operacional(
        evento=evento,
        tipo_inventario=lock.tipo_inventario,
        inventario=lock.inventario,
        ciclo=lock.ciclo,
        tarefa=lock.tarefa,
        lock=lock,
        usuario=usuario or lock.usuario,
        dispositivo=lock.dispositivo,
        ip=ip,
        posicao=lock.posicao,
        produto=lock.produto,
        dados_extras={'motivo': motivo},
    )


@transaction.atomic
def liberar_locks_usuario(
    usuario,
    *,
    session_key: str = '',
    motivo: str = InventarioLock.MotivoLiberacao.SESSAO,
    ip: str | None = None,
) -> int:
    qs = InventarioLock.objects.select_for_update().filter(ativo=True, usuario=usuario)
    if session_key:
        qs = qs.filter(session_key=session_key)
    locks = list(qs)
    for lock in locks:
        liberar_lock(lock, motivo=motivo, ip=ip, usuario=usuario)
        if lock.tarefa_id:
            InventarioTarefa.objects.filter(
                pk=lock.tarefa_id,
                status=InventarioTarefa.Status.EM_CONTAGEM,
            ).update(status=InventarioTarefa.Status.PENDENTE)
    return len(locks)


def renovar_lock_ativo(
    lock: InventarioLock,
    *,
    dispositivo: str = '',
    ip: str | None = None,
) -> InventarioLock:
    agora = timezone.now()
    lock.renovado_em = agora
    lock.expira_em = _calcular_expiracao(agora)
    if dispositivo:
        lock.dispositivo = dispositivo[:200]
    lock.save(update_fields=['renovado_em', 'expira_em', 'dispositivo'])
    registrar_evento_operacional(
        evento=InventarioAuditoriaEvento.Evento.LOCK_RENOVADO,
        tipo_inventario=lock.tipo_inventario,
        inventario=lock.inventario,
        ciclo=lock.ciclo,
        tarefa=lock.tarefa,
        lock=lock,
        usuario=lock.usuario,
        dispositivo=lock.dispositivo,
        ip=ip,
        posicao=lock.posicao,
        produto=lock.produto,
    )
    return lock


@transaction.atomic
def liberar_lock_posicao_sessao(
    *,
    tipo_inventario: str,
    usuario,
    posicao,
    inventario=None,
    ciclo=None,
    session_key: str = '',
    ip: str | None = None,
) -> bool:
    lock = _buscar_lock_ativo(
        tipo_inventario=tipo_inventario,
        inventario=inventario,
        ciclo=ciclo,
        posicao=posicao,
    )
    if lock and _lock_mesmo_operador(lock, usuario):
        liberar_lock(
            lock,
            motivo=InventarioLock.MotivoLiberacao.SESSAO,
            ip=ip,
            usuario=usuario,
        )
        return True
    return False
