from django.contrib.auth.models import User
from django.db import transaction
from django.db.models import Q, QuerySet

from accounts.models import Usuario


class UsuarioServiceError(Exception):
    pass


def obter_resumo_usuarios() -> dict[str, int]:
    queryset = Usuario.objects.all()
    perfis_operador = (
        Usuario.Perfil.OPERADOR,
        Usuario.Perfil.INVENTARIO,
    )
    return {
        'ativos': queryset.filter(ativo=True).count(),
        'inativos': queryset.filter(ativo=False).count(),
        'administradores': queryset.filter(
            perfil=Usuario.Perfil.ADMINISTRADOR,
            ativo=True,
        ).count(),
        'operadores': queryset.filter(
            perfil__in=perfis_operador,
            ativo=True,
        ).count(),
        'supervisores': queryset.filter(
            perfil=Usuario.Perfil.SUPERVISOR,
            ativo=True,
        ).count(),
    }


def filtrar_usuarios(
    queryset: QuerySet[Usuario] | None = None,
    *,
    nome: str = '',
    login: str = '',
    perfil: str = '',
    status: str = '',
) -> QuerySet[Usuario]:
    if queryset is None:
        queryset = Usuario.objects.all()

    if nome:
        queryset = queryset.filter(nome__icontains=nome.strip())
    if login:
        queryset = queryset.filter(login__icontains=login.strip())
    if perfil:
        queryset = queryset.filter(perfil=perfil)
    if status == 'ativo':
        queryset = queryset.filter(ativo=True)
    elif status == 'inativo':
        queryset = queryset.filter(ativo=False)

    return queryset.select_related('user').order_by('nome')


def _sincronizar_auth_user(
    user: User,
    *,
    login: str,
    perfil: str,
    ativo: bool,
    senha: str | None = None,
) -> User:
    user.username = login
    user.is_active = ativo
    user.is_staff = perfil == Usuario.Perfil.ADMINISTRADOR
    user.is_superuser = perfil == Usuario.Perfil.ADMINISTRADOR
    if senha:
        user.set_password(senha)
    user.save()
    return user


def _validar_login_unico(login: str, usuario_id: int | None = None) -> None:
    login = login.strip()
    if Usuario.objects.filter(login__iexact=login).exclude(pk=usuario_id).exists():
        raise UsuarioServiceError('Já existe um usuário com este login.')
    usuarios_auth = User.objects.filter(username__iexact=login)
    if usuario_id:
        usuarios_auth = usuarios_auth.exclude(perfil_operacional__pk=usuario_id)
    if usuarios_auth.exists():
        raise UsuarioServiceError('Já existe um usuário de autenticação com este login.')


@transaction.atomic
def criar_usuario_operacional(
    *,
    nome: str,
    login: str,
    setor: str,
    perfil: str,
    ativo: bool,
    senha: str,
) -> Usuario:
    _validar_login_unico(login)
    user = User.objects.create_user(
        username=login.strip(),
        password=senha,
    )
    _sincronizar_auth_user(user, login=login.strip(), perfil=perfil, ativo=ativo)
    return Usuario.objects.create(
        nome=nome.strip(),
        login=login.strip(),
        setor=setor.strip(),
        perfil=perfil,
        ativo=ativo,
        user=user,
    )


@transaction.atomic
def atualizar_usuario_operacional(
    usuario: Usuario,
    *,
    nome: str,
    login: str,
    setor: str,
    perfil: str,
    ativo: bool,
    senha: str | None = None,
) -> Usuario:
    _validar_login_unico(login, usuario.pk)
    usuario.nome = nome.strip()
    usuario.login = login.strip()
    usuario.setor = setor.strip()
    usuario.perfil = perfil
    usuario.ativo = ativo
    usuario.save()

    if usuario.user_id:
        _sincronizar_auth_user(
            usuario.user,
            login=login.strip(),
            perfil=perfil,
            ativo=ativo,
            senha=senha,
        )
    else:
        user = User.objects.create_user(username=login.strip(), password=senha or login.strip())
        _sincronizar_auth_user(
            user,
            login=login.strip(),
            perfil=perfil,
            ativo=ativo,
            senha=senha,
        )
        usuario.user = user
        usuario.save(update_fields=['user'])

    return usuario


@transaction.atomic
def alternar_status_usuario(usuario: Usuario) -> Usuario:
    usuario.ativo = not usuario.ativo
    usuario.save(update_fields=['ativo'])
    if usuario.user_id:
        usuario.user.is_active = usuario.ativo
        usuario.user.save(update_fields=['is_active'])
    return usuario


@transaction.atomic
def excluir_usuario_operacional(usuario: Usuario) -> None:
    user = usuario.user
    usuario.delete()
    if user is not None:
        user.delete()
