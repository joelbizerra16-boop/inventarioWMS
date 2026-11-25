"""Comparativo de contagens por categoria (Lubrificantes, Filtros, Agregado).

Garante que C1, C2 e C3 pertencem à mesma categoria da conferência, que a
análise usa somente produtos dessa categoria e que conferências históricas e
as permissões existentes continuam funcionando.
"""
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from accounts.models import Usuario
from accounts.test_utils import criar_usuario_teste
from core.categorias import SESSAO_CHAVE_CATEGORIA, CategoriaInventario
from estoque_sap.models import EstoqueSAP
from inventario.models import Inventario, InventarioItem
from inventario.models_comparativo import ComparativoContagem
from inventario.services.comparativo_contagens import (
    OPCAO_NOVA_REFERENCIA,
    ComparativoError,
    carregar_dados,
    excluir_comparativo,
    finalizar_comparativo,
    reabrir_comparativo,
    salvar_comparativo,
)
from posicoes.models import Posicao
from produtos.models import Produto

LUB = CategoriaInventario.LUBRIFICANTES
FIL = CategoriaInventario.FILTROS
AGR = CategoriaInventario.AGREGADO
CATEGORIAS = (LUB, FIL, AGR)
SETOR = {LUB: 'LUBRIFICANTE', FIL: 'FILTRO', AGR: 'AGREGADO'}


class ComparativoCategoriaTestCase(TestCase):
    def setUp(self):
        self.user, self.perfil = criar_usuario_teste(username='admin.comp.cat')
        self.client.force_login(self.user)
        self.produtos = {}
        self.posicoes = {}
        self.inventarios = {}
        for categoria in CATEGORIAS:
            sufixo = categoria[:3]
            self.produtos[categoria] = Produto.objects.create(
                codigo_produto=f'CP-{sufixo}', descricao=f'Produto {sufixo}', setor=SETOR[categoria],
            )
            self.posicoes[categoria] = Posicao.objects.create(
                codigo=f'CPOS-{sufixo}', posicao=f'Pos {sufixo}', categoria=categoria,
            )
            self.inventarios[categoria] = [
                self._inventario(categoria, Decimal(quantidade)) for quantidade in ('5', '5', '6')
            ]
            EstoqueSAP.objects.create(
                produto=self.produtos[categoria],
                canal_1=Decimal('5'),
                canal_110=Decimal('0'),
                total=Decimal('5'),
                arquivo_origem='sap.xlsx',
            )

    def _inventario(self, categoria, quantidade):
        inventario = Inventario.objects.create(
            usuario=self.perfil,
            status=Inventario.Status.EM_ANDAMENTO,
            categoria=categoria,
        )
        InventarioItem.objects.create(
            inventario=inventario,
            posicao=self.posicoes[categoria],
            produto=self.produtos[categoria],
            quantidade_fisica=quantidade,
        )
        return inventario

    def _salvar(self, categoria, inventarios, comparativo=None, nome='Conferência'):
        return salvar_comparativo(
            comparativo=comparativo,
            nome=nome,
            inventarios={ordem: inventario for ordem, inventario in enumerate(inventarios, start=1)},
            referencia_opcao=OPCAO_NOVA_REFERENCIA,
            estado=None,
            usuario=self.user,
            categoria=categoria,
        )

    def _selecionar(self, categoria):
        sessao = self.client.session
        sessao[SESSAO_CHAVE_CATEGORIA] = categoria
        sessao.save()

    def test_cada_categoria_associa_c1_c2_c3_da_mesma_categoria(self):
        for categoria in CATEGORIAS:
            with self.subTest(categoria=categoria):
                comparativo = self._salvar(categoria, self.inventarios[categoria])
                self.assertEqual(comparativo.categoria, categoria)
                self.assertEqual(
                    sorted(comparativo.associacoes.values_list('ordem', flat=True)),
                    [1, 2, 3],
                )

    def test_associacao_de_categorias_diferentes_e_impedida(self):
        for categoria in CATEGORIAS:
            for outra in CATEGORIAS:
                if outra == categoria:
                    continue
                for posicao_errada in range(3):
                    with self.subTest(categoria=categoria, outra=outra, ordem=posicao_errada + 1):
                        inventarios = list(self.inventarios[categoria])
                        inventarios[posicao_errada] = self.inventarios[outra][0]
                        with self.assertRaises(ComparativoError):
                            self._salvar(categoria, inventarios)
        self.assertFalse(ComparativoContagem.objects.exists())

    def test_edicao_nao_aceita_inventario_de_outra_categoria(self):
        comparativo = self._salvar(FIL, self.inventarios[FIL][:2])
        with self.assertRaises(ComparativoError):
            self._salvar(LUB, [self.inventarios[FIL][0], self.inventarios[FIL][1], self.inventarios[LUB][0]],
                         comparativo=comparativo)
        comparativo.refresh_from_db()
        self.assertEqual(comparativo.categoria, FIL)
        self.assertEqual(comparativo.associacoes.count(), 2)

    def test_tela_cria_com_categoria_da_sessao_e_filtra_inventarios(self):
        self._selecionar(AGR)
        resposta = self.client.get(reverse('comparativo:criar'))
        opcoes = set(resposta.context['form'].fields['c1'].queryset.values_list('pk', flat=True))
        self.assertEqual(opcoes, {inv.pk for inv in self.inventarios[AGR]})
        self.client.post(reverse('comparativo:criar'), {
            'nome': 'Agregado via tela',
            'c1': self.inventarios[AGR][0].pk,
            'c2': self.inventarios[AGR][1].pk,
            'c3': '',
            'referencia_sap': OPCAO_NOVA_REFERENCIA,
        })
        comparativo = ComparativoContagem.objects.get(nome='Agregado via tela')
        self.assertEqual(comparativo.categoria, AGR)

    def test_post_manual_com_inventario_de_outra_categoria_e_recusado(self):
        self._selecionar(FIL)
        self.client.post(reverse('comparativo:criar'), {
            'nome': 'Mistura',
            'c1': self.inventarios[FIL][0].pk,
            'c2': self.inventarios[LUB][0].pk,
            'c3': '',
            'referencia_sap': OPCAO_NOVA_REFERENCIA,
        })
        self.assertFalse(ComparativoContagem.objects.filter(nome='Mistura').exists())

    def test_edicao_usa_categoria_gravada_e_nao_a_da_sessao(self):
        comparativo = self._salvar(FIL, self.inventarios[FIL][:1])
        self._selecionar(LUB)
        resposta = self.client.get(reverse('comparativo:editar', args=[comparativo.pk]))
        opcoes = set(resposta.context['form'].fields['c2'].queryset.values_list('pk', flat=True))
        self.assertEqual(opcoes, {inv.pk for inv in self.inventarios[FIL]})

    def test_analise_usa_somente_produtos_da_categoria(self):
        for categoria in CATEGORIAS:
            with self.subTest(categoria=categoria):
                comparativo = self._salvar(categoria, self.inventarios[categoria], nome=f'An {categoria}')
                dados = carregar_dados(comparativo)
                self.assertEqual(set(dados.sap), {self.produtos[categoria].pk})
                self.assertEqual(
                    {lancamento.produto_id for lancamento in dados.lancamentos},
                    {self.produtos[categoria].pk},
                )

    def test_lista_filtrada_pela_categoria_da_sessao(self):
        ids = {categoria: self._salvar(categoria, self.inventarios[categoria][:1], nome=categoria).pk
               for categoria in CATEGORIAS}
        for categoria in CATEGORIAS:
            with self.subTest(categoria=categoria):
                self._selecionar(categoria)
                resposta = self.client.get(reverse('comparativo:lista'))
                self.assertEqual([c.pk for c in resposta.context['comparativos']], [ids[categoria]])

    def test_visualizacao_salvamento_finalizacao_e_exclusao_por_categoria(self):
        for categoria in CATEGORIAS:
            with self.subTest(categoria=categoria):
                comparativo = self._salvar(categoria, self.inventarios[categoria], nome=f'Ciclo {categoria}')
                self._selecionar(categoria)
                self.assertEqual(
                    self.client.get(reverse('comparativo:acompanhamento', args=[comparativo.pk])).status_code,
                    200,
                )
                self.assertEqual(
                    self.client.get(reverse('comparativo:exportar', args=[comparativo.pk])).status_code,
                    200,
                )
                finalizado = finalizar_comparativo(comparativo, self.user)
                self.assertTrue(finalizado.finalizado)
                # Regra existente: finalizada só é excluída após reabertura.
                with self.assertRaises(ComparativoError):
                    excluir_comparativo(finalizado, self.user)
                reaberto = reabrir_comparativo(finalizado, self.user)
                self.assertFalse(reaberto.finalizado)
                excluir_comparativo(reaberto, self.user)
                self.assertFalse(ComparativoContagem.objects.filter(pk=comparativo.pk).exists())

    def test_conferencia_historica_finalizada_e_preservada(self):
        comparativo = self._salvar(LUB, self.inventarios[LUB], nome='Histórica')
        finalizado = finalizar_comparativo(comparativo, self.user)
        snapshot = finalizado.snapshot_resultado
        # Mudanças posteriores no cadastro não alteram o resultado gravado.
        Produto.objects.filter(pk=self.produtos[LUB].pk).update(setor='PENDENTE')
        finalizado.refresh_from_db()
        self.assertEqual(finalizado.snapshot_resultado, snapshot)
        dados = carregar_dados(finalizado)
        self.assertIn(self.produtos[LUB].pk, {l.produto_id for l in dados.lancamentos})
        self.assertEqual(
            self.client.get(reverse('comparativo:acompanhamento', args=[finalizado.pk])).status_code,
            200,
        )

    def test_perfil_consulta_nao_configura_conferencia(self):
        consulta, _ = criar_usuario_teste(username='consulta.comp', perfil=Usuario.Perfil.CONSULTA)
        self.client.force_login(consulta)
        self.client.post(reverse('comparativo:criar'), {
            'nome': 'Sem permissão',
            'c1': self.inventarios[LUB][0].pk,
            'c2': '', 'c3': '',
            'referencia_sap': OPCAO_NOVA_REFERENCIA,
        })
        self.assertFalse(ComparativoContagem.objects.exists())

    def test_operador_pocket_nao_acessa_comparativo(self):
        operador, _ = criar_usuario_teste(username='op.comp', perfil=Usuario.Perfil.OPERADOR)
        self.client.force_login(operador)
        resposta = self.client.get(reverse('comparativo:lista'))
        self.assertNotEqual(resposta.status_code, 200)


class ContagemUnicaTestCase(ComparativoCategoriaTestCase):
    """Conferência com quantidade de contagens = 1 (C1 é o resultado)."""

    def test_classificacao_unica_e_regra_padrao_inalterada(self):
        from inventario.services.comparativo_contagens import (
            StatusContagem, StatusSAP, classificar_contagens, classificar_sap,
        )

        q = {1: Decimal('5'), 2: None, 3: None}
        unica = classificar_contagens(q, {1}, contagem_unica=True)
        self.assertEqual((unica.status, unica.valor_consenso), (StatusContagem.OK, Decimal('5')))
        self.assertEqual(classificar_sap(q, Decimal('5'), unica, contagem_unica=True).status, StatusSAP.OK)
        self.assertEqual(classificar_sap(q, Decimal('7'), unica, contagem_unica=True).status, StatusSAP.DIVERGENTE)
        vazio = {1: None, 2: None, 3: None}
        self.assertEqual(classificar_contagens(vazio, {1}, contagem_unica=True).status, StatusContagem.AGUARDANDO)
        # Regra padrão (2/3 contagens) permanece: uma contagem sozinha aguarda.
        self.assertEqual(classificar_contagens(q, {1}).status, StatusContagem.AGUARDANDO)

    def test_conferencia_de_uma_contagem_compara_c1_com_sap(self):
        from inventario.services.comparativo_contagens import (
            Canal, StatusContagem, StatusSAP, carregar_dados, montar_linhas_sku,
        )

        for categoria in CATEGORIAS:
            with self.subTest(categoria=categoria):
                c1 = self.inventarios[categoria][0]  # contou 5; SAP BRIDA = 5
                comparativo = salvar_comparativo(
                    comparativo=None, nome=f'Unica {categoria}', inventarios={1: c1},
                    referencia_opcao=OPCAO_NOVA_REFERENCIA, estado=None, usuario=self.user,
                    categoria=categoria, quantidade_contagens=1,
                )
                linhas = montar_linhas_sku(carregar_dados(comparativo), Canal.BRIDA)
                self.assertEqual(len(linhas), 1)
                self.assertEqual(linhas[0].contagens.status, StatusContagem.OK)
                self.assertEqual(linhas[0].contagens.valor_consenso, Decimal('5'))
                self.assertEqual(linhas[0].status_sap.status, StatusSAP.OK)
        # C1 diferente do SAP: divergente.
        c1 = self.inventarios[FIL][2]  # contou 6
        comparativo = salvar_comparativo(
            comparativo=None, nome='Unica divergente', inventarios={1: c1},
            referencia_opcao=OPCAO_NOVA_REFERENCIA, estado=None, usuario=self.user,
            categoria=FIL, quantidade_contagens=1,
        )
        linha = montar_linhas_sku(carregar_dados(comparativo), Canal.BRIDA)[0]
        self.assertEqual(linha.status_sap.status, StatusSAP.DIVERGENTE)

    def test_uma_contagem_nao_aceita_c2_ou_c3(self):
        with self.assertRaises(ComparativoError):
            salvar_comparativo(
                comparativo=None, nome='Inválida', inventarios={1: self.inventarios[LUB][0], 2: self.inventarios[LUB][1]},
                referencia_opcao='', estado=None, usuario=self.user, categoria=LUB, quantidade_contagens=1,
            )
        self.assertFalse(ComparativoContagem.objects.exists())

    def test_tela_cria_conferencia_de_uma_contagem_e_existentes_ficam_com_2(self):
        self._selecionar(AGR)
        formulario = self.client.get(reverse('comparativo:criar'))
        self.assertContains(formulario, 'Quantidade de contagens')
        self.assertContains(formulario, '1 contagem (C1 é o resultado)')
        self.assertContains(formulario, 'Com 1 contagem, só C1 entra')
        self.client.post(reverse('comparativo:criar'), {
            'nome': 'Só C1', 'quantidade_contagens': '1',
            'c1': self.inventarios[AGR][0].pk, 'c2': '', 'c3': '',
            'referencia_sap': OPCAO_NOVA_REFERENCIA,
        })
        self.assertEqual(ComparativoContagem.objects.get(nome='Só C1').quantidade_contagens, 1)
        self.client.post(reverse('comparativo:criar'), {
            'nome': 'Padrão', 'c1': self.inventarios[AGR][0].pk, 'c2': '', 'c3': '',
            'referencia_sap': OPCAO_NOVA_REFERENCIA,
        })
        self.assertEqual(ComparativoContagem.objects.get(nome='Padrão').quantidade_contagens, 2)
        resposta = self.client.get(reverse('comparativo:acompanhamento', args=[ComparativoContagem.objects.get(nome='Só C1').pk]))
        self.assertContains(resposta, 'data-quantidade-contagens="1"')
        self.assertContains(resposta, '1 contagem')

    def test_finalizacao_de_uma_contagem_fica_congelada(self):
        comparativo = salvar_comparativo(
            comparativo=None, nome='Unica final', inventarios={1: self.inventarios[LUB][0]},
            referencia_opcao=OPCAO_NOVA_REFERENCIA, estado=None, usuario=self.user,
            categoria=LUB, quantidade_contagens=1,
        )
        finalizado = finalizar_comparativo(comparativo, self.user)
        self.assertTrue(finalizado.finalizado)
        with self.assertRaises(ComparativoError):
            salvar_comparativo(
                comparativo=finalizado, nome='x', inventarios={1: self.inventarios[LUB][0]},
                referencia_opcao='', estado=None, usuario=self.user, quantidade_contagens=2,
            )
