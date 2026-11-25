"""Categoria efetiva do produto (regra corrigida) — testes obrigatórios 1 a 12.

1-4  sem classificação em posição de Lubrificantes/Filtros/Agregado e sem posição
5    inclusão explícita (somente no inventário)
6    produto em categorias diferentes (conflito)
7    produto classificado em posição incompatível
8    publicação atômica em caso de conflito
9    Confronto e Comparativo com categoria efetiva
10   Pocket com produto sem classificação
11   histórico congelado após finalização
12   permissões de Administrador, Operador, Consulta e Auditor
"""
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Usuario
from accounts.test_utils import criar_usuario_teste
from core.categorias import SESSAO_CHAVE_CATEGORIA, CategoriaInventario
from estoque_fisico.models import EstoqueFisico
from estoque_sap.models import EstoqueSAP
from inventario.models import Inventario, InventarioItem, InventarioProdutoInclusao
from inventario.services.categoria import (
    OrigemCategoria,
    OrigemInclusao,
    categoria_efetiva,
    incluir_produto_sem_classificacao,
    listar_conflitos_categoria,
    produtos_do_inventario,
)
from inventario.services.comparativo_contagens import (
    OPCAO_NOVA_REFERENCIA,
    carregar_dados,
    finalizar_comparativo,
    salvar_comparativo,
)
from inventario.services.confronto import executar_confronto
from inventario.services.consolidacao import ConsolidacaoError, publicar_estoque_fisico
from inventario.services.contagem import ContagemCategoriaError, salvar_contagem
from posicoes.models import Posicao
from produtos.models import Produto

LUB = CategoriaInventario.LUBRIFICANTES
FIL = CategoriaInventario.FILTROS
AGR = CategoriaInventario.AGREGADO
CATEGORIAS = (LUB, FIL, AGR)


class BaseEfetivaTestCase(TestCase):
    def setUp(self):
        self.user, self.perfil = criar_usuario_teste(username='admin.efetiva')
        self.client.force_login(self.user)
        self.posicoes = {
            c: Posicao.objects.create(codigo=f'EF-{c[:3]}', posicao=f'Pos {c}', categoria=c)
            for c in CATEGORIAS
        }
        self.posicoes_extra = {
            c: Posicao.objects.create(codigo=f'EF2-{c[:3]}', posicao=f'Pos2 {c}', categoria=c)
            for c in CATEGORIAS
        }

    def produto(self, codigo, setor='PENDENTE'):
        return Produto.objects.create(codigo_produto=codigo, descricao=f'Produto {codigo}', setor=setor)

    def publicar_saldo(self, produto, categoria, quantidade='1', posicao=None):
        return EstoqueFisico.objects.create(
            produto=produto,
            posicao=posicao or self.posicoes[categoria],
            quantidade=Decimal(quantidade),
            categoria=categoria,
            data_contagem=timezone.now(),
        )

    def inventario(self, categoria, status=Inventario.Status.EM_ANDAMENTO):
        return Inventario.objects.create(usuario=self.perfil, status=status, categoria=categoria)

    def selecionar(self, categoria):
        sessao = self.client.session
        sessao[SESSAO_CHAVE_CATEGORIA] = categoria
        sessao.save()


class SemClassificacaoPorPosicaoTestCase(BaseEfetivaTestCase):
    """Testes 1, 2, 3 e 4."""

    def test_1_2_3_sem_classificacao_segue_a_categoria_da_posicao(self):
        for categoria in CATEGORIAS:
            with self.subTest(categoria=categoria):
                produto = self.produto(f'SC-{categoria[:3]}', setor='A')
                self.publicar_saldo(produto, categoria)
                efetiva = categoria_efetiva(produto)
                self.assertEqual(efetiva.categoria, categoria)
                self.assertEqual(efetiva.origem, OrigemCategoria.POSICAO)
                produto.refresh_from_db()
                self.assertEqual(produto.setor, 'A')  # nunca regravado
                # Aparece nas telas da sua categoria e em nenhuma outra.
                for outra in CATEGORIAS:
                    self.selecionar(outra)
                    codigos = [p.codigo_produto for p in self.client.get(reverse('produtos:lista')).context['object_list']]
                    self.assertEqual(produto.codigo_produto in codigos, outra == categoria)

    def test_contagem_aberta_tambem_e_evidencia_fisica(self):
        produto = self.produto('SC-ABERTA', setor='')
        inventario = self.inventario(AGR)
        salvar_contagem(inventario, self.posicoes[AGR], produto, Decimal('2'))
        self.assertEqual(categoria_efetiva(produto).categoria, AGR)

    def test_4_sem_classificacao_e_sem_posicao_fica_sem_classificacao(self):
        produto = self.produto('SC-NADA', setor='PENDENTE')
        efetiva = categoria_efetiva(produto)
        self.assertIsNone(efetiva.categoria)
        self.assertEqual(efetiva.origem, OrigemCategoria.SEM_CLASSIFICACAO)
        for categoria in CATEGORIAS:
            self.selecionar(categoria)
            codigos = [p.codigo_produto for p in self.client.get(reverse('produtos:lista')).context['object_list']]
            self.assertNotIn('SC-NADA', codigos)
        resposta = self.client.get(reverse('produtos:lista'), {'classificacao': 'sem'})
        self.assertIn('SC-NADA', [p.codigo_produto for p in resposta.context['object_list']])

    def test_nao_classifica_por_descricao(self):
        produto = Produto.objects.create(codigo_produto='DESC-1', descricao='FILTRO DE OLEO', setor='PENDENTE')
        self.assertEqual(categoria_efetiva(produto).origem, OrigemCategoria.SEM_CLASSIFICACAO)


class InclusaoExplicitaEfetivaTestCase(BaseEfetivaTestCase):
    """Teste 5."""

    def test_5_inclusao_vale_somente_no_inventario(self):
        produto = self.produto('INC-1')
        inv_a = self.inventario(FIL)
        inv_b = self.inventario(FIL)
        inclusao, criada = incluir_produto_sem_classificacao(inv_a, produto, self.perfil)
        self.assertTrue(criada)
        self.assertEqual(inclusao.origem, OrigemInclusao.WEB)
        self.assertEqual(categoria_efetiva(produto, inv_a).origem, OrigemCategoria.INCLUSAO)
        self.assertEqual(categoria_efetiva(produto, inv_b).origem, OrigemCategoria.SEM_CLASSIFICACAO)
        self.assertIn(produto, produtos_do_inventario(inv_a))
        self.assertNotIn(produto, produtos_do_inventario(inv_b))
        produto.refresh_from_db()
        self.assertEqual(produto.setor, 'PENDENTE')


class ConflitoCategoriaTestCase(BaseEfetivaTestCase):
    """Testes 6 e 7."""

    def test_6_sem_classificacao_em_duas_categorias_e_conflito(self):
        produto = self.produto('CONF-1', setor='')
        self.publicar_saldo(produto, LUB)
        self.publicar_saldo(produto, FIL)
        efetiva = categoria_efetiva(produto)
        self.assertTrue(efetiva.conflito)
        self.assertIsNone(efetiva.categoria)
        self.assertEqual(efetiva.categorias_fisicas, {LUB, FIL})
        self.assertIn(produto.pk, [p.pk for p in listar_conflitos_categoria()])
        # Não aparece em nenhuma categoria nem em "Sem classificação"...
        for categoria in CATEGORIAS:
            self.selecionar(categoria)
            codigos = [p.codigo_produto for p in self.client.get(reverse('produtos:lista')).context['object_list']]
            self.assertNotIn('CONF-1', codigos)
        sem = self.client.get(reverse('produtos:lista'), {'classificacao': 'sem'}).context['object_list']
        self.assertNotIn('CONF-1', [p.codigo_produto for p in sem])
        # ...somente na visão de conflitos das categorias envolvidas.
        for categoria, esperado in ((LUB, True), (FIL, True), (AGR, False)):
            self.selecionar(categoria)
            resposta = self.client.get(reverse('produtos:lista'), {'classificacao': 'conflito'})
            codigos = [p.codigo_produto for p in resposta.context['object_list']]
            self.assertEqual('CONF-1' in codigos, esperado)
            if esperado:
                self.assertContains(resposta, 'data-categoria-origem="CONFLITO"')
        self.selecionar(LUB)
        # Contagem bloqueada com mensagem clara.
        with self.assertRaisesMessage(ContagemCategoriaError, 'Conflito de categoria'):
            salvar_contagem(self.inventario(LUB), self.posicoes_extra[LUB], produto, Decimal('1'))
        # Mensagem administrativa nas Pendências.
        resposta = self.client.get(reverse('accounts:pendencias_operacionais'))
        self.assertContains(resposta, 'data-conflito-produto="CONF-1"')

    def test_7_classificado_em_posicao_incompativel_e_bloqueado(self):
        produto = self.produto('INCOMP-1', setor='LUBRIFICANTE')
        inventario = self.inventario(FIL)
        with self.assertRaisesMessage(ContagemCategoriaError, 'pertence a Lubrificantes'):
            salvar_contagem(inventario, self.posicoes[FIL], produto, Decimal('1'))
        self.assertFalse(InventarioItem.objects.exists())
        # Saldo legado incompatível é apontado como conflito administrativo.
        self.publicar_saldo(produto, FIL)
        efetiva = categoria_efetiva(produto)
        self.assertTrue(efetiva.incompativel)
        self.assertEqual(efetiva.categoria, LUB)
        self.assertIn(produto.pk, [p.pk for p in listar_conflitos_categoria()])


class PublicacaoAtomicaTestCase(BaseEfetivaTestCase):
    """Teste 8."""

    def _finalizado_com(self, categoria, itens):
        inventario = self.inventario(categoria)
        for produto, posicao, quantidade in itens:
            InventarioItem.objects.create(
                inventario=inventario, posicao=posicao, produto=produto, quantidade_fisica=Decimal(quantidade),
            )
        inventario.status = Inventario.Status.FINALIZADO
        inventario.save(update_fields=['status'])
        return inventario

    def test_8_conflito_bloqueia_publicacao_sem_apagar_nada(self):
        lub = self.produto('PUB-LUB', setor='LUBRIFICANTE')
        fil_ok = self.produto('PUB-FIL', setor='FILTRO')
        sem = self.produto('PUB-SEM', setor='PENDENTE')
        publicar_estoque_fisico(self._finalizado_com(LUB, [(lub, self.posicoes[LUB], '10'), (sem, self.posicoes[LUB], '3')]))
        publicar_estoque_fisico(self._finalizado_com(FIL, [(fil_ok, self.posicoes[FIL], '4')]))
        antes = sorted(EstoqueFisico.objects.values_list('pk', 'produto_id', 'posicao_id', 'quantidade', 'categoria'))

        # Novo Filtros traz o produto sem classificação que já está em Lubrificantes.
        conflito = self._finalizado_com(FIL, [
            (fil_ok, self.posicoes[FIL], '5'),
            (sem, self.posicoes_extra[FIL], '1'),
        ])
        with self.assertRaisesMessage(ConsolidacaoError, 'Nenhum estoque foi alterado'):
            publicar_estoque_fisico(conflito)
        self.assertEqual(
            sorted(EstoqueFisico.objects.values_list('pk', 'produto_id', 'posicao_id', 'quantidade', 'categoria')),
            antes,
        )

    def test_8_produto_oficial_incompativel_bloqueia_publicacao(self):
        lub = self.produto('PUB-LUB2', setor='LUBRIFICANTE')
        inventario = self._finalizado_com(AGR, [(lub, self.posicoes[AGR], '1')])
        with self.assertRaisesMessage(ConsolidacaoError, 'pertence a Lubrificantes'):
            publicar_estoque_fisico(inventario)
        self.assertFalse(EstoqueFisico.objects.exists())

    def test_8_posicao_de_outra_categoria_bloqueia_publicacao(self):
        fil = self.produto('PUB-FIL2', setor='FILTRO')
        inventario = self._finalizado_com(FIL, [(fil, self.posicoes[LUB], '1')])
        with self.assertRaisesMessage(ConsolidacaoError, 'posição EF-LUB pertence a Lubrificantes'):
            publicar_estoque_fisico(inventario)
        self.assertFalse(EstoqueFisico.objects.exists())


class ConfrontoComparativoEfetivaTestCase(BaseEfetivaTestCase):
    """Teste 9."""

    def setUp(self):
        super().setUp()
        self.sem_por_categoria = {}
        for categoria in CATEGORIAS:
            produto = self.produto(f'CMP-{categoria[:3]}', setor='A')
            self.publicar_saldo(produto, categoria)
            EstoqueSAP.objects.create(
                produto=produto, canal_1=Decimal('7'), canal_110=Decimal('0'),
                total=Decimal('7'), arquivo_origem='sap.xlsx',
            )
            self.sem_por_categoria[categoria] = produto
        self.sem_posicao = self.produto('CMP-NADA')
        EstoqueSAP.objects.create(
            produto=self.sem_posicao, canal_1=Decimal('2'), canal_110=Decimal('0'),
            total=Decimal('2'), arquivo_origem='sap.xlsx',
        )

    def test_9_confronto_usa_categoria_efetiva(self):
        for categoria in CATEGORIAS:
            with self.subTest(categoria=categoria):
                codigos = {l.codigo_produto for l in executar_confronto(self.inventario(categoria).pk).linhas}
                self.assertEqual(codigos, {self.sem_por_categoria[categoria].codigo_produto})
        inventario = self.inventario(AGR)
        incluir_produto_sem_classificacao(inventario, self.sem_posicao, self.perfil)
        codigos = {l.codigo_produto for l in executar_confronto(inventario.pk).linhas}
        self.assertEqual(codigos, {'CMP-AGR', 'CMP-NADA'})

    def test_9_comparativo_usa_categoria_efetiva(self):
        for categoria in CATEGORIAS:
            with self.subTest(categoria=categoria):
                c1 = self.inventario(categoria)
                comparativo = salvar_comparativo(
                    comparativo=None, nome=f'Ef {categoria}', inventarios={1: c1},
                    referencia_opcao=OPCAO_NOVA_REFERENCIA, estado=None, usuario=self.user,
                    categoria=categoria,
                )
                dados = carregar_dados(comparativo)
                self.assertEqual(set(dados.sap), {self.sem_por_categoria[categoria].pk})


class PocketSemClassificacaoTestCase(BaseEfetivaTestCase):
    """Teste 10."""

    def _post(self, inventario, posicao, produto):
        return self.client.post(
            reverse('pocket:contagem', args=[inventario.pk]),
            {'codigo_posicao': posicao.codigo, 'codigo_produto': produto.codigo_produto, 'quantidade_fisica': '3'},
        )

    def test_10_pocket_conta_sem_classificacao_pela_posicao(self):
        produto = self.produto('PK-SEM')
        inventario = self.inventario(FIL)
        dados = self.client.get(reverse('pocket:dados_mestres'), {'inventario_id': inventario.pk}).json()
        self.assertIn('PK-SEM', dados['mapa_produtos'])
        self._post(inventario, self.posicoes[FIL], produto)
        self.assertTrue(InventarioItem.objects.filter(inventario=inventario, produto=produto).exists())
        self.assertEqual(categoria_efetiva(produto).categoria, FIL)
        # Agora pertence a Filtros: some do mapa de Lubrificantes e é bloqueado lá.
        lub = self.inventario(LUB)
        dados_lub = self.client.get(reverse('pocket:dados_mestres'), {'inventario_id': lub.pk}).json()
        self.assertNotIn('PK-SEM', dados_lub['mapa_produtos'])
        self._post(lub, self.posicoes[LUB], produto)
        self.assertFalse(InventarioItem.objects.filter(inventario=lub).exists())

    def test_10_precadastro_inclui_somente_no_inventario_com_aviso(self):
        Produto.objects.create(codigo_produto='CAT-EMB-EF', descricao='cat', setor='FILTRO', embalagem='CX')
        inventario = self.inventario(FIL)
        outro = self.inventario(FIL)
        resposta = self.client.post(
            reverse('pocket:precadastro_produto', args=[inventario.pk]),
            {'codigo_produto': 'PK-NOVO', 'descricao': 'Novo', 'embalagem': 'CX'},
            follow=True,
        )
        self.assertContains(resposta, f'somente neste inventário #{inventario.pk}')
        inclusao = InventarioProdutoInclusao.objects.get(produto__codigo_produto='PK-NOVO')
        self.assertEqual(
            (inclusao.inventario_id, inclusao.usuario_id, inclusao.origem),
            (inventario.pk, self.perfil.pk, OrigemInclusao.POCKET_PRECADASTRO),
        )
        self.assertIsNotNone(inclusao.data_inclusao)
        self.assertEqual(Produto.objects.get(codigo_produto='PK-NOVO').setor, 'PRÉ-CADASTRO')
        self.assertFalse(InventarioProdutoInclusao.objects.filter(inventario=outro).exists())


class HistoricoCongeladoTestCase(BaseEfetivaTestCase):
    """Teste 11."""

    def test_11_finalizacao_congela_resultado(self):
        produto = self.produto('HIST-1', setor='A')
        c1 = self.inventario(LUB)
        salvar_contagem(c1, self.posicoes[LUB], produto, Decimal('4'))
        comparativo = salvar_comparativo(
            comparativo=None, nome='Histórico', inventarios={1: c1},
            referencia_opcao='', estado=None, usuario=self.user, categoria=LUB,
        )
        finalizado = finalizar_comparativo(comparativo, self.user)
        snapshot = finalizado.snapshot_resultado
        # Mudanças posteriores (posição em outra categoria, conflito) não alteram o gravado.
        self.publicar_saldo(produto, FIL)
        finalizado.refresh_from_db()
        self.assertEqual(finalizado.snapshot_resultado, snapshot)
        self.assertEqual({l.produto_id for l in carregar_dados(finalizado).lancamentos}, {produto.pk})
        # Inventário finalizado mantém a categoria gravada e aparece no histórico dela.
        c1.status = Inventario.Status.FINALIZADO
        c1.save(update_fields=['status'])
        self.selecionar(LUB)
        resposta = self.client.get(reverse('historico_unificado'))
        self.assertIn(c1.pk, [linha.pk for linha in resposta.context['linhas']])
        self.selecionar(FIL)
        resposta = self.client.get(reverse('historico_unificado'))
        self.assertNotIn(c1.pk, [linha.pk for linha in resposta.context['linhas']])


class PermissoesCategoriaTestCase(BaseEfetivaTestCase):
    """Teste 12."""

    def test_12_administrador_inclui_e_ve_conflitos(self):
        inventario = self.inventario(LUB)
        self.client.post(reverse('inventario:incluir_produto', args=[inventario.pk]), {'codigo_produto': self.produto('PERM-1').codigo_produto})
        self.assertTrue(InventarioProdutoInclusao.objects.exists())
        self.assertEqual(self.client.get(reverse('accounts:pendencias_operacionais')).status_code, 200)

    def test_12_operador_usa_pocket_mas_nao_telas_administrativas(self):
        operador, _ = criar_usuario_teste(username='op.efetiva', perfil=Usuario.Perfil.OPERADOR)
        self.client.force_login(operador)
        self.assertEqual(self.client.get(reverse('pocket:selecionar')).status_code, 200)
        inventario = self.inventario(LUB)
        self.client.post(reverse('inventario:incluir_produto', args=[inventario.pk]), {'codigo_produto': self.produto('PERM-2').codigo_produto})
        self.assertFalse(InventarioProdutoInclusao.objects.exists())
        self.assertNotEqual(self.client.get(reverse('accounts:pendencias_operacionais')).status_code, 200)

    def test_12_consulta_le_mas_nao_altera(self):
        consulta, _ = criar_usuario_teste(username='consulta.efetiva', perfil=Usuario.Perfil.CONSULTA)
        self.client.force_login(consulta)
        self.assertEqual(self.client.get(reverse('produtos:lista')).status_code, 200)
        self.client.post(reverse('categoria_selecionar'), {'categoria': FIL})
        self.assertEqual(self.client.session[SESSAO_CHAVE_CATEGORIA], FIL)
        inventario = self.inventario(FIL)
        self.client.post(reverse('inventario:incluir_produto', args=[inventario.pk]), {'codigo_produto': self.produto('PERM-3').codigo_produto})
        self.assertFalse(InventarioProdutoInclusao.objects.exists())
        self.client.post(reverse('posicoes:criar'), {'codigo': 'PERM-POS', 'posicao': 'x', 'categoria': FIL})
        self.assertFalse(Posicao.objects.filter(codigo='PERM-POS').exists())

    def test_12_auditor_ve_somente_o_portal_sem_sap(self):
        auditor, _ = criar_usuario_teste(username='auditor.efetiva', perfil=Usuario.Perfil.AUDITOR)
        self.client.force_login(auditor)
        for nome in ('estoque_sap:lista', 'produtos:lista', 'confronto', 'comparativo:lista'):
            with self.subTest(tela=nome):
                self.assertNotEqual(self.client.get(reverse(nome)).status_code, 200)
        resposta = self.client.get(reverse('auditor:inicio'), follow=True)
        self.assertEqual(resposta.status_code, 200)
        self.assertNotContains(resposta, 'SAP')


class IsolamentoTelasTestCase(BaseEfetivaTestCase):
    """Ao selecionar uma categoria, nenhuma informação das outras aparece."""

    TELAS = [
        'produtos:lista', 'posicoes:lista', 'estoque_sap:lista', 'estoque_fisico:lista',
        'inventario:lista', 'historico_unificado', 'confronto', 'aprovacao', 'consolidacao',
        'comparativo:lista', 'home', 'dashboard_classico', 'accounts:pendencias_operacionais',
        'pocket:selecionar',
    ]

    def setUp(self):
        super().setUp()
        self.marcas = {}
        for categoria in CATEGORIAS:
            marca = f'ISO{categoria[:3]}'
            oficial = self.produto(f'{marca}-OFICIAL', setor={LUB: 'LUBRIFICANTE', FIL: 'FILTRO', AGR: 'AGREGADO'}[categoria])
            sem = self.produto(f'{marca}-SEMSETOR', setor='PENDENTE')
            posicao = Posicao.objects.create(codigo=f'{marca}-POS', posicao=f'{marca} rua', categoria=categoria)
            for produto in (oficial, sem):
                self.publicar_saldo(produto, categoria, posicao=posicao)
                EstoqueSAP.objects.create(produto=produto, canal_1=Decimal('1'), canal_110=Decimal('0'),
                                          total=Decimal('1'), arquivo_origem='sap.xlsx')
            responsavel, _ = criar_usuario_teste(username=f'resp.{categoria.lower()}', perfil=Usuario.Perfil.INVENTARIO)
            Usuario.objects.filter(login=f'resp.{categoria.lower()}').update(nome=f'{marca} Responsavel')
            perfil = Usuario.objects.get(login=f'resp.{categoria.lower()}')
            aberto = Inventario.objects.create(usuario=perfil, status=Inventario.Status.ABERTO, categoria=categoria)
            finalizado = Inventario.objects.create(usuario=perfil, status=Inventario.Status.FINALIZADO, categoria=categoria)
            InventarioItem.objects.create(inventario=finalizado, posicao=posicao, produto=oficial, quantidade_fisica=Decimal('1'))
            salvar_comparativo(
                comparativo=None, nome=f'{marca} Conferencia', inventarios={1: aberto},
                referencia_opcao='', estado=None, usuario=self.user, categoria=categoria,
            )
            self.marcas[categoria] = marca

    def test_nenhuma_tela_mostra_outra_categoria(self):
        for categoria in CATEGORIAS:
            self.selecionar(categoria)
            outras = [self.marcas[c] for c in CATEGORIAS if c != categoria]
            for nome in self.TELAS:
                with self.subTest(categoria=categoria, tela=nome):
                    resposta = self.client.get(reverse(nome))
                    self.assertEqual(resposta.status_code, 200)
                    html = resposta.content.decode('utf-8')
                    for marca in outras:
                        self.assertNotIn(marca, html)

    def test_cada_categoria_mostra_os_proprios_dados(self):
        esperados = {
            'produtos:lista': ['-OFICIAL', '-SEMSETOR'],
            'posicoes:lista': ['-POS'],
            'estoque_sap:lista': ['-OFICIAL', '-SEMSETOR'],
            'estoque_fisico:lista': ['-OFICIAL', '-SEMSETOR'],
            'inventario:lista': [' Responsavel'],
            'comparativo:lista': [' Conferencia'],
        }
        for categoria in CATEGORIAS:
            self.selecionar(categoria)
            for nome, sufixos in esperados.items():
                html = self.client.get(reverse(nome)).content.decode('utf-8')
                for sufixo in sufixos:
                    with self.subTest(categoria=categoria, tela=nome, dado=sufixo):
                        self.assertIn(self.marcas[categoria] + sufixo, html)

    def test_seletor_visivel_com_as_tres_frentes_e_ativa_destacada(self):
        for categoria in CATEGORIAS:
            self.selecionar(categoria)
            html = self.client.get(reverse('produtos:lista')).content.decode('utf-8')
            inicio = html.index('id="barraCategoria"')
            trecho = html[inicio:html.index('</nav>', inicio)]
            self.assertEqual(trecho.count('data-categoria-opcao='), 3)
            for valor, rotulo in ((LUB, 'Lubrificantes'), (FIL, 'Filtros'), (AGR, 'Agregados')):
                self.assertIn(f'value="{valor}"', trecho)
                self.assertIn(f'>{rotulo}</button>', trecho)
            self.assertEqual(trecho.count('aria-pressed="true"'), 1)
            ativo = trecho.index('aria-pressed="true"')
            self.assertIn(f'data-categoria-opcao="{categoria}"', trecho[ativo:ativo + 200])

    def test_link_direto_de_outra_categoria_alinha_a_sessao(self):
        self.selecionar(LUB)
        filtros = Inventario.objects.filter(categoria=FIL, status=Inventario.Status.ABERTO).first()
        self.client.get(reverse('inventario:contagem_lista', args=[filtros.pk]))
        self.assertEqual(self.client.session[SESSAO_CHAVE_CATEGORIA], FIL)


class OperadorPocketTrocaFrenteTestCase(BaseEfetivaTestCase):
    def test_operador_troca_frente_pela_barra_e_pocket_muda(self):
        operador, _ = criar_usuario_teste(username='op.frente', perfil=Usuario.Perfil.OPERADOR)
        ids = {c: self.inventario(c, status=Inventario.Status.ABERTO).pk for c in CATEGORIAS}
        self.client.force_login(operador)
        for categoria in CATEGORIAS:
            with self.subTest(categoria=categoria):
                resposta = self.client.post(
                    reverse('categoria_selecionar'),
                    {'categoria': categoria, 'next': reverse('pocket:selecionar')},
                )
                self.assertRedirects(resposta, reverse('pocket:selecionar'))
                self.assertEqual(self.client.session[SESSAO_CHAVE_CATEGORIA], categoria)
                lista = self.client.get(reverse('pocket:selecionar'))
                self.assertContains(lista, 'id="barraCategoria"')
                self.assertEqual([i.pk for i in lista.context['inventarios']], [ids[categoria]])
