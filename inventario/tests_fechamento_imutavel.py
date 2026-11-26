"""Fechamento finalizado permanece na fotografia gravada."""

import json
from decimal import Decimal
from io import BytesIO

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from openpyxl import load_workbook
from unittest.mock import patch

from accounts.test_utils import criar_usuario_teste
from core.categorias import SESSAO_CHAVE_CATEGORIA, CategoriaInventario
from estoque_sap.models import EstoqueSAP, ImportacaoEstoqueSAP
from inventario.models import Inventario, InventarioItem
from inventario.models_comparativo import ComparativoAcuracidade, ComparativoContagem
from inventario.services.comparativo_contagens import (
    OPCAO_NOVA_REFERENCIA,
    analisar,
    finalizar_comparativo,
    salvar_comparativo,
)
from inventario.services.comparativo_exportacao import gerar_pasta
from inventario.services.fechamento import MENSAGEM_CANAL_AUSENTE, MENSAGEM_INDISPONIVEL
from inventario.services.historico_exportacao import _linhas_exportacao
from inventario.services.historico_unificado import obter_detalhe_historico_unificado
from inventario.services.inventario_snapshot import congelar_snapshot_inventario
from dashboard.services.dashboard import obter_indicadores_dashboard
from dashboard.services.painel_inventario import montar_painel
from posicoes.models import Posicao
from produtos.models import Produto

LUB = CategoriaInventario.LUBRIFICANTES
FIL = CategoriaInventario.FILTROS
AGR = CategoriaInventario.AGREGADO
TABELAS_ATUAIS = (
    'estoque_sap_estoquesap',
    'inventario_inventarioitem',
    'produtos_produto',
    'posicoes_posicao',
    'inventario_referenciasapitem',
)


def _escritas(queries):
    proibidas = []
    for consulta in queries:
        sql = consulta['sql'].lstrip().upper()
        if sql.startswith(('INSERT', 'UPDATE', 'DELETE')) and 'DJANGO_SESSION' not in sql:
            proibidas.append(consulta['sql'])
    return proibidas


def _usa_tabela_atual(queries):
    usadas = []
    for consulta in queries:
        sql = consulta['sql'].lower()
        for tabela in TABELAS_ATUAIS:
            if tabela in sql:
                usadas.append(sql)
                break
    return usadas


class FechamentoImutavelTestCase(TestCase):
    def setUp(self):
        self.user, self.perfil = criar_usuario_teste(username='fechamento.foto')
        self.client.force_login(self.user)
        self.posicao = Posicao.objects.create(codigo='FECH-01', posicao='A-01', categoria=LUB)
        self.produtos = []
        for indice in range(37):
            self.produtos.append(Produto.objects.create(
                codigo_produto=f'F{indice:03d}',
                descricao=f'Produto {indice:03d}',
                setor='LUBRIFICANTE',
            ))
        self.divergente = self.produtos[-1]
        quantidade = {produto.pk: Decimal('10') for produto in self.produtos}
        quantidade[self.divergente.pk] = Decimal('5')
        self.c1 = self._inventario(LUB, quantidade)
        self.c2 = self._inventario(LUB, quantidade)
        for produto in self.produtos:
            EstoqueSAP.objects.create(
                produto=produto,
                canal_110=Decimal('10'),
                canal_1=Decimal('10'),
                total=Decimal('20'),
                arquivo_origem='fechamento.xlsx',
            )
        self.comparativo = salvar_comparativo(
            comparativo=None,
            nome='Fechamento COSAN',
            inventarios={1: self.c1, 2: self.c2, 3: None},
            referencia_opcao=OPCAO_NOVA_REFERENCIA,
            estado=None,
            usuario=self.user,
            categoria=LUB,
        )
        self.anterior = ComparativoAcuracidade.objects.create(
            comparativo=self.comparativo,
            canal='cosan',
            percentual=Decimal('96.61'),
            skus_avaliados=295,
            skus_acertos=285,
            skus_divergentes=10,
            skus_sem_consenso=0,
            skus_total=295,
            usuario=self.user,
        )
        self.comparativo = finalizar_comparativo(self.comparativo, self.user)
        ComparativoAcuracidade.objects.filter(pk=self.anterior.pk).update(
            data_hora=timezone.now(),
        )
        self.foto = json.dumps(self.comparativo.snapshot_resultado, sort_keys=True)

    def _inventario(self, categoria, quantidades):
        inventario = Inventario.objects.create(
            usuario=self.perfil,
            status=Inventario.Status.EM_ANDAMENTO,
            categoria=categoria,
        )
        InventarioItem.objects.bulk_create([
            InventarioItem(
                inventario=inventario,
                posicao=self.posicao,
                produto_id=produto_id,
                quantidade_fisica=quantidade,
            )
            for produto_id, quantidade in quantidades.items()
        ])
        return inventario

    def _selecionar(self, categoria):
        sessao = self.client.session
        sessao[SESSAO_CHAVE_CATEGORIA] = categoria
        sessao.save()

    def _divergencias(self):
        painel = montar_painel(self.comparativo, 'cosan')
        return [
            (item.codigo_produto, item.fisico, item.contabil, item.diferenca, item.status_label)
            for item in painel.divergencias_sap
        ]

    def test_acuracidade_do_fechamento_nao_usa_registro_anterior(self):
        resultado = analisar(
            self.comparativo, canal='cosan', visao='sku',
            filtros=type('F', (), {
                'termo': '', 'status_contagem': '', 'status_sap': '', 'necessita_c3': False,
            })(),
        )
        self.assertEqual(resultado.acuracidade.percentual, Decimal('97.30'))
        self.assertEqual(resultado.acuracidade.acertos, 36)
        self.assertEqual(resultado.acuracidade.avaliados, 37)
        self.assertNotEqual(resultado.acuracidade.percentual, Decimal('96.61'))
        self.assertTrue(ComparativoAcuracidade.objects.filter(pk=self.anterior.pk, percentual=Decimal('96.61')).exists())

        def falso(*args, **kwargs):
            from inventario.services.comparativo_contagens import Acuracidade
            return Acuracidade(
                canal='cosan', disponivel=True, motivo='', avaliados=295, acertos=285,
                divergentes=10, sem_consenso=0, total=295, percentual=Decimal('96.61'),
            )

        with patch('inventario.services.comparativo_contagens.calcular_acuracidade', falso):
            de_novo = analisar(
                self.comparativo, canal='cosan', visao='sku',
                filtros=resultado.filtros,
            )
        self.assertEqual(de_novo.acuracidade.percentual, Decimal('97.30'))

        self._selecionar(LUB)
        pagina = self.client.get(reverse('comparativo:acompanhamento', args=[self.comparativo.pk]) + '?canal=cosan')
        self.assertContains(pagina, '97,3%')
        self.assertNotContains(pagina, '96,61')
        painel = self.client.get(reverse('home') + f'?conferencia={self.comparativo.pk}&canal=cosan')
        self.assertContains(painel, '97,3%')
        self.assertNotContains(painel, '96,61')

    def test_divergencias_quantidades_e_graficos_nao_mudam(self):
        antes = self._divergencias()
        self.assertEqual(antes, [('F036', Decimal('5'), Decimal('10'), Decimal('-5'), 'Déficit físico')])
        painel_antes = montar_painel(self.comparativo, 'cosan')
        situacao_antes = [(fatia.chave, fatia.quantidade) for fatia in painel_antes.situacao]
        indicador_antes = painel_antes.indicador_sku.percentual
        from inventario.services.comparativo_contagens import FiltrosComparativo
        filtros = FiltrosComparativo('', '', '', False)
        linha = {item.codigo_produto: item for item in analisar(
            self.comparativo, canal='cosan', visao='sku', filtros=filtros,
        ).linhas}['F036']
        self.assertEqual(linha.medicoes[1].quantidade, Decimal('5'))
        self.assertEqual(linha.medicoes[2].quantidade, Decimal('5'))
        self.assertEqual(linha.contagens.status, 'OK')
        self.assertEqual(linha.status_sap.status, 'DIVERGENTE')

        EstoqueSAP.objects.update(canal_110=Decimal('999'), canal_1=Decimal('999'), total=Decimal('1998'))
        Produto.objects.filter(pk=self.divergente.pk).update(descricao='Trocado agora')
        Posicao.objects.filter(pk=self.posicao.pk).update(posicao='Z-99')
        InventarioItem.objects.update(quantidade_fisica=Decimal('1'))

        self.assertEqual(self._divergencias(), antes)
        painel = montar_painel(self.comparativo, 'cosan')
        self.assertEqual([(fatia.chave, fatia.quantidade) for fatia in painel.situacao], situacao_antes)
        self.assertEqual(painel.indicador_sku.percentual, indicador_antes)
        self.assertEqual(painel.acuracidade_fechamento.percentual, Decimal('97.30'))
        linha = {item.codigo_produto: item for item in analisar(
            self.comparativo, canal='cosan', visao='sku', filtros=FiltrosComparativo('', '', '', False),
        ).linhas}['F036']
        self.assertEqual(linha.medicoes[1].quantidade, Decimal('5'))
        self.assertEqual(linha.descricao, 'Produto 036')
        self.assertEqual(linha.posicoes[0][1], 'A-01')
        self.comparativo.refresh_from_db()
        self.assertEqual(json.dumps(self.comparativo.snapshot_resultado, sort_keys=True), self.foto)

    def test_sem_canal_abre_o_resultado_salvo_e_canal_ausente_avisa(self):
        self._selecionar(LUB)
        pagina = self.client.get(reverse('comparativo:acompanhamento', args=[self.comparativo.pk]))
        self.assertEqual(pagina.context['canal'], 'ambas')
        self.assertEqual(pagina.context['aviso_canal'], '')

        produto = Produto.objects.create(codigo_produto='SOCOS', descricao='Só COSAN', setor='LUBRIFICANTE')
        posicao = Posicao.objects.create(codigo='SOCOS', posicao='B-01', categoria=LUB)
        inventario_c1 = Inventario.objects.create(
            usuario=self.perfil, status=Inventario.Status.EM_ANDAMENTO, categoria=LUB,
        )
        inventario_c2 = Inventario.objects.create(
            usuario=self.perfil, status=Inventario.Status.EM_ANDAMENTO, categoria=LUB,
        )
        for inventario in (inventario_c1, inventario_c2):
            InventarioItem.objects.create(
                inventario=inventario, posicao=posicao, produto=produto, quantidade_fisica=Decimal('4'),
            )
        importacao = ImportacaoEstoqueSAP.objects.create(
            arquivo_origem='so-cosan.xlsx',
            data_importacao=timezone.now(),
            canais_carregados=['canal_110'],
        )
        EstoqueSAP.objects.create(
            produto=produto, canal_110=Decimal('4'), canal_1=Decimal('0'), total=Decimal('4'),
            arquivo_origem='so-cosan.xlsx', importacao=importacao,
        )
        conferencia = salvar_comparativo(
            comparativo=None, nome='Só COSAN', inventarios={1: inventario_c1, 2: inventario_c2, 3: None},
            referencia_opcao=OPCAO_NOVA_REFERENCIA, estado=None, usuario=self.user, categoria=LUB,
        )
        conferencia = finalizar_comparativo(conferencia, self.user)
        aberta = self.client.get(reverse('comparativo:acompanhamento', args=[conferencia.pk]))
        self.assertEqual(aberta.context['canal'], 'cosan')
        self.assertEqual(aberta.context['resultado'].acuracidade.percentual, Decimal('100.00'))
        ausente = self.client.get(reverse('comparativo:acompanhamento', args=[conferencia.pk]) + '?canal=brida')
        self.assertContains(ausente, MENSAGEM_CANAL_AUSENTE)
        self.assertEqual(ausente.context['resultado'].acuracidade.percentual, None)

    def test_aberto_permanece_ao_vivo_e_categorias_isoladas(self):
        aberto = salvar_comparativo(
            comparativo=None, nome='Ainda aberta',
            inventarios={1: self.c1, 2: self.c2, 3: None},
            referencia_opcao=str(self.comparativo.referencia_sap_id),
            estado=None, usuario=self.user, categoria=LUB,
        )
        from inventario.services.comparativo_contagens import FiltrosComparativo
        filtros = FiltrosComparativo('', '', '', False)
        antes = analisar(aberto, canal='cosan', visao='sku', filtros=filtros).acuracidade.percentual
        InventarioItem.objects.filter(inventario=self.c1, produto=self.divergente).update(quantidade_fisica=Decimal('8'))
        aberto.refresh_from_db()
        depois = analisar(aberto, canal='cosan', visao='sku', filtros=filtros).acuracidade.percentual
        self.assertNotEqual(antes, depois)
        self.assertEqual(
            analisar(self.comparativo, canal='cosan', visao='sku', filtros=filtros).acuracidade.percentual,
            Decimal('97.30'),
        )

        posicao = Posicao.objects.create(codigo='FIL-01', posicao='F-01', categoria=FIL)
        produto = Produto.objects.create(codigo_produto='FILTRO-X', descricao='Filtro', setor='FILTRO')
        quantidade = {produto.pk: Decimal('3')}
        fil_c1 = Inventario.objects.create(usuario=self.perfil, status=Inventario.Status.EM_ANDAMENTO, categoria=FIL)
        fil_c2 = Inventario.objects.create(usuario=self.perfil, status=Inventario.Status.EM_ANDAMENTO, categoria=FIL)
        for inventario in (fil_c1, fil_c2):
            InventarioItem.objects.create(
                inventario=inventario, posicao=posicao, produto=produto, quantidade_fisica=Decimal('3'),
            )
        EstoqueSAP.objects.create(
            produto=produto, canal_110=Decimal('9'), canal_1=Decimal('9'), total=Decimal('18'),
            arquivo_origem='filtro.xlsx',
        )
        filtros_conf = salvar_comparativo(
            comparativo=None, nome='Filtros', inventarios={1: fil_c1, 2: fil_c2, 3: None},
            referencia_opcao=OPCAO_NOVA_REFERENCIA, estado=None, usuario=self.user, categoria=FIL,
        )
        filtros_conf = finalizar_comparativo(filtros_conf, self.user)
        self._selecionar(LUB)
        html = self.client.get(reverse('home') + f'?conferencia={self.comparativo.pk}&canal=cosan').content.decode()
        self.assertNotIn('FILTRO-X', html)
        self._selecionar(FIL)
        html_fil = self.client.get(reverse('home') + f'?conferencia={filtros_conf.pk}&canal=cosan').content.decode()
        self.assertIn('FILTRO-X', html_fil)
        self.assertNotIn('F036', html_fil)
        self._selecionar(AGR)
        self.assertNotIn(
            filtros_conf.pk,
            [conf.pk for conf in self.client.get(reverse('home')).context['conferencias']],
        )

    def test_abrir_painel_finalizado_nao_grava_nem_consulta_tabelas_atuais(self):
        self._selecionar(LUB)
        urls = [
            reverse('home') + f'?conferencia={self.comparativo.pk}&canal=cosan',
            reverse('comparativo:acompanhamento', args=[self.comparativo.pk]) + '?canal=cosan&forcar=1',
            reverse('comparativo:exportar', args=[self.comparativo.pk]) + '?canal=cosan',
        ]
        for url in urls:
            with CaptureQueriesContext(connection) as contexto:
                resposta = self.client.get(url)
            self.assertEqual(resposta.status_code, 200)
            self.assertEqual(_escritas(contexto.captured_queries), [])
            self.assertEqual(_usa_tabela_atual(contexto.captured_queries), [])
        self.comparativo.refresh_from_db()
        self.assertEqual(json.dumps(self.comparativo.snapshot_resultado, sort_keys=True), self.foto)

    def test_exportacao_e_historico_repetem_o_painel(self):
        from inventario.services.comparativo_contagens import FiltrosComparativo
        filtros = FiltrosComparativo('', '', '', False)
        resultado = analisar(self.comparativo, canal='cosan', visao='sku', filtros=filtros)
        pasta, _nome = gerar_pasta(self.comparativo, canal='cosan', filtros=filtros, usuario=self.user)
        resumo = pasta['Resumo']
        valor = None
        for linha in range(2, resumo.max_row + 1):
            if str(resumo.cell(linha, 1).value).startswith('Acuracidade SAP'):
                valor = resumo.cell(linha, 2).value
        self.assertEqual(Decimal(str(valor)), resultado.acuracidade.percentual)

        resposta = self.client.get(reverse('comparativo:exportar', args=[self.comparativo.pk]) + '?canal=cosan')
        planilha = load_workbook(BytesIO(resposta.content))['Resumo']
        exportado = None
        for linha in range(2, planilha.max_row + 1):
            if str(planilha.cell(linha, 1).value).startswith('Acuracidade SAP'):
                exportado = planilha.cell(linha, 2).value
        self.assertEqual(Decimal(str(exportado)), Decimal('97.30'))

        inventario = Inventario.objects.create(
            usuario=self.perfil, status=Inventario.Status.FINALIZADO, categoria=LUB,
        )
        InventarioItem.objects.create(
            inventario=inventario, posicao=self.posicao, produto=self.divergente, quantidade_fisica=Decimal('5'),
        )
        congelar_snapshot_inventario(inventario, self.perfil)
        detalhe = obter_detalhe_historico_unificado('GERAL', inventario.pk)
        sap_antes = [produto.sap for produto in detalhe.produtos]
        EstoqueSAP.objects.update(total=Decimal('1'))
        detalhe_depois = obter_detalhe_historico_unificado('GERAL', inventario.pk)
        self.assertEqual([produto.sap for produto in detalhe_depois.produtos], sap_antes)
        self.assertEqual(
            [linha['SAP'] for linha in _linhas_exportacao(detalhe_depois)],
            sap_antes,
        )
        indicadores = obter_indicadores_dashboard(LUB)
        self.assertEqual(indicadores.maiores_divergencias_inventario.pk, inventario.pk)
        por_codigo = {produto.codigo_produto: Decimal(produto.sap) for produto in detalhe_depois.produtos}
        divergencia = indicadores.maiores_divergencias[0]
        self.assertEqual(divergencia.total_contabil, por_codigo[divergencia.codigo_produto])

    def test_snapshot_antigo_nao_e_regravado_e_usa_o_registro(self):
        bruto = json.loads(json.dumps(self.comparativo.snapshot_resultado))
        del bruto['fechamento']
        bruto['versao'] = 2
        evento = self.comparativo.eventos.filter(acao='FINALIZADA').order_by('-data_hora', '-pk').first()
        cosan = ComparativoAcuracidade.objects.exclude(pk=self.anterior.pk).get(
            comparativo=self.comparativo, canal='cosan',
        )
        evento.dados = {**(evento.dados or {}), 'acuracidades': [cosan.pk]}
        evento.save(update_fields=['dados'])
        self.comparativo.snapshot_resultado = bruto
        self.comparativo.save(update_fields=['snapshot_resultado'])
        from inventario.services.comparativo_contagens import FiltrosComparativo
        resultado = analisar(
            self.comparativo, canal='cosan', visao='sku', filtros=FiltrosComparativo('', '', '', False),
        )
        self.assertEqual(resultado.acuracidade.percentual, Decimal('97.30'))
        self._selecionar(LUB)
        self.client.get(reverse('comparativo:acompanhamento', args=[self.comparativo.pk]) + '?canal=cosan')
        self.comparativo.refresh_from_db()
        self.assertNotIn('fechamento', self.comparativo.snapshot_resultado)
        self.assertEqual(self.comparativo.snapshot_resultado['versao'], 2)
        self.assertContains(
            self.client.get(reverse('comparativo:acompanhamento', args=[self.comparativo.pk]) + '?canal=brida'),
            MENSAGEM_CANAL_AUSENTE,
        )

    def test_inventario_sem_snapshot_mostra_indisponivel(self):
        inventario = Inventario.objects.create(
            usuario=self.perfil, status=Inventario.Status.FINALIZADO, categoria=LUB,
        )
        indicadores = obter_indicadores_dashboard(LUB)
        self.assertEqual(indicadores.acuracidade, None)
        self.assertEqual(indicadores.aviso_inventario, MENSAGEM_INDISPONIVEL)
        self.assertEqual(indicadores.maiores_divergencias, [])
        painel = montar_painel(None, 'ambas', LUB)
        self.assertEqual(painel.aviso_inventario, MENSAGEM_INDISPONIVEL)
        self._selecionar(LUB)
        paginas = [
            reverse('dashboard_classico'),
            reverse('confronto') + f'?inventario={inventario.pk}',
            reverse('aprovacao') + f'?inventario={inventario.pk}',
            reverse('historico_detalhe', args=['GERAL', inventario.pk]),
        ]
        for url in paginas:
            html = self.client.get(url).content.decode()
            self.assertIn(MENSAGEM_INDISPONIVEL, html)
            self.assertNotIn('id="acuracidade-geral">0', html)
            self.assertNotIn('id="acuracidade-confronto">0', html)
            self.assertNotIn('id="acuracidade-aprovacao">0', html)
            self.assertNotRegex(html, r'id="acuracidade-[^"]+">0')
        painel_html = self.client.get(reverse('dashboard_classico')).content.decode()
        self.assertIn('id="acuracidade-geral">—<', painel_html)
        self.assertIn('id="acuracidade-confronto">—<', self.client.get(
            reverse('confronto') + f'?inventario={inventario.pk}',
        ).content.decode())
        self.assertIn('id="acuracidade-aprovacao">—<', self.client.get(
            reverse('aprovacao') + f'?inventario={inventario.pk}',
        ).content.decode())


def _divergencia(codigo, fisico, sap, diferenca, status):
    return (
        codigo,
        Decimal(str(fisico)).quantize(Decimal('0.01')),
        Decimal(str(sap)).quantize(Decimal('0.01')),
        Decimal(str(diferenca)).quantize(Decimal('0.01')),
        status,
    )


class AprovacaoFechamentoTestCase(TestCase):
    def setUp(self):
        self.user, self.perfil = criar_usuario_teste(username='aprovacao.foto')
        self.client.force_login(self.user)
        self.posicao = Posicao.objects.create(codigo='APR-01', posicao='B-09', categoria=LUB)
        self.divergente = Produto.objects.create(
            codigo_produto='DIV-APR', descricao='Divergente Aprovacao', setor='LUBRIFICANTE',
        )
        self.correto = Produto.objects.create(
            codigo_produto='OK-APR', descricao='Correto Aprovacao', setor='LUBRIFICANTE',
        )
        self.inventario = Inventario.objects.create(
            usuario=self.perfil, status=Inventario.Status.FINALIZADO, categoria=LUB,
        )
        InventarioItem.objects.create(
            inventario=self.inventario, posicao=self.posicao, produto=self.divergente,
            quantidade_fisica=Decimal('5'),
        )
        InventarioItem.objects.create(
            inventario=self.inventario, posicao=self.posicao, produto=self.correto,
            quantidade_fisica=Decimal('10'),
        )
        for produto, total in ((self.divergente, Decimal('10')), (self.correto, Decimal('10'))):
            EstoqueSAP.objects.create(
                produto=produto, canal_110=total, canal_1=Decimal('0'), total=total,
                arquivo_origem='aprovacao.xlsx',
            )
        congelar_snapshot_inventario(self.inventario, self.perfil)
        self.foto = json.dumps(self.inventario.snapshot_resultado, sort_keys=True)
        sessao = self.client.session
        sessao[SESSAO_CHAVE_CATEGORIA] = LUB
        sessao.save()

    def _fontes(self):
        from inventario.services.aprovacao import consultar_aprovacao

        aprovacao = [
            _divergencia(linha.codigo_produto, linha.fisico, linha.total_contabil, linha.diferenca, linha.status_label)
            for linha in consultar_aprovacao(self.inventario.pk).linhas
        ]
        painel = [
            _divergencia(linha.codigo_produto, linha.fisico, linha.total_contabil, linha.diferenca, linha.status_label)
            for linha in montar_painel(None, 'ambas', LUB).divergencias_inventario
        ]
        detalhe = obter_detalhe_historico_unificado('GERAL', self.inventario.pk)
        historico = [
            _divergencia(produto.codigo_produto, produto.contado, produto.sap, produto.diferenca, produto.status)
            for produto in detalhe.produtos
            if produto.status != 'Correto'
        ]
        exportadas = [
            _divergencia(linha['Código'], linha['Contado'], linha['SAP'], linha['Diferença'], linha['Status'])
            for linha in _linhas_exportacao(detalhe)
            if linha['Status'] != 'Correto'
        ]
        resposta = self.client.get(
            reverse('historico_exportar', args=['GERAL', self.inventario.pk]) + '?formato=excel',
        )
        planilha = load_workbook(BytesIO(resposta.content))['Resultados']
        cabecalho = [celula.value for celula in planilha[1]]
        arquivo = []
        for linha in planilha.iter_rows(min_row=2, values_only=True):
            registro = dict(zip(cabecalho, linha))
            if registro['Status'] == 'Correto':
                continue
            arquivo.append(_divergencia(
                registro['Código'], registro['Contado'], registro['SAP'],
                registro['Diferença'], registro['Status'],
            ))
        return aprovacao, painel, historico, exportadas, arquivo

    def test_aprovacao_repete_painel_historico_e_exportacao_sem_dados_atuais(self):
        from inventario.services.aprovacao import aprovar_inventario

        esperado = [_divergencia('DIV-APR', '5', '10', '-5', 'Déficit físico')]
        with patch('inventario.services.aprovacao.executar_confronto') as confronto:
            for url in (
                reverse('aprovacao') + f'?inventario={self.inventario.pk}',
                reverse('aprovacao') + f'?inventario={self.inventario.pk}&q=DIV-APR',
            ):
                with CaptureQueriesContext(connection) as contexto:
                    resposta = self.client.get(url)
                self.assertEqual(resposta.status_code, 200)
                self.assertEqual(_escritas(contexto.captured_queries), [])
                self.assertEqual(_usa_tabela_atual(contexto.captured_queries), [])
            self.assertEqual(confronto.call_count, 0)
            aprovacao, painel, historico, exportadas, arquivo = self._fontes()
        self.assertEqual(aprovacao, esperado)
        self.assertEqual(painel, esperado)
        self.assertEqual(historico, esperado)
        self.assertEqual(exportadas, esperado)
        self.assertEqual(arquivo, esperado)

        self.inventario.refresh_from_db()
        self.assertIsNone(self.inventario.confronto_executado_em)
        self.assertEqual(json.dumps(self.inventario.snapshot_resultado, sort_keys=True), self.foto)

        EstoqueSAP.objects.update(total=Decimal('1'), canal_110=Decimal('1'), canal_1=Decimal('1'))
        self.divergente.descricao = 'Descricao alterada'
        self.divergente.save(update_fields=['descricao'])
        self.posicao.posicao = 'Z-99'
        self.posicao.save(update_fields=['posicao'])
        InventarioItem.objects.filter(inventario=self.inventario).update(quantidade_fisica=Decimal('99'))

        with patch('inventario.services.aprovacao.executar_confronto') as confronto:
            aprovacao, painel, historico, exportadas, arquivo = self._fontes()
            self.assertEqual(confronto.call_count, 0)
        self.assertEqual(aprovacao, esperado)
        self.assertEqual(painel, esperado)
        self.assertEqual(historico, esperado)
        self.assertEqual(exportadas, esperado)
        self.assertEqual(arquivo, esperado)
        detalhe = obter_detalhe_historico_unificado('GERAL', self.inventario.pk)
        self.assertEqual(detalhe.produtos[0].descricao, 'Divergente Aprovacao')
        self.assertEqual(detalhe.produtos[0].posicoes[0].alocacao, 'B-09')
        self.inventario.refresh_from_db()
        self.assertEqual(json.dumps(self.inventario.snapshot_resultado, sort_keys=True), self.foto)

        aprovar_inventario(self.inventario)
        self.inventario.refresh_from_db()
        self.assertEqual(self.inventario.status_aprovacao, 'APROVADO')
        self.assertIsNone(self.inventario.confronto_executado_em)
        self.assertEqual(json.dumps(self.inventario.snapshot_resultado, sort_keys=True), self.foto)

