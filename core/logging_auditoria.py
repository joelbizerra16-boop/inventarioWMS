import logging
from datetime import datetime

from django.utils import timezone

logger = logging.getLogger('inventario.auditoria')


def ip_do_request(request) -> str:
    encaminhado = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if encaminhado:
        return encaminhado.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', '') or ''


def registrar_evento(evento: str, usuario=None, ip: str | None = None, **contexto) -> None:
    usuario_ref = 'anonimo'
    if usuario is not None:
        if hasattr(usuario, 'get_username'):
            usuario_ref = usuario.get_username() or str(getattr(usuario, 'pk', usuario))
        else:
            usuario_ref = str(usuario)

    agora: datetime = timezone.localtime(timezone.now())
    partes = [
        f'evento={evento}',
        f'usuario={usuario_ref}',
        f'data={agora:%Y-%m-%d}',
        f'hora={agora:%H:%M:%S}',
    ]
    if ip:
        partes.append(f'ip={ip}')

    partes_contexto = ' '.join(
        f'{chave}={valor}'
        for chave, valor in contexto.items()
        if valor is not None and valor != ''
    )
    if partes_contexto:
        partes.append(partes_contexto)

    logger.info(' '.join(partes))
