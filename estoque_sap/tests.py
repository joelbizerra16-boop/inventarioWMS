from decimal import Decimal

import pandas as pd
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from estoque_sap.models import EstoqueSAP
from estoque_sap.services.importacao_estoque_sap import (
    CAMPOS_CANAIS,
    MAPEAMENTO_COLUNAS,
    excluir_linha_preview,
    importar_dados,
    montar_preview_sessao,
    normalizar_colunas_importacao,
    obter_status_linha,
    processar_arquivo,
    serializar_preview_sessao,
    validar_produto_preview,
)
from inventario.models import CicloInventarioSku
from inventario.services.ciclico import criar_ciclo, limpar_estado_ciclico
from posicoes.models import Posicao
from produtos.models import Produto


def _linha_importacao_sap(codigo: str, total: str = '10') -> dict:
    dados = {
        'codigo_produto': codigo,
        'total': total,
    }
    for campo in CAMPOS_CANAIS:
        dados[campo] = '0' if campo != 'canal_1' else total
    return dados


class NormalizacaoColunasSAPTestCase(TestCase):
    def test_mapeia_layout_sap_b1(self):
        dataframe = pd.DataFrame(columns=[
            'CodProduto', 'Descricao', 0, 1, 2, 110, 66, 80, 81, 82, 99, 'Total',
        ])
        normalizado = normalizar_colunas_importacao(dataframe)

        self.assertEqual(list(normalizado.columns), [
            'codigo_produto',
            'descricao',
            'canal_0',
            'canal_1',
            'canal_2',
            'canal_110',
            'canal_66',
            'canal_80',
            'canal_81',
            'canal_82',
            'canal_99',
            'total',
        ])

    def test_mapeia_layout_interno(self):
        dataframe = pd.DataFrame(columns=MAPEAMENTO_COLUNAS.values())
        normalizado = normalizar_colunas_importacao(dataframe)

        for coluna in (
            'codigo_produto', 'descricao', 'canal_0', 'canal_1', 'canal_2',
            'canal_66', 'canal_80', 'canal_81', 'canal_82', 'canal_99', 'canal_110',
        ):
            self.assertIn(coluna, normalizado.columns)

    def test_aceita_variacoes_de_codigo_produto(self):
        dataframe = pd.DataFrame(columns=['Código Produto', 'Descricao', '0', '1', '2'])
        normalizado = normalizar_colunas_importacao(dataframe)
        self.assertIn('codigo_produto', normalizado.columns)


class ImportacaoSAPB1TestCase(TestCase):
    def setUp(self):
        Produto.objects.create(
            codigo_produto='8',
            descricao='KIT MOD MOBIL TROCA DE OLEO INTELIGENTE',
            embalagem='Unidade',
            setor='LUBRIFICANTE',
        )
        Produto.objects.create(
            codigo_produto='1000',
            descricao='ARLA 32 GRANEL',
            embalagem='Granel',
            setor='QUIMICO',
        )

    def test_processa_planilha_sap_b1_real(self):
        with open('estoque_sap/referencia/sap_b1.xlsx', 'rb') as arquivo:
            preview = processar_arquivo(arquivo)

        self.assertGreater(preview.total_linhas, 0)
        self.assertEqual(
            preview.colunas_detectadas[0],
            'CodProduto',
        )
        self.assertIn('codigo_produto', preview.colunas_normalizadas)
        self.assertIn('canal_110', preview.colunas_normalizadas)
        self.assertIn('total', preview.colunas_normalizadas)

        linha_valida = next(
            linha for linha in preview.linhas if linha.codigo_produto == '8'
        )
        self.assertTrue(linha_valida.valida)
        self.assertEqual(linha_valida.total, Decimal('0'))

        linha_invalida = next(
            linha for linha in preview.linhas if linha.codigo_produto == '10001'
        )
        self.assertFalse(linha_invalida.valida)
        self.assertIn('Produto não cadastrado.', linha_invalida.erros)

    def test_nao_falha_por_colunas_ausentes_no_layout_sap(self):
        with open('estoque_sap/referencia/sap_b1.xlsx', 'rb') as arquivo:
            preview = processar_arquivo(arquivo)

        self.assertIsNotNone(preview.total_linhas)


class PreviewImportacaoSAPTestCase(TestCase):
    def test_status_produto_nao_encontrado(self):
        status = obter_status_linha(False, ['Produto não cadastrado.'])
        self.assertEqual(status, 'Produto não encontrado')

    def test_validar_produto_cria_precadastro_e_libera_linha(self):
        with open('estoque_sap/referencia/sap_b1.xlsx', 'rb') as arquivo:
            preview = processar_arquivo(arquivo)

        linhas = serializar_preview_sessao(preview)
        linha_alvo = next(
            linha for linha in linhas if linha['codigo_produto'] == '10001'
        )

        self.assertFalse(linha_alvo['valida'])
        linhas = validar_produto_preview(linhas, linha_alvo['linha'])
        linha_atualizada = next(
            linha for linha in linhas if linha['linha'] == linha_alvo['linha']
        )

        self.assertTrue(linha_atualizada['valida'])
        self.assertEqual(linha_atualizada['status'], 'Válido')
        self.assertEqual(linha_atualizada['erros'], [])
        self.assertTrue(
            Produto.objects.filter(codigo_produto='10001').exists(),
        )
        produto = Produto.objects.get(codigo_produto='10001')
        self.assertEqual(produto.setor, 'PENDENTE')
        self.assertTrue(produto.ativo)

    def test_excluir_linha_remove_da_sessao_sem_apagar_banco(self):
        Produto.objects.create(
            codigo_produto='999',
            descricao='Produto Teste Exclusão Preview',
            setor='TESTE',
        )

        with open('estoque_sap/referencia/sap_b1.xlsx', 'rb') as arquivo:
            preview = processar_arquivo(arquivo)

        linhas = serializar_preview_sessao(preview)
        total_antes = len(linhas)
        linha_alvo = linhas[0]
        linhas = excluir_linha_preview(linhas, linha_alvo['linha'])
        preview_atualizado = montar_preview_sessao(linhas)

        self.assertEqual(len(linhas), total_antes)
        self.assertEqual(preview_atualizado.total_linhas, total_antes - 1)
        self.assertTrue(Produto.objects.filter(pk__isnull=False).exists())


class ImportacaoSAPCicloPerformanceTestCase(TestCase):
    def setUp(self):
        limpar_estado_ciclico()
        self.posicao = Posicao.objects.create(codigo='SAP-PERF', posicao='P-01')
        self.produtos = []
        for indice in range(368):
            codigo = f'SAP{indice:04d}'
            produto = Produto.objects.create(
                codigo_produto=codigo,
                descricao=f'Produto {codigo}',
                setor='A',
                embalagem='Unidade',
                participa_ciclico=True,
            )
            EstoqueSAP.objects.create(
                produto=produto,
                total=Decimal('10'),
                arquivo_origem='inicial.xlsx',
            )
            self.produtos.append(produto)
        criar_ciclo()

    def tearDown(self):
        limpar_estado_ciclico()

    @override_settings(DEBUG=True)
    def test_importar_368_registros_sincroniza_ciclo_sem_n_plus1(self):
        linhas = [_linha_importacao_sap(p.codigo_produto, '15') for p in self.produtos]

        with CaptureQueriesContext(connection) as contexto:
            resultado = importar_dados(linhas, arquivo_origem='carga.xlsx')

        self.assertEqual(resultado.inseridos + resultado.atualizados, 368)
        skus = CicloInventarioSku.objects.filter(status_contagem='PENDENTE')
        self.assertEqual(skus.count(), 368)
        self.assertTrue(all(sku.quantidade_sap == Decimal('15') for sku in skus))
        self.assertLessEqual(
            len(contexto.captured_queries),
            25,
            msg='Importação SAP com ciclo ativo deve evitar N+1 na sincronização.',
        )
