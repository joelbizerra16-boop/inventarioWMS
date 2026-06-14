from django.core.management.base import BaseCommand

from inventario.services.locks import expirar_locks_abandonados


class Command(BaseCommand):
    help = 'Libera locks de inventário expirados por timeout (conexão perdida, sessão abandonada).'

    def handle(self, *args, **options):
        quantidade = expirar_locks_abandonados()
        self.stdout.write(
            self.style.SUCCESS(f'{quantidade} lock(s) liberado(s) por timeout.')
        )
