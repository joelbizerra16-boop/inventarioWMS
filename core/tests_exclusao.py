from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Usuario
from accounts.test_utils import ClienteAutenticadoMixin, criar_usuario_teste
from core.services.exclusao import (
    ExclusaoBloqueadaError,
    excluir_registro_seguro,
    validar_exclusao,
)
from estoque_fisico.models import EstoqueFisico
from inventario.models import Inventario
from posicoes.models import Posicao
from produtos.models import Produto


class ExclusaoSeguraServiceTestCase(TestCase):
    def setUp(self):
        self.user, _ = criar_usuario_teste()
        self.produto = Produto.objects.create(
            codigo_produto='EXC001',
            descricao='Produto exclusão',
            embalagem='TAMBOR',
            setor='LUBRIFICANTE',
        )
        self.posicao = Posicao.objects.create(codigo='EXC-P01', posicao='P-01')
        self.inventario = Inventario.objects.create(
            usuario=Usuario.objects.get(user=self.user),
            status=Inventario.Status.ABERTO,
        )

    def test_inventario_com_estoque_fisico_bloqueia_exclusao(self):
        EstoqueFisico.objects.create(
            produto=self.produto,
            posicao=self.posicao,
            quantidade=1,
            data_contagem=timezone.now(),
            inventario_origem=self.inventario,
        )

        with self.assertRaises(ExclusaoBloqueadaError) as contexto:
            validar_exclusao(self.inventario)

        self.assertIn('vinculados', contexto.exception.mensagem)
        self.assertTrue(Inventario.objects.filter(pk=self.inventario.pk).exists())

    def test_inventario_sem_vinculos_protegidos_pode_excluir(self):
        excluir_registro_seguro(self.inventario)
        self.assertFalse(Inventario.objects.filter(pk=self.inventario.pk).exists())


class InventarioDeleteViewExclusaoTestCase(ClienteAutenticadoMixin, TestCase):
    def setUp(self):
        self.user = self.autenticar_cliente()
        self.operacional = Usuario.objects.get(user=self.user)
        self.produto = Produto.objects.create(
            codigo_produto='EXC002',
            descricao='Produto view',
            embalagem='TAMBOR',
            setor='LUBRIFICANTE',
        )
        self.posicao = Posicao.objects.create(codigo='EXC-P02', posicao='P-02')
        self.inventario = Inventario.objects.create(
            usuario=self.operacional,
            status=Inventario.Status.ABERTO,
        )
        EstoqueFisico.objects.create(
            produto=self.produto,
            posicao=self.posicao,
            quantidade=2,
            data_contagem=timezone.now(),
            inventario_origem=self.inventario,
        )

    def test_excluir_inventario_vinculado_exibe_mensagem_amigavel(self):
        url = reverse('inventario:excluir', args=[self.inventario.pk])
        response = self.client.post(url)

        self.assertRedirects(response, reverse('inventario:lista'))
        self.assertTrue(Inventario.objects.filter(pk=self.inventario.pk).exists())
