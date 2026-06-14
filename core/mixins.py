from django.contrib import messages
from django.http import HttpResponseRedirect
from django.shortcuts import redirect

from core.services.exclusao import ExclusaoBloqueadaError, excluir_registro_seguro


class ExclusaoSeguraMixin:
    mensagem_exclusao_sucesso = 'Exclusão realizada com sucesso.'

    def executar_exclusao_segura(self, objeto):
        excluir_registro_seguro(objeto, usuario=getattr(self.request, 'user', None))

    def get_mensagem_exclusao_sucesso(self) -> str:
        return self.mensagem_exclusao_sucesso

    def form_valid(self, form):
        try:
            self.executar_exclusao_segura(self.get_object())
            messages.success(self.request, self.get_mensagem_exclusao_sucesso())
        except ExclusaoBloqueadaError as exc:
            messages.error(self.request, exc.mensagem)
        return HttpResponseRedirect(self.get_success_url())
