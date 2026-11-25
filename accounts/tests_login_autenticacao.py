"""Autenticação operacional: senha, CSRF, inativo e falha técnica."""

import re
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import Usuario
from accounts.test_utils import ClienteAutenticadoMixin, criar_usuario_teste


SENHA_ESPECIAL = ' Senha!@# 9 '


class LoginAutenticacaoTestCase(ClienteAutenticadoMixin, TestCase):
    def setUp(self):
        self.admin = self.autenticar_cliente()

    def _criar_operador(self, login, senha):
        response = self.client.post(reverse('accounts:usuarios_criar'), {
            'nome': 'Operador Especial',
            'login': login,
            'setor': 'Estoque',
            'perfil': Usuario.Perfil.OPERADOR,
            'ativo': 'on',
            'senha': senha,
            'confirmar_senha': senha,
        })
        self.assertEqual(response.status_code, 302)
        usuario = Usuario.objects.get(login=login)
        self.assertTrue(usuario.user.check_password(senha))
        return usuario

    def test_cadastro_real_preserva_senha_e_autentica(self):
        usuario = self._criar_operador('op.especial', SENHA_ESPECIAL)
        self.assertFalse(usuario.user.check_password(SENHA_ESPECIAL.strip()))

        self.client.logout()
        response = self.client.post(reverse('accounts:login'), {
            'username': 'op.especial',
            'password': SENHA_ESPECIAL,
        })
        self.assertRedirects(response, reverse('pocket:selecionar'))
        self.assertIn('_auth_user_id', self.client.session)
        seguido = self.client.get(response.url)
        self.assertEqual(seguido.status_code, 200)
        self.assertTrue(seguido.wsgi_request.user.is_authenticated)

    def test_redefinicao_real_autentica_com_a_nova_senha(self):
        usuario = self._criar_operador('op.troca', SENHA_ESPECIAL)
        nova = 'Outra#Senha 1'
        response = self.client.post(
            reverse('accounts:usuarios_editar', args=[usuario.pk]),
            {
                'nome': usuario.nome,
                'login': usuario.login,
                'setor': usuario.setor,
                'perfil': usuario.perfil,
                'ativo': 'on',
                'senha': nova,
                'confirmar_senha': nova,
            },
        )
        self.assertEqual(response.status_code, 302)
        usuario.user.refresh_from_db()
        self.assertTrue(usuario.user.check_password(nova))
        self.assertFalse(usuario.user.check_password(SENHA_ESPECIAL))

        self.client.logout()
        response = self.client.post(reverse('accounts:login'), {
            'username': 'op.troca',
            'password': nova,
        })
        self.assertRedirects(response, reverse('pocket:selecionar'))

    def test_cadastro_sem_acesso_nao_grava_login_como_senha(self):
        operacional = Usuario.objects.create(
            nome='Sem Acesso',
            login='sem.acesso',
            setor='Estoque',
            perfil=Usuario.Perfil.OPERADOR,
            ativo=True,
        )
        response = self.client.post(
            reverse('accounts:usuarios_editar', args=[operacional.pk]),
            {
                'nome': 'Sem Acesso',
                'login': 'sem.acesso',
                'setor': 'Estoque',
                'perfil': Usuario.Perfil.OPERADOR,
                'ativo': 'on',
                'senha': '',
                'confirmar_senha': '',
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Informe uma senha')
        self.assertFalse(User.objects.filter(username='sem.acesso').exists())

        response = self.client.post(
            reverse('accounts:usuarios_editar', args=[operacional.pk]),
            {
                'nome': 'Sem Acesso',
                'login': 'sem.acesso',
                'setor': 'Estoque',
                'perfil': Usuario.Perfil.OPERADOR,
                'ativo': 'on',
                'senha': SENHA_ESPECIAL,
                'confirmar_senha': SENHA_ESPECIAL,
            },
        )
        self.assertEqual(response.status_code, 302)
        operacional.refresh_from_db()
        self.assertTrue(operacional.user.check_password(SENHA_ESPECIAL))

    def test_credencial_incorreta_e_usuario_inexistente_tem_a_mesma_mensagem(self):
        self._criar_operador('op.existe', 'senha12345')
        self.client.logout()
        url = reverse('accounts:login')
        incorreta = self.client.post(url, {
            'username': 'op.existe',
            'password': 'senha-errada',
        })
        inexistente = self.client.post(url, {
            'username': 'nao.existe',
            'password': 'senha12345',
        })
        self.assertEqual(incorreta.status_code, 200)
        self.assertEqual(inexistente.status_code, 200)
        self.assertContains(incorreta, 'Usuário ou senha inválidos.')
        self.assertContains(inexistente, 'Usuário ou senha inválidos.')
        self.assertNotContains(incorreta, 'Ocorreu um erro inesperado')
        self.assertNotContains(inexistente, 'não existe')
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_maiusculas_no_usuario_nao_sao_aceitas_como_o_mesmo_login(self):
        self._criar_operador('op.caixa', 'senha12345')
        self.client.logout()
        response = self.client.post(reverse('accounts:login'), {
            'username': 'OP.CAIXA',
            'password': 'senha12345',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Usuário ou senha inválidos.')

    def test_usuario_inativo_tem_a_mesma_mensagem_publica_e_nao_entra(self):
        user, perfil = criar_usuario_teste(
            username='op.inativo',
            perfil=Usuario.Perfil.OPERADOR,
        )
        user.is_active = False
        user.save(update_fields=['is_active'])
        perfil.ativo = False
        perfil.save(update_fields=['ativo'])
        self.client.logout()
        response = self.client.post(reverse('accounts:login'), {
            'username': 'op.inativo',
            'password': 'senha12345',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Usuário ou senha inválidos.')
        self.assertNotContains(response, 'Usuário inativo')
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_usuario_sem_perfil_nao_recebe_sessao(self):
        User.objects.create_user('orfao.login', password='senha12345')
        self.client.logout()
        response = self.client.post(reverse('accounts:login'), {
            'username': 'orfao.login',
            'password': 'senha12345',
        }, follow=True)
        self.assertContains(response, 'Usuário sem perfil operacional vinculado.')
        self.assertNotContains(response, 'Usuário ou senha inválidos.')
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_campos_vazios(self):
        self.client.logout()
        response = self.client.post(reverse('accounts:login'), {
            'username': '',
            'password': '',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Informe o usuário.')
        self.assertContains(response, 'Informe a senha.')
        self.assertNotContains(response, 'Usuário ou senha inválidos.')

    def test_csrf_recusado_orienta_atualizar_e_csrf_valido_entra(self):
        self._criar_operador('op.csrf', 'senha12345')
        self.client.logout()
        client = Client(enforce_csrf_checks=True)
        recusado = client.post(reverse('accounts:login'), {
            'username': 'op.csrf',
            'password': 'senha12345',
        })
        self.assertEqual(recusado.status_code, 403)
        self.assertContains(
            recusado,
            'Atualize a página e tente novamente.',
            status_code=403,
        )
        self.assertNotContains(recusado, 'Usuário ou senha inválidos.', status_code=403)
        self.assertNotContains(recusado, 'Traceback', status_code=403)

        pagina = client.get(reverse('accounts:login'))
        token = re.search(
            r'name="csrfmiddlewaretoken" value="([^"]+)"',
            pagina.content.decode(),
        ).group(1)
        autenticado = client.post(reverse('accounts:login'), {
            'username': 'op.csrf',
            'password': 'senha12345',
            'csrfmiddlewaretoken': token,
        })
        self.assertRedirects(autenticado, reverse('pocket:selecionar'))

    def test_erro_interno_nao_vaza_senha_nem_devolve_sucesso(self):
        self.client.logout()
        senha = 'Segredo!@#99'
        with self.assertLogs('inventario.auditoria', level='ERROR') as logs:
            with patch(
                'django.contrib.auth.forms.authenticate',
                side_effect=RuntimeError('falha-simulada-login'),
            ):
                response = self.client.post(reverse('accounts:login'), {
                    'username': 'op.interno',
                    'password': senha,
                })
        self.assertEqual(response.status_code, 500)
        corpo = response.content.decode()
        self.assertIn('ATD-', corpo)
        self.assertIn('erro interno', corpo.lower())
        self.assertNotIn('Traceback', corpo)
        self.assertNotIn('falha-simulada-login', corpo)
        self.assertNotIn(senha, corpo)
        self.assertNotIn('_auth_user_id', self.client.session)
        registrado = '\n'.join(logs.output)
        self.assertIn('ATD-', registrado)
        self.assertIn('RuntimeError', registrado)
        self.assertNotIn(senha, registrado)
        self.assertContains(response, 'Entrar', status_code=500)

    def test_formulario_nativo_unico_e_legivel_no_coletor(self):
        self.client.logout()
        response = self.client.get(reverse('accounts:login'))
        html = response.content.decode()
        self.assertEqual(html.count('<form'), 1)
        self.assertEqual(html.count('type="submit"'), 1)
        self.assertIn('accept-charset="UTF-8"', html)
        self.assertIn('autocapitalize="none"', html)
        self.assertIn('data-envio', html)
        self.assertIn('Não foi possível conectar', html)
        self.assertNotIn('id="appSidebar"', html)
        self.assertIn('name="password"', html)
