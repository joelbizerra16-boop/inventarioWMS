from django.db import IntegrityError, transaction
from django.utils import timezone

from inventario.models import Inventario, InventarioItem
from inventario.models_operacional import InventarioAuditoriaEvento, InventarioLock, InventarioTarefa
from inventario.services.auditoria_operacional import registrar_evento_operacional
from inventario.services.locks import LockError, adquirir_lock, liberar_lock
from inventario.services.tarefas import (
    atualizar_status_tarefa,
    iniciar_tarefa,
    obter_tarefa_geral,
    posicao_atribuida_operador_geral,
)


MENSAGEM_DUPLICADA = 'Produto já inventariado nesta posição.'


class ContagemError(Exception):
    pass


class ContagemDuplicadaError(ContagemError):
    def __init__(self, mensagem: str = MENSAGEM_DUPLICADA, *, contexto_auditoria: dict | None = None):
        super().__init__(mensagem)
        self.contexto_auditoria = contexto_auditoria or {}


def obter_inventario_para_contagem(inventario_id: int | str | None) -> Inventario:
    if inventario_id in (None, ''):
        raise ContagemError('Inventário não encontrado (ID não informado).')
    try:
        pk = int(inventario_id)
    except (TypeError, ValueError) as exc:
        raise ContagemError(f'Inventário não encontrado (ID inválido: {inventario_id!r}).') from exc
    if pk <= 0:
        raise ContagemError(f'Inventário não encontrado (ID inválido: {pk}).')
    try:
        return Inventario.objects.get(pk=pk)
    except Inventario.DoesNotExist as exc:
        raise ContagemError(f'Inventário #{pk} não encontrado.') from exc


def _atualizar_status_inventario(inventario: Inventario) -> None:
    if inventario.status == Inventario.Status.ABERTO:
        inventario.status = Inventario.Status.EM_ANDAMENTO
        inventario.save(update_fields=['status'])


def persistir_auditoria_contagem_rejeitada(exc: ContagemDuplicadaError) -> None:
    if exc.contexto_auditoria:
        ctx = dict(exc.contexto_auditoria)
        ctx['lock'] = None
        _registrar_contagem_rejeitada(**ctx)


def _contexto_rejeicao_duplicidade(
    *,
    inventario,
    posicao,
    produto,
    quantidade_fisica,
    usuario_contagem,
    origem_contagem,
    dispositivo,
    ip,
    tarefa=None,
    lock=None,
) -> dict:
    return {
        'inventario': inventario,
        'posicao': posicao,
        'produto': produto,
        'quantidade_fisica': quantidade_fisica,
        'usuario_contagem': usuario_contagem,
        'origem_contagem': origem_contagem,
        'dispositivo': dispositivo,
        'ip': ip,
        'tarefa': tarefa,
        'lock': lock,
    }


def _registrar_contagem_rejeitada(
    *,
    inventario: Inventario,
    posicao,
    produto,
    quantidade_fisica,
    usuario_contagem,
    origem_contagem: str,
    dispositivo: str,
    ip: str | None,
    tarefa=None,
    lock=None,
) -> None:
    if usuario_contagem is None:
        return
    registrar_evento_operacional(
        evento=InventarioAuditoriaEvento.Evento.CONTAGEM_REJEITADA,
        tipo_inventario=InventarioLock.TipoInventario.GERAL,
        inventario=inventario,
        tarefa=tarefa,
        lock=lock,
        usuario=usuario_contagem,
        dispositivo=dispositivo,
        ip=ip,
        posicao=posicao,
        produto=produto,
        quantidade=quantidade_fisica,
        dados_extras={
            'motivo': 'DUPLICIDADE_INVENTARIO_POSICAO_PRODUTO',
            'origem_contagem': origem_contagem,
        },
    )


@transaction.atomic
def salvar_contagem(
    inventario: Inventario,
    posicao,
    produto,
    quantidade_fisica,
    item_existente: InventarioItem | None = None,
    usuario_contagem=None,
    origem_contagem: str = '',
    dispositivo: str = '',
    session_key: str = '',
    ip: str | None = None,
) -> InventarioItem:
    inventario_pk = inventario.pk
    if not inventario_pk:
        raise ContagemError('Inventário não encontrado (ID inválido).')
    try:
        inventario = Inventario.objects.select_for_update().get(pk=inventario_pk)
    except Inventario.DoesNotExist as exc:
        raise ContagemError(f'Inventário #{inventario_pk} não encontrado.') from exc

    lock = None
    tarefa = None
    if usuario_contagem is not None:
        if not posicao_atribuida_operador_geral(inventario, posicao, usuario_contagem):
            from inventario.services.tarefas import TarefaError
            raise TarefaError(
                'Posição não atribuída a você. Contate o supervisor.'
            )
        tarefa = obter_tarefa_geral(inventario, posicao, usuario_contagem)
        if tarefa:
            iniciar_tarefa(tarefa)
        lock_info = adquirir_lock(
            tipo_inventario=InventarioLock.TipoInventario.GERAL,
            inventario=inventario,
            posicao=posicao,
            usuario=usuario_contagem,
            tarefa=tarefa,
            dispositivo=dispositivo,
            session_key=session_key,
            ip=ip,
        )
        lock = lock_info.lock

    agora = timezone.now()
    registro = InventarioItem.objects.select_for_update().filter(
        inventario=inventario,
        posicao=posicao,
        produto=produto,
    ).first()

    if item_existente is not None:
        if registro is not None and registro.pk != item_existente.pk:
            raise ContagemDuplicadaError(
                contexto_auditoria=_contexto_rejeicao_duplicidade(
                    inventario=inventario,
                    posicao=posicao,
                    produto=produto,
                    quantidade_fisica=quantidade_fisica,
                    usuario_contagem=usuario_contagem,
                    origem_contagem=origem_contagem,
                    dispositivo=dispositivo,
                    ip=ip,
                    tarefa=tarefa,
                    lock=lock,
                ),
            )

        item = item_existente
        item.quantidade_fisica = quantidade_fisica
        item.data_contagem = agora
        if usuario_contagem is not None:
            item.usuario_contagem = usuario_contagem
        if origem_contagem:
            item.origem_contagem = origem_contagem
        item.save(update_fields=[
            'quantidade_fisica',
            'data_contagem',
            'usuario_contagem',
            'origem_contagem',
        ])
    elif registro is not None:
        raise ContagemDuplicadaError(
            contexto_auditoria=_contexto_rejeicao_duplicidade(
                inventario=inventario,
                posicao=posicao,
                produto=produto,
                quantidade_fisica=quantidade_fisica,
                usuario_contagem=usuario_contagem,
                origem_contagem=origem_contagem,
                dispositivo=dispositivo,
                ip=ip,
                tarefa=tarefa,
                lock=lock,
            ),
        )
    else:
        campos = {
            'inventario': inventario,
            'posicao': posicao,
            'produto': produto,
            'quantidade_fisica': quantidade_fisica,
            'data_contagem': agora,
        }
        if usuario_contagem is not None:
            campos['usuario_contagem'] = usuario_contagem
        if origem_contagem:
            campos['origem_contagem'] = origem_contagem
        try:
            item = InventarioItem.objects.create(**campos)
        except IntegrityError as exc:
            raise ContagemDuplicadaError(
                contexto_auditoria=_contexto_rejeicao_duplicidade(
                    inventario=inventario,
                    posicao=posicao,
                    produto=produto,
                    quantidade_fisica=quantidade_fisica,
                    usuario_contagem=usuario_contagem,
                    origem_contagem=origem_contagem,
                    dispositivo=dispositivo,
                    ip=ip,
                    tarefa=tarefa,
                    lock=lock,
                ),
            ) from exc

    _atualizar_status_inventario(inventario)

    if lock:
        liberar_lock(
            lock,
            motivo=InventarioLock.MotivoLiberacao.CONCLUIDO,
            ip=ip,
            usuario=usuario_contagem,
        )
    if tarefa:
        atualizar_status_tarefa(
            tarefa,
            InventarioTarefa.Status.CONTADA,
            usuario=usuario_contagem,
        )

    if usuario_contagem is not None:
        registrar_evento_operacional(
            evento=InventarioAuditoriaEvento.Evento.CONTAGEM,
            tipo_inventario=InventarioLock.TipoInventario.GERAL,
            inventario=inventario,
            tarefa=tarefa,
            lock=lock,
            usuario=usuario_contagem,
            dispositivo=dispositivo,
            ip=ip,
            posicao=posicao,
            produto=produto,
            quantidade=quantidade_fisica,
            status_novo=InventarioTarefa.Status.CONTADA,
        )

    return item


@transaction.atomic
def excluir_contagem(item: InventarioItem) -> None:
    item.delete()
