from django.contrib import messages
from django.shortcuts import redirect, render
from django.views import View

from accounts.mixins import RequerAdministradorMixin
from core.services.exclusao import ExclusaoBloqueadaError, mensagem_de_excecao_operacional
from core.services.limpeza_dados import (
    FRASE_CONFIRMACAO,
    LimpezaDadosError,
    definir_grupos_limpeza,
    excluir_grupos_selecionados,
    grupos_por_id,
)


class LimpezaDadosView(RequerAdministradorMixin, View):
    template_name = 'accounts/limpeza_dados.html'

    def get(self, request):
        return render(request, self.template_name, self._contexto())

    def post(self, request):
        selecionados = request.POST.getlist('grupos')
        confirmacao = request.POST.get('confirmacao', '')
        contexto = self._contexto(selecionados=selecionados, confirmacao=confirmacao)
        try:
            resultado = excluir_grupos_selecionados(
                selecionados,
                usuario=request.user,
                confirmacao=confirmacao,
            )
        except (LimpezaDadosError, ExclusaoBloqueadaError) as exc:
            messages.error(request, exc.mensagem)
            return render(request, self.template_name, contexto, status=400)
        except Exception as exc:
            messages.error(request, mensagem_de_excecao_operacional(exc))
            return render(request, self.template_name, contexto, status=400)

        catalogo = grupos_por_id()
        nomes = [catalogo[grupo_id].titulo for grupo_id in resultado.grupos]
        mensagem = (
            f'Exclusão concluída: {resultado.total_excluidos} registro(s) removido(s) '
            f'em {", ".join(nomes)}.'
        )
        if resultado.grupos_incluidos:
            extras = [catalogo[grupo_id].titulo for grupo_id in resultado.grupos_incluidos]
            mensagem += f' Também foram limpos os vínculos: {", ".join(extras)}.'
        messages.success(request, mensagem)
        return redirect('accounts:limpeza_dados')

    def _contexto(self, selecionados=None, confirmacao=''):
        selecionados = list(selecionados or [])
        return {
            'grupos': definir_grupos_limpeza(),
            'grupos_selecionados': selecionados,
            'confirmacao': confirmacao,
            'frase_confirmacao': FRASE_CONFIRMACAO,
        }
