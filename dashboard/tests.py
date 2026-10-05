from datetime import timedelta
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


class DashboardEvolucaoAcuracidadeTestCase(TestCase):
    def setUp(self):
        self.usuario = Usuario.objects.create(
            nome='Operador Evolucao', login='opevo', setor='Estoque',
            perfil=Usuario.Perfil.INVENTARIO,
        )

    def _criar_finalizado(self, taxa, quantidade_produtos=1, dias_atras=0):
        inventario = Inventario.objects.create(
            usuario=self.usuario,
            status=Inventario.Status.FINALIZADO,
        )
        Inventario.objects.filter(pk=inventario.pk).update(
            data_finalizacao=timezone.now() - timedelta(days=dias_atras),
            taxa_acuracidade=taxa,
            quantidade_produtos=quantidade_produtos,
        )
        return inventario

    def test_evolucao_ordena_do_mais_antigo_para_o_mais_recente(self):
        self._criar_finalizado(Decimal('80.00'), dias_atras=5)
        self._criar_finalizado(Decimal('95.00'), dias_atras=1)

        indicadores = obter_indicadores_dashboard()
        grafico = next(g for g in indicadores.graficos_geral if g.id == 'evolucao_acuracidade')

        self.assertEqual(grafico.valores, [80.0, 95.0])
        self.assertFalse(grafico.mensagem_vazia)

    def test_evolucao_ignora_inventario_sem_acuracidade_valida(self):
        self._criar_finalizado(Decimal('90.00'))
        inventario_sem_snapshot = Inventario.objects.create(
            usuario=self.usuario,
            status=Inventario.Status.FINALIZADO,
        )
        self.assertIsNone(inventario_sem_snapshot.taxa_acuracidade)

        indicadores = obter_indicadores_dashboard()
        grafico = next(g for g in indicadores.graficos_geral if g.id == 'evolucao_acuracidade')

        self.assertEqual(grafico.valores, [90.0])

    def test_evolucao_ignora_inventario_com_zero_produtos(self):
        self._criar_finalizado(Decimal('0.00'), quantidade_produtos=0)

        indicadores = obter_indicadores_dashboard()
        grafico = next(g for g in indicadores.graficos_geral if g.id == 'evolucao_acuracidade')

        self.assertEqual(grafico.valores, [])
        self.assertEqual(grafico.labels, [])
        self.assertEqual(grafico.mensagem_vazia, 'Nenhum inventário finalizado para exibir evolução.')


class DashboardPlanejadoContadoTestCase(TestCase):
    def setUp(self):
        self.usuario = Usuario.objects.create(
            nome='Operador Planejado', login='opplan', setor='Estoque',
            perfil=Usuario.Perfil.INVENTARIO,
        )
        self.posicao = Posicao.objects.create(codigo='PLJ01', posicao='PL-01')

    def test_sem_inventario_em_andamento(self):
        indicadores = obter_indicadores_dashboard()
        grafico = next(g for g in indicadores.graficos_geral if g.id == 'planejado_contado')

        self.assertEqual(grafico.valores, [])
        self.assertEqual(grafico.mensagem_vazia, 'Nenhum inventário em andamento no momento.')

    def test_planejado_contado_pendente_do_inventario_em_andamento(self):
        inventario = Inventario.objects.create(
            usuario=self.usuario,
            status=Inventario.Status.EM_ANDAMENTO,
        )
        contado = Produto.objects.create(
            codigo_produto='PLJP01', descricao='Contado', setor='A', embalagem='Unidade',
        )
        pendente = Produto.objects.create(
            codigo_produto='PLJP02', descricao='Pendente', setor='A', embalagem='Unidade',
        )
        inativo = Produto.objects.create(
            codigo_produto='PLJP03', descricao='Inativo', setor='A', embalagem='Unidade',
            ativo=False,
        )
        InventarioItem.objects.create(
            inventario=inventario, posicao=self.posicao, produto=contado,
            quantidade_fisica=Decimal('10'),
        )
        EstoqueSAP.objects.create(produto=contado, total=Decimal('10'), arquivo_origem='t.xlsx')
        EstoqueSAP.objects.create(produto=pendente, total=Decimal('5'), arquivo_origem='t.xlsx')
        EstoqueSAP.objects.create(produto=inativo, total=Decimal('99'), arquivo_origem='t.xlsx')

        indicadores = obter_indicadores_dashboard()
        grafico = next(g for g in indicadores.graficos_geral if g.id == 'planejado_contado')

        self.assertEqual(grafico.valores, [1, 1])
        self.assertEqual(grafico.centro_valor, '50%')
        self.assertIn(f'#{inventario.pk}', grafico.progresso_texto)
        self.assertIn('1 de 2 itens', grafico.progresso_texto)
        self.assertFalse(grafico.mensagem_vazia)


class DashboardRankingContagemTestCase(TestCase):
    def test_ranking_ordena_por_quantidade_e_ignora_sem_usuario(self):
        posicao = Posicao.objects.create(codigo='RK01', posicao='R-01')
        inventario = Inventario.objects.create(
            usuario=Usuario.objects.create(
                nome='Dono Inventario', login='dono_rk', setor='Estoque',
                perfil=Usuario.Perfil.INVENTARIO,
            ),
            status=Inventario.Status.EM_ANDAMENTO,
        )
        user_a, _ = criar_usuario_teste(username='rk.usera', perfil=Usuario.Perfil.INVENTARIO)
        user_b, _ = criar_usuario_teste(username='rk.userb', perfil=Usuario.Perfil.INVENTARIO)

        produtos = [
            Produto.objects.create(
                codigo_produto=f'RKP{i}', descricao=f'Produto {i}', setor='A', embalagem='Unidade',
            )
            for i in range(4)
        ]
        for indice, produto in enumerate(produtos[:3]):
            InventarioItem.objects.create(
                inventario=inventario,
                posicao=posicao,
                produto=produto,
                quantidade_fisica=Decimal('1'),
                usuario_contagem=user_a,
            )
        InventarioItem.objects.create(
            inventario=inventario, posicao=posicao, produto=produtos[3],
            quantidade_fisica=Decimal('1'), usuario_contagem=user_b,
        )

        indicadores = obter_indicadores_dashboard()
        grafico = next(g for g in indicadores.graficos_geral if g.id == 'ranking_usuarios')

        self.assertEqual(grafico.labels[0], user_a.perfil_operacional.nome)
        self.assertEqual(grafico.valores[0], 3)
        self.assertEqual(grafico.labels[1], user_b.perfil_operacional.nome)
        self.assertEqual(grafico.valores[1], 1)
        self.assertEqual(sum(grafico.valores), 4)


class DashboardMaioresDivergenciasTestCase(TestCase):
    def test_lista_apenas_divergentes_ordenado_por_diferenca_absoluta_e_ignora_inativo(self):
        usuario = Usuario.objects.create(
            nome='Operador Divergencia', login='opdiv', setor='Estoque',
            perfil=Usuario.Perfil.INVENTARIO,
        )
        posicao = Posicao.objects.create(codigo='DIV01', posicao='D-01')
        inventario = Inventario.objects.create(usuario=usuario, status=Inventario.Status.FINALIZADO)

        correto = Produto.objects.create(
            codigo_produto='DIVP01', descricao='Correto', setor='A', embalagem='Unidade',
        )
        divergente_pequeno = Produto.objects.create(
            codigo_produto='DIVP02', descricao='Divergente Pequeno', setor='A', embalagem='Unidade',
        )
        divergente_grande = Produto.objects.create(
            codigo_produto='DIVP03', descricao='Divergente Grande', setor='A', embalagem='Unidade',
        )
        inativo = Produto.objects.create(
            codigo_produto='DIVP04', descricao='Inativo', setor='A', embalagem='Unidade',
            ativo=False,
        )

        for produto, fisico, sap in (
            (correto, Decimal('10'), Decimal('10')),
            (divergente_pequeno, Decimal('12'), Decimal('10')),
            (divergente_grande, Decimal('50'), Decimal('10')),
            (inativo, Decimal('0'), Decimal('999')),
        ):
            InventarioItem.objects.create(
                inventario=inventario, posicao=posicao, produto=produto,
                quantidade_fisica=fisico,
            )
            EstoqueSAP.objects.create(produto=produto, total=sap, arquivo_origem='t.xlsx')

        indicadores = obter_indicadores_dashboard()
        linhas = indicadores.maiores_divergencias

        self.assertEqual([linha.codigo_produto for linha in linhas], ['DIVP03', 'DIVP02'])
        self.assertEqual(linhas[0].diferenca, Decimal('40'))
        self.assertEqual(linhas[0].status_label, 'Excesso físico')

    def test_sem_inventario_finalizado_retorna_lista_vazia(self):
        indicadores = obter_indicadores_dashboard()
        self.assertEqual(indicadores.maiores_divergencias, [])
        self.assertIsNone(indicadores.maiores_divergencias_inventario)


class DashboardCiclicoNaoInterfereTestCase(TestCase):
    def test_ciclo_nao_entra_nas_analises_gerais(self):
        from decimal import Decimal as D

        from inventario.services.ciclico import criar_ciclo, limpar_estado_ciclico

        limpar_estado_ciclico()
        produto = Produto.objects.create(
            codigo_produto='CICDASH1', descricao='Produto Ciclo', setor='A', embalagem='Unidade',
        )
        posicao = Posicao.objects.create(codigo='CICDASH01', posicao='CD-01')
        EstoqueSAP.objects.create(produto=produto, total=D('10'), arquivo_origem='t.xlsx')
        EstoqueFisico.objects.create(
            posicao=posicao, produto=produto, quantidade=D('10'), data_contagem=timezone.now(),
        )

        criar_ciclo()

        indicadores = obter_indicadores_dashboard()
        grafico_ranking = next(g for g in indicadores.graficos_geral if g.id == 'ranking_usuarios')
        grafico_planejado = next(g for g in indicadores.graficos_geral if g.id == 'planejado_contado')

        self.assertEqual(grafico_ranking.valores, [])
        self.assertEqual(grafico_planejado.mensagem_vazia, 'Nenhum inventário em andamento no momento.')

        limpar_estado_ciclico()


class DashboardViewTestCase(ClienteAutenticadoMixin, TestCase):
    def setUp(self):
        self.autenticar_cliente()

    def test_home_exibe_dashboard(self):
        response = self.client.get(reverse('home'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Dashboard Operacional')
        self.assertContains(response, 'Acuracidade Geral')
        self.assertContains(response, 'chart-status_inventarios')
        self.assertNotContains(response, 'Inventário Cíclico')
        self.assertContains(response, 'dashboard.css')
        self.assertContains(response, 'id="dashboard-graficos-data"')
        self.assertContains(response, '"status_inventarios"')


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
