"""Geração do PDF executivo do Inventário Cíclico (ReportLab + Matplotlib)."""

from __future__ import annotations

import io
from decimal import Decimal
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from django.conf import settings
from django.http import HttpResponse
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    Image,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from inventario.services.ciclico import FiltrosCicloConsulta
from inventario.services.ciclico_relatorio import RelatorioExecutivoCiclo, obter_relatorio_executivo


def _fmt_data(valor) -> str:
    if valor is None:
        return '—'
    from django.utils import timezone
    return timezone.localtime(valor).strftime('%d/%m/%Y %H:%M')


def _fmt_hora(valor) -> str:
    if valor is None:
        return '—'
    from django.utils import timezone
    return timezone.localtime(valor).strftime('%d/%m %H:%M')


def _empresa_nome() -> str:
    return getattr(settings, 'RELATORIO_CICLICO_EMPRESA', 'Brida Logística')


def _logo_path() -> Path | None:
    raw = getattr(settings, 'RELATORIO_CICLICO_LOGO', '')
    if not raw:
        candidatos = [
            Path(settings.BASE_DIR) / 'static' / 'img' / 'logo.png',
            Path(settings.BASE_DIR) / 'static' / 'img' / 'logo_empresa.png',
        ]
        for caminho in candidatos:
            if caminho.is_file():
                return caminho
        return None
    caminho = Path(raw)
    return caminho if caminho.is_file() else None


def _estilos():
    base = getSampleStyleSheet()
    return {
        'titulo': ParagraphStyle(
            'TituloCapa',
            parent=base['Heading1'],
            fontSize=18,
            alignment=TA_CENTER,
            spaceAfter=12,
            textColor=colors.HexColor('#1a3a5c'),
        ),
        'subtitulo': ParagraphStyle(
            'SubCapa',
            parent=base['Normal'],
            fontSize=11,
            alignment=TA_CENTER,
            spaceAfter=6,
            textColor=colors.HexColor('#444444'),
        ),
        'secao': ParagraphStyle(
            'Secao',
            parent=base['Heading2'],
            fontSize=13,
            spaceBefore=8,
            spaceAfter=10,
            textColor=colors.HexColor('#1a3a5c'),
        ),
        'texto': ParagraphStyle(
            'Texto',
            parent=base['Normal'],
            fontSize=9,
            leading=12,
        ),
        'bullet': ParagraphStyle(
            'Bullet',
            parent=base['Normal'],
            fontSize=9,
            leftIndent=12,
            bulletIndent=0,
            spaceAfter=4,
        ),
    }


def _tabela(dados: list[list], col_widths=None, header=True) -> Table:
    tabela = Table(dados, colWidths=col_widths, repeatRows=1 if header else 0)
    estilo = [
        ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 0), (-1, -1), 8),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('GRID', (0, 0), (-1, -1), 0.25, colors.grey),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]
    if header and dados:
        estilo.extend([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1a3a5c')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ])
    tabela.setStyle(TableStyle(estilo))
    return tabela


def _chart_pizza(relatorio: RelatorioExecutivoCiclo) -> io.BytesIO:
    valores = [item.quantidade for item in relatorio.indicadores if item.quantidade > 0]
    rotulos = [item.rotulo for item in relatorio.indicadores if item.quantidade > 0]
    cores = [item.cor for item in relatorio.indicadores if item.quantidade > 0]
    if not valores:
        valores, rotulos, cores = [1], ['Sem dados'], ['#cccccc']

    fig, ax = plt.subplots(figsize=(5.5, 4))
    ax.pie(valores, labels=rotulos, colors=cores, autopct='%1.1f%%', startangle=90)
    ax.set_title('Indicadores SAP × Físico', fontsize=11, fontweight='bold')
    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=130, bbox_inches='tight')
    plt.close(fig)
    buf.seek(0)
    return buf


def _chart_barras_simples(titulo: str, categorias: list[str], valores: list[int], cor: str) -> io.BytesIO:
    fig, ax = plt.subplots(figsize=(5.5, 3.5))
    ax.bar(categorias, valores, color=cor, width=0.55)
    ax.set_title(titulo, fontsize=11, fontweight='bold')
    ax.set_ylabel('Quantidade')
    ax.tick_params(axis='x', labelsize=8)
    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=130, bbox_inches='tight')
    plt.close(fig)
    buf.seek(0)
    return buf


def _montar_capa(relatorio: RelatorioExecutivoCiclo, styles) -> list:
    elementos = []
    logo = _logo_path()
    if logo:
        elementos.append(Image(str(logo), width=4 * cm, height=2 * cm, kind='proportional'))
        elementos.append(Spacer(1, 0.5 * cm))

    elementos.append(Paragraph('RELATÓRIO EXECUTIVO', styles['titulo']))
    elementos.append(Paragraph('INVENTÁRIO CÍCLICO', styles['titulo']))
    elementos.append(Spacer(1, 1.2 * cm))

    ciclo = relatorio.ciclo
    emissor = relatorio.usuario_emissor or relatorio.responsavel
    info = [
        ['Empresa', _empresa_nome()],
        ['Data', _fmt_data(relatorio.data_emissao)[:10]],
        ['Período analisado', relatorio.periodo_analisado],
        ['Usuário', emissor],
        ['Ciclo', f'#{ciclo.pk} — {ciclo.get_status_ciclo_display()}'],
        ['Responsável do ciclo', relatorio.responsavel],
    ]
    elementos.append(_tabela([['Campo', 'Informação']] + info, col_widths=[6 * cm, 10 * cm]))

    if relatorio.filtros_aplicados:
        elementos.append(Spacer(1, 0.6 * cm))
        elementos.append(Paragraph('<b>Filtros utilizados</b>', styles['texto']))
        for filtro in relatorio.filtros_aplicados:
            elementos.append(Paragraph(f'• {filtro}', styles['bullet']))

    elementos.append(PageBreak())
    return elementos


def _montar_resumo(relatorio: RelatorioExecutivoCiclo, styles) -> list:
    resumo = relatorio.resumo
    ciclo = relatorio.ciclo
    emissor = relatorio.usuario_emissor or relatorio.responsavel

    elementos = [
        Paragraph('Resumo Executivo', styles['secao']),
        Paragraph(
            f'<b>Ciclo #{ciclo.pk}</b> | Período: {relatorio.periodo_analisado} | '
            f'Emissão: {_fmt_data(relatorio.data_emissao)[:16]} | Gerado por: {emissor}',
            styles['texto'],
        ),
        Spacer(1, 0.4 * cm),
    ]

    kpis = [
        ['Indicador', 'Valor'],
        ['SKUs Planejados', str(resumo.total_skus)],
        ['SKUs Contados', str(resumo.skus_contados)],
        ['SKUs Pendentes', str(resumo.skus_pendentes)],
        ['SKUs Validados', str(resumo.skus_validados)],
        ['SKUs Divergentes', str(resumo.skus_divergentes)],
        ['Execução %', f'{resumo.percentual_executado}%'],
        [
            'Acuracidade %',
            f'{relatorio.acuracidade_geral}%' if relatorio.acuracidade_geral is not None else '—',
        ],
        ['Canal Cosan', str(resumo.por_canal_cosan)],
        ['Canal Brida', str(resumo.por_canal_brida)],
    ]
    elementos.append(_tabela(kpis, col_widths=[8 * cm, 8 * cm]))

    if relatorio.filtros_aplicados:
        elementos.append(Spacer(1, 0.4 * cm))
        elementos.append(Paragraph('<b>Filtros do relatório</b>', styles['texto']))
        for filtro in relatorio.filtros_aplicados:
            elementos.append(Paragraph(f'• {filtro}', styles['bullet']))

    elementos.append(Spacer(1, 0.5 * cm))
    elementos.append(Paragraph('<b>Conclusão automática:</b>', styles['texto']))
    for texto in relatorio.conclusoes_resumo:
        elementos.append(Paragraph(f'• {texto}', styles['bullet']))
    elementos.append(PageBreak())
    return elementos


def _montar_indicadores(relatorio: RelatorioExecutivoCiclo, styles) -> list:
    resumo = relatorio.resumo
    elementos = [Paragraph('Análise de Resultados', styles['secao'])]

    pizza = _chart_pizza(relatorio)
    barras_plan = _chart_barras_simples(
        'Planejados × Contados',
        ['Planejados', 'Contados'],
        [resumo.total_skus, resumo.skus_contados],
        '#1a3a5c',
    )
    barras_val = _chart_barras_simples(
        'Validados × Divergentes',
        ['Validados', 'Divergentes'],
        [resumo.skus_validados, resumo.skus_divergentes],
        '#0d6efd',
    )

    graficos = Table([
        [Image(pizza, width=8.5 * cm, height=6 * cm)],
        [
            Image(barras_plan, width=8.5 * cm, height=5 * cm),
            Image(barras_val, width=8.5 * cm, height=5 * cm),
        ],
    ], colWidths=[8.5 * cm, 8.5 * cm])
    graficos.setStyle(TableStyle([
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ]))
    elementos.append(graficos)

    dados = [['Indicador', 'Quantidade', 'Percentual']]
    for item in relatorio.indicadores:
        dados.append([item.rotulo, str(item.quantidade), f'{item.percentual}%'])
    elementos.append(Spacer(1, 0.4 * cm))
    elementos.append(_tabela(dados))
    elementos.append(PageBreak())
    return elementos


def _montar_embalagens(relatorio: RelatorioExecutivoCiclo, styles) -> list:
    elementos = [Paragraph('Análise por Embalagem', styles['secao'])]
    dados = [[
        'Embalagem', 'Plan.', 'Cont.', 'Valid.', 'Div.', 'Acur.%', 'Dif. Total',
    ]]
    for item in relatorio.por_embalagem:
        dados.append([
            item.embalagem[:20],
            str(item.planejados),
            str(item.contados),
            str(item.validados),
            str(item.divergentes),
            f'{item.acuracidade}',
            str(item.diferenca_total),
        ])
    elementos.append(_tabela(dados))

    if relatorio.ranking_embalagens_divergencia:
        elementos.append(Spacer(1, 0.4 * cm))
        elementos.append(Paragraph('<b>Top embalagens com mais divergências</b>', styles['texto']))
        for item in relatorio.ranking_embalagens_divergencia:
            elementos.append(Paragraph(
                f'• {item.embalagem}: {item.divergentes} divergência(s)',
                styles['bullet'],
            ))

    if relatorio.ranking_embalagens_acuracidade:
        elementos.append(Spacer(1, 0.3 * cm))
        elementos.append(Paragraph('<b>Top embalagens com maior acuracidade</b>', styles['texto']))
        for item in relatorio.ranking_embalagens_acuracidade:
            elementos.append(Paragraph(
                f'• {item.embalagem}: {item.acuracidade}%',
                styles['bullet'],
            ))

    elementos.append(PageBreak())
    return elementos


def _montar_canais(relatorio: RelatorioExecutivoCiclo, styles) -> list:
    elementos = [Paragraph('Análise por Canal', styles['secao'])]
    dados = [['Canal', 'Plan.', 'Cont.', 'Valid.', 'Div.', 'Acur.%']]
    for item in relatorio.por_canal:
        dados.append([
            item.canal,
            str(item.planejados),
            str(item.contados),
            str(item.validados),
            str(item.divergentes),
            f'{item.acuracidade}',
        ])
    elementos.append(_tabela(dados))
    elementos.append(Spacer(1, 0.4 * cm))
    elementos.append(Paragraph(f'<b>Conclusão:</b> {relatorio.conclusao_canal}', styles['texto']))
    elementos.append(PageBreak())
    return elementos


def _montar_divergencias(relatorio: RelatorioExecutivoCiclo, styles) -> list:
    elementos = [Paragraph('Top 20 Divergências', styles['secao'])]
    dados = [[
        'SKU', 'Descrição', 'Emb.', 'Canal', 'SAP', 'Físico', 'Dif.', 'Status', 'Usuário', 'Data',
    ]]
    for item in relatorio.ranking_divergencias:
        dados.append([
            item.codigo_produto,
            item.descricao[:22],
            (item.embalagem or '')[:10],
            item.canal[:8],
            str(item.sap),
            str(item.fisico),
            str(item.diferenca),
            item.status[:12],
            item.usuario[:14],
            _fmt_data(item.data)[:11],
        ])
    if len(dados) == 1:
        dados.append(['—', 'Nenhuma divergência registrada', '', '', '', '', '', '', '', ''])
    elementos.append(_tabela(dados))
    elementos.append(PageBreak())
    return elementos


def _montar_usuarios(relatorio: RelatorioExecutivoCiclo, styles) -> list:
    elementos = [Paragraph('Análise por Usuário', styles['secao'])]
    dados = [['Usuário', 'SKUs Contados', 'Validados', 'Divergências', 'Produtividade %']]
    for item in relatorio.produtividade_usuarios:
        dados.append([
            item.usuario,
            str(item.contagens + item.recontagens),
            str(item.validacoes),
            str(item.divergencias),
            f'{item.participacao}',
        ])
    if len(dados) == 1:
        dados.append(['—', '0', '0', '0', '0'])
    elementos.append(_tabela(dados))
    elementos.append(PageBreak())
    return elementos


def _montar_linha_tempo(relatorio: RelatorioExecutivoCiclo, styles) -> list:
    elementos = [Paragraph('Página 7 — Histórico do Ciclo', styles['secao'])]
    dados = [['Data/Hora', 'Evento', 'Usuário', 'Detalhe']]
    for evento in relatorio.linha_tempo[:80]:
        dados.append([
            _fmt_hora(evento.data_hora),
            evento.descricao[:28],
            evento.usuario[:16],
            evento.detalhe[:40],
        ])
    elementos.append(_tabela(dados))
    elementos.append(PageBreak())
    return elementos


def _montar_excluidos(relatorio: RelatorioExecutivoCiclo, styles) -> list:
    elementos = [Paragraph('Página 8 — Itens Excluídos', styles['secao'])]
    dados = [['SKU', 'Descrição', 'Motivo', 'Usuário', 'Data/Hora']]
    for item in relatorio.itens_excluidos:
        dados.append([
            item.codigo_produto,
            item.descricao[:30],
            item.motivo[:40],
            item.usuario,
            _fmt_data(item.data),
        ])
    if len(dados) == 1:
        dados.append(['—', 'Nenhum SKU excluído neste ciclo', '', '', ''])
    elementos.append(_tabela(dados))
    elementos.append(PageBreak())
    return elementos


def _montar_alteracoes(relatorio: RelatorioExecutivoCiclo, styles) -> list:
    elementos = [Paragraph('Página 9 — Histórico de Alterações', styles['secao'])]
    dados = [['SKU', 'Descrição', 'Anterior', 'Nova', 'Motivo', 'Usuário', 'Data/Hora']]
    for item in relatorio.alteracoes:
        dados.append([
            item.codigo_produto,
            item.descricao[:20],
            item.quantidade_anterior,
            item.quantidade_nova,
            item.motivo[:30],
            item.usuario[:14],
            _fmt_data(item.data_hora)[:14],
        ])
    if len(dados) == 1:
        dados.append(['—', 'Nenhuma edição manual registrada', '', '', '', '', ''])
    elementos.append(_tabela(dados))
    elementos.append(PageBreak())
    return elementos


def _montar_historico_ajustes(relatorio: RelatorioExecutivoCiclo, styles) -> list:
    historico = relatorio.historico_ajustes
    elementos = [Paragraph('Histórico de Ajustes', styles['secao'])]
    dados = [
        ['Indicador', 'Quantidade'],
        ['Quantidade de recontagens', str(historico.recontagens)],
        ['Quantidade de edições', str(historico.edicoes)],
        ['Quantidade de divergências aceitas', str(historico.aceites)],
        [
            'Quantidade de ajustes realizados no Estoque Físico',
            str(historico.ajustes_estoque_fisico),
        ],
    ]
    elementos.append(_tabela(dados, col_widths=[10 * cm, 6 * cm]))
    elementos.append(PageBreak())
    return elementos


def _montar_conclusao(relatorio: RelatorioExecutivoCiclo, styles) -> list:
    elementos = [Paragraph('Conclusão Executiva', styles['secao'])]
    for texto in relatorio.conclusao_executiva:
        elementos.append(Paragraph(f'• {texto}', styles['bullet']))

    elementos.append(Spacer(1, 0.5 * cm))
    elementos.append(Paragraph('<b>Recomendações</b>', styles['texto']))
    resumo = relatorio.resumo
    if resumo.skus_divergentes:
        elementos.append(Paragraph(
            '• Priorizar tratativa das divergências pendentes antes do encerramento definitivo.',
            styles['bullet'],
        ))
    if resumo.skus_pendentes:
        elementos.append(Paragraph(
            f'• Concluir contagem dos {resumo.skus_pendentes} SKU(s) pendentes.',
            styles['bullet'],
        ))
    if relatorio.acuracidade_geral and relatorio.acuracidade_geral < Decimal('95'):
        elementos.append(Paragraph(
            '• Revisar processos de contagem nas embalagens com menor acuracidade.',
            styles['bullet'],
        ))
    if not resumo.skus_divergentes and not resumo.skus_pendentes:
        elementos.append(Paragraph(
            '• Manter padrão operacional e arquivar ciclo para auditoria.',
            styles['bullet'],
        ))
    return elementos


def _criar_cabecalho_rodape(relatorio: RelatorioExecutivoCiclo):
    empresa = _empresa_nome()
    titulo = f'Relatório Executivo — Inventário Cíclico — Ciclo #{relatorio.ciclo.pk}'
    emissor = relatorio.usuario_emissor or relatorio.responsavel

    def _desenhar(canvas, doc):
        canvas.saveState()
        canvas.setFont('Helvetica', 8)
        canvas.setFillColor(colors.HexColor('#444444'))
        canvas.drawString(1.5 * cm, A4[1] - 1.1 * cm, empresa)
        canvas.drawRightString(A4[0] - 1.5 * cm, A4[1] - 1.1 * cm, _fmt_data(relatorio.data_emissao)[:10])
        canvas.line(1.5 * cm, A4[1] - 1.25 * cm, A4[0] - 1.5 * cm, A4[1] - 1.25 * cm)
        canvas.drawString(1.5 * cm, 1.0 * cm, titulo)
        canvas.drawString(1.5 * cm, 0.6 * cm, f'Gerado por: {emissor}')
        canvas.drawRightString(A4[0] - 1.5 * cm, 0.8 * cm, f'Página {doc.page}')
        canvas.restoreState()

    return _desenhar


def gerar_relatorio_executivo_pdf(
    ciclo_id: int,
    filtros: FiltrosCicloConsulta | None = None,
    usuario_emissor=None,
) -> HttpResponse:
    relatorio = obter_relatorio_executivo(ciclo_id, filtros, usuario_emissor)
    styles = _estilos()

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=1.5 * cm,
        rightMargin=1.5 * cm,
        topMargin=1.8 * cm,
        bottomMargin=1.8 * cm,
        title=f'Relatório Executivo Ciclo {ciclo_id}',
        author=_empresa_nome(),
    )

    story = []
    story.extend(_montar_capa(relatorio, styles))
    story.extend(_montar_resumo(relatorio, styles))
    story.extend(_montar_indicadores(relatorio, styles))
    story.extend(_montar_embalagens(relatorio, styles))
    story.extend(_montar_canais(relatorio, styles))
    story.extend(_montar_divergencias(relatorio, styles))
    story.extend(_montar_usuarios(relatorio, styles))
    story.extend(_montar_historico_ajustes(relatorio, styles))
    story.extend(_montar_linha_tempo(relatorio, styles))
    story.extend(_montar_excluidos(relatorio, styles))
    story.extend(_montar_alteracoes(relatorio, styles))
    story.extend(_montar_conclusao(relatorio, styles))

    on_page = _criar_cabecalho_rodape(relatorio)
    doc.build(story, onFirstPage=on_page, onLaterPages=on_page)
    buffer.seek(0)

    response = HttpResponse(buffer.getvalue(), content_type='application/pdf')
    response['Content-Disposition'] = (
        f'attachment; filename="Relatorio_Executivo_Ciclo_{ciclo_id}.pdf"'
    )
    return response
