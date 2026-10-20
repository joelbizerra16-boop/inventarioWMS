"""Configuração do Painel do Auditor (gestores: administrador / inventário)."""

from django import forms
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views import View

from accounts.mixins import RequerEscritaInventarioMixin, RequerNaoOperadorMixin
from accounts.models import Usuario
from accounts.services.perfil import usuario_pode_escrever_inventario
from inventario.auditor_views import _contexto, _privado, resposta_conteudo, resposta_exportacao
from inventario.models import ComparativoContagem
from inventario.models_auditor import AuditorAcesso, LiberacaoContagemAuditor, PublicacaoAuditoria
from inventario.services import auditor_publicacao as servico
from inventario.services.auditor_publicacao import PublicacaoError


class PublicacaoAuditorForm(forms.Form):
    auditores = forms.ModelMultipleChoiceField(
        label='Auditores autorizados', required=False, queryset=get_user_model().objects.none(),
        widget=forms.CheckboxSelectMultiple,
    )
    mostrar_horario_real = forms.BooleanField(
        label='Mostrar ao auditor o horário real das contagens', required=False,
        help_text='Libera o gráfico de evolução dos registros e o horário real no detalhe de cada quantidade. '
                  'Desligado: o auditor vê apenas quando cada registro foi disponibilizado.',
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input', 'role': 'switch'}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['auditores'].queryset = get_user_model().objects.filter(
            perfil_operacional__perfil=Usuario.Perfil.AUDITOR, is_active=True,
        ).select_related('perfil_operacional').order_by('perfil_operacional__nome')
        self.fields['auditores'].label_from_instance = (
            lambda u: getattr(u.perfil_operacional, 'nome', '') or u.get_username()
        )


class LiberacaoContagemForm(forms.Form):
    """Configuração de uma liberação (por contagem)."""

    etapa = forms.TypedChoiceField(choices=[(1, 'C1'), (2, 'C2'), (3, 'C3')], coerce=int)
    modo = forms.ChoiceField(choices=LiberacaoContagemAuditor.Modo.choices)
    duracao_minutos = forms.IntegerField(min_value=1, max_value=24 * 60, required=False)
    ordem = forms.ChoiceField(choices=PublicacaoAuditoria.Ordem.choices, required=False)
    inicio_previsto = forms.DateTimeField(required=False, input_formats=['%Y-%m-%dT%H:%M'])


ACOES_CONTAGEM = {
    'pausar': (servico.pausar_contagem, 'Liberação de C{etapa} pausada.'),
    'retomar': (servico.retomar_contagem, 'Liberação de C{etapa} retomada.'),
    'concluir': (servico.concluir_contagem, 'Todos os registros restantes de C{etapa} foram disponibilizados.'),
    'interromper': (servico.interromper_contagem, 'Liberações restantes de C{etapa} canceladas; o que já foi disponibilizado continua visível.'),
}


class GestorAuditoriaMixin(RequerNaoOperadorMixin, RequerEscritaInventarioMixin):
    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not usuario_pode_escrever_inventario(request.user):
            messages.error(request, 'Somente administradores e equipe de inventário configuram o Painel do Auditor.')
            return redirect('comparativo:lista')
        return super().dispatch(request, *args, **kwargs)


class ComparativoAuditoriaView(GestorAuditoriaMixin, View):
    template_name = 'inventario/comparativo/auditoria.html'

    def _contexto(self, comparativo, form=None):
        publicacao = servico.obter_publicacao(comparativo, self.request.user)
        if form is None:
            form = PublicacaoAuditorForm(initial={
                'mostrar_horario_real': publicacao.mostrar_horario_real,
                'auditores': list(
                    AuditorAcesso.objects.filter(comparativo=comparativo, ativo=True).values_list('auditor_id', flat=True)
                ),
            })
        auditores = [
            getattr(getattr(a.auditor, 'perfil_operacional', None), 'nome', '') or a.auditor.get_username()
            for a in AuditorAcesso.objects.filter(comparativo=comparativo, ativo=True)
            .select_related('auditor__perfil_operacional')
        ]
        contagens = servico.estado_contagens(comparativo)
        return {
            'comparativo': comparativo,
            'publicacao': publicacao,
            'form': form,
            'Modo': LiberacaoContagemAuditor.Modo,
            'Ordem': PublicacaoAuditoria.Ordem,
            'Programacao': LiberacaoContagemAuditor.Programacao,
            'contagens': contagens,
            'auditores_ativos': auditores,
            # Configuração anterior à liberação por contagem: apresentada, nunca convertida sozinha.
            'legado_liberado': publicacao.liberado and not LiberacaoContagemAuditor.objects.filter(comparativo=comparativo).exists(),
            'legado_programada': (
                publicacao.status != PublicacaoAuditoria.Status.RASCUNHO
                or publicacao.itens.filter(liberacao__isnull=True).exists()
            ),
            'eventos': publicacao.eventos.select_related('usuario', 'usuario__perfil_operacional')[:40],
            'Liberacao': LiberacaoContagemAuditor.Status,
        }

    def get(self, request, pk):
        comparativo = get_object_or_404(ComparativoContagem, pk=pk)
        return render(request, self.template_name, self._contexto(comparativo))

    def post(self, request, pk):
        comparativo = get_object_or_404(ComparativoContagem, pk=pk)
        acao = request.POST.get('acao', 'salvar')
        try:
            if acao == 'salvar':
                form = PublicacaoAuditorForm(request.POST)
                if not form.is_valid():
                    return render(request, self.template_name, self._contexto(comparativo, form))
                servico.configurar(
                    comparativo, request.user, auditores=list(form.cleaned_data['auditores']),
                    mostrar_horario_real=form.cleaned_data['mostrar_horario_real'],
                )
                messages.success(request, 'Configuração do auditor salva.')
            elif acao == 'liberar':
                form = LiberacaoContagemForm(request.POST)
                if not form.is_valid():
                    raise PublicacaoError('Revise a configuração da liberação: ' + '; '.join(
                        f'{campo}: {" ".join(erros)}' for campo, erros in form.errors.items()
                    ))
                dados = form.cleaned_data
                inicio = dados['inicio_previsto']
                if inicio is not None and timezone.is_naive(inicio):
                    inicio = timezone.make_aware(inicio)
                liberacao = servico.liberar_contagem(
                    comparativo, dados['etapa'], request.user, modo=dados['modo'],
                    duracao_minutos=dados['duracao_minutos'] or servico.DURACAO_PADRAO_MINUTOS,
                    ordem=dados['ordem'] or PublicacaoAuditoria.Ordem.POSICAO, inicio_previsto=inicio,
                )
                if liberacao.modo == LiberacaoContagemAuditor.Modo.PROGRAMADA:
                    messages.success(request, (
                        f'C{liberacao.etapa}: liberação programada de {liberacao.total_programado} registro(s) '
                        f'em {liberacao.duracao_minutos} min. As demais contagens não foram alteradas.'
                    ))
                else:
                    messages.success(request, f'C{liberacao.etapa} liberada em tempo real. As demais contagens não foram alteradas.')
            elif acao == 'suspender':
                etapa = int(request.POST.get('etapa') or 0)
                servico.suspender_contagem(comparativo, etapa, request.user, request.POST.get('motivo', ''))
                messages.success(request, f'Acesso à C{etapa} suspenso. Dados e histórico foram preservados.')
            elif acao in ACOES_CONTAGEM:
                etapa = int(request.POST.get('etapa') or 0)
                funcao, mensagem = ACOES_CONTAGEM[acao]
                funcao(comparativo, etapa, request.user)
                messages.success(request, mensagem.format(etapa=etapa))
            else:
                messages.error(request, 'Ação desconhecida.')
        except (PublicacaoError, ValueError) as exc:
            messages.error(request, str(exc))
        return redirect('comparativo:auditoria', pk=pk)


class ComparativoAuditoriaPreviaView(GestorAuditoriaMixin, View):
    """Visualizar como auditor: mesma montagem e mesmas regras de escopo da tela real."""

    def get(self, request, pk):
        comparativo = get_object_or_404(ComparativoContagem, pk=pk)
        servico.obter_publicacao(comparativo, request.user)
        comparativo.refresh_from_db()
        _painel, contexto = _contexto(request, comparativo, previa=True)
        return _privado(render(request, 'inventario/auditor/painel.html', contexto))


class ComparativoAuditoriaPreviaConteudoView(GestorAuditoriaMixin, View):
    def get(self, request, pk):
        comparativo = get_object_or_404(ComparativoContagem, pk=pk)
        servico.obter_publicacao(comparativo, request.user)
        return resposta_conteudo(request, comparativo, previa=True)


class ComparativoAuditoriaPreviaExportarView(GestorAuditoriaMixin, View):
    """Excel da prévia: exatamente o mesmo arquivo que o auditor exportaria."""

    def get(self, request, pk):
        comparativo = get_object_or_404(ComparativoContagem, pk=pk)
        servico.obter_publicacao(comparativo, request.user)
        return resposta_exportacao(request, comparativo)
