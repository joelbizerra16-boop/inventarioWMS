from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Usuario
from accounts.test_utils import ClienteAutenticadoMixin
from estoque_fisico.models import EstoqueFisico
from estoque_sap.models import EstoqueSAP
from inventario.models import (
    CicloAuditoriaHistorico,
    CicloInventario,
    CicloInventarioItem,
    CicloInventarioSku,
)
from inventario.services.ciclico import (
    StatusItemCiclico,
    criar_ciclo,
    limpar_estado_ciclico,
    obter_skus_ciclo,
)
from inventario.services.pocket_ciclico_fila import obter_painel_pocket_ciclico
from inventario.tests_ciclico_auditoria import CiclicoAuditoriaBaseMixin
from posicoes.models import Posicao
from produtos.models import Produto


class PocketCiclicoTestCase(CiclicoAuditoriaBaseMixin, ClienteAutenticadoMixin, TestCase):
    def setUp(self):
        self.user = self.autenticar_cliente(perfil=Usuario.Perfil.INVENTARIO)
        limpar_estado_ciclico()
        self.posicao_a = Posicao.objects.create(codigo='PKT01', posicao='P-01')
        self.posicao_b = Posicao.objects.create(codigo='PKT02', posicao='P-02')
        self.produto = Produto.objects.create(
            codigo_produto='PKT100',
            descricao='Produto Pocket',
            participa_ciclico=True,
        )
        EstoqueSAP.objects.create(
            produto=self.produto,
            total=Decimal('70'),
            arquivo_origem='teste.xlsx',
        )
        EstoqueFisico.objects.create(
            produto=self.produto,
            posicao=self.posicao_a,
            quantidade=Decimal('10'),
            data_contagem=timezone.now(),
        )
        EstoqueFisico.objects.create(
            produto=self.produto,
            posicao=self.posicao_b,
            quantidade=Decimal('20'),
            data_contagem=timezone.now(),
        )
        criar_ciclo(usuario_criacao=self.user)
        self.sku = CicloInventarioSku.objects.get()
        response = self.client.post(reverse('ciclico_executar'), {
            'acao': 'gerar_lote',
            'quantidade_skus': '1',
        })
        self.assertRedirects(response, reverse('ciclico_executar'))

    def tearDown(self):
        limpar_estado_ciclico()

    def _contar_pocket(self, posicao, quantidade, sku_id=None):
        sku_id = sku_id or self.sku.pk
        return self.client.post(reverse('pocket:contagem_ciclico'), {
            'acao': 'contagem',
            'sku_id': str(sku_id),
            'codigo_posicao': posicao,
            'quantidade_fisica': str(quantidade),
        }, follow=True)

    def test_pocket_exibe_fila_sku_do_lote(self):
        response = self.client.get(reverse('pocket:contagem_ciclico'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'SKU do Lote')
        self.assertContains(response, 'PKT100')
        self.assertContains(response, 'Bipado')
        self.assertContains(response, 'pocket-indicadores')
        self.assertNotContains(response, 'Código Produto / EAN')
        self.assertNotContains(response, 'Dispositivo')

    def test_pocket_grava_origem_pocket(self):
        response = self._contar_pocket('PKT01', 10)
        self.assertEqual(response.status_code, 200)

        item = self.sku.posicoes.get(codigo_posicao='PKT01')
        self.assertEqual(item.origem_contagem, CicloInventarioItem.OrigemContagem.POCKET)
        self.assertEqual(item.dispositivo_contagem, '')

    def test_pocket_consolida_saldo_por_sku(self):
        self._contar_pocket('PKT01', 10)
        self._contar_pocket('PKT02', 60)
        self.sku.refresh_from_db()
        self.assertEqual(self.sku.quantidade_fisica, Decimal('70'))

    def test_pocket_acumula_bips_mesma_posicao(self):
        self._contar_pocket('PKT01', 81)
        self._contar_pocket('PKT01', 3)
        item = self.sku.posicoes.get(codigo_posicao='PKT01')
        self.assertEqual(item.quantidade_fisica, Decimal('84'))
        self.sku.refresh_from_db()
        self.assertEqual(self.sku.quantidade_fisica, Decimal('84'))

    def test_pocket_valida_automaticamente_quando_bate_sap(self):
        self._contar_pocket('PKT01', 10)
        self._contar_pocket('PKT02', 60)
        self.sku.refresh_from_db()
        self.assertEqual(self.sku.status_contagem, StatusItemCiclico.VALIDADO)

        ciclo = CicloInventario.objects.get()
        self.assertEqual(ciclo.status_ciclo, CicloInventario.StatusCiclo.ENCERRADO)

    def test_pocket_divergente_nao_aparece_na_combobox(self):
        self._contar_pocket('PKT01', 50)
        self._contar_pocket('PKT02', 30)
        self.sku.refresh_from_db()
        self.assertEqual(self.sku.status_contagem, StatusItemCiclico.DIVERGENTE)

        painel = obter_painel_pocket_ciclico(self.client.session)
        self.assertEqual(painel.fila, [])
        self.assertEqual(len(painel.divergencias), 1)

        response = self.client.get(reverse('pocket:contagem_ciclico'))
        self.assertNotContains(response, 'id="pocket-sku-lote"')
        self.assertContains(response, 'Divergências pendentes')

    def test_pocket_recontagem_mantem_sku_na_fila(self):
        self._contar_pocket('PKT01', 50)
        self._contar_pocket('PKT02', 30)
        self.client.post(reverse('pocket:contagem_ciclico'), {
            'acao': 'recontar',
            'sku_id': str(self.sku.pk),
        }, follow=True)

        painel = obter_painel_pocket_ciclico(self.client.session)
        self.assertEqual(len(painel.fila), 1)
        self.assertEqual(painel.fila[0].pk, self.sku.pk)
        self.assertEqual(painel.fila[0].status_contagem, StatusItemCiclico.RECONTAGEM)

    def test_pocket_ajax_remove_sku_validado_da_fila(self):
        self._contar_pocket('PKT01', 10)
        response = self.client.post(reverse('pocket:contagem_ciclico'), {
            'acao': 'contagem',
            'sku_id': str(self.sku.pk),
            'codigo_posicao': 'PKT02',
            'quantidade_fisica': '60',
        }, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        dados = response.json()
        self.assertTrue(dados['ok'])
        self.assertTrue(dados['sku_removido_fila'])
        self.assertEqual(dados['resumo']['pendentes'], 0)
        self.assertTrue(dados['resumo']['lote_concluido'])

    def test_pocket_registra_auditoria_ao_sair_da_fila(self):
        self._contar_pocket('PKT01', 10)
        self._contar_pocket('PKT02', 60)
        registro = CicloAuditoriaHistorico.objects.filter(
            ciclo_sku=self.sku,
            motivo__icontains='removido da fila operacional',
        )
        self.assertTrue(registro.exists())
        self.assertIn('Validado', registro.latest('data_hora').motivo)

    def test_pocket_recontagem_acumula_do_zero(self):
        self._contar_pocket('PKT01', 50)
        self._contar_pocket('PKT02', 30)
        self.sku.refresh_from_db()
        self.assertEqual(self.sku.status_contagem, StatusItemCiclico.DIVERGENTE)

        self.client.post(reverse('pocket:contagem_ciclico'), {
            'acao': 'recontar',
            'sku_id': str(self.sku.pk),
        }, follow=True)
        self.client.get(reverse('pocket:contagem_ciclico'))

        self._contar_pocket('PKT01', 30)
        self._contar_pocket('PKT01', 10)
        self._contar_pocket('PKT02', 30)
        self.sku.refresh_from_db()
        self.assertEqual(self.sku.quantidade_fisica, Decimal('70'))
        self.assertEqual(self.sku.status_contagem, StatusItemCiclico.VALIDADO)

    def test_pocket_aceitar_divergencia_somente_supervisor(self):
        self._contar_pocket('PKT01', 50)
        self._contar_pocket('PKT02', 30)
        self.sku.refresh_from_db()
        self.assertEqual(self.sku.status_contagem, StatusItemCiclico.DIVERGENTE)

        response = self.client.post(reverse('pocket:contagem_ciclico'), {
            'acao': 'aceitar_divergencia',
            'sku_id': str(self.sku.pk),
        }, follow=True)
        self.sku.refresh_from_db()
        self.assertEqual(self.sku.status_contagem, StatusItemCiclico.DIVERGENTE)
        self.assertContains(response, 'Somente supervisor')

    def test_execucao_exibe_origem_apos_pocket(self):
        self._contar_pocket('PKT01', 10)
        response = self.client.get(reverse('ciclico_executar'))
        self.assertContains(response, 'Pocket')
        self.assertContains(response, 'bi-eye')
        self.assertNotContains(response, 'Ver detalhes')

    def test_grid_operacional_nao_exibe_formulario_contagem(self):
        response = self.client.get(reverse('ciclico_executar'))
        self.assertNotContains(response, 'Salvar contagem')
        self.assertNotContains(response, 'id="formEditarContagemCiclico"')

    def test_skus_ciclo_incluem_origem_no_dto(self):
        self._contar_pocket('PKT01', 5)
        skus = obter_skus_ciclo(session=self.client.session, incluir_posicoes=False)
        self.assertEqual(len(skus), 1)
        self.assertEqual(skus[0].ultima_origem_label, 'Pocket')

    def test_pocket_recontagem_valida_quando_sap_bate_com_posicoes_pendentes(self):
        """Recontagem com saldo igual ao SAP deve validar sem exigir todas as posições."""
        self._contar_pocket('PKT01', 50)
        self._contar_pocket('PKT02', 30)
        self.sku.refresh_from_db()
        self.assertEqual(self.sku.status_contagem, StatusItemCiclico.DIVERGENTE)

        posicao_extra = Posicao.objects.create(codigo='PKT03', posicao='P-03')
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

        self.client.post(reverse('pocket:contagem_ciclico'), {
            'acao': 'recontar',
            'sku_id': str(self.sku.pk),
        }, follow=True)

        self._contar_pocket('PKT01', 70)
        self.sku.refresh_from_db()
        self.assertEqual(self.sku.quantidade_fisica, Decimal('70'))
        self.assertEqual(self.sku.diferenca, Decimal('0'))
        self.assertEqual(self.sku.status_contagem, StatusItemCiclico.VALIDADO)

        item_contado = self.sku.posicoes.get(codigo_posicao='PKT01')
        self.assertEqual(item_contado.status_contagem, StatusItemCiclico.VALIDADO)

    def test_pocket_valida_sku_quando_sap_atingido_sem_todas_posicoes(self):
        """SKU com diferença zero deve validar mesmo com posições não contadas (Pocket)."""
        posicao_extra = Posicao.objects.create(codigo='PKT03', posicao='P-03')
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
        self._contar_pocket('PKT01', 70)
        self.sku.refresh_from_db()
        self.assertEqual(self.sku.quantidade_fisica, Decimal('70'))
        self.assertEqual(self.sku.status_contagem, StatusItemCiclico.VALIDADO)

        painel = obter_painel_pocket_ciclico(self.client.session)
        self.assertEqual(painel.resumo.pendentes, 0)
        self.assertEqual(painel.fila, [])

    def test_pocket_encerra_ciclo_quando_todos_skus_finalizados(self):
        self._contar_pocket('PKT01', 10)
        self._contar_pocket('PKT02', 60)
        ciclo = CicloInventario.objects.get()
        ciclo.refresh_from_db()
        self.assertEqual(ciclo.status_ciclo, CicloInventario.StatusCiclo.ENCERRADO)
        self.assertIsNotNone(ciclo.data_encerramento)
        self.assertEqual(ciclo.usuario_encerramento_id, self.user.pk)

    def test_pocket_ajax_retorna_skus_lote_e_proximo_sku(self):
        self._contar_pocket('PKT01', 10)
        response = self.client.post(reverse('pocket:contagem_ciclico'), {
            'acao': 'contagem',
            'sku_id': str(self.sku.pk),
            'codigo_posicao': 'PKT02',
            'quantidade_fisica': '60',
        }, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        dados = response.json()
        self.assertTrue(dados['sku_removido_fila'])
        self.assertEqual(dados['skus_lote'], [])
        self.assertIsNone(dados['proximo_sku_id'])


class PocketCiclicoSupervisorViewTestCase(CiclicoAuditoriaBaseMixin, ClienteAutenticadoMixin, TestCase):
    def setUp(self):
        limpar_estado_ciclico()
        self.user = self.autenticar_cliente(perfil=Usuario.Perfil.ADMINISTRADOR)
        Posicao.objects.create(codigo='PKV01', posicao='V-01')
        Posicao.objects.create(codigo='PKV02', posicao='V-02')
        self.produto = Produto.objects.create(
            codigo_produto='PKV100',
            descricao='Produto View',
            participa_ciclico=True,
        )
        EstoqueSAP.objects.create(
            produto=self.produto,
            total=Decimal('70'),
            arquivo_origem='teste.xlsx',
        )
        EstoqueFisico.objects.create(
            produto=self.produto,
            posicao=Posicao.objects.get(codigo='PKV01'),
            quantidade=Decimal('10'),
            data_contagem=timezone.now(),
        )
        EstoqueFisico.objects.create(
            produto=self.produto,
            posicao=Posicao.objects.get(codigo='PKV02'),
            quantidade=Decimal('20'),
            data_contagem=timezone.now(),
        )
        criar_ciclo(usuario_criacao=self.user)
        self.sku = CicloInventarioSku.objects.get(codigo_produto='PKV100')
        self.client.post(reverse('ciclico_executar'), {
            'acao': 'gerar_lote',
            'quantidade_skus': '1',
        })

    def tearDown(self):
        limpar_estado_ciclico()

    def test_view_aceitar_divergencia_supervisor(self):
        self.client.post(reverse('pocket:contagem_ciclico'), {
            'acao': 'contagem',
            'sku_id': str(self.sku.pk),
            'codigo_posicao': 'PKV01',
            'quantidade_fisica': '50',
        }, follow=True)
        self.client.post(reverse('pocket:contagem_ciclico'), {
            'acao': 'contagem',
            'sku_id': str(self.sku.pk),
            'codigo_posicao': 'PKV02',
            'quantidade_fisica': '30',
        }, follow=True)
        response = self.client.post(reverse('pocket:contagem_ciclico'), {
            'acao': 'aceitar_divergencia',
            'sku_id': str(self.sku.pk),
        })
        self.assertRedirects(response, reverse('ciclico'))
        self.sku.refresh_from_db()
        self.assertEqual(self.sku.status_contagem, StatusItemCiclico.VALIDADO_DIVERGENCIA)

        ciclo = CicloInventario.objects.get()
        self.assertEqual(ciclo.status_ciclo, CicloInventario.StatusCiclo.ENCERRADO)


class PocketModoUnificadoTestCase(TestCase):
    def test_urls_pocket(self):
        self.assertEqual(reverse('pocket:selecionar'), '/pocket/')
        self.assertEqual(reverse('pocket:contagem_ciclico'), '/pocket/ciclico/')
