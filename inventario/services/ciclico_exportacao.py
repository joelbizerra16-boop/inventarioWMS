import io
from decimal import Decimal

import pandas as pd
from django.http import HttpResponse
from django.utils import timezone

from inventario.services.ciclico import FiltrosCicloConsulta, GrupoProdutoCiclo, IndicadorConfronto, obter_ciclo_consulta
from inventario.services.ciclico_relatorio import (
    ABAS_EXCEL_EXECUTIVO,
    obter_dados_exportacao_premium,
    obter_grupos_consulta_ciclo,
)


def _canal_grupo(grupo: GrupoProdutoCiclo) -> str:
    cosan = grupo.cosan_total or Decimal('0')
    brida = grupo.brida_total or Decimal('0')
    if cosan > 0 and brida > 0:
        return 'Cosan / Brida'
    if brida > 0:
        return 'Brida'
    if cosan > 0:
        return 'Cosan'
    return '—'


def _contar_recontagens(grupo: GrupoProdutoCiclo) -> int:
    return sum(1 for registro in grupo.historico if registro.tipo == 'RECONTAGEM')


def _ultima_alteracao(grupo: GrupoProdutoCiclo) -> str:
    if not grupo.historico:
        return '—'
    data = max(registro.data_hora for registro in grupo.historico)
    return timezone.localtime(data).strftime('%d/%m/%Y %H:%M')


def _observacoes_grupo(grupo: GrupoProdutoCiclo) -> str:
    motivos: list[str] = []
    vistos: set[str] = set()
    for registro in grupo.historico:
        texto = (registro.motivo or '').strip()
        if texto and texto not in vistos:
            vistos.add(texto)
            motivos.append(texto)
    return ' | '.join(motivos)


def _fmt_data_contagem(valor) -> str:
    if valor is None:
        return '—'
    return timezone.localtime(valor).strftime('%d/%m/%Y %H:%M')


def _linhas_consulta_excel(ciclo_id: int, grupos: list[GrupoProdutoCiclo]) -> list[dict]:
    linhas = []
    for grupo in grupos:
        linhas.append({
            'Ciclo': ciclo_id,
            'SKU': grupo.codigo_produto,
            'Descrição': grupo.descricao,
            'Embalagem': grupo.embalagem or '—',
            'Canal': _canal_grupo(grupo),
            'SAP': float(grupo.sap_total),
            'Cosan': float(grupo.cosan_total) if grupo.cosan_total is not None else None,
            'Brida': float(grupo.brida_total) if grupo.brida_total is not None else None,
            'Físico': float(grupo.fisico_total) if grupo.fisico_total is not None else None,
            'Diferença': float(grupo.diferenca_cosan) if grupo.diferenca_cosan is not None else None,
            'Indicador': IndicadorConfronto.EMOJI.get(grupo.indicador_sap, ''),
            'Status': grupo.status_label,
            'Origem': grupo.ultima_origem_label or '—',
            'Usuário': ', '.join(grupo.usuarios) if grupo.usuarios else '—',
            'Data Contagem': _fmt_data_contagem(grupo.ultima_contagem),
            'Última Alteração': _ultima_alteracao(grupo),
            'Quantidade de Recontagens': _contar_recontagens(grupo),
            'Observações': _observacoes_grupo(grupo),
        })
    return linhas


def exportar_ciclo_excel(
    ciclo_id: int | None = None,
    filtros: FiltrosCicloConsulta | None = None,
    premium: bool = False,
) -> HttpResponse:
    ciclo = obter_ciclo_consulta(ciclo_id)
    if ciclo is None:
        raise ValueError('Nenhum ciclo selecionado para exportação.')

    buffer = io.BytesIO()

    if premium:
        abas = obter_dados_exportacao_premium(ciclo.pk, filtros)
        with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
            for nome in ABAS_EXCEL_EXECUTIVO:
                linhas = abas.get(nome, [])
                if not linhas:
                    pd.DataFrame({'Informação': ['Sem registros.']}).to_excel(
                        writer,
                        index=False,
                        sheet_name=nome[:31],
                    )
                    continue
                pd.DataFrame(linhas).to_excel(writer, index=False, sheet_name=nome[:31])
        filename = f'Relatorio_Executivo_Ciclo_{ciclo.pk}.xlsx'
    else:
        grupos = obter_grupos_consulta_ciclo(ciclo.pk, filtros)
        linhas = _linhas_consulta_excel(ciclo.pk, grupos)
        dataframe = pd.DataFrame(linhas)
        with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
            dataframe.to_excel(writer, index=False, sheet_name='Consulta Ciclo')
        filename = f'Consulta_Ciclo_{ciclo.pk}.xlsx'

    buffer.seek(0)
    response = HttpResponse(
        buffer.getvalue(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response
