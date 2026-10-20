import io
import json
import re
from datetime import datetime, timedelta, timezone as dt_timezone
from decimal import Decimal as D

from django.test import TestCase
from django.urls import reverse
from openpyxl import load_workbook

from accounts.models import Usuario
from accounts.test_utils import criar_usuario_teste
from estoque_sap.models import EstoqueSAP
from inventario.models import Inventario, InventarioItem
from inventario.models_auditor import (
    AuditorAcesso,
    EventoAuditoria,
    LiberacaoContagemAuditor,
    PublicacaoAuditoria,
    PublicacaoAuditoriaItem,
)
from inventario.services import auditor_publicacao as pub
from inventario.services.auditor_painel import montar_painel
from inventario.services.comparativo_contagens import ComparativoError, excluir_comparativo, salvar_comparativo
from inventario.services.contagem import excluir_contagem, salvar_contagem
from posicoes.models import Posicao
from produtos.models import Produto

T0 = datetime(2030, 10, 19, 11, 0, tzinfo=dt_timezone.utc)  # 08:00 em Brasília; no futuro, o relógio real não libera lotes
SALDO_SECRETO = '98765'
TERMOS_PROIBIDOS = [
    r'\bsap\b', r'cosan', r'brida', r'\bcana(l|is)\b', r'cont[aá]bil', r'virtual', r'\bsaldo',
    r'acuracidade', r'estoque', SALDO_SECRETO,
]
PROGRAMADA = 'PROGRAMADA'


def _proibidos(texto: str) -> list[str]:
    # O logo oficial (marca da empresa) não é o canal "BRIDA" do SAP.
    texto = re.sub(r'<img[^>]*class="aud-logo"[^>]*>', '', texto)
    baixo = texto.lower()
    return [t for t in TERMOS_PROIBIDOS if re.search(t, baixo)]


def _planilha(conteudo: bytes) -> tuple:
    pasta = load_workbook(io.BytesIO(conteudo))
    textos = []
    for aba in pasta.worksheets:
        for linha in aba.iter_rows(values_only=True):
            textos.extend(str(v) for v in linha if v is not None)
    return pasta, ' '.join(textos)


class AuditorBase(TestCase):
    def setUp(self):
        self.admin, _ = criar_usuario_teste('admin.aud')
        self.auditor, op = criar_usuario_teste('auditor.a', perfil=Usuario.Perfil.AUDITOR)
        op.nome = 'Auditora Ana'
        op.save(update_fields=['nome'])
        self.outro_auditor, _ = criar_usuario_teste('auditor.b', perfil=Usuario.Perfil.AUDITOR)
        self.op1, o = criar_usuario_teste('op.c1', perfil=Usuario.Perfil.INVENTARIO)
        o.nome = 'Carlos'
        o.save(update_fields=['nome'])
        self.op2, o = criar_usuario_teste('op.c2', perfil=Usuario.Perfil.INVENTARIO)
        o.nome = 'Bianca'
        o.save(update_fields=['nome'])
        self.operacional = Usuario.objects.get(login='admin.aud')
        self.inv = {o: Inventario.objects.create(usuario=self.operacional, status=Inventario.Status.EM_ANDAMENTO) for o in (1, 2, 3)}
        self.pos = {c: Posicao.objects.create(codigo=c, posicao=f'01.0{i}.101') for i, c in enumerate(('P1', 'P2', 'P3'), 1)}
        self.prod = {}
        for codigo in ('A', 'B', 'C'):
            self.prod[codigo] = Produto.objects.create(codigo_produto=codigo, descricao=f'Produto {codigo}', setor='X')
            EstoqueSAP.objects.create(
                produto=self.prod[codigo], canal_110=D(SALDO_SECRETO), canal_1=D(1), total=D(SALDO_SECRETO) + 1,
                arquivo_origem='base.xlsx',
            )
        # A: C1 = C2 (10/10); B: C1 ≠ C2 (5/7); C: só C1 (zero registrado).
        self.lancar(1, 'P1', 'A', '10', self.op1, 0)
        self.lancar(2, 'P1', 'A', '10', self.op2, 30)
        self.lancar(1, 'P2', 'B', '5', self.op1, 5)
        self.lancar(2, 'P2', 'B', '7', self.op2, 35)
        self.lancar(1, 'P3', 'C', '0', self.op1, 10)
        self.comparativo = salvar_comparativo(
            comparativo=None, nome='Conferência auditada',
            inventarios={1: self.inv[1], 2: self.inv[2], 3: self.inv[3]},
            referencia_opcao='nova', estado=None, usuario=self.admin,
        )

    def lancar(self, etapa, posicao, produto, qtd, usuario, minutos, momento=None):
        momento = momento or (T0 - timedelta(days=2) + timedelta(minutes=minutos))
        return InventarioItem.objects.create(
            inventario=self.inv[etapa], posicao=self.pos[posicao], produto=self.prod[produto],
            quantidade_fisica=D(qtd), usuario_contagem=usuario, data_contagem=momento,
            usuario_registro=usuario, data_registro=momento,
        )

    def autorizar(self, auditores=None, comparativo=None):
        return pub.configurar(
            comparativo or self.comparativo, self.admin,
            auditores=auditores if auditores is not None else [self.auditor],
        )

    def liberar(self, *etapas, comparativo=None, agora=T0, **opcoes):
        for etapa in etapas:
            pub.liberar_contagem(comparativo or self.comparativo, etapa, self.admin, agora=agora, **opcoes)

    def programar(self, etapa=1, duracao=60, inicio=None, ordem='POSICAO', agora=T0):
        pub.liberar_contagem(
            self.comparativo, etapa, self.admin, modo=PROGRAMADA, duracao_minutos=duracao,
            ordem=ordem, inicio_previsto=inicio, agora=agora,
        )
        return LiberacaoContagemAuditor.objects.get(comparativo=self.comparativo, etapa=etapa)

    def publicar_tudo(self):
        self.autorizar()
        self.liberar(1, 2, 3)

    def url(self, nome, pk=None):
        return reverse(f'auditor:{nome}', args=[pk or self.comparativo.pk])

    def api(self, query=''):
        return self.client.get(self.url('api') + query).json()

    def linha(self, sku, query=''):
        return next(l for l in self.api(query)['linhas'] if l['sku'] == sku)


class AcessoTestCase(AuditorBase):
    def setUp(self):
        super().setUp()
        self.publicar_tudo()

    def test_auditor_acessa_somente_conferencias_atribuidas(self):
        self.client.force_login(self.auditor)
        self.assertRedirects(self.client.get(reverse('auditor:inicio')), self.url('painel'))
        for nome in ('painel', 'conteudo', 'api', 'exportar'):
            with self.subTest(nome=nome):
                resposta = self.client.get(self.url(nome))
                self.assertEqual(resposta.status_code, 200)
                self.assertIn('no-store', resposta['Cache-Control'])
                self.assertIn('private', resposta['Cache-Control'])

    def test_403_em_outras_conferencias_e_endpoints(self):
        outra = salvar_comparativo(
            comparativo=None, nome='Outra', inventarios={1: self.inv[1], 2: None, 3: None},
            referencia_opcao='', estado=None, usuario=self.admin,
        )
        self.client.force_login(self.auditor)
        for nome in ('painel', 'conteudo', 'api', 'exportar'):
            self.assertEqual(self.client.get(self.url(nome, outra.pk)).status_code, 403)
        self.assertTrue(EventoAuditoria.objects.filter(comparativo=outra, tipo='ACESSO_NEGADO', usuario=self.auditor).exists())
        self.assertEqual(self.client.get(self.url('api', 999999)).status_code, 403)
        for rota in ('/inventarios/', '/comparativo-contagens/', f'/comparativo-contagens/{self.comparativo.pk}/',
                     f'/comparativo-contagens/{self.comparativo.pk}/exportar/',
                     f'/comparativo-contagens/{self.comparativo.pk}/auditoria/visualizar/', '/estoque-sap/', '/confronto/',
                     '/dashboard/classico/', '/pocket/', '/admin/'):
            with self.subTest(rota=rota):
                self.assertEqual(self.client.get(rota).status_code, 403)
        self.assertRedirects(self.client.get('/'), reverse('auditor:inicio'), fetch_redirect_response=False)
        self.client.force_login(self.outro_auditor)
        self.assertEqual(self.client.get(self.url('painel')).status_code, 403)
        self.assertEqual(self.client.get(self.url('api')).status_code, 403)

    def test_outros_perfis_nao_usam_painel_do_auditor(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(self.url('painel')).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get(self.url('painel')).status_code, 302)

    def test_revogar_acesso(self):
        self.autorizar(auditores=[])
        self.client.force_login(self.auditor)
        self.assertEqual(self.client.get(self.url('painel')).status_code, 403)
        self.assertTrue(EventoAuditoria.objects.filter(tipo='ACESSO_REVOGADO').exists())

    def test_auditor_nao_altera_nada(self):
        self.client.force_login(self.auditor)
        antes = list(InventarioItem.objects.values_list('pk', 'quantidade_fisica'))
        for rota in (
            reverse('comparativo:auditoria', args=[self.comparativo.pk]),
            reverse('comparativo:finalizar', args=[self.comparativo.pk]),
            reverse('inventario:contagem_criar', args=[self.inv[1].pk]),
            reverse('inventario:finalizar', args=[self.inv[1].pk]),
        ):
            with self.subTest(rota=rota):
                self.assertEqual(self.client.post(rota, {'acao': 'liberar', 'etapa': 1}).status_code, 403)
        self.assertEqual(list(InventarioItem.objects.values_list('pk', 'quantidade_fisica')), antes)

    def test_login_leva_ao_painel(self):
        self.client.logout()
        resposta = self.client.post(reverse('accounts:login'), {'username': 'auditor.a', 'password': 'senha12345'})
        self.assertRedirects(resposta, reverse('auditor:inicio'), fetch_redirect_response=False)


class TelaAuditorTestCase(AuditorBase):
    def setUp(self):
        super().setUp()
        self.publicar_tudo()
        self.client.force_login(self.auditor)

    def test_html_sem_termos_proibidos_e_sem_captura(self):
        for nome in ('painel', 'conteudo'):
            for aba in ('posicao', 'sku', 'historico'):
                with self.subTest(nome=nome, aba=aba):
                    html = self.client.get(self.url(nome) + f'?aba={aba}').content.decode()
                    self.assertEqual(_proibidos(html), [])
                    self.assertNotIn('capturado', html.lower())
                    self.assertNotIn('Contado ', html)
        html = self.client.get(self.url('painel')).content.decode()
        self.assertIn('Portal do Auditor', html)
        self.assertIn('Acompanhamento do inventário', html)
        self.assertIn('Somente leitura', html)
        self.assertNotIn('ao vivo', html.lower())

    def test_tabela_por_posicao_tem_somente_as_colunas_pedidas(self):
        html = self.client.get(self.url('painel')).content.decode()
        cabecalhos = re.findall(r'<th scope="col"[^>]*>([^<]+)', html)
        self.assertEqual(cabecalhos[:7], ['Posição', 'SKU', 'Descrição', 'Contagem 1', 'Contagem 2', 'Contagem 3', 'Status'])
        self.assertEqual(cabecalhos[7:], ['SKU', 'Descrição', 'Contagem 1', 'Contagem 2', 'Contagem 3', 'Status'])
        self.assertIn('aud-status-coincidente', html)
        self.assertIn('aud-status-divergente', html)
        self.assertIn('Liberada em 19/10/2030 às 08:00', html)

    def test_tela_cheia_logo_e_icones_acessiveis(self):
        html = self.client.get(self.url('painel')).content.decode()
        self.assertIn('id="audTelaCheia"', html)
        self.assertIn('aria-pressed="false"', html)
        self.assertIn('img/brida/logo-brida.svg', html)
        self.assertIn('class="aud-logo"', html)
        for rotulo in ('Exportar Excel', 'Legenda', 'Limpar filtros', 'Atualizar'):
            self.assertIn(rotulo, html)
        # Quantidades são botões (teclado) com nome acessível; nada de formulário de edição.
        self.assertRegex(html, r'<button type="button" class="aud-cel-btn" data-aud-detalhe')
        self.assertIn('Ver detalhes"', html)
        self.assertNotIn('method="post"', html.split('<main', 1)[-1].replace('action="/accounts/logout/"', ''))

    def test_api_sem_campos_proibidos_nem_captura(self):
        dados = self.api()
        texto = json.dumps(dados, ensure_ascii=False)
        self.assertEqual(_proibidos(texto), [])
        self.assertNotIn('capturado', texto)
        self.assertEqual(set(dados['linhas'][0]), {'chave', 'posicao', 'sku', 'descricao', 'contagens', 'status', 'status_rotulo'})
        c1 = self.linha('A')['contagens']['C1']
        self.assertEqual(set(c1), {'estado', 'texto', 'tom', 'tom_rotulo', 'quantidade', 'operador', 'disponibilizado_em'})
        self.assertEqual(dados['escopo']['contagens_liberadas'], ['C1', 'C2', 'C3'])

    def test_exportacao_da_visao_selecionada_sem_captura_e_sem_virgula(self):
        pasta, texto = _planilha(self.client.get(self.url('exportar') + '?aba=posicao').content)
        self.assertEqual(pasta.sheetnames, ['Por posição', 'Por SKU', 'Lançamentos'])  # sem aba Resumo
        self.assertEqual(_proibidos(texto), [])
        self.assertNotIn('capturado', texto.lower())
        self.assertEqual([c.value for c in pasta['Por posição'][1]], ['Posição', 'SKU', 'Descrição', 'Contagem 1', 'Contagem 2', 'Contagem 3', 'Status'])
        linhas = {r[1]: r for r in pasta['Por posição'].iter_rows(min_row=2, values_only=True)}
        self.assertEqual(linhas['A'][3:], (10, 10, 'Não necessária', 10))  # coincidente: valor correto
        self.assertEqual(linhas['B'][3:], (5, 7, 'Não contado', 'Divergente'))
        self.assertEqual(linhas['C'][3:], (0, 'Não contado', 'Não contado', 'Sem comparação'))
        pasta_sku, _ = _planilha(self.client.get(self.url('exportar') + '?aba=sku&q=B').content)
        self.assertEqual(pasta_sku.sheetnames, ['Por SKU', 'Por posição', 'Lançamentos'])
        self.assertEqual([c.value for c in pasta_sku['Por SKU'][1]], ['SKU', 'Descrição', 'Contagem 1', 'Contagem 2', 'Contagem 3', 'Status'])
        self.assertEqual([r[0] for r in pasta_sku['Por SKU'].iter_rows(min_row=2, values_only=True)], ['B'])
        for aba in pasta.worksheets + pasta_sku.worksheets:
            self.assertEqual(aba.sheet_state, 'visible')
            for linha in aba.iter_rows(min_row=2):
                for celula in linha:
                    self.assertNotIn(',', celula.number_format, (aba.title, celula.coordinate))

    def test_filtros_e_exportacao_respeitam_escopo(self):
        self.assertEqual([l['sku'] for l in self.api('?q=B')['linhas']], ['B'])
        self.assertEqual([l['sku'] for l in self.api('?q=01.03')['linhas']], ['C'])
        pasta, _ = _planilha(self.client.get(self.url('exportar') + '?status=C1_IGUAL_C2').content)
        self.assertEqual([r[1] for r in pasta['Por posição'].iter_rows(min_row=2, values_only=True)], ['A'])

    def test_polling_sem_mudanca_responde_204(self):
        resposta = self.client.get(self.url('conteudo') + '?aba=sku&q=B')
        versao = resposta['X-Auditor-Versao']
        self.assertEqual(self.client.get(self.url('conteudo') + f'?aba=sku&q=B&versao={versao}').status_code, 204)
        self.assertEqual(self.client.get(self.url('conteudo') + f'?aba=sku&q=B&versao={versao}&forcar=1').status_code, 200)


class CelulasEVisoesTestCase(AuditorBase):
    """Quantidades por posição + SKU, consolidação por SKU e estados das células."""

    def setUp(self):
        super().setUp()
        self.autorizar()
        self.client.force_login(self.auditor)

    def test_estados_zero_nao_contado_nao_liberada_nao_necessaria(self):
        self.liberar(1, 2, 3)
        a, b, c = (self.linha(s)['contagens'] for s in 'ABC')
        self.assertEqual((a['C1']['texto'], a['C2']['texto'], a['C3']['estado']), ('10', '10', 'nao_necessaria'))
        self.assertEqual(b['C3']['estado'], 'nao_contado')  # C1 ≠ C2: C3 é exigida
        self.assertEqual((c['C1']['estado'], c['C1']['quantidade'], c['C1']['texto']), ('valor', '0.000', '0'))
        self.assertEqual(c['C2']['estado'], 'nao_contado')
        self.assertEqual(c['C3']['estado'], 'nao_contado')
        html = self.client.get(self.url('painel')).content.decode()
        for texto in ('Não necessária', 'Não contado'):
            self.assertIn(texto, html)

    def test_uma_linha_por_posicao_e_sku_sem_somar_produtos(self):
        self.lancar(1, 'P1', 'B', '4', self.op1, 40)
        self.liberar(1, 2)
        linhas = [(l['posicao'], l['sku']) for l in self.api()['linhas']]
        self.assertEqual(linhas, [('01.01.101', 'A'), ('01.01.101', 'B'), ('01.02.101', 'B'), ('01.03.101', 'C')])
        p1_b = next(l for l in self.api()['linhas'] if l['posicao'] == '01.01.101' and l['sku'] == 'B')
        self.assertEqual(D(p1_b['contagens']['C1']['quantidade']), D(4))

    def test_por_sku_consolida_e_reconcilia_com_por_posicao(self):
        self.lancar(1, 'P1', 'B', '4', self.op1, 40)
        self.liberar(1, 2)
        painel = montar_painel(self.comparativo)
        sku_b = next(s for s in painel.skus if s['sku'] == 'B')
        self.assertEqual(sku_b['celulas'][1]['quantidade'], D(9))
        self.assertEqual(sku_b['celulas'][2]['quantidade'], D(7))
        self.assertTrue(sku_b['celulas'][2]['parcial'])
        self.assertEqual(sku_b['celulas'][3]['estado'], 'nao_liberada')
        for e in (1, 2):
            for sku in painel.skus:
                soma = sum(
                    (l.celulas[e]['quantidade'] for l in painel.linhas if l.sku == sku['sku'] and l.celulas[e]['estado'] == 'valor'),
                    D(0),
                )
                if sku['celulas'][e]['estado'] == 'valor':
                    self.assertEqual(sku['celulas'][e]['quantidade'], soma, (sku['sku'], e))
        pasta, _ = _planilha(self.client.get(self.url('exportar') + '?aba=sku').content)
        self.assertEqual([c.value for c in pasta['Por SKU'][1]], ['SKU', 'Descrição', 'Contagem 1', 'Contagem 2', 'Status'])
        por_sku = {r[0]: r for r in pasta['Por SKU'].iter_rows(min_row=2, values_only=True)}
        self.assertEqual(por_sku['B'][2:], (9, 7, 'Divergente'))

    def test_velocimetro_formula_e_sem_dados(self):
        self.liberar(1)
        dados = self.api()
        self.assertFalse(dados['indicador']['suficiente'])
        self.assertIsNone(dados['indicador']['percentual'])
        html = self.client.get(self.url('painel')).content.decode()
        self.assertIn('Sem dados suficientes', html)
        self.assertNotIn('0,0%', html)
        self.liberar(2)
        ind = self.api()['indicador']
        # Consenso: só A (C1 = C2) entre A, B, C → 1 de 3.
        self.assertEqual((ind['numerador'], ind['denominador'], ind['suficiente']), (1, 3, True))
        self.assertEqual(D(ind['percentual']).quantize(D('0.01')), D('33.33'))
        html = self.client.get(self.url('painel')).content.decode()
        self.assertIn('33,3%', html)
        self.assertIn('<strong>1</strong> de <strong>3</strong>', html)

    def test_correcao_reflete_na_tabela_e_no_velocimetro(self):
        self.liberar(1, 2)
        self.assertEqual(self.api()['indicador']['numerador'], 1)
        item = InventarioItem.objects.get(inventario=self.inv[2], produto=self.prod['B'])
        salvar_contagem(self.inv[2], self.pos['P2'], self.prod['B'], D(5), item_existente=item, usuario_contagem=self.admin)
        b = self.linha('B')
        self.assertEqual(D(b['contagens']['C2']['quantidade']), D(5))
        self.assertEqual(b['contagens']['C2']['operador'], 'Bianca')  # autor original preservado
        self.assertEqual(self.api()['indicador']['numerador'], 2)
        html = self.client.get(self.url('painel') + '?aba=historico').content.decode()
        self.assertIn('Correção autorizada', html)
        self.assertNotIn('antes 7', html)
        item.refresh_from_db()
        self.assertEqual(item.usuario_registro_id, self.op2.pk)

    def test_cancelamento_de_registro_sai_da_visao(self):
        self.liberar(1)
        item = InventarioItem.objects.get(inventario=self.inv[1], produto=self.prod['C'])
        excluir_contagem(item, usuario=self.admin)
        self.assertNotIn('C', [l['sku'] for l in self.api()['linhas']])


class LiberacaoPorContagemTestCase(AuditorBase):
    """Validação obrigatória: liberação independente de C1, C2 e C3."""

    def setUp(self):
        super().setUp()
        self.autorizar()
        self.client.force_login(self.auditor)

    def test_por_padrao_nada_liberado_e_c1_privada_bloqueia(self):
        self.assertFalse(LiberacaoContagemAuditor.objects.exists())
        for nome in ('painel', 'conteudo', 'api', 'exportar'):
            self.assertEqual(self.client.get(self.url(nome)).status_code, 403)
        resposta = self.client.get(reverse('auditor:inicio'))
        self.assertEqual(resposta.status_code, 200)
        self.assertNotIn('Conferência auditada', resposta.content.decode())

    def test_c1_liberada_c2_e_c3_privadas(self):
        self.liberar(1)
        self.assertEqual(dict(LiberacaoContagemAuditor.objects.values_list('etapa', 'status')), {1: 'LIBERADA'})
        dados = self.api()
        self.assertEqual(dados['escopo']['contagens_liberadas'], ['C1'])
        for linha in dados['linhas']:
            self.assertEqual(linha['contagens']['C1']['estado'], 'valor')
            for etapa in ('C2', 'C3'):
                celula = linha['contagens'][etapa]
                self.assertEqual((celula['estado'], celula['texto']), ('nao_liberada', 'Não liberada'))
                self.assertNotIn('quantidade', celula)
        self.assertNotIn('Bianca', json.dumps(dados))
        self.assertIsNone(dados['cards']['exigem_terceira'])
        html = self.client.get(self.url('painel') + '?aba=sku').content.decode()
        self.assertIn('Não liberada', html)
        self.assertIn('Abrangência', html)
        self.assertNotIn('Bianca', html)

    def test_novos_registros_de_c2_privada_nao_alteram_o_auditor(self):
        self.liberar(1)
        versao = self.client.get(self.url('conteudo'))['X-Auditor-Versao']
        antes = self.api()
        self.lancar(2, 'P3', 'C', '3', self.op2, 50)
        self.lancar(2, 'P2', 'C', '9', self.op2, 51)
        self.assertEqual(self.client.get(self.url('conteudo') + f'?versao={versao}').status_code, 204)
        depois = self.api()
        self.assertEqual((depois['cards'], depois['indicador']), (antes['cards'], antes['indicador']))

    def test_c2_liberada_durante_execucao_mostra_existentes_e_novos(self):
        self.liberar(1)
        self.liberar(2, agora=T0 + timedelta(minutes=30))
        b = self.linha('B')['contagens']['C2']
        self.assertEqual(D(b['quantidade']), D(7))
        self.assertTrue(b['disponibilizado_em'].startswith('2030-10-19T08:30'))
        self.lancar(2, 'P3', 'C', '0', self.op2, 0, momento=T0 + timedelta(hours=1))
        c = self.linha('C')
        self.assertEqual((c['contagens']['C2']['texto'], c['contagens']['C2']['operador']), ('0', 'Bianca'))
        self.assertTrue(c['contagens']['C2']['disponibilizado_em'].startswith('2030-10-19T09:00'))
        self.assertEqual(c['status'], 'C1_IGUAL_C2')

    def test_c3_liberada_independentemente(self):
        self.lancar(3, 'P2', 'B', '6', self.op1, 90)
        self.liberar(1, 2)
        self.assertEqual(self.linha('B')['contagens']['C3']['estado'], 'nao_liberada')
        self.liberar(3)
        self.assertEqual(D(self.linha('B')['contagens']['C3']['quantidade']), D(6))
        self.assertEqual(LiberacaoContagemAuditor.objects.filter(status='LIBERADA').count(), 3)

    def test_suspensao_interrompe_url_direta_e_atualizacao(self):
        self.liberar(1, 2)
        versao = self.client.get(self.url('conteudo'))['X-Auditor-Versao']
        with self.assertRaises(pub.PublicacaoError):
            pub.suspender_contagem(self.comparativo, 2, self.admin, '')
        pub.suspender_contagem(self.comparativo, 2, self.admin, 'Recontagem em andamento', agora=T0 + timedelta(hours=1))
        resposta = self.client.get(self.url('conteudo') + f'?versao={versao}')
        self.assertEqual(resposta.status_code, 200)
        self.assertNotIn('Bianca', resposta.content.decode())
        self.assertTrue(all(l['contagens']['C2']['estado'] == 'nao_liberada' for l in self.api()['linhas']))
        liberacao = LiberacaoContagemAuditor.objects.get(etapa=2)
        self.assertEqual((liberacao.status, liberacao.motivo_suspensao, liberacao.suspenso_por_id), ('SUSPENSA', 'Recontagem em andamento', self.admin.pk))
        self.assertEqual(EventoAuditoria.objects.get(tipo='CONTAGEM_SUSPENSA').dados['motivo'], 'Recontagem em andamento')
        pub.suspender_contagem(self.comparativo, 1, self.admin, 'Encerrado')
        for nome in ('painel', 'conteudo', 'api', 'exportar'):
            self.assertEqual(self.client.get(self.url(nome) + f'?versao={versao}').status_code, 403)
        self.assertEqual(InventarioItem.objects.count(), 5)

    def test_exportacao_e_cache_com_as_mesmas_permissoes(self):
        self.liberar(1)
        resposta = self.client.get(self.url('exportar'))
        self.assertIn('no-store', resposta['Cache-Control'])
        self.assertIn('private', resposta['Cache-Control'])
        pasta, texto = _planilha(resposta.content)
        self.assertNotIn('Bianca', texto)
        # Contagens privadas não geram colunas, abas ou metadados no arquivo.
        self.assertEqual([c.value for c in pasta['Por posição'][1]], ['Posição', 'SKU', 'Descrição', 'Contagem 1', 'Status'])
        self.assertEqual([c.value for c in pasta['Por SKU'][1]], ['SKU', 'Descrição', 'Contagem 1', 'Status'])
        self.assertEqual({r[3] for r in pasta['Lançamentos'].iter_rows(min_row=2, values_only=True)}, {'C1'})
        self.assertNotIn('Contagem 2', texto)
        self.assertNotIn('C2', texto)

    def test_mudancas_no_inventario_nao_liberam_contagens(self):
        self.liberar(1)
        Inventario.objects.filter(pk=self.inv[2].pk).update(status=Inventario.Status.FINALIZADO)
        self.autorizar()
        self.assertTrue(all(l['contagens']['C2']['estado'] == 'nao_liberada' for l in self.api()['linhas']))
        self.liberar(2)
        novo = Inventario.objects.create(usuario=self.operacional, status=Inventario.Status.EM_ANDAMENTO)
        InventarioItem.objects.create(
            inventario=novo, posicao=self.pos['P1'], produto=self.prod['A'], quantidade_fisica=D(99),
            usuario_contagem=self.op2, data_contagem=T0, usuario_registro=self.op2, data_registro=T0,
        )
        self.comparativo.associacoes.filter(ordem=2).update(inventario=novo)
        self.assertTrue(all(l['contagens']['C2']['estado'] == 'nao_liberada' for l in self.api()['linhas']))
        estado = {c['etapa']: c for c in pub.estado_contagens(self.comparativo)}
        self.assertTrue(estado[2]['inventario_trocado'])
        self.assertEqual(estado[2]['status'], 'NAO_LIBERADA')

    def test_dois_inventarios_com_configuracoes_independentes(self):
        outra = salvar_comparativo(
            comparativo=None, nome='Segunda conferência', inventarios={1: self.inv[1], 2: self.inv[2], 3: None},
            referencia_opcao='', estado=None, usuario=self.admin,
        )
        self.autorizar(comparativo=outra)
        self.liberar(2, comparativo=outra)
        self.liberar(1)
        self.assertTrue(all(l['contagens']['C2']['estado'] == 'nao_liberada' for l in self.api()['linhas']))
        segunda = self.client.get(self.url('api', outra.pk)).json()
        self.assertTrue(all(l['contagens']['C1']['estado'] == 'nao_liberada' for l in segunda['linhas']))
        self.assertEqual(sorted(l['sku'] for l in segunda['linhas']), ['A', 'B'])
        self.assertTrue(all(l['contagens']['C3']['estado'] == 'nao_associada' for l in segunda['linhas']))
        pub.suspender_contagem(outra, 2, self.admin, 'Somente a segunda')
        self.assertEqual(self.client.get(self.url('api')).status_code, 200)
        self.assertEqual(self.client.get(self.url('api', outra.pk)).status_code, 403)

    def test_configuracao_anterior_e_apresentada_e_nao_convertida(self):
        PublicacaoAuditoria.objects.filter(comparativo=self.comparativo).update(liberado=True, status='CONCLUIDA')
        self.assertEqual(self.client.get(self.url('painel')).status_code, 403)
        self.client.force_login(self.admin)
        html = self.client.get(reverse('comparativo:auditoria', args=[self.comparativo.pk])).content.decode()
        self.assertIn('Configuração anterior encontrada', html)
        self.assertIn('Publicação programada geral anterior', html)
        self.assertFalse(LiberacaoContagemAuditor.objects.exists())


class ProgramacaoPorContagemTestCase(AuditorBase):
    """Liberação programada definida em cada contagem (ex.: C1 em 60 min)."""

    def setUp(self):
        super().setUp()
        self.autorizar()

    def test_c1_programada_e_c2_em_tempo_real_independentes(self):
        c1 = self.programar(1, duracao=60)
        self.liberar(2)
        self.assertEqual((c1.programacao, c1.total_programado), ('EM_LIBERACAO', 3))
        self.assertEqual(c1.termino_previsto, T0 + timedelta(minutes=60))
        painel = montar_painel(self.comparativo)
        self.assertEqual({l.etapa for l in painel.lancamentos}, {2})  # C1 ainda nos lotes; C2 já toda
        pub.processar(agora=T0 + timedelta(minutes=20))
        painel = montar_painel(self.comparativo)
        self.assertEqual(sum(1 for l in painel.lancamentos if l.etapa == 1), 1)
        pub.processar(agora=T0 + timedelta(minutes=60))
        c1.refresh_from_db()
        self.assertEqual((c1.total_publicado, c1.programacao), (3, 'CONCLUIDA'))
        self.assertEqual(LiberacaoContagemAuditor.objects.get(etapa=2).programacao, '')

    def test_status_da_descarga_para_gestor_e_auditor(self):
        self.programar(1, duracao=60)
        pub.processar(agora=T0 + timedelta(minutes=40))
        self.client.force_login(self.admin)
        html = self.client.get(reverse('comparativo:auditoria', args=[self.comparativo.pk])).content.decode()
        self.assertIn('Em liberação', html)
        self.assertIn('2 de 3', html)
        self.assertIn('Pausar', html)
        self.client.force_login(self.auditor)
        dados = self.api()
        c1 = next(c for c in dados['contagens'] if c['rotulo'] == 'C1')
        self.assertEqual((c1['estado'], c1['liberados'], c1['programados']), ('programada', 2, 3))
        html = self.client.get(self.url('painel')).content.decode()
        self.assertNotIn('Resultados coletados anteriormente', html)  # cartão retirado a pedido
        self.assertIn('2 de 3 registros disponibilizados', html)
        self.assertNotIn('ao vivo', html.lower())

    def test_programada_inicia_no_horario_agendado(self):
        inicio = T0 + timedelta(minutes=10)
        c1 = self.programar(1, inicio=inicio)
        self.assertEqual(c1.programacao, 'AGENDADA')
        self.assertEqual(pub.processar(agora=inicio - timedelta(seconds=1)), 0)
        self.assertEqual(montar_painel(self.comparativo).lancamentos, [])
        pub.processar(agora=inicio + timedelta(seconds=1))
        c1.refresh_from_db()
        self.assertEqual(c1.programacao, 'EM_LIBERACAO')
        pub.processar(agora=inicio + timedelta(minutes=60))
        c1.refresh_from_db()
        self.assertEqual((c1.total_publicado, c1.programacao), (3, 'CONCLUIDA'))

    def test_573_linhas_em_lotes_pequenos(self):
        produtos = [Produto(codigo_produto=f'Z{i:03d}', descricao=f'Item {i}', setor='X') for i in range(285)]
        Produto.objects.bulk_create(produtos)
        produtos = list(Produto.objects.filter(codigo_produto__startswith='Z').order_by('codigo_produto'))
        momento = T0 - timedelta(days=1)
        itens = [
            InventarioItem(
                inventario=self.inv[1], posicao=self.pos[p], produto=prod, quantidade_fisica=D(1),
                usuario_contagem=self.op1, data_contagem=momento, usuario_registro=self.op1, data_registro=momento,
            )
            for p in ('P1', 'P2') for prod in produtos
        ]
        InventarioItem.objects.bulk_create(itens)
        c1 = self.programar(1, ordem='EQUILIBRADA')
        self.assertEqual(c1.total_programado, 573)
        self.assertEqual(PublicacaoAuditoriaItem.objects.filter(liberacao=c1).values('lote').distinct().count(), 240)
        pub.processar(agora=T0 + timedelta(minutes=30))
        c1.refresh_from_db()
        self.assertTrue(280 <= c1.total_publicado <= 293, c1.total_publicado)
        pub.processar(agora=T0 + timedelta(minutes=60))
        c1.refresh_from_db()
        self.assertEqual((c1.total_publicado, c1.programacao), (573, 'CONCLUIDA'))
        self.assertEqual(EventoAuditoria.objects.filter(tipo='LOTE_PUBLICADO').count(), 240)

    def test_pausar_retomar_sem_duplicar(self):
        self.programar(1)
        pub.processar(agora=T0 + timedelta(minutes=13))
        pub.pausar_contagem(self.comparativo, 1, self.admin, agora=T0 + timedelta(minutes=13))
        publicados = LiberacaoContagemAuditor.objects.get(etapa=1).total_publicado
        self.assertEqual(pub.processar(agora=T0 + timedelta(minutes=50)), 0)
        self.assertEqual(LiberacaoContagemAuditor.objects.get(etapa=1).total_publicado, publicados)
        c1 = pub.retomar_contagem(self.comparativo, 1, self.admin, agora=T0 + timedelta(minutes=33))
        self.assertEqual(c1.termino_previsto, T0 + timedelta(minutes=80))
        pub.processar(agora=T0 + timedelta(minutes=80))
        c1.refresh_from_db()
        self.assertEqual((c1.total_publicado, c1.programacao), (3, 'CONCLUIDA'))
        self.assertEqual(PublicacaoAuditoriaItem.objects.filter(status='PUBLICADO').count(), 3)
        tipos = list(EventoAuditoria.objects.values_list('tipo', flat=True))
        self.assertIn('PAUSADA', tipos)
        self.assertIn('RETOMADA', tipos)

    def test_reinicio_e_dois_processos_nao_duplicam(self):
        c1 = self.programar(1)
        agora = T0 + timedelta(minutes=40)
        n1 = pub._publicar_vencidos(LiberacaoContagemAuditor.objects.get(pk=c1.pk), agora)
        n2 = pub._publicar_vencidos(LiberacaoContagemAuditor.objects.get(pk=c1.pk), agora)
        self.assertGreater(n1, 0)
        self.assertEqual(n2, 0)
        eventos = EventoAuditoria.objects.filter(tipo='LOTE_PUBLICADO').count()
        self.assertEqual(pub.processar(agora=agora), 0)
        self.assertEqual(EventoAuditoria.objects.filter(tipo='LOTE_PUBLICADO').count(), eventos)

    def test_indisponibilidade_libera_somente_vencidos(self):
        self.programar(1)
        agora = T0 + timedelta(minutes=45)
        pub.processar(agora=agora)
        vencidos = PublicacaoAuditoriaItem.objects.filter(previsto_para__lte=agora).count()
        self.assertEqual(PublicacaoAuditoriaItem.objects.filter(status='PUBLICADO').count(), vencidos)
        self.assertTrue(PublicacaoAuditoriaItem.objects.filter(status='PENDENTE').exists())
        self.assertFalse(PublicacaoAuditoriaItem.objects.filter(status='PUBLICADO').exclude(publicado_em=agora).exists())

    def test_interromper_e_concluir_agora(self):
        self.programar(1)
        self.programar(2)
        pub.processar(agora=T0 + timedelta(minutes=20))
        c1 = pub.interromper_contagem(self.comparativo, 1, self.admin, agora=T0 + timedelta(minutes=21))
        publicados = c1.total_publicado
        pub.processar(agora=T0 + timedelta(minutes=90))
        c1.refresh_from_db()
        self.assertEqual((c1.total_publicado, c1.programacao), (publicados, 'INTERROMPIDA'))
        c2 = LiberacaoContagemAuditor.objects.get(etapa=2)
        self.assertEqual((c2.total_publicado, c2.programacao), (2, 'CONCLUIDA'))
        self.liberar(3, modo=PROGRAMADA, duracao_minutos=60)
        self.lancar(3, 'P2', 'B', '6', self.op1, 90)
        self.assertEqual(LiberacaoContagemAuditor.objects.get(etapa=3).programacao, 'CONCLUIDA')  # nada a programar

    def test_concluir_agora_publica_restante(self):
        self.programar(1)
        c1 = pub.concluir_contagem(self.comparativo, 1, self.admin, agora=T0 + timedelta(minutes=5))
        self.assertEqual((c1.total_publicado, c1.programacao), (3, 'CONCLUIDA'))

    def test_registros_novos_apos_inicio_aparecem_conforme_gravados(self):
        self.programar(1)
        self.client.force_login(self.auditor)
        novo = self.lancar(1, 'P3', 'A', '2', self.op1, 0, momento=T0 + timedelta(minutes=5))
        linhas = {(l['posicao'], l['sku']): l for l in self.api()['linhas']}
        self.assertIn(('01.03.101', 'A'), linhas)
        self.assertFalse(PublicacaoAuditoriaItem.objects.filter(item_original_id=novo.pk).exists())

    def test_original_intacto_e_somente_disponibilizado_exposto(self):
        antes = list(InventarioItem.objects.order_by('pk').values(
            'pk', 'quantidade_fisica', 'usuario_registro_id', 'data_registro', 'usuario_contagem_id', 'data_contagem',
        ))
        self.programar(1)
        pub.processar(agora=T0 + timedelta(minutes=60))
        self.liberar(2)
        depois = list(InventarioItem.objects.order_by('pk').values(
            'pk', 'quantidade_fisica', 'usuario_registro_id', 'data_registro', 'usuario_contagem_id', 'data_contagem',
        ))
        self.assertEqual(antes, depois)
        self.client.force_login(self.auditor)
        c1 = self.linha('A')['contagens']['C1']
        self.assertNotIn('capturado_em', c1)
        self.assertTrue(c1['disponibilizado_em'].startswith('2030-10-19'))
        self.assertEqual(c1['operador'], 'Carlos')

    def test_nova_liberacao_abre_novo_ciclo_e_preserva_o_anterior(self):
        self.programar(1)
        pub.processar(agora=T0 + timedelta(minutes=60))
        pub.suspender_contagem(self.comparativo, 1, self.admin, 'Revisão da gestão')
        c1 = self.programar(1, agora=T0 + timedelta(hours=2))
        self.assertEqual(c1.ciclo, 2)
        self.assertEqual(PublicacaoAuditoriaItem.objects.filter(liberacao=c1, ciclo=1, status='PUBLICADO').count(), 3)
        self.assertEqual(PublicacaoAuditoriaItem.objects.filter(liberacao=c1, ciclo=2).count(), 3)
        self.assertEqual(montar_painel(self.comparativo).lancamentos, [])  # ciclo novo recomeça a agenda

    def test_eventos_imutaveis_e_conferencia_protegida(self):
        self.programar(1)
        evento = EventoAuditoria.objects.first()
        with self.assertRaises(ValueError):
            evento.save()
        with self.assertRaises(ValueError):
            evento.delete()
        with self.assertRaises(ComparativoError):
            excluir_comparativo(self.comparativo, self.admin)


class TelaGestorTestCase(AuditorBase):
    def test_configuracao_por_contagem_e_visualizar_como_auditor(self):
        self.client.force_login(self.admin)
        url = reverse('comparativo:auditoria', args=[self.comparativo.pk])
        self.client.post(url, {'acao': 'salvar', 'auditores': [self.auditor.pk]})
        self.assertTrue(AuditorAcesso.objects.filter(auditor=self.auditor, ativo=True).exists())
        html = self.client.get(url).content.decode()
        self.assertEqual(html.count('Confirmar liberação de C'), 3)
        self.assertIn('Alcance:', html)
        self.client.post(url, {'acao': 'liberar', 'etapa': 1, 'modo': PROGRAMADA, 'duracao_minutos': 60, 'ordem': 'POSICAO', 'inicio_previsto': ''})
        self.client.post(url, {'acao': 'liberar', 'etapa': 2, 'modo': 'TEMPO_REAL', 'duracao_minutos': '', 'ordem': '', 'inicio_previsto': ''})
        estados = {l.etapa: (l.modo, l.duracao_minutos) for l in LiberacaoContagemAuditor.objects.all()}
        self.assertEqual(estados, {1: (PROGRAMADA, 60), 2: ('TEMPO_REAL', 60)})
        self.client.post(url, {'acao': 'pausar', 'etapa': 1})
        self.assertEqual(LiberacaoContagemAuditor.objects.get(etapa=1).programacao, 'PAUSADA')
        self.client.post(url, {'acao': 'retomar', 'etapa': 1})
        self.client.post(url, {'acao': 'concluir', 'etapa': 1})
        self.assertEqual(LiberacaoContagemAuditor.objects.get(etapa=1).programacao, 'CONCLUIDA')
        self.client.post(url, {'acao': 'suspender', 'etapa': 2, 'motivo': ''})
        self.assertEqual(LiberacaoContagemAuditor.objects.get(etapa=2).status, 'LIBERADA')  # motivo obrigatório
        previa = self.client.get(reverse('comparativo:auditoria_previa', args=[self.comparativo.pk]))
        self.assertEqual(previa.status_code, 200)
        texto = previa.content.decode()
        self.assertIn('Visualizar como auditor', texto)
        self.assertIn('Pré-visualização do gestor', texto)
        self.assertEqual(_proibidos(texto.split('<main', 1)[-1]), [])
        self.assertEqual(self.client.get(reverse('comparativo:auditoria_previa_conteudo', args=[self.comparativo.pk])).status_code, 200)
        self.client.post(url, {'acao': 'suspender', 'etapa': 2, 'motivo': 'Fim da auditoria'})
        self.assertEqual(LiberacaoContagemAuditor.objects.get(etapa=2).status, 'SUSPENSA')
        resposta = self.client.get(url)
        self.assertIn('Fim da auditoria', resposta.content.decode())
        self.assertIn('Processo automático', resposta.content.decode())
        consulta, _ = criar_usuario_teste('consulta.aud', perfil=Usuario.Perfil.CONSULTA)
        self.client.force_login(consulta)
        self.client.post(url, {'acao': 'liberar', 'etapa': 3, 'modo': 'TEMPO_REAL'})
        self.assertFalse(LiberacaoContagemAuditor.objects.filter(etapa=3).exists())


class StatusGraficosEDetalhesTestCase(AuditorBase):
    """Cores de status, gráficos de registros, participação, horário real e detalhes."""

    def setUp(self):
        super().setUp()
        self.autorizar()
        self.client.force_login(self.auditor)

    def permitir_horario_real(self, valor=True):
        pub.configurar(self.comparativo, self.admin, auditores=[self.auditor], mostrar_horario_real=valor)

    def test_cores_de_status_pela_regra_existente(self):
        self.lancar(3, 'P2', 'B', '5', self.op1, 90)  # C1 = C3 ≠ C2
        self.liberar(1, 2, 3)
        a, b, c = (self.linha(sku)['contagens'] for sku in 'ABC')
        self.assertEqual((a['C1']['tom'], a['C2']['tom'], a['C3']['tom']), ('confere', 'confere', 'nao_necessaria'))
        self.assertEqual((b['C1']['tom'], b['C2']['tom'], b['C3']['tom']), ('confere', 'difere', 'confere'))
        self.assertEqual((c['C1']['tom'], c['C2']['tom']), ('registrado', 'nao_contado'))
        self.assertEqual(c['C1']['texto'], '0')  # zero registrado ≠ ausência

    def test_divergencia_aguardando_e_sem_concordancia_com_contagem_privada(self):
        self.liberar(1)
        b = self.linha('B')['contagens']
        self.assertEqual(b['C1']['tom'], 'registrado')  # C2 privada não confirma nem diverge
        self.liberar(2)
        b = self.linha('B')['contagens']
        self.assertEqual((b['C1']['tom'], b['C2']['tom']), ('divergente', 'divergente'))
        html = self.client.get(self.url('painel')).content.decode()
        self.assertIn('Divergência aguardando resolução', html)
        self.assertIn('bi-exclamation-circle-fill', html)

    def test_totais_iguais_por_sku_nao_escondem_divergencia_entre_posicoes(self):
        self.prod['D'] = Produto.objects.create(codigo_produto='D', descricao='Produto D', setor='X')
        self.lancar(1, 'P1', 'D', '5', self.op1, 60)
        self.lancar(2, 'P1', 'D', '3', self.op2, 61)
        self.lancar(1, 'P2', 'D', '3', self.op1, 62)
        self.lancar(2, 'P2', 'D', '5', self.op2, 63)
        self.liberar(1, 2)
        sku_d = next(s for s in self.api()['skus'] if s['sku'] == 'D')
        self.assertEqual((sku_d['contagens']['C1']['texto'], sku_d['contagens']['C2']['texto']), ('8', '8'))
        self.assertEqual(sku_d['contagens']['C1']['tom'], 'divergente')
        self.assertEqual(sku_d['contagens']['C1']['tom_rotulo'], 'Divergência em 2 de 2 posição(ões)')
        sku_a = next(s for s in self.api()['skus'] if s['sku'] == 'A')
        self.assertEqual(sku_a['contagens']['C1']['tom'], 'confere')

    def test_sku_com_zeros_a_esquerda_fica_texto_e_quantidade_numerica(self):
        self.prod['Z'] = Produto.objects.create(codigo_produto='000777', descricao='Graxa', setor='X')
        self.lancar(1, 'P3', 'Z', '12', self.op1, 70)
        self.liberar(1)
        for aba in ('posicao', 'sku'):
            pasta, _ = _planilha(self.client.get(self.url('exportar') + f'?aba={aba}').content)
            planilha = pasta.worksheets[0]
            coluna_sku = 2 if aba == 'posicao' else 1
            linha = next(r for r in planilha.iter_rows(min_row=2) if r[coluna_sku - 1].value == '000777')
            self.assertEqual(linha[coluna_sku - 1].data_type, 's')
            self.assertEqual(linha[-2].value, 12)
            self.assertEqual(linha[-2].data_type, 'n')
            self.assertEqual(linha[-1].value, 'Sem comparação')

    def test_horario_real_desligado_nao_expoe_coleta(self):
        self.liberar(1, 2)
        dados = self.api()
        self.assertNotIn('registrado_em', json.dumps(dados))
        html = self.client.get(self.url('painel')).content.decode()
        self.assertIn('Horário da coleta não liberado pela gestão', html)
        self.assertNotIn('17/10/2030', html)  # data real da coleta (T0 − 2 dias)
        self.assertIn('Participação por contador', html)
        self.assertNotIn('Evolução da contagem', html)  # gráfico temporal retirado

    def test_horario_real_ligado_aparece_so_no_detalhe(self):
        self.permitir_horario_real()
        self.liberar(1, 2, agora=T0 + timedelta(days=1))
        html = self.client.get(self.url('painel')).content.decode()
        self.assertIn('17/10/2030 08:00', html)  # detalhe: data real da coleta
        self.assertIn('data-liberado="Liberada em 20/10/2030 às 08:00"', html)
        # Correção não conta como novo item na participação.
        item = InventarioItem.objects.get(inventario=self.inv[1], produto=self.prod['B'])
        salvar_contagem(self.inv[1], self.pos['P2'], self.prod['B'], D(6), item_existente=item, usuario_contagem=self.admin)
        c1 = next(c for c in self.api()['participacao'] if c['contagem'] == 'C1')
        self.assertEqual((c1['registros'], c1['posicoes']), (3, 3))

    def test_colunas_respeitam_liberacao_e_programacao(self):
        self.programar(1, duracao=60)
        dados = self.api()
        self.assertEqual([c['contagem'] for c in dados['participacao']], ['C1'])  # C2 e C3 privadas
        self.assertEqual(dados['participacao'][0]['registros'], 0)  # nada publicado ainda
        pub.processar(agora=T0 + timedelta(minutes=20))
        self.assertEqual(self.api()['participacao'][0]['registros'], 1)
        html = self.client.get(self.url('painel')).content.decode()
        self.assertEqual(html.count('Contagem não liberada</strong>'), 2)  # colunas C2 e C3

    def test_participacao_por_contador_e_filtro(self):
        self.liberar(1, 2)
        item = InventarioItem.objects.get(inventario=self.inv[2], produto=self.prod['B'])
        salvar_contagem(self.inv[2], self.pos['P2'], self.prod['B'], D(5), item_existente=item, usuario_contagem=self.admin)
        graficos = {c['contagem']: c for c in self.api()['participacao']}
        self.assertEqual(graficos['C1']['contadores'], [{'nome': 'Carlos', 'itens': 3, 'correcoes': 0}])
        c2 = {c['nome']: c for c in graficos['C2']['contadores']}
        self.assertEqual((c2['Bianca']['itens'], c2['Bianca']['correcoes']), (2, 0))
        self.assertEqual(c2['Usuário admin.aud']['correcoes'] if 'Usuário admin.aud' in c2 else next(
            v['correcoes'] for k, v in c2.items() if k != 'Bianca'), 1)
        self.assertNotIn('admin.aud@', json.dumps(graficos))
        filtrado = self.api('?operador=Bianca&etapa=2')
        self.assertEqual(sorted(l['sku'] for l in filtrado['linhas']), ['A', 'B'])
        html = self.client.get(self.url('painel') + '?operador=Bianca&etapa=2').content.decode()
        self.assertIn('Limpar seleção', html)
        self.assertIn('Mostrando registros de <strong>Bianca</strong>', html)

    def test_gestor_controla_horario_real(self):
        self.client.force_login(self.admin)
        url = reverse('comparativo:auditoria', args=[self.comparativo.pk])
        self.assertIn('Mostrar ao auditor o horário real das contagens', self.client.get(url).content.decode())
        self.client.post(url, {'acao': 'salvar', 'auditores': [self.auditor.pk], 'mostrar_horario_real': 'on'})
        self.assertTrue(PublicacaoAuditoria.objects.get().mostrar_horario_real)
        evento = EventoAuditoria.objects.filter(tipo='CONFIGURADA').latest('pk')
        self.assertEqual(evento.dados['alteracoes']['mostrar_horario_real'], [False, True])

    def test_data_de_liberacao_independente_por_contagem(self):
        self.liberar(1)
        self.liberar(3, agora=T0 + timedelta(hours=5, minutes=7))
        html = self.client.get(self.url('painel')).content.decode()
        self.assertIn('Liberada em 19/10/2030 às 08:00', html)
        self.assertIn('Liberada em 19/10/2030 às 13:07', html)
        self.assertEqual(html.count('Não liberada</span></th>'), 2)  # C2 nas duas tabelas
        pub.suspender_contagem(self.comparativo, 1, self.admin, 'Revisão')
        self.liberar(1, agora=T0 + timedelta(days=1, hours=1))
        html = self.client.get(self.url('painel')).content.decode()
        self.assertIn('Liberada em 20/10/2030 às 09:00', html)  # liberação vigente
        self.assertTrue(EventoAuditoria.objects.filter(tipo='CONTAGEM_SUSPENSA').exists())
        self.assertEqual(EventoAuditoria.objects.filter(tipo='CONTAGEM_LIBERADA', dados__etapa=1).count(), 2)


class GraficoTodasEExcelCompletoTestCase(AuditorBase):
    def setUp(self):
        super().setUp()
        pub.configurar(self.comparativo, self.admin, auditores=[self.auditor], mostrar_horario_real=True)
        self.client.force_login(self.auditor)

    def test_tres_colunas_lado_a_lado(self):
        self.lancar(3, 'P2', 'B', '6', self.op1, 90)
        self.liberar(1, 2, 3)
        participacao = {c['contagem']: c for c in self.api()['participacao']}
        self.assertEqual({k: v['registros'] for k, v in participacao.items()}, {'C1': 3, 'C2': 2, 'C3': 1})
        html = self.client.get(self.url('painel')).content.decode()
        self.assertEqual(html.count('class="aud-card aud-coluna"'), 3)
        for e in (1, 2, 3):
            self.assertIn(f'id="audCol{e}"', html)
        self.assertNotIn('data-aud-ge=', html)  # sem seletor de gráfico

    def test_c3_privada_aparece_bloqueada(self):
        self.liberar(1, 2)
        html = self.client.get(self.url('painel')).content.decode()
        self.assertIn('Contagem não liberada</strong>', html)
        self.assertEqual([c['contagem'] for c in self.api()['participacao']], ['C1', 'C2'])

    def test_lancamentos_com_coleta_real_e_previa_do_gestor_exporta(self):
        self.liberar(1)
        pasta, _ = _planilha(self.client.get(self.url('exportar')).content)
        cab = [c.value for c in pasta['Lançamentos'][1]]
        self.assertNotIn('Data e hora reais da coleta', cab)  # coluna retirada de todos os relatórios
        self.assertIn('Liberação da contagem', cab)
        pub.configurar(self.comparativo, self.admin, auditores=[self.auditor], mostrar_horario_real=False)
        pasta, _ = _planilha(self.client.get(self.url('exportar')).content)
        self.assertNotIn('Data e hora reais da coleta', [c.value for c in pasta['Lançamentos'][1]])
        self.client.force_login(self.admin)
        previa = self.client.get(reverse('comparativo:auditoria_previa', args=[self.comparativo.pk])).content.decode()
        self.assertIn('id="audExportar"', previa)
        resposta = self.client.get(reverse('comparativo:auditoria_previa_exportar', args=[self.comparativo.pk]) + '?aba=sku')
        self.assertEqual(resposta.status_code, 200)
        pasta, texto = _planilha(resposta.content)
        self.assertEqual(pasta.sheetnames, ['Por SKU', 'Por posição', 'Lançamentos'])
        self.assertNotIn('Bianca', texto)  # C2 privada também fora do Excel da prévia
        self.client.force_login(self.auditor)
        self.assertEqual(self.client.get(reverse('comparativo:auditoria_previa_exportar', args=[self.comparativo.pk])).status_code, 403)


class ExcelStatusTestCase(AuditorBase):
    def test_status_coincidente_divergente_e_cores(self):
        self.autorizar()
        self.prod['D'] = Produto.objects.create(codigo_produto='D', descricao='Produto D', setor='X')
        # D: totais iguais (8/8) mas posições divergentes → Divergente no SKU.
        self.lancar(1, 'P1', 'D', '5', self.op1, 60)
        self.lancar(2, 'P1', 'D', '3', self.op2, 61)
        self.lancar(1, 'P2', 'D', '3', self.op1, 62)
        self.lancar(2, 'P2', 'D', '5', self.op2, 63)
        self.liberar(1, 2)
        self.client.force_login(self.auditor)
        pasta, _ = _planilha(self.client.get(self.url('exportar') + '?aba=sku').content)
        aba = pasta['Por SKU']
        linhas = {r[0].value: r for r in aba.iter_rows(min_row=2)}
        self.assertEqual([c.value for c in linhas['A'][2:]], [10, 10, 10])  # valor correto do SKU
        self.assertEqual(linhas['A'][4].data_type, 'n')
        self.assertEqual([c.value for c in linhas['D'][2:]], [8, 8, 'Divergente'])
        self.assertEqual(linhas['A'][2].fill.fgColor.rgb[-6:], 'E9F7EE')  # verde
        self.assertEqual(linhas['D'][4].fill.fgColor.rgb[-6:], 'FFF1E8')  # laranja
        lanc = pasta['Lançamentos']
        cab = [c.value for c in lanc[1]]
        self.assertIn('Status', cab)
        self.assertNotIn('Data e hora reais da coleta', cab)
        status_b = {r[cab.index('Contagem')]: r[cab.index('Status')]
                    for r in lanc.iter_rows(min_row=2, values_only=True) if r[1] == 'B'}
        self.assertEqual(status_b, {'C1': 'Divergente', 'C2': 'Divergente'})


class CadastroUsuarioAuditorTestCase(AuditorBase):
    def test_cadastro_do_auditor_define_a_conferencia_acompanhada(self):
        self.client.force_login(self.admin)
        url = reverse('accounts:usuarios_criar')
        html = self.client.get(url).content.decode()
        self.assertIn('Conferências que o auditor vai acompanhar', html)
        self.assertIn('Conferência auditada', html)
        self.assertIn('C1 · Inventário #', html)
        resposta = self.client.post(url, {
            'nome': 'Auditor Externo', 'login': 'auditor.externo', 'setor': 'Auditoria', 'perfil': 'AUDITOR',
            'ativo': 'on', 'senha': 'senha-segura-1', 'confirmar_senha': 'senha-segura-1',
            'conferencias_auditor': [self.comparativo.pk],
        })
        self.assertEqual(resposta.status_code, 302)
        operacional = Usuario.objects.get(login='auditor.externo')
        acesso = AuditorAcesso.objects.get(auditor=operacional.user, comparativo=self.comparativo)
        self.assertTrue(acesso.ativo)
        self.assertTrue(EventoAuditoria.objects.filter(tipo='ACESSO_CONCEDIDO', dados__origem='cadastro de usuário').exists())
        # Sem contagem liberada, o acesso existe mas nada aparece ainda.
        self.client.force_login(operacional.user)
        self.assertEqual(self.client.get(self.url('painel')).status_code, 403)
        self.liberar(1)
        self.assertEqual(self.client.get(self.url('painel')).status_code, 200)
        # Edição sem a conferência revoga o acesso.
        self.client.force_login(self.admin)
        self.client.post(reverse('accounts:usuarios_editar', args=[operacional.pk]), {
            'nome': 'Auditor Externo', 'login': 'auditor.externo', 'setor': 'Auditoria', 'perfil': 'AUDITOR',
            'ativo': 'on', 'senha': '', 'confirmar_senha': '',
        })
        acesso.refresh_from_db()
        self.assertFalse(acesso.ativo)
        self.assertTrue(EventoAuditoria.objects.filter(tipo='ACESSO_REVOGADO').exists())
