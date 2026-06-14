from accounts.models import Usuario


def obter_perfil_usuario(user) -> str | None:
    if not user.is_authenticated:
        return None
    if user.is_superuser:
        return Usuario.Perfil.ADMINISTRADOR
    perfil = getattr(user, 'perfil_operacional', None)
    if perfil is None:
        return None
    return perfil.perfil


def obter_usuario_operacional(user) -> Usuario | None:
    if not user.is_authenticated:
        return None
    return getattr(user, 'perfil_operacional', None)


def usuario_pode_escrever_cadastros(user) -> bool:
    return obter_perfil_usuario(user) == Usuario.Perfil.ADMINISTRADOR


def usuario_pode_escrever_inventario(user) -> bool:
    return obter_perfil_usuario(user) in (
        Usuario.Perfil.ADMINISTRADOR,
        Usuario.Perfil.INVENTARIO,
    )


def usuario_pode_executar_pocket(user) -> bool:
    return obter_perfil_usuario(user) in (
        Usuario.Perfil.ADMINISTRADOR,
        Usuario.Perfil.INVENTARIO,
        Usuario.Perfil.OPERADOR,
    )


def usuario_pode_escrever(user) -> bool:
    return usuario_pode_escrever_inventario(user)


def usuario_pode_acessar(user) -> bool:
    return obter_perfil_usuario(user) in (
        Usuario.Perfil.ADMINISTRADOR,
        Usuario.Perfil.SUPERVISOR,
        Usuario.Perfil.INVENTARIO,
        Usuario.Perfil.OPERADOR,
        Usuario.Perfil.CONSULTA,
    )


def usuario_pode_supervisionar_ciclico(user) -> bool:
    """Aceitar divergência no Pocket Cíclico — supervisor ou administrador."""
    return obter_perfil_usuario(user) in (
        Usuario.Perfil.ADMINISTRADOR,
        Usuario.Perfil.SUPERVISOR,
    )


def usuario_e_operador_pocket(user) -> bool:
    return obter_perfil_usuario(user) == Usuario.Perfil.OPERADOR


PREFIXOS_URL_PERMITIDOS_OPERADOR = (
    '/pocket/',
    '/accounts/logout/',
    '/accounts/login/',
    '/static/',
    '/admin/logout/',
)


def url_permitida_para_operador(path: str) -> bool:
    if not path:
        return False
    if path == '/favicon.ico':
        return True
    return any(path.startswith(prefixo) for prefixo in PREFIXOS_URL_PERMITIDOS_OPERADOR)
