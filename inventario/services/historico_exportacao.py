import csv
import io
from decimal import Decimal

import pandas as pd
from django.http import HttpResponse
from django.utils import timezone

from inventario.services.historico_unificado import (
    HistoricoDetalheUnificado,
    TipoHistorico,
    obter_detalhe_historico_unificado,
)


def _linhas_exportacao(detalhe: HistoricoDetalheUnificado) -> list[dict]:
    linhas = []
    for produto in detalhe.produtos:
        linhas.append({
            'ID': detalhe.pk,
            'Tipo': detalhe.tipo_label,
            'Código': produto.codigo_produto,
            'Descrição': produto.descricao,
            'Embalagem': produto.embalagem,
            'SAP': produto.sap,
            'Contado': produto.contado,
            'Diferença': produto.diferenca,
            'Status': produto.status,
        })
    return linhas


def _linhas_posicoes_exportacao(detalhe: HistoricoDetalheUnificado) -> list[dict]:
    linhas = []
    for produto in detalhe.produtos:
        for posicao in produto.posicoes:
            linhas.append({
                'ID': detalhe.pk,
                'Tipo': detalhe.tipo_label,
                'Código': produto.codigo_produto,
                'Posição': posicao.alocacao,
                'Código Posição': posicao.codigo,
                'Quantidade': posicao.quantidade,
            })
    return linhas


def exportar_historico_excel(tipo: str, pk: int) -> HttpResponse:
    detalhe = obter_detalhe_historico_unificado(tipo, pk)
    if detalhe is None:
        return HttpResponse('Registro não encontrado.', status=404)

    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
        resumo = pd.DataFrame([
            {'Campo': 'ID', 'Valor': detalhe.pk},
            {'Campo': 'Tipo', 'Valor': detalhe.tipo_label},
            {'Campo': 'Responsável', 'Valor': detalhe.responsavel},
            {'Campo': 'Data', 'Valor': timezone.localtime(detalhe.data_referencia).strftime('%d/%m/%Y %H:%M')},
            {'Campo': 'Status', 'Valor': detalhe.status_label},
            {'Campo': 'Total Itens', 'Valor': detalhe.quantidade_itens},
            {'Campo': 'Conciliados', 'Valor': detalhe.conciliados},
            {'Campo': 'Divergentes', 'Valor': detalhe.divergentes},
            {'Campo': 'Acuracidade', 'Valor': f'{detalhe.acuracidade}%' if detalhe.acuracidade is not None else '—'},
        ])
        resumo.to_excel(writer, sheet_name='Resumo', index=False)
        pd.DataFrame(_linhas_exportacao(detalhe)).to_excel(writer, sheet_name='Resultados', index=False)
        pd.DataFrame(_linhas_posicoes_exportacao(detalhe)).to_excel(writer, sheet_name='Posições', index=False)

    buffer.seek(0)
    nome = f'historico_{tipo.lower()}_{pk}.xlsx'
    response = HttpResponse(
        buffer.getvalue(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    response['Content-Disposition'] = f'attachment; filename="{nome}"'
    return response


def exportar_historico_csv(tipo: str, pk: int) -> HttpResponse:
    detalhe = obter_detalhe_historico_unificado(tipo, pk)
    if detalhe is None:
        return HttpResponse('Registro não encontrado.', status=404)

    buffer = io.StringIO()
    writer = csv.DictWriter(
        buffer,
        fieldnames=['ID', 'Tipo', 'Código', 'Descrição', 'Embalagem', 'SAP', 'Contado', 'Diferença', 'Status'],
    )
    writer.writeheader()
    writer.writerows(_linhas_exportacao(detalhe))

    response = HttpResponse(buffer.getvalue(), content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="historico_{tipo.lower()}_{pk}.csv"'
    return response


def exportar_historico_pdf_redirect_tipo(tipo: str) -> str | None:
    if tipo == TipoHistorico.CICLICO:
        return 'ciclico_relatorio'
    return None
