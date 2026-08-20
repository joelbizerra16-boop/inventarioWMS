import os

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand

from accounts.models import Usuario


class Command(BaseCommand):
    help = (
        'Garante a existência de um usuário administrador operacional, lendo '
        'credenciais das variáveis de ambiente ADMIN_USERNAME, ADMIN_EMAIL e '
        'ADMIN_PASSWORD. Não faz nada se ADMIN_USERNAME ou ADMIN_PASSWORD '
        'não estiverem definidas (idempotente, seguro para rodar a cada deploy).'
    )

    def handle(self, *args, **options):
        username = os.environ.get('ADMIN_USERNAME')
        password = os.environ.get('ADMIN_PASSWORD')
        email = os.environ.get('ADMIN_EMAIL', '')

        if not username or not password:
            self.stdout.write('ADMIN_USERNAME/ADMIN_PASSWORD não definidas — nada a fazer.')
            return

        user, criado = User.objects.get_or_create(
            username=username,
            defaults={'email': email},
        )
        user.email = email or user.email
        user.set_password(password)
        user.is_staff = True
        user.is_superuser = True
        user.is_active = True
        user.save()

        operacional, _ = Usuario.objects.get_or_create(
            login=username,
            defaults={
                'nome': username,
                'setor': 'Administração',
                'perfil': Usuario.Perfil.ADMINISTRADOR,
                'user': user,
            },
        )
        if operacional.user_id != user.pk or operacional.perfil != Usuario.Perfil.ADMINISTRADOR:
            operacional.user = user
            operacional.perfil = Usuario.Perfil.ADMINISTRADOR
            operacional.ativo = True
            operacional.save(update_fields=['user', 'perfil', 'ativo'])

        acao = 'criado' if criado else 'atualizado'
        self.stdout.write(self.style.SUCCESS(f'Admin {username} {acao} com sucesso.'))
