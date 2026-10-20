"""Libera os lotes vencidos das publicações ao auditor.

Uso:
  python manage.py processar_publicacoes_auditor            # uma passada (cron/timer)
  python manage.py processar_publicacoes_auditor --loop     # processo contínuo

Idempotente e seguro com mais de um processo: cada lote é reivindicado por
UPDATE condicional. Após indisponibilidade, libera somente o que venceu.
"""

import signal
import time

from django.core.management.base import BaseCommand

from inventario.services.auditor_publicacao import processar


class Command(BaseCommand):
    help = 'Disponibiliza ao auditor os lotes vencidos das publicações programadas.'

    def add_arguments(self, parser):
        parser.add_argument('--loop', action='store_true', help='Executa continuamente.')
        parser.add_argument('--intervalo', type=int, default=10, help='Segundos entre passadas (com --loop).')

    def handle(self, *args, **options):
        if not options['loop']:
            total = processar()
            self.stdout.write(f'{total} lançamento(s) disponibilizado(s).')
            return
        ativo = {'valor': True}

        def parar(*_):
            ativo['valor'] = False

        signal.signal(signal.SIGTERM, parar)
        signal.signal(signal.SIGINT, parar)
        intervalo = max(2, options['intervalo'])
        while ativo['valor']:
            try:
                total = processar()
                if total:
                    self.stdout.write(f'{total} lançamento(s) disponibilizado(s).')
            except Exception as exc:  # noqa: BLE001 — mantém o processo vivo
                self.stderr.write(f'Falha ao processar publicações: {type(exc).__name__}')
            for _ in range(intervalo):
                if not ativo['valor']:
                    break
                time.sleep(1)
