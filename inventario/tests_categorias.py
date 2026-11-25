"""Isolamento entre categorias de inventário (Lubrificantes, Filtros, Agregado).

Cobre: definição central, sessão, inventário, contagem web/Pocket/POST manual,
inclusão explícita, posições, publicação do estoque físico, importação SAP,
confronto, tarefas, locks e auditoria.
"""
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Usuario
from accounts.test_utils import criar_usuario_teste
from core.categorias import (
    CATEGORIA_PADRAO,
    SESSAO_CHAVE_CATEGORIA,
    CategoriaInventario,
    categoria_do_setor,
    q_produto_da_categoria,
    q_produto_sem_classificacao,
)
from estoque_fisico.models import EstoqueFisico
from estoque_sap.models import EstoqueSAP
from inventario.models import Inventario, InventarioItem, InventarioProdutoInclusao
from inventario.models_operacional import InventarioAuditoriaEvento, InventarioLock, InventarioTarefa
from inventario.services.categoria import CategoriaError, incluir_produto_sem_classificacao
from inventario.services.consolidacao import ConsolidacaoError, publicar_estoque_fisico
from inventario.services.contagem import ContagemCategoriaError, salvar_contagem
from posicoes.models import Posicao
from produtos.models import Produto

LUB = CategoriaInventario.LUBRIFICANTES
FIL = CategoriaInventario.FILTROS
AGR = CategoriaInventario.AGREGADO
CATEGORIAS = (LUB, FIL, AGR)
SETOR_DA_CATEGORIA = {LUB: 'LUBRIFICANTE', FIL: 'FILTRO', AGR: 'AGREGADO'}


class BaseCategoriaTestCase(TestCase):
    def setUp(self):
        self.user, self.perfil = criar_usuario_teste(username='admin.cat')
        self.client.force_login(self.user)
        self.produtos = {}
        self.posicoes = {}
        for categoria in CATEGORIAS:
            sufixo = categoria[:3]
            self.produtos[categoria] = Produto.objects.create(
                codigo_produto=f'P-{sufixo}',
                descricao=f'Produto {categoria}',
                setor=SETOR_DA_CATEGORIA[categoria],
            )
            self.posicoes[categoria] = Posicao.objects.create(
                codigo=f'POS-{sufixo}',
                posicao=f'Posição {categoria}',
                categoria=categoria,
            )
        self.produto_sem_classe = Produto.objects.create(
            codigo_produto='P-PEND',
            descricao='Produto pendente',
            setor='PENDENTE',
        )

    def criar_inventario(self, categoria, status=Inventario.Status.ABERTO):
        return Inventario.objects.create(usuario=self.perfil, status=status, categoria=categoria)

    def selecionar_categoria(self, categoria):
        sessao = self.client.session
        sessao[SESSAO_CHAVE_CATEGORIA] = categoria
        sessao.save()


class DefinicaoCentralCategoriasTestCase(TestCase):
    def test_mapeamento_de_setores_legados(self):
        self.assertEqual(categoria_do_setor('LUBRIFICANTE'), LUB)
        self.assertEqual(categoria_do_setor('  lubrificantes '), LUB)
        self.assertEqual(categoria_do_setor('Filtros'), FIL)
        self.assertEqual(categoria_do_setor('FILTRO'), FIL)
        self.assertEqual(categoria_do_setor('agregado'), AGR)
        self.assertEqual(categoria_do_setor('AGREGADOS'), AGR)

    def test_valores_ambiguos_ficam_sem_classificacao(self):
        for valor in ('PENDENTE', 'A', '', None, 'PRÉ-CADASTRO', 'GERAL', 'LUB', 'X'):
            with self.subTest(valor=valor):
                self.assertIsNone(categoria_do_setor(valor))

    def test_padrao_e_lubrificantes(self):
        self.assertEqual(CATEGORIA_PADRAO, LUB)

    def test_filtros_de_banco_sem_alterar_setor(self):
        Produto.objects.create(codigo_produto='L1', descricao='x', setor=' Lubrificante ')
        Produto.objects.create(codigo_produto='F1', descricao='x', setor='FILTROS')
        Produto.objects.create(codigo_produto='A1', descricao='x', setor='A')
        Produto.objects.create(codigo_produto='N1', descricao='x', setor='')
        self.assertEqual(
            list(Produto.objects.filter(q_produto_da_categoria(LUB)).values_list('codigo_produto', flat=True)),
            ['L1'],
        )
        self.assertEqual(
            sorted(Produto.objects.filter(q_produto_sem_classificacao()).values_list('codigo_produto', flat=True)),
            ['A1', 'N1'],
        )
        self.assertEqual(Produto.objects.get(codigo_produto='L1').setor, ' Lubrificante ')
        self.assertEqual(Produto.objects.get(codigo_produto='A1').setor, 'A')


class SessaoCategoriaTestCase(BaseCategoriaTestCase):
    def test_padrao_lubrificantes_na_sessao(self):
        resposta = self.client.get(reverse('inventario:lista'))
        self.assertEqual(resposta.context['categoria_atual'], LUB)

    def test_troca_categoria_e_permanece_na_sessao(self):
        resposta = self.client.post(
            reverse('categoria_selecionar'),
            {'categoria': FIL, 'next': reverse('inventario:lista')},
        )
        self.assertRedirects(resposta, reverse('inventario:lista'))
        self.assertEqual(self.client.session[SESSAO_CHAVE_CATEGORIA], FIL)
        self.assertEqual(self.client.get(reverse('produtos:lista')).context['categoria_atual'], FIL)

    def test_categoria_invalida_e_recusada(self):
        self.client.post(reverse('categoria_selecionar'), {'categoria': 'GERAL'})
        self.assertNotIn(SESSAO_CHAVE_CATEGORIA, self.client.session)

    def test_redirecionamento_externo_e_bloqueado(self):
        resposta = self.client.post(
            reverse('categoria_selecionar'),
            {'categoria': AGR, 'next': 'https://externo.example.com/'},
        )
        self.assertRedirects(resposta, reverse('home'), fetch_redirect_response=False)

    def test_selecao_exige_login(self):
        self.client.logout()
        resposta = self.client.post(reverse('categoria_selecionar'), {'categoria': FIL})
        self.assertEqual(resposta.status_code, 302)
        self.assertIn(reverse('accounts:login'), resposta['Location'])


class InventarioCategoriaTestCase(BaseCategoriaTestCase):
    def test_criacao_permite_escolher_cada_frente(self):
        self.selecionar_categoria(FIL)
        resposta = self.client.get(reverse('inventario:criar'))
        self.assertEqual(resposta.context['form'].fields['categoria'].initial, FIL)
        for rotulo in ('Inventário de Lubrificantes', 'Inventário de Filtros', 'Inventário de Agregados'):
            self.assertContains(resposta, rotulo)
        for categoria in CATEGORIAS:
            with self.subTest(categoria=categoria):
                self.client.post(
                    reverse('inventario:criar'),
                    {'usuario': self.perfil.pk, 'categoria': categoria, 'observacao': categoria},
                )
                self.assertEqual(Inventario.objects.get(observacao=categoria).categoria, categoria)
                # A sessão passa para a frente criada (o inventário aparece na lista).
                self.assertEqual(self.client.session[SESSAO_CHAVE_CATEGORIA], categoria)

    def test_criacao_exige_frente_valida(self):
        self.client.post(reverse('inventario:criar'), {'usuario': self.perfil.pk, 'observacao': 'x'})
        self.client.post(
            reverse('inventario:criar'),
            {'usuario': self.perfil.pk, 'observacao': 'x', 'categoria': 'GERAL'},
        )
        self.assertFalse(Inventario.objects.exists())

    def test_frente_muda_somente_antes_do_inicio_da_contagem(self):
        inventario = self.criar_inventario(FIL)
        self.client.post(
            reverse('inventario:editar', args=[inventario.pk]),
            {'usuario': self.perfil.pk, 'observacao': 'antes', 'categoria': AGR},
        )
        inventario.refresh_from_db()
        self.assertEqual(inventario.categoria, AGR)
        salvar_contagem(inventario, self.posicoes[AGR], self.produtos[AGR], Decimal('1'))
        inventario.status = Inventario.Status.ABERTO
        inventario.save(update_fields=['status'])
        resposta = self.client.get(reverse('inventario:editar', args=[inventario.pk]))
        self.assertTrue(resposta.context['form'].fields['categoria'].disabled)
        self.client.post(
            reverse('inventario:editar', args=[inventario.pk]),
            {'usuario': self.perfil.pk, 'observacao': 'depois', 'categoria': LUB},
        )
        inventario.refresh_from_db()
        self.assertEqual(inventario.categoria, AGR)
        self.assertEqual(inventario.observacao, 'depois')

    def test_lista_mostra_somente_categoria_da_sessao(self):
        ids = {categoria: self.criar_inventario(categoria).pk for categoria in CATEGORIAS}
        for categoria in CATEGORIAS:
            with self.subTest(categoria=categoria):
                self.selecionar_categoria(categoria)
                resposta = self.client.get(reverse('inventario:lista'))
                self.assertEqual([i.pk for i in resposta.context['inventarios']], [ids[categoria]])

    def test_tela_interna_usa_categoria_gravada_e_nao_a_da_sessao(self):
        inventario = self.criar_inventario(FIL)
        self.selecionar_categoria(LUB)
        resposta = self.client.get(reverse('inventario:contagem_criar', args=[inventario.pk]))
        produtos = list(resposta.context['form'].fields['produto'].queryset)
        posicoes = list(resposta.context['form'].fields['posicao'].queryset)
        # Filtros + sem classificação ainda sem posição (a contagem define a categoria).
        self.assertEqual(produtos, [self.produtos[FIL], self.produto_sem_classe])
        self.assertEqual(posicoes, [self.posicoes[FIL]])


class ContagemCategoriaTestCase(BaseCategoriaTestCase):
    def test_cada_categoria_aceita_somente_seu_produto_e_sua_posicao(self):
        for categoria in CATEGORIAS:
            inventario = self.criar_inventario(categoria)
            for outra in CATEGORIAS:
                with self.subTest(inventario=categoria, produto=outra):
                    if outra == categoria:
                        salvar_contagem(
                            inventario, self.posicoes[categoria], self.produtos[outra], Decimal('1'),
                        )
                    else:
                        with self.assertRaises(ContagemCategoriaError):
                            salvar_contagem(
                                inventario, self.posicoes[categoria], self.produtos[outra], Decimal('1'),
                            )
                with self.subTest(inventario=categoria, posicao=outra):
                    if outra != categoria:
                        with self.assertRaises(ContagemCategoriaError):
                            salvar_contagem(
                                inventario, self.posicoes[outra], self.produtos[categoria], Decimal('1'),
                            )
            self.assertEqual(
                set(InventarioItem.objects.filter(inventario=inventario).values_list('produto_id', flat=True)),
                {self.produtos[categoria].pk},
            )

    def test_post_manual_web_com_produto_de_outra_categoria_e_bloqueado(self):
        inventario = self.criar_inventario(FIL)
        resposta = self.client.post(
            reverse('inventario:contagem_criar', args=[inventario.pk]),
            {
                'posicao': self.posicoes[FIL].pk,
                'produto': self.produtos[LUB].pk,
                'quantidade_fisica': '5',
            },
        )
        self.assertEqual(resposta.status_code, 200)
        self.assertFalse(InventarioItem.objects.exists())

    def test_post_manual_web_com_posicao_de_outra_categoria_e_bloqueado(self):
        inventario = self.criar_inventario(AGR)
        self.client.post(
            reverse('inventario:contagem_criar', args=[inventario.pk]),
            {
                'posicao': self.posicoes[LUB].pk,
                'produto': self.produtos[AGR].pk,
                'quantidade_fisica': '5',
            },
        )
        self.assertFalse(InventarioItem.objects.exists())

    def test_sem_classificacao_segue_a_posicao_e_bloqueia_outra_categoria(self):
        # Regra corrigida: sem setor válido, a posição física define a categoria.
        inventario = self.criar_inventario(LUB)
        salvar_contagem(inventario, self.posicoes[LUB], self.produto_sem_classe, Decimal('1'))
        self.assertTrue(InventarioItem.objects.filter(produto=self.produto_sem_classe).exists())
        self.produto_sem_classe.refresh_from_db()
        self.assertEqual(self.produto_sem_classe.setor, 'PENDENTE')
        # Já localizado em Lubrificantes: contar em Filtros geraria conflito.
        filtros = self.criar_inventario(FIL)
        with self.assertRaises(ContagemCategoriaError):
            salvar_contagem(filtros, self.posicoes[FIL], self.produto_sem_classe, Decimal('1'))

    def test_inclusao_vale_somente_para_o_inventario_onde_foi_feita(self):
        from inventario.services.categoria import produtos_do_inventario

        inv_a = self.criar_inventario(LUB)
        inv_b = self.criar_inventario(LUB)
        incluir_produto_sem_classificacao(inv_a, self.produto_sem_classe, self.perfil)
        self.assertIn(self.produto_sem_classe, produtos_do_inventario(inv_a))
        self.assertNotIn(self.produto_sem_classe, produtos_do_inventario(inv_b))

    def test_lancamento_historico_preserva_edicao_de_quantidade(self):
        inventario = self.criar_inventario(LUB)
        legado = InventarioItem.objects.create(
            inventario=inventario,
            posicao=self.posicoes[LUB],
            produto=self.produto_sem_classe,
            quantidade_fisica=Decimal('2'),
        )
        salvar_contagem(
            inventario, self.posicoes[LUB], self.produto_sem_classe, Decimal('3'), item_existente=legado,
        )
        legado.refresh_from_db()
        self.assertEqual(legado.quantidade_fisica, Decimal('3'))

    def test_edicao_web_nao_troca_para_produto_de_outra_categoria(self):
        inventario = self.criar_inventario(LUB)
        item = salvar_contagem(inventario, self.posicoes[LUB], self.produtos[LUB], Decimal('1'))
        self.client.post(
            reverse('inventario:contagem_editar', args=[inventario.pk, item.pk]),
            {
                'posicao': self.posicoes[LUB].pk,
                'produto': self.produtos[FIL].pk,
                'quantidade_fisica': '9',
            },
        )
        item.refresh_from_db()
        self.assertEqual(item.produto_id, self.produtos[LUB].pk)
        self.assertEqual(item.quantidade_fisica, Decimal('1'))


class InclusaoExplicitaTestCase(BaseCategoriaTestCase):
    def test_view_inclui_sem_classificacao_e_nao_altera_setor(self):
        inventario = self.criar_inventario(FIL)
        self.client.post(
            reverse('inventario:incluir_produto', args=[inventario.pk]),
            {'codigo_produto': 'p-pend'},
        )
        inclusao = InventarioProdutoInclusao.objects.get(inventario=inventario)
        self.assertEqual(inclusao.produto, self.produto_sem_classe)
        self.assertEqual(inclusao.usuario, self.perfil)
        self.assertEqual(inclusao.setor_no_momento, 'PENDENTE')
        self.produto_sem_classe.refresh_from_db()
        self.assertEqual(self.produto_sem_classe.setor, 'PENDENTE')

    def test_view_recusa_produto_classificado(self):
        inventario = self.criar_inventario(FIL)
        for categoria in CATEGORIAS:
            self.client.post(
                reverse('inventario:incluir_produto', args=[inventario.pk]),
                {'codigo_produto': self.produtos[categoria].codigo_produto},
            )
        self.assertFalse(InventarioProdutoInclusao.objects.exists())
        with self.assertRaises(CategoriaError):
            incluir_produto_sem_classificacao(inventario, self.produtos[LUB], self.perfil)

    def test_inventario_finalizado_nao_recebe_inclusao(self):
        inventario = self.criar_inventario(LUB, status=Inventario.Status.FINALIZADO)
        self.client.post(
            reverse('inventario:incluir_produto', args=[inventario.pk]),
            {'codigo_produto': 'P-PEND'},
        )
        self.assertFalse(InventarioProdutoInclusao.objects.exists())

    def test_perfil_consulta_nao_inclui(self):
        consulta, _ = criar_usuario_teste(username='consulta.cat', perfil=Usuario.Perfil.CONSULTA)
        self.client.force_login(consulta)
        inventario = self.criar_inventario(LUB)
        self.client.post(
            reverse('inventario:incluir_produto', args=[inventario.pk]),
            {'codigo_produto': 'P-PEND'},
        )
        self.assertFalse(InventarioProdutoInclusao.objects.exists())

    def test_inclusao_aceita_somente_post(self):
        inventario = self.criar_inventario(LUB)
        resposta = self.client.get(reverse('inventario:incluir_produto', args=[inventario.pk]))
        self.assertEqual(resposta.status_code, 405)


class PublicacaoEstoqueFisicoCategoriaTestCase(BaseCategoriaTestCase):
    def _finalizar_com_item(self, categoria, quantidade):
        inventario = self.criar_inventario(categoria)
        InventarioItem.objects.create(
            inventario=inventario,
            posicao=self.posicoes[categoria],
            produto=self.produtos[categoria],
            quantidade_fisica=Decimal(quantidade),
        )
        inventario.status = Inventario.Status.FINALIZADO
        inventario.save(update_fields=['status'])
        return inventario

    def test_finalizar_filtros_nao_apaga_lubrificantes_nem_agregado(self):
        publicar_estoque_fisico(self._finalizar_com_item(LUB, '10'))
        publicar_estoque_fisico(self._finalizar_com_item(AGR, '7'))
        publicar_estoque_fisico(self._finalizar_com_item(FIL, '3'))
        saldos = dict(EstoqueFisico.objects.values_list('categoria', 'quantidade'))
        self.assertEqual(saldos, {LUB: Decimal('10'), AGR: Decimal('7'), FIL: Decimal('3')})

    def test_republicar_categoria_substitui_somente_ela(self):
        publicar_estoque_fisico(self._finalizar_com_item(LUB, '10'))
        publicar_estoque_fisico(self._finalizar_com_item(FIL, '3'))
        novo_fil = self._finalizar_com_item(FIL, '4')
        publicar_estoque_fisico(novo_fil)
        self.assertEqual(EstoqueFisico.objects.get(categoria=FIL).quantidade, Decimal('4'))
        self.assertEqual(EstoqueFisico.objects.get(categoria=FIL).inventario_origem, novo_fil)
        self.assertEqual(EstoqueFisico.objects.get(categoria=LUB).quantidade, Decimal('10'))

    def test_ultimo_finalizado_e_avaliado_por_categoria(self):
        lub = self._finalizar_com_item(LUB, '10')
        self._finalizar_com_item(FIL, '3')
        # Filtros finalizado depois não impede publicar Lubrificantes.
        publicar_estoque_fisico(lub)
        lub_antigo = self._finalizar_com_item(LUB, '1')
        self._finalizar_com_item(LUB, '2')
        with self.assertRaises(ConsolidacaoError):
            publicar_estoque_fisico(lub_antigo)

    def test_conflito_com_saldo_de_outra_categoria_bloqueia_sem_apagar(self):
        publicar_estoque_fisico(self._finalizar_com_item(LUB, '10'))
        anterior = list(EstoqueFisico.objects.values_list('pk', 'quantidade'))
        # Saldo legado de Filtros sobre o mesmo par produto/posição.
        EstoqueFisico.objects.create(
            produto=self.produtos[FIL],
            posicao=self.posicoes[FIL],
            quantidade=Decimal('1'),
            categoria=LUB,
            data_contagem=timezone.now(),
        )
        with self.assertRaises(ConsolidacaoError):
            publicar_estoque_fisico(self._finalizar_com_item(FIL, '5'))
        self.assertTrue(set(anterior) <= set(EstoqueFisico.objects.values_list('pk', 'quantidade')))

    def test_finalizar_pela_tela_publica_somente_a_categoria(self):
        publicar_estoque_fisico(self._finalizar_com_item(LUB, '10'))
        inventario = self.criar_inventario(FIL)
        InventarioItem.objects.create(
            inventario=inventario,
            posicao=self.posicoes[FIL],
            produto=self.produtos[FIL],
            quantidade_fisica=Decimal('6'),
        )
        self.client.post(reverse('inventario:finalizar', args=[inventario.pk]))
        self.assertEqual(
            dict(EstoqueFisico.objects.values_list('categoria', 'quantidade')),
            {LUB: Decimal('10'), FIL: Decimal('6')},
        )


class AuditoriaCategoriaTestCase(BaseCategoriaTestCase):
    def test_evento_operacional_grava_categoria_do_inventario(self):
        inventario = self.criar_inventario(AGR)
        salvar_contagem(
            inventario, self.posicoes[AGR], self.produtos[AGR], Decimal('1'), usuario_contagem=self.user,
        )
        eventos = InventarioAuditoriaEvento.objects.filter(inventario=inventario)
        self.assertTrue(eventos.exists())
        self.assertEqual(set(eventos.values_list('categoria', flat=True)), {AGR})

    def test_lock_registrado_no_inventario_da_categoria(self):
        inventario = self.criar_inventario(FIL)
        salvar_contagem(
            inventario, self.posicoes[FIL], self.produtos[FIL], Decimal('1'), usuario_contagem=self.user,
        )
        self.assertTrue(InventarioLock.objects.filter(inventario=inventario, posicao=self.posicoes[FIL]).exists())
        self.assertFalse(InventarioLock.objects.exclude(posicao=self.posicoes[FIL]).exists())


class SapImportacaoGlobalTestCase(BaseCategoriaTestCase):
    def _linha(self, produto, total):
        from estoque_sap.services.importacao_estoque_sap import CAMPOS_CANAIS

        dados = {campo: '0' for campo in CAMPOS_CANAIS}
        dados['canal_1'] = str(total)
        dados['codigo_produto'] = produto.codigo_produto
        dados['total'] = str(total)
        return dados

    def test_importacao_global_mantem_todas_as_categorias(self):
        from estoque_sap.services.importacao_estoque_sap import importar_dados

        self.selecionar_categoria(FIL)
        importar_dados(
            [self._linha(self.produtos[c], 5) for c in CATEGORIAS],
            arquivo_origem='sap.xlsx',
        )
        self.assertEqual(EstoqueSAP.objects.count(), 3)
        # Filtrar a tela por categoria não apaga as demais.
        for categoria in CATEGORIAS:
            self.selecionar_categoria(categoria)
            resposta = self.client.get(reverse('estoque_sap:lista'))
            self.assertEqual(resposta.status_code, 200)
        self.assertEqual(EstoqueSAP.objects.count(), 3)

    def test_tela_sap_filtra_pela_categoria_da_sessao(self):
        from estoque_sap.services.importacao_estoque_sap import importar_dados

        importar_dados(
            [self._linha(self.produtos[c], 5) for c in CATEGORIAS],
            arquivo_origem='sap.xlsx',
        )
        for categoria in CATEGORIAS:
            with self.subTest(categoria=categoria):
                self.selecionar_categoria(categoria)
                resposta = self.client.get(reverse('estoque_sap:lista'))
                codigos = [registro.produto.codigo_produto for registro in resposta.context['object_list']]
                self.assertEqual(codigos, [self.produtos[categoria].codigo_produto])


class TelasEscopadasTestCase(BaseCategoriaTestCase):
    def test_produtos_filtrados_pela_sessao(self):
        for categoria in CATEGORIAS:
            with self.subTest(categoria=categoria):
                self.selecionar_categoria(categoria)
                resposta = self.client.get(reverse('produtos:lista'))
                codigos = [p.codigo_produto for p in resposta.context['object_list']]
                self.assertEqual(codigos, [self.produtos[categoria].codigo_produto])

    def test_produtos_sem_classificacao_visiveis_em_filtro_proprio(self):
        resposta = self.client.get(reverse('produtos:lista'), {'classificacao': 'sem'})
        codigos = [p.codigo_produto for p in resposta.context['object_list']]
        self.assertEqual(codigos, ['P-PEND'])

    def test_posicoes_filtradas_pela_sessao(self):
        for categoria in CATEGORIAS:
            with self.subTest(categoria=categoria):
                self.selecionar_categoria(categoria)
                resposta = self.client.get(reverse('posicoes:lista'))
                codigos = [p.codigo for p in resposta.context['object_list']]
                self.assertEqual(codigos, [self.posicoes[categoria].codigo])

    def test_estoque_fisico_filtrado_pela_sessao(self):
        for categoria in CATEGORIAS:
            EstoqueFisico.objects.create(
                produto=self.produtos[categoria],
                posicao=self.posicoes[categoria],
                quantidade=Decimal('1'),
                categoria=categoria,
                data_contagem=timezone.now(),
            )
        for categoria in CATEGORIAS:
            with self.subTest(categoria=categoria):
                self.selecionar_categoria(categoria)
                resposta = self.client.get(reverse('estoque_fisico:lista'))
                self.assertEqual(
                    [r.categoria for r in resposta.context['object_list']],
                    [categoria],
                )
                exportacao = self.client.get(reverse('estoque_fisico:exportar'))
                self.assertEqual(exportacao.status_code, 200)
        self.assertEqual(EstoqueFisico.objects.count(), 3)

    def test_telas_principais_respondem_em_todas_as_categorias(self):
        nomes = [
            'home', 'dashboard_classico', 'confronto', 'aprovacao', 'consolidacao',
            'historico_unificado', 'comparativo:lista', 'inventario:lista',
            'produtos:lista', 'posicoes:lista', 'estoque_sap:lista', 'estoque_fisico:lista',
        ]
        for categoria in CATEGORIAS:
            self.selecionar_categoria(categoria)
            for nome in nomes:
                with self.subTest(categoria=categoria, tela=nome):
                    resposta = self.client.get(reverse(nome))
                    self.assertEqual(resposta.status_code, 200)
                    self.assertContains(resposta, 'id="barraCategoria"')


class ConfrontoCategoriaTestCase(BaseCategoriaTestCase):
    def test_lado_sap_restrito_a_categoria_do_inventario(self):
        from inventario.services.confronto import executar_confronto

        for categoria in CATEGORIAS:
            EstoqueSAP.objects.create(
                produto=self.produtos[categoria], total=Decimal('5'), canal_1=Decimal('5'), arquivo_origem='x',
            )
        EstoqueSAP.objects.create(
            produto=self.produto_sem_classe, total=Decimal('5'), canal_1=Decimal('5'), arquivo_origem='x',
        )
        for categoria in CATEGORIAS:
            with self.subTest(categoria=categoria):
                inventario = self.criar_inventario(categoria)
                resultado = executar_confronto(inventario.pk)
                self.assertEqual(
                    [linha.codigo_produto for linha in resultado.linhas],
                    [self.produtos[categoria].codigo_produto],
                )
        inventario = self.criar_inventario(LUB)
        incluir_produto_sem_classificacao(inventario, self.produto_sem_classe, self.perfil)
        codigos = {linha.codigo_produto for linha in executar_confronto(inventario.pk).linhas}
        self.assertEqual(codigos, {'P-LUB', 'P-PEND'})


class TarefasCategoriaTestCase(BaseCategoriaTestCase):
    def test_distribuicao_somente_posicoes_da_categoria(self):
        from inventario.services.tarefas import distribuir_tarefas_geral

        operador, _ = criar_usuario_teste(username='op.cat', perfil=Usuario.Perfil.OPERADOR)
        for categoria in CATEGORIAS:
            inventario = self.criar_inventario(categoria)
            distribuir_tarefas_geral(inventario, [operador], atribuido_por=self.user)
            with self.subTest(categoria=categoria):
                self.assertEqual(
                    list(InventarioTarefa.objects.filter(inventario=inventario).values_list('posicao_id', flat=True)),
                    [self.posicoes[categoria].pk],
                )


class LockCategoriaTestCase(BaseCategoriaTestCase):
    def test_lock_em_posicao_de_outra_categoria_e_recusado(self):
        from inventario.services.locks import LockError, adquirir_lock

        inventario = self.criar_inventario(FIL)
        with self.assertRaises(LockError):
            adquirir_lock(
                tipo_inventario=InventarioLock.TipoInventario.GERAL,
                inventario=inventario,
                posicao=self.posicoes[LUB],
                usuario=self.user,
            )
        self.assertFalse(InventarioLock.objects.exists())


class PocketCategoriaTestCase(BaseCategoriaTestCase):
    def _post(self, inventario, posicao, produto, ajax=False):
        extra = {'HTTP_X_REQUESTED_WITH': 'XMLHttpRequest'} if ajax else {}
        return self.client.post(
            reverse('pocket:contagem', args=[inventario.pk]),
            {
                'codigo_posicao': posicao.codigo,
                'codigo_produto': produto.codigo_produto,
                'quantidade_fisica': '2',
            },
            **extra,
        )

    def test_pocket_aceita_somente_combinacao_da_mesma_categoria(self):
        for categoria in CATEGORIAS:
            inventario = self.criar_inventario(categoria)
            for outra in CATEGORIAS:
                if outra == categoria:
                    continue
                with self.subTest(inventario=categoria, outra=outra):
                    self._post(inventario, self.posicoes[categoria], self.produtos[outra])
                    self._post(inventario, self.posicoes[outra], self.produtos[categoria])
                    self.assertFalse(InventarioItem.objects.filter(inventario=inventario).exists())
            self._post(inventario, self.posicoes[categoria], self.produtos[categoria])
            self.assertEqual(InventarioItem.objects.filter(inventario=inventario).count(), 1)

    def test_pocket_ajax_retorna_erro_claro_de_categoria(self):
        inventario = self.criar_inventario(FIL)
        resposta = self._post(inventario, self.posicoes[FIL], self.produtos[LUB], ajax=True)
        self.assertEqual(resposta.status_code, 400)
        self.assertIn('Lubrificantes', resposta.content.decode('utf-8'))

    def test_pocket_lock_de_posicao_de_outra_categoria(self):
        inventario = self.criar_inventario(AGR)
        resposta = self.client.post(
            reverse('pocket:contagem', args=[inventario.pk]),
            {'acao': 'lock_posicao', 'codigo_posicao': self.posicoes[LUB].codigo},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertIn(resposta.status_code, (400, 403, 409))
        self.assertFalse(InventarioLock.objects.exists())

    def test_pocket_lista_inventarios_da_sessao(self):
        ids = {categoria: self.criar_inventario(categoria).pk for categoria in CATEGORIAS}
        self.selecionar_categoria(AGR)
        resposta = self.client.get(reverse('pocket:selecionar'))
        self.assertEqual([i.pk for i in resposta.context['inventarios']], [ids[AGR]])

    def test_dados_mestres_escopados_pelo_inventario(self):
        inventario = self.criar_inventario(FIL)
        resposta = self.client.get(reverse('pocket:dados_mestres'), {'inventario_id': inventario.pk})
        dados = resposta.json()
        self.assertEqual(set(dados['mapa_posicoes']), {self.posicoes[FIL].codigo})
        self.assertEqual(
            set(dados['mapa_produtos']),
            {self.produtos[FIL].codigo_produto, self.produto_sem_classe.codigo_produto},
        )
        self.assertEqual((dados['inventario_id'], dados['categoria']), (inventario.pk, FIL))
        self.assertIn('no-cache', resposta['Cache-Control'])

    def test_precadastro_no_inventario_inclui_explicitamente_sem_alterar_setor(self):
        inventario = self.criar_inventario(FIL)
        Produto.objects.create(codigo_produto='CAT-EMB', descricao='cat', setor='FILTRO', embalagem='CX')
        self.client.post(
            reverse('pocket:precadastro_produto', args=[inventario.pk]),
            {'codigo_produto': 'NOVO-1', 'descricao': 'Novo filtro', 'embalagem': 'CX'},
        )
        produto = Produto.objects.get(codigo_produto='NOVO-1')
        self.assertTrue(InventarioProdutoInclusao.objects.filter(inventario=inventario, produto=produto).exists())
        self.assertEqual(produto.setor, 'PRÉ-CADASTRO')
        self._post(inventario, self.posicoes[FIL], produto)
        self.assertTrue(InventarioItem.objects.filter(inventario=inventario, produto=produto).exists())

    def test_precadastro_de_codigo_de_outra_categoria_e_recusado(self):
        inventario = self.criar_inventario(FIL)
        self.client.post(
            reverse('pocket:precadastro_produto', args=[inventario.pk]),
            {'codigo_produto': 'P-LUB', 'descricao': 'x'},
        )
        self.assertFalse(InventarioProdutoInclusao.objects.exists())


class PosicaoCategoriaTestCase(BaseCategoriaTestCase):
    def _planilha(self, linhas):
        import io

        from openpyxl import Workbook

        livro = Workbook()
        aba = livro.active
        aba.append(['codigo', 'posicao', 'categoria'])
        for linha in linhas:
            aba.append(list(linha))
        arquivo = io.BytesIO()
        livro.save(arquivo)
        arquivo.seek(0)
        return arquivo

    def test_default_do_modelo_e_lubrificantes_para_posicoes_existentes(self):
        self.assertEqual(Posicao._meta.get_field('categoria').default, LUB)

    def test_nova_posicao_exige_categoria(self):
        resposta = self.client.post(
            reverse('posicoes:criar'),
            {'codigo': 'NOVA-1', 'posicao': 'Nova', 'ativo': 'on'},
        )
        self.assertEqual(resposta.status_code, 200)
        self.assertFalse(Posicao.objects.filter(codigo='NOVA-1').exists())
        resposta = self.client.get(reverse('posicoes:criar'))
        self.assertIsNone(resposta.context['form'].fields['categoria'].initial)

    def test_nova_posicao_grava_categoria_escolhida(self):
        for categoria in CATEGORIAS:
            with self.subTest(categoria=categoria):
                codigo = f'NOVA-{categoria}'
                self.client.post(
                    reverse('posicoes:criar'),
                    {'codigo': codigo, 'posicao': 'x', 'categoria': categoria, 'ativo': 'on'},
                )
                self.assertEqual(Posicao.objects.get(codigo=codigo).categoria, categoria)

    def test_codigo_de_outra_categoria_gera_erro_claro_sem_duplicar(self):
        resposta = self.client.post(
            reverse('posicoes:criar'),
            {'codigo': 'POS-LUB', 'posicao': 'dup', 'categoria': FIL, 'ativo': 'on'},
        )
        self.assertContains(resposta, 'já pertence a uma posição de Lubrificantes')
        self.client.post(
            reverse('posicoes:criar'),
            {'codigo': 'pos-lub', 'posicao': 'dup', 'categoria': LUB, 'ativo': 'on'},
        )
        self.assertEqual(Posicao.objects.filter(codigo__iexact='POS-LUB').count(), 1)

    def test_edicao_aceita_categoria_quando_sem_vinculos(self):
        posicao = Posicao.objects.create(codigo='LIVRE', posicao='Livre', categoria=LUB)
        self.client.post(
            reverse('posicoes:editar', args=[posicao.pk]),
            {'codigo': 'LIVRE', 'posicao': 'Livre', 'categoria': AGR, 'ativo': 'on'},
        )
        posicao.refresh_from_db()
        self.assertEqual(posicao.categoria, AGR)

    def test_edicao_bloqueia_troca_com_estoque_publicado(self):
        EstoqueFisico.objects.create(
            produto=self.produtos[LUB],
            posicao=self.posicoes[LUB],
            quantidade=Decimal('1'),
            categoria=LUB,
            data_contagem=timezone.now(),
        )
        resposta = self.client.post(
            reverse('posicoes:editar', args=[self.posicoes[LUB].pk]),
            {'codigo': 'POS-LUB', 'posicao': 'x', 'categoria': FIL, 'ativo': 'on'},
        )
        self.assertContains(resposta, 'não pode ser alterada')
        self.posicoes[LUB].refresh_from_db()
        self.assertEqual(self.posicoes[LUB].categoria, LUB)

    def test_importacao_aceita_categoria_e_recusa_conflito(self):
        from posicoes.services.importacao_posicoes import (
            importar_dados,
            processar_arquivo,
            serializar_linhas_validas,
        )

        preview = processar_arquivo(self._planilha([
            ('IMP-F1', 'Filtro 1', 'Filtros'),
            ('IMP-A1', 'Agregado 1', 'agregado'),
            ('IMP-X1', 'Sem categoria', ''),
            ('IMP-Z1', 'Inválida', 'Geral'),
            ('POS-LUB', 'Conflito', 'Filtros'),
            ('POS-FIL', 'Atualiza descrição', 'Filtros'),
            ('POS-AGR', 'Sem categoria na linha', ''),
        ]))
        erros = {linha.codigo: linha.erros for linha in preview.linhas}
        self.assertEqual(erros['IMP-F1'], [])
        self.assertEqual(erros['IMP-A1'], [])
        self.assertTrue(erros['IMP-X1'])
        self.assertTrue(erros['IMP-Z1'])
        self.assertIn('já pertence a Lubrificantes', erros['POS-LUB'][0])
        self.assertEqual(erros['POS-FIL'], [])
        self.assertTrue(erros['POS-AGR'])
        resultado = importar_dados(serializar_linhas_validas(preview), rejeitados=preview.linhas_invalidas)
        self.assertEqual((resultado.inseridos, resultado.atualizados), (2, 1))
        self.assertEqual(Posicao.objects.get(codigo='IMP-F1').categoria, FIL)
        self.assertEqual(Posicao.objects.get(codigo='IMP-A1').categoria, AGR)
        self.assertEqual(Posicao.objects.get(codigo='POS-LUB').categoria, LUB)
        self.assertEqual(Posicao.objects.get(codigo='POS-FIL').posicao, 'Atualiza descrição')
        self.assertEqual(Posicao.objects.get(codigo='POS-FIL').categoria, FIL)

    def test_importacao_revalida_conflito_na_confirmacao(self):
        from posicoes.services.importacao_posicoes import importar_dados

        resultado = importar_dados([{'codigo': 'POS-AGR', 'posicao': 'x', 'categoria': FIL}])
        self.assertEqual(resultado.rejeitados, 1)
        self.assertEqual(Posicao.objects.get(codigo='POS-AGR').categoria, AGR)

    def test_precadastro_pocket_no_inventario_usa_categoria_do_inventario(self):
        inventario = self.criar_inventario(AGR)
        self.client.post(
            reverse('pocket:precadastro_posicao', args=[inventario.pk]),
            {'codigo': 'PRE-AGR', 'posicao': 'Pré'},
        )
        self.assertEqual(Posicao.objects.get(codigo='PRE-AGR').categoria, AGR)

    def test_precadastro_pocket_recusa_codigo_de_outra_categoria(self):
        inventario = self.criar_inventario(AGR)
        resposta = self.client.post(
            reverse('pocket:precadastro_posicao', args=[inventario.pk]),
            {'codigo': 'POS-LUB', 'posicao': 'x'},
        )
        self.assertContains(resposta, 'já pertence a uma posição de Lubrificantes')
        self.assertEqual(Posicao.objects.get(codigo='POS-LUB').categoria, LUB)

    def test_operador_precadastra_posicao_com_categoria_explicita(self):
        operador, _ = criar_usuario_teste(username='op.pos.cat', perfil=Usuario.Perfil.OPERADOR)
        self.client.force_login(operador)
        self.selecionar_categoria(FIL)
        resposta = self.client.get(reverse('pocket:operador_precadastro_posicao'))
        self.assertEqual(resposta.context['form']['categoria'].value(), FIL)
        self.client.post(
            reverse('pocket:operador_precadastro_posicao'),
            {'codigo': 'OP-POS-1', 'posicao': 'x'},
        )
        self.assertFalse(Posicao.objects.filter(codigo='OP-POS-1').exists())
        self.client.post(
            reverse('pocket:operador_precadastro_posicao'),
            {'codigo': 'OP-POS-1', 'posicao': 'x', 'categoria': FIL},
        )
        self.assertEqual(Posicao.objects.get(codigo='OP-POS-1').categoria, FIL)
