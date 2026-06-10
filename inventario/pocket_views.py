from decimal import Decimal



from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render

from django.views import View



from accounts.mixins import AcessoOperacionalMixin, RequerEscritaPocketMixin

from accounts.services.perfil import (
    usuario_pode_supervisionar_ciclico,
)
from inventario.forms import PocketContagemCiclicoForm, PocketContagemForm

from inventario.models import CicloInventarioSku, Inventario, InventarioItem

from inventario.services.ciclico import (

    CiclicoError,

    limpar_pocket_sessao_contagem,

    obter_ciclo_atual,

    obter_lote_execucao_info,

)
from inventario.services.pocket_ciclico_fila import (
    aceitar_divergencia_pocket,
    obter_painel_pocket_ciclico,
    obter_resposta_contagem_pocket,
    registrar_contagem_pocket_ciclico_por_sku,
    solicitar_recontagem_pocket,
)

from inventario.services.contagem import salvar_contagem

from inventario.services.pocket import (

    MODO_CICLICO,

    MODO_INVENTARIO,

    PocketContagemError,

    buscar_posicao_por_codigo,

    buscar_produto_por_codigo,

    definir_modo_pocket,

    listar_inventarios_pocket,

    obter_historico_pocket,

    obter_historico_pocket_ciclico,

    obter_item_existente,

    obter_modo_pocket,

    obter_posicao_pocket,

    registrar_historico_pocket,

    registrar_historico_pocket_ciclico,

    registrar_posicao_pocket,

    SESSION_POCKET_MANTER_CONTAGEM,

    validar_inventario_para_pocket,

)





class PocketSelecionarView(AcessoOperacionalMixin, View):

    template_name = 'inventario/pocket/selecionar.html'



    def get(self, request):

        modo = obter_modo_pocket(request.session)

        ciclo = obter_ciclo_atual(request.session)

        lote_info = obter_lote_execucao_info(request.session)

        return render(request, self.template_name, {

            'modo': modo,

            'inventarios': listar_inventarios_pocket(),

            'ciclo': ciclo,

            'lote_info': lote_info,

        })



    def post(self, request):

        modo = request.POST.get('modo', MODO_INVENTARIO)

        if modo not in (MODO_INVENTARIO, MODO_CICLICO):

            modo = MODO_INVENTARIO

        definir_modo_pocket(request.session, modo)

        return redirect('pocket:selecionar')





class PocketContagemView(RequerEscritaPocketMixin, View):

    template_name = 'inventario/pocket/contagem.html'



    def get(self, request, inventario_id):

        inventario = get_object_or_404(

            Inventario.objects.select_related('usuario'),

            pk=inventario_id,

        )



        try:

            validar_inventario_para_pocket(inventario)

        except PocketContagemError as exc:

            messages.error(request, str(exc))

            return redirect('pocket:selecionar')



        codigo_posicao = obter_posicao_pocket(request.session, inventario.pk)

        return render(request, self.template_name, {

            'modo_ciclico': False,

            'inventario': inventario,

            'form': PocketContagemForm(

                initial={'codigo_posicao': codigo_posicao},

            ),

            'historico': obter_historico_pocket(request.session, inventario.pk),

        })



    def post(self, request, inventario_id):

        inventario = get_object_or_404(Inventario, pk=inventario_id)



        try:

            validar_inventario_para_pocket(inventario)

        except PocketContagemError as exc:

            messages.error(request, str(exc))

            return redirect('pocket:selecionar')



        form = PocketContagemForm(request.POST)

        if not form.is_valid():

            return render(request, self.template_name, {

                'modo_ciclico': False,

                'inventario': inventario,

                'form': form,

                'historico': obter_historico_pocket(request.session, inventario.pk),

            })



        posicao = buscar_posicao_por_codigo(form.cleaned_data['codigo_posicao'])

        if posicao is None:

            form.add_error('codigo_posicao', 'Posição não encontrada.')

            return render(request, self.template_name, {

                'modo_ciclico': False,

                'inventario': inventario,

                'form': form,

                'historico': obter_historico_pocket(request.session, inventario.pk),

            })



        produto = buscar_produto_por_codigo(form.cleaned_data['codigo_produto'])

        if produto is None:

            form.add_error('codigo_produto', 'Produto não encontrado.')

            return render(request, self.template_name, {

                'modo_ciclico': False,

                'inventario': inventario,

                'form': form,

                'historico': obter_historico_pocket(request.session, inventario.pk),

            })



        quantidade = Decimal(form.cleaned_data['quantidade_fisica'])

        item_existente = obter_item_existente(inventario, posicao, produto)



        salvar_contagem(

            inventario=inventario,

            posicao=posicao,

            produto=produto,

            quantidade_fisica=quantidade,

            item_existente=item_existente,

            usuario_contagem=request.user,

            origem_contagem=InventarioItem.OrigemContagem.POCKET,

        )

        registrar_posicao_pocket(

            request.session,

            inventario.pk,

            form.cleaned_data['codigo_posicao'],

        )

        registrar_historico_pocket(

            request.session,

            inventario.pk,

            posicao,

            produto,

            quantidade,

        )



        messages.success(

            request,

            f'Contagem salva: {posicao.codigo} / {produto.codigo_produto} = {quantidade}',

        )

        return redirect('pocket:contagem', inventario_id=inventario.pk)





class PocketContagemCiclicoView(RequerEscritaPocketMixin, View):
    template_name = 'inventario/pocket/contagem_ciclico.html'

    @staticmethod
    def _requisicao_ajax(request) -> bool:
        return request.headers.get('X-Requested-With') == 'XMLHttpRequest'

    @staticmethod
    def _resposta_json_erro(form=None, mensagem=''):
        payload = {'ok': False}
        if mensagem:
            payload['message'] = mensagem
        if form is not None:
            payload['errors'] = form.errors.get_json_data()
        return JsonResponse(payload, status=400)

    def _contexto(self, request, form=None, painel=None):
        painel = painel or obter_painel_pocket_ciclico(request.session)
        if form is None:
            form = PocketContagemCiclicoForm(fila=painel.fila)
        return {
            'ciclo': obter_ciclo_atual(request.session),
            'lote_info': obter_lote_execucao_info(request.session),
            'painel': painel,
            'form': form,
            'historico': obter_historico_pocket_ciclico(request.session),
            'pode_supervisionar': usuario_pode_supervisionar_ciclico(request.user),
        }

    def get(self, request):
        ciclo = obter_ciclo_atual(request.session)
        if ciclo is None:
            messages.error(request, 'Nenhum ciclo cíclico ativo.')
            return redirect('pocket:selecionar')

        lote_info = obter_lote_execucao_info(request.session)
        if lote_info is None:
            messages.warning(
                request,
                'Gere um lote na tela Executar Ciclo antes de contar.',
            )
            return redirect('pocket:selecionar')

        if not request.session.pop(SESSION_POCKET_MANTER_CONTAGEM, False):
            limpar_pocket_sessao_contagem(request.session)

        return render(request, self.template_name, self._contexto(request))

    def post(self, request):
        ciclo = obter_ciclo_atual(request.session)
        if ciclo is None:
            messages.error(request, 'Nenhum ciclo cíclico ativo.')
            return redirect('pocket:selecionar')

        if obter_lote_execucao_info(request.session) is None:
            messages.warning(
                request,
                'Gere um lote na tela Executar Ciclo antes de contar.',
            )
            return redirect('pocket:selecionar')

        acao = request.POST.get('acao', 'contagem')
        painel = obter_painel_pocket_ciclico(request.session)

        if acao == 'recontar':
            return self._post_recontar(request, painel)
        if acao == 'aceitar_divergencia':
            return self._post_aceitar_divergencia(request, painel)
        return self._post_contagem(request, painel)

    def _post_recontar(self, request, painel):
        sku_id = request.POST.get('sku_id', '').strip()
        if not sku_id.isdigit():
            messages.error(request, 'SKU inválido para recontagem.')
            return render(request, self.template_name, self._contexto(request, painel=painel))

        try:
            solicitar_recontagem_pocket(request.session, int(sku_id), request.user)
        except CiclicoError as exc:
            messages.error(request, str(exc))
            return render(request, self.template_name, self._contexto(request, painel=painel))

        messages.info(request, 'SKU enviado para recontagem na fila.')
        return redirect('pocket:contagem_ciclico')

    def _post_aceitar_divergencia(self, request, painel):
        if not usuario_pode_supervisionar_ciclico(request.user):
            messages.error(request, 'Somente supervisor pode aceitar divergências.')
            return render(request, self.template_name, self._contexto(request, painel=painel))

        sku_id = request.POST.get('sku_id', '').strip()
        if not sku_id.isdigit():
            messages.error(request, 'SKU inválido.')
            return render(request, self.template_name, self._contexto(request, painel=painel))

        try:
            _, ciclo_encerrado = aceitar_divergencia_pocket(
                request.session,
                int(sku_id),
                request.user,
            )
        except CiclicoError as exc:
            messages.error(request, str(exc))
            return render(request, self.template_name, self._contexto(request, painel=painel))

        if ciclo_encerrado is not None:
            messages.success(
                request,
                'Inventário Cíclico concluído com sucesso.',
            )
            return redirect('ciclico')
        messages.success(request, 'Divergência aceita. SKU concluído no lote.')
        return redirect('pocket:contagem_ciclico')

    def _post_contagem(self, request, painel):
        form = PocketContagemCiclicoForm(request.POST, fila=painel.fila)
        contexto = self._contexto(request, form=form, painel=painel)

        if not form.is_valid():
            if self._requisicao_ajax(request):
                return self._resposta_json_erro(form=form)
            return render(request, self.template_name, contexto)

        posicao = buscar_posicao_por_codigo(form.cleaned_data['codigo_posicao'])
        if posicao is None:
            form.add_error('codigo_posicao', 'Posição não encontrada.')
            if self._requisicao_ajax(request):
                return self._resposta_json_erro(form=form)
            return render(request, self.template_name, contexto)

        quantidade = Decimal(form.cleaned_data['quantidade_fisica'])
        sku_id = form.cleaned_data['sku_id']

        try:
            dto, ciclo_encerrado = registrar_contagem_pocket_ciclico_por_sku(
                request.session,
                sku_id,
                posicao,
                quantidade,
                request.user,
            )
        except CiclicoError as exc:
            if self._requisicao_ajax(request):
                return self._resposta_json_erro(mensagem=str(exc))
            form.add_error(None, str(exc))
            return render(request, self.template_name, contexto)

        sku = CicloInventarioSku.objects.select_related('produto').get(pk=dto.pk)
        registrar_historico_pocket_ciclico(
            request.session,
            posicao,
            sku.produto,
            quantidade,
        )

        if dto.status_contagem == 'VALIDADO':
            limpar_pocket_sessao_contagem(request.session)
            mensagem = (
                f'Contagem validada: {posicao.codigo} / {sku.codigo_produto} '
                f'= {dto.quantidade_fisica} (+{quantidade})'
            )
            tipo_mensagem = 'success'
        elif dto.status_contagem == 'DIVERGENTE':
            request.session[SESSION_POCKET_MANTER_CONTAGEM] = True
            mensagem = (
                f'Contagem registrada com divergência: {posicao.codigo} / '
                f'{sku.codigo_produto} = {dto.quantidade_fisica} (+{quantidade})'
            )
            tipo_mensagem = 'warning'
        else:
            request.session[SESSION_POCKET_MANTER_CONTAGEM] = True
            mensagem = (
                f'Contagem cíclica: {posicao.codigo} / {sku.codigo_produto} '
                f'= {dto.quantidade_fisica} (+{quantidade})'
            )
            tipo_mensagem = 'success'

        if self._requisicao_ajax(request):
            resposta = obter_resposta_contagem_pocket(
                request.session,
                sku_id,
                request.user,
                ciclo_encerrado=ciclo_encerrado,
            )
            if resposta.get('sku_removido_fila'):
                limpar_pocket_sessao_contagem(request.session)
            return JsonResponse({
                'ok': True,
                'message': mensagem,
                'tipo_mensagem': tipo_mensagem,
                **resposta,
            })

        if ciclo_encerrado is not None:
            messages.success(request, 'Inventário Cíclico concluído com sucesso.')
            return redirect('ciclico')

        if tipo_mensagem == 'warning':
            messages.warning(request, mensagem)
        else:
            messages.success(request, mensagem)
        return redirect('pocket:contagem_ciclico')


