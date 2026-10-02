"""Pré-cadastro Pocket: validação do formulário, zeros à esquerda e retorno.

O cliente de teste do Django grava só no banco de testes, separado do desenvolvimento.
"""

import json
import re

from django.test import TestCase
from django.urls import reverse

from accounts.models import Usuario
from accounts.test_utils import ClienteAutenticadoMixin, criar_usuario_teste
from core.choices import StatusHomologacao
from inventario.models import Inventario
from posicoes.models import Posicao
from produtos.models import Produto


class OperadorPrecadastroPocketTestCase(ClienteAutenticadoMixin, TestCase):
    def setUp(self):
        self.user = self.autenticar_cliente(perfil=Usuario.Perfil.OPERADOR)

    def test_cria_produto_com_zero_a_esquerda_e_volta_ao_pocket(self):
        resposta = self.client.post(reverse('pocket:operador_precadastro_produto'), {
            'codigo_produto': '00123',
            'descricao': 'Item com zero',
            'embalagem': 'CX',
        })
        self.assertEqual(resposta.status_code, 302)
        self.assertEqual(resposta['Location'], reverse('pocket:selecionar'))

        produto = Produto.objects.get(codigo_produto='00123')
        self.assertEqual(produto.descricao, 'Item com zero')
        self.assertEqual(produto.embalagem, 'CX')
        self.assertTrue(produto.ativo)

        mestres = self.client.get(reverse('pocket:dados_mestres'))
        self.assertEqual(mestres.status_code, 200)
        self.assertEqual(mestres.json()['mapa_produtos']['00123'], 'Item com zero')

    def test_campos_obrigatorios_mantem_valor_informado(self):
        resposta = self.client.post(reverse('pocket:operador_precadastro_produto'), {
            'codigo_produto': '00123',
            'descricao': '',
            'embalagem': 'CX',
        })
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, 'Este campo é obrigatório.')
        self.assertContains(resposta, 'value="00123"')
        self.assertContains(resposta, 'value="CX"')
        self.assertContains(resposta, reverse('pocket:selecionar'))
        self.assertFalse(Produto.objects.filter(codigo_produto='00123').exists())
        self.assertNotContains(resposta, 'disabled')

    def test_sku_duplicado_nao_altera_o_registro(self):
        Produto.objects.create(
            codigo_produto='00100',
            descricao='Original',
            setor='A',
            embalagem='CX',
        )
        resposta = self.client.post(reverse('pocket:operador_precadastro_produto'), {
            'codigo_produto': '00100',
            'descricao': 'Outra descrição',
            'embalagem': 'UN',
        })
        self.assertEqual(resposta.status_code, 302)
        produto = Produto.objects.get(codigo_produto='00100')
        self.assertEqual(Produto.objects.filter(codigo_produto='00100').count(), 1)
        self.assertEqual(produto.descricao, 'Original')
        self.assertEqual(produto.embalagem, 'CX')

    def test_produto_rejeitado_mostra_mensagem_e_mantem_valores(self):
        Produto.objects.create(
            codigo_produto='00999',
            descricao='Original',
            setor='A',
            status_homologacao=StatusHomologacao.REJEITADO,
            ativo=False,
        )
        resposta = self.client.post(reverse('pocket:operador_precadastro_produto'), {
            'codigo_produto': '00999',
            'descricao': 'Tentativa',
            'embalagem': 'CX',
        })
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, 'Produto rejeitado. Solicite revisão ao administrador.')
        self.assertContains(resposta, 'value="00999"')
        self.assertContains(resposta, 'value="Tentativa"')
        produto = Produto.objects.get(codigo_produto='00999')
        self.assertEqual(produto.descricao, 'Original')
        self.assertEqual(Produto.objects.filter(codigo_produto='00999').count(), 1)

    def test_cria_posicao_com_zero_a_esquerda_sem_confundir_codigo_e_descricao(self):
        resposta = self.client.post(reverse('pocket:operador_precadastro_posicao'), {
            'codigo': '001',
            'posicao': 'Docas 001',
        })
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, 'data-sucesso="1"')
        self.assertContains(resposta, 'Voltar ao Pocket')
        self.assertContains(resposta, reverse('pocket:selecionar'))

        posicao = Posicao.objects.get(codigo='001')
        self.assertEqual(posicao.posicao, 'Docas 001')
        self.assertTrue(posicao.ativo)

        mapa = self.client.get(reverse('pocket:dados_mestres')).json()['mapa_posicoes']
        self.assertEqual(mapa['001'], 'Docas 001')

    def test_posicao_obrigatoria_mantem_codigo(self):
        resposta = self.client.post(reverse('pocket:operador_precadastro_posicao'), {
            'codigo': '001',
            'posicao': '',
        })
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, 'Este campo é obrigatório.')
        self.assertContains(resposta, 'value="001"')
        self.assertFalse(Posicao.objects.filter(codigo='001').exists())

    def test_codigo_duplicado_nao_sobrescreve_posicao(self):
        Posicao.objects.create(codigo='001', posicao='Antiga', ativo=True)
        resposta = self.client.post(reverse('pocket:operador_precadastro_posicao'), {
            'codigo': '001',
            'posicao': 'Nova',
        })
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(Posicao.objects.filter(codigo='001').count(), 1)
        self.assertEqual(Posicao.objects.get(codigo='001').posicao, 'Antiga')
        self.assertContains(resposta, 'data-sucesso="1"')

    def test_posicao_rejeitada_mostra_mensagem_e_mantem_valores(self):
        Posicao.objects.create(
            codigo='00999',
            posicao='Antiga',
            status_homologacao=StatusHomologacao.REJEITADO,
            ativo=False,
        )
        resposta = self.client.post(reverse('pocket:operador_precadastro_posicao'), {
            'codigo': '00999',
            'posicao': 'Tentativa',
        })
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, 'Posição rejeitada. Solicite revisão ao administrador.')
        self.assertContains(resposta, 'value="00999"')
        self.assertContains(resposta, 'value="Tentativa"')
        self.assertNotContains(resposta, 'data-sucesso="1"')
        posicao = Posicao.objects.get(codigo='00999')
        self.assertEqual(posicao.posicao, 'Antiga')


class InventarioPrecadastroPocketTestCase(ClienteAutenticadoMixin, TestCase):
    def setUp(self):
        self.user = self.autenticar_cliente(perfil=Usuario.Perfil.INVENTARIO)
        self.perfil = Usuario.objects.get(login=self.user.username)
        self.inventario = Inventario.objects.create(
            usuario=self.perfil,
            status=Inventario.Status.EM_ANDAMENTO,
        )

    def test_produto_salvo_volta_para_o_inventario_e_entra_na_contagem(self):
        url = reverse('pocket:precadastro_produto', args=[self.inventario.pk])
        resposta = self.client.post(url, {
            'codigo_produto': '00123',
            'descricao': 'Item do inventário',
            'codigo_ean': '0789',
            'embalagem': 'CX',
            'observacao': 'nota',
        })
        destino = reverse('pocket:contagem', args=[self.inventario.pk])
        self.assertEqual(resposta.status_code, 302)
        self.assertEqual(resposta['Location'], destino)

        produto = Produto.objects.get(codigo_produto='00123')
        self.assertEqual(produto.codigo_ean, '0789')
        self.assertEqual(produto.embalagem, 'CX')
        self.assertEqual(produto.observacao_precadastro, 'nota')

        contagem = self.client.get(destino)
        self.assertEqual(contagem.status_code, 200)
        html = contagem.content.decode()
        bloco = re.search(
            r'id="pocket-produtos-data"[^>]*>(.*?)</script>',
            html,
        )
        self.assertIsNotNone(bloco)
        mapa = json.loads(bloco.group(1))
        self.assertEqual(mapa['00123'], 'Item do inventário')
        ean = json.loads(re.search(
            r'id="pocket-ean-data"[^>]*>(.*?)</script>',
            html,
        ).group(1))
        self.assertEqual(ean['0789']['codigo_produto'], '00123')

    def test_erro_de_produto_preserva_url_do_inventario(self):
        url = reverse('pocket:precadastro_produto', args=[self.inventario.pk])
        resposta = self.client.post(url, {
            'codigo_produto': '',
            'descricao': '',
            'codigo_ean': '',
            'embalagem': '',
            'observacao': '',
        })
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, 'Este campo é obrigatório.')
        self.assertContains(
            resposta,
            reverse('pocket:contagem', args=[self.inventario.pk]),
        )
        self.assertContains(resposta, f'Inventário #{self.inventario.pk}')

    def test_posicao_salva_permanece_no_inventario(self):
        url = reverse('pocket:precadastro_posicao', args=[self.inventario.pk])
        resposta = self.client.post(url, {
            'codigo': '001',
            'posicao': 'Docas 001',
        })
        destino = reverse('pocket:contagem', args=[self.inventario.pk])
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, destino)
        self.assertContains(resposta, 'Voltar à contagem')
        self.assertEqual(Posicao.objects.get(codigo='001').posicao, 'Docas 001')

        contagem = self.client.get(destino)
        self.assertContains(contagem, '001')
        self.assertContains(contagem, 'Docas 001')

    def test_ciclico_preserva_retorno_da_contagem(self):
        resposta = self.client.get(reverse('pocket:precadastro_posicao_ciclico'))
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, reverse('pocket:contagem_ciclico'))
        self.assertContains(resposta, 'Voltar à contagem')
        self.assertContains(resposta, 'id="pocket-cadastro-teclado"')
        self.assertNotContains(resposta, 'pocket-bipagem.js')


class PrecadastroNaoUsaFluxoDeContagemTestCase(TestCase):
    def test_tela_do_operador_nao_herda_debounce_da_contagem(self):
        usuario, _perfil = criar_usuario_teste(
            username='op.precadastro.tela',
            perfil=Usuario.Perfil.OPERADOR,
        )
        self.client.force_login(usuario)
        produto = self.client.get(reverse('pocket:operador_precadastro_produto'))
        posicao = self.client.get(reverse('pocket:operador_precadastro_posicao'))
        self.assertEqual(produto.status_code, 200)
        self.assertEqual(posicao.status_code, 200)
        for resposta in (produto, posicao):
            html = resposta.content.decode()
            self.assertNotIn('pocket-bipagem.js', html)
            self.assertNotIn('POCKET_VALIDACAO_ATRASO_MS', html)
            self.assertIn('type="text"', html)
            self.assertNotIn('type="number"', html)
            self.assertIn('inputmode="none"', html)
