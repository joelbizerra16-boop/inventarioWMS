from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View
from django.views.generic import TemplateView

from accounts.mixins import RequerAdministradorMixin
from posicoes.forms import PosicaoHomologacaoForm
from posicoes.models import Posicao
from posicoes.services.homologacao import (
    aprovar_posicao,
    editar_posicao_homologacao,
    listar_posicoes_pendentes,
    rejeitar_posicao,
)
from produtos.forms import ProdutoHomologacaoForm
from produtos.models import Produto
from produtos.services.homologacao import (
    aprovar_produto,
    editar_produto_homologacao,
    listar_produtos_pendentes,
    rejeitar_produto,
)


def _equipamento_request(request) -> str:
    return request.META.get('HTTP_USER_AGENT', '')[:255]


class PendenciasOperacionaisView(RequerAdministradorMixin, TemplateView):
    template_name = 'accounts/pendencias_operacionais.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['produtos_pendentes'] = listar_produtos_pendentes()
        context['posicoes_pendentes'] = listar_posicoes_pendentes()
        return context


class ProdutoPendenciaAprovarView(RequerAdministradorMixin, View):
    def post(self, request, pk):
        produto = get_object_or_404(Produto, pk=pk)
        aprovar_produto(
            produto,
            request.user.perfil_operacional,
            equipamento=_equipamento_request(request),
        )
        messages.success(request, f'Produto {produto.codigo_produto} homologado.')
        return redirect('accounts:pendencias_operacionais')


class ProdutoPendenciaRejeitarView(RequerAdministradorMixin, View):
    def post(self, request, pk):
        produto = get_object_or_404(Produto, pk=pk)
        observacao = request.POST.get('observacao', '')
        rejeitar_produto(
            produto,
            request.user.perfil_operacional,
            equipamento=_equipamento_request(request),
            observacao=observacao,
        )
        messages.warning(request, f'Produto {produto.codigo_produto} rejeitado.')
        return redirect('accounts:pendencias_operacionais')


class ProdutoPendenciaEditarView(RequerAdministradorMixin, View):
    template_name = 'accounts/pendencia_produto_editar.html'

    def get(self, request, pk):
        produto = get_object_or_404(Produto, pk=pk)
        return render(request, self.template_name, {
            'produto': produto,
            'form': ProdutoHomologacaoForm(instance=produto),
            'auditorias': produto.auditorias_homologacao.select_related('usuario')[:20],
        })

    def post(self, request, pk):
        produto = get_object_or_404(Produto, pk=pk)
        form = ProdutoHomologacaoForm(request.POST, instance=produto)
        if not form.is_valid():
            return render(request, self.template_name, {
                'produto': produto,
                'form': form,
                'auditorias': produto.auditorias_homologacao.select_related('usuario')[:20],
            })
        editar_produto_homologacao(
            produto,
            request.user.perfil_operacional,
            {
                **form.cleaned_data,
                'aprovar': form.cleaned_data.get('aprovar'),
            },
            equipamento=_equipamento_request(request),
        )
        messages.success(request, f'Produto {produto.codigo_produto} atualizado.')
        return redirect('accounts:pendencias_operacionais')


class PosicaoPendenciaAprovarView(RequerAdministradorMixin, View):
    def post(self, request, pk):
        posicao = get_object_or_404(Posicao, pk=pk)
        aprovar_posicao(
            posicao,
            request.user.perfil_operacional,
            equipamento=_equipamento_request(request),
        )
        messages.success(request, f'Posição {posicao.codigo} homologada.')
        return redirect('accounts:pendencias_operacionais')


class PosicaoPendenciaRejeitarView(RequerAdministradorMixin, View):
    def post(self, request, pk):
        posicao = get_object_or_404(Posicao, pk=pk)
        observacao = request.POST.get('observacao', '')
        rejeitar_posicao(
            posicao,
            request.user.perfil_operacional,
            equipamento=_equipamento_request(request),
            observacao=observacao,
        )
        messages.warning(request, f'Posição {posicao.codigo} rejeitada.')
        return redirect('accounts:pendencias_operacionais')


class PosicaoPendenciaEditarView(RequerAdministradorMixin, View):
    template_name = 'accounts/pendencia_posicao_editar.html'

    def get(self, request, pk):
        posicao = get_object_or_404(Posicao, pk=pk)
        return render(request, self.template_name, {
            'posicao': posicao,
            'form': PosicaoHomologacaoForm(instance=posicao),
            'auditorias': posicao.auditorias_homologacao.select_related('usuario')[:20],
        })

    def post(self, request, pk):
        posicao = get_object_or_404(Posicao, pk=pk)
        form = PosicaoHomologacaoForm(request.POST, instance=posicao)
        if not form.is_valid():
            return render(request, self.template_name, {
                'posicao': posicao,
                'form': form,
                'auditorias': posicao.auditorias_homologacao.select_related('usuario')[:20],
            })
        editar_posicao_homologacao(
            posicao,
            request.user.perfil_operacional,
            {
                **form.cleaned_data,
                'aprovar': form.cleaned_data.get('aprovar'),
            },
            equipamento=_equipamento_request(request),
        )
        messages.success(request, f'Posição {posicao.codigo} atualizada.')
        return redirect('accounts:pendencias_operacionais')
