from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Usuario
from accounts.test_utils import ClienteAutenticadoMixin
from estoque_fisico.models import EstoqueFisico
from estoque_sap.models import EstoqueSAP
from inventario.models import CicloInventarioItem, CicloInventarioSku
from inventario.services.ciclico import criar_ciclo, limpar_estado_ciclico
from inventario.services.consulta_contagem_ciclico import obter_consulta_contagem
from inventario.tests_ciclico_auditoria import CiclicoAuditoriaBaseMixin
from posicoes.models import Posicao
from produtos.models import Produto


class ConsultaContagemCiclicoTestCase(CiclicoAuditoriaBaseMixin, ClienteAutenticadoMixin, TestCase):
    def setUp(self):
        limpar_estado_ciclico()
        self.user = self.autenticar_cliente(perfil=Usuario.Perfil.ADMINISTRADOR)
        self.posicao_a = Posicao.objects.create(codigo='PKT01', posicao='1 1 3')
        self.posicao_b = Posicao.objects.create(codigo='PKT02', posicao='1 2 6')
        self.produto = Produto.objects.create(
            codigo_produto='111881',
            descricao='PRODUTO CONSULTA TESTE',
            participa_ciclico=True,
        )
        EstoqueSAP.objects.create(
            produto=self.produto,
            total=Decimal('54'),
            arquivo_origem='teste.xlsx',
        )
        EstoqueFisico.objects.create(
            produto=self.produto,
            posicao=self.posicao_a,
            quantidade=Decimal('50'),
            data_contagem=timezone.now(),
        )
        EstoqueFisico.objects.create(
            produto=self.produto,
            posicao=self.posicao_b,
            quantidade=Decimal('4'),
            data_contagem=timezone.now(),
        )
        criar_ciclo(usuario_criacao=self.user)
        self.sku = CicloInventarioSku.objects.get(produto=self.produto)
        CicloInventarioItem.objects.filter(ciclo_sku=self.sku, posicao=self.posicao_a).update(
            quantidade_fisica=Decimal('50'),
            usuario_contagem=self.user,
            data_contagem=timezone.now(),
            origem_contagem=CicloInventarioItem.OrigemContagem.POCKET,
        )
        CicloInventarioItem.objects.filter(ciclo_sku=self.sku, posicao=self.posicao_b).update(
            quantidade_fisica=Decimal('4'),
            usuario_contagem=self.user,
            data_contagem=timezone.now(),
            origem_contagem=CicloInventarioItem.OrigemContagem.POCKET,
        )
        self.sku.quantidade_fisica = Decimal('54')
        self.sku.diferenca = Decimal('0')
        self.sku.status_contagem = 'VALIDADO'
        self.sku.save(update_fields=['quantidade_fisica', 'diferenca', 'status_contagem'])

    def tearDown(self):
        limpar_estado_ciclico()

    def test_servico_retorna_posicoes_e_operadores(self):
        resultado = obter_consulta_contagem(self.sku.ciclo_id, sku_id=self.sku.pk)
        self.assertIsNotNone(resultado.sku)
        detalhe = resultado.sku
        self.assertEqual(detalhe.resumo.codigo_produto, '111881')
        self.assertEqual(len(detalhe.posicoes), 2)
        self.assertEqual(detalhe.resumo.contado, Decimal('54'))
        self.assertEqual(detalhe.resumo.sap, Decimal('54'))
        self.assertEqual(detalhe.resumo.faltam, Decimal('0'))
        self.assertEqual(len(detalhe.operadores), 1)

    def test_view_exibe_consulta_sem_eventos(self):
        response = self.client.get(
            reverse('ciclico_consulta_contagem'),
            {'q': '111881', 'ciclo': self.sku.ciclo_id},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Consulta de Contagem')
        self.assertContains(response, 'Onde foi contado')
        self.assertContains(response, 'Quem contou')
        self.assertContains(response, '111881')
        self.assertNotContains(response, 'Trilha completa')
        self.assertNotContains(response, 'Auditoria')

    def test_busca_por_descricao_lista_sugestoes(self):
        resultado = obter_consulta_contagem(self.sku.ciclo_id, termo='CONSULTA')
        self.assertIsNotNone(resultado.sku)
        self.assertEqual(resultado.sku.resumo.codigo_produto, '111881')
