from django.test import TestCase

from accounts.models import Usuario
from core.choices import StatusHomologacao
from posicoes.services.homologacao import criar_precadastro_posicao
from produtos.models import AuditoriaHomologacao, Produto
from produtos.services.homologacao import (
    aprovar_produto,
    criar_precadastro_produto,
    listar_produtos_pendentes,
    rejeitar_produto,
)


class HomologacaoProdutoTestCase(TestCase):
    def setUp(self):
        self.usuario = Usuario.objects.create(
            nome='Operador Teste',
            login='op_precad',
            setor='Estoque',
            perfil=Usuario.Perfil.OPERADOR,
        )

    def test_criar_precadastro_produto_disponivel_para_operacao(self):
        produto = criar_precadastro_produto(
            codigo_produto='SKU-900',
            descricao='Produto operacional',
            usuario=self.usuario,
            origem='POCKET_GERAL',
            equipamento='coletor-01',
        )
        self.assertEqual(produto.status_homologacao, StatusHomologacao.PENDENTE)
        self.assertTrue(produto.ativo)
        self.assertTrue(Produto.objects.filter(codigo_produto='SKU-900', ativo=True).exists())
        self.assertEqual(AuditoriaHomologacao.objects.filter(produto=produto).count(), 1)

    def test_aprovar_produto(self):
        produto = criar_precadastro_produto(
            codigo_produto='SKU-901',
            descricao='Aguardando',
            usuario=self.usuario,
            origem='POCKET_GERAL',
        )
        admin = Usuario.objects.create(
            nome='Admin',
            login='admin_precad',
            setor='TI',
            perfil=Usuario.Perfil.ADMINISTRADOR,
        )
        aprovar_produto(produto, admin, setor='GERAL')
        produto.refresh_from_db()
        self.assertEqual(produto.status_homologacao, StatusHomologacao.HOMOLOGADO)
        self.assertEqual(produto.setor, 'GERAL')

    def test_rejeitar_produto_inativa_registro(self):
        produto = criar_precadastro_produto(
            codigo_produto='SKU-902',
            descricao='Rejeitar',
            usuario=self.usuario,
            origem='POCKET_GERAL',
        )
        admin = Usuario.objects.create(
            nome='Admin 2',
            login='admin_precad2',
            setor='TI',
            perfil=Usuario.Perfil.ADMINISTRADOR,
        )
        rejeitar_produto(produto, admin, observacao='Duplicado')
        produto.refresh_from_db()
        self.assertEqual(produto.status_homologacao, StatusHomologacao.REJEITADO)
        self.assertFalse(produto.ativo)
        self.assertEqual(produto.pk, Produto.objects.get(codigo_produto='SKU-902').pk)

    def test_listar_produtos_pendentes(self):
        criar_precadastro_produto(
            codigo_produto='SKU-903',
            descricao='Pendente',
            usuario=self.usuario,
            origem='POCKET_GERAL',
        )
        self.assertEqual(listar_produtos_pendentes().count(), 1)


class HomologacaoPosicaoTestCase(TestCase):
    def setUp(self):
        self.usuario = Usuario.objects.create(
            nome='Operador Pos',
            login='op_pos',
            setor='Estoque',
            perfil=Usuario.Perfil.OPERADOR,
        )

    def test_criar_precadastro_posicao_estruturada(self):
        posicao = criar_precadastro_posicao(
            usuario=self.usuario,
            origem='POCKET_CICLICO',
            rua='A',
            predio='01',
            nivel='02',
            apto='03',
        )
        self.assertEqual(posicao.codigo, 'A-01-02-03')
        self.assertEqual(posicao.status_homologacao, StatusHomologacao.PENDENTE)
        self.assertTrue(posicao.ativo)
