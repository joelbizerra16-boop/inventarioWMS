from django.core.management.base import BaseCommand

from accounts.models import Usuario
from accounts.test_utils import criar_usuario_teste


class Command(BaseCommand):
    help = 'Cria usuários demo para os três perfis operacionais (admin, inventário, consulta).'

    def handle(self, *args, **options):
        perfis = (
            ('admin.demo', Usuario.Perfil.ADMINISTRADOR, 'Administrador Demo'),
            ('inventario.demo', Usuario.Perfil.INVENTARIO, 'Inventário Demo'),
            ('consulta.demo', Usuario.Perfil.CONSULTA, 'Consulta Demo'),
        )
        senha = 'Demo@2026!'

        for username, perfil, nome in perfis:
            user, operacional = criar_usuario_teste(
                username=username,
                password=senha,
                perfil=perfil,
            )
            operacional.nome = nome
            operacional.save(update_fields=['nome'])
            self.stdout.write(
                self.style.SUCCESS(
                    f'Perfil {perfil}: login={username} / senha={senha}',
                ),
            )
