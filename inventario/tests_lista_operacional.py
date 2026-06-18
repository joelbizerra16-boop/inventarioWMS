from decimal import Decimal

from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from accounts.models import Usuario
from accounts.test_utils import ClienteAutenticadoMixin
from inventario.models import Inventario
from inventario.services.ciclico import criar_ciclo, limpar_estado_ciclico, obter_resumo_ciclico
from inventario.tests_ciclico_auditoria import CiclicoAuditoriaBaseMixin
from posicoes.models import Posicao
from produtos.models import Produto


class InventarioListaViewTestCase(ClienteAutenticadoMixin, TestCase):
    def setUp(self):
        self.user = self.autenticar_cliente()
        self.operador = Usuario.objects.create(
            nome='Lista Operador',
            login='lista.op',
            setor='WMS',
            perfil=Usuario.Perfil.INVENTARIO,
        )
        self.inventario = Inventario.objects.create(
            usuario=self.operador,
            status=Inventario.Status.ABERTO,
        )
        posicao = Posicao.objects.create(codigo='LST01', posicao='L-01')
        produto = Produto.objects.create(
            codigo_produto='LSTP01',
            descricao='Produto lista',
            setor='A',
            embalagem='Unidade',
        )
        self.inventario.itens.create(posicao=posicao, produto=produto)

    def test_lista_inventarios_responde_200_sem_mensagem_erro(self):
        response = self.client.get(reverse('inventario:lista'))
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertNotIn('Ocorreu um erro inesperado', body)
        self.assertNotIn('alert-danger', body)
        self.assertIn('Inventários', body)

    def test_lista_exibe_quantidade_itens_contados(self):
        response = self.client.get(reverse('inventario:lista'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '1')


class CiclicoListaViewTestCase(CiclicoAuditoriaBaseMixin, ClienteAutenticadoMixin, TestCase):
    def setUp(self):
        self.user = self.autenticar_cliente()
        limpar_estado_ciclico()
        self.posicao = Posicao.objects.create(codigo='CLST01', posicao='C-01')
        self._criar_par_sap_fisico('CLSTP1', self.posicao, Decimal('5'), Decimal('5'))

    def tearDown(self):
        limpar_estado_ciclico()

    def test_ciclico_responde_200_sem_erro(self):
        criar_ciclo(usuario_criacao=self.user)
        response = self.client.get(reverse('ciclico'))
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertNotIn('Erro interno', body)
        self.assertNotIn('Ocorreu um erro inesperado', body)
        self.assertIn('Inventário Cíclico', body)

    @override_settings(DEBUG=True)
    def test_resumo_ciclico_nao_dispara_n_plus1_canais_sap(self):
        criar_ciclo(usuario_criacao=self.user)
        with CaptureQueriesContext(connection) as contexto:
            resumo = obter_resumo_ciclico()
        self.assertGreater(resumo.total_skus, 0)
        self.assertLessEqual(
            len(contexto.captured_queries),
            12,
            msg='Resumo cíclico deve usar poucas queries (prefetch + canais em lote).',
        )
