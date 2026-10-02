"""Pré-cadastro Pocket: validação do formulário, zeros à esquerda e retorno.

O cliente de teste do Django grava só no banco de testes, separado do desenvolvimento.
"""

import json
import re
from pathlib import Path

from django.conf import settings
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from accounts.models import Usuario
from accounts.test_utils import ClienteAutenticadoMixin, criar_usuario_teste
from core.choices import StatusHomologacao
from inventario.models import Inventario
from posicoes.models import Posicao
from produtos.forms import listar_embalagens_distintas
from produtos.models import Produto


class OperadorPrecadastroPocketTestCase(ClienteAutenticadoMixin, TestCase):
    def setUp(self):
        self.user = self.autenticar_cliente(perfil=Usuario.Perfil.OPERADOR)

    def test_cria_produto_com_zero_a_esquerda_e_volta_ao_pocket(self):
        Produto.objects.create(
            codigo_produto='CAT-CX',
            descricao='Catálogo',
            setor='A',
            embalagem='CX',
        )
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
        Produto.objects.create(
            codigo_produto='CAT-CX',
            descricao='Catálogo',
            setor='A',
            embalagem='CX',
        )
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
            'embalagem': 'CX',
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
            embalagem='CX',
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
        Produto.objects.create(
            codigo_produto='CAT-CX',
            descricao='Catálogo',
            setor='A',
            embalagem='CX',
        )
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
            self.assertNotIn('inputmode="none"', html)

    def test_codigo_recebe_bip_sem_virar_campo_de_descricao(self):
        from pathlib import Path

        usuario, _perfil = criar_usuario_teste(
            username='op.bip.codigo',
            perfil=Usuario.Perfil.OPERADOR,
        )
        self.client.force_login(usuario)
        produto = self.client.get(reverse('pocket:operador_precadastro_produto')).content.decode()
        posicao = self.client.get(reverse('pocket:operador_precadastro_posicao')).content.decode()

        sku = re.search(r'<input\b[^>]*\bname="codigo_produto"[^>]*>', produto)
        descricao = re.search(r'<input\b[^>]*\bname="descricao"[^>]*>', produto)
        codigo = re.search(r'<input\b[^>]*\bname="codigo"[^>]*>', posicao)
        nome = re.search(r'<input\b[^>]*\bname="posicao"[^>]*>', posicao)
        self.assertIsNotNone(sku)
        self.assertIsNotNone(descricao)
        self.assertIsNotNone(codigo)
        self.assertIsNotNone(nome)
        for tag in (sku.group(0), codigo.group(0)):
            self.assertIn('type="text"', tag)
            self.assertNotIn('inputmode=', tag)
            self.assertIn('autofocus', tag)
            self.assertIn('maxlength="50"', tag)
            self.assertNotIn('readonly', tag)
            self.assertNotIn('disabled', tag)
            self.assertNotIn('type="number"', tag)
        self.assertNotIn('autofocus', descricao.group(0))
        self.assertNotIn('autofocus', nome.group(0))
        self.assertNotIn('data-cadastro-enter', descricao.group(0))
        self.assertIn('data-cadastro-enter="validar-codigo"', codigo.group(0))
        self.assertIsNotNone(re.search(r'<select\b[^>]*\bname="embalagem"', produto))
        self.assertIn('Não informado', produto)

        cadastro = Path(settings.BASE_DIR, 'static', 'js', 'pocket-cadastro.js').read_text(encoding='utf-8')
        fluxo = Path(settings.BASE_DIR, 'static', 'js', 'pocket-precadastro-posicao.js').read_text(encoding='utf-8')
        self.assertIn('function instalarRecepcaoDeLeitura', cadastro)
        self.assertNotIn('requestAnimationFrame', cadastro)
        self.assertNotIn('POCKET_VALIDACAO_ATRASO', cadastro)
        self.assertNotIn('localStorage', cadastro)
        self.assertNotIn('sessionStorage', cadastro)
        self.assertIn("addEventListener('keyup'", cadastro)
        self.assertIn('data-cadastro-enter-pendente', cadastro)
        foco = fluxo.split('function focarCampo', 1)[1].split('function campoTemErro', 1)[0]
        self.assertNotIn('requestAnimationFrame', foco)
        self.assertNotIn('setTimeout', foco)
        self.assertIn('.focus(', foco)
        self.assertIn('data-codigo-confirma-pendente', fluxo)
        self.assertIn("addEventListener('keyup'", fluxo)
        self.assertNotIn('POCKET_VALIDACAO_ATRASO', fluxo)


def _criar_catalogo(codigo, embalagem, descricao='Catálogo'):
    return Produto.objects.create(
        codigo_produto=codigo,
        descricao=descricao,
        setor='A',
        embalagem=embalagem,
    )


def _select_embalagem(html):
    select = re.search(
        r'<select\b[^>]*\bname="embalagem"[^>]*>(.*?)</select>',
        html,
        re.DOTALL,
    )
    return select.group(1) if select else ''


def _opcoes_embalagem(html):
    miolo = _select_embalagem(html)
    return re.findall(r'<option\b([^>]*)>(.*?)</option>', miolo, re.DOTALL)


def _valor_option(attrs):
    achado = re.search(r'\bvalue="([^"]*)"', attrs)
    return achado.group(1) if achado else ''


class EmbalagemComboboxPrecadastroTestCase(TestCase):
    def setUp(self):
        self.operador, _perfil = criar_usuario_teste(
            username='op.emb.combo',
            perfil=Usuario.Perfil.OPERADOR,
        )
        self.usuario_inventario, perfil = criar_usuario_teste(
            username='inv.emb.combo',
            perfil=Usuario.Perfil.INVENTARIO,
        )
        self.inventario = Inventario.objects.create(
            usuario=perfil,
            status=Inventario.Status.EM_ANDAMENTO,
        )
        _criar_catalogo('CAT-1', 'BOMBONA')
        _criar_catalogo('CAT-2', 'BOMBONA')
        _criar_catalogo('CAT-3', 'bombona')
        _criar_catalogo('CAT-4', 'Tambor')
        _criar_catalogo('CAT-5', '  CX ')
        _criar_catalogo('CAT-6', '')
        _criar_catalogo('CAT-7', '   ')
        _criar_catalogo('CAT-8', '\t')
        _criar_catalogo('CAT-9', '\n')
        _criar_catalogo('CAT-10', ' \r\n ')

    def test_consulta_distinta_exclui_vazio_e_nao_regrava(self):
        with CaptureQueriesContext(connection) as consultas:
            opcoes = listar_embalagens_distintas()
        self.assertEqual(len(consultas), 1)
        sql = consultas[0]['sql'].upper()
        self.assertIn('DISTINCT', sql)
        self.assertEqual(opcoes.count('BOMBONA'), 1)
        self.assertEqual(opcoes.count('bombona'), 1)
        self.assertNotIn('', opcoes)
        self.assertNotIn('   ', opcoes)
        self.assertNotIn('\t', opcoes)
        self.assertNotIn('\n', opcoes)
        self.assertNotIn(' \r\n ', opcoes)
        self.assertEqual(
            opcoes,
            sorted(opcoes, key=lambda valor: (valor.casefold(), valor)),
        )
        self.assertEqual(Produto.objects.get(codigo_produto='CAT-5').embalagem, '  CX ')

    def test_selecao_valida_grava_o_valor_exato_nos_dois_fluxos(self):
        self.client.force_login(self.operador)
        operador = self.client.post(reverse('pocket:operador_precadastro_produto'), {
            'codigo_produto': '001',
            'descricao': 'Item operador',
            'embalagem': '  CX ',
        })
        self.assertEqual(operador.status_code, 302)
        self.assertEqual(Produto.objects.get(codigo_produto='001').embalagem, '  CX ')

        self.client.force_login(self.usuario_inventario)
        url = reverse('pocket:precadastro_produto', args=[self.inventario.pk])
        inventario = self.client.post(url, {
            'codigo_produto': '002',
            'descricao': 'Item inventário',
            'codigo_ean': '',
            'embalagem': 'Tambor',
            'observacao': '',
        })
        self.assertEqual(inventario.status_code, 302)
        self.assertEqual(
            inventario['Location'],
            reverse('pocket:contagem', args=[self.inventario.pk]),
        )
        self.assertEqual(Produto.objects.get(codigo_produto='002').embalagem, 'Tambor')
        self.assertEqual(Produto.objects.get(codigo_produto='CAT-5').embalagem, '  CX ')

    def test_valor_fora_do_conjunto_e_rejeitado(self):
        self.client.force_login(self.operador)
        resposta = self.client.post(reverse('pocket:operador_precadastro_produto'), {
            'codigo_produto': '003',
            'descricao': 'Item',
            'embalagem': 'GRANEL_NOVO',
        })
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, 'Selecione uma embalagem já cadastrada.')
        self.assertFalse(Produto.objects.filter(codigo_produto='003').exists())

        self.client.force_login(self.usuario_inventario)
        url = reverse('pocket:precadastro_produto', args=[self.inventario.pk])
        inventario = self.client.post(url, {
            'codigo_produto': '004',
            'descricao': 'Item',
            'embalagem': '   ',
        })
        self.assertEqual(inventario.status_code, 200)
        self.assertContains(inventario, 'Selecione uma embalagem já cadastrada.')
        self.assertFalse(Produto.objects.filter(codigo_produto='004').exists())

    def test_selecao_permanece_quando_outro_campo_falha(self):
        self.client.force_login(self.operador)
        resposta = self.client.post(reverse('pocket:operador_precadastro_produto'), {
            'codigo_produto': '005',
            'descricao': '',
            'embalagem': 'Tambor',
        })
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, 'Este campo é obrigatório.')
        html = resposta.content.decode()
        selecionada = [
            _valor_option(attrs)
            for attrs, _rotulo in _opcoes_embalagem(html)
            if re.search(r'\bselected\b', attrs)
        ]
        self.assertEqual(selecionada, ['Tambor'])
        self.assertEqual(html.count('value="BOMBONA"'), 1)
        self.assertFalse(Produto.objects.filter(codigo_produto='005').exists())

    def test_embalagem_continua_opcional_e_a_lista_e_um_select(self):
        from produtos.forms import PrecadastroProdutoForm, PrecadastroProdutoOperadorForm

        self.assertFalse(PrecadastroProdutoOperadorForm.base_fields['embalagem'].required)
        self.assertFalse(PrecadastroProdutoForm.base_fields['embalagem'].required)

        self.client.force_login(self.operador)
        tela = self.client.get(reverse('pocket:operador_precadastro_produto'))
        html = tela.content.decode()
        self.assertIsNotNone(re.search(r'<select\b[^>]*name="embalagem"', html))
        self.assertIsNone(re.search(r'<input\b[^>]*name="embalagem"', html))
        self.assertIn('Não informado', _select_embalagem(html))
        self.assertNotIn('Embalagem *', html)
        self.assertIn('id="pocket-cadastro-teclado"', html)
        self.assertIn('Salvar pré-cadastro', html)
        self.assertIn(reverse('pocket:selecionar'), html)
        self.assertNotIn('Nenhuma embalagem cadastrada', html)

        salvo = self.client.post(reverse('pocket:operador_precadastro_produto'), {
            'codigo_produto': '006',
            'descricao': 'Sem embalagem',
            'embalagem': '',
        })
        self.assertEqual(salvo.status_code, 302)
        self.assertEqual(Produto.objects.get(codigo_produto='006').embalagem, '')

        self.client.force_login(self.usuario_inventario)
        url = reverse('pocket:precadastro_produto', args=[self.inventario.pk])
        inventario = self.client.get(url)
        self.assertContains(inventario, 'name="embalagem"')
        self.assertContains(inventario, 'Não informado')
        self.assertContains(inventario, reverse('pocket:contagem', args=[self.inventario.pk]))
        self.assertContains(inventario, 'Voltar à contagem')
        self.assertNotContains(inventario, 'pocket-bipagem.js')

    def test_sem_opcoes_mostra_aviso_e_nao_inventa_embalagem(self):
        Produto.objects.all().delete()
        _criar_catalogo('SO-1', '')
        _criar_catalogo('SO-2', '   ')
        self.client.force_login(self.operador)
        tela = self.client.get(reverse('pocket:operador_precadastro_produto'))
        html = tela.content.decode()
        self.assertIn('Nenhuma embalagem cadastrada nos produtos.', html)
        self.assertIn('Não informado', _select_embalagem(html))
        self.assertEqual(
            [_valor_option(attrs) for attrs, _rotulo in _opcoes_embalagem(html)],
            [''],
        )
        self.assertIsNone(re.search(r'<input\b[^>]*name="embalagem"', html))
        salvo = self.client.post(reverse('pocket:operador_precadastro_produto'), {
            'codigo_produto': '007',
            'descricao': 'Sem lista',
            'embalagem': '',
        })
        self.assertEqual(salvo.status_code, 302)
        self.assertEqual(Produto.objects.get(codigo_produto='007').embalagem, '')

    def test_contagem_e_cadastro_completo_nao_mudam_e_atraso_permanece(self):
        cadastro = Path(
            settings.BASE_DIR, 'produtos', 'templates', 'produtos', 'formulario.html',
        ).read_text(encoding='utf-8')
        self.assertIn('id="embalagem-opcoes"', cadastro)
        self.assertNotIn('Não informado', cadastro)

        contagem = Path(
            settings.BASE_DIR, 'inventario', 'templates', 'inventario', 'pocket', 'contagem.html',
        ).read_text(encoding='utf-8')
        self.assertNotIn('pocket-cadastro.js', contagem)
        self.assertNotIn('Não informado', contagem)

        js = Path(settings.BASE_DIR, 'static', 'js', 'pocket-cadastro.js').read_text(encoding='utf-8')
        self.assertIn("=== 'SELECT'", js)
        self.assertIn("querySelectorAll('input, textarea')", js)
        self.assertNotIn('setInterval', js)
        self.assertNotIn('POCKET_VALIDACAO_ATRASO', js)

        bipagem = Path(settings.BASE_DIR, 'static', 'js', 'pocket-bipagem.js').read_text(encoding='utf-8')
        self.assertEqual(bipagem.count('POCKET_VALIDACAO_ATRASO_MS = 2000'), 1)

        settings_src = Path(settings.BASE_DIR, 'core', 'settings.py').read_text(encoding='utf-8')
        self.assertIn("os.environ.get('POCKET_STATIC_VERSION', '20261002c')", settings_src)
