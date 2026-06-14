from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Usuario
from accounts.test_utils import ClienteAutenticadoMixin
from estoque_fisico.models import EstoqueFisico
from inventario.models import CicloEstoqueFisicoAjuste, CicloInventarioItem, CicloInventarioSku
from inventario.services.ciclico import StatusItemCiclico, criar_ciclo, limpar_estado_ciclico
from inventario.tests_ciclico_auditoria import CiclicoAuditoriaBaseMixin
from posicoes.models import Posicao
from produtos.models import Produto
from estoque_sap.models import EstoqueSAP


class CiclicoEstoqueFisicoTestCase(CiclicoAuditoriaBaseMixin, ClienteAutenticadoMixin, TestCase):
    def setUp(self):
        self.user = self.autenticar_cliente(perfil=Usuario.Perfil.INVENTARIO)
        limpar_estado_ciclico()
        self.posicao_a = Posicao.objects.create(codigo='EF01', posicao='1.2.6')
        self.posicao_b = Posicao.objects.create(codigo='EF02', posicao='1.1.4')
        self.produto = Produto.objects.create(
            codigo_produto='EF100',
            descricao='Produto Estoque Fisico',
            participa_ciclico=True,
        )
        EstoqueSAP.objects.create(
            produto=self.produto,
            total=Decimal('71'),
            arquivo_origem='teste.xlsx',
        )
        EstoqueFisico.objects.create(
            produto=self.produto,
            posicao=self.posicao_a,
            quantidade=Decimal('40'),
            data_contagem=timezone.now(),
        )
        EstoqueFisico.objects.create(
            produto=self.produto,
            posicao=self.posicao_b,
            quantidade=Decimal('32'),
            data_contagem=timezone.now(),
        )
        criar_ciclo(usuario_criacao=self.user)
        self.sku = CicloInventarioSku.objects.get()
        self.client.post(reverse('ciclico_executar'), {
            'acao': 'gerar_lote',
            'quantidade_skus': '1',
        })

    def tearDown(self):
        limpar_estado_ciclico()

    def _contar(self, codigo_posicao, quantidade):
        return self.client.post(reverse('pocket:contagem_ciclico'), {
            'acao': 'contagem',
            'sku_id': str(self.sku.pk),
            'codigo_posicao': codigo_posicao,
            'quantidade_fisica': str(quantidade),
        }, follow=True)

    def test_nao_atualiza_estoque_fisico_antes_da_validacao(self):
        self._contar('EF01', 41)
        estoque_a = EstoqueFisico.objects.get(produto=self.produto, posicao=self.posicao_a)
        self.assertEqual(estoque_a.quantidade, Decimal('40'))
        self.assertEqual(CicloEstoqueFisicoAjuste.objects.count(), 0)

    def test_atualiza_posicoes_contadas_ao_validar_sku(self):
        self._contar('EF01', 41)
        self._contar('EF02', 30)
        self.sku.refresh_from_db()
        self.assertEqual(self.sku.status_contagem, StatusItemCiclico.VALIDADO)

        self.assertEqual(
            EstoqueFisico.objects.get(produto=self.produto, posicao=self.posicao_a).quantidade,
            Decimal('41'),
        )
        self.assertEqual(
            EstoqueFisico.objects.get(produto=self.produto, posicao=self.posicao_b).quantidade,
            Decimal('30'),
        )

        self.assertEqual(CicloEstoqueFisicoAjuste.objects.filter(ciclo_sku=self.sku).count(), 2)
        ajuste_a = CicloEstoqueFisicoAjuste.objects.get(ciclo_sku=self.sku, codigo_posicao='EF01')
        self.assertEqual(ajuste_a.origem, CicloEstoqueFisicoAjuste.OrigemAjuste.INVENTARIO_CICLICO)
        self.assertEqual(ajuste_a.quantidade_anterior, Decimal('40'))
        self.assertEqual(ajuste_a.quantidade_nova, Decimal('41'))
        self.assertEqual(ajuste_a.diferenca, Decimal('1'))
        self.assertEqual(ajuste_a.usuario_id, self.user.pk)

    def test_nao_atualiza_em_divergente(self):
        self._contar('EF01', 50)
        self._contar('EF02', 30)
        self.sku.refresh_from_db()
        self.assertEqual(self.sku.status_contagem, StatusItemCiclico.DIVERGENTE)
        self.assertEqual(CicloEstoqueFisicoAjuste.objects.count(), 0)
        self.assertEqual(
            EstoqueFisico.objects.get(produto=self.produto, posicao=self.posicao_a).quantidade,
            Decimal('40'),
        )

    def test_atualiza_apos_aceitar_divergencia(self):
        supervisor = self.autenticar_cliente(perfil=Usuario.Perfil.ADMINISTRADOR)
        limpar_estado_ciclico()
        EstoqueSAP.objects.create(
            produto=self.produto,
            total=Decimal('71'),
            arquivo_origem='teste.xlsx',
        )
        criar_ciclo(usuario_criacao=supervisor)
        self.sku = CicloInventarioSku.objects.get()
        self.client.post(reverse('ciclico_executar'), {
            'acao': 'gerar_lote',
            'quantidade_skus': '1',
        })
        self._contar('EF01', 50)
        self._contar('EF02', 30)
        self.client.post(reverse('pocket:contagem_ciclico'), {
            'acao': 'aceitar_divergencia',
            'sku_id': str(self.sku.pk),
        })
        self.sku.refresh_from_db()
        self.assertEqual(self.sku.status_contagem, StatusItemCiclico.VALIDADO_DIVERGENCIA)
        self.assertEqual(
            EstoqueFisico.objects.get(produto=self.produto, posicao=self.posicao_a).quantidade,
            Decimal('50'),
        )
        ajuste = CicloEstoqueFisicoAjuste.objects.filter(
            ciclo_sku=self.sku,
            codigo_posicao='EF01',
        ).get()
        self.assertEqual(ajuste.usuario_id, supervisor.pk)
        self.assertIn('Divergência aceita', ajuste.motivo)

    def test_atualiza_somente_posicoes_contadas(self):
        posicao_extra = Posicao.objects.create(codigo='EF03', posicao='9.9.9')
        CicloInventarioItem.objects.create(
            ciclo=self.sku.ciclo,
            ciclo_sku=self.sku,
            produto=self.produto,
            codigo_produto=self.produto.codigo_produto,
            descricao=self.produto.descricao,
            codigo_posicao=posicao_extra.codigo,
            posicao=posicao_extra,
            alocacao=posicao_extra.posicao,
        )
        EstoqueFisico.objects.create(
            produto=self.produto,
            posicao=posicao_extra,
            quantidade=Decimal('99'),
            data_contagem=timezone.now(),
        )

        self._contar('EF01', 71)
        self.sku.refresh_from_db()
        self.assertEqual(self.sku.status_contagem, StatusItemCiclico.VALIDADO)

        self.assertEqual(
            EstoqueFisico.objects.get(produto=self.produto, posicao=self.posicao_a).quantidade,
            Decimal('71'),
        )
        self.assertEqual(
            EstoqueFisico.objects.get(produto=self.produto, posicao=self.posicao_b).quantidade,
            Decimal('32'),
        )
        self.assertEqual(
            EstoqueFisico.objects.get(produto=self.produto, posicao=posicao_extra).quantidade,
            Decimal('99'),
        )
        self.assertEqual(CicloEstoqueFisicoAjuste.objects.filter(ciclo_sku=self.sku).count(), 1)
