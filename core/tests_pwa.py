"""PWA do coletor: manifesto, service worker e tela Pocket.

Gravações ficam no banco de testes do Django, separado da base de desenvolvimento.
"""

from django.conf import settings
from django.contrib.staticfiles import finders
from django.contrib.staticfiles.views import serve
from django.test import RequestFactory, TestCase
from django.urls import reverse
from PIL import Image

from accounts.models import Usuario
from accounts.test_utils import ClienteAutenticadoMixin, criar_usuario_teste
from inventario.models import Inventario
from posicoes.models import Posicao
from produtos.models import Produto


class ManifestPwaTestCase(TestCase):
    def test_manifest_standalone_no_escopo_raiz(self):
        response = self.client.get(reverse('pwa_manifest'))
        self.assertEqual(response.status_code, 200)
        self.assertIn('application/manifest+json', response['Content-Type'])
        dados = response.json()
        self.assertEqual(dados['name'], 'Sistema de Inventário')
        self.assertEqual(dados['short_name'], 'Inventário')
        self.assertEqual(dados['display'], 'standalone')
        self.assertEqual(dados['scope'], '/')
        self.assertEqual(dados['start_url'], reverse('accounts:login'))
        self.assertTrue(dados['start_url'].startswith(dados['scope']))
        self.assertEqual(dados['theme_color'], '#1E293B')
        self.assertEqual(dados['background_color'], '#F1F5F9')
        por_papel = {(item['sizes'], item['purpose']): item for item in dados['icons']}
        self.assertEqual(
            por_papel[('192x192', 'any')]['src'],
            '/static/img/pwa/icon-192.png',
        )
        self.assertEqual(
            por_papel[('512x512', 'any')]['src'],
            '/static/img/pwa/icon-512.png',
        )
        self.assertEqual(
            por_papel[('512x512', 'maskable')]['src'],
            '/static/img/pwa/icon-maskable-512.png',
        )
        for item in dados['icons']:
            self.assertEqual(item['type'], 'image/png')

    def test_icones_tem_tamanho_certo_e_url_200(self):
        esperados = {
            'img/pwa/icon-192.png': (192, 192),
            'img/pwa/icon-512.png': (512, 512),
            'img/pwa/icon-maskable-512.png': (512, 512),
            'img/pwa/favicon-32.png': (32, 32),
        }
        for relativo, tamanho in esperados.items():
            caminho = finders.find(relativo)
            self.assertTrue(caminho, relativo)
            with Image.open(caminho) as imagem:
                self.assertEqual(imagem.size, tamanho)
            resposta = serve(RequestFactory().get(f'/static/{relativo}'), relativo, insecure=True)
            self.assertEqual(resposta.status_code, 200)
            self.assertIn('image/png', resposta['Content-Type'])
        favicon = finders.find('img/pwa/favicon.ico')
        self.assertTrue(favicon)

    def test_service_worker_nao_cacheia(self):
        response = self.client.get(reverse('pwa_service_worker'))
        self.assertEqual(response.status_code, 200)
        self.assertIn('javascript', response['Content-Type'])
        self.assertEqual(response['Service-Worker-Allowed'], '/')
        corpo = response.content.decode()
        self.assertIn("addEventListener('fetch'", corpo)
        self.assertNotIn('caches.open', corpo)
        self.assertNotIn('caches.match', corpo)
        self.assertNotIn('caches.put', corpo)
        self.assertNotIn('respondWith', corpo)

    def test_login_liga_manifesto_sem_bloquear_zoom(self):
        response = self.client.get(reverse('accounts:login'))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn('rel="manifest"', html)
        self.assertIn('rel="icon"', html)
        self.assertIn('img/pwa/favicon.ico', html)
        self.assertIn('img/pwa/favicon-32.png', html)
        self.assertIn(reverse('pwa_manifest'), html)
        self.assertIn(reverse('pwa_service_worker'), html)
        self.assertNotIn('maximum-scale', html)
        self.assertNotIn('user-scalable', html)

    def test_login_redireciona_no_mesmo_origem(self):
        criar_usuario_teste(username='adm.pwa', perfil=Usuario.Perfil.ADMINISTRADOR)
        response = self.client.post(reverse('accounts:login'), {
            'username': 'adm.pwa',
            'password': 'senha12345',
        })
        self.assertEqual(response.status_code, 302)
        destino = response['Location']
        self.assertTrue(destino.startswith('/'))
        self.assertFalse(destino.startswith('//'))
        self.assertNotIn('://', destino)

        criar_usuario_teste(username='op.pwa', perfil=Usuario.Perfil.OPERADOR)
        self.client.logout()
        response = self.client.post(reverse('accounts:login'), {
            'username': 'op.pwa',
            'password': 'senha12345',
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], reverse('pocket:selecionar'))

    def test_operador_permanece_no_app_ao_buscar_manifesto(self):
        criar_usuario_teste(username='op.sw', perfil=Usuario.Perfil.OPERADOR)
        self.client.login(username='op.sw', password='senha12345')
        manifesto = self.client.get(reverse('pwa_manifest'))
        worker = self.client.get(reverse('pwa_service_worker'))
        self.assertEqual(manifesto.status_code, 200)
        self.assertEqual(worker.status_code, 200)


class PocketTelaPwaTestCase(ClienteAutenticadoMixin, TestCase):
    def setUp(self):
        self.autenticar_cliente()
        usuario = Usuario.objects.create(
            nome='Operador PWA',
            login='pwa.view',
            setor='Estoque',
            perfil=Usuario.Perfil.INVENTARIO,
        )
        Produto.objects.create(
            codigo_produto='PWA001',
            descricao='Produto PWA',
            setor='A',
            codigo_ean='7890000000001',
        )
        Posicao.objects.create(codigo='PWA-01', posicao='A-01')
        self.inventario = Inventario.objects.create(
            usuario=usuario,
            status=Inventario.Status.EM_ANDAMENTO,
        )

    def test_contagem_inputmode_none_e_versao_estatica(self):
        import re
        from pathlib import Path

        response = self.client.get(reverse('pocket:contagem', args=[self.inventario.pk]))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertNotIn('id="pocket-vk-toggle"', html)
        self.assertNotIn('sessionStorage', html)
        self.assertNotIn('pocket-teclado-virtual', html)
        self.assertNotIn('type="number"', html)
        for nome in ('codigo_posicao', 'codigo_produto', 'quantidade_fisica'):
            tag = re.search(rf'<input\b[^>]*\bname="{nome}"[^>]*>', html)
            self.assertIsNotNone(tag, nome)
            attrs = tag.group(0)
            self.assertIn('type="text"', attrs, nome)
            self.assertNotIn('inputmode="none"', attrs, nome)
            self.assertNotIn('readonly', attrs, nome)
            self.assertNotIn('disabled', attrs, nome)
        for nome in ('codigo_posicao', 'codigo_produto'):
            tag = re.search(rf'<input\b[^>]*\bname="{nome}"[^>]*>', html)
            self.assertNotIn('inputmode=', tag.group(0), nome)
        quantidade = re.search(r'<input\b[^>]*\bname="quantidade_fisica"[^>]*>', html)
        self.assertIn('inputmode="numeric"', quantidade.group(0))
        posicao = re.search(r'<input\b[^>]*\bname="codigo_posicao"[^>]*>', html)
        self.assertIn('autofocus', posicao.group(0))
        self.assertIn(f'pocket-bipagem.js?v={settings.POCKET_STATIC_VERSION}', html)
        self.assertIn(f'pocket.css?v={settings.POCKET_STATIC_VERSION}', html)
        self.assertIn('rel="manifest"', html)

        js = Path(finders.find('js/pocket-bipagem.js')).read_text(encoding='utf-8')
        self.assertNotIn('sessionStorage', js)
        self.assertNotIn('localStorage', js)
        self.assertNotIn('pocket-teclado-virtual', js)
        self.assertNotIn('initTecladoColetor', js)
        foco = js.split('function focarCampo', 1)[1].split('function marcarErro', 1)[0]
        self.assertIn('.focus(', foco)
        self.assertNotIn('setInterval', foco)
        self.assertNotIn('setTimeout', foco)
        sync = js.split('function iniciarSincronizacaoPeriodica', 1)[1].split(
            'function exibirPosicao', 1
        )[0]
        self.assertNotIn('focarCampo', sync)
        self.assertNotIn('.focus(', sync)
        self.assertIn('focarCampo(opcoes.posicaoInput)', js)

    def test_leitura_de_posicao_e_produto_espera_sem_salvar_quantidade(self):
        from pathlib import Path

        settings_src = Path(settings.BASE_DIR, 'core', 'settings.py').read_text(encoding='utf-8')
        self.assertIn("os.environ.get('POCKET_STATIC_VERSION', '20261002c')", settings_src)

        js = Path(finders.find('js/pocket-bipagem.js')).read_text(encoding='utf-8')
        self.assertEqual(js.count('POCKET_VALIDACAO_ATRASO_MS = 2000'), 1)
        controle = js.split('function criarControleValidacaoLeitura', 1)[1].split(
            'function registrarLeituraCampo', 1
        )[0]
        self.assertNotIn('setInterval', controle)
        self.assertNotIn('enviarContagem', controle)
        self.assertNotIn('alert(', controle)
        self.assertNotIn('parseInt', controle)
        self.assertNotIn('Number(', controle)
        self.assertIn('compositionstart', js)
        self.assertIn('compositionend', js)
        self.assertNotIn("addEventListener('blur'", js)
        self.assertEqual(js.count('setInterval('), 1)
        self.assertIn('MESTRES_SYNC_MS', js)
        self.assertEqual(js.count('registrarEnter(quantidadeInput, enviarContagem)'), 2)
        self.assertNotIn('registrarLeituraCampo(quantidadeInput', js)
        self.assertEqual(js.count('registrarLeituraCampo('), 5)
        self.assertIn("_envioEmAndamento", js)

        css = Path(finders.find('css/pocket.css')).read_text(encoding='utf-8')
        self.assertIn('.pocket-campo-erro', css)

        ciclico = Path(
            settings.BASE_DIR,
            'inventario',
            'templates',
            'inventario',
            'pocket',
            'contagem_ciclico.html',
        ).read_text(encoding='utf-8')
        self.assertIn(
            "js/pocket-bipagem.js' %}?v={{ POCKET_STATIC_VERSION }}",
            ciclico,
        )
        self.assertNotIn('pocket-cadastro-teclado', ciclico)
        self.assertNotIn('pocket-cadastro.js', ciclico)

    def test_precadastro_tem_teclado_e_contagem_nao(self):
        from pathlib import Path

        versao = settings.POCKET_STATIC_VERSION
        paginas = [
            reverse('pocket:precadastro_produto', args=[self.inventario.pk]),
            reverse('pocket:precadastro_posicao', args=[self.inventario.pk]),
            reverse('pocket:precadastro_posicao_ciclico'),
        ]
        for url in paginas:
            resposta = self.client.get(url)
            self.assertEqual(resposta.status_code, 200, url)
            html = resposta.content.decode()
            self.assertIn('id="pocket-cadastro-teclado"', html)
            self.assertIn('Teclado: físico', html)
            self.assertIn(f'pocket-cadastro.js?v={versao}', html)
            self.assertIn(f'pocket.css?v={versao}', html)
            self.assertNotIn('inputmode="none"', html)
            self.assertIn('rel="manifest"', html)
            self.assertNotIn('pocket-bipagem.js', html)
            self.assertNotIn('POCKET_VALIDACAO_ATRASO_MS', html)
            self.assertNotIn('maximum-scale', html)
            self.assertNotIn('user-scalable', html)
            self.assertNotIn('sessionStorage', html)
            self.assertNotIn('localStorage', html)

        contagem = self.client.get(
            reverse('pocket:contagem', args=[self.inventario.pk])
        ).content.decode()
        self.assertNotIn('pocket-cadastro-teclado', contagem)
        self.assertNotIn('pocket-cadastro.js', contagem)
        self.assertNotIn('Teclado:', contagem)

        contagem_tpl = Path(
            settings.BASE_DIR,
            'inventario',
            'templates',
            'inventario',
            'pocket',
            'contagem.html',
        ).read_text(encoding='utf-8')
        self.assertNotIn('pocket-cadastro-teclado', contagem_tpl)
        self.assertNotIn('pocket-cadastro.js', contagem_tpl)

        cadastro_js = Path(finders.find('js/pocket-cadastro.js')).read_text(encoding='utf-8')
        self.assertNotIn('sessionStorage', cadastro_js)
        self.assertNotIn('localStorage', cadastro_js)
        self.assertNotIn('setInterval', cadastro_js)
        self.assertNotIn('POCKET_VALIDACAO_ATRASO', cadastro_js)
        self.assertNotIn('pocket-bipagem', cadastro_js)
        self.assertNotIn("setAttribute('readonly'", cadastro_js)
        self.assertNotIn("setAttribute('disabled'", cadastro_js)
        self.assertNotIn('campo.disabled =', cadastro_js)
        self.assertIn("data-cadastro-enviando", cadastro_js)
        self.assertIn("removeAttribute('inputmode')", cadastro_js)
        self.assertNotIn("virtual ? 'text' : 'none'", cadastro_js)

        posicao_js = Path(finders.find('js/pocket-precadastro-posicao.js')).read_text(encoding='utf-8')
        self.assertNotIn('requestSubmit', posicao_js)
        self.assertNotIn('POCKET_VALIDACAO_ATRASO', posicao_js)
        self.assertNotIn('setInterval', posicao_js)
        self.assertIn('POSIÇÃO JÁ CADASTRADA', posicao_js)

        operador, _ = criar_usuario_teste(
            username='op.cadastro.pwa',
            perfil=Usuario.Perfil.OPERADOR,
        )
        self.client.force_login(operador)
        produto_op = self.client.get(reverse('pocket:operador_precadastro_produto'))
        posicao_op = self.client.get(reverse('pocket:operador_precadastro_posicao'))
        self.assertContains(produto_op, 'id="pocket-cadastro-teclado"')
        self.assertContains(produto_op, 'Salvar pré-cadastro')
        self.assertContains(produto_op, 'Voltar ao Pocket')
        self.assertNotContains(produto_op, 'pocket-bipagem.js')
        self.assertContains(posicao_op, 'id="pocket-cadastro-teclado"')
        self.assertContains(posicao_op, 'Salvar posição')
        self.assertContains(posicao_op, 'Voltar ao Pocket')
        self.assertNotContains(posicao_op, 'pocket-bipagem.js')

    def test_bip_preserva_codigo_selecionado_e_confirma_no_keyup(self):
        import re
        from pathlib import Path

        js = Path(finders.find('js/pocket-bipagem.js')).read_text(encoding='utf-8')
        self.assertEqual(js.count('POCKET_VALIDACAO_ATRASO_MS = 2000'), 1)
        self.assertIn('function instalarRecepcaoDeLeitura', js)
        self.assertIn("addEventListener('keyup'", js)
        self.assertNotIn("addEventListener('blur'", js)
        self.assertNotIn('localStorage', js)
        self.assertNotIn('sessionStorage', js)
        foco = js.split('function focarCampo', 1)[1].split('function focoNoCampo', 1)[0]
        self.assertNotIn('requestAnimationFrame', foco)
        self.assertNotIn('setTimeout', foco)
        self.assertIn('!el.value', foco)

        leitura = js.split('function registrarLeituraCampo', 1)[1].split(
            'function initAudioTouch', 1
        )[0]
        self.assertIn('confirmaPendente', leitura)
        self.assertIn('controle.cancelar()', leitura)
        self.assertNotIn('setInterval', leitura)
