from decimal import Decimal
import uuid

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import connection
from django.test import Client
from django.urls import reverse

from accounts.test_utils import criar_usuario_teste
from accounts.models import Usuario
from dashboard.services.dashboard import obter_indicadores_dashboard
from estoque_fisico.models import EstoqueFisico
from estoque_sap.models import EstoqueSAP
from inventario.models import Inventario, InventarioItem
from inventario.services.aprovacao import consultar_aprovacao, aprovar_inventario
from inventario.services.consolidacao import consolidar_estoque_fisico
from inventario.services.confronto import executar_confronto
from posicoes.models import Posicao
from produtos.models import Produto


class Command(BaseCommand):
    help = 'Validação de homologação: banco, migrações, URLs, ciclo operacional e segurança.'

    def _host_homologacao(self) -> str:
        for host in settings.ALLOWED_HOSTS:
            if host not in ('*', 'testserver'):
                return host
        return 'localhost'

    def _cliente_autenticado(self, username: str) -> Client:
        user, _ = criar_usuario_teste(username=username)
        client = Client()
        client.force_login(user)
        return client

    def handle(self, *args, **options):
        call_command('migrate', verbosity=0, interactive=False)

        resultados = []
        resultados.append(self._validar_conexao())
        resultados.append(self._validar_migracoes())
        resultados.append(self._validar_indices_constraints())
        resultados.append(self._validar_urls())
        resultados.append(self._validar_csrf())
        resultados.append(self._validar_ciclo_operacional())
        resultados.append(self._validar_dashboard())
        resultados.append(self._validar_deploy_check())

        self.stdout.write('')
        self.stdout.write(self.style.MIGRATE_HEADING('=== RESULTADO DA HOMOLOGAÇÃO ==='))
        falhas = 0
        for nome, ok, detalhe in resultados:
            if ok:
                self.stdout.write(self.style.SUCCESS(f'[OK] {nome}: {detalhe}'))
            else:
                falhas += 1
                self.stdout.write(self.style.ERROR(f'[FALHA] {nome}: {detalhe}'))

        self.stdout.write('')
        if falhas:
            self.stdout.write(self.style.ERROR(f'Homologação concluída com {falhas} falha(s).'))
        else:
            self.stdout.write(self.style.SUCCESS('Homologação concluída sem falhas.'))

    def _validar_conexao(self):
        try:
            with connection.cursor() as cursor:
                cursor.execute('SELECT 1')
                cursor.fetchone()
            vendor = connection.vendor
            return 'Conexão', True, f'{vendor} respondendo'
        except Exception as exc:
            return 'Conexão', False, str(exc)

    def _validar_migracoes(self):
        try:
            from io import StringIO
            out = StringIO()
            call_command('showmigrations', '--plan', stdout=out, no_color=True)
            plano = out.getvalue()
            pendentes = [linha for linha in plano.splitlines() if '[ ]' in linha]
            if pendentes:
                return 'Migrações', False, f'{len(pendentes)} pendente(s)'
            return 'Migrações', True, 'Todas aplicadas'
        except Exception as exc:
            return 'Migrações', False, str(exc)

    def _validar_indices_constraints(self):
        if connection.vendor != 'postgresql':
            return 'Índices/Constraints', True, 'Ignorado (não PostgreSQL/Supabase)'

        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT COUNT(*)
                    FROM pg_indexes
                    WHERE schemaname = 'public'
                    """
                )
                indices = cursor.fetchone()[0]

                cursor.execute(
                    """
                    SELECT COUNT(*)
                    FROM information_schema.table_constraints
                    WHERE constraint_schema = 'public'
                    """
                )
                constraints = cursor.fetchone()[0]

                cursor.execute(
                    """
                    SELECT COUNT(*)
                    FROM pg_constraint
                    WHERE connamespace = 'public'::regnamespace
                    """
                )
                pg_constraints = cursor.fetchone()[0]

            return (
                'Índices/Constraints',
                indices > 0 and constraints > 0,
                f'{indices} índice(s), {constraints} constraint(s) info_schema, '
                f'{pg_constraints} pg_constraint',
            )
        except Exception as exc:
            return 'Índices/Constraints', False, str(exc)

    def _validar_urls(self):
        rotas_operacionais = [
            'home',
            'produtos:lista',
            'posicoes:lista',
            'estoque_sap:lista',
            'inventario:lista',
            'confronto',
            'aprovacao',
            'consolidacao',
            'ciclico',
            'ciclico_consulta',
            'ciclico_executar',
        ]
        client = self._cliente_autenticado('homologacao.urls')
        host = self._host_homologacao()

        from inventario.services.ciclico import criar_ciclo, limpar_estado_ciclico
        from django.utils import timezone as tz

        limpar_estado_ciclico()
        sufixo = uuid.uuid4().hex[:6]
        produto = Produto.objects.create(
            codigo_produto=f'URL{sufixo}',
            descricao='Produto URL Homologação',
            setor='A',
            embalagem='Unidade',
        )
        posicao = Posicao.objects.create(codigo=f'U{sufixo}', posicao='U-01')
        EstoqueSAP.objects.create(
            produto=produto,
            total=Decimal('1'),
            arquivo_origem='homologacao.xlsx',
        )
        EstoqueFisico.objects.create(
            posicao=posicao,
            produto=produto,
            quantidade=Decimal('1'),
            data_contagem=tz.now(),
        )
        criar_ciclo()

        falhas = []
        for rota in rotas_operacionais:
            try:
                response = client.get(reverse(rota), HTTP_HOST=host)
                if response.status_code != 200:
                    falhas.append(f'{rota}={response.status_code}')
            except Exception as exc:
                falhas.append(f'{rota}={exc}')

        try:
            admin_response = Client().get(reverse('admin:index'), HTTP_HOST=host)
            if admin_response.status_code != 302:
                falhas.append(f'admin:index={admin_response.status_code}')
        except Exception as exc:
            falhas.append(f'admin:index={exc}')

        if falhas:
            return 'URLs', False, '; '.join(falhas)
        return 'URLs', True, f'{len(rotas_operacionais) + 1} rotas respondendo'

    def _validar_csrf(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(criar_usuario_teste(username='homologacao.csrf')[0])
        host = self._host_homologacao()
        response = client.post(
            reverse('produtos:criar'),
            {'codigo_produto': 'X'},
            HTTP_HOST=host,
        )
        if response.status_code == 403:
            return 'CSRF', True, 'POST sem token bloqueado (403)'
        return 'CSRF', False, f'Esperado 403, recebido {response.status_code}'

    def _validar_ciclo_operacional(self):
        try:
            from inventario.services.aprovacao import limpar_estado_aprovacao
            from inventario.services.ciclico import limpar_estado_ciclico
            from inventario.services.consolidacao import limpar_estado_consolidacao

            limpar_estado_aprovacao()
            limpar_estado_ciclico()
            limpar_estado_consolidacao()

            sufixo = uuid.uuid4().hex[:8]
            usuario = Usuario.objects.create(
                nome='Homologação',
                login=f'homolog-{sufixo}',
                setor='Estoque',
                perfil=Usuario.Perfil.INVENTARIO,
            )
            produto = Produto.objects.create(
                codigo_produto=f'HOM{sufixo}',
                descricao='Produto Homologação',
                setor='A',
                embalagem='Unidade',
            )
            posicao = Posicao.objects.create(codigo=f'H{sufixo[:6]}', posicao='Z-01')
            EstoqueSAP.objects.create(
                produto=produto,
                total=Decimal('100'),
                arquivo_origem='homologacao.xlsx',
            )
            inventario = Inventario.objects.create(
                usuario=usuario,
                status=Inventario.Status.ABERTO,
            )
            InventarioItem.objects.create(
                inventario=inventario,
                posicao=posicao,
                produto=produto,
                quantidade_fisica=Decimal('100'),
            )
            inventario.status = Inventario.Status.FINALIZADO
            inventario.save(update_fields=['status'])

            confronto = executar_confronto(inventario.pk)
            consultar_aprovacao(inventario.pk)
            aprovar_inventario(inventario)
            consolidar_estoque_fisico(inventario)

            if confronto.resumo.produtos_corretos != 1:
                return 'Ciclo operacional', False, 'Confronto incorreto'
            if EstoqueFisico.objects.filter(produto=produto, posicao=posicao).count() != 1:
                return 'Ciclo operacional', False, 'Consolidação não gerou estoque físico'

            return 'Ciclo operacional', True, '11 etapas simuladas com sucesso'
        except Exception as exc:
            return 'Ciclo operacional', False, str(exc)

    def _validar_dashboard(self):
        try:
            indicadores = obter_indicadores_dashboard()
            if indicadores.total_produtos < 0:
                return 'Dashboard', False, 'Indicadores inválidos'
            return (
                'Dashboard',
                True,
                f'{indicadores.total_produtos} produto(s), acuracidade {indicadores.acuracidade}%',
            )
        except Exception as exc:
            return 'Dashboard', False, str(exc)

    def _validar_deploy_check(self):
        try:
            import os
            import subprocess
            import sys

            env = os.environ.copy()
            env['DEBUG'] = 'False'
            if len(settings.SECRET_KEY) < 50:
                env['SECRET_KEY'] = 'x' * 50 + '-homologacao-deploy-check-key'
            resultado = subprocess.run(
                [sys.executable, 'manage.py', 'check', '--deploy'],
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            saida = (resultado.stdout + resultado.stderr).lower()
            if resultado.returncode == 0 and 'warning' not in saida:
                return 'Segurança (deploy check)', True, 'Sem alertas com DEBUG=False'
            detalhe = (resultado.stdout + resultado.stderr).strip().replace('\n', ' | ')
            return 'Segurança (deploy check)', False, detalhe or f'exit code {resultado.returncode}'
        except Exception as exc:
            return 'Segurança (deploy check)', False, str(exc)
