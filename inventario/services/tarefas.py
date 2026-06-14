"""Distribuição e gestão de tarefas de inventário multiusuário."""

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from accounts.models import Usuario
from inventario.models import CicloInventario, CicloInventarioItem, Inventario
from inventario.models_operacional import InventarioAuditoriaEvento, InventarioTarefa
from inventario.services.auditoria_operacional import registrar_evento_operacional
from posicoes.models import Posicao


class TarefaError(Exception):
    pass


STATUS_ABERTOS = {
    InventarioTarefa.Status.PENDENTE,
    InventarioTarefa.Status.EM_CONTAGEM,
    InventarioTarefa.Status.EM_RECONTAGEM,
}


def usuario_e_supervisor(user) -> bool:
    from accounts.services.perfil import obter_perfil_usuario
    return obter_perfil_usuario(user) in (
        Usuario.Perfil.ADMINISTRADOR,
        Usuario.Perfil.SUPERVISOR,
        Usuario.Perfil.INVENTARIO,
    )


def _filtro_area(criterio: dict) -> Q:
    filtro = Q()
    if criterio.get('rua'):
        filtro &= Q(rua=criterio['rua'])
    if criterio.get('predio'):
        filtro &= Q(predio=criterio['predio'])
    if criterio.get('nivel'):
        filtro &= Q(nivel=criterio['nivel'])
    if criterio.get('setor'):
        filtro &= Q(posicao__icontains=criterio['setor'])
    if criterio.get('posicao_ids'):
        filtro &= Q(pk__in=criterio['posicao_ids'])
    return filtro


@transaction.atomic
def distribuir_tarefas_geral(
    inventario: Inventario,
    operadores: list,
    *,
    modo: str = InventarioTarefa.ModoAtribuicao.AUTOMATICA,
    criterio_area: dict | None = None,
    atribuido_por=None,
) -> list[InventarioTarefa]:
    if not operadores:
        raise TarefaError('Informe ao menos um operador.')

    posicoes = Posicao.objects.filter(ativo=True)
    if criterio_area:
        posicoes = posicoes.filter(_filtro_area(criterio_area))
    posicoes = list(posicoes.order_by('codigo'))
    if not posicoes:
        raise TarefaError('Nenhuma posição encontrada para distribuição.')

    tarefas_criadas: list[InventarioTarefa] = []
    for indice, posicao in enumerate(posicoes):
        operador = operadores[indice % len(operadores)]
        tarefa, criada = InventarioTarefa.objects.get_or_create(
            tipo_inventario=InventarioTarefa.TipoInventario.GERAL,
            inventario=inventario,
            posicao=posicao,
            produto=None,
            operador=operador,
            defaults={
                'modo_atribuicao': modo,
                'area_criterio': criterio_area or {},
                'atribuido_por': atribuido_por,
                'ordem': indice,
            },
        )
        if criada:
            tarefas_criadas.append(tarefa)
            registrar_evento_operacional(
                evento=InventarioAuditoriaEvento.Evento.TAREFA_ATRIBUIDA,
                tipo_inventario=InventarioTarefa.TipoInventario.GERAL,
                inventario=inventario,
                tarefa=tarefa,
                usuario=atribuido_por,
                posicao=posicao,
                status_novo=InventarioTarefa.Status.PENDENTE,
                dados_extras={
                    'operador_id': operador.pk,
                    'modo': modo,
                },
            )
    return tarefas_criadas


@transaction.atomic
def distribuir_tarefas_ciclico(
    ciclo: CicloInventario,
    operadores: list,
    *,
    item_ids: list[int] | None = None,
    modo: str = InventarioTarefa.ModoAtribuicao.AUTOMATICA,
    atribuido_por=None,
) -> list[InventarioTarefa]:
    if not operadores:
        raise TarefaError('Informe ao menos um operador.')

    itens = CicloInventarioItem.objects.filter(
        ciclo=ciclo,
        status_contagem__in=(
            CicloInventarioItem.StatusContagem.PENDENTE,
            CicloInventarioItem.StatusContagem.RECONTAGEM,
            CicloInventarioItem.StatusContagem.EM_CONTAGEM,
        ),
    ).select_related('posicao', 'produto')
    if item_ids:
        itens = itens.filter(pk__in=item_ids)
    itens = list(itens.order_by('codigo_produto', 'codigo_posicao'))
    if not itens:
        raise TarefaError('Nenhuma posição pendente para distribuição.')

    tarefas_criadas: list[InventarioTarefa] = []
    for indice, item in enumerate(itens):
        operador = operadores[indice % len(operadores)]
        eh_recontagem = item.status_contagem == CicloInventarioItem.StatusContagem.RECONTAGEM
        tarefa, criada = InventarioTarefa.objects.get_or_create(
            tipo_inventario=InventarioTarefa.TipoInventario.CICLICO,
            ciclo=ciclo,
            ciclo_item=item,
            posicao=item.posicao,
            produto=item.produto,
            operador=operador,
            eh_recontagem=eh_recontagem,
            defaults={
                'modo_atribuicao': modo,
                'atribuido_por': atribuido_por,
                'ordem': indice,
                'status': (
                    InventarioTarefa.Status.EM_RECONTAGEM
                    if eh_recontagem
                    else InventarioTarefa.Status.PENDENTE
                ),
            },
        )
        if criada:
            tarefas_criadas.append(tarefa)
            registrar_evento_operacional(
                evento=InventarioAuditoriaEvento.Evento.TAREFA_ATRIBUIDA,
                tipo_inventario=InventarioTarefa.TipoInventario.CICLICO,
                ciclo=ciclo,
                tarefa=tarefa,
                usuario=atribuido_por,
                posicao=item.posicao,
                produto=item.produto,
                status_novo=tarefa.status,
                dados_extras={
                    'operador_id': operador.pk,
                    'ciclo_item_id': item.pk,
                    'modo': modo,
                },
            )
    return tarefas_criadas


def listar_tarefas_operador(
    usuario,
    *,
    tipo_inventario: str | None = None,
    inventario=None,
    ciclo=None,
) -> list[InventarioTarefa]:
    qs = InventarioTarefa.objects.filter(
        operador=usuario,
        status__in=STATUS_ABERTOS,
    ).select_related('posicao', 'produto', 'inventario', 'ciclo', 'ciclo_item')
    if tipo_inventario:
        qs = qs.filter(tipo_inventario=tipo_inventario)
    if inventario:
        qs = qs.filter(inventario=inventario)
    if ciclo:
        qs = qs.filter(ciclo=ciclo)
    return list(qs.order_by('ordem', 'pk'))


def listar_tarefas_supervisor(
    *,
    inventario=None,
    ciclo=None,
) -> list[InventarioTarefa]:
    qs = InventarioTarefa.objects.select_related(
        'posicao', 'produto', 'operador', 'operador__perfil_operacional',
    )
    if inventario:
        qs = qs.filter(inventario=inventario)
    elif ciclo:
        qs = qs.filter(ciclo=ciclo)
    else:
        qs = qs.filter(
            Q(inventario__status__in=(
                Inventario.Status.ABERTO,
                Inventario.Status.EM_ANDAMENTO,
            ))
            | Q(ciclo__status_ciclo=CicloInventario.StatusCiclo.ATIVO)
        )
    return list(qs.order_by('ordem', 'pk'))


def obter_tarefa_geral(inventario, posicao, operador) -> InventarioTarefa | None:
    return InventarioTarefa.objects.filter(
        tipo_inventario=InventarioTarefa.TipoInventario.GERAL,
        inventario=inventario,
        posicao=posicao,
        operador=operador,
        status__in=STATUS_ABERTOS,
    ).first()


def obter_tarefa_ciclica(ciclo_item, operador) -> InventarioTarefa | None:
    return InventarioTarefa.objects.filter(
        tipo_inventario=InventarioTarefa.TipoInventario.CICLICO,
        ciclo_item=ciclo_item,
        operador=operador,
        status__in=STATUS_ABERTOS,
    ).first()


def validar_acesso_tarefa(tarefa: InventarioTarefa, usuario) -> None:
    if usuario_e_supervisor(usuario):
        return
    if tarefa.operador_id != usuario.pk:
        raise TarefaError(
            f'Posição atribuída a outro operador ({tarefa.operador_id}). '
            'Contate o supervisor.'
        )


def posicao_atribuida_operador_geral(
    inventario,
    posicao,
    operador,
) -> bool:
    existe_distribuicao = InventarioTarefa.objects.filter(
        tipo_inventario=InventarioTarefa.TipoInventario.GERAL,
        inventario=inventario,
    ).exists()
    if not existe_distribuicao:
        return True
    return InventarioTarefa.objects.filter(
        tipo_inventario=InventarioTarefa.TipoInventario.GERAL,
        inventario=inventario,
        posicao=posicao,
        operador=operador,
        status__in=STATUS_ABERTOS,
    ).exists()


def item_atribuido_operador_ciclico(ciclo_item, operador) -> bool:
    existe_distribuicao = InventarioTarefa.objects.filter(
        tipo_inventario=InventarioTarefa.TipoInventario.CICLICO,
        ciclo=ciclo_item.ciclo,
    ).exists()
    if not existe_distribuicao:
        return True
    return InventarioTarefa.objects.filter(
        tipo_inventario=InventarioTarefa.TipoInventario.CICLICO,
        ciclo_item=ciclo_item,
        operador=operador,
        status__in=STATUS_ABERTOS,
    ).exists()


@transaction.atomic
def iniciar_tarefa(tarefa: InventarioTarefa) -> InventarioTarefa:
    if tarefa.status not in (
        InventarioTarefa.Status.PENDENTE,
        InventarioTarefa.Status.EM_RECONTAGEM,
    ):
        return tarefa
    status_anterior = tarefa.status
    tarefa.status = InventarioTarefa.Status.EM_CONTAGEM
    tarefa.iniciado_em = timezone.now()
    tarefa.save(update_fields=['status', 'iniciado_em'])
    registrar_evento_operacional(
        evento=InventarioAuditoriaEvento.Evento.TAREFA_INICIADA,
        tipo_inventario=tarefa.tipo_inventario,
        inventario=tarefa.inventario,
        ciclo=tarefa.ciclo,
        tarefa=tarefa,
        usuario=tarefa.operador,
        posicao=tarefa.posicao,
        produto=tarefa.produto,
        status_anterior=status_anterior,
        status_novo=tarefa.status,
    )
    return tarefa


@transaction.atomic
def atualizar_status_tarefa(
    tarefa: InventarioTarefa | None,
    novo_status: str,
    *,
    usuario=None,
) -> None:
    if tarefa is None:
        return
    status_anterior = tarefa.status
    if status_anterior == novo_status:
        return
    tarefa.status = novo_status
    campos = ['status']
    if novo_status in (
        InventarioTarefa.Status.CONTADA,
        InventarioTarefa.Status.APROVADA,
        InventarioTarefa.Status.FINALIZADA,
        InventarioTarefa.Status.DIVERGENTE,
    ):
        tarefa.finalizado_em = timezone.now()
        campos.append('finalizado_em')
    tarefa.save(update_fields=campos)
    registrar_evento_operacional(
        evento=InventarioAuditoriaEvento.Evento.STATUS_ALTERADO,
        tipo_inventario=tarefa.tipo_inventario,
        inventario=tarefa.inventario,
        ciclo=tarefa.ciclo,
        tarefa=tarefa,
        usuario=usuario or tarefa.operador,
        posicao=tarefa.posicao,
        produto=tarefa.produto,
        status_anterior=status_anterior,
        status_novo=novo_status,
    )
