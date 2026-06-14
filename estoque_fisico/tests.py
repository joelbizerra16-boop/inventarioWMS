from decimal import Decimal
from io import BytesIO

import pandas as pd
from django.test import TestCase
from django.urls import reverse

from accounts.models import Usuario
from accounts.test_utils import ClienteAutenticadoMixin, criar_usuario_teste
from estoque_fisico.models import EstoqueFisico
from posicoes.models import Posicao
from produtos.models import Produto


class EstoqueFisicoViewTestCase(ClienteAutenticadoMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.produto_a = Produto.objects.create(
            codigo_produto='EF001',
            descricao='Produto Estoque Fisico A',
            setor='SECAO-A',
            embalagem='CX',
        )
        cls.produto_b = Produto.objects.create(
            codigo_produto='EF002',
            descricao='Produto Estoque Fisico B',
            setor='SECAO-B',
            embalagem='UN',
        )
        cls.posicao_a = Posicao.objects.create(codigo='ALOC-A', posicao='RUA-A-01')
        cls.posicao_b = Posicao.objects.create(codigo='ALOC-B', posicao='RUA-B-01')

        cls.registro_a = EstoqueFisico.objects.create(
            posicao=cls.posicao_a,
            produto=cls.produto_a,
            quantidade=Decimal('10.000'),
            data_contagem='2026-06-01T10:00:00Z',
        )
        cls.registro_b = EstoqueFisico.objects.create(
            posicao=cls.posicao_b,
            produto=cls.produto_b,
            quantidade=Decimal('0.000'),
            data_contagem='2026-06-01T10:00:00Z',
        )

        for indice in range(3, 23):
            produto = Produto.objects.create(
                codigo_produto=f'EF{indice:03d}',
                descricao=f'Produto paginacao {indice}',
                setor='SECAO-A',
            )
            posicao = Posicao.objects.create(
                codigo=f'ALOC-{indice:03d}',
                posicao=f'RUA-{indice:03d}',
            )
            EstoqueFisico.objects.create(
                posicao=posicao,
                produto=produto,
                quantidade=Decimal('5.000'),
                data_contagem='2026-06-01T10:00:00Z',
            )

    def setUp(self):
        self.autenticar_cliente()

    def test_listagem_requer_autenticacao(self):
        self.client.logout()
        response = self.client.get(reverse('estoque_fisico:lista'))
        self.assertEqual(response.status_code, 302)

    def test_listagem_exibe_registros(self):
        response = self.client.get(
            reverse('estoque_fisico:lista'),
            {'q': 'EF001'},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'EF001')
        self.assertContains(response, 'ALOC-A')
        self.assertContains(response, 'RUA-A-01')

    def test_pesquisa_por_codigo_produto(self):
        response = self.client.get(
            reverse('estoque_fisico:lista'),
            {'q': 'EF002'},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'EF002')
        self.assertNotContains(response, 'EF001')

    def test_filtro_setor(self):
        response = self.client.get(
            reverse('estoque_fisico:lista'),
            {'setor': 'SECAO-B', 'q': 'EF002'},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'EF002')
        self.assertNotContains(response, 'EF001')

    def test_filtro_somente_quantidade_positiva(self):
        response = self.client.get(
            reverse('estoque_fisico:lista'),
            {'somente_positivo': '1', 'q': 'EF001'},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['page_obj'].paginator.count, 1)
        self.assertContains(response, 'EF001')

        response_zero = self.client.get(
            reverse('estoque_fisico:lista'),
            {'somente_positivo': '1', 'q': 'EF002'},
        )
        self.assertEqual(response_zero.status_code, 200)
        self.assertEqual(response_zero.context['page_obj'].paginator.count, 0)

    def test_paginacao(self):
        response = self.client.get(
            reverse('estoque_fisico:lista'),
            {'setor': 'SECAO-A'},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['page_obj'].has_other_pages)
        self.assertGreaterEqual(
            response.context['page_obj'].paginator.num_pages,
            2,
        )

        response_pagina_2 = self.client.get(
            reverse('estoque_fisico:lista'),
            {'setor': 'SECAO-A', 'page': 2},
        )
        self.assertEqual(response_pagina_2.status_code, 200)
        self.assertEqual(response_pagina_2.context['page_obj'].number, 2)

    def test_exportacao_excel(self):
        response = self.client.get(reverse('estoque_fisico:exportar'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response['Content-Type'],
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
        self.assertIn('Estoque_Fisico.xlsx', response['Content-Disposition'])

        dataframe = pd.read_excel(BytesIO(response.content))
        self.assertEqual(
            list(dataframe.columns),
            [
                'Código Alocação',
                'Alocação',
                'Código Produto',
                'Descrição',
                'Quantidade',
                'Data Atualização',
            ],
        )
        self.assertGreaterEqual(len(dataframe), 2)

    def test_perfil_consulta_pode_acessar(self):
        self.client.logout()
        user, _ = criar_usuario_teste(
            username='consulta.ef',
            perfil=Usuario.Perfil.CONSULTA,
        )
        self.client.force_login(user)
        response = self.client.get(reverse('estoque_fisico:lista'))
        self.assertEqual(response.status_code, 200)
