import io

import pandas as pd
from django.http import HttpResponse
from django.utils import timezone

from estoque_fisico.models import EstoqueFisico


def _montar_linha_exportacao(registro: EstoqueFisico) -> dict:
    return {
        'Código Alocação': registro.posicao.codigo,
        'Alocação': registro.posicao.posicao,
        'Código Produto': registro.produto.codigo_produto,
        'Descrição': registro.produto.descricao,
        'Quantidade': float(registro.quantidade),
        'Data Atualização': timezone.localtime(
            registro.data_atualizacao,
        ).strftime('%d/%m/%Y %H:%M'),
    }


def exportar_estoque_fisico_excel(queryset) -> HttpResponse:
    linhas = [_montar_linha_exportacao(registro) for registro in queryset]
    dataframe = pd.DataFrame(
        linhas,
        columns=[
            'Código Alocação',
            'Alocação',
            'Código Produto',
            'Descrição',
            'Quantidade',
            'Data Atualização',
        ],
    )

    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
        dataframe.to_excel(writer, index=False, sheet_name='Estoque Fisico')

    buffer.seek(0)
    response = HttpResponse(
        buffer.getvalue(),
        content_type=(
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        ),
    )
    response['Content-Disposition'] = 'attachment; filename="Estoque_Fisico.xlsx"'
    return response
