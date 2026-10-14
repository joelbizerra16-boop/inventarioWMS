from datetime import datetime, timezone as dt_timezone
from decimal import Decimal as D

from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from accounts.models import Usuario
from accounts.test_utils import criar_usuario_teste
from dashboard.services.painel_inventario import comparativo_padrao, montar_painel
from estoque_sap.models import EstoqueSAP
from inventario.models import Inventario, InventarioItem
from inventario.models_operacional import InventarioTarefa
from inventario.services.comparativo_contagens import (
    FAIXA_AMARELA,
    FAIXA_LARANJA,
    FAIXA_NEUTRA,
    FAIXA_VERDE,
    FAIXA_VERMELHA,
    faixa_acuracidade,
    finalizar_comparativo,
    formatar_percentual,
    salvar_comparativo,
)
from posicoes.models import Posicao
from produtos.models import Produto


class FaixasTestCase(SimpleTestCase):
    def test_limites_pelo_valor_exato(self):
        casos = [
            ('0', FAIXA_VERMELHA), ('60', FAIXA_VERMELHA), ('60.01', FAIXA_LARANJA),
            ('90', FAIXA_LARANJA), ('90.01', FAIXA_AMARELA), ('97', FAIXA_AMARELA),
            ('97.99', FAIXA_AMARELA), ('98', FAIXA_VERDE), ('100', FAIXA_VERDE),
        ]
        for valor, faixa in casos:
            with self.subTest(valor=valor):
                self.assertEqual(faixa_acuracidade(D(valor)), faixa)
        self.assertEqual(faixa_acuracidade(None), FAIXA_NEUTRA)
        self.assertEqual(formatar_percentual(D('97.99')), '98,0%')  # cor segue o valor exato
        self.assertEqual(formatar_percentual(None), '—')


class PainelBaseTestCase(TestCase):
    """5 SKUs elegíveis: A e B confirmados com SAP; C só uma igual; D dupla != SAP; E sem contagem."""

    def setUp(self):
        self.user, self.operacional = criar_usuario_teste()
        self.client.force_login(self.user)
        self.ana, op = criar_usuario_teste('ana.pn', perfil=Usuario.Perfil.INVENTARIO)
        op.nome = 'Ana'
        op.save(update_fields=['nome'])
        self.bruno, op = criar_usuario_teste('bruno.pn', perfil=Usuario.Perfil.INVENTARIO)
        op.nome = 'Bruno'
        op.save(update_fields=['nome'])
        self.inv1 = Inventario.objects.create(usuario=self.operacional, status=Inventario.Status.FINALIZADO)
        self.inv2 = Inventario.objects.create(usuario=self.operacional, status=Inventario.Status.EM_ANDAMENTO)
        self.pos = {c: Posicao.objects.create(codigo=c, posicao=f'01.0{i}.101') for i, c in enumerate(('P1', 'P2', 'P3', 'P4'), 1)}
        self.p = {}
        for codigo, cosan in (('A', 10), ('B', 20), ('C', 30), ('D', 40), ('E', 50)):
            self.p[codigo] = Produto.objects.create(codigo_produto=codigo, descricao=f'Produto {codigo}', setor='A')
            EstoqueSAP.objects.create(
                produto=self.p[codigo], canal_110=D(cosan), canal_1=D(0), total=D(cosan), arquivo_origem='sap.xlsx',
            )
        self.lancar(self.inv1, 'P1', 'A', '10', self.ana, 9)
        self.lancar(self.inv2, 'P1', 'A', '10', self.bruno, 10)
        self.lancar(self.inv1, 'P2', 'B', '20', self.ana, 9)
        self.lancar(self.inv2, 'P2', 'B', '20', self.bruno, 11)
        self.lancar(self.inv1, 'P3', 'C', '30', self.ana, 9)
        self.lancar(self.inv2, 'P3', 'C', '31', self.bruno, 11)
        self.lancar(self.inv1, 'P4', 'D', '5', self.ana, 10)
        self.lancar(self.inv2, 'P4', 'D', '5', self.bruno, 12)
        self.comparativo = salvar_comparativo(
            comparativo=None, nome='Painel', inventarios={1: self.inv1, 2: self.inv2, 3: None},
            referencia_opcao='nova', estado=None, usuario=self.user,
        )

    def lancar(self, inventario, posicao, codigo, quantidade, usuario, hora):
        momento = datetime(2026, 10, 14, hora + 3, 0, tzinfo=dt_timezone.utc)  # hora de Brasília
        return InventarioItem.objects.create(
            inventario=inventario, posicao=self.pos[posicao], produto=self.p[codigo],
            quantidade_fisica=D(quantidade), usuario_contagem=usuario, data_contagem=momento,
            usuario_registro=usuario, data_registro=momento,
        )


class PainelServicoTestCase(PainelBaseTestCase):
    def test_cenario_obrigatorio(self):
        painel = montar_painel(self.comparativo, 'cosan')
        ind = painel.indicador_sku
        self.assertEqual((ind.numerador, ind.denominador, ind.auxiliar), (2, 5, 4))
        self.assertEqual(ind.percentual_texto, '40,0%')
        self.assertEqual(ind.faixa, FAIXA_VERMELHA)
        self.assertEqual(ind.linhas, ['2 de 5 SKUs conformes com SAP', '4 de 5 SKUs com alguma contagem'])
        self.assertTrue(painel.provisorio)
        self.assertEqual(painel.totais, {
            'skus': 5, 'posicoes': 4, 'posicoes_rotulo': 'Posições com registros',
            'operadores': 2, 'lancamentos': 8,
        })

    def test_situacao_fecha_o_total(self):
        situacao = {f.chave: f.quantidade for f in montar_painel(self.comparativo, 'cosan').situacao}
        # A,B confirmados; C (laranja) e D (dupla != SAP) em divergência; E sem contagem.
        self.assertEqual(situacao, {'confirmado': 2, 'divergencia': 2, 'aguardando': 0, 'sem_contagem': 1})

    def test_concordancia_por_posicao_e_planejadas(self):
        painel = montar_painel(self.comparativo, 'cosan')
        self.assertEqual((painel.indicador_posicao.numerador, painel.indicador_posicao.denominador), (3, 4))
        self.assertEqual(painel.indicador_posicao.faixa, FAIXA_LARANJA)
        planejada = Posicao.objects.create(codigo='P9', posicao='01.09.101')
        InventarioTarefa.objects.create(
            tipo_inventario=InventarioTarefa.TipoInventario.GERAL, inventario=self.inv1, posicao=planejada, operador=self.ana,
        )
        painel = montar_painel(self.comparativo, 'cosan')
        self.assertEqual((painel.indicador_posicao.numerador, painel.indicador_posicao.denominador), (3, 5))
        # Concordância física não muda com o canal SAP.
        self.assertEqual(
            montar_painel(self.comparativo, 'brida').indicador_posicao.percentual,
            painel.indicador_posicao.percentual,
        )

    def test_avanco_c3_so_nos_que_exigem_desempate(self):
        avanco = {a.rotulo: a for a in montar_painel(self.comparativo, 'cosan').avanco}
        self.assertEqual((avanco['C1'].numerador, avanco['C1'].denominador), (4, 5))
        self.assertEqual((avanco['C2'].numerador, avanco['C2'].denominador), (4, 5))
        self.assertEqual((avanco['C3'].numerador, avanco['C3'].denominador), (0, 1))  # só C (30 x 31)
        self.assertFalse(avanco['C3'].associada)

    def test_ranking_por_operador_e_etapa(self):
        painel = montar_painel(self.comparativo, 'cosan')
        self.assertEqual(
            [(r.operador, r.etapa, r.quantidade) for r in painel.ranking],
            [('Ana', 'C1', 4), ('Bruno', 'C2', 4)],
        )
        self.assertEqual(painel.total_lancamentos, 8)

    def test_evolucao_acumulada(self):
        ev = montar_painel(self.comparativo, 'cosan').evolucao
        self.assertTrue(ev.disponivel)
        finais = {s.chave: s.final for s in ev.series}
        self.assertEqual(finais, {'contagem': 4, 'confirmado': 2})

    def test_cobertura(self):
        barras = {b['chave']: b['quantidade'] for b in montar_painel(self.comparativo, 'cosan').cobertura_barras}
        self.assertEqual(barras, {'sap': 5, 'fisico': 4, 'ambas': 4, 'so_sap': 1, 'so_fisico': 0})

    def test_maiores_divergencias(self):
        painel = montar_painel(self.comparativo, 'cosan')
        # Contra SAP: só SKUs com consenso; D (5 x 40) = -35.
        self.assertEqual(
            [(d.codigo_produto, d.fisico, d.contabil, d.diferenca, d.status_label) for d in painel.divergencias_sap],
            [('D', D(5), D(40), D(-35), 'Déficit físico')],
        )
        self.assertEqual([d.codigo_produto for d in painel.divergencias_contagens], ['C'])
        self.assertEqual(painel.divergencias_contagens[0].amplitude, D(1))

    def test_sap_indisponivel_e_sem_conferencia(self):
        from inventario.models_comparativo import ReferenciaSAP
        ReferenciaSAP.objects.filter(pk=self.comparativo.referencia_sap_id).update(canais_carregados=['canal_110'])
        self.comparativo.refresh_from_db()
        painel = montar_painel(self.comparativo, 'ambas')
        self.assertFalse(painel.indicador_sku.disponivel)
        self.assertEqual(painel.indicador_sku.percentual_texto, '—')
        self.assertEqual(painel.divergencias_sap, [])
        vazio = montar_painel(None, 'ambas')
        self.assertIsNone(vazio.indicador_sku)

    def test_conferencia_padrao_prefere_aberta(self):
        outra = salvar_comparativo(
            comparativo=None, nome='Posterior', inventarios={1: self.inv2, 2: None, 3: None},
            referencia_opcao='', estado=None, usuario=self.user,
        )
        self.assertEqual(comparativo_padrao().pk, outra.pk)
        finalizar_comparativo(outra, self.user)
        self.assertEqual(comparativo_padrao().pk, self.comparativo.pk)


class PainelTelaTestCase(PainelBaseTestCase):
    def test_tela_inicial_e_o_painel(self):
        html = self.client.get(reverse('home') + '?canal=cosan').content.decode()
        self.assertIn('Painel geral do inventário', html)
        self.assertIn('40,0%', html)
        self.assertIn('2 de 5 SKUs conformes com SAP', html)
        self.assertIn('4 de 5 SKUs com alguma contagem', html)
        self.assertIn('cmp-faixa-vermelho', html)
        self.assertIn('Resultado provisório', html)
        self.assertIn('Maiores Divergências do Inventário', html)
        self.assertIn('Contra SAP', html)
        self.assertIn('Entre contagens', html)
        self.assertIn('Último inventário finalizado', html)
        self.assertIn('C1 #', html)
        self.assertIn(reverse('dashboard_classico'), html)
        self.assertIn('role="img"', html)
        # Coordenadas SVG sempre com ponto decimal (pt-BR não pode quebrar o desenho).
        self.assertNotRegex(html, r'(cx|cy|x|y|x1|y1|x2|y2)="\d+,\d')

    def test_seletor_de_conferencia_e_canal(self):
        resposta = self.client.get(reverse('home'), {'conferencia': self.comparativo.pk, 'canal': 'ambas'})
        self.assertEqual(resposta.context['comparativo'].pk, self.comparativo.pk)
        self.assertEqual(resposta.context['canal'], 'ambas')

    def test_sem_conferencia_mostra_estado_vazio_e_divergencias_atuais(self):
        from inventario.models_comparativo import ComparativoContagem
        ComparativoContagem.objects.all().delete()
        html = self.client.get(reverse('home')).content.decode()
        self.assertIn('Nenhuma conferência para analisar', html)
        self.assertIn('Último inventário finalizado', html)

    def test_dashboard_anterior_continua_disponivel(self):
        self.assertEqual(self.client.get(reverse('dashboard_classico')).status_code, 200)

    def test_operador_pocket_nao_acessa(self):
        operador, _ = criar_usuario_teste('op.pn', perfil=Usuario.Perfil.OPERADOR)
        self.client.force_login(operador)
        resposta = self.client.get(reverse('home'))
        self.assertEqual(resposta.status_code, 302)
