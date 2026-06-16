"""Testes do guard de redirect do Pocket."""

import json

from django.contrib.auth.models import AnonymousUser
from django.http import HttpResponseRedirect
from django.test import RequestFactory, TestCase, override_settings

from core.pocket_http import json_erro_pocket
from core.pocket_middleware import PocketPostRedirectGuardMiddleware


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
