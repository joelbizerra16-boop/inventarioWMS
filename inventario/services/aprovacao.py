from django.db import transaction
from django.utils import timezone

from inventario.models import Inventario
from inventario.services.confronto import ResultadoConfronto, executar_confronto


class StatusAprovacao:
    PENDENTE_APROVACAO = Inventario.StatusAprovacao.PENDENTE_APROVACAO
    APROVADO = Inventario.StatusAprovacao.APROVADO

    LABELS = {
        PENDENTE_APROVACAO: 'Pendente de Aprovação',
        APROVADO: 'Aprovado',
    }


class AprovacaoError(Exception):
    pass


def limpar_estado_aprovacao() -> None:
    Inventario.objects.update(
        status_aprovacao='',
        confronto_executado_em=None,
    )


def obter_status_aprovacao(inventario_id: int) -> str | None:
    status = (
        Inventario.objects.filter(pk=inventario_id)
        .values_list('status_aprovacao', flat=True)
        .first()
    )
    return status or None


def obter_label_status_aprovacao(inventario_id: int) -> str | None:
    status = obter_status_aprovacao(inventario_id)
    if status is None:
        return None
    return StatusAprovacao.LABELS[status]


def _validar_inventario_finalizado(inventario: Inventario) -> None:
    if inventario.status != Inventario.Status.FINALIZADO:
        raise AprovacaoError('Somente inventários finalizados podem ser aprovados.')


def _registrar_confronto_executado(inventario: Inventario) -> None:
    inventario.confronto_executado_em = timezone.now()
    if inventario.status_aprovacao != StatusAprovacao.APROVADO:
        inventario.status_aprovacao = StatusAprovacao.PENDENTE_APROVACAO
    inventario.save(update_fields=['confronto_executado_em', 'status_aprovacao'])


def consultar_aprovacao(
    inventario_id: int,
    termo_busca: str = '',
) -> ResultadoConfronto:
    inventario = Inventario.objects.get(pk=inventario_id)
    resultado = executar_confronto(
        inventario_id=inventario_id,
        filtro_status='divergencias',
        termo_busca=termo_busca,
    )
    _registrar_confronto_executado(inventario)
    return resultado


@transaction.atomic
def aprovar_inventario(inventario: Inventario) -> None:
    inventario.refresh_from_db()
    _validar_inventario_finalizado(inventario)

    if inventario.status_aprovacao == StatusAprovacao.APROVADO:
        raise AprovacaoError('Inventário já está aprovado.')

    if inventario.confronto_executado_em is None:
        raise AprovacaoError(
            'Execute o confronto antes de aprovar o inventário.',
        )

    inventario.status_aprovacao = StatusAprovacao.APROVADO
    inventario.save(update_fields=['status_aprovacao'])


@transaction.atomic
def reabrir_inventario(inventario: Inventario) -> None:
    inventario.refresh_from_db()
    _validar_inventario_finalizado(inventario)

    if inventario.itens.exists():
        inventario.status = Inventario.Status.EM_ANDAMENTO
    else:
        inventario.status = Inventario.Status.ABERTO

    inventario.status_aprovacao = ''
    inventario.confronto_executado_em = None
    inventario.save(update_fields=['status', 'status_aprovacao', 'confronto_executado_em'])

    from inventario.services.consolidacao import limpar_auditoria_inventario

    limpar_auditoria_inventario(inventario.pk)


def pode_aprovar(inventario_id: int) -> bool:
    return obter_status_aprovacao(inventario_id) == StatusAprovacao.PENDENTE_APROVACAO


def pode_reabrir(inventario_id: int) -> bool:
    return obter_status_aprovacao(inventario_id) in (
        StatusAprovacao.PENDENTE_APROVACAO,
        StatusAprovacao.APROVADO,
    )


def listar_ids_inventarios_aprovados() -> list[int]:
    return list(
        Inventario.objects.filter(
            status=Inventario.Status.FINALIZADO,
            status_aprovacao=StatusAprovacao.APROVADO,
        ).values_list('pk', flat=True),
    )
