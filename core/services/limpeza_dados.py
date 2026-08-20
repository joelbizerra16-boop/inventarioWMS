from dataclasses import dataclass, field
from typing import Iterable

from django.contrib.auth.models import User
from django.db import transaction
from django.db.models import QuerySet
from django.db.models.deletion import ProtectedError

from accounts.models import Usuario
from core.logging_auditoria import registrar_evento
from core.services.exclusao import ExclusaoBloqueadaError, mensagem_de_protected_error
from estoque_fisico.models import EstoqueFisico
from estoque_sap.models import EstoqueSAP
from inventario.models import (
    CicloAuditoriaHistorico,
    CicloEstoqueFisicoAjuste,
    CicloInventario,
    CicloInventarioItem,
    CicloInventarioSku,
    CicloLoteExecucao,
    CicloLoteExecucaoItem,
    Inventario,
    InventarioAuditoriaEvento,
    InventarioItem,
    InventarioLock,
    InventarioTarefa,
)
from movimentacoes.models import Movimentacao
from posicoes.models import Posicao
from posicoes.services.exclusao import CODIGO_POSICAO_SEM_POSICAO
from produtos.models import AuditoriaHomologacao, Produto

FRASE_CONFIRMACAO = 'EXCLUIR'

GRUPOS_DEPENDENTES_POSICOES = (
    'inventario_ciclico',
    'inventario_geral',
    'estoque_fisico',
    'movimentacoes',
)


@dataclass(frozen=True)
class GrupoLimpeza:
    id: str
    titulo: str
    descricao: str
    quantidade: int
    protegido: bool = False
    dependencias: tuple[str, ...] = ()
    detalhes: tuple[tuple[str, int], ...] = ()


@dataclass
class ResultadoLimpeza:
    grupos: tuple[str, ...]
    grupos_incluidos: tuple[str, ...] = ()
    excluido_por_grupo: dict[str, int] = field(default_factory=dict)

    @property
    def total_excluidos(self) -> int:
        return sum(self.excluido_por_grupo.values())


def _contar(*querysets: QuerySet) -> int:
    return sum(qs.count() for qs in querysets)


def _apagar(*querysets: QuerySet) -> int:
    total = 0
    for qs in querysets:
        apagados, _ = qs.delete()
        total += apagados
    return total


def _querysets_ciclico() -> tuple[QuerySet, ...]:
    return (
        InventarioLock.objects.filter(tipo_inventario=InventarioLock.TipoInventario.CICLICO),
        InventarioTarefa.objects.filter(tipo_inventario=InventarioTarefa.TipoInventario.CICLICO),
        InventarioAuditoriaEvento.objects.filter(
            tipo_inventario=InventarioAuditoriaEvento.TipoInventario.CICLICO,
        ),
        CicloLoteExecucaoItem.objects.all(),
        CicloLoteExecucao.objects.all(),
        CicloEstoqueFisicoAjuste.objects.all(),
        CicloAuditoriaHistorico.objects.all(),
        CicloInventarioItem.objects.all(),
        CicloInventarioSku.objects.all(),
        CicloInventario.objects.all(),
    )


def _querysets_geral() -> tuple[QuerySet, ...]:
    return (
        InventarioLock.objects.filter(tipo_inventario=InventarioLock.TipoInventario.GERAL),
        InventarioTarefa.objects.filter(tipo_inventario=InventarioTarefa.TipoInventario.GERAL),
        InventarioAuditoriaEvento.objects.filter(
            tipo_inventario=InventarioAuditoriaEvento.TipoInventario.GERAL,
        ),
        InventarioItem.objects.all(),
        Inventario.objects.all(),
    )


def definir_grupos_limpeza() -> tuple[GrupoLimpeza, ...]:
    return (
        GrupoLimpeza(
            id='inventario_ciclico',
            titulo='Inventário cíclico',
            descricao=(
                'Ciclos, SKUs, contagens, lotes, histórico de auditoria, ajustes e '
                'tarefas operacionais do cíclico.'
            ),
            quantidade=_contar(*_querysets_ciclico()),
            detalhes=(
                ('Ciclos', CicloInventario.objects.count()),
                ('SKUs', CicloInventarioSku.objects.count()),
                ('Itens / posições', CicloInventarioItem.objects.count()),
                ('Lotes', CicloLoteExecucao.objects.count()),
                ('Histórico de auditoria', CicloAuditoriaHistorico.objects.count()),
                ('Ajustes de estoque', CicloEstoqueFisicoAjuste.objects.count()),
            ),
        ),
        GrupoLimpeza(
            id='inventario_geral',
            titulo='Inventário geral',
            descricao=(
                'Inventários, itens de contagem, locks, tarefas e auditoria operacional. '
                'O cadastro de produtos não é alterado.'
            ),
            quantidade=_contar(*_querysets_geral()),
            detalhes=(
                ('Inventários', Inventario.objects.count()),
                ('Itens de contagem', InventarioItem.objects.count()),
                (
                    'Locks / tarefas / eventos',
                    InventarioLock.objects.filter(
                        tipo_inventario=InventarioLock.TipoInventario.GERAL,
                    ).count()
                    + InventarioTarefa.objects.filter(
                        tipo_inventario=InventarioTarefa.TipoInventario.GERAL,
                    ).count()
                    + InventarioAuditoriaEvento.objects.filter(
                        tipo_inventario=InventarioAuditoriaEvento.TipoInventario.GERAL,
                    ).count(),
                ),
            ),
        ),
        GrupoLimpeza(
            id='estoque_fisico',
            titulo='Estoque físico',
            descricao='Saldos físicos publicados após consolidação ou ajuste.',
            quantidade=EstoqueFisico.objects.count(),
        ),
        GrupoLimpeza(
            id='estoque_sap',
            titulo='Estoque SAP',
            descricao='Saldos importados do SAP. O cadastro de produtos é mantido.',
            quantidade=EstoqueSAP.objects.count(),
        ),
        GrupoLimpeza(
            id='movimentacoes',
            titulo='Movimentações',
            descricao='Histórico de movimentações de estoque.',
            quantidade=Movimentacao.objects.count(),
        ),
        GrupoLimpeza(
            id='posicoes',
            titulo='Posições',
            descricao=(
                'Cadastro de posições, exceto a posição padrão SEM POSIÇÃO. '
                'Também remove inventários, estoque físico e movimentações vinculados.'
            ),
            quantidade=Posicao.objects.exclude(codigo=CODIGO_POSICAO_SEM_POSICAO).count(),
            dependencias=GRUPOS_DEPENDENTES_POSICOES,
        ),
        GrupoLimpeza(
            id='produtos',
            titulo='Cadastro de produtos',
            descricao='Protegido. SKUs, descrições e homologação não podem ser excluídos aqui.',
            quantidade=Produto.objects.count(),
            protegido=True,
        ),
        GrupoLimpeza(
            id='usuarios',
            titulo='Usuários e senhas',
            descricao='Protegido. Contas, perfis e senhas não podem ser excluídos aqui.',
            quantidade=Usuario.objects.count() + User.objects.count(),
            protegido=True,
        ),
    )


def grupos_por_id() -> dict[str, GrupoLimpeza]:
    return {grupo.id: grupo for grupo in definir_grupos_limpeza()}


def expandir_grupos(selecionados: Iterable[str]) -> tuple[str, ...]:
    escolhidos = []
    vistos = set()
    catalogo = grupos_por_id()
    for grupo_id in selecionados:
        if grupo_id in vistos:
            continue
        grupo = catalogo.get(grupo_id)
        if grupo is None or grupo.protegido:
            continue
        for dependencia in grupo.dependencias:
            if dependencia not in vistos:
                escolhidos.append(dependencia)
                vistos.add(dependencia)
        escolhidos.append(grupo_id)
        vistos.add(grupo_id)
    return tuple(escolhidos)


def validar_confirmacao(frase: str) -> None:
    if (frase or '').strip().upper() != FRASE_CONFIRMACAO:
        raise LimpezaDadosError(
            f'Digite {FRASE_CONFIRMACAO} para confirmar a exclusão definitiva.'
        )


def validar_grupos(selecionados: Iterable[str]) -> tuple[str, ...]:
    catalogo = grupos_por_id()
    escolhidos = []
    vistos = set()
    for grupo_id in selecionados:
        if grupo_id in vistos:
            continue
        grupo = catalogo.get(grupo_id)
        if grupo is None:
            raise LimpezaDadosError('Grupo de exclusão inválido.')
        if grupo.protegido:
            raise LimpezaDadosError(
                f'{grupo.titulo} está protegido e não pode ser excluído nesta tela.'
            )
        escolhidos.append(grupo_id)
        vistos.add(grupo_id)
    if not escolhidos:
        raise LimpezaDadosError('Selecione ao menos um grupo de dados para excluir.')
    return tuple(escolhidos)


def _excluir_inventario_ciclico() -> int:
    return _apagar(*_querysets_ciclico())


def _excluir_inventario_geral(*, desvincular_estoque: bool) -> int:
    if desvincular_estoque:
        EstoqueFisico.objects.filter(inventario_origem__isnull=False).update(
            inventario_origem=None,
        )
    return _apagar(*_querysets_geral())


def _excluir_posicoes() -> int:
    AuditoriaHomologacao.objects.filter(posicao__isnull=False).update(posicao=None)
    return _apagar(Posicao.objects.exclude(codigo=CODIGO_POSICAO_SEM_POSICAO))


def excluir_grupos_selecionados(
    selecionados: Iterable[str],
    *,
    usuario=None,
    confirmacao: str = '',
) -> ResultadoLimpeza:
    validar_confirmacao(confirmacao)
    grupos = validar_grupos(selecionados)
    executaveis = expandir_grupos(grupos)
    incluidos = tuple(item for item in executaveis if item not in grupos)
    resultado = ResultadoLimpeza(grupos=grupos, grupos_incluidos=incluidos)

    try:
        with transaction.atomic():
            if 'inventario_ciclico' in executaveis:
                resultado.excluido_por_grupo['inventario_ciclico'] = _excluir_inventario_ciclico()
            if 'movimentacoes' in executaveis:
                resultado.excluido_por_grupo['movimentacoes'] = _apagar(
                    Movimentacao.objects.all(),
                )
            if 'estoque_fisico' in executaveis:
                resultado.excluido_por_grupo['estoque_fisico'] = _apagar(
                    EstoqueFisico.objects.all(),
                )
            if 'inventario_geral' in executaveis:
                resultado.excluido_por_grupo['inventario_geral'] = _excluir_inventario_geral(
                    desvincular_estoque='estoque_fisico' not in executaveis,
                )
            if 'estoque_sap' in executaveis:
                resultado.excluido_por_grupo['estoque_sap'] = _apagar(EstoqueSAP.objects.all())
            if 'posicoes' in executaveis:
                resultado.excluido_por_grupo['posicoes'] = _excluir_posicoes()
    except ProtectedError as exc:
        raise ExclusaoBloqueadaError(mensagem_de_protected_error(exc)) from exc

    registrar_evento(
        'limpeza_dados',
        usuario=usuario,
        grupos=','.join(executaveis),
        total=resultado.total_excluidos,
    )
    return resultado


class LimpezaDadosError(Exception):
    def __init__(self, mensagem: str):
        self.mensagem = mensagem
        super().__init__(mensagem)
