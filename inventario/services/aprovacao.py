from django.db import transaction

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


def consultar_aprovacao(
    inventario_id: int,
    termo_busca: str = '',
) -> ResultadoConfronto:
    """Lê divergências para a tela de aprovação, sem gravar.

    Inventário finalizado sai só da fotografia. Inventário ainda aberto
    confronta ao vivo. Nenhum dos dois caminhos altera confronto_executado_em
    nem recalcula o fechamento.
    """
    inventario = Inventario.objects.get(pk=inventario_id)
    if inventario.status == Inventario.Status.FINALIZADO:
        from inventario.services.inventario_snapshot import leitura_inventario_congelado

        resultado, _aviso = leitura_inventario_congelado(
            inventario,
            canal=None,
            filtro_status='divergencias',
            termo_busca=termo_busca,
        )
        return resultado
    return executar_confronto(
        inventario_id=inventario_id,
        filtro_status='divergencias',
        termo_busca=termo_busca,
    )


@transaction.atomic
def aprovar_inventario(inventario: Inventario) -> None:
    inventario.refresh_from_db()
    _validar_inventario_finalizado(inventario)

    if inventario.status_aprovacao == StatusAprovacao.APROVADO:
        raise AprovacaoError('Inventário já está aprovado.')

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
    registro = (
        Inventario.objects.filter(pk=inventario_id)
        .values_list('status', 'status_aprovacao')
        .first()
    )
    if registro is None:
        return False
    status, status_aprovacao = registro
    return (
        status == Inventario.Status.FINALIZADO
        and status_aprovacao != StatusAprovacao.APROVADO
    )


def pode_reabrir(inventario_id: int) -> bool:
    status = (
        Inventario.objects.filter(pk=inventario_id)
        .values_list('status', flat=True)
        .first()
    )
    return status == Inventario.Status.FINALIZADO


def listar_ids_inventarios_aprovados() -> list[int]:
    return list(
        Inventario.objects.filter(
            status=Inventario.Status.FINALIZADO,
            status_aprovacao=StatusAprovacao.APROVADO,
        ).values_list('pk', flat=True),
    )
