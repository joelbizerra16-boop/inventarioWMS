import json
import logging
import time

from django.contrib import messages
from django.db.models import Q
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import ListView

from accounts.mixins import (
    AcessoOperacionalMixin,
    PaginacaoContextMixin,
    PaginacaoMixin,
    RequerEscritaInventarioMixin,
)
from core.services.perf_diagnostico import medir_etapa
from estoque_sap.forms import EstoqueSAPImportacaoForm
from estoque_sap.models import EstoqueSAP
from estoque_sap.services.importacao_estoque_sap import (
    excluir_linha_preview,
    filtrar_linhas_para_importacao,
    importar_dados,
    linha_permite_validar_produto,
    montar_preview_sessao,
    processar_arquivo,
    serializar_preview_sessao,
    validar_produto_preview,
)

SESSION_LINHAS_KEY = 'importacao_estoque_sap_linhas'
SESSION_COLUNAS_DETECTADAS_KEY = 'importacao_estoque_sap_colunas_detectadas'
SESSION_COLUNAS_NORMALIZADAS_KEY = 'importacao_estoque_sap_colunas_normalizadas'
SESSION_ARQUIVO_KEY = 'importacao_estoque_sap_arquivo'
logger = logging.getLogger(__name__)


class EstoqueSAPListView(AcessoOperacionalMixin, PaginacaoMixin, PaginacaoContextMixin, ListView):
    model = EstoqueSAP
    template_name = 'estoque_sap/lista.html'
    context_object_name = 'estoques'
    ordering = ['-data_importacao']

    def get_queryset(self):
        queryset = EstoqueSAP.objects.select_related('produto')
        termo = self.request.GET.get('q', '').strip()

        if termo:
            queryset = queryset.filter(
                Q(produto__codigo_produto__icontains=termo)
                | Q(produto__descricao__icontains=termo)
            )

        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['termo_busca'] = self.request.GET.get('q', '')
        return context


class EstoqueSAPImportarView(RequerEscritaInventarioMixin, View):
    template_name = 'estoque_sap/importar.html'
    success_url = reverse_lazy('estoque_sap:lista')

    def get(self, request):
        inicio = time.perf_counter()
        if request.session.get(SESSION_LINHAS_KEY):
            resposta = self._render_preview(request)
            fim = time.perf_counter()
            logger.info('VIEW=%s TEMPO=%.2fs', 'EstoqueSAPImportarView.get', fim - inicio)
            return resposta

        with medir_etapa('estoque_sap.importar.get.render_upload'):
            resposta = render(request, self.template_name, {
                'etapa': 'upload',
                'form': EstoqueSAPImportacaoForm(),
            })
        fim = time.perf_counter()
        logger.info('VIEW=%s TEMPO=%.2fs', 'EstoqueSAPImportarView.get', fim - inicio)
        return resposta

    def post(self, request):
        inicio = time.perf_counter()
        acao = request.POST.get('acao')

        if acao == 'cancelar':
            self._limpar_sessao(request)
            messages.info(request, 'Importação cancelada.')
            resposta = redirect('estoque_sap:lista')
            fim = time.perf_counter()
            logger.info('VIEW=%s TEMPO=%.2fs', 'EstoqueSAPImportarView.post', fim - inicio)
            return resposta

        if acao == 'confirmar':
            resposta = self._confirmar_importacao(request)
            fim = time.perf_counter()
            logger.info('VIEW=%s TEMPO=%.2fs', 'EstoqueSAPImportarView.post', fim - inicio)
            return resposta

        if acao == 'validar_produto':
            resposta = self._validar_produto_preview(request)
            fim = time.perf_counter()
            logger.info('VIEW=%s TEMPO=%.2fs', 'EstoqueSAPImportarView.post', fim - inicio)
            return resposta

        if acao == 'excluir_linha':
            resposta = self._excluir_linha_preview(request)
            fim = time.perf_counter()
            logger.info('VIEW=%s TEMPO=%.2fs', 'EstoqueSAPImportarView.post', fim - inicio)
            return resposta

        resposta = self._processar_upload(request)
        fim = time.perf_counter()
        logger.info('VIEW=%s TEMPO=%.2fs', 'EstoqueSAPImportarView.post', fim - inicio)
        return resposta

    def _processar_upload(self, request):
        self._limpar_sessao(request)
        form = EstoqueSAPImportacaoForm(request.POST, request.FILES)

        if not form.is_valid():
            messages.error(request, 'Arquivo inválido.')
            return render(request, self.template_name, {
                'etapa': 'upload',
                'form': form,
            })

        arquivo = form.cleaned_data['arquivo']

        try:
            with medir_etapa('estoque_sap.importar.post.processar_arquivo'):
                preview = processar_arquivo(arquivo)
        except (ValueError, Exception):
            messages.error(request, 'Arquivo inválido.')
            return render(request, self.template_name, {
                'etapa': 'upload',
                'form': EstoqueSAPImportacaoForm(),
            })

        if preview.total_linhas == 0:
            messages.error(request, 'Nenhum registro encontrado.')
            return render(request, self.template_name, {
                'etapa': 'upload',
                'form': EstoqueSAPImportacaoForm(),
            })

        with medir_etapa('estoque_sap.importar.post.serializar_preview_sessao'):
            request.session[SESSION_LINHAS_KEY] = serializar_preview_sessao(preview)
        request.session[SESSION_COLUNAS_DETECTADAS_KEY] = preview.colunas_detectadas
        request.session[SESSION_COLUNAS_NORMALIZADAS_KEY] = preview.colunas_normalizadas
        request.session[SESSION_ARQUIVO_KEY] = arquivo.name
        request.session.modified = True
        logger.info(
            'IMPORTACAO_SAP_PREVIEW sessao_bytes=%s registros=%s',
            len(json.dumps(request.session[SESSION_LINHAS_KEY], ensure_ascii=False).encode('utf-8')),
            len(request.session[SESSION_LINHAS_KEY]),
        )
        logger.info('IMPORTACAO PREVIEW registros=%s', len(request.session[SESSION_LINHAS_KEY]))

        return self._render_preview(request)

    def _validar_produto_preview(self, request):
        linhas = request.session.get(SESSION_LINHAS_KEY)
        if not linhas:
            messages.error(request, 'Nenhum preview ativo. Envie o arquivo novamente.')
            return redirect('estoque_sap:importar')

        try:
            numero_linha = int(request.POST.get('linha', ''))
        except (TypeError, ValueError):
            messages.error(request, 'Linha inválida.')
            return self._render_preview(request)

        linhas = validar_produto_preview(linhas, numero_linha)
        request.session[SESSION_LINHAS_KEY] = linhas
        request.session.modified = True
        messages.success(request, f'Pré-cadastro criado para a linha {numero_linha}.')

        return self._render_preview(request)

    def _excluir_linha_preview(self, request):
        linhas = request.session.get(SESSION_LINHAS_KEY)
        if not linhas:
            messages.error(request, 'Nenhum preview ativo. Envie o arquivo novamente.')
            return redirect('estoque_sap:importar')

        try:
            numero_linha = int(request.POST.get('linha', ''))
        except (TypeError, ValueError):
            messages.error(request, 'Linha inválida.')
            return self._render_preview(request)

        linhas = excluir_linha_preview(linhas, numero_linha)
        request.session[SESSION_LINHAS_KEY] = linhas
        request.session.modified = True
        messages.info(request, f'Linha {numero_linha} removida desta importação.')

        return self._render_preview(request)

    def _render_preview(self, request):
        linhas = request.session.get(SESSION_LINHAS_KEY, [])
        logger.info(
            'IMPORTACAO_SAP_RENDER_PREVIEW sessao_bytes=%s registros=%s',
            len(json.dumps(linhas, ensure_ascii=False).encode('utf-8')),
            len(linhas),
        )
        with medir_etapa('estoque_sap.importar.preview.montar_preview_sessao'):
            preview = montar_preview_sessao(
                linhas,
                request.session.get(SESSION_COLUNAS_DETECTADAS_KEY, []),
                request.session.get(SESSION_COLUNAS_NORMALIZADAS_KEY, []),
            )
        for linha in preview.linhas:
            linha.permite_validar_produto = linha_permite_validar_produto(linha)

        with medir_etapa('estoque_sap.importar.preview.render_html'):
            resposta = render(request, self.template_name, {
                'etapa': 'preview',
                'preview': preview,
            })
        logger.info('IMPORTACAO_SAP_PREVIEW html_bytes=%s', len(resposta.content))
        return resposta

    def _confirmar_importacao(self, request):
        linhas = request.session.get(SESSION_LINHAS_KEY)
        arquivo_origem = request.session.get(SESSION_ARQUIVO_KEY, '')
        logger.info('IMPORTACAO CONFIRMAR registros=%s', len(linhas or []))

        if not linhas:
            messages.error(request, 'Nenhum registro encontrado.')
            return redirect('estoque_sap:importar')

        with medir_etapa('estoque_sap.importar.confirmar.filtrar_linhas'):
            linhas_validas, rejeitados = filtrar_linhas_para_importacao(linhas)

        if not linhas_validas:
            messages.error(request, 'Nenhuma linha válida para importação.')
            return self._render_preview(request)

        with medir_etapa('estoque_sap.importar.confirmar.importar_dados'):
            resultado = importar_dados(
                linhas_validas,
                arquivo_origem=arquivo_origem,
                rejeitados=rejeitados,
            )
        logger.info(
            'IMPORTACAO RESULTADO inseridos=%s atualizados=%s',
            resultado.inseridos,
            resultado.atualizados,
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
        request.session.pop(SESSION_LINHAS_KEY, None)
        request.session.pop(SESSION_COLUNAS_DETECTADAS_KEY, None)
        request.session.pop(SESSION_COLUNAS_NORMALIZADAS_KEY, None)
        request.session.pop(SESSION_ARQUIVO_KEY, None)
