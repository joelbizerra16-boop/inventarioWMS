"""Recontagem automática com operador distinto dos contadores anteriores."""

from django.db import transaction

from accounts.models import Usuario
from inventario.models import CicloInventarioItem
from inventario.models_operacional import InventarioAuditoriaEvento, InventarioTarefa
from inventario.services.auditoria_operacional import registrar_evento_operacional
from inventario.services.tarefas import TarefaError


def _operadores_elegiveis(excluir_ids: set[int]) -> list[Usuario]:
    return list(
        Usuario.objects.filter(
            is_active=True,
            perfil_operacional__perfil__in=(
                Usuario.Perfil.OPERADOR,
                Usuario.Perfil.INVENTARIO,
            ),
        )
        .exclude(pk__in=excluir_ids)
        .select_related('perfil_operacional')
        .order_by('pk')
    )


def _coletar_operadores_anteriores(item: CicloInventarioItem) -> set[int]:
    ids: set[int] = set()
    if item.usuario_contagem_id:
        ids.add(item.usuario_contagem_id)
    if item.usuario_recontagem_id:
        ids.add(item.usuario_recontagem_id)
    for uid in CicloInventarioItem.objects.filter(
        ciclo=item.ciclo,
        produto=item.produto,
    ).values_list('usuario_contagem_id', flat=True):
        if uid:
            ids.add(uid)
    tarefas = InventarioTarefa.objects.filter(ciclo_item=item)
    for tarefa in tarefas:
        ids.add(tarefa.operador_id)
        ids.update(tarefa.operadores_anteriores or [])
    return ids


def _selecionar_operador_recontagem(
    item: CicloInventarioItem,
    operadores_disponiveis: list[Usuario] | None = None,
) -> Usuario:
    excluir = _coletar_operadores_anteriores(item)
    candidatos = operadores_disponiveis or _operadores_elegiveis(excluir)
    candidatos = [op for op in candidatos if op.pk not in excluir]
    if not candidatos:
        raise TarefaError(
            'Não há operador disponível para recontagem '
            '(todos os elegíveis já contaram este item).'
        )
    cargas = {}
    for op in candidatos:
        cargas[op.pk] = InventarioTarefa.objects.filter(
            operador=op,
            status__in=(
                InventarioTarefa.Status.PENDENTE,
                InventarioTarefa.Status.EM_CONTAGEM,
                InventarioTarefa.Status.EM_RECONTAGEM,
            ),
        ).count()
    return min(candidatos, key=lambda op: (cargas.get(op.pk, 0), op.pk))


@transaction.atomic
def gerar_recontagem_terceiro_operador(
    item: CicloInventarioItem,
    *,
    gerado_por=None,
    operadores_disponiveis: list[Usuario] | None = None,
) -> InventarioTarefa:
    if item.status_contagem not in (
        CicloInventarioItem.StatusContagem.DIVERGENTE,
        CicloInventarioItem.StatusContagem.RECONTAGEM,
    ):
        raise TarefaError('Recontagem automática apenas para itens divergentes.')

    operadores_anteriores = list(_coletar_operadores_anteriores(item))
    operador = _selecionar_operador_recontagem(item, operadores_disponiveis)

    status_anterior = item.status_contagem
    item.status_contagem = CicloInventarioItem.StatusContagem.RECONTAGEM
    item.quantidade_fisica = None
    item.quantidade_recontagem = None
    item.diferenca = None
    item.save(update_fields=[
        'status_contagem', 'quantidade_fisica', 'quantidade_recontagem', 'diferenca',
    ])

    tarefa = InventarioTarefa.objects.create(
        tipo_inventario=InventarioTarefa.TipoInventario.CICLICO,
        ciclo=item.ciclo,
        ciclo_item=item,
        posicao=item.posicao,
        produto=item.produto,
        operador=operador,
        status=InventarioTarefa.Status.EM_RECONTAGEM,
        modo_atribuicao=InventarioTarefa.ModoAtribuicao.AUTOMATICA,
        eh_recontagem=True,
        operadores_anteriores=operadores_anteriores,
        atribuido_por=gerado_por,
    )

    registrar_evento_operacional(
        evento=InventarioAuditoriaEvento.Evento.RECONTAGEM_GERADA,
        tipo_inventario=InventarioTarefa.TipoInventario.CICLICO,
        ciclo=item.ciclo,
        tarefa=tarefa,
        usuario=gerado_por,
        posicao=item.posicao,
        produto=item.produto,
        status_anterior=status_anterior,
        status_novo=item.status_contagem,
        dados_extras={
            'operador_recontagem_id': operador.pk,
            'operadores_excluidos': operadores_anteriores,
        },
    )
    return tarefa


def processar_divergencia_pos_contagem(
    item: CicloInventarioItem,
    *,
    gerado_por=None,
) -> InventarioTarefa | None:
    if item.status_contagem != CicloInventarioItem.StatusContagem.DIVERGENTE:
        return None
    ja_existe = InventarioTarefa.objects.filter(
        ciclo_item=item,
        eh_recontagem=True,
        status__in=(
            InventarioTarefa.Status.PENDENTE,
            InventarioTarefa.Status.EM_CONTAGEM,
            InventarioTarefa.Status.EM_RECONTAGEM,
        ),
    ).exists()
    if ja_existe:
        return None
    return gerar_recontagem_terceiro_operador(item, gerado_por=gerado_por)
