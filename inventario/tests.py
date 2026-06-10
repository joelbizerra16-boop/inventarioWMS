from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Usuario
from accounts.test_utils import ClienteAutenticadoMixin
from estoque_fisico.models import EstoqueFisico
from estoque_sap.models import EstoqueSAP
from inventario.models import Inventario, InventarioItem
from inventario.services.aprovacao import (
    AprovacaoError,
    StatusAprovacao,
    aprovar_inventario,
    consultar_aprovacao,
    limpar_estado_aprovacao,
    obter_status_aprovacao,
    pode_aprovar,
    reabrir_inventario,
)
from inventario.services.consolidacao import (
    ConsolidacaoError,
    consolidar_estoque_fisico,
    limpar_estado_consolidacao,
    obter_auditoria_consolidacao,
    obter_id_inventario_finalizado_mais_recente,
    publicar_estoque_fisico,
)
from inventario.services.confronto import executar_confronto
from posicoes.models import Posicao
from produtos.models import Produto


class AprovacaoServiceTestCase(TestCase):
    def setUp(self):
        limpar_estado_aprovacao()

        self.usuario = Usuario.objects.create(
            nome='Operador Teste',
            login='operador',
            setor='Estoque',
            perfil=Usuario.Perfil.INVENTARIO,
        )
        self.produto_correto = Produto.objects.create(
            codigo_produto='P001',
            descricao='Produto Correto',
            setor='A',
            embalagem='Unidade',
        )
        self.produto_excesso = Produto.objects.create(
            codigo_produto='P002',
            descricao='Produto Excesso',
            setor='A',
            embalagem='Unidade',
        )
        self.produto_deficit = Produto.objects.create(
            codigo_produto='P003',
            descricao='Produto Déficit',
            setor='A',
            embalagem='Unidade',
        )
        self.posicao = Posicao.objects.create(
            codigo='POS01',
            posicao='A-01',
        )
        self.inventario = Inventario.objects.create(
            usuario=self.usuario,
            status=Inventario.Status.FINALIZADO,
        )

        InventarioItem.objects.create(
            inventario=self.inventario,
            posicao=self.posicao,
            produto=self.produto_correto,
            quantidade_fisica=Decimal('10'),
        )
        InventarioItem.objects.create(
            inventario=self.inventario,
            posicao=self.posicao,
            produto=self.produto_excesso,
            quantidade_fisica=Decimal('15'),
        )
        InventarioItem.objects.create(
            inventario=self.inventario,
            posicao=self.posicao,
            produto=self.produto_deficit,
            quantidade_fisica=Decimal('5'),
        )

        EstoqueSAP.objects.create(
            produto=self.produto_correto,
            total=Decimal('10'),
            arquivo_origem='teste.xlsx',
        )
        EstoqueSAP.objects.create(
            produto=self.produto_excesso,
            total=Decimal('10'),
            arquivo_origem='teste.xlsx',
        )
        EstoqueSAP.objects.create(
            produto=self.produto_deficit,
            total=Decimal('10'),
            arquivo_origem='teste.xlsx',
        )

    def tearDown(self):
        limpar_estado_aprovacao()
        limpar_estado_consolidacao()

    def test_consultar_aprovacao_marca_pendente_e_calcula_resumo(self):
        resultado = consultar_aprovacao(self.inventario.pk)

        self.assertEqual(
            obter_status_aprovacao(self.inventario.pk),
            StatusAprovacao.PENDENTE_APROVACAO,
        )
        self.assertEqual(resultado.resumo.total_produtos, 3)
        self.assertEqual(resultado.resumo.produtos_corretos, 1)
        self.assertEqual(resultado.resumo.produtos_divergentes, 2)
        self.assertEqual(resultado.resumo.produtos_excesso_fisico, 1)
        self.assertEqual(resultado.resumo.produtos_deficit_fisico, 1)
        self.assertEqual(resultado.resumo.acuracidade, Decimal('33.33'))
        self.assertEqual(len(resultado.linhas), 2)
        self.assertTrue(all(linha.possui_divergencia for linha in resultado.linhas))

    def test_aprovar_inventario_exige_confronto(self):
        with self.assertRaises(AprovacaoError):
            aprovar_inventario(self.inventario)

    def test_aprovar_inventario_com_sucesso(self):
        consultar_aprovacao(self.inventario.pk)
        aprovar_inventario(self.inventario)

        self.assertEqual(
            obter_status_aprovacao(self.inventario.pk),
            StatusAprovacao.APROVADO,
        )
        self.assertFalse(pode_aprovar(self.inventario.pk))

    def test_nao_aprovar_duas_vezes(self):
        consultar_aprovacao(self.inventario.pk)
        aprovar_inventario(self.inventario)

        with self.assertRaises(AprovacaoError):
            aprovar_inventario(self.inventario)

    def test_reabrir_inventario_com_itens_volta_para_em_andamento(self):
        consultar_aprovacao(self.inventario.pk)
        reabrir_inventario(self.inventario)

        self.inventario.refresh_from_db()
        self.assertEqual(self.inventario.status, Inventario.Status.EM_ANDAMENTO)
        self.assertIsNone(obter_status_aprovacao(self.inventario.pk))

    def test_reabrir_inventario_sem_itens_volta_para_aberto(self):
        inventario_vazio = Inventario.objects.create(
            usuario=self.usuario,
            status=Inventario.Status.FINALIZADO,
        )
        consultar_aprovacao(inventario_vazio.pk)
        reabrir_inventario(inventario_vazio)

        inventario_vazio.refresh_from_db()
        self.assertEqual(inventario_vazio.status, Inventario.Status.ABERTO)

    def test_reabrir_limpa_estado_aprovado(self):
        consultar_aprovacao(self.inventario.pk)
        aprovar_inventario(self.inventario)
        reabrir_inventario(self.inventario)

        self.assertIsNone(obter_status_aprovacao(self.inventario.pk))

    def test_consultar_nao_regride_status_aprovado(self):
        consultar_aprovacao(self.inventario.pk)
        aprovar_inventario(self.inventario)
        consultar_aprovacao(self.inventario.pk)

        self.assertEqual(
            obter_status_aprovacao(self.inventario.pk),
            StatusAprovacao.APROVADO,
        )

    def test_nao_altera_estoque_fisico_nem_sap(self):
        EstoqueFisico.objects.create(
            posicao=self.posicao,
            produto=self.produto_correto,
            quantidade=Decimal('99'),
            data_contagem=timezone.now(),
        )
        estoque_fisico_antes = list(
            EstoqueFisico.objects.values('produto_id', 'quantidade'),
        )
        estoque_sap_antes = list(
            EstoqueSAP.objects.values('produto_id', 'total'),
        )

        consultar_aprovacao(self.inventario.pk)
        aprovar_inventario(self.inventario)

        self.assertEqual(
            list(EstoqueFisico.objects.values('produto_id', 'quantidade')),
            estoque_fisico_antes,
        )
        self.assertEqual(
            list(EstoqueSAP.objects.values('produto_id', 'total')),
            estoque_sap_antes,
        )


class ConfrontoResumoTestCase(TestCase):
    def setUp(self):
        self.usuario = Usuario.objects.create(
            nome='Operador',
            login='op',
            setor='Estoque',
            perfil=Usuario.Perfil.INVENTARIO,
        )
        self.produto = Produto.objects.create(
            codigo_produto='PX',
            descricao='Produto X',
            setor='A',
            embalagem='Unidade',
        )
        self.posicao = Posicao.objects.create(
            codigo='PX01',
            posicao='B-01',
        )
        self.inventario = Inventario.objects.create(
            usuario=self.usuario,
            status=Inventario.Status.FINALIZADO,
        )

    def test_resumo_inclui_excesso_e_deficit(self):
        InventarioItem.objects.create(
            inventario=self.inventario,
            posicao=self.posicao,
            produto=self.produto,
            quantidade_fisica=Decimal('20'),
        )
        EstoqueSAP.objects.create(
            produto=self.produto,
            total=Decimal('10'),
            arquivo_origem='teste.xlsx',
        )

        resultado = executar_confronto(self.inventario.pk)

        self.assertEqual(resultado.resumo.produtos_excesso_fisico, 1)
        self.assertEqual(resultado.resumo.produtos_deficit_fisico, 0)
        linha = resultado.linhas[0]
        self.assertEqual(linha.embalagem, 'Unidade')
        self.assertEqual(linha.setor, 'A')
        self.assertEqual(linha.total_contabil, Decimal('10'))


class AprovacaoViewTestCase(ClienteAutenticadoMixin, TestCase):
    def setUp(self):
        limpar_estado_aprovacao()
        limpar_estado_consolidacao()

        self.usuario = Usuario.objects.create(
            nome='Operador View',
            login='opview',
            setor='Estoque',
            perfil=Usuario.Perfil.INVENTARIO,
        )
        self.produto = Produto.objects.create(
            codigo_produto='PV01',
            descricao='Produto View',
            setor='A',
            embalagem='Unidade',
        )
        self.posicao = Posicao.objects.create(
            codigo='PV01',
            posicao='C-01',
        )
        self.inventario_finalizado = Inventario.objects.create(
            usuario=self.usuario,
            status=Inventario.Status.FINALIZADO,
        )
        self.inventario_aberto = Inventario.objects.create(
            usuario=self.usuario,
            status=Inventario.Status.ABERTO,
        )
        InventarioItem.objects.create(
            inventario=self.inventario_finalizado,
            posicao=self.posicao,
            produto=self.produto,
            quantidade_fisica=Decimal('10'),
        )
        EstoqueSAP.objects.create(
            produto=self.produto,
            total=Decimal('10'),
            arquivo_origem='teste.xlsx',
        )
        self.autenticar_cliente(Usuario.Perfil.INVENTARIO)

    def tearDown(self):
        limpar_estado_aprovacao()
        limpar_estado_consolidacao()

    def test_get_aprovacao_com_inventario_finalizado(self):
        url = reverse('aprovacao')
        response = self.client.get(url, {'inventario': self.inventario_finalizado.pk})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Resumo executivo')
        self.assertContains(response, 'Aprovar Inventário')
        self.assertEqual(
            obter_status_aprovacao(self.inventario_finalizado.pk),
            StatusAprovacao.PENDENTE_APROVACAO,
        )

    def test_post_aprovar(self):
        consultar_aprovacao(self.inventario_finalizado.pk)
        url = reverse('aprovacao')

        response = self.client.post(url, {
            'inventario': self.inventario_finalizado.pk,
            'acao': 'aprovar',
        })

        self.assertRedirects(
            response,
            f'{url}?inventario={self.inventario_finalizado.pk}',
        )
        self.assertEqual(
            obter_status_aprovacao(self.inventario_finalizado.pk),
            StatusAprovacao.APROVADO,
        )
        self.assertEqual(EstoqueFisico.objects.count(), 0)

    def test_post_reabrir(self):
        consultar_aprovacao(self.inventario_finalizado.pk)
        url = reverse('aprovacao')

        response = self.client.post(url, {
            'inventario': self.inventario_finalizado.pk,
            'acao': 'reabrir',
        })

        self.assertRedirects(response, url)
        self.inventario_finalizado.refresh_from_db()
        self.assertEqual(
            self.inventario_finalizado.status,
            Inventario.Status.EM_ANDAMENTO,
        )

    def test_get_nao_lista_inventario_aberto(self):
        url = reverse('aprovacao')
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, f'value="{self.inventario_aberto.pk}"')

    def test_get_inventario_nao_finalizado_retorna_404(self):
        url = reverse('aprovacao')
        response = self.client.get(url, {'inventario': self.inventario_aberto.pk})

        self.assertEqual(response.status_code, 404)


class ConsolidacaoServiceTestCase(TestCase):
    def setUp(self):
        limpar_estado_aprovacao()
        limpar_estado_consolidacao()

        self.usuario = Usuario.objects.create(
            nome='Operador Consolidação',
            login='opcons',
            setor='Estoque',
            perfil=Usuario.Perfil.INVENTARIO,
        )
        self.produto_a = Produto.objects.create(
            codigo_produto='CA001',
            descricao='Produto A',
            setor='A',
            embalagem='Unidade',
        )
        self.produto_b = Produto.objects.create(
            codigo_produto='CA002',
            descricao='Produto B',
            setor='A',
            embalagem='Unidade',
        )
        self.posicao_1 = Posicao.objects.create(codigo='CP01', posicao='D-01')
        self.posicao_2 = Posicao.objects.create(codigo='CP02', posicao='D-02')
        self.inventario = Inventario.objects.create(
            usuario=self.usuario,
            status=Inventario.Status.FINALIZADO,
        )
        self.inventario_nao_aprovado = Inventario.objects.create(
            usuario=self.usuario,
            status=Inventario.Status.FINALIZADO,
        )
        self.inventario_aberto = Inventario.objects.create(
            usuario=self.usuario,
            status=Inventario.Status.ABERTO,
        )

        InventarioItem.objects.create(
            inventario=self.inventario,
            posicao=self.posicao_1,
            produto=self.produto_a,
            quantidade_fisica=Decimal('10'),
        )
        InventarioItem.objects.create(
            inventario=self.inventario,
            posicao=self.posicao_1,
            produto=self.produto_b,
            quantidade_fisica=Decimal('5'),
        )
        InventarioItem.objects.create(
            inventario=self.inventario,
            posicao=self.posicao_2,
            produto=self.produto_a,
            quantidade_fisica=Decimal('7'),
        )

    def tearDown(self):
        limpar_estado_aprovacao()
        limpar_estado_consolidacao()

    def test_consolidacao_simples(self):
        resultado = consolidar_estoque_fisico(self.inventario)

        self.assertEqual(resultado.registros_processados, 3)
        self.assertEqual(resultado.registros_criados, 3)
        self.assertEqual(resultado.registros_atualizados, 0)
        self.assertEqual(resultado.total_produtos, 2)
        self.assertEqual(resultado.total_posicoes, 2)
        self.assertEqual(resultado.quantidade_consolidada, Decimal('22'))
        self.assertEqual(EstoqueFisico.objects.count(), 3)

        estoque = EstoqueFisico.objects.get(
            produto=self.produto_a,
            posicao=self.posicao_2,
        )
        self.assertEqual(estoque.quantidade, Decimal('7'))
        self.assertEqual(
            estoque.inventario_origem_id,
            self.inventario.pk,
        )

    def test_reexecucao_recria_registros(self):
        consolidar_estoque_fisico(self.inventario)
        resultado = consolidar_estoque_fisico(self.inventario)

        self.assertEqual(resultado.registros_processados, 3)
        self.assertEqual(resultado.registros_criados, 3)
        self.assertEqual(resultado.registros_atualizados, 0)
        self.assertEqual(EstoqueFisico.objects.count(), 3)

    def test_publicacao_bloqueia_inventario_antigo(self):
        consolidar_estoque_fisico(self.inventario)

        inventario_maior = Inventario.objects.create(
            usuario=self.usuario,
            status=Inventario.Status.FINALIZADO,
        )
        InventarioItem.objects.create(
            inventario=inventario_maior,
            posicao=self.posicao_1,
            produto=self.produto_b,
            quantidade_fisica=Decimal('7'),
        )

        with self.assertRaises(ConsolidacaoError):
            consolidar_estoque_fisico(self.inventario)

        resultado = consolidar_estoque_fisico(inventario_maior)

        self.assertGreater(inventario_maior.pk, self.inventario.pk)
        self.assertEqual(
            obter_id_inventario_finalizado_mais_recente(),
            inventario_maior.pk,
        )
        self.assertEqual(resultado.inventario_id, inventario_maior.pk)
        self.assertEqual(EstoqueFisico.objects.count(), 1)
        estoque = EstoqueFisico.objects.get()
        self.assertEqual(estoque.produto, self.produto_b)
        self.assertEqual(estoque.quantidade, Decimal('7'))

    def test_inventario_homologacao_excluido_da_selecao(self):
        usuario_homolog = Usuario.objects.create(
            nome='Homologação',
            login='homolog-teste',
            setor='Estoque',
            perfil=Usuario.Perfil.INVENTARIO,
        )
        inventario_homolog = Inventario.objects.create(
            usuario=usuario_homolog,
            status=Inventario.Status.FINALIZADO,
        )
        InventarioItem.objects.create(
            inventario=inventario_homolog,
            posicao=self.posicao_2,
            produto=self.produto_b,
            quantidade_fisica=Decimal('999'),
        )

        self.assertGreater(inventario_homolog.pk, self.inventario.pk)
        self.assertEqual(
            obter_id_inventario_finalizado_mais_recente(),
            self.inventario.pk,
        )

        resultado = consolidar_estoque_fisico(self.inventario)

        self.assertEqual(resultado.inventario_id, self.inventario.pk)
        self.assertEqual(EstoqueFisico.objects.count(), 3)

    def test_publicacao_remove_estoque_homologacao_anterior(self):
        EstoqueFisico.objects.create(
            posicao=self.posicao_1,
            produto=self.produto_a,
            quantidade=Decimal('100'),
            data_contagem=timezone.now(),
        )

        consolidar_estoque_fisico(self.inventario)

        self.assertEqual(EstoqueFisico.objects.count(), 3)
        self.assertFalse(
            EstoqueFisico.objects.filter(
                produto=self.produto_a,
                posicao=self.posicao_1,
                quantidade=Decimal('100'),
            ).exists(),
        )
        self.assertEqual(
            EstoqueFisico.objects.get(
                produto=self.produto_a,
                posicao=self.posicao_1,
            ).quantidade,
            Decimal('10'),
        )

    def test_publicacao_inventario_b_substitui_integralmente_inventario_a(self):
        produto_x = self.produto_a
        produto_y = self.produto_b
        posicao = self.posicao_1

        inventario_a = self.inventario
        InventarioItem.objects.filter(inventario=inventario_a).delete()
        InventarioItem.objects.create(
            inventario=inventario_a,
            posicao=posicao,
            produto=produto_x,
            quantidade_fisica=Decimal('100'),
        )

        consolidar_estoque_fisico(inventario_a)
        self.assertEqual(EstoqueFisico.objects.count(), 1)
        self.assertEqual(
            EstoqueFisico.objects.get().quantidade,
            Decimal('100'),
        )

        inventario_b = Inventario.objects.create(
            usuario=self.usuario,
            status=Inventario.Status.FINALIZADO,
        )
        InventarioItem.objects.create(
            inventario=inventario_b,
            posicao=posicao,
            produto=produto_x,
            quantidade_fisica=Decimal('150'),
        )
        InventarioItem.objects.create(
            inventario=inventario_b,
            posicao=posicao,
            produto=produto_y,
            quantidade_fisica=Decimal('20'),
        )

        consolidar_estoque_fisico(inventario_b)

        estoques = {
            (item.produto_id, item.quantidade)
            for item in EstoqueFisico.objects.all()
        }
        self.assertEqual(EstoqueFisico.objects.count(), 2)
        self.assertEqual(
            estoques,
            {
                (produto_x.pk, Decimal('150')),
                (produto_y.pk, Decimal('20')),
            },
        )
        self.assertFalse(
            EstoqueFisico.objects.filter(
                produto=produto_x,
                quantidade=Decimal('100'),
            ).exists(),
        )

    def test_segundo_inventario_substitui_estoque_anterior(self):
        consolidar_estoque_fisico(self.inventario)
        self.assertEqual(EstoqueFisico.objects.count(), 3)

        inventario_b = Inventario.objects.create(
            usuario=self.usuario,
            status=Inventario.Status.FINALIZADO,
        )
        InventarioItem.objects.create(
            inventario=inventario_b,
            posicao=self.posicao_1,
            produto=self.produto_b,
            quantidade_fisica=Decimal('99'),
        )

        consolidar_estoque_fisico(inventario_b)

        self.assertEqual(EstoqueFisico.objects.count(), 1)
        estoque = EstoqueFisico.objects.get()
        self.assertEqual(estoque.produto, self.produto_b)
        self.assertEqual(estoque.posicao, self.posicao_1)
        self.assertEqual(estoque.quantidade, Decimal('99'))
        self.assertFalse(
            EstoqueFisico.objects.filter(produto=self.produto_a).exists(),
        )

    def test_idempotencia_tres_execucoes(self):
        consolidar_estoque_fisico(self.inventario)
        consolidar_estoque_fisico(self.inventario)
        consolidar_estoque_fisico(self.inventario)

        self.assertEqual(EstoqueFisico.objects.count(), 3)
        quantidades = sorted(
            EstoqueFisico.objects.values_list('quantidade', flat=True),
        )
        self.assertEqual(quantidades, [Decimal('5.000'), Decimal('7.000'), Decimal('10.000')])

    def test_bloqueio_inventario_aberto(self):
        with self.assertRaises(ConsolidacaoError):
            consolidar_estoque_fisico(self.inventario_aberto)

    def test_nao_altera_estoque_sap_nem_inventario_item(self):
        EstoqueSAP.objects.create(
            produto=self.produto_a,
            total=Decimal('100'),
            arquivo_origem='teste.xlsx',
        )
        itens_antes = list(
            InventarioItem.objects.filter(
                inventario=self.inventario,
            ).values('produto_id', 'posicao_id', 'quantidade_fisica'),
        )
        sap_antes = list(EstoqueSAP.objects.values('produto_id', 'total'))

        consolidar_estoque_fisico(self.inventario)

        self.assertEqual(
            list(
                InventarioItem.objects.filter(
                    inventario=self.inventario,
                ).values('produto_id', 'posicao_id', 'quantidade_fisica'),
            ),
            itens_antes,
        )
        self.assertEqual(
            list(EstoqueSAP.objects.values('produto_id', 'total')),
            sap_antes,
        )

    def test_auditoria_em_memoria(self):
        resultado = consolidar_estoque_fisico(self.inventario)
        auditoria = obter_auditoria_consolidacao(self.inventario.pk)

        self.assertIsNotNone(auditoria)
        self.assertEqual(auditoria.registros_processados, resultado.registros_processados)
        self.assertEqual(auditoria.registros_criados, resultado.registros_criados)


class PublicacaoAoFinalizarTestCase(TestCase):
    def setUp(self):
        limpar_estado_consolidacao()
        self.usuario = Usuario.objects.create(
            nome='Operador Publicação',
            login='oppub',
            setor='Estoque',
            perfil=Usuario.Perfil.INVENTARIO,
        )
        self.posicao = Posicao.objects.create(codigo='PUB01', posicao='P-01')

    def _criar_produto(self, codigo: str) -> Produto:
        return Produto.objects.create(
            codigo_produto=codigo,
            descricao=f'Produto {codigo}',
            setor='A',
            embalagem='Unidade',
        )

    def _finalizar_e_publicar(self, inventario: Inventario):
        inventario.status = Inventario.Status.FINALIZADO
        inventario.save(update_fields=['status'])
        return publicar_estoque_fisico(inventario)

    def test_cenario_100_produto_a(self):
        produto_a = self._criar_produto('PUB100A')
        inventario = Inventario.objects.create(
            usuario=self.usuario,
            status=Inventario.Status.EM_ANDAMENTO,
        )
        InventarioItem.objects.create(
            inventario=inventario,
            posicao=self.posicao,
            produto=produto_a,
            quantidade_fisica=Decimal('10'),
        )

        resultado = self._finalizar_e_publicar(inventario)

        self.assertEqual(resultado.registros_processados, 1)
        self.assertEqual(EstoqueFisico.objects.count(), 1)
        estoque = EstoqueFisico.objects.get()
        self.assertEqual(estoque.produto, produto_a)
        self.assertEqual(estoque.quantidade, Decimal('10'))
        self.assertEqual(estoque.inventario_origem_id, inventario.pk)
        self.assertIsNotNone(estoque.data_publicacao)

    def test_cenario_101_produto_b_substitui_a(self):
        produto_a = self._criar_produto('PUB101A')
        produto_b = self._criar_produto('PUB101B')

        inventario_a = Inventario.objects.create(
            usuario=self.usuario,
            status=Inventario.Status.EM_ANDAMENTO,
        )
        InventarioItem.objects.create(
            inventario=inventario_a,
            posicao=self.posicao,
            produto=produto_a,
            quantidade_fisica=Decimal('10'),
        )
        self._finalizar_e_publicar(inventario_a)

        inventario_b = Inventario.objects.create(
            usuario=self.usuario,
            status=Inventario.Status.EM_ANDAMENTO,
        )
        InventarioItem.objects.create(
            inventario=inventario_b,
            posicao=self.posicao,
            produto=produto_b,
            quantidade_fisica=Decimal('20'),
        )
        self._finalizar_e_publicar(inventario_b)

        self.assertEqual(EstoqueFisico.objects.count(), 1)
        estoque = EstoqueFisico.objects.get()
        self.assertEqual(estoque.produto, produto_b)
        self.assertEqual(estoque.quantidade, Decimal('20'))
        self.assertEqual(estoque.inventario_origem_id, inventario_b.pk)
        self.assertFalse(EstoqueFisico.objects.filter(produto=produto_a).exists())

    def test_cenario_102_produto_c_substitui_anteriores(self):
        produto_a = self._criar_produto('PUB102A')
        produto_b = self._criar_produto('PUB102B')
        produto_c = self._criar_produto('PUB102C')

        for produto in (produto_a, produto_b):
            inventario = Inventario.objects.create(
                usuario=self.usuario,
                status=Inventario.Status.EM_ANDAMENTO,
            )
            InventarioItem.objects.create(
                inventario=inventario,
                posicao=self.posicao,
                produto=produto,
                quantidade_fisica=Decimal('1'),
            )
            self._finalizar_e_publicar(inventario)

        inventario_c = Inventario.objects.create(
            usuario=self.usuario,
            status=Inventario.Status.EM_ANDAMENTO,
        )
        InventarioItem.objects.create(
            inventario=inventario_c,
            posicao=self.posicao,
            produto=produto_c,
            quantidade_fisica=Decimal('30'),
        )
        self._finalizar_e_publicar(inventario_c)

        self.assertEqual(EstoqueFisico.objects.count(), 1)
        estoque = EstoqueFisico.objects.get()
        self.assertEqual(estoque.produto, produto_c)
        self.assertEqual(estoque.quantidade, Decimal('30'))
        self.assertEqual(estoque.inventario_origem_id, inventario_c.pk)


class InventarioFinalizarViewTestCase(ClienteAutenticadoMixin, TestCase):
    def setUp(self):
        limpar_estado_consolidacao()
        self.usuario = Usuario.objects.create(
            nome='Operador Finalizar',
            login='opfin',
            setor='Estoque',
            perfil=Usuario.Perfil.INVENTARIO,
        )
        self.produto = Produto.objects.create(
            codigo_produto='FIN01',
            descricao='Produto Finalizar',
            setor='A',
            embalagem='Unidade',
        )
        self.posicao = Posicao.objects.create(codigo='FIN01', posicao='F-01')
        self.inventario = Inventario.objects.create(
            usuario=self.usuario,
            status=Inventario.Status.EM_ANDAMENTO,
        )
        InventarioItem.objects.create(
            inventario=self.inventario,
            posicao=self.posicao,
            produto=self.produto,
            quantidade_fisica=Decimal('8'),
        )
        self.autenticar_cliente(Usuario.Perfil.INVENTARIO)

    def test_finalizar_publica_estoque_fisico(self):
        url = reverse('inventario:finalizar', args=[self.inventario.pk])
        response = self.client.post(url)

        self.assertRedirects(response, reverse('inventario:lista'))
        self.inventario.refresh_from_db()
        self.assertEqual(self.inventario.status, Inventario.Status.FINALIZADO)
        self.assertEqual(EstoqueFisico.objects.count(), 1)
        estoque = EstoqueFisico.objects.get()
        self.assertEqual(estoque.quantidade, Decimal('8'))
        self.assertEqual(estoque.inventario_origem_id, self.inventario.pk)


class ConsolidacaoViewTestCase(ClienteAutenticadoMixin, TestCase):
    def setUp(self):
        limpar_estado_aprovacao()
        limpar_estado_consolidacao()

        self.usuario = Usuario.objects.create(
            nome='Operador View Consolidação',
            login='opvcons',
            setor='Estoque',
            perfil=Usuario.Perfil.INVENTARIO,
        )
        self.produto = Produto.objects.create(
            codigo_produto='CV01',
            descricao='Produto View Consolidação',
            setor='A',
            embalagem='Unidade',
        )
        self.posicao = Posicao.objects.create(codigo='CV01', posicao='E-01')
        self.inventario = Inventario.objects.create(
            usuario=self.usuario,
            status=Inventario.Status.FINALIZADO,
        )
        self.inventario_nao_aprovado = Inventario.objects.create(
            usuario=self.usuario,
            status=Inventario.Status.FINALIZADO,
        )
        InventarioItem.objects.create(
            inventario=self.inventario,
            posicao=self.posicao,
            produto=self.produto,
            quantidade_fisica=Decimal('12'),
        )
        self.autenticar_cliente(Usuario.Perfil.INVENTARIO)

    def tearDown(self):
        limpar_estado_aprovacao()
        limpar_estado_consolidacao()

    def test_get_consolidacao_com_inventario_aprovado(self):
        url = reverse('consolidacao')
        response = self.client.get(url, {'inventario': self.inventario.pk})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Consolidar Estoque Físico')
        self.assertContains(response, 'Quantidade Consolidada')

    def test_post_consolidar(self):
        url = reverse('consolidacao')
        response = self.client.post(url, {'inventario': self.inventario.pk})

        self.assertRedirects(
            response,
            f'{url}?inventario={self.inventario.pk}',
        )
        self.assertEqual(EstoqueFisico.objects.count(), 1)
        self.assertEqual(
            EstoqueFisico.objects.get().quantidade,
            Decimal('12'),
        )

    def test_get_nao_lista_inventario_sem_itens(self):
        url = reverse('consolidacao')
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, f'value="{self.inventario_nao_aprovado.pk}"')

    def test_post_bloqueia_inventario_antigo(self):
        inventario_novo = Inventario.objects.create(
            usuario=self.usuario,
            status=Inventario.Status.FINALIZADO,
        )
        InventarioItem.objects.create(
            inventario=inventario_novo,
            posicao=self.posicao,
            produto=self.produto,
            quantidade_fisica=Decimal('99'),
        )

        url = reverse('consolidacao')
        response = self.client.post(url, {
            'inventario': self.inventario.pk,
        })

        self.assertRedirects(response, url)
        self.assertEqual(EstoqueFisico.objects.count(), 0)


class CiclicoServiceTestCase(TestCase):
    def setUp(self):
        from inventario.services.ciclico import limpar_estado_ciclico
        limpar_estado_ciclico()

        self.produto_a = Produto.objects.create(
            codigo_produto='CY001',
            descricao='Produto Cíclico A',
            setor='A',
            embalagem='Unidade',
        )
        self.produto_b = Produto.objects.create(
            codigo_produto='CY002',
            descricao='Produto Cíclico B',
            setor='A',
            embalagem='Unidade',
        )
        self.posicao = Posicao.objects.create(codigo='CY01', posicao='H-01')

        EstoqueSAP.objects.create(
            produto=self.produto_a,
            total=Decimal('10'),
            arquivo_origem='teste.xlsx',
        )
        EstoqueSAP.objects.create(
            produto=self.produto_b,
            total=Decimal('20'),
            arquivo_origem='teste.xlsx',
        )
        EstoqueFisico.objects.create(
            posicao=self.posicao,
            produto=self.produto_a,
            quantidade=Decimal('10'),
            data_contagem=timezone.now(),
        )
        EstoqueFisico.objects.create(
            posicao=self.posicao,
            produto=self.produto_b,
            quantidade=Decimal('20'),
            data_contagem=timezone.now(),
        )

    def tearDown(self):
        from inventario.services.ciclico import limpar_estado_ciclico
        limpar_estado_ciclico()

    def test_criar_ciclo_congela_itens(self):
        from inventario.services.ciclico import (
            StatusItemCiclico,
            criar_ciclo,
            obter_itens_ciclo,
        )

        criar_ciclo()
        itens = obter_itens_ciclo()

        self.assertEqual(len(itens), 2)
        self.assertEqual(itens[0].quantidade_sap, Decimal('10'))
        self.assertEqual(itens[0].status_contagem, StatusItemCiclico.PENDENTE)

    def test_resumo_e_percentual_executado(self):
        from accounts.test_utils import criar_usuario_teste
        from inventario.models import CicloInventarioSku
        from inventario.services.ciclico import (
            criar_ciclo,
            obter_resumo_ciclico,
            salvar_contagem_sku,
        )

        user, _ = criar_usuario_teste(perfil=Usuario.Perfil.INVENTARIO)
        criar_ciclo()
        sku = CicloInventarioSku.objects.filter(codigo_produto='CY001').first()
        posicao = sku.posicoes.get()
        salvar_contagem_sku(sku.pk, {posicao.pk: Decimal('10')}, user)

        resumo = obter_resumo_ciclico()

        self.assertEqual(resumo.total_skus, 2)
        self.assertEqual(resumo.skus_pendentes, 1)
        self.assertEqual(resumo.skus_contados, 1)
        self.assertEqual(resumo.skus_divergentes, 0)
        self.assertEqual(resumo.percentual_executado, Decimal('50.00'))

    def test_nao_altera_estoque_sap_fisico_nem_inventario_item(self):
        from inventario.services.ciclico import criar_ciclo

        sap_antes = EstoqueSAP.objects.count()
        fisico_antes = EstoqueFisico.objects.count()
        itens_antes = InventarioItem.objects.count()

        criar_ciclo()

        self.assertEqual(EstoqueSAP.objects.count(), sap_antes)
        self.assertEqual(EstoqueFisico.objects.count(), fisico_antes)
        self.assertEqual(InventarioItem.objects.count(), itens_antes)


class CiclicoViewTestCase(ClienteAutenticadoMixin, TestCase):
    def setUp(self):
        from inventario.services.ciclico import limpar_estado_ciclico
        limpar_estado_ciclico()

        self.produto = Produto.objects.create(
            codigo_produto='CYV01',
            descricao='Produto View Cíclico',
            setor='A',
            embalagem='Unidade',
        )
        self.posicao = Posicao.objects.create(codigo='CYV01', posicao='I-01')
        EstoqueSAP.objects.create(
            produto=self.produto,
            total=Decimal('5'),
            arquivo_origem='teste.xlsx',
        )
        EstoqueFisico.objects.create(
            posicao=self.posicao,
            produto=self.produto,
            quantidade=Decimal('5'),
            data_contagem=timezone.now(),
        )
        self.autenticar_cliente(Usuario.Perfil.INVENTARIO)

    def tearDown(self):
        from inventario.services.ciclico import limpar_estado_ciclico
        limpar_estado_ciclico()

    def test_post_criar_ciclo(self):
        url = reverse('ciclico')
        response = self.client.post(url, {'acao': 'criar'})
        self.assertRedirects(response, url)

    def test_executar_exclusao_sku(self):
        from inventario.models import CicloInventarioSku

        self.client.post(reverse('ciclico'), {'acao': 'criar'})
        sku = CicloInventarioSku.objects.get()
        response = self.client.post(
            f"{reverse('ciclico_executar')}?iniciado=1",
            {
                'sku_id': sku.pk,
                'acao': 'excluir',
                'motivo_exclusao': 'Obsoleto',
            },
        )
        self.assertEqual(response.status_code, 302)
        sku.refresh_from_db()
        self.assertEqual(sku.status_contagem, 'EXCLUIDO')
