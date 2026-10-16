"""Telas do Comparativo de contagens (C1, C2 e C3 opcional)."""

from urllib.parse import urlencode

from django import forms
from django.contrib import messages
from django.core.paginator import Paginator
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View

from accounts.mixins import RequerEscritaInventarioMixin, RequerNaoOperadorMixin
from accounts.services.perfil import usuario_pode_escrever_inventario
from inventario.models import Inventario
from inventario.models_comparativo import ComparativoContagem, ReferenciaSAP
from inventario.services.comparativo_contagens import (
    FUSO_ROTULO,
    OPCAO_NOVA_REFERENCIA,
    ORDENS,
    Canal,
    ComparativoError,
    FiltrosComparativo,
    StatusContagem,
    StatusSAP,
    Visao,
    analisar,
    formatar_quantidade,
    obter_ultima_importacao_sap,
    excluir_comparativo,
    finalizar_comparativo,
    pode_reabrir_comparativo,
    reabrir_comparativo,
    salvar_acuracidade,
    salvar_comparativo,
    sincronizar_referencia_sap,
    versao_dados,
    NOME_CANAL,
)
from inventario.services.comparativo_exportacao import resposta_excel_comparativo

LINHAS_POR_PAGINA = 50


def _rotulo_inventario(inventario: Inventario) -> str:
    return (
        f'#{inventario.pk} · {inventario.get_status_display()} · '
        f'{inventario.usuario.nome} · {timezone.localtime(inventario.data_criacao):%d/%m/%Y}'
    )


class ComparativoForm(forms.Form):
    nome = forms.CharField(label='Nome', max_length=150)
    c1 = forms.ModelChoiceField(label='C1 · primeira contagem', queryset=Inventario.objects.none())
    c2 = forms.ModelChoiceField(
        label='C2 · segunda contagem', queryset=Inventario.objects.none(), required=False,
    )
    c3 = forms.ModelChoiceField(
        label='C3 · terceira contagem (opcional)', queryset=Inventario.objects.none(), required=False,
    )
    referencia_sap = forms.ChoiceField(label='Referência SAP', required=False)

    def __init__(self, *args, comparativo=None, **kwargs):
        super().__init__(*args, **kwargs)
        inventarios = Inventario.objects.select_related('usuario').order_by('-data_criacao')
        for campo in ('c1', 'c2', 'c3'):
            self.fields[campo].queryset = inventarios
            self.fields[campo].label_from_instance = _rotulo_inventario
        opcoes = []
        if comparativo is not None:
            opcoes.append(('', 'Manter a referência atual'))
        ultima = obter_ultima_importacao_sap()
        if ultima is not None:
            opcoes.append((
                OPCAO_NOVA_REFERENCIA,
                f'Fotografar base SAP vigente ({ultima.arquivo_origem} · '
                f'{timezone.localtime(ultima.data_importacao):%d/%m/%Y %H:%M})',
            ))
        for referencia in ReferenciaSAP.objects.order_by('-data_captura')[:30]:
            opcoes.append((str(referencia.pk), f'Fotografia #{referencia.pk} · {referencia.descricao}'))
        if comparativo is None:
            opcoes.append(('', 'Sem referência SAP'))
        self.fields['referencia_sap'].choices = opcoes
        for campo in self.fields.values():
            css = 'form-select' if isinstance(campo.widget, forms.Select) else 'form-control'
            campo.widget.attrs['class'] = css


class ComparativoAcessoMixin(RequerNaoOperadorMixin):
    """Leitura: perfis com acesso operacional, exceto operador Pocket."""


class ComparativoListaView(ComparativoAcessoMixin, View):
    template_name = 'inventario/comparativo/lista.html'

    def get(self, request):
        comparativos = (
            ComparativoContagem.objects
            .select_related(
                'referencia_sap', 'usuario_criacao', 'usuario_criacao__perfil_operacional',
                'usuario_finalizacao', 'usuario_finalizacao__perfil_operacional',
            )
            .prefetch_related('associacoes__inventario', 'acuracidades')
            .order_by('-data_criacao')
        )
        pagina = Paginator(comparativos, 20).get_page(request.GET.get('page'))
        for comparativo in pagina:
            comparativo.contagens_ordenadas = sorted(
                comparativo.associacoes.all(), key=lambda a: a.ordem,
            )
            comparativo.ultima_acuracidade = next(iter(comparativo.acuracidades.all()), None)
            comparativo.pode_reabrir = pode_reabrir_comparativo(comparativo)
        return render(request, self.template_name, {
            'page_obj': pagina,
            'comparativos': pagina,
            'pode_editar': usuario_pode_escrever_inventario(request.user),
            'rotulos_canal': Canal.ROTULOS,
        })


class ComparativoFormularioView(RequerEscritaInventarioMixin, ComparativoAcessoMixin, View):
    template_name = 'inventario/comparativo/formulario.html'

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not usuario_pode_escrever_inventario(request.user):
            messages.error(request, 'Seu perfil não pode configurar conferências.')
            return redirect('comparativo:lista')
        return super().dispatch(request, *args, **kwargs)

    def _comparativo(self):
        pk = self.kwargs.get('pk')
        return get_object_or_404(ComparativoContagem, pk=pk) if pk else None

    def _bloquear_finalizado(self, request, comparativo):
        if comparativo is not None and comparativo.finalizado:
            messages.error(request, 'Conferência salva e finalizada não pode ser alterada.')
            return redirect('comparativo:acompanhamento', pk=comparativo.pk)
        return None

    def _inicial(self, comparativo):
        if comparativo is None:
            return {'referencia_sap': OPCAO_NOVA_REFERENCIA}
        inicial = {'nome': comparativo.nome, 'referencia_sap': ''}
        for associacao in comparativo.associacoes.all():
            inicial[f'c{associacao.ordem}'] = associacao.inventario_id
        return inicial

    def get(self, request, pk=None):
        comparativo = self._comparativo()
        bloqueio = self._bloquear_finalizado(request, comparativo)
        if bloqueio:
            return bloqueio
        form = ComparativoForm(initial=self._inicial(comparativo), comparativo=comparativo)
        return self._render(request, form, comparativo)

    def post(self, request, pk=None):
        comparativo = self._comparativo()
        bloqueio = self._bloquear_finalizado(request, comparativo)
        if bloqueio:
            return bloqueio
        form = ComparativoForm(request.POST, comparativo=comparativo)
        if form.is_valid():
            dados = form.cleaned_data
            try:
                comparativo = salvar_comparativo(
                    comparativo=comparativo,
                    nome=dados['nome'],
                    inventarios={o: dados.get(f'c{o}') for o in ORDENS},
                    referencia_opcao=dados.get('referencia_sap') or '',
                    estado=None,
                    usuario=request.user,
                )
            except ComparativoError as exc:
                form.add_error(None, str(exc))
            else:
                messages.success(request, 'Conferência salva.')
                return redirect('comparativo:acompanhamento', pk=comparativo.pk)
        return self._render(request, form, comparativo)

    def _render(self, request, form, comparativo):
        return render(request, self.template_name, {
            'form': form,
            'comparativo': comparativo,
            'eventos': comparativo.eventos.select_related('usuario')[:20] if comparativo else [],
        })


def _parametros(request):
    visao = Visao.normalizar(request.GET.get('visao'))
    canal = Canal.normalizar(request.GET.get('canal'))
    filtros = FiltrosComparativo.de_parametros(request.GET, visao)
    return canal, visao, filtros


def _querystring(canal_atual, visao_atual, filtros, **substituir) -> str:
    params = {
        'canal': canal_atual,
        'visao': visao_atual,
        'q': filtros.termo,
        'status_contagem': filtros.status_contagem,
        'status_sap': filtros.status_sap,
        'necessita_c3': '1' if filtros.necessita_c3 else '',
    }
    params.update(substituir)
    if params.get('visao') == Visao.POSICAO:
        params['status_sap'] = ''
    return urlencode({k: v for k, v in params.items() if v})


class ComparativoAcompanhamentoView(ComparativoAcessoMixin, View):
    template_name = 'inventario/comparativo/acompanhamento.html'
    template_parcial = 'inventario/comparativo/_resultado.html'

    def get(self, request, pk):
        comparativo = get_object_or_404(
            ComparativoContagem.objects.select_related(
                'referencia_sap', 'usuario_criacao',
                'usuario_finalizacao', 'usuario_finalizacao__perfil_operacional',
            ),
            pk=pk,
        )
        comparativo.pode_reabrir = pode_reabrir_comparativo(comparativo)
        canal, visao, filtros = _parametros(request)
        parcial = request.GET.get('parcial') == '1'
        forcar = request.GET.get('forcar') == '1'
        # Conferência aberta acompanha a importação SAP concluída mais recente
        # (troca auditada; fotografia anterior preservada).
        _alterou, aviso_sap = sincronizar_referencia_sap(
            comparativo, request.user, motivo='atualizar' if forcar else 'automatica',
        )
        versao = versao_dados(comparativo)
        if parcial and not forcar and request.GET.get('versao') == versao:
            # Nada mudou desde a última resposta: evita recalcular e redesenhar.
            resposta = HttpResponse(status=204)
            resposta['X-Comparativo-Versao'] = versao
            resposta['Cache-Control'] = 'no-store'
            return resposta
        resultado = analisar(comparativo, canal=canal, visao=visao, filtros=filtros)
        pagina = Paginator(resultado.linhas, LINHAS_POR_PAGINA).get_page(request.GET.get('page'))
        dados = resultado.dados
        contexto = {
            'versao': versao,
            'aviso_sap': aviso_sap,
            'canais_status': [
                (NOME_CANAL[chave], dados.canal_carregado(chave)) for chave in ('cosan', 'brida')
            ],
            'motivo_sap': dados.motivo_sap(canal),
            'comparativo': comparativo,
            'resultado': resultado,
            'page_obj': pagina,
            'linhas': pagina.object_list,
            'canal': canal,
            'visao': visao,
            'filtros': filtros,
            'ordens': [o for o in ORDENS if o in resultado.dados.ordens_associadas or o in (1, 2)],
            'contagens': [resultado.dados.contagens.get(o) for o in ORDENS],
            'colunas_sap': resultado.colunas_sap,
            'canais': [(valor, Canal.ROTULOS[valor]) for valor in (Canal.COSAN, Canal.BRIDA, Canal.AMBAS)],
            'status_contagem_opcoes': list(StatusContagem.ROTULOS.items()),
            'status_sap_opcoes': list(StatusSAP.ROTULOS.items()),
            'querystring': _querystring(canal, visao, filtros),
            'qs_canal': {
                valor: _querystring(canal, visao, filtros, canal=valor)
                for valor in (Canal.COSAN, Canal.BRIDA, Canal.AMBAS)
            },
            'qs_visao': {
                valor: _querystring(canal, visao, filtros, visao=valor)
                for valor in (Visao.SKU, Visao.POSICAO)
            },
            'fuso_rotulo': FUSO_ROTULO,
            'atualizado_em': timezone.localtime(resultado.dados.gerado_em),
            'pode_editar': usuario_pode_escrever_inventario(request.user),
            'acuracidades_salvas': list(
                comparativo.acuracidades.select_related('usuario', 'usuario__perfil_operacional')[:10]
            ),
            'rotulos_canal': Canal.ROTULOS,
        }
        if parcial:
            resposta = render(request, self.template_parcial, contexto)
            resposta['Cache-Control'] = 'no-store'
            resposta['X-Comparativo-Versao'] = versao
            return resposta
        return render(request, self.template_name, contexto)


class ComparativoAcuracidadeSalvarView(RequerEscritaInventarioMixin, ComparativoAcessoMixin, View):
    """Grava o cálculo atual da acuracidade do canal como registro histórico."""

    def post(self, request, pk):
        comparativo = get_object_or_404(ComparativoContagem, pk=pk)
        canal = Canal.normalizar(request.POST.get('canal'))
        destino = (
            reverse('comparativo:acompanhamento', args=[pk])
            + '?' + urlencode({'canal': canal, 'visao': Visao.SKU})
        )
        if not usuario_pode_escrever_inventario(request.user):
            messages.error(request, 'Seu perfil não pode salvar a acuracidade.')
            return redirect(destino)
        try:
            registro = salvar_acuracidade(comparativo, canal, request.user)
        except ComparativoError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(
                request,
                f'Acuracidade {Canal.ROTULOS[canal]} salva: {formatar_quantidade(registro.percentual)}% '
                f'({registro.skus_acertos} de {registro.skus_avaliados} SKUs).',
            )
        return redirect(destino)


class ComparativoFinalizarView(RequerEscritaInventarioMixin, ComparativoAcessoMixin, View):
    """Salva (grava o resultado atual) e finaliza a conferência."""

    def post(self, request, pk):
        comparativo = get_object_or_404(ComparativoContagem, pk=pk)
        if not usuario_pode_escrever_inventario(request.user):
            messages.error(request, 'Seu perfil não pode salvar a conferência.')
            return redirect('comparativo:lista')
        try:
            finalizar_comparativo(comparativo, request.user)
        except ComparativoError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(
                request,
                f'Conferência "{comparativo.nome}" salva e finalizada. O resultado ficou gravado.',
            )
        destino = request.POST.get('voltar') or ''
        if destino == 'lista':
            return redirect('comparativo:lista')
        return redirect('comparativo:acompanhamento', pk=pk)


class ComparativoReabrirView(RequerEscritaInventarioMixin, ComparativoAcessoMixin, View):
    """Descarta a finalização de teste e devolve a conferência ao estado aberto."""

    def post(self, request, pk):
        comparativo = get_object_or_404(ComparativoContagem, pk=pk)
        if not usuario_pode_escrever_inventario(request.user):
            messages.error(request, 'Seu perfil não pode reabrir a conferência.')
            return redirect('comparativo:lista')
        try:
            reabrir_comparativo(comparativo, request.user)
        except ComparativoError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(
                request,
                (
                    f'Conferência "{comparativo.nome}" reaberta. '
                    'O resultado gravado foi descartado e os inventários em andamento podem ser excluídos.'
                ),
            )
        destino = request.POST.get('voltar') or ''
        if destino == 'lista':
            return redirect('comparativo:lista')
        return redirect('comparativo:acompanhamento', pk=pk)


class ComparativoExcluirView(RequerEscritaInventarioMixin, ComparativoAcessoMixin, View):
    """Exclui conferência aberta de teste. Não remove inventários de origem."""

    def post(self, request, pk):
        comparativo = get_object_or_404(ComparativoContagem, pk=pk)
        if not usuario_pode_escrever_inventario(request.user):
            messages.error(request, 'Seu perfil não pode excluir a conferência.')
            return redirect('comparativo:lista')
        nome = comparativo.nome
        try:
            excluir_comparativo(comparativo, request.user)
        except ComparativoError as exc:
            messages.error(request, str(exc))
            return redirect('comparativo:acompanhamento', pk=pk)
        messages.success(
            request,
            f'Conferência "{nome}" excluída. Os inventários associados foram mantidos.',
        )
        return redirect('comparativo:lista')


class ComparativoExportarView(ComparativoAcessoMixin, View):
    def get(self, request, pk):
        comparativo = get_object_or_404(
            ComparativoContagem.objects.select_related('referencia_sap'), pk=pk,
        )
        canal, _visao, _filtros = _parametros(request)
        filtros = FiltrosComparativo.de_parametros(request.GET, Visao.SKU)
        sincronizar_referencia_sap(comparativo, request.user)
        return resposta_excel_comparativo(
            comparativo, canal=canal, filtros=filtros, usuario=request.user,
        )
