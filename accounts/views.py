from django.contrib import messages
from django.contrib.auth.views import LoginView, LogoutView
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.views import View
from django.views.generic import CreateView, DeleteView, DetailView, ListView, UpdateView

from accounts.forms import UsuarioForm
from accounts.mixins import PaginacaoContextMixin, PaginacaoMixin, RequerAdministradorMixin
from accounts.models import Usuario
from core.logging_auditoria import ip_do_request, registrar_evento
from core.mixins import ExclusaoSeguraMixin
from accounts.services.usuarios import (
    UsuarioServiceError,
    alternar_status_usuario,
    atualizar_usuario_operacional,
    criar_usuario_operacional,
    excluir_usuario_operacional,
    filtrar_usuarios,
    obter_resumo_usuarios,
)


class OperacionalLoginView(LoginView):
    template_name = 'accounts/login.html'
    redirect_authenticated_user = True

    def form_valid(self, form):
        response = super().form_valid(form)
        registrar_evento('login', usuario=self.request.user, ip=ip_do_request(self.request))
        return response

    def form_invalid(self, form):
        registrar_evento(
            'login_falhou',
            usuario=form.data.get('username', ''),
            ip=ip_do_request(self.request),
        )
        return super().form_invalid(form)

    def get_success_url(self):
        from accounts.services.perfil import usuario_e_operador_pocket

        if usuario_e_operador_pocket(self.request.user):
            return reverse('pocket:selecionar')
        return super().get_success_url()


class OperacionalLogoutView(LogoutView):
    next_page = reverse_lazy('accounts:login')

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            from inventario.services.locks import liberar_locks_usuario

            liberar_locks_usuario(
                request.user,
                session_key=request.session.session_key or '',
                ip=ip_do_request(request),
            )
            registrar_evento('logout', usuario=request.user, ip=ip_do_request(request))
        return super().dispatch(request, *args, **kwargs)


class UsuarioListView(
    RequerAdministradorMixin,
    PaginacaoMixin,
    PaginacaoContextMixin,
    ListView,
):
    model = Usuario
    template_name = 'accounts/usuarios_lista.html'
    context_object_name = 'usuarios'

    def get_queryset(self):
        return filtrar_usuarios(
            nome=self.request.GET.get('nome', ''),
            login=self.request.GET.get('login', ''),
            perfil=self.request.GET.get('perfil', ''),
            status=self.request.GET.get('status', ''),
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['resumo'] = obter_resumo_usuarios()
        context['filtros'] = {
            'nome': self.request.GET.get('nome', ''),
            'login': self.request.GET.get('login', ''),
            'perfil': self.request.GET.get('perfil', ''),
            'status': self.request.GET.get('status', ''),
        }
        context['perfil_opcoes'] = Usuario.Perfil.choices
        context['status_opcoes'] = (
            ('', 'Todos'),
            ('ativo', 'Ativo'),
            ('inativo', 'Inativo'),
        )
        return context


class UsuarioDetailView(RequerAdministradorMixin, DetailView):
    model = Usuario
    template_name = 'accounts/usuario_detalhe.html'
    context_object_name = 'usuario_operacional'


class UsuarioCreateView(RequerAdministradorMixin, CreateView):
    model = Usuario
    form_class = UsuarioForm
    template_name = 'accounts/usuario_formulario.html'
    success_url = reverse_lazy('accounts:usuarios_lista')

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['criacao'] = True
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['titulo'] = 'Novo Usuário'
        context['botao'] = 'Salvar'
        return context

    def form_valid(self, form):
        try:
            criar_usuario_operacional(
                nome=form.cleaned_data['nome'],
                login=form.cleaned_data['login'],
                setor=form.cleaned_data['setor'],
                perfil=form.cleaned_data['perfil'],
                ativo=form.cleaned_data['ativo'],
                senha=form.cleaned_data['senha'],
            )
        except UsuarioServiceError as exc:
            form.add_error('login', str(exc))
            return self.form_invalid(form)

        messages.success(self.request, 'Usuário criado com sucesso.')
        return redirect(self.success_url)


class UsuarioUpdateView(RequerAdministradorMixin, UpdateView):
    model = Usuario
    form_class = UsuarioForm
    template_name = 'accounts/usuario_formulario.html'
    success_url = reverse_lazy('accounts:usuarios_lista')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['titulo'] = 'Editar Usuário'
        context['botao'] = 'Atualizar'
        return context

    def form_valid(self, form):
        senha = form.cleaned_data.get('senha') or None
        try:
            atualizar_usuario_operacional(
                self.object,
                nome=form.cleaned_data['nome'],
                login=form.cleaned_data['login'],
                setor=form.cleaned_data['setor'],
                perfil=form.cleaned_data['perfil'],
                ativo=form.cleaned_data['ativo'],
                senha=senha,
            )
        except UsuarioServiceError as exc:
            form.add_error('login', str(exc))
            return self.form_invalid(form)

        messages.success(self.request, 'Usuário atualizado com sucesso.')
        return redirect(self.success_url)


class UsuarioDeleteView(RequerAdministradorMixin, ExclusaoSeguraMixin, DeleteView):
    model = Usuario
    template_name = 'accounts/usuario_excluir.html'
    success_url = reverse_lazy('accounts:usuarios_lista')
    mensagem_exclusao_sucesso = 'Usuário excluído com sucesso.'

    def dispatch(self, request, *args, **kwargs):
        usuario = self.get_object()
        operacional = getattr(request.user, 'perfil_operacional', None)
        if operacional and operacional.pk == usuario.pk:
            messages.error(request, 'Você não pode excluir o próprio usuário.')
            return redirect('accounts:usuarios_lista')
        return super().dispatch(request, *args, **kwargs)

    def executar_exclusao_segura(self, objeto):
        excluir_usuario_operacional(objeto)


class UsuarioToggleStatusView(RequerAdministradorMixin, View):
    def post(self, request, pk):
        usuario = get_object_or_404(Usuario, pk=pk)
        operacional = getattr(request.user, 'perfil_operacional', None)
        if operacional and operacional.pk == usuario.pk:
            messages.error(request, 'Você não pode alterar o status do próprio usuário.')
            return redirect('accounts:usuarios_lista')

        alternar_status_usuario(usuario)
        acao = 'ativado' if usuario.ativo else 'desativado'
        messages.success(request, f'Usuário {acao} com sucesso.')
        return redirect(request.POST.get('next') or reverse('accounts:usuarios_lista'))
