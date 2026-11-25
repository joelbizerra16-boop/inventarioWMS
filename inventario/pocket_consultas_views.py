import logging

from django.http import Http404
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.views import View

from accounts.mixins import AcessoOperacionalMixin
from inventario.models import Inventario
from inventario.services.consulta_pocket import (
    POR_PAGINA,
    ConsultaPocketError,
    consultar_contagens_pocket,
)
from inventario.services.pocket_operacional_ui import montar_operacional_geral

logger = logging.getLogger(__name__)

MENSAGEM_FALHA = 'Não foi possível concluir a consulta. Tente novamente.'
MENSAGEM_SEM_INVENTARIO = (
    'Selecione um inventário antes de pesquisar.'
)


class PocketConsultasIndiceView(AcessoOperacionalMixin, View):
    template_name = 'inventario/pocket/consultas.html'

    def get(self, request):
        return render(request, self.template_name, {
            'sem_inventario': True,
            'mensagem_sem_inventario': MENSAGEM_SEM_INVENTARIO,
            'selecionar_url': reverse('pocket:selecionar'),
        })


class PocketConsultasView(AcessoOperacionalMixin, View):
    template_name = 'inventario/pocket/consultas.html'

    def get(self, request, inventario_id):
        inventario = get_object_or_404(Inventario, pk=inventario_id)
        tipo = request.GET.get('tipo', 'sku')
        termo = request.GET.get('q', '')
        pesquisou = 'q' in request.GET
        linhas = []
        page_obj = None
        mensagem = ''
        aviso = ''
        falha = False

        if pesquisou:
            try:
                resultado = consultar_contagens_pocket(
                    inventario,
                    tipo,
                    termo,
                    pagina=request.GET.get('page') or 1,
                    por_pagina=POR_PAGINA,
                )
            except ConsultaPocketError as exc:
                aviso = str(exc)
            except Http404:
                raise
            except Exception:
                logger.exception(
                    'pocket_consulta_falha inventario=%s',
                    inventario.pk,
                )
                falha = True
                mensagem = MENSAGEM_FALHA
            else:
                linhas = resultado.linhas
                page_obj = resultado.page_obj
                mensagem = resultado.mensagem

        params = request.GET.copy()
        params.pop('page', None)
        return render(request, self.template_name, {
            'inventario': inventario,
            'operacional': montar_operacional_geral(request, inventario),
            'tipo': 'posicao' if (tipo or '').strip().lower() == 'posicao' else 'sku',
            'termo': termo,
            'linhas': linhas,
            'page_obj': page_obj,
            'querystring': params.urlencode(),
            'mensagem': mensagem if pesquisou else '',
            'aviso': aviso,
            'falha': falha,
            'voltar_url': reverse('pocket:contagem', kwargs={'inventario_id': inventario.pk}),
        })
