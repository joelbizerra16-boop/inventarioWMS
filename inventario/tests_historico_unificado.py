from decimal import Decimal

from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from accounts.models import Usuario
from accounts.test_utils import ClienteAutenticadoMixin, criar_usuario_teste
from estoque_sap.models import EstoqueSAP
from inventario.models import Inventario, InventarioItem
from inventario.services.ciclico import criar_ciclo, encerrar_ciclo, limpar_estado_ciclico
from inventario.services.ciclico_relatorio import obter_grupos_consulta_ciclo
from inventario.services.historico_unificado import obter_detalhe_historico_unificado
from inventario.services.inventario_snapshot import congelar_snapshot_inventario
from inventario.tests_ciclico_auditoria import CiclicoAuditoriaBaseMixin
from posicoes.models import Posicao
from produtos.models import Produto


class HistoricoUnificadoTestCase(ClienteAutenticadoMixin, TestCase):
    def setUp(self):
        self.user, self.usuario = criar_usuario_teste(perfil=Usuario.Perfil.INVENTARIO)
        self.client.force_login(self.user)
        self.posicao = Posicao.objects.create(codigo='HIST-P1', posicao='Corredor A')
        self.produto = Produto.objects.create(
            codigo_produto='HIST100',
            descricao='Produto Histórico Geral',
        )
        EstoqueSAP.objects.create(
            produto=self.produto,
            total=Decimal('56'),
            arquivo_origem='teste.xlsx',
        )
        self.inventario = Inventario.objects.create(
            usuario=self.usuario,
            status=Inventario.Status.FINALIZADO,
        )
        InventarioItem.objects.create(
            inventario=self.inventario,
            posicao=self.posicao,
            produto=self.produto,
            quantidade_fisica=Decimal('56'),
            usuario_contagem=self.user,
        )
        congelar_snapshot_inventario(self.inventario, self.usuario)

    def test_lista_unificada_exibe_inventario_finalizado(self):
        response = self.client.get(reverse('historico_unificado') + '?tipo=GERAL')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'#{self.inventario.pk}')
        self.assertContains(response, 'Inventário')

    def test_detalhe_inventario_exibe_posicoes(self):
        response = self.client.get(
            reverse('historico_detalhe', args=['GERAL', self.inventario.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'HIST100')
        self.assertContains(response, 'Corredor A')
        self.assertContains(response, 'Posições contadas')

    def test_exportacao_excel_inventario(self):
        response = self.client.get(
            reverse('historico_exportar', args=['GERAL', self.inventario.pk]) + '?formato=excel'
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            response['Content-Type'],
        )

    def test_ciclico_historico_redireciona_unificado(self):
        response = self.client.get(reverse('ciclico_historico'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('historico', response.url)
        self.assertIn('tipo=CICLICO', response.url)


class HistoricoCiclicoPerformanceTestCase(
    CiclicoAuditoriaBaseMixin,
    ClienteAutenticadoMixin,
    TestCase,
):
    def setUp(self):
        self.user = self.autenticar_cliente()
        limpar_estado_ciclico()
        self.posicao = Posicao.objects.create(codigo='HPERF01', posicao='P-01')
        for indice in range(25):
            self._criar_par_sap_fisico(
                f'HP{indice:03d}',
                self.posicao,
                Decimal('10'),
                Decimal('10'),
            )
        criar_ciclo(usuario_criacao=self.user)
        self.ciclo = encerrar_ciclo()

    def tearDown(self):
        limpar_estado_ciclico()

    @override_settings(DEBUG=True)
    def test_detalhe_ciclico_sem_n_plus1(self):
        with CaptureQueriesContext(connection) as contexto:
            detalhe = obter_detalhe_historico_unificado('CICLICO', self.ciclo.pk)
        self.assertIsNotNone(detalhe)
        self.assertGreater(len(detalhe.produtos), 0)
        self.assertLessEqual(
            len(contexto.captured_queries),
            15,
            msg='Detalhe histórico cíclico deve usar poucas queries com prefetch.',
        )

    def test_detalhe_ciclico_view_responde_200(self):
        response = self.client.get(
            reverse('historico_detalhe', args=['CICLICO', self.ciclo.pk]),
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('Erro interno', response.content.decode())

    def test_exportacao_excel_ciclico_responde_200(self):
        response = self.client.get(
            reverse('historico_exportar', args=['CICLICO', self.ciclo.pk]) + '?formato=excel',
        )
        self.assertEqual(response.status_code, 200)

    @override_settings(DEBUG=True)
    def test_grupos_consulta_com_historico_usa_prefetch(self):
        with CaptureQueriesContext(connection) as contexto:
            grupos = obter_grupos_consulta_ciclo(
                self.ciclo.pk,
                incluir_historico=True,
            )
        self.assertGreater(len(grupos), 0)
        self.assertLessEqual(len(contexto.captured_queries), 20)
