from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Usuario
from accounts.test_utils import ClienteAutenticadoMixin
from estoque_sap.models import EstoqueSAP
from inventario.models import CicloInventario
from inventario.services.ciclico import (
    StatusCiclo,
    criar_ciclo,
    encerrar_ciclo,
    limpar_estado_ciclico,
    reabrir_ciclo,
)
from inventario.services.ciclico_historico import (
    StatusHistoricoCiclo,
    listar_historico_ciclos,
    obter_detalhe_historico_ciclo,
    obter_status_exibicao_ciclo,
)
from inventario.tests_ciclico_auditoria import CiclicoAuditoriaBaseMixin
from produtos.models import Produto


class CiclicoHistoricoTestCase(CiclicoAuditoriaBaseMixin, ClienteAutenticadoMixin, TestCase):
    def setUp(self):
        self.user = self.autenticar_cliente(perfil=Usuario.Perfil.INVENTARIO)
        limpar_estado_ciclico()
        self.produto = Produto.objects.create(
            codigo_produto='HIST01',
            descricao='Produto Histórico',
            participa_ciclico=True,
        )
        EstoqueSAP.objects.create(
            produto=self.produto,
            total=Decimal('10'),
            arquivo_origem='teste.xlsx',
        )

    def tearDown(self):
        limpar_estado_ciclico()

    def test_lista_historico_exibe_ciclo_encerrado(self):
        ciclo = criar_ciclo(usuario_criacao=self.user)
        encerrar_ciclo()
        response = self.client.get(reverse('ciclico_historico'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'#{ciclo.pk}')
        self.assertContains(response, 'Encerrado')
        self.assertContains(response, 'Histórico — Inventário Cíclico')

    def test_detalhe_historico_exibe_indicadores(self):
        ciclo = criar_ciclo(usuario_criacao=self.user)
        encerrar_ciclo()
        response = self.client.get(reverse('ciclico_historico_detalhe', args=[ciclo.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Planejados')
        self.assertContains(response, 'Acuracidade')

    def test_auditoria_historico_exibe_secoes(self):
        ciclo = criar_ciclo(usuario_criacao=self.user)
        encerrar_ciclo()
        response = self.client.get(reverse('ciclico_historico_auditoria', args=[ciclo.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Criação do ciclo')
        self.assertContains(response, 'Encerramento')

    def test_status_reaberto_apos_reabrir_ciclo(self):
        ciclo = criar_ciclo(usuario_criacao=self.user)
        encerrar_ciclo()
        reabrir_ciclo(ciclo.pk)
        ciclo.refresh_from_db()
        codigo, rotulo = obter_status_exibicao_ciclo(ciclo)
        self.assertEqual(codigo, StatusHistoricoCiclo.REABERTO)
        self.assertEqual(rotulo, 'Reaberto')

        linhas = listar_historico_ciclos(status_filtro=StatusHistoricoCiclo.REABERTO)
        self.assertEqual(len(linhas), 1)
        self.assertEqual(linhas[0].pk, ciclo.pk)

    def test_status_cancelado_para_arquivado(self):
        ciclo = criar_ciclo(usuario_criacao=self.user)
        encerrar_ciclo()
        ciclo.status_ciclo = StatusCiclo.ARQUIVADO
        ciclo.save(update_fields=['status_ciclo'])
        codigo, rotulo = obter_status_exibicao_ciclo(ciclo)
        self.assertEqual(codigo, StatusHistoricoCiclo.CANCELADO)
        self.assertEqual(rotulo, 'Cancelado')

    def test_obter_detalhe_retorna_none_para_inexistente(self):
        self.assertIsNone(obter_detalhe_historico_ciclo(99999))
