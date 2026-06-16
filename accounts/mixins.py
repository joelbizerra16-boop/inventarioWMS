from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth.mixins import LoginRequiredMixin as DjangoLoginRequiredMixin
from django.shortcuts import redirect
from django.urls import reverse_lazy

from accounts.services.perfil import (
    usuario_e_operador_pocket,
    usuario_pode_acessar,
    usuario_pode_escrever_cadastros,
    usuario_pode_escrever_inventario,
    usuario_pode_executar_pocket,
)
from core.pocket_http import deve_responder_json_pocket, json_erro_pocket


class LoginRequiredMixin(DjangoLoginRequiredMixin):
    login_url = reverse_lazy('accounts:login')

    def handle_no_permission(self):
        if deve_responder_json_pocket(self.request):
            return json_erro_pocket(
                self.request,
                'Sessão expirada. Faça login novamente.',
                status=401,
            )
        return super().handle_no_permission()


class AcessoOperacionalMixin(LoginRequiredMixin):
    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if not usuario_pode_acessar(request.user):
            logout(request)
            if deve_responder_json_pocket(request):
                return json_erro_pocket(
                    request,
                    'Usuário sem perfil operacional vinculado.',
                    status=403,
                )
            messages.error(request, 'Usuário sem perfil operacional vinculado.')
            return redirect('accounts:login')
        return super(DjangoLoginRequiredMixin, self).dispatch(request, *args, **kwargs)


class RequerAdministradorMixin(AcessoOperacionalMixin):
    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not usuario_pode_escrever_cadastros(request.user):
            messages.error(request, 'Acesso restrito a administradores.')
            return redirect('home')
        return super().dispatch(request, *args, **kwargs)


class RequerEscritaCadastroMixin(AcessoOperacionalMixin):
    def dispatch(self, request, *args, **kwargs):
        if (
            request.method == 'POST'
            and request.user.is_authenticated
            and not usuario_pode_escrever_cadastros(request.user)
        ):
            messages.error(request, 'Sem permissão para alterar cadastros.')
            return redirect('home')
        return super().dispatch(request, *args, **kwargs)


class RequerEscritaInventarioMixin(AcessoOperacionalMixin):
    def dispatch(self, request, *args, **kwargs):
        if (
            request.method == 'POST'
            and request.user.is_authenticated
            and not usuario_pode_escrever_inventario(request.user)
        ):
            messages.error(request, 'Perfil consulta não pode alterar inventários.')
            return redirect(request.path or reverse_lazy('home'))
        return super().dispatch(request, *args, **kwargs)


class RequerEscritaPocketMixin(AcessoOperacionalMixin):
    """Permite POST de contagem no Pocket para operador, inventário e administrador."""

    def dispatch(self, request, *args, **kwargs):
        if (
            request.method == 'POST'
            and request.user.is_authenticated
            and not usuario_pode_executar_pocket(request.user)
        ):
            if deve_responder_json_pocket(request):
                return json_erro_pocket(
                    request,
                    'Perfil consulta não pode executar contagem.',
                    status=403,
                )
            messages.error(request, 'Perfil consulta não pode executar contagem.')
            return redirect(request.path or reverse_lazy('pocket:selecionar'))
        return super().dispatch(request, *args, **kwargs)


class RequerNaoOperadorMixin(AcessoOperacionalMixin):
    """Bloqueia operador Pocket mesmo se o middleware estiver desabilitado."""

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and usuario_e_operador_pocket(request.user):
            messages.error(request, 'Acesso restrito ao fluxo Pocket.')
            return redirect('pocket:selecionar')
        return super().dispatch(request, *args, **kwargs)


class PaginacaoMixin:
    paginate_by = 20


class PaginacaoContextMixin:
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        params = self.request.GET.copy()
        params.pop('page', None)
        context['querystring'] = params.urlencode()
        if 'termo_busca' not in context:
            context['termo_busca'] = self.request.GET.get('q', '')
        return context
