"""Testes da view de login operacional com fluxo Pocket."""

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import Usuario
from accounts.test_utils import criar_usuario_teste


class OperacionalLoginPocketAjaxTestCase(TestCase):
    def setUp(self):
        self.client = Client()
        self.user, _ = criar_usuario_teste(
            username='operador.login.pocket',
            perfil=Usuario.Perfil.OPERADOR,
        )

    def test_login_autenticado_com_referer_pocket_retorna_json_401(self):
        self.client.force_login(self.user)
        response = self.client.get(
            reverse('accounts:login'),
            HTTP_REFERER='https://inventariowms.onrender.com/pocket/ciclico/',
        )
        self.assertEqual(response.status_code, 401)
        self.assertIn('application/json', response['Content-Type'])
        self.assertIn('Sessão expirada', response.json()['message'])
