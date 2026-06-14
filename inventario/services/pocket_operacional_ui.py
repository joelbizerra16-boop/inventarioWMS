"""Contexto de apresentação operacional para telas Pocket (somente leitura)."""

from inventario.models import Inventario, InventarioItem
from inventario.services.locks import obter_dispositivo
from posicoes.models import Posicao

_STATUS_BADGE = {
    Inventario.Status.ABERTO: 'aberto',
    Inventario.Status.EM_ANDAMENTO: 'andamento',
    Inventario.Status.FINALIZADO: 'fechado',
}


def _nome_usuario(user) -> str:
    if user is None or not getattr(user, 'is_authenticated', False):
        return '—'
    nome = user.get_full_name().strip()
    return nome or user.get_username()


def _coletor_id(request) -> str:
    dispositivo = obter_dispositivo(request)
    if not dispositivo:
        return ''
    return dispositivo[:24]


def montar_operacional_geral(request, inventario: Inventario) -> dict:
    status = inventario.status
    return {
        'modo_label': 'GERAL',
        'referencia_label': f'Inventário #{inventario.pk}',
        'status_label': inventario.get_status_display().upper(),
        'status_badge': _STATUS_BADGE.get(status, 'andamento'),
        'usuario_nome': _nome_usuario(request.user),
        'coletor_id': _coletor_id(request),
        'area_setor': '—',
    }


def montar_operacional_ciclico(request, ciclo) -> dict:
    status_label = 'ATIVO'
    status_badge = 'aberto'
    if ciclo is not None:
        status_label = str(getattr(ciclo, 'get_status_ciclo_display', lambda: 'Ativo')()).upper()
        if getattr(ciclo, 'status_ciclo', '') not in ('ATIVO', ''):
            status_badge = 'andamento'
    return {
        'modo_label': 'CÍCLICO',
        'referencia_label': f'Ciclo #{ciclo.pk}' if ciclo else 'Ciclo',
        'status_label': status_label,
        'status_badge': status_badge,
        'usuario_nome': _nome_usuario(request.user),
        'coletor_id': _coletor_id(request),
        'area_setor': '—',
    }


def obter_metricas_inventario_geral(inventario: Inventario) -> dict:
    total = Posicao.objects.filter(ativo=True).count()
    contadas = (
        InventarioItem.objects.filter(
            inventario=inventario,
            quantidade_fisica__gt=0,
        )
        .values('posicao_id')
        .distinct()
        .count()
    )
    return {
        'total_posicoes': total,
        'contadas': contadas,
        'pendentes': max(total - contadas, 0),
    }


def obter_metricas_ciclico(painel) -> dict:
    resumo = painel.resumo
    return {
        'total_posicoes': resumo.total_lote,
        'contadas': resumo.contados,
        'pendentes': resumo.pendentes,
    }
