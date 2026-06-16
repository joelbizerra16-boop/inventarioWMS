"""Testes do guard de redirect do Pocket."""

import json
from unittest.mock import patch

from django.contrib.auth.models import AnonymousUser
from django.http import HttpResponseRedirect
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import reverse

from core.pocket_http import json_erro_pocket
from core.pocket_middleware import PocketPostRedirectGuardMiddleware
from core.services.exclusao import MENSAGEM_ERRO_INESPERADO
from core.views import handler500
from inventario.tests_pocket_ciclico import PocketCiclicoTestCase


@override_settings(MIDDLEWARE=[])
class PocketRedirectGuardMiddlewareTestCase(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.middleware = PocketPostRedirectGuardMiddleware(self._get_response_redirect)

    @staticmethod
    def _get_response_redirect(request):
        return HttpResponseRedirect('/')

    def test_bloqueia_redirect_em_post_pocket_ciclico(self):
        request = self.factory.post(
            '/pocket/ciclico/',
            data={'acao': 'contagem', 'pocket_ajax': '1'},
        )
        request.user = AnonymousUser()
        response = self.middleware(request)
        self.assertEqual(response.status_code, 500)
        self.assertIn('application/json', response['Content-Type'])
        payload = json.loads(response.content)
        self.assertFalse(payload['ok'])
        self.assertIn('Redirect indevido bloqueado', payload['message'])

    def test_permite_json_em_post_pocket_ciclico(self):
        def get_response(request):
            return json_erro_pocket(request, 'erro de teste', status=400)

        middleware = PocketPostRedirectGuardMiddleware(get_response)
        request = self.factory.post(
            '/pocket/ciclico/',
            data={'acao': 'contagem', 'pocket_ajax': '1'},
        )
        request.user = AnonymousUser()
        response = middleware(request)
        self.assertEqual(response.status_code, 400)
        self.assertFalse(json.loads(response.content)['ok'])


class Handler500PocketContagemTestCase(TestCase):
    def test_handler500_post_contagem_retorna_json_nao_redirect_home(self):
        request = RequestFactory().post(
            '/pocket/ciclico/',
            data={'acao': 'contagem', 'pocket_ajax': '1'},
        )
        request.user = AnonymousUser()
        response = handler500(request)
        self.assertEqual(response.status_code, 500)
        self.assertIn('application/json', response['Content-Type'])
        payload = json.loads(response.content)
        self.assertFalse(payload['ok'])
        self.assertEqual(payload['message'], MENSAGEM_ERRO_INESPERADO)
        self.assertNotIn('Location', response)


class PocketContagemExcecaoNaoRedirecionaTestCase(PocketCiclicoTestCase):
    def test_contagem_excecao_inesperada_retorna_json_sem_redirect(self):
        with patch(
            'inventario.pocket_views.registrar_contagem_pocket_ciclico_por_sku',
            side_effect=RuntimeError('falha simulada'),
        ):
            response = self.client.post(reverse('pocket:contagem_ciclico'), {
                'acao': 'contagem',
                'pocket_ajax': '1',
                'sku_id': str(self.sku.pk),
                'codigo_posicao': 'PKT01',
                'codigo_produto_lido': self.produto.codigo_produto,
                'quantidade_fisica': '10',
            })
        self.assertEqual(response.status_code, 500)
        self.assertIn('application/json', response['Content-Type'])
        self.assertFalse(response.json()['ok'])
        self.assertNotIn('Location', response)
