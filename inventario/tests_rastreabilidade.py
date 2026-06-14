from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from accounts.models import Usuario
from accounts.test_utils import ClienteAutenticadoMixin, criar_usuario_teste
from estoque_fisico.models import EstoqueFisico
from inventario.models import Inventario, InventarioItem
from inventario.services.consolidacao import limpar_estado_consolidacao, publicar_estoque_fisico
from inventario.services.contagem import salvar_contagem
from posicoes.models import Posicao
from produtos.models import Produto


class RastreabilidadeContagemTestCase(TestCase):
    def setUp(self):
        limpar_estado_consolidacao()
        self.user_a, self.operador_a = criar_usuario_teste(
            username='usuario.a',
            perfil=Usuario.Perfil.INVENTARIO,
        )
        self.operador_a.nome = 'João Silva'
        self.operador_a.save(update_fields=['nome'])

        self.user_b, self.operador_b = criar_usuario_teste(
            username='usuario.b',
            perfil=Usuario.Perfil.INVENTARIO,
        )
        self.operador_b.nome = 'Maria Souza'
        self.operador_b.save(update_fields=['nome'])

        self.usuario_inventario = Usuario.objects.create(
            nome='Responsável Inventário',
            login='resp.inv',
            setor='Estoque',
            perfil=Usuario.Perfil.INVENTARIO,
        )
        self.produto = Produto.objects.create(
            codigo_produto='110830',
            descricao='Produto Rastreio',
            setor='A',
        )
        self.posicao = Posicao.objects.create(codigo='RAST01', posicao='R-01')
        self.inventario = Inventario.objects.create(
            usuario=self.usuario_inventario,
            status=Inventario.Status.EM_ANDAMENTO,
        )

    def test_cenario_1_usuario_a_realiza_contagem(self):
        item = salvar_contagem(
            self.inventario,
            self.posicao,
            self.produto,
            Decimal('15'),
            usuario_contagem=self.user_a,
            origem_contagem=InventarioItem.OrigemContagem.POCKET,
        )

        self.assertEqual(item.usuario_contagem_id, self.user_a.pk)
        self.assertIsNotNone(item.data_contagem)
        self.assertEqual(item.origem_contagem, InventarioItem.OrigemContagem.POCKET)

    def test_cenario_2_usuario_b_atualiza_contagem(self):
        item_a = salvar_contagem(
            self.inventario,
            self.posicao,
            self.produto,
            Decimal('15'),
            usuario_contagem=self.user_a,
            origem_contagem=InventarioItem.OrigemContagem.POCKET,
        )
        item = salvar_contagem(
            self.inventario,
            self.posicao,
            self.produto,
            Decimal('20'),
            item_existente=item_a,
            usuario_contagem=self.user_b,
            origem_contagem=InventarioItem.OrigemContagem.WEB,
        )

        self.assertEqual(item.usuario_contagem_id, self.user_b.pk)
        self.assertEqual(item.origem_contagem, InventarioItem.OrigemContagem.WEB)
        self.assertEqual(InventarioItem.objects.count(), 1)

    def test_registro_legado_exibe_rotulos(self):
        item = InventarioItem.objects.create(
            inventario=self.inventario,
            posicao=self.posicao,
            produto=self.produto,
            quantidade_fisica=Decimal('5'),
        )

        self.assertEqual(item.usuario_contagem_nome, 'Não informado')
        self.assertEqual(item.origem_contagem_rotulo, 'Legado')


class RastreabilidadeContagemViewTestCase(ClienteAutenticadoMixin, TestCase):
    def setUp(self):
        self.autenticar_cliente()
        limpar_estado_consolidacao()

        self.user_a, self.operador_a = criar_usuario_teste(
            username='view.usuario.a',
            perfil=Usuario.Perfil.INVENTARIO,
        )
        self.operador_a.nome = 'João Silva'
        self.operador_a.save(update_fields=['nome'])

        self.user_b, self.operador_b = criar_usuario_teste(
            username='view.usuario.b',
            perfil=Usuario.Perfil.INVENTARIO,
        )
        self.operador_b.nome = 'Maria Souza'
        self.operador_b.save(update_fields=['nome'])

        self.usuario_inventario = Usuario.objects.create(
            nome='Responsável',
            login='resp.view',
            setor='Estoque',
            perfil=Usuario.Perfil.INVENTARIO,
        )
        self.produto_a = Produto.objects.create(
            codigo_produto='110830',
            descricao='Produto A',
            setor='A',
        )
        self.produto_b = Produto.objects.create(
            codigo_produto='124380',
            descricao='Produto B',
            setor='A',
        )
        self.posicao = Posicao.objects.create(codigo='RASTV01', posicao='RV-01')
        self.inventario = Inventario.objects.create(
            usuario=self.usuario_inventario,
            status=Inventario.Status.EM_ANDAMENTO,
        )

        salvar_contagem(
            self.inventario,
            self.posicao,
            self.produto_a,
            Decimal('15'),
            usuario_contagem=self.user_a,
            origem_contagem=InventarioItem.OrigemContagem.POCKET,
        )
        salvar_contagem(
            self.inventario,
            self.posicao,
            self.produto_b,
            Decimal('20'),
            usuario_contagem=self.user_b,
            origem_contagem=InventarioItem.OrigemContagem.POCKET,
        )

    def test_cenario_3_listagem_exibe_rastreabilidade(self):
        response = self.client.get(
            reverse('inventario:contagem_lista', args=[self.inventario.pk]),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'João Silva')
        self.assertContains(response, 'Maria Souza')
        self.assertContains(response, 'Pocket')
        self.assertContains(response, '110830')
        self.assertContains(response, '124380')

    def test_cenario_4_finalizar_publica_estoque_normalmente(self):
        inventario = Inventario.objects.create(
            usuario=self.usuario_inventario,
            status=Inventario.Status.EM_ANDAMENTO,
        )
        salvar_contagem(
            inventario,
            self.posicao,
            self.produto_a,
            Decimal('10'),
            usuario_contagem=self.user_a,
            origem_contagem=InventarioItem.OrigemContagem.POCKET,
        )

        inventario.status = Inventario.Status.FINALIZADO
        inventario.save(update_fields=['status'])
        resultado = publicar_estoque_fisico(inventario)

        self.assertEqual(resultado.registros_processados, 1)
        estoque = EstoqueFisico.objects.get()
        self.assertEqual(estoque.quantidade, Decimal('10'))
        self.assertEqual(estoque.inventario_origem_id, inventario.pk)
        self.assertFalse(hasattr(estoque, 'usuario_contagem'))

    def test_contagem_web_registra_origem(self):
        produto = Produto.objects.create(
            codigo_produto='WEB001',
            descricao='Produto Web',
            setor='A',
        )
        response = self.client.post(
            reverse('inventario:contagem_criar', args=[self.inventario.pk]),
            {
                'posicao': self.posicao.pk,
                'produto': produto.pk,
                'quantidade_fisica': '7',
            },
        )
        self.assertEqual(response.status_code, 302)

        item = InventarioItem.objects.get(produto=produto)
        self.assertEqual(item.origem_contagem, InventarioItem.OrigemContagem.WEB)
        self.assertIsNotNone(item.usuario_contagem_id)
        self.assertIsNotNone(item.data_contagem)
