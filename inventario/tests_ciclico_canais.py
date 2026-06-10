import time
from decimal import Decimal
from io import BytesIO

import pandas as pd
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Usuario
from accounts.test_utils import ClienteAutenticadoMixin, criar_usuario_teste
from estoque_fisico.models import EstoqueFisico
from estoque_sap.models import EstoqueSAP
from inventario.models import CicloInventarioSku
from inventario.services.ciclico import (
    IndicadorConfronto,
    _calcular_indicador_confronto,
    criar_ciclo,
    limpar_estado_ciclico,
    obter_consulta_agrupada_por_sku,
    obter_dados_exportacao_ciclo,
    obter_resumo_ciclico,
    obter_skus_ciclo,
    salvar_contagem_sku,
)
from posicoes.models import Posicao
from produtos.models import Produto


class CiclicoCanaisBaseMixin:
    def _criar_sap(
        self,
        codigo: str,
        total: Decimal,
        cosan: Decimal | None = None,
        brida: Decimal | None = None,
    ) -> Produto:
        produto = Produto.objects.create(
            codigo_produto=codigo,
            descricao=f'Produto {codigo}',
            setor='A',
            embalagem='Unidade',
        )
        EstoqueSAP.objects.create(
            produto=produto,
            total=total,
            canal_1=brida if brida is not None else Decimal('0'),
            canal_110=cosan if cosan is not None else Decimal('0'),
            arquivo_origem='teste.xlsx',
        )
        return produto

    def _criar_ciclo_com_fisico(
        self,
        codigo: str,
        total: Decimal,
        cosan: Decimal | None,
        brida: Decimal | None,
        fisico: Decimal,
    ) -> CicloInventarioSku:
        posicao = Posicao.objects.create(
            codigo=f'POS-{codigo}',
            posicao=f'P-{codigo}',
        )
        produto = self._criar_sap(codigo, total, cosan=cosan, brida=brida)
        EstoqueFisico.objects.create(
            posicao=posicao,
            produto=produto,
            quantidade=fisico,
            data_contagem=timezone.now(),
        )
        criar_ciclo()
        sku = CicloInventarioSku.objects.get(codigo_produto=codigo)
        return sku


class CiclicoIndicadorCosanTestCase(CiclicoCanaisBaseMixin, TestCase):
    def test_indicador_verde(self):
        diff, indicador, tooltip = _calcular_indicador_confronto(Decimal('100'), Decimal('100'))
        self.assertEqual(diff, Decimal('0'))
        self.assertEqual(indicador, IndicadorConfronto.VERDE)

    def test_indicador_laranja(self):
        diff, indicador, tooltip = _calcular_indicador_confronto(Decimal('105'), Decimal('100'))
        self.assertEqual(diff, Decimal('5'))
        self.assertEqual(indicador, IndicadorConfronto.LARANJA)

    def test_indicador_vermelho(self):
        diff, indicador, tooltip = _calcular_indicador_confronto(Decimal('95'), Decimal('100'))
        self.assertEqual(diff, Decimal('-5'))
        self.assertEqual(indicador, IndicadorConfronto.VERMELHO)

    def test_sem_fisico_ou_referencia_sem_indicador(self):
        diff, indicador, tooltip = _calcular_indicador_confronto(None, Decimal('10'))
        self.assertIsNone(diff)
        self.assertIsNone(indicador)
        self.assertEqual(tooltip, '')


class CiclicoCanaisIntegracaoTestCase(CiclicoCanaisBaseMixin, TestCase):
    def setUp(self):
        limpar_estado_ciclico()
        self.user, _ = criar_usuario_teste(
            username='canais.user',
            perfil=Usuario.Perfil.INVENTARIO,
        )

    def tearDown(self):
        limpar_estado_ciclico()

    def test_sku_com_cosan_e_brida(self):
        sku = self._criar_ciclo_com_fisico(
            'CAN01',
            Decimal('37'),
            Decimal('37'),
            Decimal('37'),
            Decimal('37'),
        )
        detalhe = obter_skus_ciclo(apenas_lote_diario=False)[0]
        self.assertEqual(detalhe.quantidade_cosan, Decimal('37'))
        self.assertEqual(detalhe.quantidade_brida, Decimal('37'))

        posicao = sku.posicoes.get()
        salvar_contagem_sku(sku.pk, {posicao.pk: Decimal('37')}, self.user)
        detalhe = obter_skus_ciclo(apenas_lote_diario=False)[0]
        self.assertEqual(detalhe.indicador_sap, IndicadorConfronto.VERDE)

    def test_fisico_maior_sap_indicador_laranja(self):
        sku = self._criar_ciclo_com_fisico(
            'CAN02',
            Decimal('370'),
            Decimal('370'),
            Decimal('370'),
            Decimal('369'),
        )
        posicao = sku.posicoes.get()
        salvar_contagem_sku(sku.pk, {posicao.pk: Decimal('375')}, self.user)
        detalhe = obter_skus_ciclo(apenas_lote_diario=False)[0]
        self.assertEqual(detalhe.diferenca_cosan, Decimal('5'))
        self.assertEqual(detalhe.indicador_sap, IndicadorConfronto.LARANJA)

    def test_fisico_menor_sap_indicador_vermelho(self):
        sku = self._criar_ciclo_com_fisico(
            'CAN03',
            Decimal('10'),
            Decimal('10'),
            Decimal('8'),
            Decimal('10'),
        )
        posicao = sku.posicoes.get()
        salvar_contagem_sku(sku.pk, {posicao.pk: Decimal('8')}, self.user)
        detalhe = obter_skus_ciclo(apenas_lote_diario=False)[0]
        self.assertEqual(detalhe.diferenca_cosan, Decimal('-2'))
        self.assertEqual(detalhe.indicador_sap, IndicadorConfronto.VERMELHO)

    def test_canais_congelados_apos_remover_sap(self):
        produto = self._criar_sap(
            'SEMCOSAN',
            Decimal('10'),
            Decimal('10'),
            Decimal('8'),
        )
        posicao = Posicao.objects.create(codigo='SC01', posicao='S-01')
        EstoqueFisico.objects.create(
            posicao=posicao,
            produto=produto,
            quantidade=Decimal('5'),
            data_contagem=timezone.now(),
        )
        criar_ciclo()
        EstoqueSAP.objects.filter(produto=produto).delete()

        detalhe = obter_skus_ciclo(apenas_lote_diario=False)[0]
        self.assertEqual(detalhe.quantidade_cosan, Decimal('10'))
        self.assertEqual(detalhe.quantidade_brida, Decimal('8'))

    def test_sku_sem_brida_exibe_zero(self):
        self._criar_sap('SEMBRIDA', Decimal('15'), cosan=Decimal('15'), brida=Decimal('0'))
        criar_ciclo()
        detalhe = obter_skus_ciclo(apenas_lote_diario=False)[0]
        self.assertEqual(detalhe.quantidade_cosan, Decimal('15'))
        self.assertEqual(detalhe.quantidade_brida, Decimal('0'))

    def test_dashboard_indicadores_cosan(self):
        for codigo, cosan, fisico in [
            ('DBV', Decimal('10'), Decimal('10')),
            ('DBO', Decimal('20'), Decimal('25')),
            ('DBR', Decimal('30'), Decimal('28')),
        ]:
            posicao = Posicao.objects.create(
                codigo=f'POS-{codigo}',
                posicao=f'P-{codigo}',
            )
            produto = self._criar_sap(
                codigo,
                cosan,
                cosan,
                cosan,
            )
            EstoqueFisico.objects.create(
                posicao=posicao,
                produto=produto,
                quantidade=fisico,
                data_contagem=timezone.now(),
            )

        criar_ciclo()
        for codigo, _, fisico in [
            ('DBV', Decimal('10'), Decimal('10')),
            ('DBO', Decimal('20'), Decimal('25')),
            ('DBR', Decimal('30'), Decimal('28')),
        ]:
            sku = CicloInventarioSku.objects.get(codigo_produto=codigo)
            salvar_contagem_sku(
                sku.pk,
                {sku.posicoes.get().pk: fisico},
                self.user,
            )

        resumo = obter_resumo_ciclico()
        self.assertEqual(resumo.skus_conciliados_cosan, 1)
        self.assertEqual(resumo.skus_acima_cosan, 1)
        self.assertEqual(resumo.skus_abaixo_cosan, 1)

    def test_consulta_consolidada_com_canais(self):
        sku = self._criar_ciclo_com_fisico(
            'CONS01',
            Decimal('100'),
            Decimal('100'),
            Decimal('99'),
            Decimal('100'),
        )
        posicao = sku.posicoes.get()
        salvar_contagem_sku(sku.pk, {posicao.pk: Decimal('102')}, self.user)

        grupos = obter_consulta_agrupada_por_sku()
        self.assertEqual(len(grupos), 1)
        self.assertEqual(grupos[0].cosan_total, Decimal('100'))
        self.assertEqual(grupos[0].brida_total, Decimal('99'))
        self.assertEqual(grupos[0].diferenca_cosan, Decimal('2'))
        self.assertEqual(grupos[0].indicador_sap, IndicadorConfronto.LARANJA)
        self.assertIsNotNone(grupos[0].ultima_contagem)

    def test_exportacao_excel_com_colunas_canais(self):
        sku = self._criar_ciclo_com_fisico(
            'EXP01',
            Decimal('50'),
            Decimal('50'),
            Decimal('48'),
            Decimal('50'),
        )
        posicao = sku.posicoes.get()
        salvar_contagem_sku(sku.pk, {posicao.pk: Decimal('50')}, self.user)

        linhas = obter_dados_exportacao_ciclo()
        self.assertEqual(len(linhas), 1)
        self.assertEqual(linhas[0]['COSAN'], 50.0)
        self.assertEqual(linhas[0]['BRIDA'], 48.0)
        self.assertEqual(linhas[0]['FÍSICO'], 50.0)
        self.assertEqual(linhas[0]['Diferença'], 0.0)
        self.assertEqual(linhas[0]['Indicador'], '🟢')

    def test_performance_5000_skus(self):
        produtos = [
            Produto(
                codigo_produto=f'P{i:05d}',
                descricao=f'Bulk {i}',
                setor='A',
                embalagem='Un',
            )
            for i in range(5000)
        ]
        Produto.objects.bulk_create(produtos)
        produtos_db = {
            produto.codigo_produto: produto
            for produto in Produto.objects.filter(codigo_produto__startswith='P')
        }
        EstoqueSAP.objects.bulk_create([
            EstoqueSAP(
                produto=produtos_db[f'P{i:05d}'],
                total=Decimal('1'),
                canal_1=Decimal('1'),
                canal_110=Decimal('1'),
                arquivo_origem='bulk.xlsx',
            )
            for i in range(5000)
        ])

        criar_ciclo()
        inicio = time.perf_counter()
        skus = obter_skus_ciclo(apenas_lote_diario=False)
        duracao = time.perf_counter() - inicio

        self.assertEqual(len(skus), 5000)
        self.assertEqual(skus[0].quantidade_cosan, Decimal('1'))
        self.assertLess(duracao, 30.0)


class CiclicoCanaisViewTestCase(CiclicoCanaisBaseMixin, ClienteAutenticadoMixin, TestCase):
    def setUp(self):
        self.user = self.autenticar_cliente(Usuario.Perfil.INVENTARIO)
        limpar_estado_ciclico()
        self.sku = self._criar_ciclo_com_fisico(
            'VIEW01',
            Decimal('37'),
            Decimal('37'),
            Decimal('37'),
            Decimal('37'),
        )

    def tearDown(self):
        limpar_estado_ciclico()

    def test_execucao_exibe_colunas_canais(self):
        response = self.client.get(reverse('ciclico_executar'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Cosan')
        self.assertContains(response, 'Brida')
        self.assertContains(response, 'Gerar lote')

    def test_detalhe_exibe_canais_e_saldo(self):
        response = self.client.get(
            reverse('ciclico_sku_detalhe', kwargs={'sku_id': self.sku.pk}),
        )
        self.assertContains(response, '37')
        self.assertContains(response, 'SAP')

    def test_consulta_exibe_canais(self):
        response = self.client.get(reverse('ciclico_consulta'))
        self.assertContains(response, 'Cosan')
        self.assertContains(response, 'Brida')

    def test_dashboard_exibe_indicadores_sap(self):
        response = self.client.get(reverse('ciclico'))
        self.assertContains(response, 'Conciliados')
        self.assertContains(response, 'Acima SAP')
        self.assertContains(response, 'Abaixo SAP')

    def test_exportar_excel(self):
        response = self.client.get(reverse('ciclico_exportar'))
        self.assertEqual(response.status_code, 200)
        self.assertIn(
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            response['Content-Type'],
        )
        dataframe = pd.read_excel(BytesIO(response.content))
        self.assertIn('Cosan', dataframe.columns)
        self.assertIn('Brida', dataframe.columns)
        self.assertIn('Indicador', dataframe.columns)
