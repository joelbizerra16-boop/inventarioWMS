from decimal import Decimal

from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.test_utils import ClienteAutenticadoMixin, criar_usuario_teste
from estoque_fisico.models import EstoqueFisico
from estoque_sap.models import EstoqueSAP
from inventario.models import CicloInventario, CicloInventarioItem, CicloInventarioSku
from inventario.services.ciclico import criar_ciclo, limpar_estado_ciclico
from posicoes.models import Posicao
from posicoes.services.exclusao import (
    CODIGO_POSICAO_SEM_POSICAO,
    MENSAGEM_SUCESSO_COM_VINCULOS,
    ExclusaoPosicaoError,
    excluir_posicao_com_vinculos,
    obter_posicao_sem_posicao,
)
from produtos.models import Produto


class ExclusaoPosicaoServiceTestCase(TestCase):
    def setUp(self):
        self.user, _ = criar_usuario_teste()
        self.posicao_teste = Posicao.objects.create(codigo='TEST-DEL', posicao='T-01')
        self.produto = Produto.objects.create(
            codigo_produto='DEL100',
            descricao='Produto exclusão',
            participa_ciclico=True,
        )

    def tearDown(self):
        limpar_estado_ciclico()

    def _criar_item_ciclo(self, posicao: Posicao) -> CicloInventarioItem:
        EstoqueSAP.objects.create(
            produto=self.produto,
            total=Decimal('10'),
            arquivo_origem='teste.xlsx',
        )
        criar_ciclo(usuario_criacao=self.user)
        ciclo = CicloInventario.objects.get()
        sku = CicloInventarioSku.objects.get(ciclo=ciclo, produto=self.produto)
        return CicloInventarioItem.objects.create(
            ciclo=ciclo,
            ciclo_sku=sku,
            produto=self.produto,
            codigo_produto=self.produto.codigo_produto,
            descricao=self.produto.descricao,
            posicao=posicao,
            codigo_posicao=posicao.codigo,
            alocacao=posicao.posicao,
            quantidade_fisica=Decimal('5'),
        )

    def test_excluir_posicao_sem_vinculos(self):
        resultado = excluir_posicao_com_vinculos(self.posicao_teste, usuario=self.user)

        self.assertFalse(resultado.houve_vinculos)
        self.assertFalse(Posicao.objects.filter(codigo='TEST-DEL').exists())

    def test_excluir_posicao_transfere_item_ciclo_para_sem_posicao(self):
        item = self._criar_item_ciclo(self.posicao_teste)

        resultado = excluir_posicao_com_vinculos(self.posicao_teste, usuario=self.user)

        self.assertTrue(resultado.houve_vinculos)
        self.assertFalse(Posicao.objects.filter(pk=self.posicao_teste.pk).exists())
        posicao_sem = obter_posicao_sem_posicao()
        item_transferido = CicloInventarioItem.objects.get(
            ciclo_id=item.ciclo_id,
            produto_id=item.produto_id,
            posicao=posicao_sem,
        )
        self.assertEqual(item_transferido.codigo_posicao, CODIGO_POSICAO_SEM_POSICAO)
        self.assertEqual(item_transferido.quantidade_fisica, Decimal('5'))

    def test_nao_permite_excluir_posicao_padrao(self):
        posicao_padrao = obter_posicao_sem_posicao()

        with self.assertRaises(ExclusaoPosicaoError):
            excluir_posicao_com_vinculos(posicao_padrao, usuario=self.user)

    def test_excluir_posicao_transfere_estoque_fisico(self):
        EstoqueFisico.objects.create(
            produto=self.produto,
            posicao=self.posicao_teste,
            quantidade=Decimal('3'),
            data_contagem=timezone.now(),
        )

        resultado = excluir_posicao_com_vinculos(self.posicao_teste, usuario=self.user)

        self.assertTrue(resultado.houve_vinculos)
        posicao_sem = obter_posicao_sem_posicao()
        self.assertTrue(
            EstoqueFisico.objects.filter(
                produto=self.produto,
                posicao=posicao_sem,
                quantidade=Decimal('3'),
            ).exists()
        )


class ExclusaoPosicaoViewTestCase(ClienteAutenticadoMixin, TestCase):
    def setUp(self):
        self.user = self.autenticar_cliente()
        self.posicao_teste = Posicao.objects.create(codigo='VIEW-DEL', posicao='V-01')
        self.produto = Produto.objects.create(
            codigo_produto='VIEW100',
            descricao='Produto view',
            participa_ciclico=True,
        )
        EstoqueSAP.objects.create(
            produto=self.produto,
            total=Decimal('10'),
            arquivo_origem='teste.xlsx',
        )
        criar_ciclo(usuario_criacao=self.user)
        ciclo = CicloInventario.objects.get()
        sku = CicloInventarioSku.objects.get(ciclo=ciclo, produto=self.produto)
        CicloInventarioItem.objects.create(
            ciclo=ciclo,
            ciclo_sku=sku,
            produto=self.produto,
            codigo_produto=self.produto.codigo_produto,
            descricao=self.produto.descricao,
            posicao=self.posicao_teste,
            codigo_posicao=self.posicao_teste.codigo,
            alocacao=self.posicao_teste.posicao,
        )

    def tearDown(self):
        limpar_estado_ciclico()

    def test_view_excluir_posicao_com_vinculos_exibe_mensagem_amigavel(self):
        url = reverse('posicoes:excluir', args=[self.posicao_teste.pk])
        response = self.client.post(url)

        self.assertRedirects(response, reverse('posicoes:lista'))
        self.assertFalse(Posicao.objects.filter(pk=self.posicao_teste.pk).exists())
        mensagens = [str(m) for m in get_messages(response.wsgi_request)]
        self.assertIn(MENSAGEM_SUCESSO_COM_VINCULOS, mensagens)
