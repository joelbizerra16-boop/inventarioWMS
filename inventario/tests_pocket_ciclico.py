from decimal import Decimal

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Usuario
from accounts.test_utils import ClienteAutenticadoMixin, criar_usuario_teste
from estoque_fisico.models import EstoqueFisico
from estoque_sap.models import EstoqueSAP
from inventario.models import (
    CicloAuditoriaHistorico,
    CicloInventario,
    CicloInventarioItem,
    CicloInventarioSku,
    CicloLoteExecucao,
)
from inventario.services.ciclico import (
    StatusItemCiclico,
    criar_ciclo,
    limpar_estado_ciclico,
    obter_lote_execucao_ativo,
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

    def _contar_pocket(self, posicao, quantidade, sku_id=None, codigo_produto=None):
        sku_id = sku_id or self.sku.pk
        codigo_produto = codigo_produto or self.produto.codigo_produto
        return self.client.post(reverse('pocket:contagem_ciclico'), {
            'acao': 'contagem',
            'sku_id': str(sku_id),
            'codigo_posicao': posicao,
            'codigo_produto_lido': codigo_produto,
            'quantidade_fisica': str(quantidade),
        }, follow=True)

    def _contar_pocket_ajax(self, posicao, quantidade, sku_id=None, codigo_produto=None):
        sku_id = sku_id or self.sku.pk
        codigo_produto = codigo_produto or self.produto.codigo_produto
        return self.client.post(reverse('pocket:contagem_ciclico'), {
            'acao': 'contagem',
            'sku_id': str(sku_id),
            'codigo_posicao': posicao,
            'codigo_produto_lido': codigo_produto,
            'quantidade_fisica': str(quantidade),
        }, HTTP_X_REQUESTED_WITH='XMLHttpRequest')

    def _finalizar_pocket(self, sku_id=None):
        sku_id = sku_id or self.sku.pk
        return self.client.post(reverse('pocket:contagem_ciclico'), {
            'acao': 'finalizar_sku',
            'sku_id': str(sku_id),
        }, HTTP_X_REQUESTED_WITH='XMLHttpRequest')

    def _lock_posicao_pocket_ajax(self, codigo_posicao, sku_id=None):
        sku_id = sku_id or self.sku.pk
        return self.client.post(reverse('pocket:contagem_ciclico'), {
            'acao': 'lock_posicao',
            'sku_id': str(sku_id),
            'codigo_posicao': codigo_posicao,
        }, HTTP_X_REQUESTED_WITH='XMLHttpRequest')

    def test_pocket_exibe_fila_sku_do_lote(self):
        response = self.client.get(reverse('pocket:contagem_ciclico'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'SKU do lote')
        self.assertContains(response, 'PKT100')
        self.assertContains(response, 'Finalizar SKU')
        self.assertContains(response, 'pocket-res-sap')
        self.assertNotContains(response, 'Código Produto / EAN')
        self.assertNotContains(response, 'Dispositivo')

    def test_lock_posicao_ciclico_retorna_alocacao(self):
        response = self._lock_posicao_pocket_ajax('PKT01')
        self.assertEqual(response.status_code, 200)
        dados = response.json()
        self.assertTrue(dados['ok'])
        self.assertEqual(dados['posicao_codigo'], 'PKT01')
        self.assertEqual(dados['posicao_alocacao'], self.posicao_a.posicao)

    def test_operador_visualiza_lote_gerado_por_supervisor(self):
        operador_user, _ = criar_usuario_teste(
            username='operador.pocket.lote',
            perfil=Usuario.Perfil.OPERADOR,
        )
        operador_client = Client()
        operador_client.force_login(operador_user)
        operador_client.post(reverse('pocket:selecionar'), {'modo': 'ciclico'})

        response = operador_client.get(reverse('pocket:selecionar'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Lote ativo: 1 SKU(s)')
        self.assertContains(response, 'Iniciar contagem cíclica')
        self.assertNotContains(
            response,
            'Aguarde a geração do lote pelo supervisor antes de contar.',
        )

    def test_lote_persistido_sobrevive_restart_e_usuarios_diferentes(self):
        self.client.post(reverse('ciclico_executar'), {
            'acao': 'gerar_lote',
            'quantidade_skus': '5',
        })
        lote = obter_lote_execucao_ativo()
        self.assertIsNotNone(lote)
        qtd = lote.itens.count()
        self.assertGreater(qtd, 0)

        operador_user, _ = criar_usuario_teste(
            username='operador.restart.lote',
            perfil=Usuario.Perfil.OPERADOR,
        )
        operador_client = Client()
        operador_client.force_login(operador_user)
        operador_client.post(reverse('pocket:selecionar'), {'modo': 'ciclico'})
        response = operador_client.get(reverse('pocket:selecionar'))
        self.assertContains(response, f'Lote ativo: {qtd} SKU(s)')

        supervisor_novo = Client()
        supervisor_novo.force_login(self.user)
        response = supervisor_novo.get(reverse('ciclico_executar'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(obter_skus_ciclo(session=None)), qtd)

        lote_apos_restart = obter_lote_execucao_ativo()
        self.assertIsNotNone(lote_apos_restart)
        self.assertEqual(lote_apos_restart.pk, lote.pk)
        self.assertEqual(lote_apos_restart.itens.count(), qtd)

    def test_pocket_grava_origem_pocket(self):
        response = self._contar_pocket('PKT01', 10)
        self.assertEqual(response.status_code, 200)

        item = self.sku.posicoes.get(codigo_posicao='PKT01')
        self.assertEqual(item.origem_contagem, CicloInventarioItem.OrigemContagem.POCKET)
        self.assertEqual(item.dispositivo_contagem, '')

    def test_pocket_consolida_saldo_por_sku(self):
        self._contar_pocket('PKT01', 10)
        self.sku.refresh_from_db()
        self.assertEqual(self.sku.quantidade_fisica, Decimal('10'))
        self.assertEqual(self.sku.status_contagem, StatusItemCiclico.PENDENTE)
        self.assertIsNone(self.sku.diferenca)
        self._contar_pocket('PKT02', 60)
        self.sku.refresh_from_db()
        self.assertEqual(self.sku.quantidade_fisica, Decimal('70'))
        self.assertEqual(self.sku.status_contagem, StatusItemCiclico.VALIDADO)
        self.assertEqual(self.sku.diferenca, Decimal('0'))

    def test_pocket_mantem_pendente_em_contagem_parcial(self):
        self._contar_pocket('PKT01', 50)
        self.sku.refresh_from_db()
        self.assertEqual(self.sku.quantidade_fisica, Decimal('50'))
        self.assertEqual(self.sku.status_contagem, StatusItemCiclico.PENDENTE)
        self.assertIsNone(self.sku.diferenca)

        painel = obter_painel_pocket_ciclico(self.client.session)
        self.assertEqual(len(painel.fila), 1)
        self.assertEqual(len(painel.divergencias), 0)

    def test_pocket_rejeita_bip_duplicado_mesma_posicao(self):
        self._contar_pocket('PKT01', 81)
        response = self._contar_pocket_ajax('PKT01', 3)
        self.assertEqual(response.status_code, 400)
        item = self.sku.posicoes.get(codigo_posicao='PKT01')
        self.assertEqual(item.quantidade_fisica, Decimal('81'))
        self.sku.refresh_from_db()
        self.assertEqual(self.sku.quantidade_fisica, Decimal('81'))

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
        self.assertEqual(self.sku.status_contagem, StatusItemCiclico.PENDENTE)

        response = self._finalizar_pocket()
        self.assertTrue(response.json()['ok'])
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
        self._finalizar_pocket()
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
        response = self._contar_pocket_ajax('PKT02', 60)
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
        self._finalizar_pocket()
        self.sku.refresh_from_db()
        self.assertEqual(self.sku.status_contagem, StatusItemCiclico.DIVERGENTE)

        self.client.post(reverse('pocket:contagem_ciclico'), {
            'acao': 'recontar',
            'sku_id': str(self.sku.pk),
        }, follow=True)
        self.client.get(reverse('pocket:contagem_ciclico'))

        self._contar_pocket('PKT01', 40)
        self._contar_pocket('PKT02', 30)
        self.sku.refresh_from_db()
        self.assertEqual(self.sku.quantidade_fisica, Decimal('70'))
        self.assertEqual(self.sku.status_contagem, StatusItemCiclico.VALIDADO)

    def test_pocket_aceitar_divergencia_somente_supervisor(self):
        self._contar_pocket('PKT01', 50)
        self._contar_pocket('PKT02', 30)
        self._finalizar_pocket()
        self.sku.refresh_from_db()
        self.assertEqual(self.sku.status_contagem, StatusItemCiclico.DIVERGENTE)

        response = self.client.post(reverse('pocket:contagem_ciclico'), {
            'acao': 'aceitar_divergencia',
            'sku_id': str(self.sku.pk),
        }, follow=True)
        self.sku.refresh_from_db()
        self.assertEqual(self.sku.status_contagem, StatusItemCiclico.DIVERGENTE)
        self.assertContains(response, 'Somente supervisor')

    def test_execucao_nao_exibe_coluna_origem_na_grade(self):
        self._contar_pocket('PKT01', 10)
        response = self.client.get(reverse('ciclico_executar'))
        self.assertNotContains(response, 'sku-col-origem')
        self.assertNotContains(response, '<th>Origem</th>')
        self.assertContains(response, 'bi-eye')
        self.assertNotContains(response, 'Ver detalhes')

        detalhe = self.client.get(reverse('ciclico_sku_detalhe', kwargs={'sku_id': self.sku.pk}))
        self.assertEqual(detalhe.status_code, 200)
        self.assertContains(detalhe, 'Pocket')

    def test_grid_operacional_nao_exibe_formulario_contagem(self):
        response = self.client.get(reverse('ciclico_executar'))
        self.assertNotContains(response, 'Salvar contagem')
        self.assertNotContains(response, 'id="formEditarContagemCiclico"')

    def test_skus_ciclo_incluem_origem_no_dto(self):
        self._contar_pocket('PKT01', 5)
        skus = obter_skus_ciclo(session=self.client.session, incluir_posicoes=False)
        self.assertEqual(len(skus), 1)
        self.assertEqual(skus[0].ultima_origem_label, 'Pocket')

    def test_pocket_rejeita_produto_divergente_do_lote(self):
        response = self.client.post(reverse('pocket:contagem_ciclico'), {
            'acao': 'contagem',
            'sku_id': str(self.sku.pk),
            'codigo_posicao': 'PKT01',
            'codigo_produto_lido': 'OUTRO999',
            'quantidade_fisica': '5',
        }, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.status_code, 400)
        body = response.json()
        texto = str(body.get('message') or body.get('errors'))
        self.assertIn('Produto divergente do SKU selecionado', texto)

    def test_pocket_aceita_ean_do_sku_do_lote(self):
        self.produto.codigo_ean = '7899999000011'
        self.produto.save(update_fields=['codigo_ean'])
        response = self.client.post(reverse('pocket:contagem_ciclico'), {
            'acao': 'contagem',
            'sku_id': str(self.sku.pk),
            'codigo_posicao': 'PKT01',
            'codigo_produto_lido': '7899999000011',
            'quantidade_fisica': '5',
        }, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['ok'])

    def test_pocket_recontagem_valida_quando_sap_bate_com_posicoes_pendentes(self):
        """Recontagem com saldo igual ao SAP deve validar sem exigir todas as posições."""
        self._contar_pocket('PKT01', 50)
        self._contar_pocket('PKT02', 30)
        self._finalizar_pocket()
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
        response = self._contar_pocket_ajax('PKT02', 60)
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

    def _finalizar_pocket(self, sku_id=None):
        sku_id = sku_id or self.sku.pk
        return self.client.post(reverse('pocket:contagem_ciclico'), {
            'acao': 'finalizar_sku',
            'sku_id': str(sku_id),
        }, HTTP_X_REQUESTED_WITH='XMLHttpRequest')

    def test_view_aceitar_divergencia_supervisor(self):
        self.client.post(reverse('pocket:contagem_ciclico'), {
            'acao': 'contagem',
            'sku_id': str(self.sku.pk),
            'codigo_posicao': 'PKV01',
            'codigo_produto_lido': self.produto.codigo_produto,
            'quantidade_fisica': '50',
        }, follow=True)
        self.client.post(reverse('pocket:contagem_ciclico'), {
            'acao': 'contagem',
            'sku_id': str(self.sku.pk),
            'codigo_posicao': 'PKV02',
            'codigo_produto_lido': self.produto.codigo_produto,
            'quantidade_fisica': '30',
        }, follow=True)
        self._finalizar_pocket(sku_id=self.sku.pk)
        self.sku.refresh_from_db()
        self.assertEqual(self.sku.status_contagem, StatusItemCiclico.DIVERGENTE)
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
