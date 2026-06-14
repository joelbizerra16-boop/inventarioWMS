from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render

from django.views import View

from accounts.mixins import (
    AcessoOperacionalMixin,
    RequerEscritaInventarioMixin,
    RequerNaoOperadorMixin,
)
from accounts.services.perfil import usuario_e_operador_pocket
from django.urls import reverse
from posicoes.forms import PrecadastroPosicaoForm, PrecadastroPosicaoOperadorForm
from produtos.forms import PrecadastroProdutoForm, PrecadastroProdutoOperadorForm
from inventario.models import Inventario
from posicoes.services.homologacao import HomologacaoPosicaoError, criar_precadastro_posicao
from produtos.services.homologacao import HomologacaoError, criar_precadastro_produto


def _equipamento_request(request) -> str:
    return request.META.get('HTTP_USER_AGENT', '')[:255]


def _usuario_operacional(request):
    return request.user.perfil_operacional


class RequerOperadorPocketMixin(AcessoOperacionalMixin):
    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not usuario_e_operador_pocket(request.user):
            messages.error(request, 'Acesso restrito ao operador Pocket.')
            return redirect('pocket:selecionar')
        return super().dispatch(request, *args, **kwargs)


class OperadorPrecadastroProdutoView(RequerOperadorPocketMixin, View):
    template_name = 'inventario/pocket/precadastro_produto.html'

    def get(self, request):
        codigo = request.GET.get('codigo', '').strip().upper()
        return render(request, self.template_name, {
            'form': PrecadastroProdutoOperadorForm(initial={'codigo_produto': codigo}),
            'voltar_url': reverse('pocket:selecionar'),
            'voltar_rotulo': 'Voltar ao Pocket',
        })

    def post(self, request):
        form = PrecadastroProdutoOperadorForm(request.POST)
        if not form.is_valid():
            return render(request, self.template_name, {
                'form': form,
                'voltar_url': reverse('pocket:selecionar'),
                'voltar_rotulo': 'Voltar ao Pocket',
            })
        try:
            criar_precadastro_produto(
                codigo_produto=form.cleaned_data['codigo_produto'],
                descricao=form.cleaned_data['descricao'],
                embalagem=form.cleaned_data.get('embalagem', ''),
                usuario=_usuario_operacional(request),
                origem='POCKET_OPERADOR',
                equipamento=_equipamento_request(request),
            )
        except HomologacaoError as exc:
            form.add_error(None, str(exc))
            return render(request, self.template_name, {
                'form': form,
                'voltar_url': reverse('pocket:selecionar'),
                'voltar_rotulo': 'Voltar ao Pocket',
            })
        messages.success(request, 'Pré-cadastro de produto realizado.')
        return redirect('pocket:selecionar')


class OperadorPrecadastroPosicaoView(RequerOperadorPocketMixin, View):
    template_name = 'inventario/pocket/precadastro_posicao_ciclico.html'

    def get(self, request):
        codigo = request.GET.get('codigo', '').strip().upper()
        return render(request, self.template_name, {
            'form': PrecadastroPosicaoOperadorForm(initial={'codigo': codigo}),
            'voltar_url': reverse('pocket:selecionar'),
            'voltar_rotulo': 'Voltar ao Pocket',
        })

    def post(self, request):
        form = PrecadastroPosicaoOperadorForm(request.POST)
        if not form.is_valid():
            return render(request, self.template_name, {
                'form': form,
                'voltar_url': reverse('pocket:selecionar'),
                'voltar_rotulo': 'Voltar ao Pocket',
            })
        try:
            criar_precadastro_posicao(
                codigo_completo=form.cleaned_data['codigo'],
                posicao_descricao=form.cleaned_data['posicao'],
                usuario=_usuario_operacional(request),
                origem='POCKET_OPERADOR',
                equipamento=_equipamento_request(request),
            )
        except HomologacaoPosicaoError as exc:
            form.add_error(None, str(exc))
            return render(request, self.template_name, {
                'form': form,
                'voltar_url': reverse('pocket:selecionar'),
                'voltar_rotulo': 'Voltar ao Pocket',
            })
        messages.success(request, 'Pré-cadastro de posição realizado.')
        return redirect('pocket:selecionar')


class PocketPrecadastroProdutoView(RequerNaoOperadorMixin, RequerEscritaInventarioMixin, View):
    template_name = 'inventario/pocket/precadastro_produto.html'

    def get(self, request, inventario_id):
        inventario = get_object_or_404(Inventario, pk=inventario_id)
        codigo = request.GET.get('codigo', '').strip().upper()
        return render(request, self.template_name, {
            'inventario': inventario,
            'form': PrecadastroProdutoForm(initial={'codigo_produto': codigo}),
            'voltar_url': reverse('pocket:contagem', kwargs={'inventario_id': inventario.pk}),
            'voltar_rotulo': 'Voltar à contagem',
        })

    def post(self, request, inventario_id):
        inventario = get_object_or_404(Inventario, pk=inventario_id)
        form = PrecadastroProdutoForm(request.POST)
        if not form.is_valid():
            return render(request, self.template_name, {
                'inventario': inventario,
                'form': form,
                'voltar_url': reverse('pocket:contagem', kwargs={'inventario_id': inventario.pk}),
                'voltar_rotulo': 'Voltar à contagem',
            })
        try:
            criar_precadastro_produto(
                codigo_produto=form.cleaned_data['codigo_produto'],
                descricao=form.cleaned_data['descricao'],
                codigo_ean=form.cleaned_data.get('codigo_ean', ''),
                embalagem=form.cleaned_data.get('embalagem', ''),
                observacao=form.cleaned_data.get('observacao', ''),
                usuario=_usuario_operacional(request),
                origem='POCKET_GERAL',
                equipamento=_equipamento_request(request),
            )
        except HomologacaoError as exc:
            form.add_error(None, str(exc))
            return render(request, self.template_name, {
                'inventario': inventario,
                'form': form,
                'voltar_url': reverse('pocket:contagem', kwargs={'inventario_id': inventario.pk}),
                'voltar_rotulo': 'Voltar à contagem',
            })
        messages.success(request, 'Pré-cadastro de produto realizado. Continue a contagem.')
        return redirect('pocket:contagem', inventario_id=inventario.pk)


class PocketPrecadastroPosicaoView(RequerNaoOperadorMixin, RequerEscritaInventarioMixin, View):
    template_name = 'inventario/pocket/precadastro_posicao.html'

    def get(self, request, inventario_id):
        inventario = get_object_or_404(Inventario, pk=inventario_id)
        codigo = request.GET.get('codigo', '').strip().upper()
        return render(request, self.template_name, {
            'inventario': inventario,
            'form': PrecadastroPosicaoForm(initial={'codigo': codigo}),
        })

    def post(self, request, inventario_id):
        inventario = get_object_or_404(Inventario, pk=inventario_id)
        form = PrecadastroPosicaoForm(request.POST)
        if not form.is_valid():
            return render(request, self.template_name, {
                'inventario': inventario,
                'form': form,
            })
        try:
            criar_precadastro_posicao(
                codigo_completo=form.cleaned_data['codigo'],
                posicao_descricao=form.cleaned_data['posicao'],
                usuario=_usuario_operacional(request),
                origem='POCKET_GERAL',
                equipamento=_equipamento_request(request),
            )
        except HomologacaoPosicaoError as exc:
            form.add_error(None, str(exc))
            return render(request, self.template_name, {
                'inventario': inventario,
                'form': form,
            })
        messages.success(request, 'Pré-cadastro de posição realizado. Continue a contagem.')
        return redirect('pocket:contagem', inventario_id=inventario.pk)


class PocketCiclicoPrecadastroPosicaoView(RequerNaoOperadorMixin, RequerEscritaInventarioMixin, View):
    template_name = 'inventario/pocket/precadastro_posicao_ciclico.html'

    def get(self, request):
        codigo = request.GET.get('codigo', '').strip().upper()
        return render(request, self.template_name, {
            'form': PrecadastroPosicaoForm(initial={'codigo': codigo}),
            'voltar_url': reverse('pocket:contagem_ciclico'),
            'voltar_rotulo': 'Voltar à contagem',
        })

    def post(self, request):
        form = PrecadastroPosicaoForm(request.POST)
        contexto_voltar = {
            'voltar_url': reverse('pocket:contagem_ciclico'),
            'voltar_rotulo': 'Voltar à contagem',
        }
        if not form.is_valid():
            return render(request, self.template_name, {'form': form, **contexto_voltar})
        try:
            criar_precadastro_posicao(
                codigo_completo=form.cleaned_data['codigo'],
                posicao_descricao=form.cleaned_data['posicao'],
                usuario=_usuario_operacional(request),
                origem='POCKET_CICLICO',
                equipamento=_equipamento_request(request),
            )
        except HomologacaoPosicaoError as exc:
            form.add_error(None, str(exc))
            return render(request, self.template_name, {'form': form, **contexto_voltar})
        messages.success(request, 'Pré-cadastro de posição realizado. Continue a contagem.')
        return redirect('pocket:contagem_ciclico')
