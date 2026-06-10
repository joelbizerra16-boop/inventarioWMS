from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from accounts.models import Usuario
from accounts.test_utils import ClienteAutenticadoMixin, criar_usuario_teste
from inventario.models import Inventario, InventarioItem
from inventario.services.aprovacao import StatusAprovacao
from inventario.services.consolidacao import consolidar_estoque_fisico, limpar_estado_consolidacao
from inventario.services.contagem import salvar_contagem
from inventario.services.pocket import (
    PocketContagemError,
    buscar_posicao_por_codigo,
    buscar_produto_por_codigo,
    validar_inventario_para_pocket,
)
from posicoes.models import Posicao
from produtos.models import Produto


class PocketServiceTestCase(TestCase):
    def setUp(self):
        self.usuario = Usuario.objects.create(
            nome='Operador Pocket',
            login='pocket',
            setor='Estoque',
            perfil=Usuario.Perfil.INVENTARIO,
        )
        self.produto = Produto.objects.create(
            codigo_produto='PKT001',
            descricao='Produto Pocket',
            setor='A',
            codigo_ean='7891234567890',
        )
        self.posicao = Posicao.objects.create(codigo='PKT-Z01', posicao='Z-01')
        self.inventario = Inventario.objects.create(
            usuario=self.usuario,
            status=Inventario.Status.EM_ANDAMENTO,
        )

    def test_buscar_posicao_por_codigo(self):
        self.assertEqual(
            buscar_posicao_por_codigo('PKT-Z01').pk,
            self.posicao.pk,
        )
        self.assertIsNone(buscar_posicao_por_codigo('INEXISTENTE'))

    def test_buscar_produto_por_codigo_produto(self):
        self.assertEqual(
            buscar_produto_por_codigo('PKT001').pk,
            self.produto.pk,
        )

    def test_buscar_produto_por_ean(self):
        self.assertEqual(
            buscar_produto_por_codigo('7891234567890').pk,
            self.produto.pk,
        )

    def test_inventario_finalizado_bloqueado(self):
        self.inventario.status = Inventario.Status.FINALIZADO
        self.inventario.save(update_fields=['status'])
        with self.assertRaises(PocketContagemError):
            validar_inventario_para_pocket(self.inventario)

    def test_inventario_aprovado_bloqueado(self):
        self.inventario.status = Inventario.Status.FINALIZADO
        self.inventario.status_aprovacao = StatusAprovacao.APROVADO
        self.inventario.save(update_fields=['status', 'status_aprovacao'])
        with self.assertRaises(PocketContagemError):
            validar_inventario_para_pocket(self.inventario)

    def test_inventario_consolidado_bloqueado(self):
        limpar_estado_consolidacao()
        InventarioItem.objects.create(
            inventario=self.inventario,
            posicao=self.posicao,
            produto=self.produto,
            quantidade_fisica=Decimal('1'),
        )
        self.inventario.status = Inventario.Status.FINALIZADO
        self.inventario.save(update_fields=['status'])
        consolidar_estoque_fisico(self.inventario)
        with self.assertRaises(PocketContagemError):
            validar_inventario_para_pocket(self.inventario)

    def test_multiplos_produtos_mesma_posicao(self):
        produto_b = Produto.objects.create(
            codigo_produto='PKT002',
            descricao='Produto B',
            setor='A',
        )
        salvar_contagem(
            self.inventario,
            self.posicao,
            self.produto,
            Decimal('10'),
        )
        salvar_contagem(
            self.inventario,
            self.posicao,
            produto_b,
            Decimal('5'),
        )
        self.assertEqual(
            InventarioItem.objects.filter(
                inventario=self.inventario,
                posicao=self.posicao,
            ).count(),
            2,
        )

    def test_mesmo_produto_multiplas_posicoes(self):
        posicao_b = Posicao.objects.create(codigo='PKT-Z02', posicao='Z-02')
        salvar_contagem(
            self.inventario,
            self.posicao,
            self.produto,
            Decimal('10'),
        )
        salvar_contagem(
            self.inventario,
            posicao_b,
            self.produto,
            Decimal('7'),
        )
        self.assertEqual(
            InventarioItem.objects.filter(
                inventario=self.inventario,
                produto=self.produto,
            ).count(),
            2,
        )


class PocketViewTestCase(ClienteAutenticadoMixin, TestCase):
    def setUp(self):
        self.user = self.autenticar_cliente()
        limpar_estado_consolidacao()

        self.usuario = Usuario.objects.create(
            nome='Operador Pocket View',
            login='pocketview',
            setor='Estoque',
            perfil=Usuario.Perfil.INVENTARIO,
        )
        self.produto = Produto.objects.create(
            codigo_produto='PKV001',
            descricao='Produto View',
            setor='A',
            codigo_ean='7890001112223',
        )
        self.posicao = Posicao.objects.create(codigo='PKV-Z01', posicao='Z-01')
        self.inventario = Inventario.objects.create(
            usuario=self.usuario,
            status=Inventario.Status.EM_ANDAMENTO,
        )

    def test_selecionar_inventario(self):
        response = self.client.get(reverse('pocket:selecionar'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'Inventário #{self.inventario.pk}')

    def test_contagem_por_codigo_produto(self):
        response = self.client.post(
            reverse('pocket:contagem', args=[self.inventario.pk]),
            {
                'codigo_posicao': 'PKV-Z01',
                'codigo_produto': 'PKV001',
                'quantidade_fisica': '12',
            },
        )
        self.assertRedirects(
            response,
            reverse('pocket:contagem', args=[self.inventario.pk]),
        )
        item = InventarioItem.objects.get(inventario=self.inventario)
        self.assertEqual(item.quantidade_fisica, Decimal('12'))
        self.assertEqual(item.origem_contagem, InventarioItem.OrigemContagem.POCKET)
        self.assertEqual(item.usuario_contagem_id, self.user.pk)
        self.assertIsNotNone(item.data_contagem)

    def test_contagem_por_ean(self):
        response = self.client.post(
            reverse('pocket:contagem', args=[self.inventario.pk]),
            {
                'codigo_posicao': 'PKV-Z01',
                'codigo_produto': '7890001112223',
                'quantidade_fisica': '3',
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            InventarioItem.objects.filter(
                inventario=self.inventario,
                produto=self.produto,
            ).exists(),
        )

    def test_posicao_inexistente(self):
        response = self.client.post(
            reverse('pocket:contagem', args=[self.inventario.pk]),
            {
                'codigo_posicao': 'NAO-EXISTE',
                'codigo_produto': 'PKV001',
                'quantidade_fisica': '1',
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Posição não encontrada.')
        self.assertEqual(InventarioItem.objects.count(), 0)

    def test_produto_inexistente(self):
        response = self.client.post(
            reverse('pocket:contagem', args=[self.inventario.pk]),
            {
                'codigo_posicao': 'PKV-Z01',
                'codigo_produto': 'NAO-EXISTE',
                'quantidade_fisica': '1',
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Produto não encontrado.')
        self.assertEqual(InventarioItem.objects.count(), 0)

    def test_inventario_finalizado_bloqueia_tela(self):
        self.inventario.status = Inventario.Status.FINALIZADO
        self.inventario.save(update_fields=['status'])
        response = self.client.get(
            reverse('pocket:contagem', args=[self.inventario.pk]),
        )
        self.assertRedirects(response, reverse('pocket:selecionar'))

    def test_inventario_aprovado_bloqueia_tela(self):
        self.inventario.status = Inventario.Status.FINALIZADO
        self.inventario.status_aprovacao = StatusAprovacao.APROVADO
        self.inventario.save(update_fields=['status', 'status_aprovacao'])
        response = self.client.get(
            reverse('pocket:contagem', args=[self.inventario.pk]),
        )
        self.assertRedirects(response, reverse('pocket:selecionar'))

    def test_inventario_consolidado_bloqueia_tela(self):
        InventarioItem.objects.create(
            inventario=self.inventario,
            posicao=self.posicao,
            produto=self.produto,
            quantidade_fisica=Decimal('1'),
        )
        self.inventario.status = Inventario.Status.FINALIZADO
        self.inventario.save(update_fields=['status'])
        consolidar_estoque_fisico(self.inventario)

        response = self.client.get(
            reverse('pocket:contagem', args=[self.inventario.pk]),
        )
        self.assertRedirects(response, reverse('pocket:selecionar'))

    def test_atualiza_contagem_existente(self):
        InventarioItem.objects.create(
            inventario=self.inventario,
            posicao=self.posicao,
            produto=self.produto,
            quantidade_fisica=Decimal('5'),
        )
        self.client.post(
            reverse('pocket:contagem', args=[self.inventario.pk]),
            {
                'codigo_posicao': 'PKV-Z01',
                'codigo_produto': 'PKV001',
                'quantidade_fisica': '9',
            },
        )
        item = InventarioItem.objects.get(inventario=self.inventario)
        self.assertEqual(item.quantidade_fisica, Decimal('9'))
        self.assertEqual(InventarioItem.objects.count(), 1)

    def test_tela_exibe_campos_operacionais(self):
        response = self.client.get(
            reverse('pocket:contagem', args=[self.inventario.pk]),
        )
        self.assertContains(response, 'posicao-alocacao')
        self.assertContains(response, 'produto-descricao')
        self.assertContains(response, 'Descrição Produto')

    def test_mantem_posicao_apos_salvar(self):
        self.client.post(
            reverse('pocket:contagem', args=[self.inventario.pk]),
            {
                'codigo_posicao': 'PKV-Z01',
                'codigo_produto': 'PKV001',
                'quantidade_fisica': '4',
            },
        )
        response = self.client.get(
            reverse('pocket:contagem', args=[self.inventario.pk]),
        )
        self.assertContains(response, 'value="PKV-Z01"')

    def test_historico_sessao(self):
        self.client.post(
            reverse('pocket:contagem', args=[self.inventario.pk]),
            {
                'codigo_posicao': 'PKV-Z01',
                'codigo_produto': 'PKV001',
                'quantidade_fisica': '4',
            },
        )
        response = self.client.get(
            reverse('pocket:contagem', args=[self.inventario.pk]),
        )
        self.assertContains(response, 'PKV-Z01')
        self.assertContains(response, 'PKV001')
        self.assertContains(response, '4')

    def test_perfil_consulta_nao_grava(self):
        self.client.logout()
        user, _ = criar_usuario_teste(
            username='consulta.pocket',
            perfil=Usuario.Perfil.CONSULTA,
        )
        self.client.force_login(user)
        response = self.client.post(
            reverse('pocket:contagem', args=[self.inventario.pk]),
            {
                'codigo_posicao': 'PKV-Z01',
                'codigo_produto': 'PKV001',
                'quantidade_fisica': '1',
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(InventarioItem.objects.count(), 0)
