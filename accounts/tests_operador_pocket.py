from django.test import TestCase
from django.urls import reverse

from accounts.models import Usuario
from accounts.test_utils import ClienteAutenticadoMixin, criar_usuario_teste
from core.choices import StatusHomologacao
from posicoes.models import Posicao
from produtos.models import Produto


class OperadorPocketAcessoTestCase(ClienteAutenticadoMixin, TestCase):
    def setUp(self):
        self.user = self.autenticar_cliente(perfil=Usuario.Perfil.OPERADOR)

    def test_login_redireciona_para_pocket(self):
        self.client.logout()
        response = self.client.post(reverse('accounts:login'), {
            'username': self.user.username,
            'password': 'senha12345',
        })
        self.assertRedirects(response, reverse('pocket:selecionar'))

    def test_operador_bloqueado_fora_do_pocket(self):
        response = self.client.get(reverse('home'))
        self.assertRedirects(response, reverse('pocket:selecionar'))

    def test_operador_bloqueado_em_ciclico_executar(self):
        response = self.client.get(reverse('ciclico_executar'))
        self.assertRedirects(response, reverse('pocket:selecionar'))

    def test_operador_bloqueado_em_inventarios(self):
        response = self.client.get(reverse('inventario:lista'))
        self.assertRedirects(response, reverse('pocket:selecionar'))

    def test_operador_acessa_pocket(self):
        response = self.client.get(reverse('pocket:selecionar'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Pocket')
        self.assertNotContains(response, 'Voltar para Inventários')

    def test_operador_usa_interface_exclusiva(self):
        response = self.client.get(reverse('pocket:selecionar'))
        self.assertNotContains(response, 'id="appSidebar"')
        self.assertNotContains(response, 'sidebar-nav')
        self.assertNotContains(response, 'Painel de Controle')
        self.assertContains(response, 'operador-app')

    def test_operador_menu_hamburguer(self):
        response = self.client.get(reverse('pocket:selecionar'))
        self.assertContains(response, 'Cadastrar Produto')
        self.assertContains(response, 'Cadastrar Posição')

    def test_operador_precadastra_produto(self):
        response = self.client.post(reverse('pocket:operador_precadastro_produto'), {
            'codigo_produto': 'OP-100',
            'descricao': 'Produto operador',
            'embalagem': 'CAIXA',
        }, follow=True)
        self.assertEqual(response.status_code, 200)
        produto = Produto.objects.get(codigo_produto='OP-100')
        self.assertEqual(produto.status_homologacao, StatusHomologacao.PENDENTE)
        self.assertEqual(produto.usuario_precadastro.login, self.user.username)

    def test_operador_precadastra_posicao(self):
        response = self.client.post(reverse('pocket:operador_precadastro_posicao'), {
            'codigo': 'A-01-02-03',
            'posicao': 'Posição A-01-02-03',
        }, follow=True)
        self.assertEqual(response.status_code, 200)
        posicao = Posicao.objects.get(codigo='A-01-02-03')
        self.assertEqual(posicao.posicao, 'Posição A-01-02-03')
        self.assertEqual(posicao.status_homologacao, StatusHomologacao.HOMOLOGADO)
        self.assertTrue(posicao.ativo)

    def test_inventario_nao_usa_rotas_operador(self):
        inventario_user, _ = criar_usuario_teste(
            username='inv.pocket',
            perfil=Usuario.Perfil.INVENTARIO,
        )
        self.client.force_login(inventario_user)
        response = self.client.get(reverse('pocket:operador_precadastro_produto'))
        self.assertRedirects(response, reverse('pocket:selecionar'))
