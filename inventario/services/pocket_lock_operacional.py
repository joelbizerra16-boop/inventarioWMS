"""Locks de posição para fluxo operacional Pocket (multiusuário)."""

from inventario.models import CicloInventarioSku
from inventario.models_operacional import InventarioLock
from inventario.services.locks import (
    LockError,
    adquirir_lock,
    liberar_lock_posicao_sessao,
    obter_dispositivo,
    obter_session_key,
)
from inventario.services.pocket import buscar_posicao_por_codigo
from inventario.services.tarefas import (
    TarefaError,
    iniciar_tarefa,
    item_atribuido_operador_ciclico,
    obter_tarefa_ciclica,
    obter_tarefa_geral,
    posicao_atribuida_operador_geral,
)
from core.logging_auditoria import ip_do_request


class PocketLockOperacionalError(Exception):
    pass


def adquirir_lock_posicao_pocket_geral(request, inventario, codigo_posicao: str):
    posicao = buscar_posicao_por_codigo(codigo_posicao)
    if posicao is None:
        raise PocketLockOperacionalError('Posição inválida')

    if not posicao_atribuida_operador_geral(inventario, posicao, request.user):
        raise TarefaError('Posição não atribuída a você. Contate o supervisor.')

    tarefa = obter_tarefa_geral(inventario, posicao, request.user)
    if tarefa:
        iniciar_tarefa(tarefa)

    return adquirir_lock(
        tipo_inventario=InventarioLock.TipoInventario.GERAL,
        inventario=inventario,
        posicao=posicao,
        usuario=request.user,
        tarefa=tarefa,
        dispositivo=obter_dispositivo(request),
        session_key=obter_session_key(request.session),
        ip=ip_do_request(request),
    )


def liberar_lock_posicao_pocket_geral(request, inventario, codigo_posicao: str) -> bool:
    posicao = buscar_posicao_por_codigo(codigo_posicao)
    if posicao is None:
        return False
    return liberar_lock_posicao_sessao(
        tipo_inventario=InventarioLock.TipoInventario.GERAL,
        inventario=inventario,
        posicao=posicao,
        usuario=request.user,
        session_key=obter_session_key(request.session),
        ip=ip_do_request(request),
    )


def adquirir_lock_posicao_pocket_ciclico(request, sku_id: int, codigo_posicao: str):
    posicao = buscar_posicao_por_codigo(codigo_posicao)
    if posicao is None:
        raise PocketLockOperacionalError('Posição inválida')

    sku = CicloInventarioSku.objects.select_related('ciclo').filter(pk=sku_id).first()
    if sku is None:
        raise PocketLockOperacionalError('SKU inválido')

    item = sku.posicoes.filter(posicao=posicao).first()
    if item and not item_atribuido_operador_ciclico(item, request.user):
        raise TarefaError('Posição não atribuída a você. Contate o supervisor.')

    tarefa = obter_tarefa_ciclica(item, request.user) if item else None
    if tarefa:
        iniciar_tarefa(tarefa)

    return adquirir_lock(
        tipo_inventario=InventarioLock.TipoInventario.CICLICO,
        ciclo=sku.ciclo,
        ciclo_item=item,
        posicao=posicao,
        usuario=request.user,
        tarefa=tarefa,
        dispositivo=obter_dispositivo(request),
        session_key=obter_session_key(request.session),
        ip=ip_do_request(request),
    )


def liberar_lock_posicao_pocket_ciclico(request, ciclo, codigo_posicao: str) -> bool:
    posicao = buscar_posicao_por_codigo(codigo_posicao)
    if posicao is None:
        return False
    return liberar_lock_posicao_sessao(
        tipo_inventario=InventarioLock.TipoInventario.CICLICO,
        ciclo=ciclo,
        posicao=posicao,
        usuario=request.user,
        session_key=obter_session_key(request.session),
        ip=ip_do_request(request),
    )
