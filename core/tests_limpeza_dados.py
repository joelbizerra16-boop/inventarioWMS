from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Usuario
from accounts.test_utils import ClienteAutenticadoMixin, criar_usuario_teste
from core.services.limpeza_dados import (
    FRASE_CONFIRMACAO,
    LimpezaDadosError,
    excluir_grupos_selecionados,
)
from estoque_fisico.models import EstoqueFisico
from estoque_sap.models import EstoqueSAP
from inventario.models import (
    CicloInventario,
    CicloInventarioSku,
    Inventario,
    InventarioItem,
)
from movimentacoes.models import Movimentacao
from posicoes.models import Posicao
from posicoes.services.exclusao import CODIGO_POSICAO_SEM_POSICAO
from produtos.models import Produto


class LimpezaDadosBaseMixin:
    def preparar_dados_limpeza(self):
        self.user, self.operacional = criar_usuario_teste()
        self.produto = Produto.objects.create(
            codigo_produto='LIMP001',
            descricao='Produto limpeza',
            setor='A',
            embalagem='UN',
        )
        self.posicao = Posicao.objects.create(codigo='LIMP-P01', posicao='P-01')
        Posicao.objects.get_or_create(
            codigo=CODIGO_POSICAO_SEM_POSICAO,
            defaults={'posicao': 'Sem posição definida'},
        )
        self.inventario = Inventario.objects.create(
            usuario=self.operacional,
            status=Inventario.Status.ABERTO,
        )
        InventarioItem.objects.create(
            inventario=self.inventario,
            posicao=self.posicao,
            produto=self.produto,
            quantidade_fisica=Decimal('3'),
        )
        self.ciclo = CicloInventario.objects.create()
        CicloInventarioSku.objects.create(
            ciclo=self.ciclo,
            produto=self.produto,
            codigo_produto=self.produto.codigo_produto,
            descricao=self.produto.descricao,
        )
        self.estoque_fisico = EstoqueFisico.objects.create(
            produto=self.produto,
            posicao=self.posicao,
            quantidade=Decimal('3'),
            data_contagem=timezone.now(),
            inventario_origem=self.inventario,
        )
        EstoqueSAP.objects.create(
            produto=self.produto,
            total=Decimal('10'),
            arquivo_origem='teste.xlsx',
        )
        Movimentacao.objects.create(
            usuario=self.operacional,
            produto=self.produto,
            posicao=self.posicao,
            quantidade=Decimal('1'),
            tipo_movimento=Movimentacao.TipoMovimento.INVENTARIO,
        )


class LimpezaDadosServiceTestCase(LimpezaDadosBaseMixin, TestCase):
    def setUp(self):
        self.preparar_dados_limpeza()

    def test_excluir_somente_ciclico_preserva_produtos_e_usuarios(self):
        resultado = excluir_grupos_selecionados(
            ['inventario_ciclico'],
            usuario=self.user,
            confirmacao=FRASE_CONFIRMACAO,
        )

        self.assertGreater(resultado.total_excluidos, 0)
        self.assertFalse(CicloInventario.objects.exists())
        self.assertTrue(Inventario.objects.filter(pk=self.inventario.pk).exists())
        self.assertTrue(Produto.objects.filter(pk=self.produto.pk).exists())
        self.assertTrue(Usuario.objects.filter(pk=self.operacional.pk).exists())
        self.assertTrue(User.objects.filter(pk=self.user.pk).exists())

    def test_excluir_inventario_geral_desvincula_estoque_fisico(self):
        excluir_grupos_selecionados(
            ['inventario_geral'],
            usuario=self.user,
            confirmacao=FRASE_CONFIRMACAO,
        )

        self.assertFalse(Inventario.objects.exists())
        self.assertFalse(InventarioItem.objects.exists())
        self.estoque_fisico.refresh_from_db()
        self.assertIsNone(self.estoque_fisico.inventario_origem_id)
        self.assertTrue(Produto.objects.filter(pk=self.produto.pk).exists())

    def test_excluir_posicoes_remove_vinculos_e_mantem_sem_posicao(self):
        excluir_grupos_selecionados(
            ['posicoes'],
            usuario=self.user,
            confirmacao=FRASE_CONFIRMACAO,
        )

        self.assertFalse(Inventario.objects.exists())
        self.assertFalse(CicloInventario.objects.exists())
        self.assertFalse(EstoqueFisico.objects.exists())
        self.assertFalse(Movimentacao.objects.exists())
        self.assertFalse(Posicao.objects.filter(codigo='LIMP-P01').exists())
        self.assertTrue(Posicao.objects.filter(codigo=CODIGO_POSICAO_SEM_POSICAO).exists())
        self.assertTrue(Produto.objects.filter(pk=self.produto.pk).exists())
        self.assertTrue(Usuario.objects.filter(pk=self.operacional.pk).exists())

    def test_rejeita_grupo_protegido_e_sem_confirmacao(self):
        with self.assertRaises(LimpezaDadosError):
            excluir_grupos_selecionados(
                ['produtos'],
                usuario=self.user,
                confirmacao=FRASE_CONFIRMACAO,
            )
        with self.assertRaises(LimpezaDadosError):
            excluir_grupos_selecionados(
                ['inventario_ciclico'],
                usuario=self.user,
                confirmacao='nao',
            )
        self.assertTrue(CicloInventario.objects.exists())
        self.assertTrue(Produto.objects.filter(pk=self.produto.pk).exists())


class LimpezaDadosViewTestCase(LimpezaDadosBaseMixin, ClienteAutenticadoMixin, TestCase):
    def setUp(self):
        self.preparar_dados_limpeza()
        self.client.force_login(self.user)

    def test_admin_ve_lista_de_grupos(self):
        response = self.client.get(reverse('accounts:limpeza_dados'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Inventário cíclico')
        self.assertContains(response, 'Inventário geral')
        self.assertContains(response, 'Cadastro de produtos')
        self.assertContains(response, 'Usuários e senhas')
        self.assertContains(response, 'Protegido')

    def test_nao_admin_nao_acessa(self):
        operador_user, _ = criar_usuario_teste(
            username='operador.limpeza',
            perfil=Usuario.Perfil.INVENTARIO,
        )
        self.client.force_login(operador_user)
        response = self.client.get(reverse('accounts:limpeza_dados'))
        self.assertEqual(response.status_code, 302)

    def test_post_exclui_apenas_grupos_escolhidos(self):
        response = self.client.post(reverse('accounts:limpeza_dados'), {
            'grupos': ['inventario_ciclico', 'estoque_sap'],
            'confirmacao': FRASE_CONFIRMACAO,
        })
        self.assertRedirects(response, reverse('accounts:limpeza_dados'))
        self.assertFalse(CicloInventario.objects.exists())
        self.assertFalse(EstoqueSAP.objects.exists())
        self.assertTrue(Inventario.objects.filter(pk=self.inventario.pk).exists())
        self.assertTrue(Produto.objects.filter(pk=self.produto.pk).exists())
        self.assertTrue(Usuario.objects.filter(pk=self.operacional.pk).exists())

    def test_sidebar_exibe_link_para_admin(self):
        response = self.client.get(reverse('home'))
        self.assertContains(response, reverse('accounts:limpeza_dados'))
        self.assertContains(response, 'Excluir dados')
