from django.contrib.auth.models import User
from django.test import TestCase

from accounts.models import Usuario


def criar_usuario_teste(
    username: str = 'admin.teste',
    password: str = 'senha12345',
    perfil: str = Usuario.Perfil.ADMINISTRADOR,
) -> tuple[User, Usuario]:
    user, _ = User.objects.get_or_create(
        username=username,
        defaults={'email': f'{username}@teste.local'},
    )
    user.set_password(password)
    user.is_staff = perfil == Usuario.Perfil.ADMINISTRADOR
    user.is_superuser = perfil == Usuario.Perfil.ADMINISTRADOR
    user.save()

    operacional, _ = Usuario.objects.get_or_create(
        login=username,
        defaults={
            'nome': f'Usuário {username}',
            'setor': 'Estoque',
            'perfil': perfil,
            'user': user,
        },
    )
    if operacional.user_id != user.pk:
        operacional.user = user
        operacional.perfil = perfil
        operacional.save(update_fields=['user', 'perfil'])

    return user, operacional


class ClienteAutenticadoMixin:
    """Autentica client de teste com perfil ADMINISTRADOR."""

    def autenticar_cliente(self, perfil: str = Usuario.Perfil.ADMINISTRADOR):
        user, _ = criar_usuario_teste(perfil=perfil)
        self.client.force_login(user)
        return user

