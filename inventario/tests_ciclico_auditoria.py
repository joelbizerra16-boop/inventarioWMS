from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Usuario
from accounts.test_utils import ClienteAutenticadoMixin, criar_usuario_teste
from estoque_fisico.models import EstoqueFisico
from estoque_sap.models import EstoqueSAP
from estoque_sap.services.importacao_estoque_sap import importar_dados
from inventario.models import (
    CicloAuditoriaHistorico,
    CicloInventarioItem,
    CicloInventarioSku,
)
from inventario.services.ciclico import (
    CiclicoError,
    MSG_CICLO_ENCERRADO,
    StatusItemCiclico,
    criar_ciclo,
    definir_dia_execucao,
    editar_contagem_ciclico,
    encerrar_ciclo,
    limpar_estado_ciclico,
    obter_consulta_agrupada_por_sku,
    obter_resumo_ciclico,
    obter_skus_ciclo,
    salvar_contagem_sku,
    sincronizar_sap_ciclo_ativo,
    usuario_pode_editar_contagem_ciclico,
)
from posicoes.models import Posicao
from produtos.models import Produto


class CiclicoAuditoriaBaseMixin:
    def _criar_par_sap_fisico(
        self,
        codigo: str,
        posicao: Posicao,
        sap_total: Decimal,
        fisico: Decimal,
    ) -> Produto:
        produto = Produto.objects.create(
            codigo_produto=codigo,
            descricao=f'Produto {codigo}',
            setor='A',
            embalagem='Unidade',
        )
        EstoqueSAP.objects.create(
            produto=produto,
            total=sap_total,
            arquivo_origem='teste.xlsx',
        )
        EstoqueFisico.objects.create(
            posicao=posicao,
            produto=produto,
            quantidade=fisico,
            data_contagem=timezone.now(),
        )
        return produto

    def _importar_sap(self, codigo: str, total: Decimal):
        importar_dados(
            [{
                'codigo_produto': codigo,
                'total': str(total),
                'canal_0': '0',
                'canal_1': str(total),
                'canal_2': '0',
                'canal_66': '0',
                'canal_80': '0',
                'canal_81': '0',
                'canal_82': '0',
                'canal_99': '0',
                'canal_110': '0',
            }],
            'atualizacao.xlsx',
        )


class CiclicoAuditoriaTestCase(CiclicoAuditoriaBaseMixin, TestCase):
    def setUp(self):
        limpar_estado_ciclico()
        self.user_a, self.operador_a = criar_usuario_teste(
            username='ciclo.usuario.a',
            perfil=Usuario.Perfil.INVENTARIO,
        )
        self.operador_a.nome = 'João'
        self.operador_a.save(update_fields=['nome'])
        self.posicao = Posicao.objects.create(codigo='CIC-P01', posicao='1 1 10')

    def tearDown(self):
        limpar_estado_ciclico()

    def test_criar_ciclo_com_100_skus_congela_lista(self):
        posicoes = [
            Posicao.objects.create(codigo=f'CIC-{i:03d}', posicao=f'P-{i}')
            for i in range(100)
        ]
        for indice, posicao in enumerate(posicoes):
            self._criar_par_sap_fisico(
                f'CIC{indice:03d}',
                posicao,
                Decimal('10'),
                Decimal('10'),
            )

        ciclo = criar_ciclo()
        total_inicial = CicloInventarioSku.objects.filter(ciclo=ciclo).count()
        self.assertEqual(total_inicial, 100)

        self._importar_sap('CIC000', Decimal('999'))
        Produto.objects.create(
            codigo_produto='CICNOVO',
            descricao='Novo SAP',
            setor='A',
        )
        self._importar_sap('CICNOVO', Decimal('50'))

        self.assertEqual(
            CicloInventarioSku.objects.filter(ciclo=ciclo).count(),
            total_inicial,
        )
        self.assertFalse(
            CicloInventarioSku.objects.filter(
                ciclo=ciclo,
                codigo_produto='CICNOVO',
            ).exists(),
        )

    def test_somente_pendentes_atualizam_sap(self):
        for indice in range(5):
            posicao = Posicao.objects.create(
                codigo=f'PEN-{indice}',
                posicao=f'P-{indice}',
            )
            self._criar_par_sap_fisico(
                f'PEN{indice}',
                posicao,
                Decimal('100'),
                Decimal('100'),
            )

        ciclo = criar_ciclo()
        skus = list(CicloInventarioSku.objects.filter(ciclo=ciclo).order_by('pk'))
        self.assertEqual(len(skus), 5)

        for sku in skus[:2]:
            posicao = sku.posicoes.get()
            salvar_contagem_sku(sku.pk, {posicao.pk: Decimal('100')}, self.user_a)

        for codigo in [f'PEN{i}' for i in range(5)]:
            self._importar_sap(codigo, Decimal('120'))

        skus_atualizados = {
            sku.codigo_produto: sku
            for sku in CicloInventarioSku.objects.filter(ciclo=ciclo)
        }
        self.assertEqual(skus_atualizados['PEN0'].quantidade_sap, Decimal('100'))
        self.assertEqual(skus_atualizados['PEN1'].quantidade_sap, Decimal('100'))
        self.assertEqual(skus_atualizados['PEN2'].quantidade_sap, Decimal('120'))
        self.assertEqual(skus_atualizados['PEN3'].quantidade_sap, Decimal('120'))
        self.assertEqual(skus_atualizados['PEN4'].quantidade_sap, Decimal('120'))

    def test_remover_sku_sap_nao_remove_do_ciclo(self):
        produto = self._criar_par_sap_fisico(
            'REM001',
            self.posicao,
            Decimal('15'),
            Decimal('15'),
        )
        ciclo = criar_ciclo()
        EstoqueSAP.objects.filter(produto=produto).delete()

        self.assertTrue(
            CicloInventarioSku.objects.filter(
                ciclo=ciclo,
                codigo_produto='REM001',
            ).exists(),
        )

    def test_produto_multiplas_posicoes_soma_consolidada(self):
        produto = Produto.objects.create(
            codigo_produto='203075',
            descricao='Oleo 25/40',
            setor='A',
            embalagem='Bombona',
        )
        EstoqueSAP.objects.create(
            produto=produto,
            total=Decimal('70'),
            arquivo_origem='teste.xlsx',
        )
        quantidades = [Decimal('10'), Decimal('20'), Decimal('20'), Decimal('10'), Decimal('10')]
        alocacoes = ['1 1 10', '1 2 11', '1 2 9', '1 3 2', '2 3 5']
        posicoes = []
        for indice, alocacao in enumerate(alocacoes):
            posicao = Posicao.objects.create(
                codigo=f'MULT-{indice}',
                posicao=alocacao,
            )
            posicoes.append(posicao)
            EstoqueFisico.objects.create(
                posicao=posicao,
                produto=produto,
                quantidade=quantidades[indice],
                data_contagem=timezone.now(),
            )

        ciclo = criar_ciclo()
        sku = CicloInventarioSku.objects.get(ciclo=ciclo, produto=produto)
        self.assertEqual(sku.posicoes.count(), 5)
        self.assertEqual(sku.quantidade_sap, Decimal('70'))

        itens_posicao = list(sku.posicoes.order_by('codigo_posicao'))
        quantidades_contagem = [
            Decimal('10'),
            Decimal('20'),
            Decimal('18'),
            Decimal('10'),
            Decimal('10'),
        ]
        contagens = {
            item.pk: quantidades_contagem[indice]
            for indice, item in enumerate(itens_posicao)
        }
        salvar_contagem_sku(sku.pk, contagens, self.user_a)

        sku.refresh_from_db()
        self.assertEqual(sku.quantidade_fisica, Decimal('68'))
        self.assertEqual(sku.diferenca, Decimal('-2'))
        self.assertEqual(sku.status_contagem, StatusItemCiclico.DIVERGENTE)

        grupos = obter_consulta_agrupada_por_sku()
        self.assertEqual(len(grupos), 1)
        self.assertEqual(grupos[0].sap_total, Decimal('70'))
        self.assertEqual(grupos[0].fisico_total, Decimal('68'))
        self.assertEqual(len(grupos[0].posicoes), 5)

    def test_divergencia_recontagem_e_historico(self):
        produto = self._criar_par_sap_fisico(
            '110830',
            self.posicao,
            Decimal('120'),
            Decimal('120'),
        )
        ciclo = criar_ciclo()
        sku = CicloInventarioSku.objects.get(ciclo=ciclo, produto=produto)
        posicao = sku.posicoes.get()

        salvar_contagem_sku(sku.pk, {posicao.pk: Decimal('118')}, self.user_a)
        sku.refresh_from_db()
        self.assertEqual(sku.status_contagem, StatusItemCiclico.DIVERGENTE)
        self.assertEqual(sku.diferenca, Decimal('-2'))

        self._importar_sap('110830', Decimal('130'))
        sku.refresh_from_db()
        self.assertEqual(sku.quantidade_sap, Decimal('120'))

        user_b, _ = criar_usuario_teste(
            username='ciclo.usuario.b',
            perfil=Usuario.Perfil.INVENTARIO,
        )
        salvar_contagem_sku(
            sku.pk,
            {posicao.pk: Decimal('120')},
            user_b,
            recontagem=True,
        )
        sku.refresh_from_db()
        self.assertEqual(sku.status_contagem, StatusItemCiclico.VALIDADO)

        historico = CicloAuditoriaHistorico.objects.filter(ciclo_sku=sku).order_by('data_hora')
        self.assertGreaterEqual(historico.count(), 3)

    def test_recontagem_mantem_divergente_quando_ainda_diverge(self):
        produto = self._criar_par_sap_fisico(
            '110829',
            self.posicao,
            Decimal('84'),
            Decimal('84'),
        )
        ciclo = criar_ciclo()
        sku = CicloInventarioSku.objects.get(ciclo=ciclo, produto=produto)
        posicao = sku.posicoes.get()

        salvar_contagem_sku(sku.pk, {posicao.pk: Decimal('81')}, self.user_a)
        salvar_contagem_sku(
            sku.pk,
            {posicao.pk: Decimal('3')},
            self.user_a,
            recontagem=True,
        )
        sku.refresh_from_db()
        self.assertEqual(sku.quantidade_fisica, Decimal('3'))
        self.assertEqual(sku.diferenca, Decimal('-81'))
        self.assertEqual(sku.status_contagem, StatusItemCiclico.DIVERGENTE)

    def test_rastreabilidade_usuario_e_data(self):
        produto = self._criar_par_sap_fisico(
            'RAST01',
            self.posicao,
            Decimal('10'),
            Decimal('10'),
        )
        ciclo = criar_ciclo()
        sku = CicloInventarioSku.objects.get(ciclo=ciclo, produto=produto)
        posicao = sku.posicoes.get()

        salvar_contagem_sku(sku.pk, {posicao.pk: Decimal('10')}, self.user_a)
        posicao.refresh_from_db()

        self.assertEqual(posicao.usuario_contagem_id, self.user_a.pk)
        self.assertIsNotNone(posicao.data_contagem)
        self.assertEqual(posicao.usuario_contagem_nome, 'João')

    def test_dashboard_por_sku(self):
        for indice in range(3):
            posicao = Posicao.objects.create(
                codigo=f'DASH-{indice}',
                posicao=f'D-{indice}',
            )
            self._criar_par_sap_fisico(
                f'DASH{indice}',
                posicao,
                Decimal('5'),
                Decimal('5'),
            )

        criar_ciclo()
        sku = CicloInventarioSku.objects.first()
        posicao = sku.posicoes.get()
        salvar_contagem_sku(sku.pk, {posicao.pk: Decimal('5')}, self.user_a)

        resumo = obter_resumo_ciclico()
        self.assertEqual(resumo.total_skus, 3)
        self.assertEqual(resumo.skus_pendentes, 2)
        self.assertEqual(resumo.skus_contados, 1)

    def test_encerramento_ciclo(self):
        self._criar_par_sap_fisico('ENC01', self.posicao, Decimal('3'), Decimal('3'))
        criar_ciclo()
        ciclo_encerrado = encerrar_ciclo()
        self.assertFalse(ciclo_encerrado.ativo)
        self.assertIsNotNone(ciclo_encerrado.data_encerramento)


class CiclicoAuditoriaViewTestCase(CiclicoAuditoriaBaseMixin, ClienteAutenticadoMixin, TestCase):
    def setUp(self):
        self.user = self.autenticar_cliente()
        limpar_estado_ciclico()
        self.posicao = Posicao.objects.create(codigo='VIEW01', posicao='V-01')
        self._criar_par_sap_fisico('VIEWP1', self.posicao, Decimal('8'), Decimal('8'))

    def tearDown(self):
        limpar_estado_ciclico()

    def test_criar_ciclo_pela_view(self):
        response = self.client.post(reverse('ciclico'), {'acao': 'criar'})
        self.assertRedirects(response, reverse('ciclico'))
        self.assertEqual(CicloInventarioSku.objects.count(), 1)

    def test_executar_contagem_via_servico_exibida_na_view(self):
        from inventario.services.ciclico import salvar_contagem_sku

        self.client.post(reverse('ciclico'), {'acao': 'criar'})
        sku = CicloInventarioSku.objects.get()
        posicao = sku.posicoes.get()
        self.client.post(reverse('ciclico_executar'), {
            'acao': 'gerar_lote',
            'quantidade_skus': '1',
        })
        salvar_contagem_sku(sku.pk, {posicao.pk: Decimal('8')}, self.user)
        response = self.client.get(reverse('ciclico_executar'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'VIEWP1')
        sku.refresh_from_db()
        self.assertEqual(sku.status_contagem, StatusItemCiclico.CONTADO)

    def test_consulta_agrupada(self):
        self.client.post(reverse('ciclico'), {'acao': 'criar'})
        response = self.client.get(reverse('ciclico_consulta'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'VIEWP1')
        self.assertContains(response, 'Usuários')

    def test_criar_ciclo_congela_todos_sap(self):
        for indice in range(5):
            posicao = Posicao.objects.create(
                codigo=f'LIM-{indice}',
                posicao=f'L-{indice}',
            )
            self._criar_par_sap_fisico(
                f'LIM{indice}',
                posicao,
                Decimal('1'),
                Decimal('1'),
            )
        self.client.post(reverse('ciclico'), {'acao': 'criar'})
        total_sap = EstoqueSAP.objects.count()
        self.assertEqual(CicloInventarioSku.objects.count(), total_sap)
        self.assertGreaterEqual(total_sap, 5)


class CiclicoSapFonteOficialTestCase(CiclicoAuditoriaBaseMixin, TestCase):
    """Ciclo gerado exclusivamente do estoque SAP."""

    def setUp(self):
        limpar_estado_ciclico()
        self.user, _ = criar_usuario_teste(
            username='sap.fonte',
            perfil=Usuario.Perfil.INVENTARIO,
        )
        self.posicao = Posicao.objects.create(codigo='SAP-P01', posicao='1 1 1')

    def tearDown(self):
        limpar_estado_ciclico()

    def _criar_sap_sem_fisico(self, codigo: str, total: Decimal) -> Produto:
        produto = Produto.objects.create(
            codigo_produto=codigo,
            descricao=f'Produto {codigo}',
            setor='A',
            embalagem='Unidade',
        )
        EstoqueSAP.objects.create(
            produto=produto,
            total=total,
            arquivo_origem='teste.xlsx',
        )
        return produto

    def test_ciclo_igual_quantidade_sap(self):
        quantidade_sap = 50
        for indice in range(quantidade_sap):
            self._criar_sap_sem_fisico(f'SAP{indice:03d}', Decimal('10'))

        ciclo = criar_ciclo()
        self.assertEqual(EstoqueSAP.objects.count(), quantidade_sap)
        self.assertEqual(CicloInventarioSku.objects.filter(ciclo=ciclo).count(), quantidade_sap)
        self.assertEqual(ciclo.quantidade_skus_planejados, quantidade_sap)

    def test_fisico_vazio_nao_impede_criacao(self):
        for indice in range(10):
            self._criar_sap_sem_fisico(f'VF{indice:02d}', Decimal('5'))

        ciclo = criar_ciclo()
        self.assertEqual(CicloInventarioSku.objects.filter(ciclo=ciclo).count(), 10)
        self.assertEqual(EstoqueFisico.objects.count(), 0)

    def test_fisico_parcial_nao_limita_ciclo(self):
        produtos_sap = [
            self._criar_sap_sem_fisico(f'FP{i:02d}', Decimal('1'))
            for i in range(10)
        ]
        EstoqueFisico.objects.create(
            posicao=self.posicao,
            produto=produtos_sap[0],
            quantidade=Decimal('1'),
            data_contagem=timezone.now(),
        )

        ciclo = criar_ciclo()
        self.assertEqual(CicloInventarioSku.objects.filter(ciclo=ciclo).count(), 10)

    def test_ciclo_1000_skus(self):
        produtos = []
        for indice in range(1000):
            produtos.append(Produto(
                codigo_produto=f'K{indice:04d}',
                descricao=f'Bulk {indice}',
                setor='A',
                embalagem='Un',
            ))
        Produto.objects.bulk_create(produtos)
        produtos_db = {
            p.codigo_produto: p
            for p in Produto.objects.filter(codigo_produto__startswith='K')
        }
        EstoqueSAP.objects.bulk_create([
            EstoqueSAP(
                produto=produtos_db[f'K{indice:04d}'],
                total=Decimal('1'),
                arquivo_origem='bulk.xlsx',
            )
            for indice in range(1000)
        ])

        ciclo = criar_ciclo()
        self.assertEqual(CicloInventarioSku.objects.filter(ciclo=ciclo).count(), 1000)

    def test_execucao_diaria_20_por_lote(self):
        from inventario.services.ciclico import (
            ConfiguracaoExecucao,
            StatusItemCiclico,
            gerar_lote_execucao,
        )

        for indice in range(45):
            self._criar_sap_sem_fisico(f'DIA{indice:02d}', Decimal('1'))

        criar_ciclo()
        session = {}

        lote_dia1 = gerar_lote_execucao(
            session,
            ConfiguracaoExecucao(quantidade_skus=20),
        )
        self.assertEqual(len(lote_dia1), 20)
        CicloInventarioSku.objects.filter(
            pk__in=[sku.pk for sku in lote_dia1],
        ).update(status_contagem=StatusItemCiclico.CONTADO)

        lote_dia2 = gerar_lote_execucao(
            session,
            ConfiguracaoExecucao(quantidade_skus=20),
        )
        self.assertEqual(len(lote_dia2), 20)

        CicloInventarioSku.objects.filter(
            pk__in=[sku.pk for sku in lote_dia2],
        ).update(status_contagem=StatusItemCiclico.CONTADO)

        lote_dia3 = gerar_lote_execucao(
            session,
            ConfiguracaoExecucao(quantidade_skus=20),
        )
        self.assertEqual(len(lote_dia3), 5)

        todos = obter_skus_ciclo(session=None, apenas_lote_diario=False)
        self.assertEqual(len(todos), 45)

    def test_dashboard_apos_criacao_sap(self):
        for indice in range(5):
            self._criar_sap_sem_fisico(f'DB{indice}', Decimal('1'))

        criar_ciclo()
        resumo = obter_resumo_ciclico()
        self.assertEqual(resumo.total_skus, 5)
        self.assertEqual(resumo.skus_pendentes, 5)
        self.assertEqual(resumo.skus_contados, 0)
        self.assertEqual(resumo.skus_divergentes, 0)
        self.assertEqual(resumo.percentual_executado, Decimal('0'))

    def test_sku_sem_fisico_recebe_posicao_generica(self):
        produto = self._criar_sap_sem_fisico('GEN01', Decimal('7'))
        ciclo = criar_ciclo()
        sku = CicloInventarioSku.objects.get(ciclo=ciclo, produto=produto)
        self.assertEqual(sku.posicoes.count(), 1)
        self.assertTrue(
            sku.posicoes.filter(codigo_posicao='CICLICO-SEM-POS').exists(),
        )


class CiclicoEdicaoContagemTestCase(CiclicoAuditoriaBaseMixin, ClienteAutenticadoMixin, TestCase):
    def setUp(self):
        limpar_estado_ciclico()
        self.user_operador, _ = criar_usuario_teste(
            username='op.edicao',
            perfil=Usuario.Perfil.INVENTARIO,
        )
        self.user_admin, _ = criar_usuario_teste(
            username='admin.edicao',
            perfil=Usuario.Perfil.ADMINISTRADOR,
        )
        self.user_outro, _ = criar_usuario_teste(
            username='op.outro',
            perfil=Usuario.Perfil.INVENTARIO,
        )
        self.posicao = Posicao.objects.create(codigo='EDIT-P01', posicao='1 1 1')
        self.produto = self._criar_par_sap_fisico(
            'EDIT001',
            self.posicao,
            Decimal('50'),
            Decimal('50'),
        )
        self.ciclo = criar_ciclo()
        self.sku = CicloInventarioSku.objects.get(ciclo=self.ciclo, produto=self.produto)
        self.item = self.sku.posicoes.get()

    def tearDown(self):
        limpar_estado_ciclico()

    def _contar_sku(self, usuario=None):
        salvar_contagem_sku(
            self.sku.pk,
            {self.item.pk: Decimal('50')},
            usuario or self.user_operador,
        )

    def test_editar_contagem_gera_historico_edicao(self):
        self._contar_sku()
        editar_contagem_ciclico(
            self.sku.pk,
            {self.item.pk: {
                'quantidade': Decimal('55'),
                'posicao_id': self.posicao.pk,
            }},
            'Erro de digitação',
            self.user_operador,
        )
        self.item.refresh_from_db()
        self.assertEqual(self.item.quantidade_fisica, Decimal('55'))
        registro = CicloAuditoriaHistorico.objects.get(
            ciclo_sku=self.sku,
            tipo=CicloAuditoriaHistorico.TipoRegistro.EDICAO,
        )
        self.assertIn('→ 55', registro.motivo)
        self.assertIn('Erro de digitação', registro.motivo)

    def test_editar_bloqueado_ciclo_encerrado(self):
        self._contar_sku()
        encerrar_ciclo()
        with self.assertRaises(CiclicoError) as ctx:
            editar_contagem_ciclico(
                self.sku.pk,
                {self.item.pk: {
                    'quantidade': Decimal('55'),
                    'posicao_id': self.posicao.pk,
                }},
                'Teste',
                self.user_operador,
            )
        self.assertEqual(str(ctx.exception), MSG_CICLO_ENCERRADO)

    def test_operador_nao_edita_contagem_de_outro(self):
        self._contar_sku()
        self.assertFalse(usuario_pode_editar_contagem_ciclico(self.user_outro, self.sku))
        with self.assertRaises(CiclicoError):
            editar_contagem_ciclico(
                self.sku.pk,
                {self.item.pk: {
                    'quantidade': Decimal('55'),
                    'posicao_id': self.posicao.pk,
                }},
                'Teste',
                self.user_outro,
            )

    def test_admin_edita_contagem_de_outro(self):
        self._contar_sku()
        self.assertTrue(usuario_pode_editar_contagem_ciclico(self.user_admin, self.sku))
        editar_contagem_ciclico(
            self.sku.pk,
            {self.item.pk: {
                'quantidade': Decimal('55'),
                'posicao_id': self.posicao.pk,
            }},
            'Correção supervisor',
            self.user_admin,
        )
        self.item.refresh_from_db()
        self.assertEqual(self.item.quantidade_fisica, Decimal('55'))

    def test_executar_exibe_icones_acoes(self):
        self.client.force_login(self.user_operador)
        self.client.post(reverse('ciclico_executar'), {
            'acao': 'gerar_lote',
            'quantidade_skus': '1',
        })
        self._contar_sku()
        response = self.client.get(reverse('ciclico_executar'))
        self.assertContains(response, 'bi-eye')
        self.assertContains(response, 'bi-pencil')
        self.assertContains(response, 'bi-trash')
        self.assertContains(response, 'Ações')

    def test_detalhe_exibe_historico_edicao(self):
        self._contar_sku()
        editar_contagem_ciclico(
            self.sku.pk,
            {self.item.pk: {
                'quantidade': Decimal('55'),
                'posicao_id': self.posicao.pk,
            }},
            'Erro de digitação',
            self.user_operador,
        )
        self.client.force_login(self.user_operador)
        response = self.client.get(
            reverse('ciclico_sku_detalhe', kwargs={'sku_id': self.sku.pk}),
        )
        self.assertContains(response, 'Histórico de contagens')
        self.assertContains(response, 'Editado por')
        self.assertContains(response, '→ 55')

    def test_post_editar_via_view(self):
        self._contar_sku()
        self.client.force_login(self.user_operador)
        response = self.client.post(
            reverse('ciclico_sku_editar', kwargs={'sku_id': self.sku.pk}),
            {
                f'quantidade_posicao_{self.item.pk}': '55',
                f'posicao_id_{self.item.pk}': str(self.posicao.pk),
                'motivo_edicao': 'Erro de digitação',
            },
        )
        self.assertRedirects(response, reverse('ciclico_executar'))
        self.item.refresh_from_db()
        self.assertEqual(self.item.quantidade_fisica, Decimal('55'))

    def test_get_editar_exibe_botoes_rodape(self):
        self._contar_sku()
        self.client.force_login(self.user_operador)
        response = self.client.get(
            reverse('ciclico_sku_editar', kwargs={'sku_id': self.sku.pk}),
        )
        self.assertContains(response, 'Salvar Alteração')
        self.assertContains(response, 'bi-check-lg')
        self.assertContains(response, 'modal-footer-acao')
        self.assertContains(response, 'Cancelar')

    def test_post_editar_ajax_retorna_dados_grid(self):
        self._contar_sku()
        self.client.force_login(self.user_operador)
        response = self.client.post(
            reverse('ciclico_sku_editar', kwargs={'sku_id': self.sku.pk}),
            {
                f'quantidade_posicao_{self.item.pk}': '55',
                f'posicao_id_{self.item.pk}': str(self.posicao.pk),
                'motivo_edicao': 'Erro de digitação',
            },
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(response.status_code, 200)
        dados = response.json()
        self.assertTrue(dados['ok'])
        self.assertEqual(dados['sku']['pk'], self.sku.pk)
        self.assertIn('55', dados['sku']['quantidade_fisica'])
        self.item.refresh_from_db()
        self.assertEqual(self.item.quantidade_fisica, Decimal('55'))
        self.assertTrue(
            CicloAuditoriaHistorico.objects.filter(
                ciclo_sku=self.sku,
                tipo=CicloAuditoriaHistorico.TipoRegistro.EDICAO,
            ).exists(),
        )

    def test_post_editar_ajax_rejeita_motivo_vazio(self):
        self._contar_sku()
        self.client.force_login(self.user_operador)
        response = self.client.post(
            reverse('ciclico_sku_editar', kwargs={'sku_id': self.sku.pk}),
            {
                f'quantidade_posicao_{self.item.pk}': '55',
                f'posicao_id_{self.item.pk}': str(self.posicao.pk),
                'motivo_edicao': '',
            },
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()['ok'])

    def test_post_editar_ajax_rejeita_quantidade_negativa(self):
        self._contar_sku()
        self.client.force_login(self.user_operador)
        response = self.client.post(
            reverse('ciclico_sku_editar', kwargs={'sku_id': self.sku.pk}),
            {
                f'quantidade_posicao_{self.item.pk}': '-1',
                f'posicao_id_{self.item.pk}': str(self.posicao.pk),
                'motivo_edicao': 'Erro de digitação',
            },
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('negativa', response.json()['message'].lower())

    def test_post_excluir_via_view(self):
        self.client.force_login(self.user_operador)
        response = self.client.post(
            reverse('ciclico_sku_excluir', kwargs={'sku_id': self.sku.pk}),
            {'motivo_exclusao': 'Produto obsoleto'},
        )
        self.assertRedirects(response, reverse('ciclico_executar'))
        self.sku.refresh_from_db()
        self.assertEqual(self.sku.status_contagem, StatusItemCiclico.EXCLUIDO)
