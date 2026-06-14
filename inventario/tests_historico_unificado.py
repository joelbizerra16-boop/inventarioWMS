from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from accounts.models import Usuario
from accounts.test_utils import ClienteAutenticadoMixin, criar_usuario_teste
from estoque_sap.models import EstoqueSAP
from inventario.models import Inventario, InventarioItem
from inventario.services.inventario_snapshot import congelar_snapshot_inventario
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
