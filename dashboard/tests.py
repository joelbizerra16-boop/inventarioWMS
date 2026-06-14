from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Usuario
from accounts.test_utils import ClienteAutenticadoMixin, criar_usuario_teste
from dashboard.services.dashboard import obter_indicadores_dashboard
from estoque_fisico.models import EstoqueFisico
from estoque_sap.models import EstoqueSAP
from inventario.models import Inventario, InventarioItem
from posicoes.models import Posicao
from produtos.models import Produto


class DashboardServiceTestCase(TestCase):
    def test_dashboard_sem_dados(self):
        indicadores = obter_indicadores_dashboard()

        self.assertEqual(indicadores.total_produtos, 0)
        self.assertEqual(indicadores.total_posicoes, 0)
        self.assertEqual(indicadores.produtos_estoque_sap, 0)
        self.assertEqual(indicadores.produtos_estoque_fisico, 0)
        self.assertEqual(indicadores.inventarios_abertos, 0)
        self.assertEqual(indicadores.inventarios_em_andamento, 0)
        self.assertEqual(indicadores.inventarios_finalizados, 0)
        self.assertEqual(indicadores.produtos_corretos, 0)
        self.assertEqual(indicadores.produtos_divergentes, 0)
        self.assertEqual(indicadores.acuracidade, Decimal('0'))
        self.assertEqual(indicadores.ciclico_itens_planejados, 0)
        self.assertEqual(indicadores.ciclico_itens_contados, 0)
        self.assertEqual(indicadores.ciclico_percentual_concluido, Decimal('0'))

    def test_dashboard_com_dados(self):
        usuario = Usuario.objects.create(
            nome='Operador Dashboard',
            login='opdash',
            setor='Estoque',
            perfil=Usuario.Perfil.INVENTARIO,
        )
        produto_correto = Produto.objects.create(
            codigo_produto='D001',
            descricao='Produto Correto',
            setor='A',
            embalagem='Unidade',
        )
        produto_divergente = Produto.objects.create(
            codigo_produto='D002',
            descricao='Produto Divergente',
            setor='A',
            embalagem='Unidade',
        )
        posicao = Posicao.objects.create(codigo='DP01', posicao='F-01')

        Inventario.objects.create(
            usuario=usuario,
            status=Inventario.Status.ABERTO,
        )
        Inventario.objects.create(
            usuario=usuario,
            status=Inventario.Status.EM_ANDAMENTO,
        )
        inventario_finalizado = Inventario.objects.create(
            usuario=usuario,
            status=Inventario.Status.FINALIZADO,
        )

        InventarioItem.objects.create(
            inventario=inventario_finalizado,
            posicao=posicao,
            produto=produto_correto,
            quantidade_fisica=Decimal('10'),
        )
        InventarioItem.objects.create(
            inventario=inventario_finalizado,
            posicao=posicao,
            produto=produto_divergente,
            quantidade_fisica=Decimal('15'),
        )

        EstoqueSAP.objects.create(
            produto=produto_correto,
            total=Decimal('10'),
            arquivo_origem='teste.xlsx',
        )
        EstoqueSAP.objects.create(
            produto=produto_divergente,
            total=Decimal('10'),
            arquivo_origem='teste.xlsx',
        )

        EstoqueFisico.objects.create(
            posicao=posicao,
            produto=produto_correto,
            quantidade=Decimal('10'),
            data_contagem=timezone.now(),
        )

        indicadores = obter_indicadores_dashboard()

        self.assertEqual(indicadores.total_produtos, 2)
        self.assertEqual(indicadores.total_posicoes, 1)
        self.assertEqual(indicadores.produtos_estoque_sap, 2)
        self.assertEqual(indicadores.produtos_estoque_fisico, 1)
        self.assertEqual(indicadores.inventarios_abertos, 1)
        self.assertEqual(indicadores.inventarios_em_andamento, 1)
        self.assertEqual(indicadores.inventarios_finalizados, 1)
        self.assertEqual(indicadores.produtos_corretos, 1)
        self.assertEqual(indicadores.produtos_divergentes, 1)
        self.assertEqual(indicadores.acuracidade, Decimal('50.00'))
        self.assertEqual(indicadores.grafico_inventarios_valores, [1, 1, 1])
        self.assertEqual(len(indicadores.graficos_geral), 4)
        self.assertEqual(len(indicadores.graficos_ciclico), 5)
        planejado = next(g for g in indicadores.graficos_geral if g.id == 'planejado_contado')
        self.assertEqual(planejado.valores[1], 1)

    def test_acuracidade_100_por_cento(self):
        usuario = Usuario.objects.create(
            nome='Operador Acuracidade',
            login='opacu',
            setor='Estoque',
            perfil=Usuario.Perfil.INVENTARIO,
        )
        produto = Produto.objects.create(
            codigo_produto='D100',
            descricao='Produto 100',
            setor='A',
            embalagem='Unidade',
        )
        posicao = Posicao.objects.create(codigo='DP100', posicao='G-01')
        inventario = Inventario.objects.create(
            usuario=usuario,
            status=Inventario.Status.FINALIZADO,
        )
        InventarioItem.objects.create(
            inventario=inventario,
            posicao=posicao,
            produto=produto,
            quantidade_fisica=Decimal('25'),
        )
        EstoqueSAP.objects.create(
            produto=produto,
            total=Decimal('25'),
            arquivo_origem='teste.xlsx',
        )

        indicadores = obter_indicadores_dashboard()

        self.assertEqual(indicadores.produtos_corretos, 1)
        self.assertEqual(indicadores.produtos_divergentes, 0)
        self.assertEqual(indicadores.acuracidade, Decimal('100.00'))


class DashboardViewTestCase(ClienteAutenticadoMixin, TestCase):
    def setUp(self):
        self.autenticar_cliente()

    def test_home_exibe_dashboard(self):
        response = self.client.get(reverse('home'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Dashboard Operacional')
        self.assertContains(response, 'Acuracidade Geral')
        self.assertContains(response, 'chart-status_inventarios')
        self.assertContains(response, 'visaoGraficos')
        self.assertContains(response, 'Inventário Cíclico')
        self.assertContains(response, 'Itens Planejados')
        self.assertContains(response, 'dashboard.css')
        self.assertContains(response, 'id="dashboard-graficos-data"')
        self.assertContains(response, '"status_inventarios"')
        self.assertContains(response, 'data-chart-wrapper="status_ciclos"')


class DashboardCiclicoTestCase(TestCase):
    def setUp(self):
        from accounts.test_utils import criar_usuario_teste
        from inventario.models import CicloInventarioItem
        from inventario.services.ciclico import (
            criar_ciclo,
            limpar_estado_ciclico,
            registrar_contagem,
        )

        limpar_estado_ciclico()

        produto = Produto.objects.create(
            codigo_produto='DC01',
            descricao='Dashboard Cíclico',
            setor='A',
            embalagem='Unidade',
        )
        posicao = Posicao.objects.create(codigo='DC01', posicao='J-01')
        EstoqueSAP.objects.create(
            produto=produto,
            total=Decimal('8'),
            arquivo_origem='teste.xlsx',
        )
        EstoqueFisico.objects.create(
            posicao=posicao,
            produto=produto,
            quantidade=Decimal('8'),
            data_contagem=timezone.now(),
        )
        criar_ciclo()
        user, _ = criar_usuario_teste(perfil=Usuario.Perfil.INVENTARIO)
        item = CicloInventarioItem.objects.get()
        registrar_contagem(item.pk, Decimal('8'), user)

    def tearDown(self):
        from inventario.services.ciclico import limpar_estado_ciclico
        limpar_estado_ciclico()

    def test_dashboard_exibe_indicadores_ciclico(self):
        indicadores = obter_indicadores_dashboard()

        self.assertEqual(indicadores.ciclico_itens_planejados, 1)
        self.assertEqual(indicadores.ciclico_itens_contados, 1)
        self.assertEqual(indicadores.ciclico_percentual_concluido, Decimal('100.00'))


class CicloOperacionalHomologacaoTestCase(TestCase):
    def setUp(self):
        from inventario.services.aprovacao import limpar_estado_aprovacao
        from inventario.services.ciclico import limpar_estado_ciclico
        from inventario.services.consolidacao import limpar_estado_consolidacao

        limpar_estado_aprovacao()
        limpar_estado_consolidacao()
        limpar_estado_ciclico()

    def tearDown(self):
        from inventario.services.aprovacao import limpar_estado_aprovacao
        from inventario.services.ciclico import limpar_estado_ciclico
        from inventario.services.consolidacao import limpar_estado_consolidacao

        limpar_estado_aprovacao()
        limpar_estado_consolidacao()
        limpar_estado_ciclico()

    def test_ciclo_completo_operacional(self):
        from inventario.services.aprovacao import consultar_aprovacao, aprovar_inventario
        from inventario.services.ciclico import criar_ciclo
        from inventario.services.consolidacao import consolidar_estoque_fisico
        from inventario.services.confronto import executar_confronto

        usuario = Usuario.objects.create(
            nome='Piloto',
            login='piloto',
            setor='Estoque',
            perfil=Usuario.Perfil.INVENTARIO,
        )
        produto = Produto.objects.create(
            codigo_produto='PIL001',
            descricao='Produto Piloto',
            setor='A',
            embalagem='Unidade',
        )
        posicao = Posicao.objects.create(codigo='PIL01', posicao='P-01')

        EstoqueSAP.objects.create(
            produto=produto,
            total=Decimal('50'),
            arquivo_origem='piloto.xlsx',
        )
        inventario = Inventario.objects.create(
            usuario=usuario,
            status=Inventario.Status.ABERTO,
        )
        InventarioItem.objects.create(
            inventario=inventario,
            posicao=posicao,
            produto=produto,
            quantidade_fisica=Decimal('50'),
        )
        inventario.status = Inventario.Status.FINALIZADO
        inventario.save(update_fields=['status'])

        confronto = executar_confronto(inventario.pk)
        self.assertEqual(confronto.resumo.produtos_corretos, 1)

        consultar_aprovacao(inventario.pk)
        aprovar_inventario(inventario)
        consolidar_estoque_fisico(inventario)

        self.assertTrue(
            EstoqueFisico.objects.filter(produto=produto, posicao=posicao).exists(),
        )
        self.assertGreaterEqual(obter_indicadores_dashboard().total_produtos, 1)

        ciclo = criar_ciclo()
        self.assertEqual(ciclo.skus.count(), 1)


class SegurancaHomologacaoTestCase(TestCase):
    def test_csrf_protege_post(self):
        from django.test import Client

        user, _ = criar_usuario_teste()
        client = Client(enforce_csrf_checks=True)
        client.force_login(user)
        response = client.post(reverse('inventario:criar'), {})
        self.assertEqual(response.status_code, 403)

    def test_rotas_operacionais_exigem_login(self):
        response = self.client.get(reverse('home'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('login', response.url)

    def test_admin_exige_autenticacao(self):
        response = self.client.get(reverse('admin:index'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/admin/login/', response.url)

    def test_sessao_disponivel(self):
        session = self.client.session
        session['homologacao'] = True
        session.save()
        self.assertTrue(self.client.session.get('homologacao'))


class PerformanceHomologacaoTestCase(ClienteAutenticadoMixin, TestCase):
    def setUp(self):
        self.autenticar_cliente()

    def test_dashboard_responde_rapidamente(self):
        import time

        inicio = time.perf_counter()
        obter_indicadores_dashboard()
        self.assertLess(time.perf_counter() - inicio, 2.0)

    def test_listagens_respondem(self):
        for url in (
            reverse('home'),
            reverse('produtos:lista'),
            reverse('inventario:lista'),
            reverse('confronto'),
        ):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
