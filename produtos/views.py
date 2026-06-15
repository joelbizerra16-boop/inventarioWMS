import logging

from django.contrib import messages
from django.db.models import Q
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import CreateView, DeleteView, ListView, UpdateView

from accounts.mixins import (
    AcessoOperacionalMixin,
    PaginacaoContextMixin,
    PaginacaoMixin,
    RequerEscritaCadastroMixin,
)
from core.logging_auditoria import registrar_evento
from core.mixins import ExclusaoSeguraMixin
from produtos.forms import ProdutoForm, ProdutoImportacaoForm
from produtos.models import Produto
from produtos.services.importacao_produtos import (
    ImportacaoProdutosError,
    LinhaImportacao,
    ResultadoPreview,
    importar_dados,
    processar_arquivo,
    serializar_linhas_validas,
)

logger = logging.getLogger(__name__)

SESSION_PREVIEW_KEY = 'importacao_produtos_preview'
SESSION_REJEITADOS_KEY = 'importacao_produtos_rejeitados'


class ProdutoListView(AcessoOperacionalMixin, PaginacaoMixin, PaginacaoContextMixin, ListView):
    model = Produto
    template_name = 'produtos/lista.html'
    context_object_name = 'produtos'

    def get_queryset(self):
        queryset = super().get_queryset()
        termo = self.request.GET.get('q', '').strip()

        if termo:
            queryset = queryset.filter(
                Q(codigo_produto__icontains=termo)
                | Q(descricao__icontains=termo)
                | Q(embalagem__icontains=termo)
                | Q(setor__icontains=termo)
                | Q(codigo_ean__icontains=termo)
            )

        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['termo_busca'] = self.request.GET.get('q', '')
        return context


class ProdutoCreateView(RequerEscritaCadastroMixin, CreateView):
    model = Produto
    form_class = ProdutoForm
    template_name = 'produtos/formulario.html'
    success_url = reverse_lazy('produtos:lista')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['titulo'] = 'Novo Produto'
        context['botao'] = 'Salvar'
        return context

    def form_valid(self, form):
        messages.success(self.request, 'Cadastro realizado.')
        return super().form_valid(form)

    def form_invalid(self, form):
        messages.error(self.request, 'Falha ao salvar.')
        return super().form_invalid(form)


class ProdutoUpdateView(RequerEscritaCadastroMixin, UpdateView):
    model = Produto
    form_class = ProdutoForm
    template_name = 'produtos/formulario.html'
    success_url = reverse_lazy('produtos:lista')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['titulo'] = 'Editar Produto'
        context['botao'] = 'Atualizar'
        return context

    def form_valid(self, form):
        messages.success(self.request, 'Alteração realizada.')
        return super().form_valid(form)

    def form_invalid(self, form):
        messages.error(self.request, 'Falha ao salvar.')
        return super().form_invalid(form)


class ProdutoDeleteView(RequerEscritaCadastroMixin, ExclusaoSeguraMixin, DeleteView):
    model = Produto
    template_name = 'produtos/excluir.html'
    success_url = reverse_lazy('produtos:lista')
    mensagem_exclusao_sucesso = 'Exclusão realizada.'


class ProdutoImportarView(RequerEscritaCadastroMixin, View):
    template_name = 'produtos/importar.html'

    def get(self, request):
        preview = self._preview_da_sessao(request)
        if preview is not None:
            return render(request, self.template_name, {
                'etapa': 'preview',
                'preview': preview,
            })
        return render(request, self.template_name, {
            'etapa': 'upload',
            'form': ProdutoImportacaoForm(),
        })

    def post(self, request):
        acao = request.POST.get('acao')

        if acao == 'cancelar':
            self._limpar_sessao(request)
            messages.info(request, 'Importação cancelada.')
            return redirect('produtos:lista')

        if acao == 'confirmar':
            return self._confirmar_importacao(request)

        return self._processar_upload(request)

    def _mensagem_erro_formulario(self, form: ProdutoImportacaoForm) -> str:
        erros_arquivo = form.errors.get('arquivo')
        if not erros_arquivo:
            return str(next(iter(form.errors.values()))[0])

        for erro in erros_arquivo:
            if getattr(erro, 'code', '') == 'invalid_extension':
                return 'Arquivo não é XLSX.'
            texto = str(erro)
            if 'xlsx' in texto.lower() or 'xls' in texto.lower():
                return 'Arquivo não é XLSX.'
        return str(erros_arquivo[0])

    def _preview_da_sessao(self, request) -> ResultadoPreview | None:
        linhas_validas = request.session.get(SESSION_PREVIEW_KEY)
        if not linhas_validas:
            return None

        rejeitados = request.session.get(SESSION_REJEITADOS_KEY, 0)
        linhas = [
            LinhaImportacao(
                linha=indice + 1,
                codigo_produto=dados['codigo_produto'],
                descricao=dados['descricao'],
                embalagem=dados['embalagem'],
                setor=dados['setor'],
                codigo_ean=dados.get('codigo_ean', ''),
                valida=True,
            )
            for indice, dados in enumerate(linhas_validas)
        ]
        return ResultadoPreview(
            total_linhas=len(linhas) + rejeitados,
            linhas_validas=len(linhas),
            linhas_invalidas=rejeitados,
            linhas=linhas,
        )

    def _processar_upload(self, request):
        self._limpar_sessao(request)
        form = ProdutoImportacaoForm(request.POST, request.FILES)

        if not form.is_valid():
            messages.error(request, self._mensagem_erro_formulario(form))
            return render(request, self.template_name, {
                'etapa': 'upload',
                'form': form,
            })

        try:
            preview = processar_arquivo(form.cleaned_data['arquivo'])
        except ImportacaoProdutosError as exc:
            messages.error(request, exc.mensagem)
            return render(request, self.template_name, {
                'etapa': 'upload',
                'form': ProdutoImportacaoForm(),
            })

        if preview.total_linhas == 0:
            messages.error(request, 'Planilha vazia.')
            return render(request, self.template_name, {
                'etapa': 'upload',
                'form': ProdutoImportacaoForm(),
            })

        linhas_sessao = serializar_linhas_validas(preview)
        request.session[SESSION_PREVIEW_KEY] = linhas_sessao
        request.session[SESSION_REJEITADOS_KEY] = preview.linhas_invalidas
        request.session.modified = True
        logger.info('IMPORTACAO PREVIEW registros=%s', len(linhas_sessao))

        return render(request, self.template_name, {
            'etapa': 'preview',
            'preview': preview,
        })

    def _confirmar_importacao(self, request):
        linhas_validas = request.session.get(SESSION_PREVIEW_KEY)
        rejeitados = request.session.get(SESSION_REJEITADOS_KEY, 0)

        logger.info('IMPORTACAO CONFIRMAR registros=%s', len(linhas_validas or []))

        if not linhas_validas:
            messages.error(request, 'Nenhum registro encontrado.')
            return redirect('produtos:importar')

        resultado = importar_dados(linhas_validas, rejeitados=rejeitados)
        logger.info(
            'IMPORTACAO RESULTADO inseridos=%s atualizados=%s',
            resultado.inseridos,
            resultado.atualizados,
        )
        self._limpar_sessao(request)
        registrar_evento(
            'importacao_produtos',
            usuario=request.user,
            inseridos=resultado.inseridos,
            atualizados=resultado.atualizados,
            rejeitados=resultado.rejeitados,
        )

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
