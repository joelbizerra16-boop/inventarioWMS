from django.contrib import messages
from decimal import Decimal, InvalidOperation
from django.db.models import Count, Q
from django.http import HttpResponseNotFound, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import formats, timezone
from django.views import View
from django.views.generic import CreateView, DeleteView, ListView, UpdateView

from accounts.mixins import (
    AcessoOperacionalMixin,
    PaginacaoContextMixin,
    PaginacaoMixin,
    RequerEscritaInventarioMixin,
    RequerNaoOperadorMixin,
)
from core.logging_auditoria import registrar_evento
from core.mixins import ExclusaoSeguraMixin
from accounts.services.perfil import (
    obter_usuario_operacional,
    usuario_pode_escrever_cadastros,
    usuario_pode_escrever_inventario,
)
from inventario.forms import ContagemForm, InventarioForm
from inventario.models import CicloInventarioSku, Inventario, InventarioItem
from inventario.services.contagem import (
    ContagemDuplicadaError,
    excluir_contagem,
    persistir_auditoria_contagem_rejeitada,
    salvar_contagem,
)
from inventario.services.confronto import executar_confronto
from inventario.services.aprovacao import (
    AprovacaoError,
    consultar_aprovacao,
    aprovar_inventario,
    listar_ids_inventarios_aprovados,
    obter_label_status_aprovacao,
    obter_status_aprovacao,
    pode_aprovar,
    pode_reabrir,
    reabrir_inventario,
)
from inventario.services.consolidacao import (
    ConsolidacaoError,
    consolidar_estoque_fisico,
    obter_auditoria_consolidacao,
    obter_preview_consolidacao,
    publicar_estoque_fisico,
)
from decimal import Decimal, InvalidOperation

from inventario.constants_ciclico import (
    METAS_DIARIAS_SUGERIDAS,
    MOTIVOS_EXCLUSAO_SKU,
)
from inventario.services.ciclico import (
    CiclicoError,
    ConfiguracaoExecucao,
    FiltrosCicloConsulta,
    MSG_CICLO_ENCERRADO,
    arquivar_ciclo,
    criar_ciclo,
    editar_contagem_ciclico,
    encerrar_ciclo,
    excluir_sku_do_ciclo,
    gerar_lote_execucao,
    limpar_lote_sessao,
    listar_ciclos_historico,
    obter_consulta_agrupada_por_sku,
    obter_ciclo_atual,
    obter_ciclo_consulta,
    obter_embalagens_disponiveis,
    obter_info_sap_para_ciclo,
    obter_lote_execucao_info,
    obter_resumo_ciclico,
    obter_sku_detalhe,
    obter_skus_ciclo,
    limpar_recontagem_pocket_itens,
    OrigemContagem,
    reabrir_ciclo,
    salvar_contagem_sku,
    StatusCiclo,
    usuario_pode_editar_contagem_ciclico,
    usuario_pode_excluir_sku_ciclico,
)
from inventario.services.consulta_contagem_ciclico import (
    obter_consulta_contagem,
    resolver_ciclo_consulta_contagem,
)
from inventario.services.ciclico_historico import (
    StatusHistoricoCiclo,
    listar_historico_ciclos,
    obter_auditoria_historico_ciclo,
    obter_detalhe_historico_ciclo,
)
from inventario.services.ciclico_exportacao import exportar_ciclo_excel
from posicoes.models import Posicao


class InventarioListView(AcessoOperacionalMixin, PaginacaoMixin, PaginacaoContextMixin, ListView):
    model = Inventario
    template_name = 'inventario/lista.html'
    context_object_name = 'inventarios'
    ordering = ['-data_criacao']

    def get_queryset(self):
        queryset = Inventario.objects.select_related('usuario').annotate(
            total_itens=Count('itens'),
        ).order_by('-data_criacao')
        termo = self.request.GET.get('q', '').strip()

        if not termo:
            return queryset

        filtros = (
            Q(usuario__nome__icontains=termo)
            | Q(usuario__login__icontains=termo)
            | Q(status__icontains=termo)
        )

        if termo.isdigit():
            filtros |= Q(pk=int(termo))

        return queryset.filter(filtros)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['termo_busca'] = self.request.GET.get('q', '')
        for inventario in context['inventarios']:
            inventario.quantidade_itens = inventario.total_itens
        return context


class InventarioCreateView(RequerNaoOperadorMixin, RequerEscritaInventarioMixin, CreateView):
    model = Inventario
    form_class = InventarioForm
    template_name = 'inventario/formulario.html'
    success_url = reverse_lazy('inventario:lista')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['titulo'] = 'Novo Inventário'
        context['botao'] = 'Salvar'
        return context

    def form_valid(self, form):
        form.instance.status = Inventario.Status.ABERTO
        messages.success(self.request, 'Inventário criado com sucesso.')
        return super().form_valid(form)


class InventarioUpdateView(RequerEscritaInventarioMixin, UpdateView):
    model = Inventario
    form_class = InventarioForm
    template_name = 'inventario/formulario.html'
    success_url = reverse_lazy('inventario:lista')

    def dispatch(self, request, *args, **kwargs):
        inventario = self.get_object()
        if inventario.status != Inventario.Status.ABERTO:
            if inventario.status == Inventario.Status.FINALIZADO:
                messages.error(
                    request,
                    'Inventário finalizado não pode ser alterado.',
                )
            else:
                messages.error(
                    request,
                    'Somente inventários abertos podem ser alterados.',
                )
            return redirect('inventario:lista')
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['titulo'] = 'Editar Inventário'
        context['botao'] = 'Atualizar'
        return context

    def form_valid(self, form):
        messages.success(self.request, 'Inventário alterado com sucesso.')
        return super().form_valid(form)


class InventarioDeleteView(RequerEscritaInventarioMixin, ExclusaoSeguraMixin, DeleteView):
    model = Inventario
    template_name = 'inventario/excluir.html'
    success_url = reverse_lazy('inventario:lista')
    mensagem_exclusao_sucesso = 'Inventário excluído com sucesso.'

    def dispatch(self, request, *args, **kwargs):
        inventario = self.get_object()
        if inventario.status == Inventario.Status.FINALIZADO:
            messages.error(
                request,
                'Inventário finalizado não pode ser excluído.',
            )
            return redirect('inventario:lista')
        if inventario.status not in (
            Inventario.Status.ABERTO,
            Inventario.Status.EM_ANDAMENTO,
        ):
            messages.error(
                request,
                'Este inventário não pode ser excluído.',
            )
            return redirect('inventario:lista')
        return super().dispatch(request, *args, **kwargs)


class InventarioFinalizarView(RequerNaoOperadorMixin, RequerEscritaInventarioMixin, View):
    template_name = 'inventario/excluir.html'

    def get(self, request, pk):
        inventario = get_object_or_404(
            Inventario.objects.select_related('usuario'),
            pk=pk,
        )

        if inventario.status == Inventario.Status.FINALIZADO:
            messages.error(
                request,
                'Inventário finalizado não pode ser alterado.',
            )
            return redirect('inventario:lista')

        return render(request, self.template_name, {
            'object': inventario,
            'finalizar': True,
        })

    def post(self, request, pk):
        inventario = get_object_or_404(Inventario, pk=pk)

        if inventario.status == Inventario.Status.FINALIZADO:
            messages.error(
                request,
                'Inventário finalizado não pode ser alterado.',
            )
            return redirect('inventario:lista')

        inventario.status = Inventario.Status.FINALIZADO
        inventario.save(update_fields=['status'])

        from inventario.services.inventario_snapshot import congelar_snapshot_inventario
        congelar_snapshot_inventario(inventario, obter_usuario_operacional(request.user))

        try:
            resultado = publicar_estoque_fisico(inventario)
            messages.success(
                request,
                (
                    'Inventário finalizado e estoque físico publicado: '
                    f'{resultado.registros_processados} registro(s).'
                ),
            )
        except ConsolidacaoError as exc:
            messages.warning(
                request,
                f'Inventário finalizado, porém o estoque não foi publicado: {exc}',
            )

        return HttpResponseRedirect(reverse('inventario:lista'))


class InventarioContagemMixin:
    inventario_url_kwarg = 'pk'

    def get_inventario(self):
        return get_object_or_404(Inventario, pk=self.kwargs[self.inventario_url_kwarg])

    def inventario_permite_contagem(self, inventario):
        return inventario.status in (
            Inventario.Status.ABERTO,
            Inventario.Status.EM_ANDAMENTO,
        )

    def bloquear_se_finalizado(self, request, inventario):
        if inventario.status == Inventario.Status.FINALIZADO:
            messages.error(
                request,
                'Inventário finalizado não permite alterações.',
            )
            return redirect(
                'inventario:contagem_lista',
                pk=inventario.pk,
            )
        return None


class ContagemListView(AcessoOperacionalMixin, InventarioContagemMixin, ListView):
    model = InventarioItem
    template_name = 'inventario/contagem_lista.html'
    context_object_name = 'contagens'
    ordering = ['posicao__codigo', 'produto__codigo_produto']

    def get_queryset(self):
        inventario = self.get_inventario()
        queryset = InventarioItem.objects.filter(
            inventario=inventario,
        ).select_related(
            'posicao',
            'produto',
            'usuario_contagem',
            'usuario_contagem__perfil_operacional',
        )

        termo = self.request.GET.get('q', '').strip()
        if termo:
            queryset = queryset.filter(
                Q(produto__codigo_produto__icontains=termo)
                | Q(produto__descricao__icontains=termo)
                | Q(posicao__codigo__icontains=termo)
                | Q(posicao__posicao__icontains=termo)
            )

        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        inventario = self.get_inventario()
        context['inventario'] = inventario
        context['termo_busca'] = self.request.GET.get('q', '')
        context['permite_contagem'] = self.inventario_permite_contagem(inventario)
        return context


class ContagemCreateView(RequerEscritaInventarioMixin, InventarioContagemMixin, CreateView):
    model = InventarioItem
    form_class = ContagemForm
    template_name = 'inventario/contagem_formulario.html'

    def dispatch(self, request, *args, **kwargs):
        inventario = self.get_inventario()
        resposta = self.bloquear_se_finalizado(request, inventario)
        if resposta:
            return resposta
        if not self.inventario_permite_contagem(inventario):
            messages.error(
                request,
                'Inventário finalizado não permite alterações.',
            )
            return redirect('inventario:contagem_lista', pk=inventario.pk)
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['inventario'] = self.get_inventario()
        context['titulo'] = 'Nova Contagem'
        context['botao'] = 'Salvar'
        return context

    def form_valid(self, form):
        inventario = self.get_inventario()
        try:
            salvar_contagem(
                inventario=inventario,
                posicao=form.cleaned_data['posicao'],
                produto=form.cleaned_data['produto'],
                quantidade_fisica=form.cleaned_data['quantidade_fisica'],
                usuario_contagem=self.request.user,
                origem_contagem=InventarioItem.OrigemContagem.WEB,
            )
        except ContagemDuplicadaError as exc:
            persistir_auditoria_contagem_rejeitada(exc)
            form.add_error(None, str(exc))
            return self.form_invalid(form)
        messages.success(self.request, 'Contagem registrada.')
        return redirect('inventario:contagem_lista', pk=inventario.pk)


class ContagemUpdateView(RequerEscritaInventarioMixin, InventarioContagemMixin, UpdateView):
    model = InventarioItem
    form_class = ContagemForm
    template_name = 'inventario/contagem_formulario.html'
    pk_url_kwarg = 'item_id'

    def get_queryset(self):
        return InventarioItem.objects.filter(
            inventario_id=self.kwargs['pk'],
        ).select_related('inventario', 'posicao', 'produto')

    def dispatch(self, request, *args, **kwargs):
        inventario = self.get_inventario()
        resposta = self.bloquear_se_finalizado(request, inventario)
        if resposta:
            return resposta
        if not self.inventario_permite_contagem(inventario):
            messages.error(
                request,
                'Inventário finalizado não permite alterações.',
            )
            return redirect('inventario:contagem_lista', pk=inventario.pk)
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['inventario'] = self.get_inventario()
        context['titulo'] = 'Editar Contagem'
        context['botao'] = 'Atualizar'
        return context

    def form_valid(self, form):
        inventario = self.get_inventario()
        salvar_contagem(
            inventario=inventario,
            posicao=form.cleaned_data['posicao'],
            produto=form.cleaned_data['produto'],
            quantidade_fisica=form.cleaned_data['quantidade_fisica'],
            item_existente=self.object,
            usuario_contagem=self.request.user,
            origem_contagem=InventarioItem.OrigemContagem.WEB,
        )
        messages.success(self.request, 'Contagem alterada.')
        return redirect('inventario:contagem_lista', pk=inventario.pk)


class ContagemDeleteView(
    RequerEscritaInventarioMixin,
    ExclusaoSeguraMixin,
    InventarioContagemMixin,
    DeleteView,
):
    model = InventarioItem
    template_name = 'inventario/contagem_excluir.html'
    pk_url_kwarg = 'item_id'
    mensagem_exclusao_sucesso = 'Contagem excluída.'

    def get_queryset(self):
        return InventarioItem.objects.filter(
            inventario_id=self.kwargs['pk'],
        ).select_related('inventario', 'posicao', 'produto')

    def dispatch(self, request, *args, **kwargs):
        inventario = self.get_inventario()
        resposta = self.bloquear_se_finalizado(request, inventario)
        if resposta:
            return resposta
        if not self.inventario_permite_contagem(inventario):
            messages.error(
                request,
                'Inventário finalizado não permite alterações.',
            )
            return redirect('inventario:contagem_lista', pk=inventario.pk)
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['inventario'] = self.get_inventario()
        return context

    def executar_exclusao_segura(self, objeto):
        excluir_contagem(objeto)

    def get_success_url(self):
        return reverse('inventario:contagem_lista', kwargs={'pk': self.get_inventario().pk})


class ConfrontoListView(AcessoOperacionalMixin, View):
    template_name = 'inventario/confronto.html'

    def get(self, request):
        inventarios = Inventario.objects.select_related('usuario').order_by('-data_criacao')
        inventario_id = request.GET.get('inventario', '').strip()
        filtro_status = request.GET.get('filtro', 'todos')
        termo_busca = request.GET.get('q', '')

        resultado = None
        inventario_selecionado = None

        if inventario_id.isdigit():
            inventario_selecionado = get_object_or_404(Inventario, pk=int(inventario_id))
            resultado = executar_confronto(
                inventario_id=inventario_selecionado.pk,
                filtro_status=filtro_status,
                termo_busca=termo_busca,
            )

        return render(request, self.template_name, {
            'inventarios': inventarios,
            'inventario_selecionado': inventario_selecionado,
            'inventario_id': inventario_id,
            'filtro_status': filtro_status,
            'termo_busca': termo_busca,
            'resultado': resultado,
        })


class AprovacaoView(RequerNaoOperadorMixin, RequerEscritaInventarioMixin, View):
    template_name = 'inventario/aprovacao.html'

    def get(self, request):
        inventarios = Inventario.objects.filter(
            status=Inventario.Status.FINALIZADO,
        ).select_related('usuario').order_by('-data_criacao')

        inventario_id = request.GET.get('inventario', '').strip()
        termo_busca = request.GET.get('q', '')

        resultado = None
        inventario_selecionado = None
        status_aprovacao = None
        status_aprovacao_label = None

        if inventario_id.isdigit():
            inventario_selecionado = Inventario.objects.filter(
                pk=int(inventario_id),
                status=Inventario.Status.FINALIZADO,
            ).first()
            if inventario_selecionado is None:
                return HttpResponseNotFound()
            resultado = consultar_aprovacao(
                inventario_id=inventario_selecionado.pk,
                termo_busca=termo_busca,
            )
            status_aprovacao = obter_status_aprovacao(inventario_selecionado.pk)
            status_aprovacao_label = obter_label_status_aprovacao(
                inventario_selecionado.pk,
            )

        return render(request, self.template_name, {
            'inventarios': inventarios,
            'inventario_selecionado': inventario_selecionado,
            'inventario_id': inventario_id,
            'termo_busca': termo_busca,
            'resultado': resultado,
            'status_aprovacao': status_aprovacao,
            'status_aprovacao_label': status_aprovacao_label,
            'pode_aprovar': (
                inventario_selecionado
                and pode_aprovar(inventario_selecionado.pk)
            ),
            'pode_reabrir': (
                inventario_selecionado
                and pode_reabrir(inventario_selecionado.pk)
            ),
        })

    def post(self, request):
        inventario_id = request.POST.get('inventario', '').strip()
        acao = request.POST.get('acao', '')

        if not inventario_id.isdigit():
            messages.error(request, 'Selecione um inventário válido.')
            return redirect('aprovacao')

        inventario = get_object_or_404(
            Inventario,
            pk=int(inventario_id),
            status=Inventario.Status.FINALIZADO,
        )

        try:
            if acao == 'aprovar':
                aprovar_inventario(inventario)
                registrar_evento(
                    'aprovacao_inventario',
                    usuario=request.user,
                    inventario_id=inventario.pk,
                )
                messages.success(request, 'Inventário aprovado com sucesso.')
            elif acao == 'reabrir':
                reabrir_inventario(inventario)
                registrar_evento(
                    'reabertura_inventario',
                    usuario=request.user,
                    inventario_id=inventario.pk,
                )
                messages.success(request, 'Inventário reaberto com sucesso.')
            else:
                messages.error(request, 'Ação inválida.')
                return redirect(f'{reverse("aprovacao")}?inventario={inventario_id}')
        except AprovacaoError as exc:
            messages.error(request, str(exc))
            return redirect(f'{reverse("aprovacao")}?inventario={inventario_id}')

        if acao == 'reabrir':
            return redirect('aprovacao')

        return redirect(f'{reverse("aprovacao")}?inventario={inventario_id}')


class ConsolidacaoView(RequerNaoOperadorMixin, RequerEscritaInventarioMixin, View):
    template_name = 'inventario/consolidacao.html'

    def _obter_inventarios_finalizados(self):
        from inventario.services.consolidacao import _queryset_inventarios_finalizados

        return (
            _queryset_inventarios_finalizados()
            .select_related('usuario')
            .order_by('-pk')
        )

    def get(self, request):
        inventarios = self._obter_inventarios_finalizados()
        inventario_id = request.GET.get('inventario', '').strip()

        inventario_selecionado = None
        preview = None
        auditoria = None

        if inventario_id.isdigit():
            inventario_selecionado = get_object_or_404(
                Inventario,
                pk=int(inventario_id),
                status=Inventario.Status.FINALIZADO,
            )
            ids_finalizados = set(inventarios.values_list('pk', flat=True))
            if inventario_selecionado.pk not in ids_finalizados:
                return redirect('consolidacao')

            preview = obter_preview_consolidacao(inventario_selecionado)
            auditoria = obter_auditoria_consolidacao(inventario_selecionado.pk)

        return render(request, self.template_name, {
            'inventarios': inventarios,
            'inventario_selecionado': inventario_selecionado,
            'inventario_id': inventario_id,
            'preview': preview,
            'auditoria': auditoria,
        })

    def post(self, request):
        inventario_id = request.POST.get('inventario', '').strip()

        if not inventario_id.isdigit():
            messages.error(request, 'Selecione um inventário válido.')
            return redirect('consolidacao')

        inventario = get_object_or_404(
            Inventario,
            pk=int(inventario_id),
            status=Inventario.Status.FINALIZADO,
        )

        try:
            resultado = publicar_estoque_fisico(inventario)
            registrar_evento(
                'consolidacao_inventario',
                usuario=request.user,
                inventario_id=inventario.pk,
                registros=resultado.registros_processados,
            )
            messages.success(
                request,
                (
                    f'Estoque físico publicado: {resultado.registros_processados} '
                    f'registro(s) do inventário #{inventario.pk}.'
                ),
            )
        except ConsolidacaoError as exc:
            messages.error(request, str(exc))
            return redirect('consolidacao')

        return redirect(f'{reverse("consolidacao")}?inventario={inventario_id}')


def _montar_filtros_ciclo(request) -> FiltrosCicloConsulta:
    ciclo_raw = request.GET.get('ciclo', '').strip()
    ciclo_id = int(ciclo_raw) if ciclo_raw.isdigit() else None
    return FiltrosCicloConsulta(
        sku=request.GET.get('sku', '').strip(),
        descricao=request.GET.get('descricao', '').strip(),
        embalagem=request.GET.get('embalagem', '').strip(),
        setor=request.GET.get('setor', '').strip(),
        canal=request.GET.get('canal', '').strip(),
        status=request.GET.get('status', '').strip(),
        usuario=request.GET.get('usuario', '').strip(),
        origem=request.GET.get('origem', '').strip(),
        data=request.GET.get('data', '').strip(),
        data_inicial=request.GET.get('data_inicial', '').strip(),
        data_final=request.GET.get('data_final', '').strip(),
        divergente=request.GET.get('divergente', '') == '1',
        somente_divergentes=request.GET.get('somente_divergentes', '') == '1',
        somente_recontagens=request.GET.get('somente_recontagens', '') == '1',
        somente_validados=request.GET.get('somente_validados', '') == '1',
        status_ciclo=request.GET.get('status_ciclo', '').strip(),
        ciclo_id=ciclo_id,
    )


def _resolver_ciclo_consulta(request, filtros: FiltrosCicloConsulta):
    ciclo = obter_ciclo_consulta(filtros.ciclo_id)
    if ciclo is None:
        historico = listar_ciclos_historico()
        if historico:
            ciclo = historico[0]
    return ciclo


def _montar_configuracao_execucao(request) -> ConfiguracaoExecucao:
    quantidade_raw = request.POST.get('quantidade_skus', '').strip()
    quantidade = None
    if quantidade_raw:
        if not quantidade_raw.isdigit() or int(quantidade_raw) <= 0:
            raise CiclicoError('Informe uma quantidade válida de SKUs.')
        quantidade = int(quantidade_raw)

    embalagens = request.POST.getlist('embalagens')
    canal = request.POST.get('canal', '').strip()
    return ConfiguracaoExecucao(
        embalagens=embalagens or None,
        canal=canal,
        quantidade_skus=quantidade,
        respeitar_somente_embalagens=(
            request.POST.get('respeitar_somente_embalagens') == '1'
        ),
    )


def _montar_contagens_recontagem(request, sku: CicloInventarioSku) -> dict[int, Decimal]:
    contagens: dict[int, Decimal] = {}
    for posicao in sku.posicoes.all():
        rotulo = posicao.alocacao or posicao.codigo_posicao
        raw = request.POST.get(f'quantidade_posicao_{posicao.pk}', '').strip()
        if not raw:
            raise CiclicoError(f'Informe a quantidade da posição {rotulo}.')
        try:
            contagens[posicao.pk] = Decimal(raw.replace(',', '.'))
        except InvalidOperation as exc:
            raise CiclicoError('Quantidade inválida.') from exc
    return contagens


def _montar_edicoes_contagem(request, sku: CicloInventarioSku) -> dict[int, dict]:
    edicoes: dict[int, dict] = {}
    for posicao in sku.posicoes.all():
        quantidade_raw = request.POST.get(
            f'quantidade_posicao_{posicao.pk}',
            '',
        ).strip()
        posicao_raw = request.POST.get(f'posicao_id_{posicao.pk}', '').strip()
        if not quantidade_raw and not posicao_raw:
            continue
        if not quantidade_raw or not posicao_raw.isdigit():
            rotulo = posicao.alocacao or posicao.codigo_posicao
            raise CiclicoError(f'Dados inválidos para a posição {rotulo}.')
        try:
            quantidade = Decimal(quantidade_raw.replace(',', '.'))
        except InvalidOperation as exc:
            raise CiclicoError('Quantidade inválida.') from exc
        if quantidade < 0:
            raise CiclicoError('Quantidade não pode ser negativa.')
        edicoes[posicao.pk] = {
            'quantidade': quantidade,
            'posicao_id': int(posicao_raw),
        }
    return edicoes


def _requisicao_ajax(request) -> bool:
    return request.headers.get('X-Requested-With') == 'XMLHttpRequest'


def _formatar_quantidade_ciclico(valor) -> str:
    if valor is None:
        return '—'
    return formats.number_format(valor, decimal_pos=3, use_l10n=True, force_grouping=True)


def _serializar_sku_linha_execucao(dto) -> dict:
    indicador_emoji = {
        'verde': '🟢',
        'laranja': '🟠',
        'vermelho': '🔴',
    }
    ultima_data = ''
    if dto.ultima_data:
        ultima_data = timezone.localtime(dto.ultima_data).strftime('%d/%m/%Y %H:%M')

    return {
        'pk': dto.pk,
        'quantidade_fisica': _formatar_quantidade_ciclico(dto.quantidade_fisica),
        'diferenca_cosan': _formatar_quantidade_ciclico(dto.diferenca_cosan),
        'indicador_sap': dto.indicador_sap or '',
        'indicador_emoji': indicador_emoji.get(dto.indicador_sap, '—'),
        'indicador_tooltip': dto.indicador_sap_tooltip,
        'status_label': dto.status_label,
        'status_classe': dto.status_classe,
        'ultima_origem_label': dto.ultima_origem_label or '—',
        'ultimo_usuario': dto.ultimo_usuario or '—',
        'ultima_data': ultima_data or '—',
    }


class CiclicoListView(RequerNaoOperadorMixin, AcessoOperacionalMixin, View):
    template_name = 'inventario/ciclico.html'

    def get(self, request):
        ciclo = obter_ciclo_atual(request.session)
        info_sap = obter_info_sap_para_ciclo()
        return render(request, self.template_name, {
            'ciclo': ciclo,
            'resumo': obter_resumo_ciclico(),
            'info_sap': info_sap,
            'pode_escrever': usuario_pode_escrever_inventario(request.user),
            'pode_administrar': usuario_pode_escrever_cadastros(request.user),
        })

    def post(self, request):
        if request.POST.get('acao') in ('reabrir', 'arquivar'):
            if not usuario_pode_escrever_cadastros(request.user):
                messages.error(request, 'Somente administradores podem gerenciar ciclos encerrados.')
                return redirect('ciclico')
            ciclo_id_raw = request.POST.get('ciclo_id', '').strip()
            if not ciclo_id_raw.isdigit():
                messages.error(request, 'Ciclo inválido.')
                return redirect('ciclico')
            try:
                if request.POST.get('acao') == 'reabrir':
                    ciclo = reabrir_ciclo(int(ciclo_id_raw))
                    messages.success(request, f'Ciclo #{ciclo.pk} reaberto para consulta operacional.')
                else:
                    ciclo = arquivar_ciclo(int(ciclo_id_raw))
                    messages.success(request, f'Ciclo #{ciclo.pk} arquivado.')
            except CiclicoError as exc:
                messages.error(request, str(exc))
            return redirect('ciclico')

        if not usuario_pode_escrever_inventario(request.user):
            messages.error(request, 'Seu perfil não permite esta operação.')
            return redirect('ciclico')

        acao = request.POST.get('acao', '').strip()
        try:
            if acao == 'criar':
                ciclo = criar_ciclo(usuario_criacao=request.user)
                limpar_lote_sessao(request.session)
                messages.success(
                    request,
                    (
                        f'Ciclo #{ciclo.pk} criado com '
                        f'{ciclo.quantidade_skus_planejados} SKU(s) congelados do SAP.'
                    ),
                )
            elif acao == 'encerrar':
                ciclo = encerrar_ciclo()
                limpar_lote_sessao(request.session)
                messages.success(request, f'Ciclo #{ciclo.pk} encerrado com sucesso.')
            else:
                messages.error(request, 'Ação inválida.')
        except CiclicoError as exc:
            messages.error(request, str(exc))

        return redirect('ciclico')


class CiclicoConsultaView(AcessoOperacionalMixin, View):
    template_name = 'inventario/ciclico_consulta.html'

    def get(self, request):
        filtros = _montar_filtros_ciclo(request)
        ciclo = _resolver_ciclo_consulta(request, filtros)
        if ciclo is None:
            messages.warning(request, 'Nenhum ciclo cíclico disponível para consulta.')
            return redirect('ciclico')

        filtros.ciclo_id = ciclo.pk
        query_sem_ciclo = request.GET.copy()
        query_sem_ciclo.pop('ciclo', None)

        return render(request, self.template_name, {
            'ciclo': ciclo,
            'grupos': obter_consulta_agrupada_por_sku(filtros),
            'resumo': obter_resumo_ciclico(ciclo.pk),
            'filtros': filtros,
            'embalagens_disponiveis': obter_embalagens_disponiveis(),
            'ciclos_disponiveis': listar_ciclos_historico(),
            'status_ciclo_opcoes': StatusCiclo.choices,
            'query_string': query_sem_ciclo.urlencode(),
            'status_opcoes': [
                ('PENDENTE', 'Pendente'),
                ('CONTADO', 'Contado'),
                ('DIVERGENTE', 'Divergente'),
                ('RECONTAGEM', 'Recontagem'),
                ('VALIDADO', 'Validado'),
                ('VALIDADO_DIVERGENCIA', 'Validado c/ divergência'),
                ('EXCLUIDO', 'Excluído'),
            ],
            'origem_opcoes': [
                ('POCKET', 'Pocket'),
                ('WEB', 'Web'),
                ('IMPORTACAO', 'Importação'),
                ('RECONTAGEM', 'Recontagem'),
            ],
            'pode_administrar': usuario_pode_escrever_cadastros(request.user),
            'somente_leitura': ciclo.status_ciclo != StatusCiclo.ATIVO,
        })


class CiclicoConsultaContagemView(AcessoOperacionalMixin, View):
    template_name = 'inventario/ciclico_consulta_contagem.html'

    def get(self, request):
        ciclo_raw = request.GET.get('ciclo', '').strip()
        ciclo_id = int(ciclo_raw) if ciclo_raw.isdigit() else None
        ciclo = resolver_ciclo_consulta_contagem(ciclo_id)
        if ciclo is None:
            messages.warning(request, 'Nenhum ciclo cíclico disponível para consulta.')
            return redirect('ciclico')

        sku_raw = request.GET.get('sku_id', '').strip()
        sku_id = int(sku_raw) if sku_raw.isdigit() else None
        termo = request.GET.get('q', '').strip()

        resultado = obter_consulta_contagem(
            ciclo.pk,
            sku_id=sku_id,
            termo=termo,
        )

        return render(request, self.template_name, {
            'ciclo': ciclo,
            'termo': termo,
            'detalhe': resultado.sku,
            'sugestoes': resultado.sugestoes,
            'ciclos_disponiveis': listar_ciclos_historico(),
        })


class CiclicoRelatorioView(AcessoOperacionalMixin, View):
    def get(self, request):
        ciclo_raw = request.GET.get('ciclo', '').strip()
        if not ciclo_raw.isdigit():
            messages.warning(request, 'Selecione um ciclo para gerar o relatório.')
            return redirect('ciclico_consulta')
        try:
            from inventario.services.ciclico_relatorio_pdf import gerar_relatorio_executivo_pdf
            filtros = _montar_filtros_ciclo(request)
            filtros.ciclo_id = int(ciclo_raw)
            return gerar_relatorio_executivo_pdf(int(ciclo_raw), filtros, request.user)
        except ImportError:
            messages.error(
                request,
                'Dependências do PDF não instaladas. Execute: pip install reportlab matplotlib',
            )
            return redirect('ciclico_consulta')
        except CiclicoError as exc:
            messages.error(request, str(exc))
            return redirect('ciclico_consulta')


class CiclicoExportarView(AcessoOperacionalMixin, View):
    def get(self, request):
        ciclo_raw = request.GET.get('ciclo', '').strip()
        ciclo_id = int(ciclo_raw) if ciclo_raw.isdigit() else None
        ciclo = obter_ciclo_consulta(ciclo_id)
        if ciclo is None:
            messages.warning(request, 'Nenhum ciclo selecionado para exportação.')
            return redirect('ciclico_consulta')

        filtros = _montar_filtros_ciclo(request)
        filtros.ciclo_id = ciclo.pk
        premium = request.GET.get('premium', '') == '1'
        try:
            return exportar_ciclo_excel(ciclo.pk, filtros, premium=premium)
        except ValueError as exc:
            messages.error(request, str(exc))
            return redirect('ciclico_consulta')


class CiclicoExecutarView(RequerNaoOperadorMixin, AcessoOperacionalMixin, View):
    template_name = 'inventario/ciclico_execucao.html'

    def get(self, request):
        ciclo = obter_ciclo_atual(request.session)
        if ciclo is None:
            messages.warning(request, 'Crie um ciclo cíclico antes de executar o ciclo.')
            return redirect('ciclico')

        lote_info = obter_lote_execucao_info(request.session)
        lote_ativo = bool(lote_info)
        return render(request, self.template_name, {
            'ciclo': ciclo,
            'skus': (
                obter_skus_ciclo(session=request.session, usuario=request.user)
                if lote_ativo else []
            ),
            'resumo': obter_resumo_ciclico(),
            'lote_ativo': lote_ativo,
            'lote_info': lote_info,
            'motivos_exclusao': MOTIVOS_EXCLUSAO_SKU,
            'pode_escrever': usuario_pode_escrever_inventario(request.user),
            'embalagens_disponiveis': obter_embalagens_disponiveis(),
            'metas_diarias': METAS_DIARIAS_SUGERIDAS,
        })

    def post(self, request):
        if not usuario_pode_escrever_inventario(request.user):
            messages.error(request, 'Seu perfil não permite esta operação.')
            return redirect('ciclico_executar')

        ciclo = obter_ciclo_atual(request.session)
        if ciclo is None:
            messages.warning(request, 'Crie um ciclo cíclico antes de executar o ciclo.')
            return redirect('ciclico')

        acao = request.POST.get('acao', '').strip()

        try:
            if acao == 'gerar_lote':
                config = _montar_configuracao_execucao(request)
                lote = gerar_lote_execucao(
                    request.session,
                    config,
                    usuario=request.user,
                )
                messages.success(
                    request,
                    f'Lote gerado com {len(lote)} SKU(s) para execução.',
                )
            elif acao == 'excluir':
                sku_id_raw = request.POST.get('sku_id', '').strip()
                if not sku_id_raw.isdigit():
                    raise CiclicoError('SKU inválido.')
                motivo = request.POST.get('motivo_exclusao', '').strip()
                excluir_sku_do_ciclo(int(sku_id_raw), motivo, request.user)
                messages.success(request, 'SKU removido do ciclo.')
            elif acao == 'recontagem':
                sku_id_raw = request.POST.get('sku_id', '').strip()
                if not sku_id_raw.isdigit():
                    raise CiclicoError('SKU inválido.')
                sku = CicloInventarioSku.objects.prefetch_related('posicoes').get(
                    pk=int(sku_id_raw),
                )
                contagens = _montar_contagens_recontagem(request, sku)
                limpar_recontagem_pocket_itens(
                    request.session,
                    list(sku.posicoes.values_list('pk', flat=True)),
                )
                salvar_contagem_sku(
                    sku.pk,
                    contagens,
                    request.user,
                    recontagem=True,
                    origem_contagem=OrigemContagem.RECONTAGEM,
                )
                messages.success(request, 'Recontagem registrada e consolidada.')
            else:
                messages.error(request, 'Ação inválida.')
        except CiclicoError as exc:
            messages.error(request, str(exc))

        return redirect('ciclico_executar')


class CiclicoSkuDetalheView(AcessoOperacionalMixin, View):
    template_name = 'inventario/partials/ciclico_sku_detalhe.html'

    def get(self, request, sku_id):
        try:
            sku = obter_sku_detalhe(sku_id)
        except CiclicoError as exc:
            messages.error(request, str(exc))
            return redirect('ciclico_executar')
        sku_model = CicloInventarioSku.objects.select_related('ciclo').get(pk=sku_id)
        historico_ordenado = sorted(sku.historico, key=lambda linha: linha.data_hora)
        return render(request, self.template_name, {
            'sku': sku,
            'historico_ordenado': historico_ordenado,
            'ciclo': obter_ciclo_atual(request.session),
            'pode_editar': usuario_pode_editar_contagem_ciclico(request.user, sku_model),
        })


class CiclicoSkuEditarView(RequerEscritaInventarioMixin, View):
    template_name = 'inventario/partials/ciclico_sku_editar.html'

    def get(self, request, sku_id):
        try:
            sku = obter_sku_detalhe(sku_id)
        except CiclicoError as exc:
            messages.error(request, str(exc))
            return redirect('ciclico_executar')

        sku_model = CicloInventarioSku.objects.select_related('ciclo').prefetch_related('posicoes').get(pk=sku_id)
        if not usuario_pode_editar_contagem_ciclico(request.user, sku_model):
            messages.error(
                request,
                MSG_CICLO_ENCERRADO if not sku_model.ciclo.ativo else (
                    'Sem permissão para editar esta contagem.'
                ),
            )
            return redirect('ciclico_executar')

        posicoes_ativas = Posicao.objects.filter(ativo=True).order_by('codigo')
        return render(request, self.template_name, {
            'sku': sku,
            'posicoes_ativas': posicoes_ativas,
        })

    def post(self, request, sku_id):
        sku_model = CicloInventarioSku.objects.select_related('ciclo').prefetch_related('posicoes').get(pk=sku_id)
        motivo = request.POST.get('motivo_edicao', '').strip()
        ajax = _requisicao_ajax(request)
        try:
            if not motivo:
                raise CiclicoError('Informe o motivo da alteração.')
            edicoes = _montar_edicoes_contagem(request, sku_model)
            dto = editar_contagem_ciclico(sku_id, edicoes, motivo, request.user)
            if ajax:
                return JsonResponse({
                    'ok': True,
                    'message': 'Contagem atualizada com histórico registrado.',
                    'sku': _serializar_sku_linha_execucao(dto),
                })
            messages.success(request, 'Contagem atualizada com histórico registrado.')
        except CiclicoError as exc:
            if ajax:
                return JsonResponse({'ok': False, 'message': str(exc)}, status=400)
            messages.error(request, str(exc))
        except CicloInventarioSku.DoesNotExist:
            if ajax:
                return JsonResponse({'ok': False, 'message': 'SKU não encontrado.'}, status=404)
            messages.error(request, 'SKU não encontrado.')
        return redirect('ciclico_executar')


class CiclicoSkuExcluirView(RequerEscritaInventarioMixin, View):
    template_name = 'inventario/partials/ciclico_sku_excluir.html'

    def get(self, request, sku_id):
        try:
            sku = obter_sku_detalhe(sku_id)
        except CiclicoError as exc:
            messages.error(request, str(exc))
            return redirect('ciclico_executar')

        sku_model = CicloInventarioSku.objects.select_related('ciclo').get(pk=sku_id)
        if not usuario_pode_excluir_sku_ciclico(request.user, sku_model):
            messages.error(
                request,
                MSG_CICLO_ENCERRADO if not sku_model.ciclo.ativo else (
                    'Sem permissão para excluir este SKU.'
                ),
            )
            return redirect('ciclico_executar')

        return render(request, self.template_name, {
            'sku': sku,
            'motivos_exclusao': MOTIVOS_EXCLUSAO_SKU,
        })

    def post(self, request, sku_id):
        sku_model = CicloInventarioSku.objects.select_related('ciclo').get(pk=sku_id)
        motivo = request.POST.get('motivo_exclusao', '').strip()
        try:
            if not usuario_pode_excluir_sku_ciclico(request.user, sku_model):
                raise CiclicoError(
                    MSG_CICLO_ENCERRADO if not sku_model.ciclo.ativo else (
                        'Sem permissão para excluir este SKU.'
                    ),
                )
            excluir_sku_do_ciclo(sku_id, motivo, request.user)
            messages.success(request, 'SKU removido do ciclo.')
        except CiclicoError as exc:
            messages.error(request, str(exc))
        except CicloInventarioSku.DoesNotExist:
            messages.error(request, 'SKU não encontrado.')
        return redirect('ciclico_executar')


class CiclicoContagemSkuView(RequerEscritaInventarioMixin, View):
    """Compatibilidade com URL legada de contagem/recontagem por SKU."""

    def get(self, request, sku_id):
        ciclo = obter_ciclo_atual()
        if ciclo is None:
            return redirect('ciclico')
        return redirect(f'{reverse("ciclico_executar")}?sku={sku_id}')

    def post(self, request, sku_id):
        ciclo = obter_ciclo_atual()
        if ciclo is None:
            return redirect('ciclico')

        try:
            sku = CicloInventarioSku.objects.prefetch_related('posicoes').get(pk=sku_id)
            recontagem = request.POST.get('recontagem') == '1'
            contagens = _montar_contagens_recontagem(request, sku)
            salvar_contagem_sku(
                sku.pk,
                contagens,
                request.user,
                recontagem=recontagem,
                origem_contagem=(
                    OrigemContagem.RECONTAGEM if recontagem else OrigemContagem.WEB
                ),
            )
            messages.success(
                request,
                'Recontagem registrada.' if recontagem else 'Contagem registrada.',
            )
        except (CicloInventarioSku.DoesNotExist, CiclicoError) as exc:
            messages.error(request, str(exc))

        return redirect(f'{reverse("ciclico_executar")}?sku={sku_id}')


class CiclicoHistoricoView(AcessoOperacionalMixin, View):
    def get(self, request):
        params = request.GET.urlencode()
        destino = reverse('historico_unificado')
        if params:
            query = request.GET.copy()
            if not query.get('tipo'):
                query['tipo'] = 'CICLICO'
            destino = f'{destino}?{query.urlencode()}'
        else:
            destino = f'{destino}?tipo=CICLICO'
        return redirect(destino)


class CiclicoHistoricoDetalheView(AcessoOperacionalMixin, View):
    def get(self, request, ciclo_id: int):
        return redirect('historico_detalhe', tipo='CICLICO', pk=ciclo_id)


class CiclicoHistoricoAuditoriaView(AcessoOperacionalMixin, View):
    template_name = 'inventario/ciclico_historico_auditoria.html'

    def get(self, request, ciclo_id: int):
        detalhe, secoes = obter_auditoria_historico_ciclo(ciclo_id)
        if detalhe is None:
            messages.error(request, 'Ciclo não encontrado.')
            return redirect('ciclico_historico')
        return render(request, self.template_name, {
            'detalhe': detalhe,
            'secoes': secoes,
        })

