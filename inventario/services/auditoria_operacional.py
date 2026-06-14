"""Auditoria operacional unificada para inventário multiusuário."""

from decimal import Decimal

from django.utils import timezone

from inventario.models_operacional import InventarioAuditoriaEvento
from core.logging_auditoria import registrar_evento


def registrar_evento_operacional(
    *,
    evento: str,
    tipo_inventario: str,
    usuario=None,
    dispositivo: str = '',
    ip: str | None = None,
    inventario=None,
    ciclo=None,
    tarefa=None,
    lock=None,
    posicao=None,
    produto=None,
    lote: str = '',
    quantidade=None,
    status_anterior: str = '',
    status_novo: str = '',
    dados_extras: dict | None = None,
) -> InventarioAuditoriaEvento:
    registro = InventarioAuditoriaEvento.objects.create(
        tipo_inventario=tipo_inventario,
        inventario=inventario,
        ciclo=ciclo,
        tarefa=tarefa,
        lock=lock,
        evento=evento,
        usuario=usuario,
        dispositivo=dispositivo[:200] if dispositivo else '',
        ip=ip,
        posicao=posicao,
        produto=produto,
        lote=lote,
        quantidade=Decimal(str(quantidade)) if quantidade is not None else None,
        status_anterior=status_anterior,
        status_novo=status_novo,
        dados_extras=dados_extras or {},
        data_hora=timezone.now(),
    )

    contexto = {
        'tipo_inventario': tipo_inventario,
        'evento_operacional': evento,
        'inventario_id': getattr(inventario, 'pk', inventario),
        'ciclo_id': getattr(ciclo, 'pk', ciclo),
        'tarefa_id': getattr(tarefa, 'pk', tarefa),
        'lock_id': getattr(lock, 'pk', lock),
        'posicao_id': getattr(posicao, 'pk', posicao),
        'produto_id': getattr(produto, 'pk', produto),
        'status_anterior': status_anterior,
        'status_novo': status_novo,
        'dispositivo': dispositivo[:80] if dispositivo else '',
    }
    if quantidade is not None:
        contexto['quantidade'] = str(quantidade)

    registrar_evento(
        f'inventario_operacional_{evento.lower()}',
        usuario=usuario,
        ip=ip,
        **{k: v for k, v in contexto.items() if v is not None and v != ''},
    )
    return registro
