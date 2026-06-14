from django.contrib import messages
from django.db.models import Q
from django.http import HttpResponseRedirect
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import CreateView, DeleteView, ListView, UpdateView

from core.logging_auditoria import registrar_evento
from accounts.mixins import (
    AcessoOperacionalMixin,
    PaginacaoContextMixin,
    PaginacaoMixin,
    RequerEscritaCadastroMixin,
)
from posicoes.forms import PosicaoForm, PosicaoImportacaoForm
from posicoes.models import Posicao
from posicoes.services.exclusao import (
    MENSAGEM_SUCESSO_COM_VINCULOS,
    ExclusaoPosicaoError,
    excluir_posicao_com_vinculos,
)
from posicoes.services.importacao_posicoes import (
    importar_dados,
    processar_arquivo,
    serializar_linhas_validas,
)

SESSION_PREVIEW_KEY = 'importacao_posicoes_preview'
SESSION_REJEITADOS_KEY = 'importacao_posicoes_rejeitados'


class PosicaoListView(AcessoOperacionalMixin, PaginacaoMixin, PaginacaoContextMixin, ListView):
    model = Posicao
    template_name = 'posicoes/lista.html'
    context_object_name = 'posicoes'

    def get_queryset(self):
        queryset = super().get_queryset()
        termo = self.request.GET.get('q', '').strip()

        if termo:
            queryset = queryset.filter(
                Q(codigo__icontains=termo) | Q(posicao__icontains=termo)
            )

        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['termo_busca'] = self.request.GET.get('q', '')
        return context


class PosicaoCreateView(RequerEscritaCadastroMixin, CreateView):
    model = Posicao
    form_class = PosicaoForm
    template_name = 'posicoes/formulario.html'
    success_url = reverse_lazy('posicoes:lista')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['titulo'] = 'Nova Posição'
        context['botao'] = 'Salvar'
        return context

    def form_valid(self, form):
        messages.success(self.request, 'Cadastro realizado.')
        return super().form_valid(form)

    def form_invalid(self, form):
        messages.error(self.request, 'Falha ao salvar.')
        return super().form_invalid(form)


class PosicaoUpdateView(RequerEscritaCadastroMixin, UpdateView):
    model = Posicao
    form_class = PosicaoForm
    template_name = 'posicoes/formulario.html'
    success_url = reverse_lazy('posicoes:lista')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['titulo'] = 'Editar Posição'
        context['botao'] = 'Atualizar'
        return context

    def form_valid(self, form):
        messages.success(self.request, 'Alteração realizada.')
        return super().form_valid(form)

    def form_invalid(self, form):
        messages.error(self.request, 'Falha ao salvar.')
        return super().form_invalid(form)


class PosicaoDeleteView(RequerEscritaCadastroMixin, DeleteView):
    model = Posicao
    template_name = 'posicoes/excluir.html'
    success_url = reverse_lazy('posicoes:lista')

    def form_valid(self, form):
        try:
            resultado = excluir_posicao_com_vinculos(
                self.get_object(),
                usuario=self.request.user,
            )
        except ExclusaoPosicaoError as exc:
            messages.error(self.request, str(exc))
            return redirect(self.success_url)

        if resultado.houve_vinculos:
            messages.success(self.request, MENSAGEM_SUCESSO_COM_VINCULOS)
        else:
            messages.success(self.request, 'Exclusão realizada.')
        return HttpResponseRedirect(self.get_success_url())


class PosicaoImportarView(RequerEscritaCadastroMixin, View):
    template_name = 'posicoes/importar.html'

    def get(self, request):
        self._limpar_sessao(request)
        return render(request, self.template_name, {
            'etapa': 'upload',
            'form': PosicaoImportacaoForm(),
        })

    def post(self, request):
        acao = request.POST.get('acao')

        if acao == 'cancelar':
            self._limpar_sessao(request)
            messages.info(request, 'Importação cancelada.')
            return redirect('posicoes:lista')

        if acao == 'confirmar':
            return self._confirmar_importacao(request)

        return self._processar_upload(request)

    def _processar_upload(self, request):
        form = PosicaoImportacaoForm(request.POST, request.FILES)

        if not form.is_valid():
            messages.error(request, 'Arquivo inválido.')
            return render(request, self.template_name, {
                'etapa': 'upload',
                'form': form,
            })

        try:
            preview = processar_arquivo(form.cleaned_data['arquivo'])
        except (ValueError, Exception):
            messages.error(request, 'Arquivo inválido.')
            return render(request, self.template_name, {
                'etapa': 'upload',
                'form': PosicaoImportacaoForm(),
            })

        if preview.total_linhas == 0:
            messages.error(request, 'Nenhum registro encontrado.')
            return render(request, self.template_name, {
                'etapa': 'upload',
                'form': PosicaoImportacaoForm(),
            })

        request.session[SESSION_PREVIEW_KEY] = serializar_linhas_validas(preview)
        request.session[SESSION_REJEITADOS_KEY] = preview.linhas_invalidas

        return render(request, self.template_name, {
            'etapa': 'preview',
            'preview': preview,
        })

    def _confirmar_importacao(self, request):
        linhas_validas = request.session.get(SESSION_PREVIEW_KEY)
        rejeitados = request.session.get(SESSION_REJEITADOS_KEY, 0)

        if not linhas_validas:
            messages.error(request, 'Nenhum registro encontrado.')
            return redirect('posicoes:importar')

        resultado = importar_dados(linhas_validas, rejeitados=rejeitados)
        registrar_evento(
            'importacao_posicoes',
            usuario=request.user,
            inseridos=resultado.inseridos,
            atualizados=resultado.atualizados,
            rejeitados=resultado.rejeitados,
        )
        self._limpar_sessao(request)

        if resultado.rejeitados > 0:
            messages.warning(request, 'Importação concluída parcialmente.')
        else:
            messages.success(request, 'Importação concluída com sucesso.')

        return render(request, self.template_name, {
            'etapa': 'resultado',
            'resultado': resultado,
        })

    def _limpar_sessao(self, request):
        request.session.pop(SESSION_PREVIEW_KEY, None)
        request.session.pop(SESSION_REJEITADOS_KEY, None)
