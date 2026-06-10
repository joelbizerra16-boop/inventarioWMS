import io
from decimal import Decimal

import pandas as pd
from django.test import TestCase
from django.urls import reverse

from accounts.models import Usuario
from accounts.test_utils import ClienteAutenticadoMixin, criar_usuario_teste
from inventario.models import CicloInventario
from inventario.services.ciclico import (
    CiclicoError,
    FiltrosCicloConsulta,
    StatusCiclo,
    criar_ciclo,
    encerrar_ciclo,
    limpar_estado_ciclico,
    listar_ciclos_historico,
    obter_ciclo_consulta,
    reabrir_ciclo,
)
from inventario.services.ciclico_exportacao import exportar_ciclo_excel
from inventario.services.ciclico_relatorio import (
    ABAS_EXCEL_EXECUTIVO,
    obter_dados_exportacao_premium,
    obter_relatorio_executivo,
)
from inventario.services.ciclico_relatorio_pdf import gerar_relatorio_executivo_pdf
from inventario.tests_ciclico_professional import CiclicoProfissionalBaseMixin


class CiclicoPremiumTestCase(CiclicoProfissionalBaseMixin, ClienteAutenticadoMixin, TestCase):
    def setUp(self):
        self.user = self.autenticar_cliente()
        limpar_estado_ciclico()

    def tearDown(self):
        limpar_estado_ciclico()

    def test_encerrar_ciclo_persiste_snapshot_e_status(self):
        self._sap('PRE01', 'Bombona', Decimal('10'))
        ciclo = criar_ciclo(usuario_criacao=self.user)
        encerrado = encerrar_ciclo()

        encerrado.refresh_from_db()
        self.assertEqual(encerrado.status_ciclo, StatusCiclo.ENCERRADO)
        self.assertFalse(encerrado.ativo)
        self.assertIsNotNone(encerrado.data_encerramento)
        self.assertEqual(encerrado.quantidade_skus_planejados, 1)
        self.assertIsNotNone(encerrado.percentual_executado)

    def test_ciclo_encerrado_permanece_consultavel(self):
        self._sap('PRE02', 'Tambor', Decimal('20'))
        ciclo = criar_ciclo()
        ciclo_id = ciclo.pk
        encerrar_ciclo()

        consultado = obter_ciclo_consulta(ciclo_id)
        self.assertIsNotNone(consultado)
        self.assertEqual(consultado.pk, ciclo_id)
        self.assertEqual(consultado.status_ciclo, StatusCiclo.ENCERRADO)

        historico = listar_ciclos_historico()
        self.assertTrue(any(item.pk == ciclo_id for item in historico))

    def test_relatorio_executivo_ciclo_encerrado(self):
        self._sap('PRE03', 'Balde', Decimal('30'))
        ciclo = criar_ciclo()
        ciclo_id = ciclo.pk
        encerrar_ciclo()

        relatorio = obter_relatorio_executivo(ciclo_id)
        self.assertEqual(relatorio.ciclo.pk, ciclo_id)
        self.assertEqual(relatorio.resumo.total_skus, 1)
        self.assertEqual(len(relatorio.indicadores), 3)

    def test_exportacao_premium_multi_abas(self):
        self._sap('PRE04', 'Caixa', Decimal('40'))
        ciclo = criar_ciclo()
        ciclo_id = ciclo.pk
        encerrar_ciclo()

        dados = obter_dados_exportacao_premium(ciclo_id)
        for aba in ABAS_EXCEL_EXECUTIVO:
            self.assertIn(aba, dados)
        self.assertGreaterEqual(len(dados['01_RESUMO']), 15)

    def test_exportar_excel_premium_http(self):
        self._sap('PRE05', 'IBC', Decimal('50'))
        ciclo = criar_ciclo()
        ciclo_id = ciclo.pk
        encerrar_ciclo()

        response = exportar_ciclo_excel(ciclo_id, premium=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            response['Content-Type'],
        )
        self.assertIn('Relatorio_Executivo', response['Content-Disposition'])

    def test_exportar_pdf_executivo_http(self):
        self._sap('PRE05B', 'IBC', Decimal('50'))
        ciclo = criar_ciclo()
        ciclo_id = ciclo.pk
        encerrar_ciclo()

        response = gerar_relatorio_executivo_pdf(ciclo_id)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertTrue(response.content.startswith(b'%PDF'))

    def test_admin_reabre_ciclo_encerrado(self):
        self._sap('PRE06', 'Bombona', Decimal('10'))
        ciclo = criar_ciclo()
        ciclo_id = ciclo.pk
        encerrar_ciclo()

        reaberto = reabrir_ciclo(ciclo_id)
        self.assertEqual(reaberto.status_ciclo, StatusCiclo.ATIVO)
        self.assertTrue(reaberto.ativo)
        self.assertIsNone(reaberto.data_encerramento)

    def test_nao_reabrir_com_ciclo_ativo(self):
        self._sap('PRE07A', 'Bombona', Decimal('10'))
        self._sap('PRE07B', 'Tambor', Decimal('10'))
        ciclo1 = criar_ciclo()
        encerrar_ciclo()
        criar_ciclo()

        with self.assertRaises(CiclicoError):
            reabrir_ciclo(ciclo1.pk)

    def test_views_consulta_e_relatorio_ciclo_encerrado(self):
        self._sap('PRE08', 'Bombona', Decimal('10'))
        ciclo = criar_ciclo()
        ciclo_id = ciclo.pk
        encerrar_ciclo()

        consulta = self.client.get(reverse('ciclico_consulta'), {'ciclo': ciclo_id})
        self.assertEqual(consulta.status_code, 200)
        self.assertContains(consulta, f'Ciclo #{ciclo_id}')
        self.assertContains(consulta, 'Relatório Executivo PDF')
        self.assertContains(consulta, 'Excel Consulta')
        self.assertNotContains(consulta, 'Relatório Executivo Excel')

        relatorio = self.client.get(reverse('ciclico_relatorio'), {'ciclo': ciclo_id})
        self.assertEqual(relatorio.status_code, 200)
        self.assertEqual(relatorio['Content-Type'], 'application/pdf')

    def test_reabrir_via_post_admin(self):
        admin, _ = criar_usuario_teste(
            username='admin_premium',
            perfil=Usuario.Perfil.ADMINISTRADOR,
        )
        self.client.force_login(admin)

        self._sap('PRE09', 'Bombona', Decimal('10'))
        ciclo = criar_ciclo()
        ciclo_id = ciclo.pk
        encerrar_ciclo()

        response = self.client.post(reverse('ciclico'), {
            'acao': 'reabrir',
            'ciclo_id': str(ciclo_id),
        }, follow=True)
        self.assertEqual(response.status_code, 200)

        ciclo.refresh_from_db()
        self.assertEqual(ciclo.status_ciclo, StatusCiclo.ATIVO)

    def test_exportar_excel_consulta_colunas_e_filtro_sku(self):
        self._sap('PRE10A', 'Bombona', Decimal('10'))
        self._sap('PRE10B', 'Tambor', Decimal('20'))
        ciclo = criar_ciclo()
        ciclo_id = ciclo.pk
        encerrar_ciclo()

        filtros = FiltrosCicloConsulta(sku='PRE10A', ciclo_id=ciclo_id)
        response = exportar_ciclo_excel(ciclo_id, filtros)
        self.assertIn('Consulta_Ciclo_', response['Content-Disposition'])

        planilha = pd.read_excel(io.BytesIO(response.content), sheet_name='Consulta Ciclo')
        self.assertEqual(len(planilha), 1)
        self.assertEqual(planilha.iloc[0]['SKU'], 'PRE10A')
        colunas_esperadas = [
            'Ciclo', 'SKU', 'Descrição', 'Embalagem', 'Canal', 'SAP', 'Cosan', 'Brida',
            'Físico', 'Diferença', 'Indicador', 'Status', 'Origem', 'Usuário',
            'Data Contagem', 'Última Alteração', 'Quantidade de Recontagens', 'Observações',
        ]
        self.assertEqual(list(planilha.columns), colunas_esperadas)

    def test_relatorio_executivo_respeita_filtro_embalagem(self):
        self._sap('PRE11A', 'Bombona', Decimal('10'))
        self._sap('PRE11B', 'Tambor', Decimal('20'))
        ciclo = criar_ciclo()
        ciclo_id = ciclo.pk
        encerrar_ciclo()

        filtros = FiltrosCicloConsulta(embalagem='Bombona', ciclo_id=ciclo_id)
        relatorio = obter_relatorio_executivo(ciclo_id, filtros)
        self.assertEqual(relatorio.resumo.total_skus, 1)
        self.assertIn('Embalagem: Bombona', relatorio.filtros_aplicados)

    def test_pdf_executivo_com_filtros(self):
        self._sap('PRE12A', 'Bombona', Decimal('10'))
        self._sap('PRE12B', 'Tambor', Decimal('20'))
        ciclo = criar_ciclo()
        ciclo_id = ciclo.pk
        encerrar_ciclo()

        response = gerar_relatorio_executivo_pdf(
            ciclo_id,
            FiltrosCicloConsulta(embalagem='Tambor', ciclo_id=ciclo_id),
            self.user,
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content.startswith(b'%PDF'))
