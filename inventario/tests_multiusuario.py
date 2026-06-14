"""Testes de concorrência, locks, tarefas e recontagem multiusuário."""

from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import Client, TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Usuario
from accounts.test_utils import criar_usuario_teste
from estoque_fisico.models import EstoqueFisico
from estoque_sap.models import EstoqueSAP
from inventario.models import (
    CicloInventario,
    CicloInventarioItem,
    CicloInventarioSku,
    Inventario,
    InventarioItem,
    InventarioLock,
    InventarioTarefa,
)
from inventario.models_operacional import InventarioAuditoriaEvento as EventoModel
from inventario.services.ciclico import criar_ciclo, limpar_estado_ciclico
from inventario.services.contagem import salvar_contagem
from inventario.services.locks import LockError, adquirir_lock, expirar_locks_abandonados
from inventario.services.recontagem_multiusuario import gerar_recontagem_terceiro_operador
from inventario.services.tarefas import (
    TarefaError,
    distribuir_tarefas_geral,
)
from inventario.tests_ciclico_auditoria import CiclicoAuditoriaBaseMixin
from posicoes.models import Posicao
from produtos.models import Produto


def _criar_op(username: str, perfil: str) -> User:
    user, _ = criar_usuario_teste(username=username, perfil=perfil)
    return user


class LocksMultiusuarioTestCase(CiclicoAuditoriaBaseMixin, TestCase):
    def setUp(self):
        self.supervisor = _criar_op('sup1', Usuario.Perfil.SUPERVISOR)
        self.op1 = _criar_op('op1', Usuario.Perfil.OPERADOR)
        self.op2 = _criar_op('op2', Usuario.Perfil.OPERADOR)
        self.posicao = Posicao.objects.create(codigo='L01', posicao='Loc 01', rua='A')
        self.produto = Produto.objects.create(
            codigo_produto='L100',
            descricao='Prod Lock',
            participa_ciclico=True,
        )
        self.inventario = Inventario.objects.create(
            usuario=Usuario.objects.get(login='sup1'),
            status=Inventario.Status.ABERTO,
        )

    def test_lock_impede_segundo_operador_na_mesma_posicao(self):
        adquirir_lock(
            tipo_inventario=InventarioLock.TipoInventario.GERAL,
            inventario=self.inventario,
            posicao=self.posicao,
            produto=self.produto,
            usuario=self.op1,
            session_key='sessao-op1',
        )
        with self.assertRaises(LockError) as ctx:
            adquirir_lock(
                tipo_inventario=InventarioLock.TipoInventario.GERAL,
                inventario=self.inventario,
                posicao=self.posicao,
                produto=self.produto,
                usuario=self.op2,
                session_key='sessao-op2',
            )
        self.assertIn('outro operador', str(ctx.exception).lower())

    def test_mesmo_operador_mesma_sessao_renova_lock(self):
        info1 = adquirir_lock(
            tipo_inventario=InventarioLock.TipoInventario.GERAL,
            inventario=self.inventario,
            posicao=self.posicao,
            produto=self.produto,
            usuario=self.op1,
            session_key='sessao-op1',
        )
        info2 = adquirir_lock(
            tipo_inventario=InventarioLock.TipoInventario.GERAL,
            inventario=self.inventario,
            posicao=self.posicao,
            produto=self.produto,
            usuario=self.op1,
            session_key='sessao-op1',
        )
        self.assertTrue(info2.renovado)
        self.assertEqual(info1.lock.pk, info2.lock.pk)

    @patch('inventario.services.locks.obter_timeout_segundos', return_value=1)
    def test_timeout_libera_lock_automaticamente(self, _mock_timeout):
        lock_info = adquirir_lock(
            tipo_inventario=InventarioLock.TipoInventario.GERAL,
            inventario=self.inventario,
            posicao=self.posicao,
            produto=self.produto,
            usuario=self.op1,
            session_key='sessao-op1',
        )
        lock_info.lock.expira_em = timezone.now() - timezone.timedelta(seconds=5)
        lock_info.lock.save(update_fields=['expira_em'])

        liberados = expirar_locks_abandonados()
        self.assertEqual(liberados, 1)
        lock_info.lock.refresh_from_db()
        self.assertFalse(lock_info.lock.ativo)
        self.assertTrue(
            EventoModel.objects.filter(
                evento=EventoModel.Evento.LOCK_TIMEOUT,
                lock=lock_info.lock,
            ).exists()
        )

    def test_auditoria_registra_lock_adquirido(self):
        adquirir_lock(
            tipo_inventario=InventarioLock.TipoInventario.GERAL,
            inventario=self.inventario,
            posicao=self.posicao,
            produto=self.produto,
            usuario=self.op1,
            dispositivo='Coletor-Test',
            session_key='sessao-op1',
        )
        self.assertTrue(
            EventoModel.objects.filter(
                evento=EventoModel.Evento.LOCK_ADQUIRIDO,
                dispositivo='Coletor-Test',
            ).exists()
        )


class TarefasMultiusuarioTestCase(CiclicoAuditoriaBaseMixin, TestCase):
    def setUp(self):
        self.supervisor = _criar_op('sup2', Usuario.Perfil.SUPERVISOR)
        self.op1 = _criar_op('op1b', Usuario.Perfil.OPERADOR)
        self.op2 = _criar_op('op2b', Usuario.Perfil.OPERADOR)
        self.pos1 = Posicao.objects.create(codigo='T01', posicao='T-01', rua='A')
        self.pos2 = Posicao.objects.create(codigo='T02', posicao='T-02', rua='A')
        self.inventario = Inventario.objects.create(
            usuario=Usuario.objects.get(login='sup2'),
            status=Inventario.Status.ABERTO,
        )

    def test_distribuicao_automatica_geral_por_operador(self):
        tarefas = distribuir_tarefas_geral(
            self.inventario,
            [self.op1, self.op2],
            modo=InventarioTarefa.ModoAtribuicao.AUTOMATICA,
            atribuido_por=self.supervisor,
        )
        self.assertEqual(len(tarefas), 2)
        operadores = {t.operador_id for t in tarefas}
        self.assertEqual(operadores, {self.op1.pk, self.op2.pk})

    def test_operador_nao_conta_posicao_nao_atribuida(self):
        distribuir_tarefas_geral(
            self.inventario,
            [self.op1, self.op2],
            atribuido_por=self.supervisor,
        )
        produto = Produto.objects.create(codigo_produto='TG01', descricao='Teste')
        with self.assertRaises(TarefaError):
            salvar_contagem(
                inventario=self.inventario,
                posicao=self.pos2,
                produto=produto,
                quantidade_fisica=Decimal('5'),
                usuario_contagem=self.op1,
                origem_contagem='POCKET',
                session_key='s1',
            )


class RecontagemTerceiroOperadorTestCase(CiclicoAuditoriaBaseMixin, TestCase):
    def setUp(self):
        limpar_estado_ciclico()
        self.supervisor = _criar_op('sup3', Usuario.Perfil.SUPERVISOR)
        self.op1 = _criar_op('rc1', Usuario.Perfil.OPERADOR)
        self.op2 = _criar_op('rc2', Usuario.Perfil.OPERADOR)
        self.op3 = _criar_op('rc3', Usuario.Perfil.OPERADOR)
        self.posicao = Posicao.objects.create(codigo='RC01', posicao='RC-01')
        self.produto = Produto.objects.create(
            codigo_produto='RC100',
            descricao='Prod Recontagem',
            participa_ciclico=True,
        )
        EstoqueSAP.objects.create(
            produto=self.produto,
            total=Decimal('100'),
            arquivo_origem='t.xlsx',
        )
        EstoqueFisico.objects.create(
            produto=self.produto,
            posicao=self.posicao,
            quantidade=Decimal('100'),
            data_contagem=timezone.now(),
        )
        criar_ciclo(usuario_criacao=self.supervisor)
        self.ciclo = CicloInventario.objects.get()
        self.sku = CicloInventarioSku.objects.get()
        self.item = CicloInventarioItem.objects.get(ciclo_sku=self.sku)

    def test_recontagem_atribuida_a_terceiro_operador(self):
        self.item.usuario_contagem = self.op1
        self.item.status_contagem = CicloInventarioItem.StatusContagem.DIVERGENTE
        self.item.save()

        tarefa = gerar_recontagem_terceiro_operador(
            self.item,
            gerado_por=self.supervisor,
            operadores_disponiveis=[self.op1, self.op2, self.op3],
        )
        self.assertNotIn(tarefa.operador_id, [self.op1.pk])
        self.assertIn(tarefa.operador_id, [self.op2.pk, self.op3.pk])
        self.assertTrue(tarefa.eh_recontagem)
        self.assertTrue(
            EventoModel.objects.filter(
                evento=EventoModel.Evento.RECONTAGEM_GERADA,
                tarefa=tarefa,
            ).exists()
        )

    def test_recontagem_falha_sem_operador_disponivel(self):
        self.item.usuario_contagem = self.op1
        self.item.usuario_recontagem = self.op2
        self.item.status_contagem = CicloInventarioItem.StatusContagem.DIVERGENTE
        self.item.save()

        with self.assertRaises(TarefaError):
            gerar_recontagem_terceiro_operador(
                self.item,
                operadores_disponiveis=[self.op1, self.op2],
            )


class ConcorrenciaCargaTestCase(TransactionTestCase):
    def setUp(self):
        self.supervisor = _criar_op('supc', Usuario.Perfil.SUPERVISOR)
        self.ops = [
            _criar_op(f'opc{i}', Usuario.Perfil.OPERADOR)
            for i in range(5)
        ]
        self.posicao = Posicao.objects.create(codigo='C01', posicao='C-01')
        self.produto = Produto.objects.create(codigo_produto='C100', descricao='Carga')
        self.inventario = Inventario.objects.create(
            usuario=Usuario.objects.get(login='supc'),
            status=Inventario.Status.ABERTO,
        )

    def test_apenas_um_lock_ativo_por_posicao(self):
        sucesso = 0
        bloqueados = 0
        for i, op in enumerate(self.ops):
            try:
                adquirir_lock(
                    tipo_inventario=InventarioLock.TipoInventario.GERAL,
                    inventario=self.inventario,
                    posicao=self.posicao,
                    produto=self.produto,
                    usuario=op,
                    session_key=f'sess-{i}',
                )
                sucesso += 1
            except LockError:
                bloqueados += 1

        self.assertEqual(sucesso, 1)
        self.assertEqual(bloqueados, 4)
        self.assertEqual(
            InventarioLock.objects.filter(ativo=True).count(),
            1,
        )


class MultiplosOperadoresPosicoesDistintasTestCase(CiclicoAuditoriaBaseMixin, TestCase):
    def setUp(self):
        self.supervisor = _criar_op('supm', Usuario.Perfil.SUPERVISOR)
        self.inventario = Inventario.objects.create(
            usuario=Usuario.objects.get(login='supm'),
            status=Inventario.Status.ABERTO,
        )
        self.produto = Produto.objects.create(
            codigo_produto='MULTI01',
            descricao='Prod Multi',
        )
        self.operadores = [_criar_op(f'mop{i}', Usuario.Perfil.OPERADOR) for i in range(10)]
        self.posicoes = [
            Posicao.objects.create(codigo=f'MP{i:02d}', posicao=f'Pos {i}')
            for i in range(10)
        ]

    def test_dez_operadores_posicoes_diferentes_simultaneo(self):
        sucesso = 0
        for i, op in enumerate(self.operadores):
            adquirir_lock(
                tipo_inventario=InventarioLock.TipoInventario.GERAL,
                inventario=self.inventario,
                posicao=self.posicoes[i],
                usuario=op,
                session_key=f'sess-{i}',
            )
            sucesso += 1
        self.assertEqual(sucesso, 10)
        self.assertEqual(InventarioLock.objects.filter(ativo=True).count(), 10)

    def test_dois_operadores_salvam_posicoes_diferentes(self):
        salvar_contagem(
            inventario=self.inventario,
            posicao=self.posicoes[0],
            produto=self.produto,
            quantidade_fisica=Decimal('3'),
            usuario_contagem=self.operadores[0],
            origem_contagem='POCKET',
            session_key='s0',
        )
        salvar_contagem(
            inventario=self.inventario,
            posicao=self.posicoes[1],
            produto=self.produto,
            quantidade_fisica=Decimal('7'),
            usuario_contagem=self.operadores[1],
            origem_contagem='POCKET',
            session_key='s1',
        )
        self.assertEqual(InventarioItem.objects.filter(inventario=self.inventario).count(), 2)


class PocketLockHttpTestCase(CiclicoAuditoriaBaseMixin, TestCase):
    def setUp(self):
        self.op1 = _criar_op('pock1', Usuario.Perfil.INVENTARIO)
        self.op2 = _criar_op('pock2', Usuario.Perfil.INVENTARIO)
        self.posicao = Posicao.objects.create(codigo='PLK01', posicao='Pocket Lock 01')
        self.produto = Produto.objects.create(
            codigo_produto='PLKPRD',
            descricao='Prod Pocket Lock',
            codigo_ean='7895556667777',
        )
        self.inventario = Inventario.objects.create(
            usuario=Usuario.objects.get(login='pock1'),
            status=Inventario.Status.EM_ANDAMENTO,
        )

    def _lock_posicao(self, client, codigo):
        return client.post(
            reverse('pocket:contagem', args=[self.inventario.pk]),
            {
                'acao': 'lock_posicao',
                'codigo_posicao': codigo,
            },
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )

    def test_pocket_lock_posicao_via_ajax(self):
        self.client.force_login(self.op1)
        response = self._lock_posicao(self.client, 'PLK01')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['ok'])
        self.assertTrue(
            InventarioLock.objects.filter(
                ativo=True,
                posicao=self.posicao,
                usuario=self.op1,
            ).exists()
        )

    def test_pocket_bloqueia_mesma_posicao_outro_operador(self):
        self.client.force_login(self.op1)
        self.assertEqual(self._lock_posicao(self.client, 'PLK01').status_code, 200)

        from django.test import Client
        client2 = Client()
        client2.force_login(self.op2)
        response = self._lock_posicao(client2, 'PLK01')
        self.assertEqual(response.status_code, 409)
        self.assertIn('outro operador', response.json()['message'].lower())

    def test_logout_libera_locks_ativos(self):
        self.client.force_login(self.op1)
        self._lock_posicao(self.client, 'PLK01')
        self.assertEqual(InventarioLock.objects.filter(ativo=True).count(), 1)

        self.client.post(reverse('accounts:logout'))
        self.assertEqual(InventarioLock.objects.filter(ativo=True).count(), 0)

    def test_pocket_ajax_salva_apos_lock_mesmo_operador(self):
        self.client.force_login(self.op1)
        self.assertTrue(self._lock_posicao(self.client, 'PLK01').json()['ok'])
        response = self.client.post(
            reverse('pocket:contagem', args=[self.inventario.pk]),
            {
                'codigo_posicao': 'PLK01',
                'codigo_produto': 'PLKPRD',
                'quantidade_fisica': '50',
            },
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['ok'])
        self.assertEqual(InventarioItem.objects.count(), 1)

    def test_pocket_bloqueia_produto_duplicado_mesma_posicao(self):
        self.client.force_login(self.op1)
        url = reverse('pocket:contagem', args=[self.inventario.pk])
        ajax = {'HTTP_X_REQUESTED_WITH': 'XMLHttpRequest'}
        payload = {
            'codigo_posicao': 'PLK01',
            'codigo_produto': 'PLKPRD',
            'quantidade_fisica': '10',
        }
        self.assertTrue(self.client.post(url, payload, **ajax).json()['ok'])
        bloqueio = self.client.post(url, {**payload, 'quantidade_fisica': '5'}, **ajax)
        self.assertEqual(bloqueio.status_code, 400)
        self.assertIn('já inventariado', bloqueio.json()['message'].lower())
        self.assertEqual(InventarioItem.objects.count(), 1)
