"""Auditoria de diagnóstico da gravação Pocket (inventário geral)."""

from core.logging_auditoria import ip_do_request, registrar_evento


def _usuario_ref(request):
    user = getattr(request, 'user', None)
    if user is None or not getattr(user, 'is_authenticated', False):
        return None
    return user


def log_pocket_gravacao(request, etapa: str, **contexto) -> None:
    """Registra etapa da cadeia Posição → Produto → Quantidade → Salvar."""
    registrar_evento(
        f'pocket_gravacao_{etapa}',
        usuario=_usuario_ref(request),
        ip=ip_do_request(request),
        **contexto,
    )


def log_pocket_gravacao_erro(request, etapa: str, erro: str, **contexto) -> None:
    registrar_evento(
        f'pocket_gravacao_erro_{etapa}',
        usuario=_usuario_ref(request),
        ip=ip_do_request(request),
        erro=erro,
        **contexto,
    )
