import io

import pandas as pd
from django.test import TestCase

from produtos.forms import ProdutoForm
from produtos.models import Produto
from produtos.services.importacao_produtos import (
    ImportacaoProdutosError,
    importar_dados,
    processar_arquivo,
)


def _gerar_arquivo_excel(dataframe: pd.DataFrame, nome: str = 'teste.xlsx') -> io.BytesIO:
    buffer = io.BytesIO()
    dataframe.to_excel(buffer, index=False)
    buffer.seek(0)
    buffer.name = nome
    return buffer


class ProdutoFormTestCase(TestCase):
    def setUp(self):
        Produto.objects.create(
            codigo_produto='P001',
            descricao='Produto A',
            embalagem='BOMBONA',
            setor='LUBRIFICANTE',
        )
        Produto.objects.create(
            codigo_produto='P002',
            descricao='Produto B',
            embalagem='TAMBOR',
            setor='QUÍMICOS',
        )

    def test_carrega_opcoes_distintas_embalagem_e_setor(self):
        form = ProdutoForm()
        self.assertEqual(form.opcoes_embalagem, ['BOMBONA', 'TAMBOR'])
        self.assertEqual(form.opcoes_setor, ['LUBRIFICANTE', 'QUÍMICOS'])

    def test_opcoes_sem_duplicatas_com_multiplos_produtos(self):
        for indice in range(5):
            Produto.objects.create(
                codigo_produto=f'PD{indice}',
                descricao=f'Produto duplicado {indice}',
                embalagem='BALDE',
                setor='LUBRIFICANTE',
            )

        form = ProdutoForm()

        self.assertEqual(len(form.opcoes_embalagem), len(set(form.opcoes_embalagem)))
        self.assertEqual(len(form.opcoes_setor), len(set(form.opcoes_setor)))
        self.assertEqual(form.opcoes_embalagem.count('BALDE'), 1)
        self.assertEqual(form.opcoes_setor.count('LUBRIFICANTE'), 1)

    def test_permite_novo_valor_embalagem_e_setor(self):
        form = ProdutoForm(data={
            'codigo_produto': 'P003',
            'descricao': 'Produto C',
            'embalagem': 'GRANEL',
            'setor': 'MANUTENÇÃO',
            'codigo_ean': '',
            'ativo': True,
        })
        self.assertTrue(form.is_valid(), form.errors)
        produto = form.save()
        self.assertEqual(produto.embalagem, 'GRANEL')
        self.assertEqual(produto.setor, 'MANUTENÇÃO')


class ImportacaoProdutosColunasTestCase(TestCase):
    def test_aceita_coluna_cod_com_ponto(self):
        arquivo = _gerar_arquivo_excel(pd.DataFrame({
            'Cód.': ['110267'],
            'Descrição': ['NUTO H 32 200L'],
            'Embalagem': ['TAMBOR'],
            'Setor': ['LUBRIFICANTE'],
            'EAN': ['7896636548367'],
        }))

        preview = processar_arquivo(arquivo)

        self.assertEqual(preview.linhas_validas, 1)
        self.assertEqual(preview.linhas[0].codigo_produto, '110267')
        self.assertEqual(preview.linhas[0].codigo_ean, '7896636548367')

    def test_aceita_cabecalhos_ean_e_persiste_no_banco(self):
        casos = [
            {'EAN': [7896636548367]},
            {'Ean': [7896636548367]},
            {'Código EAN': [7896636548367]},
            {'Codigo EAN': [7896636548367]},
        ]
        for indice, coluna_ean in enumerate(casos):
            with self.subTest(cabecalho=next(iter(coluna_ean))):
                arquivo = _gerar_arquivo_excel(pd.DataFrame({
                    'Cod_prod': [f'EAN{indice}'],
                    'Descrição': ['Produto EAN'],
                    'Embalagem': ['TAMBOR'],
                    'SETOR': ['LUBRIFICANTE'],
                    **coluna_ean,
                }))
                preview = processar_arquivo(arquivo)
                linhas = [
                    {
                        'codigo_produto': linha.codigo_produto,
                        'descricao': linha.descricao,
                        'embalagem': linha.embalagem,
                        'setor': linha.setor,
                        'codigo_ean': linha.codigo_ean,
                    }
                    for linha in preview.linhas
                    if linha.valida
                ]
                importar_dados(linhas)
                produto = Produto.objects.get(codigo_produto=f'EAN{indice}')
                self.assertEqual(produto.codigo_ean, '7896636548367')

    def test_aceita_variacoes_codigo_e_setor(self):
        arquivo = _gerar_arquivo_excel(pd.DataFrame({
            'Codigo': ['110268'],
            'Descrição': ['NUTO H 32 20L'],
            'Embalagem': ['BOMBONA'],
            'SETOR': ['LUBRIFICANTE'],
        }))

        preview = processar_arquivo(arquivo)

        self.assertEqual(preview.linhas_validas, 1)
        self.assertEqual(preview.linhas[0].codigo_produto, '110268')
        self.assertEqual(preview.linhas[0].setor, 'LUBRIFICANTE')

    def test_rejeita_planilha_sem_coluna_codigo(self):
        arquivo = _gerar_arquivo_excel(pd.DataFrame({
            'Descrição': ['Produto sem código'],
            'Embalagem': ['TAMBOR'],
            'Setor': ['LUBRIFICANTE'],
        }))

        with self.assertRaisesMessage(
            ImportacaoProdutosError,
            'Coluna Cod_prod não encontrada.',
        ):
            processar_arquivo(arquivo)

    def test_rejeita_planilha_vazia(self):
        arquivo = _gerar_arquivo_excel(pd.DataFrame(columns=[
            'Cod_prod',
            'Descrição',
            'Embalagem',
            'SETOR',
        ]))

        with self.assertRaisesMessage(ImportacaoProdutosError, 'Planilha vazia.'):
            processar_arquivo(arquivo)


class ImportacaoProdutosMobilTestCase(TestCase):
    def test_importa_arquivo_referencia_mobil(self):
        caminho = 'produtos/referencia/produtos_mobil.xlsx'
        with open(caminho, 'rb') as arquivo:
            preview = processar_arquivo(arquivo)

        self.assertGreater(preview.total_linhas, 0)
        self.assertEqual(preview.linhas_invalidas, 0)
        self.assertEqual(preview.linhas_validas, preview.total_linhas)

        linhas = [
            {
                'codigo_produto': linha.codigo_produto,
                'descricao': linha.descricao,
                'embalagem': linha.embalagem,
                'setor': linha.setor,
                'codigo_ean': linha.codigo_ean,
            }
            for linha in preview.linhas
            if linha.valida
        ]
        codigos_unicos = len({linha.codigo_produto for linha in preview.linhas if linha.valida})
        resultado = importar_dados(linhas)

        self.assertEqual(codigos_unicos, 372)
        self.assertEqual(resultado.inseridos + resultado.atualizados, preview.linhas_validas)
        self.assertEqual(Produto.objects.count(), codigos_unicos)

        produto = Produto.objects.get(codigo_produto='110267')
        self.assertEqual(produto.descricao, 'NUTO H 32 200L')
        self.assertEqual(produto.embalagem, 'TAMBOR')
        self.assertEqual(produto.setor, 'LUBRIFICANTE')

    def test_reimportacao_atualiza_sem_duplicar(self):
        caminho = 'produtos/referencia/produtos_mobil.xlsx'
        with open(caminho, 'rb') as arquivo:
            preview = processar_arquivo(arquivo)

        linhas = [
            {
                'codigo_produto': linha.codigo_produto,
                'descricao': linha.descricao,
                'embalagem': linha.embalagem,
                'setor': linha.setor,
                'codigo_ean': linha.codigo_ean,
            }
            for linha in preview.linhas
            if linha.valida
        ]
        importar_dados(linhas)
        resultado = importar_dados(linhas)

        codigos_unicos = len({linha.codigo_produto for linha in preview.linhas if linha.valida})

        self.assertEqual(resultado.inseridos, 0)
        self.assertEqual(resultado.atualizados, preview.linhas_validas)
        self.assertEqual(Produto.objects.count(), codigos_unicos)
