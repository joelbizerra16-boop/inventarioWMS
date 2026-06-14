from decimal import Decimal

from django.utils import timezone

from inventario.models import Inventario, InventarioItem
from inventario.services.aprovacao import StatusAprovacao, obter_status_aprovacao
from inventario.services.consolidacao import inventario_foi_consolidado
from posicoes.models import Posicao
from produtos.models import Produto


class PocketContagemError(Exception):
    pass


SESSION_HISTORICO_PREFIX = 'pocket_historico_'
SESSION_MODO = 'pocket_modo'
MODO_INVENTARIO = 'inventario'
MODO_CICLICO = 'ciclico'
SESSION_POCKET_MANTER_CONTAGEM = 'pocket_ciclico_manter_sessao'
SESSION_HISTORICO_CICLICO = 'pocket_historico_ciclico'


def obter_modo_pocket(session) -> str:
    return session.get(SESSION_MODO, MODO_INVENTARIO)


def definir_modo_pocket(session, modo: str) -> None:
    session[SESSION_MODO] = modo
    session.modified = True


def obter_historico_pocket_ciclico(session) -> list[dict]:
    return session.get(SESSION_HISTORICO_CICLICO, [])


def registrar_historico_pocket_ciclico(
    session,
    posicao: Posicao,
    produto: Produto,
    quantidade: Decimal,
    dispositivo: str = '',
) -> None:
    historico = obter_historico_pocket_ciclico(session)
    historico.insert(0, {
        'data_hora': timezone.localtime(timezone.now()).strftime('%d/%m/%Y %H:%M:%S'),
        'posicao': posicao.codigo,
        'produto': produto.codigo_produto,
        'quantidade': str(quantidade),
        'dispositivo': dispositivo,
    })
    session[SESSION_HISTORICO_CICLICO] = historico[:20]
    session.modified = True


def validar_inventario_para_pocket(inventario: Inventario) -> None:
    if inventario.status == Inventario.Status.FINALIZADO:
        raise PocketContagemError('Inventário finalizado não permite contagem.')

    if inventario.status not in (
        Inventario.Status.ABERTO,
        Inventario.Status.EM_ANDAMENTO,
    ):
        raise PocketContagemError('Inventário não disponível para contagem.')

    if obter_status_aprovacao(inventario.pk) == StatusAprovacao.APROVADO:
        raise PocketContagemError('Inventário aprovado não permite contagem.')

    if (
        inventario.status == Inventario.Status.FINALIZADO
        and inventario_foi_consolidado(inventario.pk)
    ):
        raise PocketContagemError('Inventário consolidado não permite contagem.')


def listar_inventarios_pocket():
    return Inventario.objects.filter(
        status__in=(
            Inventario.Status.ABERTO,
            Inventario.Status.EM_ANDAMENTO,
        ),
    ).select_related('usuario').order_by('-data_criacao')


def buscar_posicao_por_codigo(codigo: str) -> Posicao | None:
    codigo = codigo.strip()
    if not codigo:
        return None
    return Posicao.objects.filter(codigo=codigo, ativo=True).first()


def buscar_produto_por_codigo(codigo: str) -> Produto | None:
    codigo = codigo.strip()
    if not codigo:
        return None

    produto = Produto.objects.filter(
        codigo_produto=codigo,
        ativo=True,
    ).first()
    if produto:
        return produto

    return Produto.objects.filter(
        codigo_ean=codigo,
        ativo=True,
    ).first()


def obter_item_existente(
    inventario: Inventario,
    posicao: Posicao,
    produto: Produto,
) -> InventarioItem | None:
    return InventarioItem.objects.filter(
        inventario=inventario,
        posicao=posicao,
        produto=produto,
    ).first()


SESSION_POSICAO_PREFIX = 'pocket_posicao_'


def chave_posicao_pocket(inventario_id: int) -> str:
    return f'{SESSION_POSICAO_PREFIX}{inventario_id}'


def obter_posicao_pocket(session, inventario_id: int) -> str:
    return session.get(chave_posicao_pocket(inventario_id), '')


def registrar_posicao_pocket(session, inventario_id: int, codigo_posicao: str) -> None:
    session[chave_posicao_pocket(inventario_id)] = codigo_posicao.strip()
    session.modified = True


def limpar_posicao_pocket(session, inventario_id: int) -> None:
    chave = chave_posicao_pocket(inventario_id)
    if chave in session:
        del session[chave]
        session.modified = True


def chave_historico_pocket(inventario_id: int) -> str:
    return f'{SESSION_HISTORICO_PREFIX}{inventario_id}'


def obter_historico_pocket(session, inventario_id: int) -> list[dict]:
    return session.get(chave_historico_pocket(inventario_id), [])


def registrar_historico_pocket(
    session,
    inventario_id: int,
    posicao: Posicao,
    produto: Produto,
    quantidade: Decimal,
) -> None:
    historico = obter_historico_pocket(session, inventario_id)
    historico.insert(0, {
        'data_hora': timezone.localtime(timezone.now()).strftime('%d/%m/%Y %H:%M:%S'),
        'posicao': posicao.codigo,
        'produto': produto.codigo_produto,
        'quantidade': str(quantidade),
    })
    session[chave_historico_pocket(inventario_id)] = historico[:20]
    session.modified = True
