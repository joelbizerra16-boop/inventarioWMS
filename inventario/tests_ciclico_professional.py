import time
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Usuario
from accounts.test_utils import ClienteAutenticadoMixin, criar_usuario_teste
from estoque_fisico.models import EstoqueFisico
from estoque_sap.models import EstoqueSAP
from inventario.models import (
    CicloAuditoriaHistorico,
    CicloInventarioSku,
)
from inventario.services.ciclico import (
    ConfiguracaoExecucao,
    IndicadorConfronto,
    StatusItemCiclico,
    criar_ciclo,
    excluir_sku_do_ciclo,
    gerar_lote_execucao,
    limpar_estado_ciclico,
    obter_resumo_ciclico,
    obter_skus_ciclo,
    salvar_contagem_sku,
)
from inventario.tests_ciclico_auditoria import CiclicoAuditoriaBaseMixin
from posicoes.models import Posicao
from produtos.models import Produto


class CiclicoProfissionalBaseMixin:
    def _sap(
        self,
        codigo: str,
        embalagem: str,
        total: Decimal,
        cosan: Decimal | None = None,
        brida: Decimal | None = None,
        participa: bool = True,
    ) -> Produto:
        produto = Produto.objects.create(
            codigo_produto=codigo,
            descricao=f'Produto {codigo}',
            setor='A',
            embalagem=embalagem,
            participa_ciclico=participa,
        )
        EstoqueSAP.objects.create(
            produto=produto,
            total=total,
            canal_110=cosan if cosan is not None else total,
            canal_1=brida if brida is not None else total,
            arquivo_origem='teste.xlsx',
        )
        return produto


class CiclicoProfissionalTestCase(
    CiclicoProfissionalBaseMixin,
    CiclicoAuditoriaBaseMixin,
    ClienteAutenticadoMixin,
    TestCase,
):
    def setUp(self):
        self.user = self.autenticar_cliente()
        limpar_estado_ciclico()

    def tearDown(self):
        limpar_estado_ciclico()

    def test_criar_ciclo_congela_todos_sem_filtro(self):
        self._sap('A1', 'Bombona', Decimal('10'))
        self._sap('A2', 'Tambor', Decimal('10'))
        ciclo = criar_ciclo()
        self.assertEqual(CicloInventarioSku.objects.filter(ciclo=ciclo).count(), 2)

    def test_gerar_lote_respeita_embalagem(self):
        for indice in range(12):
            self._sap(f'B{indice:02d}', 'Bombona', Decimal('10'))
        for indice in range(8):
            self._sap(f'T{indice:02d}', 'Tambor', Decimal('10'))
        criar_ciclo()
        lote = gerar_lote_execucao(
            self.client.session,
            ConfiguracaoExecucao(
                embalagens=['Bombona'],
                quantidade_skus=20,
                respeitar_somente_embalagens=True,
            ),
        )
        self.assertEqual(len(lote), 12)
        self.assertTrue(all(sku.embalagem == 'Bombona' for sku in lote))

    def test_gerar_lote_completa_com_outras_embalagens(self):
        for indice in range(12):
            self._sap(f'B{indice:02d}', 'Bombona', Decimal('10'))
        for indice in range(8):
            self._sap(f'T{indice:02d}', 'Tambor', Decimal('10'))
        criar_ciclo()
        lote = gerar_lote_execucao(
            self.client.session,
            ConfiguracaoExecucao(
                embalagens=['Bombona'],
                quantidade_skus=20,
            ),
        )
        self.assertEqual(len(lote), 20)
        bombonas = sum(1 for sku in lote if sku.embalagem == 'Bombona')
        self.assertEqual(bombonas, 12)

    def test_gerar_lote_filtro_canal(self):
        self._sap('C1', 'Bombona', Decimal('10'), Decimal('10'), Decimal('0'))
        self._sap('C2', 'Bombona', Decimal('10'), Decimal('0'), Decimal('10'))
        criar_ciclo()
        lote = gerar_lote_execucao(
            self.client.session,
            ConfiguracaoExecucao(canal='cosan', quantidade_skus=10),
        )
        self.assertEqual(len(lote), 1)
        self.assertEqual(lote[0].codigo_produto, 'C1')

    def test_view_criar_ciclo_um_clique(self):
        self._sap('V1', 'Balde', Decimal('10'))
        response = self.client.post(reverse('ciclico'), {'acao': 'criar'})
        self.assertRedirects(response, reverse('ciclico'))
        self.assertEqual(CicloInventarioSku.objects.count(), 1)

    def test_view_gerar_lote_executar(self):
        for indice in range(5):
            self._sap(f'X{indice}', 'IBC', Decimal('10'))
        self.client.post(reverse('ciclico'), {'acao': 'criar'})
        response = self.client.post(reverse('ciclico_executar'), {
            'acao': 'gerar_lote',
            'quantidade_skus': '3',
        })
        self.assertRedirects(response, reverse('ciclico_executar'))
        skus = obter_skus_ciclo(session=self.client.session)
        self.assertEqual(len(skus), 3)


class CiclicoExclusaoTestCase(CiclicoProfissionalBaseMixin, TestCase):
    def setUp(self):
        limpar_estado_ciclico()
        self.user, _ = criar_usuario_teste(perfil=Usuario.Perfil.INVENTARIO)
        posicao = Posicao.objects.create(codigo='EX01', posicao='E-01')
        produto = self._sap('EX01', 'Bombona', Decimal('5'))
        EstoqueFisico.objects.create(
            posicao=posicao,
            produto=produto,
            quantidade=Decimal('5'),
            data_contagem=timezone.now(),
        )
        criar_ciclo()
        self.sku = CicloInventarioSku.objects.get()

    def tearDown(self):
        limpar_estado_ciclico()

    def test_exclusao_permanente_produto(self):
        Produto.objects.filter(pk=self.sku.produto_id).update(participa_ciclico=False)
        limpar_estado_ciclico()
        self._sap('NOVO', 'Bombona', Decimal('1'))
        criar_ciclo()
        self.assertFalse(
            CicloInventarioSku.objects.filter(codigo_produto='EX01').exists(),
        )

    def test_exclusao_dentro_ciclo_preserva_historico(self):
        excluir_sku_do_ciclo(self.sku.pk, 'Brinde promocional', self.user)
        self.sku.refresh_from_db()
        self.assertEqual(self.sku.status_contagem, StatusItemCiclico.EXCLUIDO)
        self.assertEqual(CicloAuditoriaHistorico.objects.filter(
            ciclo_sku=self.sku,
            tipo='EXCLUSAO',
        ).count(), 1)
        resumo = obter_resumo_ciclico()
        self.assertEqual(resumo.skus_excluidos, 1)
        self.assertEqual(resumo.total_skus, 0)


class CiclicoCongelamentoCanaisTestCase(CiclicoProfissionalBaseMixin, TestCase):
    def setUp(self):
        limpar_estado_ciclico()
        self._sap('CH01', 'Bombona', Decimal('100'), Decimal('90'), Decimal('80'))

    def tearDown(self):
        limpar_estado_ciclico()

    def test_congela_canais_na_criacao(self):
        criar_ciclo()
        sku = CicloInventarioSku.objects.get()
        self.assertEqual(sku.quantidade_cosan, Decimal('90'))
        self.assertEqual(sku.quantidade_brida, Decimal('80'))

        EstoqueSAP.objects.filter(produto=sku.produto).update(
            canal_110=Decimal('999'),
            canal_1=Decimal('888'),
        )
        sku.refresh_from_db()
        self.assertEqual(sku.quantidade_cosan, Decimal('90'))


class CiclicoIndicadorSapTestCase(CiclicoProfissionalBaseMixin, TestCase):
    def setUp(self):
        limpar_estado_ciclico()
        self.user, _ = criar_usuario_teste(perfil=Usuario.Perfil.INVENTARIO)
        posicao = Posicao.objects.create(codigo='IND1', posicao='I-1')
        produto = self._sap('IND1', 'Tambor', Decimal('100'))
        EstoqueFisico.objects.create(
            posicao=posicao,
            produto=produto,
            quantidade=Decimal('100'),
            data_contagem=timezone.now(),
        )
        criar_ciclo()
        self.sku = CicloInventarioSku.objects.get()

    def tearDown(self):
        limpar_estado_ciclico()

    def test_indicador_verde_sap(self):
        salvar_contagem_sku(
            self.sku.pk,
            {self.sku.posicoes.get().pk: Decimal('100')},
            self.user,
        )
        dto = obter_skus_ciclo(apenas_lote_diario=False)[0]
        self.assertEqual(dto.indicador_sap, IndicadorConfronto.VERDE)

    def test_indicador_laranja_sap(self):
        salvar_contagem_sku(
            self.sku.pk,
            {self.sku.posicoes.get().pk: Decimal('105')},
            self.user,
        )
        dto = obter_skus_ciclo(apenas_lote_diario=False)[0]
        self.assertEqual(dto.indicador_sap, IndicadorConfronto.LARANJA)


class CiclicoPerformanceTestCase(CiclicoProfissionalBaseMixin, TestCase):
    def setUp(self):
        limpar_estado_ciclico()

    def tearDown(self):
        limpar_estado_ciclico()

    def test_ciclo_5000_skus(self):
        produtos = [
            Produto(
                codigo_produto=f'K{i:05d}',
                descricao=f'Bulk {i}',
                setor='A',
                embalagem='Bombona',
                participa_ciclico=True,
            )
            for i in range(5000)
        ]
        Produto.objects.bulk_create(produtos)
        produtos_db = {
            p.codigo_produto: p
            for p in Produto.objects.filter(codigo_produto__startswith='K')
        }
        EstoqueSAP.objects.bulk_create([
            EstoqueSAP(
                produto=produtos_db[f'K{i:05d}'],
                total=Decimal('1'),
                canal_1=Decimal('1'),
                canal_110=Decimal('1'),
                arquivo_origem='bulk.xlsx',
            )
            for i in range(5000)
        ])
        inicio = time.perf_counter()
        criar_ciclo()
        duracao = time.perf_counter() - inicio
        self.assertEqual(CicloInventarioSku.objects.count(), 5000)
        self.assertLess(duracao, 60.0)
