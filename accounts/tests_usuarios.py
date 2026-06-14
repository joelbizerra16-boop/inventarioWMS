from django.test import TestCase
from django.urls import reverse

from accounts.models import Usuario
from accounts.test_utils import ClienteAutenticadoMixin, criar_usuario_teste
from accounts.services.usuarios import criar_usuario_operacional, filtrar_usuarios, obter_resumo_usuarios


class UsuariosModuloTestCase(ClienteAutenticadoMixin, TestCase):
    def setUp(self):
        self.admin = self.autenticar_cliente()
        self.user_operador, self.operador = criar_usuario_teste(
            username='operador.modulo',
            perfil=Usuario.Perfil.INVENTARIO,
        )

    def test_lista_usuarios_admin(self):
        response = self.client.get(reverse('accounts:usuarios_lista'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Usuários Ativos')
        self.assertContains(response, 'Novo Usuário')
        self.assertContains(response, self.operador.nome)

    def test_lista_bloqueada_para_nao_admin(self):
        self.client.force_login(self.user_operador)
        response = self.client.get(reverse('accounts:usuarios_lista'))
        self.assertEqual(response.status_code, 302)

    def test_criar_usuario(self):
        response = self.client.post(reverse('accounts:usuarios_criar'), {
            'nome': 'Supervisor Teste',
            'login': 'supervisor.teste',
            'setor': 'Estoque',
            'perfil': Usuario.Perfil.SUPERVISOR,
            'ativo': 'on',
            'senha': 'senha12345',
            'confirmar_senha': 'senha12345',
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Usuario.objects.filter(login='supervisor.teste').exists())

    def test_filtro_por_login(self):
        resultado = filtrar_usuarios(login='operador.modulo')
        self.assertEqual(resultado.count(), 1)
        self.assertEqual(resultado.first().login, 'operador.modulo')

    def test_resumo_usuarios(self):
        resumo = obter_resumo_usuarios()
        self.assertGreaterEqual(resumo['ativos'], 2)
        self.assertGreaterEqual(resumo['administradores'], 1)
        self.assertGreaterEqual(resumo['operadores'], 1)

    def test_toggle_status(self):
        response = self.client.post(
            reverse('accounts:usuarios_toggle_status', args=[self.operador.pk]),
        )
        self.assertEqual(response.status_code, 302)
        self.operador.refresh_from_db()
        self.assertFalse(self.operador.ativo)

    def test_sidebar_link_usuarios(self):
        response = self.client.get(reverse('home'))
        self.assertContains(response, reverse('accounts:usuarios_lista'))
        self.assertNotContains(response, 'href="#">\n                    <i class="bi bi-people')

    def test_criar_via_servico(self):
        usuario = criar_usuario_operacional(
            nome='Novo Operador',
            login='novo.operador',
            setor='Picking',
            perfil=Usuario.Perfil.OPERADOR,
            ativo=True,
            senha='senha12345',
        )
        self.assertIsNotNone(usuario.user_id)
        self.assertTrue(usuario.user.check_password('senha12345'))
