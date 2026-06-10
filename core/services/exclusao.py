import logging

from django.core.exceptions import ValidationError
from core.logging_auditoria import registrar_evento
from django.db import IntegrityError, router
from django.db.models.deletion import Collector, ProtectedError

logger = logging.getLogger(__name__)

MENSAGEM_BLOQUEIO_PADRAO = (
    'Não é possível excluir este registro porque existem registros vinculados.'
)
MENSAGEM_INTEGRIDADE = (
    'Não é possível concluir esta operação porque existem registros vinculados.'
)
MENSAGEM_NAO_ENCONTRADO = 'Registro não encontrado.'
MENSAGEM_PERMISSAO = 'Você não tem permissão para realizar esta ação.'
MENSAGEM_ERRO_INESPERADO = (
    'Ocorreu um erro inesperado. Tente novamente ou contate o suporte.'
)


class ExclusaoBloqueadaError(Exception):
    def __init__(self, mensagem: str | None = None):
        self.mensagem = mensagem or MENSAGEM_BLOQUEIO_PADRAO
        super().__init__(self.mensagem)


def mensagem_de_protected_error(exc: ProtectedError) -> str:
    rotulos = []
    vistos = set()
    for objeto in exc.protected_objects:
        rotulo = objeto._meta.verbose_name_plural
        if rotulo in vistos:
            continue
        vistos.add(rotulo)
        rotulos.append(str(rotulo))

    if not rotulos:
        return MENSAGEM_BLOQUEIO_PADRAO

    if len(rotulos) == 1:
        return (
            'Não é possível excluir este registro porque existem registros '
            f'vinculados em {rotulos[0]}.'
        )

    return (
        'Não é possível excluir este registro porque existem registros '
        f'vinculados: {", ".join(rotulos)}.'
    )


def mensagem_de_validation_error(exc: ValidationError) -> str:
    if hasattr(exc, 'message_dict'):
        mensagens = []
        for erros in exc.message_dict.values():
            if isinstance(erros, list):
                mensagens.extend(str(erro) for erro in erros)
            else:
                mensagens.append(str(erros))
        if mensagens:
            return mensagens[0]
    if hasattr(exc, 'messages') and exc.messages:
        return str(exc.messages[0])
    return str(exc)


def mensagem_de_excecao_operacional(exc: Exception) -> str:
    if isinstance(exc, ExclusaoBloqueadaError):
        return exc.mensagem
    if isinstance(exc, ProtectedError):
        return mensagem_de_protected_error(exc)
    if isinstance(exc, IntegrityError):
        return MENSAGEM_INTEGRIDADE
    if isinstance(exc, ValidationError):
        return mensagem_de_validation_error(exc)
    return MENSAGEM_ERRO_INESPERADO


def validar_exclusao(instance) -> None:
    using = router.db_for_write(type(instance), instance=instance)
    collector = Collector(using=using)
    try:
        collector.collect([instance])
    except ProtectedError as exc:
        raise ExclusaoBloqueadaError(mensagem_de_protected_error(exc)) from exc


def excluir_registro_seguro(instance, usuario=None) -> None:
    validar_exclusao(instance)
    try:
        pk = getattr(instance, 'pk', None)
        label = instance._meta.label
        instance.delete()
        registrar_evento(
            'exclusao',
            usuario=usuario,
            modelo=label,
            registro_id=pk,
        )
    except ProtectedError as exc:
        logger.warning(
            'ProtectedError ao excluir %s pk=%s: %s',
            instance._meta.label,
            getattr(instance, 'pk', None),
            exc,
        )
        raise ExclusaoBloqueadaError(mensagem_de_protected_error(exc)) from exc
    except IntegrityError as exc:
        logger.warning(
            'IntegrityError ao excluir %s pk=%s: %s',
            instance._meta.label,
            getattr(instance, 'pk', None),
            exc,
        )
        raise ExclusaoBloqueadaError(MENSAGEM_INTEGRIDADE) from exc
