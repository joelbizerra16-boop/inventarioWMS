from decimal import Decimal



from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse

from django.views import View



from accounts.mixins import AcessoOperacionalMixin, RequerEscritaPocketMixin

from accounts.services.perfil import (
    usuario_pode_supervisionar_ciclico,
)
from inventario.forms import PocketContagemCiclicoForm, PocketContagemForm

from inventario.models import CicloInventarioSku, Inventario, InventarioItem

from inventario.services.ciclico import (

    CiclicoError,
    CiclicoContagemDuplicadaError,

    encerrar_ciclo,

    limpar_lote_sessao,

    limpar_pocket_sessao_contagem,

    obter_ciclo_atual,

    obter_lote_execucao_info,

)
from inventario.services.pocket_ciclico_fila import (
    aceitar_divergencia_pocket,
    finalizar_sku_pocket_ciclico,
    obter_painel_pocket_ciclico,
    obter_resposta_contagem_pocket,
    registrar_contagem_pocket_ciclico_por_sku,
    serializar_ciclo_encerrado_pocket,
    solicitar_recontagem_pocket,
)

from inventario.services.contagem import (
    ContagemDuplicadaError,
    ContagemError,
    obter_inventario_para_contagem,
    persistir_auditoria_contagem_rejeitada,
    salvar_contagem,
)
from inventario.services.locks import LockError, obter_dispositivo, obter_session_key
from inventario.services.pocket_operacional_ui import (
    montar_operacional_ciclico,
    montar_operacional_geral,
    obter_metricas_ciclico,
    obter_metricas_inventario_geral,
)
from inventario.services.pocket_gravacao import log_pocket_gravacao, log_pocket_gravacao_erro
from inventario.services.pocket_lock_operacional import (
    PocketLockOperacionalError,
    adquirir_lock_posicao_pocket_ciclico,
    adquirir_lock_posicao_pocket_geral,
    liberar_lock_posicao_pocket_ciclico,
    liberar_lock_posicao_pocket_geral,
)
from inventario.services.tarefas import TarefaError
from core.logging_auditoria import ip_do_request

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

    obter_modo_pocket,

    registrar_historico_pocket,

    registrar_historico_pocket_ciclico,

    limpar_posicao_pocket,

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


class PocketMestresSyncView(AcessoOperacionalMixin, View):
    """Atualização AJAX dos dados mestres (posições, produtos, EAN) do Pocket."""

    def get(self, request):
        from inventario.services.pocket_mestres import obter_mapas_mestres_pocket

        return JsonResponse({'ok': True, **obter_mapas_mestres_pocket()})


class PocketContagemView(RequerEscritaPocketMixin, View):
    template_name = 'inventario/pocket/contagem.html'

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

    @staticmethod
    def _resposta_sem_cache(response):
        response['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        response['Pragma'] = 'no-cache'
        response['Expires'] = '0'
        return response

    @staticmethod
    def _inventario_id_post(request) -> str:
        return (request.POST.get('inventario_id') or '').strip()

    def _resolver_inventario_geral(self, request, inventario_id_url: int):
        inventario_id_post = self._inventario_id_post(request)
        log_pocket_gravacao(
            request,
            'resolver_inventario',
            inventario_id_url=inventario_id_url,
            inventario_id_post=inventario_id_post or None,
            path=request.path,
        )
        if inventario_id_post.isdigit() and int(inventario_id_post) != inventario_id_url:
            log_pocket_gravacao_erro(
                request,
                'inventario_id_divergente',
                'ID da URL difere do POST',
                inventario_id_url=inventario_id_url,
                inventario_id_post=inventario_id_post,
            )
        return obter_inventario_para_contagem(inventario_id_url)

    def _contexto(self, request, inventario, form=None, codigo_posicao=None):
        if form is None:
            form = PocketContagemForm(
                initial={'codigo_posicao': codigo_posicao or ''},
            )
        return {
            'modo_ciclico': False,
            'inventario': inventario,
            'form': form,
            'post_url': reverse('pocket:contagem', kwargs={'inventario_id': inventario.pk}),
            'historico': obter_historico_pocket(request.session, inventario.pk),
            'operacional': montar_operacional_geral(request, inventario),
            'metricas': obter_metricas_inventario_geral(inventario),
        }

    def get(self, request, inventario_id):

        try:
            inventario = self._resolver_inventario_geral(request, inventario_id)
        except ContagemError as exc:
            log_pocket_gravacao_erro(
                request,
                'get_inventario',
                str(exc),
                inventario_id_url=inventario_id,
            )
            messages.error(request, str(exc))
            return redirect('pocket:selecionar')

        try:

            validar_inventario_para_pocket(inventario)

        except PocketContagemError as exc:

            messages.error(request, str(exc))

            return redirect('pocket:selecionar')

        log_pocket_gravacao(
            request,
            'tela_aberta',
            inventario_id=inventario.pk,
            inventario_status=inventario.status,
        )

        limpar_posicao_pocket(request.session, inventario.pk)
        return self._resposta_sem_cache(render(
            request,
            self.template_name,
            self._contexto(request, inventario),
        ))

    def _post_lock_operacional_geral(self, request, inventario, acao: str):
        if not self._requisicao_ajax(request):
            return JsonResponse({'ok': False, 'message': 'Requisição inválida.'}, status=400)

        codigo = request.POST.get('codigo_posicao', '').strip()
        if acao == 'liberar_posicao':
            liberar_lock_posicao_pocket_geral(request, inventario, codigo)
            return JsonResponse({'ok': True})

        try:
            adquirir_lock_posicao_pocket_geral(request, inventario, codigo)
        except LockError as exc:
            return JsonResponse({'ok': False, 'message': str(exc)}, status=409)
        except TarefaError as exc:
            return JsonResponse({'ok': False, 'message': str(exc)}, status=403)
        except PocketLockOperacionalError as exc:
            return JsonResponse({'ok': False, 'message': str(exc)}, status=400)

        return JsonResponse({'ok': True, 'message': 'Posição válida'})


    def post(self, request, inventario_id):

        try:
            inventario = self._resolver_inventario_geral(request, inventario_id)
        except ContagemError as exc:
            log_pocket_gravacao_erro(
                request,
                'post_inventario',
                str(exc),
                inventario_id_url=inventario_id,
                inventario_id_post=self._inventario_id_post(request) or None,
            )
            if self._requisicao_ajax(request):
                return self._resposta_json_erro(mensagem=str(exc))
            messages.error(request, str(exc))
            return redirect('pocket:selecionar')

        log_pocket_gravacao(
            request,
            'post_iniciado',
            inventario_id=inventario.pk,
            acao=request.POST.get('acao', 'contagem'),
        )

        try:

            validar_inventario_para_pocket(inventario)

        except PocketContagemError as exc:

            log_pocket_gravacao_erro(
                request,
                'validar_inventario',
                str(exc),
                inventario_id=inventario.pk,
            )
            if self._requisicao_ajax(request):
                return self._resposta_json_erro(mensagem=str(exc))
            messages.error(request, str(exc))
            return redirect('pocket:selecionar')

        acao = request.POST.get('acao', 'contagem')
        if acao in ('lock_posicao', 'liberar_posicao'):
            return self._post_lock_operacional_geral(request, inventario, acao)

        form = PocketContagemForm(request.POST)

        if not form.is_valid():

            if self._requisicao_ajax(request):

                return self._resposta_json_erro(form=form)

            return render(request, self.template_name, self._contexto(request, inventario, form=form))

        posicao = buscar_posicao_por_codigo(form.cleaned_data['codigo_posicao'])

        if posicao is None:

            form.add_error('codigo_posicao', 'Posição não encontrada.')

            if self._requisicao_ajax(request):

                return self._resposta_json_erro(form=form)

            return render(request, self.template_name, self._contexto(request, inventario, form=form))

        produto = buscar_produto_por_codigo(form.cleaned_data['codigo_produto'])

        if produto is None:

            form.add_error('codigo_produto', 'Produto não encontrado.')

            if self._requisicao_ajax(request):

                return self._resposta_json_erro(form=form)

            return render(request, self.template_name, self._contexto(request, inventario, form=form))

        quantidade = Decimal(form.cleaned_data['quantidade_fisica'])

        log_pocket_gravacao(
            request,
            'pre_salvar',
            inventario_id=inventario.pk,
            posicao_id=posicao.pk,
            posicao_codigo=posicao.codigo,
            produto_id=produto.pk,
            produto_codigo=produto.codigo_produto,
            quantidade=str(quantidade),
        )

        try:

            salvar_contagem(

                inventario=inventario,

                posicao=posicao,

                produto=produto,

                quantidade_fisica=quantidade,

                usuario_contagem=request.user,

                origem_contagem=InventarioItem.OrigemContagem.POCKET,

                dispositivo=obter_dispositivo(request),

                session_key=obter_session_key(request.session),

                ip=ip_do_request(request),

            )

        except ContagemDuplicadaError as exc:
            persistir_auditoria_contagem_rejeitada(exc)
            log_pocket_gravacao_erro(
                request,
                'duplicidade',
                str(exc),
                inventario_id=inventario.pk,
                posicao_codigo=posicao.codigo,
                produto_codigo=produto.codigo_produto,
                quantidade=str(quantidade),
            )
            if self._requisicao_ajax(request):

                return self._resposta_json_erro(mensagem=str(exc))

            form.add_error(None, str(exc))

            return self._resposta_sem_cache(render(
                request,
                self.template_name,
                self._contexto(request, inventario, form=form),
            ))

        except ContagemError as exc:
            log_pocket_gravacao_erro(
                request,
                'salvar_contagem',
                str(exc),
                inventario_id=inventario.pk,
                posicao_codigo=posicao.codigo,
                produto_codigo=produto.codigo_produto,
                quantidade=str(quantidade),
            )
            if self._requisicao_ajax(request):
                return self._resposta_json_erro(mensagem=str(exc))
            form.add_error(None, str(exc))
            return self._resposta_sem_cache(render(
                request,
                self.template_name,
                self._contexto(request, inventario, form=form),
            ))

        except (LockError, TarefaError) as exc:

            form.add_error(None, str(exc))

            if self._requisicao_ajax(request):

                return self._resposta_json_erro(mensagem=str(exc))

            return render(request, self.template_name, self._contexto(request, inventario, form=form))

        limpar_posicao_pocket(

            request.session,

            inventario.pk,

        )

        registrar_historico_pocket(

            request.session,

            inventario.pk,

            posicao,

            produto,

            quantidade,

        )

        log_pocket_gravacao(
            request,
            'salvo',
            inventario_id=inventario.pk,
            posicao_codigo=posicao.codigo,
            produto_codigo=produto.codigo_produto,
            quantidade=str(quantidade),
        )

        mensagem = f'{produto.codigo_produto} = {quantidade}'

        if self._requisicao_ajax(request):

            return JsonResponse({
                'ok': True,
                'message': mensagem,
                'tipo_mensagem': 'success',
                'posicao_codigo': posicao.codigo,
                'produto_codigo': produto.codigo_produto,
                'quantidade': str(quantidade),
            })

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
        ciclo = obter_ciclo_atual(request.session)
        if form is None:
            form = PocketContagemCiclicoForm(fila=painel.fila)
        return {
            'ciclo': ciclo,
            'lote_info': obter_lote_execucao_info(request.session),
            'painel': painel,
            'form': form,
            'historico': obter_historico_pocket_ciclico(request.session),
            'pode_supervisionar': usuario_pode_supervisionar_ciclico(request.user),
            'operacional': montar_operacional_ciclico(request, ciclo),
            'metricas': obter_metricas_ciclico(painel),
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

    def _post_lock_operacional_ciclico(self, request, ciclo, acao: str):
        if not self._requisicao_ajax(request):
            return JsonResponse({'ok': False, 'message': 'Requisição inválida.'}, status=400)

        codigo = request.POST.get('codigo_posicao', '').strip()
        if acao == 'liberar_posicao':
            liberar_lock_posicao_pocket_ciclico(request, ciclo, codigo)
            return JsonResponse({'ok': True})

        sku_raw = request.POST.get('sku_id', '').strip()
        if not sku_raw.isdigit():
            return JsonResponse({'ok': False, 'message': 'SKU inválido.'}, status=400)

        try:
            _, posicao = adquirir_lock_posicao_pocket_ciclico(request, int(sku_raw), codigo)
        except LockError as exc:
            return JsonResponse({'ok': False, 'message': str(exc)}, status=409)
        except TarefaError as exc:
            return JsonResponse({'ok': False, 'message': str(exc)}, status=403)
        except PocketLockOperacionalError as exc:
            return JsonResponse({'ok': False, 'message': str(exc)}, status=400)

        return JsonResponse({
            'ok': True,
            'message': 'Posição válida',
            'posicao_codigo': posicao.codigo,
            'posicao_alocacao': posicao.posicao,
        })

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

        if acao in ('lock_posicao', 'liberar_posicao'):
            return self._post_lock_operacional_ciclico(request, ciclo, acao)
        if acao == 'recontar':
            return self._post_recontar(request, painel)
        if acao == 'aceitar_divergencia':
            return self._post_aceitar_divergencia(request, painel)
        if acao == 'encerrar_ciclo':
            return self._post_encerrar_ciclo(request)
        if acao == 'finalizar_sku':
            return self._post_finalizar_sku(request, painel)
        return self._post_contagem(request, painel)

    def _post_finalizar_sku(self, request, painel):
        if not self._requisicao_ajax(request):
            messages.error(request, 'Requisição inválida.')
            return redirect('pocket:contagem_ciclico')

        sku_raw = request.POST.get('sku_id', '').strip()
        if not sku_raw.isdigit():
            return JsonResponse({'ok': False, 'message': 'SKU inválido.'}, status=400)

        sku_id = int(sku_raw)
        try:
            _, ciclo_encerrado = finalizar_sku_pocket_ciclico(
                request.session,
                sku_id,
                request.user,
            )
        except CiclicoError as exc:
            return JsonResponse({'ok': False, 'message': str(exc)}, status=400)

        limpar_pocket_sessao_contagem(request.session)
        resposta = obter_resposta_contagem_pocket(
            request.session,
            sku_id,
            request.user,
            ciclo_encerrado=ciclo_encerrado,
        )
        mensagem = 'SKU finalizado com sucesso.'
        tipo_mensagem = 'success'
        sku = CicloInventarioSku.objects.get(pk=sku_id)
        if sku.status_contagem == 'DIVERGENTE':
            tipo_mensagem = 'warning'
            mensagem = 'SKU finalizado com divergência.'

        return JsonResponse({
            'ok': True,
            'message': mensagem,
            'tipo_mensagem': tipo_mensagem,
            'sku_finalizado': True,
            **resposta,
        })

    def _post_encerrar_ciclo(self, request):
        if not self._requisicao_ajax(request):
            messages.error(request, 'Requisição inválida.')
            return redirect('pocket:contagem_ciclico')

        try:
            ciclo = encerrar_ciclo()
        except CiclicoError as exc:
            return JsonResponse({'ok': False, 'message': str(exc)}, status=400)

        limpar_lote_sessao(request.session)
        limpar_pocket_sessao_contagem(request.session)
        return JsonResponse({
            'ok': True,
            'message': f'Ciclo #{ciclo.pk} encerrado com sucesso.',
            'redirect_url': reverse('ciclico'),
            'ciclo_encerrado': serializar_ciclo_encerrado_pocket(ciclo),
        })

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
                request=request,
            )
        except CiclicoContagemDuplicadaError as exc:
            from inventario.models_operacional import InventarioAuditoriaEvento, InventarioLock
            from inventario.services.auditoria_operacional import registrar_evento_operacional

            ctx = exc.contexto_auditoria
            if ctx:
                registrar_evento_operacional(
                    evento=InventarioAuditoriaEvento.Evento.CONTAGEM_REJEITADA,
                    tipo_inventario=InventarioLock.TipoInventario.CICLICO,
                    ciclo=ctx.get('ciclo'),
                    usuario=ctx.get('usuario'),
                    dispositivo=ctx.get('dispositivo', ''),
                    posicao=ctx.get('posicao'),
                    produto=ctx.get('produto'),
                    quantidade=ctx.get('quantidade'),
                    dados_extras={'motivo': 'DUPLICIDADE_CICLO_POSICAO_PRODUTO'},
                )
            if self._requisicao_ajax(request):
                return self._resposta_json_erro(mensagem=str(exc))
            form.add_error(None, str(exc))
            return render(request, self.template_name, contexto)
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
            messages.success(request, 'Ciclo concluído com sucesso.')
            return redirect('pocket:selecionar')

        if tipo_mensagem == 'warning':
            messages.warning(request, mensagem)
        else:
            messages.success(request, mensagem)
        return redirect('pocket:contagem_ciclico')


