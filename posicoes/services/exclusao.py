import logging
from dataclasses import dataclass
from decimal import Decimal

from django.db import transaction

from posicoes.models import Posicao

logger = logging.getLogger(__name__)

CODIGO_POSICAO_SEM_POSICAO = 'CICLICO-SEM-POS'
MENSAGEM_SUCESSO_COM_VINCULOS = (
    'Posição excluída com sucesso. Os registros vinculados foram transferidos para SEM POSIÇÃO.'
)


class ExclusaoPosicaoError(Exception):
    pass


@dataclass(frozen=True)
class ResultadoExclusaoPosicao:
    houve_vinculos: bool
    codigo_posicao: str


def obter_posicao_sem_posicao() -> Posicao:
    posicao, _ = Posicao.objects.get_or_create(
        codigo=CODIGO_POSICAO_SEM_POSICAO,
        defaults={
            'posicao': 'Sem posição definida',
            'ativo': True,
        },
    )
    return posicao


def _decimal(valor) -> Decimal:
    if valor is None:
        return Decimal('0')
    return Decimal(str(valor))


def _usuario_log(usuario) -> str:
    if usuario is None:
        return '—'
    if hasattr(usuario, 'get_username'):
        return usuario.get_username() or str(usuario.pk)
    return str(usuario)


def _mesclar_ou_transferir_item_ciclo(item, posicao_destino: Posicao) -> None:
    from inventario.models import CicloInventarioItem

    existente = CicloInventarioItem.objects.filter(
        ciclo_id=item.ciclo_id,
        produto_id=item.produto_id,
        posicao=posicao_destino,
    ).exclude(pk=item.pk).first()

    if existente is None:
        CicloInventarioItem.objects.filter(pk=item.pk).update(
            posicao=posicao_destino,
            codigo_posicao=posicao_destino.codigo,
            alocacao=posicao_destino.posicao,
        )
        return

    if item.quantidade_fisica is not None:
        if existente.quantidade_fisica is None:
            existente.quantidade_fisica = item.quantidade_fisica
        else:
            existente.quantidade_fisica = _decimal(existente.quantidade_fisica) + _decimal(
                item.quantidade_fisica
            )
        if item.diferenca is not None:
            if existente.diferenca is None:
                existente.diferenca = item.diferenca
            else:
                existente.diferenca = _decimal(existente.diferenca) + _decimal(item.diferenca)

    if not existente.usuario_contagem_id and item.usuario_contagem_id:
        existente.usuario_contagem_id = item.usuario_contagem_id
    if not existente.data_contagem and item.data_contagem:
        existente.data_contagem = item.data_contagem
    if not existente.origem_contagem and item.origem_contagem:
        existente.origem_contagem = item.origem_contagem
    if not existente.dispositivo_contagem and item.dispositivo_contagem:
        existente.dispositivo_contagem = item.dispositivo_contagem

    existente.save()
    item.delete()


def _transferir_itens_ciclo(posicao: Posicao, posicao_destino: Posicao) -> int:
    from inventario.models import CicloInventarioItem

    itens = list(CicloInventarioItem.objects.filter(posicao=posicao))
    for item in itens:
        _mesclar_ou_transferir_item_ciclo(item, posicao_destino)
    return len(itens)


def _mesclar_ou_transferir_item_inventario(item, posicao_destino: Posicao) -> None:
    from inventario.models import InventarioItem

    existente = InventarioItem.objects.filter(
        inventario_id=item.inventario_id,
        produto_id=item.produto_id,
        posicao=posicao_destino,
    ).exclude(pk=item.pk).first()

    if existente is None:
        InventarioItem.objects.filter(pk=item.pk).update(posicao=posicao_destino)
        return

    existente.quantidade_fisica = _decimal(existente.quantidade_fisica) + _decimal(
        item.quantidade_fisica
    )
    if not existente.usuario_contagem_id and item.usuario_contagem_id:
        existente.usuario_contagem_id = item.usuario_contagem_id
    if not existente.data_contagem and item.data_contagem:
        existente.data_contagem = item.data_contagem
    if not existente.origem_contagem and item.origem_contagem:
        existente.origem_contagem = item.origem_contagem
    existente.save()
    item.delete()


def _transferir_itens_inventario(posicao: Posicao, posicao_destino: Posicao) -> int:
    from inventario.models import InventarioItem

    itens = list(InventarioItem.objects.filter(posicao=posicao))
    for item in itens:
        _mesclar_ou_transferir_item_inventario(item, posicao_destino)
    return len(itens)


def _transferir_estoques_fisicos(posicao: Posicao, posicao_destino: Posicao) -> int:
    from estoque_fisico.models import EstoqueFisico

    registros = list(EstoqueFisico.objects.filter(posicao=posicao))
    for registro in registros:
        existente = EstoqueFisico.objects.filter(
            produto_id=registro.produto_id,
            posicao=posicao_destino,
        ).exclude(pk=registro.pk).first()
        if existente:
            existente.quantidade = _decimal(existente.quantidade) + _decimal(registro.quantidade)
            existente.save(update_fields=['quantidade'])
            registro.delete()
        else:
            registro.posicao = posicao_destino
            registro.save(update_fields=['posicao'])
    return len(registros)


def _transferir_ajustes_ciclo(posicao: Posicao, posicao_destino: Posicao) -> int:
    from inventario.models import CicloEstoqueFisicoAjuste

    return CicloEstoqueFisicoAjuste.objects.filter(posicao=posicao).update(
        posicao=posicao_destino,
        codigo_posicao=posicao_destino.codigo,
    )


def _transferir_movimentacoes(posicao: Posicao, posicao_destino: Posicao) -> int:
    from movimentacoes.models import Movimentacao

    return Movimentacao.objects.filter(posicao=posicao).update(posicao=posicao_destino)


def _desvincular_auditorias_homologacao(posicao: Posicao) -> int:
    from produtos.models import AuditoriaHomologacao

    return AuditoriaHomologacao.objects.filter(posicao=posicao).update(posicao=None)


@transaction.atomic
def excluir_posicao_com_vinculos(posicao: Posicao, usuario=None) -> ResultadoExclusaoPosicao:
    if posicao.codigo == CODIGO_POSICAO_SEM_POSICAO:
        raise ExclusaoPosicaoError('Não é possível excluir a posição padrão SEM POSIÇÃO.')

    posicao_destino = obter_posicao_sem_posicao()
    codigo = posicao.codigo
    posicao_id = posicao.pk

    qtd_ciclo = _transferir_itens_ciclo(posicao, posicao_destino)
    qtd_inventario = _transferir_itens_inventario(posicao, posicao_destino)
    qtd_estoque = _transferir_estoques_fisicos(posicao, posicao_destino)
    qtd_ajustes = _transferir_ajustes_ciclo(posicao, posicao_destino)
    qtd_movimentacoes = _transferir_movimentacoes(posicao, posicao_destino)
    qtd_auditorias = _desvincular_auditorias_homologacao(posicao)

    houve_vinculos = any(
        qtd > 0
        for qtd in (
            qtd_ciclo,
            qtd_inventario,
            qtd_estoque,
            qtd_ajustes,
            qtd_movimentacoes,
            qtd_auditorias,
        )
    )

    posicao.delete()

    logger.info(
        'Posição excluída: id=%s codigo=%s usuario=%s '
        'itens_ciclo=%s itens_inventario=%s estoques_fisicos=%s '
        'ajustes_ciclo=%s movimentacoes=%s auditorias_homologacao=%s',
        posicao_id,
        codigo,
        _usuario_log(usuario),
        qtd_ciclo,
        qtd_inventario,
        qtd_estoque,
        qtd_ajustes,
        qtd_movimentacoes,
        qtd_auditorias,
    )

    return ResultadoExclusaoPosicao(
        houve_vinculos=houve_vinculos,
        codigo_posicao=codigo,
    )
