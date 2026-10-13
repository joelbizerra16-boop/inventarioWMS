from datetime import datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Usuario
from accounts.test_utils import ClienteAutenticadoMixin, criar_usuario_teste
from inventario.models import Inventario, InventarioItem
from inventario.models_operacional import InventarioAuditoriaEvento
from inventario.services.consulta_pocket import consultar_contagens_pocket
from inventario.services.pocket import chave_posicao_pocket
from posicoes.models import Posicao
from produtos.models import Produto


class ConsultasPocketTestCase(ClienteAutenticadoMixin, TestCase):
    def setUp(self):
        self.autenticar_cliente()
        self.usuario = Usuario.objects.get(login='admin.teste')
        self.auth_user = self.usuario.user
        self.outro_user, self.outro_operacional = criar_usuario_teste(
            username='op.consulta',
            perfil=Usuario.Perfil.INVENTARIO,
        )
        self.momento = timezone.make_aware(datetime(2026, 10, 13, 16, 5))
        self.produto = Produto.objects.create(
            codigo_produto='00123',
            descricao='Parafuso',
            setor='A',
            codigo_ean='7890000001234',
        )
        self.produto_b = Produto.objects.create(
            codigo_produto='00999',
            descricao='Porca',
            setor='A',
        )
        self.posicao_a = Posicao.objects.create(codigo='0007', posicao='RUA-01')
        self.posicao_b = Posicao.objects.create(codigo='0008', posicao='RUA-02')
        self.inventario = Inventario.objects.create(
            usuario=self.usuario,
            status=Inventario.Status.EM_ANDAMENTO,
        )
        self.outro_inventario = Inventario.objects.create(
            usuario=self.usuario,
            status=Inventario.Status.EM_ANDAMENTO,
        )
        self.item_a = self._item(
            self.inventario, self.posicao_a, self.produto, Decimal('4.000'), self.auth_user,
        )
        self.item_b = self._item(
            self.inventario, self.posicao_b, self.produto, Decimal('6.000'), self.outro_user,
        )
        self._item(
            self.inventario, self.posicao_a, self.produto_b, Decimal('3.000'), self.auth_user,
        )
        self._item(
            self.outro_inventario, self.posicao_a, self.produto, Decimal('99.000'), self.auth_user,
        )

    def _item(self, inventario, posicao, produto, quantidade, usuario):
        return InventarioItem.objects.create(
            inventario=inventario,
            posicao=posicao,
            produto=produto,
            quantidade_fisica=quantidade,
            usuario_contagem=usuario,
            data_contagem=self.momento,
            origem_contagem=InventarioItem.OrigemContagem.POCKET,
        )

    def _url(self, inventario=None):
        inventario = inventario or self.inventario
        return reverse('pocket:consultas_inventario', args=[inventario.pk])

    def _auditoria(self, evento, quantidade, produto=None, posicao=None):
        InventarioAuditoriaEvento.objects.create(
            tipo_inventario=InventarioAuditoriaEvento.TipoInventario.GERAL,
            inventario=self.inventario,
            evento=evento,
            produto=produto or self.produto,
            posicao=posicao or self.posicao_a,
            quantidade=quantidade,
            usuario=self.auth_user,
            data_hora=timezone.now(),
        )

    def test_menu_e_tela_somente_leitura(self):
        contagem = self.client.get(reverse('pocket:contagem', args=[self.inventario.pk]))
        self.assertContains(contagem, 'Consultas')
        self.assertContains(contagem, reverse('pocket:consultas_inventario', args=[self.inventario.pk]))
        self.assertContains(contagem, 'Pré-cadastro de posição')
        self.assertContains(contagem, 'Pré-cadastro de produto')
        self.assertContains(contagem, 'id="pocket-contagem-form"')

        tela = self.client.get(self._url())
        self.assertContains(tela, 'Consultas')
        self.assertContains(tela, f'Inventário #{self.inventario.pk}')
        self.assertContains(tela, 'SKU / Posição')
        self.assertContains(tela, '>Consultar<')
        self.assertContains(tela, reverse('pocket:contagem', args=[self.inventario.pk]))
        self.assertNotContains(tela, 'inputmode=')
        self.assertNotContains(tela, 'pocket-cadastro.js')
        self.assertNotContains(tela, 'pocket-bipagem.js')
        html = tela.content.decode()
        self.assertIn('autocomplete="off"', html)
        self.assertNotIn('Salvar', html)

    def test_sku_em_varias_posicoes_nao_atribui_total_a_um_operador(self):
        resposta = self.client.get(self._url(), {'tipo': 'sku', 'q': '00123'})
        self.assertEqual(resposta.status_code, 200)
        linhas = resposta.context['linhas']
        self.assertEqual(
            [(linha.codigo_posicao, linha.quantidade, linha.operador) for linha in linhas],
            [
                ('0007', Decimal('4.000'), 'Usuário admin.teste'),
                ('0008', Decimal('6.000'), 'Usuário op.consulta'),
            ],
        )
        self.assertContains(resposta, '00123')
        self.assertContains(resposta, 'Parafuso')
        self.assertNotContains(resposta, '00999')
        self.assertNotContains(resposta, '99')
        self.assertEqual(resposta.content.decode().count('pocket-consulta-card'), 2)

    def test_posicao_lista_skus_daquela_posicao(self):
        resposta = self.client.get(self._url(), {'tipo': 'posicao', 'q': '0007'})
        codigos = [linha.codigo_produto for linha in resposta.context['linhas']]
        self.assertEqual(codigos, ['00123', '00999'])
        self.assertTrue(all(linha.codigo_posicao == '0007' for linha in resposta.context['linhas']))
        self.assertContains(resposta, '0007')

    def test_codigo_inexistente_campo_vazio_e_zeros_a_esquerda(self):
        inexistente = self.client.get(self._url(), {'tipo': 'sku', 'q': '00000'})
        self.assertContains(inexistente, 'Nenhuma contagem encontrada para esta pesquisa.')

        vazio = self.client.get(self._url(), {'tipo': 'sku', 'q': '   '})
        self.assertContains(vazio, 'Informe o código para consultar.')
        self.assertNotContains(vazio, 'Nenhuma contagem encontrada para esta pesquisa.')
        self.assertNotContains(vazio, '00123')

        sem_busca = self.client.get(self._url())
        self.assertNotContains(sem_busca, 'Nenhuma contagem encontrada para esta pesquisa.')
        self.assertNotContains(sem_busca, '00123')

        parcial = self.client.get(self._url(), {'tipo': 'sku', 'q': '123'})
        self.assertContains(parcial, 'Nenhuma contagem encontrada para esta pesquisa.')
        self.assertNotContains(parcial, '>00123<')

        exato = self.client.get(self._url(), {'tipo': 'posicao', 'q': '0007'})
        self.assertContains(exato, '0007')
        self.assertNotContains(exato, '0008')

    def test_ean_usa_a_mesma_identificacao_do_pocket(self):
        resposta = self.client.get(self._url(), {'tipo': 'sku', 'q': '7890000001234'})
        codigos = {linha.codigo_posicao for linha in resposta.context['linhas']}
        self.assertEqual(codigos, {'0007', '0008'})

    def test_isolamento_entre_inventarios_e_permissoes(self):
        resposta = self.client.get(
            self._url(self.outro_inventario),
            {'tipo': 'sku', 'q': '00123'},
        )
        self.assertEqual(len(resposta.context['linhas']), 1)
        self.assertEqual(resposta.context['linhas'][0].quantidade, Decimal('99.000'))

        consulta, _ = criar_usuario_teste(
            username='consulta.pocket',
            perfil=Usuario.Perfil.CONSULTA,
        )
        self.client.force_login(consulta)
        self.assertEqual(self.client.get(self._url()).status_code, 200)

        operador, _ = criar_usuario_teste(
            username='operador.pocket',
            perfil=Usuario.Perfil.OPERADOR,
        )
        self.client.force_login(operador)
        menu = self.client.get(reverse('pocket:selecionar'))
        self.assertContains(menu, 'Consultas')
        self.assertContains(menu, 'Cadastrar Posição')
        self.assertContains(menu, reverse('pocket:consultas'))
        self.assertEqual(self.client.get(self._url()).status_code, 200)

        self.client.logout()
        anonimo = self.client.get(self._url())
        self.assertEqual(anonimo.status_code, 302)
        self.assertIn('/accounts/login/', anonimo.url)

    def test_correcao_cancelamento_e_recontagem_nao_somam_historico(self):
        self.item_a.quantidade_fisica = Decimal('8.000')
        self.item_a.save(update_fields=['quantidade_fisica'])
        self._auditoria(InventarioAuditoriaEvento.Evento.CONTAGEM, Decimal('4.000'))
        self._auditoria(InventarioAuditoriaEvento.Evento.RECONTAGEM_GERADA, Decimal('50.000'))

        corrigida = self.client.get(self._url(), {'tipo': 'posicao', 'q': '0007', 'page': '9'})
        parafuso = [
            linha for linha in corrigida.context['linhas']
            if linha.codigo_produto == '00123'
        ]
        self.assertEqual(len(parafuso), 1)
        self.assertEqual(parafuso[0].quantidade, Decimal('8.000'))
        self.assertNotContains(corrigida, '50,000')
        self.assertNotContains(corrigida, '50.000')
        self.assertNotContains(corrigida, '58,000')

        self.item_b.delete()
        self._auditoria(
            InventarioAuditoriaEvento.Evento.CONTAGEM,
            Decimal('6.000'),
            posicao=self.posicao_b,
        )
        cancelada = self.client.get(self._url(), {'tipo': 'posicao', 'q': '0008'})
        self.assertContains(cancelada, 'Nenhuma contagem encontrada para esta pesquisa.')
        self.assertNotContains(cancelada, '6,000')
        self.assertNotContains(cancelada, '6.000')

        antes = list(InventarioItem.objects.values_list('pk', 'quantidade_fisica'))
        post = self.client.post(self._url(), {'tipo': 'sku', 'q': '00123'})
        self.assertEqual(post.status_code, 405)
        self.assertEqual(
            list(InventarioItem.objects.values_list('pk', 'quantidade_fisica')),
            antes,
        )

    def test_paginacao_nao_omite_registros(self):
        with patch('inventario.pocket_consultas_views.POR_PAGINA', 1):
            primeira = self.client.get(self._url(), {'tipo': 'posicao', 'q': '0007'})
            segunda = self.client.get(self._url(), {'tipo': 'posicao', 'q': '0007', 'page': '2'})
        self.assertEqual(len(primeira.context['linhas']), 1)
        self.assertEqual(len(segunda.context['linhas']), 1)
        self.assertNotEqual(
            primeira.context['linhas'][0].codigo_produto,
            segunda.context['linhas'][0].codigo_produto,
        )
        juntos = {
            primeira.context['linhas'][0].codigo_produto,
            segunda.context['linhas'][0].codigo_produto,
        }
        self.assertEqual(juntos, {'00123', '00999'})
        self.assertContains(primeira, 'Próxima')
        self.assertContains(segunda, 'Anterior')

        servico = consultar_contagens_pocket(
            self.inventario, 'sku', '00123', pagina=2, por_pagina=1,
        )
        self.assertEqual(servico.page_obj.paginator.count, 2)
        self.assertEqual(len(servico.linhas), 1)

    def test_falha_diferente_de_resultado_vazio_e_volta_sem_perder_contexto(self):
        with patch(
            'inventario.pocket_consultas_views.consultar_contagens_pocket',
            side_effect=RuntimeError('falha de teste'),
        ):
            falha = self.client.get(self._url(), {'tipo': 'sku', 'q': '00123'})
        self.assertContains(falha, 'Não foi possível concluir a consulta. Tente novamente.')
        self.assertNotContains(falha, 'Nenhuma contagem encontrada para esta pesquisa.')
        self.assertContains(falha, 'Consultar')
        self.assertContains(falha, 'value="00123"')

        sessao = self.client.session
        sessao[chave_posicao_pocket(self.inventario.pk)] = '0007'
        sessao.save()
        consulta = self.client.get(self._url())
        self.assertEqual(
            self.client.session[chave_posicao_pocket(self.inventario.pk)],
            '0007',
        )
        self.assertContains(
            consulta,
            reverse('pocket:contagem', args=[self.inventario.pk]),
        )
        self.assertEqual(Inventario.objects.get(pk=self.inventario.pk).status, Inventario.Status.EM_ANDAMENTO)

        sem_inventario = self.client.get(reverse('pocket:consultas'))
        self.assertContains(sem_inventario, 'Selecione um inventário antes de pesquisar.')
        self.assertContains(sem_inventario, reverse('pocket:selecionar'))
        self.assertNotContains(sem_inventario, 'Nenhuma contagem encontrada para esta pesquisa.')

    def test_script_pesquisa_por_enter_sem_duplicar_e_sem_vazio(self):
        js = Path(settings.BASE_DIR, 'static', 'js', 'pocket-consultas.js').read_text(encoding='utf-8')
        self.assertIn("getElementById('pocket-consultas-form')", js)
        self.assertIn('enviando', js)
        self.assertIn('event.preventDefault()', js)
        self.assertNotIn('inputmode', js)
        self.assertNotIn('.click()', js)
        self.assertLess(js.count("addEventListener('submit'"), 2)
