"""Correções de contagem web, finalização atômica e recontagem de SKU ausente."""

from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from accounts.models import Usuario
from accounts.test_utils import ClienteAutenticadoMixin
from estoque_fisico.models import EstoqueFisico
from inventario.models import Inventario, InventarioItem
from inventario.models_operacional import InventarioTarefa
from inventario.services.contagem import salvar_contagem
from posicoes.models import Posicao
from produtos.models import Produto


class ContagemWebIntegridadeTestCase(ClienteAutenticadoMixin, TestCase):
    def setUp(self):
        self.user = self.autenticar_cliente(Usuario.Perfil.INVENTARIO)
        self.operacional = Usuario.objects.get(login='admin.teste')
        self.produto = Produto.objects.create(
            codigo_produto='INT01',
            descricao='Produto integridade',
            setor='A',
            embalagem='Unidade',
        )
        self.produto_b = Produto.objects.create(
            codigo_produto='INT02',
            descricao='Produto integridade B',
            setor='A',
            embalagem='Unidade',
        )
        self.posicao = Posicao.objects.create(codigo='INT01', posicao='I-01')
        self.posicao_b = Posicao.objects.create(codigo='INT02', posicao='I-02')
        self.inventario = Inventario.objects.create(
            usuario=self.operacional,
            status=Inventario.Status.ABERTO,
        )

    def test_edicao_grava_nova_posicao_e_produto(self):
        item = InventarioItem.objects.create(
            inventario=self.inventario,
            posicao=self.posicao,
            produto=self.produto,
            quantidade_fisica=Decimal('1.000'),
        )
        url = reverse('inventario:contagem_editar', args=[self.inventario.pk, item.pk])
        response = self.client.post(url, {
            'posicao': self.posicao_b.pk,
            'produto': self.produto_b.pk,
            'quantidade_fisica': '4.500',
        })

        self.assertRedirects(
            response,
            reverse('inventario:contagem_lista', args=[self.inventario.pk]),
        )
        item.refresh_from_db()
        self.assertEqual(item.posicao_id, self.posicao_b.pk)
        self.assertEqual(item.produto_id, self.produto_b.pk)
        self.assertEqual(item.quantidade_fisica, Decimal('4.500'))

    def test_edicao_duplicada_mostra_erro_sem_500(self):
        item = InventarioItem.objects.create(
            inventario=self.inventario,
            posicao=self.posicao,
            produto=self.produto,
            quantidade_fisica=Decimal('2'),
        )
        outro = InventarioItem.objects.create(
            inventario=self.inventario,
            posicao=self.posicao_b,
            produto=self.produto_b,
            quantidade_fisica=Decimal('3'),
        )
        url = reverse('inventario:contagem_editar', args=[self.inventario.pk, outro.pk])
        response = self.client.post(url, {
            'posicao': self.posicao.pk,
            'produto': self.produto.pk,
            'quantidade_fisica': '9',
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Produto já inventariado nesta posição.')
        outro.refresh_from_db()
        self.assertEqual(outro.posicao_id, self.posicao_b.pk)
        self.assertEqual(outro.quantidade_fisica, Decimal('3'))
        item.refresh_from_db()
        self.assertEqual(item.quantidade_fisica, Decimal('2'))

    def test_quantidade_negativa_e_rejeitada_no_servidor(self):
        url = reverse('inventario:contagem_criar', args=[self.inventario.pk])
        response = self.client.post(url, {
            'posicao': self.posicao.pk,
            'produto': self.produto.pk,
            'quantidade_fisica': '-1',
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Quantidade não pode ser negativa.')
        self.assertFalse(InventarioItem.objects.exists())

    def test_posicao_nao_atribuida_mostra_erro_sem_500(self):
        outro = User.objects.create_user(username='outro.op', password='senha12345')
        InventarioTarefa.objects.create(
            tipo_inventario=InventarioTarefa.TipoInventario.GERAL,
            inventario=self.inventario,
            posicao=self.posicao,
            operador=outro,
        )
        url = reverse('inventario:contagem_criar', args=[self.inventario.pk])
        response = self.client.post(url, {
            'posicao': self.posicao.pk,
            'produto': self.produto.pk,
            'quantidade_fisica': '1',
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Posição não atribuída a você.')
        self.assertFalse(InventarioItem.objects.exists())

    def test_servico_atualiza_posicao_do_item_existente(self):
        item = InventarioItem.objects.create(
            inventario=self.inventario,
            posicao=self.posicao,
            produto=self.produto,
            quantidade_fisica=Decimal('1'),
        )
        atualizado = salvar_contagem(
            self.inventario,
            self.posicao_b,
            self.produto_b,
            Decimal('7.250'),
            item_existente=item,
            usuario_contagem=self.user,
            origem_contagem=InventarioItem.OrigemContagem.WEB,
        )
        atualizado.refresh_from_db()
        self.assertEqual(atualizado.posicao_id, self.posicao_b.pk)
        self.assertEqual(atualizado.produto_id, self.produto_b.pk)
        self.assertEqual(atualizado.quantidade_fisica, Decimal('7.250'))


class FinalizacaoAtomicaTestCase(ClienteAutenticadoMixin, TestCase):
    def setUp(self):
        self.autenticar_cliente(Usuario.Perfil.INVENTARIO)
        self.operacional = Usuario.objects.get(login='admin.teste')
        self.produto = Produto.objects.create(
            codigo_produto='FINX',
            descricao='Produto falha snapshot',
            setor='A',
            embalagem='Unidade',
        )
        self.posicao = Posicao.objects.create(codigo='FINX', posicao='F-99')
        self.inventario = Inventario.objects.create(
            usuario=self.operacional,
            status=Inventario.Status.EM_ANDAMENTO,
        )
        InventarioItem.objects.create(
            inventario=self.inventario,
            posicao=self.posicao,
            produto=self.produto,
            quantidade_fisica=Decimal('8'),
        )

    def test_falha_no_snapshot_desfaz_finalizacao(self):
        self.client.raise_request_exception = False
        url = reverse('inventario:finalizar', args=[self.inventario.pk])
        with patch(
            'inventario.services.inventario_snapshot.congelar_snapshot_inventario',
            side_effect=RuntimeError('falha de snapshot'),
        ):
            response = self.client.post(url, follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Ocorreu um erro inesperado.')
        self.inventario.refresh_from_db()
        self.assertEqual(self.inventario.status, Inventario.Status.EM_ANDAMENTO)
        self.assertIsNone(self.inventario.data_finalizacao)
        self.assertIsNone(self.inventario.snapshot_resultado)
        self.assertEqual(EstoqueFisico.objects.count(), 0)


class RecontagemSkuAusenteTestCase(ClienteAutenticadoMixin, TestCase):
    def test_sku_inexistente_redireciona_com_mensagem(self):
        self.autenticar_cliente()
        with patch('inventario.views.obter_ciclo_atual', return_value=object()):
            response = self.client.post(reverse('ciclico_executar'), {
                'acao': 'recontagem',
                'sku_id': '999999',
            })

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('ciclico_executar'))
