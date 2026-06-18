"""Relatório executivo gerencial do Inventário Cíclico — dados extraídos do banco."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal

from django.contrib.auth.models import AbstractBaseUser
from django.db.models import Prefetch
from django.utils import timezone

from inventario.models import (
    CicloAuditoriaHistorico,
    CicloEstoqueFisicoAjuste,
    CicloInventario,
    CicloInventarioItem,
    CicloInventarioSku,
)
from inventario.services.ciclico import (
    CiclicoError,
    FiltrosCicloConsulta,
    GrupoProdutoCiclo,
    IndicadorConfronto,
    StatusItemCiclico,
    _aplicar_filtros_consulta,
    _calcular_indicador_confronto,
    _historico_para_dto,
    _obter_ultima_contagem_sku_info,
    _precarregar_canais_skus,
    _sku_para_dto,
    calcular_resumo_ciclo,
    calcular_resumo_skus,
    obter_ciclo_consulta,
)

ABAS_EXCEL_EXECUTIVO = (
    '01_RESUMO',
    '02_INDICADORES',
    '03_EMBALAGENS',
    '04_CANAIS',
    '05_DIVERGENCIAS',
    '06_CONTAGENS',
    '07_RECONTAGENS',
    '08_USUARIOS',
    '09_ALTERACOES',
    '10_AUDITORIA',
)


@dataclass
class IndicadorRelatorio:
    rotulo: str
    emoji: str
    quantidade: int
    percentual: Decimal
    cor: str = ''


@dataclass
class LinhaEmbalagemRelatorio:
    embalagem: str
    planejados: int
    contados: int
    validados: int
    divergentes: int
    pendentes: int
    acuracidade: Decimal
    percentual: Decimal
    diferenca_total: Decimal = Decimal('0')


@dataclass
class LinhaCanalRelatorio:
    canal: str
    planejados: int
    contados: int
    validados: int
    divergentes: int
    acuracidade: Decimal


@dataclass
class LinhaDivergenciaRanking:
    codigo_produto: str
    descricao: str
    embalagem: str
    canal: str
    sap: Decimal
    cosan: Decimal | None
    brida: Decimal | None
    fisico: Decimal
    diferenca: Decimal
    percentual: Decimal
    valor_absoluto: Decimal
    indicador: str
    status: str
    usuario: str
    data: timezone.datetime | None


@dataclass
class LinhaProdutividadeUsuario:
    usuario: str
    contagens: int
    recontagens: int
    validacoes: int
    divergencias: int
    participacao: Decimal


@dataclass
class EventoLinhaTempo:
    data_hora: timezone.datetime
    descricao: str
    usuario: str
    detalhe: str


@dataclass
class LinhaExcluido:
    codigo_produto: str
    descricao: str
    motivo: str
    usuario: str
    data: timezone.datetime | None


@dataclass
class LinhaAlteracao:
    codigo_produto: str
    descricao: str
    quantidade_anterior: str
    quantidade_nova: str
    motivo: str
    usuario: str
    data_hora: timezone.datetime


@dataclass
class HistoricoAjustesResumo:
    recontagens: int = 0
    edicoes: int = 0
    aceites: int = 0
    ajustes_estoque_fisico: int = 0


@dataclass
class RelatorioExecutivoCiclo:
    ciclo: CicloInventario
    responsavel: str
    data_emissao: timezone.datetime
    resumo: object
    acuracidade_geral: Decimal | None
    indicadores: list[IndicadorRelatorio]
    por_embalagem: list[LinhaEmbalagemRelatorio]
    ranking_embalagens_divergencia: list[LinhaEmbalagemRelatorio]
    ranking_embalagens_acuracidade: list[LinhaEmbalagemRelatorio]
    por_canal: list[LinhaCanalRelatorio]
    ranking_divergencias: list[LinhaDivergenciaRanking]
    produtividade_usuarios: list[LinhaProdutividadeUsuario]
    linha_tempo: list[EventoLinhaTempo]
    itens_excluidos: list[LinhaExcluido]
    alteracoes: list[LinhaAlteracao]
    historico_ajustes: HistoricoAjustesResumo = field(default_factory=HistoricoAjustesResumo)
    usuario_emissor: str = ''
    filtros_aplicados: list[str] = field(default_factory=list)
    periodo_analisado: str = ''
    conclusoes_resumo: list[str] = field(default_factory=list)
    conclusao_canal: str = ''
    conclusao_executiva: list[str] = field(default_factory=list)


def _nome_usuario(user: AbstractBaseUser | None) -> str:
    if not user:
        return 'Não informado'
    perfil = getattr(user, 'perfil_operacional', None)
    if perfil and perfil.nome:
        return perfil.nome
    nome = user.get_full_name()
    return nome or user.get_username()


def _nome_usuario_ciclo(ciclo: CicloInventario) -> str:
    return _nome_usuario(ciclo.usuario_criacao)


def _obter_skus_filtrados_relatorio(
    ciclo_id: int,
    filtros: FiltrosCicloConsulta | None,
) -> list[CicloInventarioSku]:
    ciclo = obter_ciclo_consulta(ciclo_id)
    if ciclo is None:
        return []
    skus = _skus_ciclo(ciclo)
    if filtros is not None:
        skus = _aplicar_filtros_consulta(skus, filtros)
    return skus


def _ids_skus(skus: list[CicloInventarioSku]) -> set[int]:
    return {sku.pk for sku in skus}


def _historico_queryset_filtrado(ciclo: CicloInventario, sku_ids: set[int] | None):
    queryset = CicloAuditoriaHistorico.objects.filter(ciclo_sku__ciclo=ciclo)
    if sku_ids is not None:
        queryset = queryset.filter(ciclo_sku_id__in=sku_ids)
    return queryset


def _periodo_analisado(
    ciclo: CicloInventario,
    filtros: FiltrosCicloConsulta | None,
) -> str:
    if filtros and filtros.data_inicial and filtros.data_final:
        return f'{filtros.data_inicial} — {filtros.data_final}'
    if filtros and filtros.data_inicial:
        return f'A partir de {filtros.data_inicial}'
    if filtros and filtros.data_final:
        return f'Até {filtros.data_final}'
    fim = ciclo.data_encerramento or timezone.now()
    return (
        f'{timezone.localtime(ciclo.data_criacao).strftime("%d/%m/%Y")}'
        f' — {timezone.localtime(fim).strftime("%d/%m/%Y")}'
    )


def _descricao_filtros_aplicados(filtros: FiltrosCicloConsulta | None) -> list[str]:
    if filtros is None:
        return ['Nenhum filtro adicional — universo completo do ciclo.']

    rotulos: list[str] = []
    if filtros.sku:
        rotulos.append(f'SKU: {filtros.sku}')
    if filtros.descricao:
        rotulos.append(f'Descrição: {filtros.descricao}')
    if filtros.embalagem:
        rotulos.append(f'Embalagem: {filtros.embalagem}')
    if filtros.setor:
        rotulos.append(f'Setor: {filtros.setor}')
    if filtros.canal:
        rotulos.append(f'Canal: {filtros.canal.capitalize()}')
    if filtros.status:
        rotulos.append(f'Status SKU: {StatusItemCiclico.LABELS.get(filtros.status, filtros.status)}')
    if filtros.usuario:
        rotulos.append(f'Usuário: {filtros.usuario}')
    if filtros.origem:
        rotulos.append(f'Origem: {filtros.origem}')
    if filtros.data:
        rotulos.append(f'Data contagem: {filtros.data}')
    if filtros.data_inicial:
        rotulos.append(f'Data inicial: {filtros.data_inicial}')
    if filtros.data_final:
        rotulos.append(f'Data final: {filtros.data_final}')
    if filtros.somente_divergentes or filtros.divergente:
        rotulos.append('Somente divergentes')
    if filtros.somente_recontagens:
        rotulos.append('Somente recontagens')
    if filtros.somente_validados:
        rotulos.append('Somente validados')
    if not rotulos:
        return ['Nenhum filtro adicional — universo completo do ciclo.']
    return rotulos


def _skus_ciclo(ciclo: CicloInventario, incluir_excluidos: bool = False) -> list[CicloInventarioSku]:
    queryset = CicloInventarioSku.objects.filter(ciclo=ciclo).order_by(
        'ordem_planejamento',
        'codigo_produto',
    )
    skus = list(queryset)
    if incluir_excluidos:
        return skus
    return [sku for sku in skus if sku.status_contagem != StatusItemCiclico.EXCLUIDO]


def _contado(sku: CicloInventarioSku) -> bool:
    return sku.status_contagem not in (
        StatusItemCiclico.PENDENTE,
        StatusItemCiclico.EXCLUIDO,
    )


def _validado(sku: CicloInventarioSku) -> bool:
    return sku.status_contagem in (
        StatusItemCiclico.VALIDADO,
        StatusItemCiclico.VALIDADO_DIVERGENCIA,
    )


def _divergente(sku: CicloInventarioSku) -> bool:
    return sku.status_contagem in (
        StatusItemCiclico.DIVERGENTE,
        StatusItemCiclico.RECONTAGEM,
    )


def _pendente(sku: CicloInventarioSku) -> bool:
    return sku.status_contagem == StatusItemCiclico.PENDENTE


def _acuracidade(contados: int, divergentes: int) -> Decimal:
    if contados == 0:
        return Decimal('0')
    return (
        Decimal(max(contados - divergentes, 0)) / Decimal(contados) * Decimal('100')
    ).quantize(Decimal('0.01'))


def _canal_sku(sku: CicloInventarioSku) -> str:
    cosan = sku.quantidade_cosan or Decimal('0')
    brida = sku.quantidade_brida or Decimal('0')
    if cosan > 0 and brida > 0:
        return 'Cosan / Brida'
    if brida > 0:
        return 'Brida'
    return 'Cosan'


def _taxa_acuracidade_ciclo(
    ciclo: CicloInventario,
    resumo: object,
    *,
    usar_snapshot: bool = True,
) -> Decimal | None:
    if usar_snapshot and ciclo.taxa_acuracidade is not None:
        return ciclo.taxa_acuracidade
    contados_fisico = getattr(resumo, 'skus_conciliados', 0) + getattr(resumo, 'skus_acima_sap', 0) + getattr(
        resumo, 'skus_abaixo_sap', 0,
    )
    if contados_fisico == 0:
        return None
    return (
        Decimal(getattr(resumo, 'skus_conciliados', 0)) / Decimal(contados_fisico) * Decimal('100')
    ).quantize(Decimal('0.01'))


def _parse_edicao_quantidades(motivo: str) -> tuple[str, str]:
    match = re.search(
        r'Quantidade:\s*\n?\s*([\d.,]+)\s*[→\->]\s*([\d.,]+)',
        motivo,
        re.IGNORECASE,
    )
    if match:
        return match.group(1), match.group(2)
    return '', ''


def _motivo_edicao_limpo(motivo: str) -> str:
    if 'Motivo:' in motivo:
        return motivo.split('Motivo:', maxsplit=1)[-1].strip()
    return motivo.strip()


def _gerar_conclusoes_resumo(resumo: object, acuracidade: Decimal | None) -> list[str]:
    conclusoes: list[str] = []
    if acuracidade is not None and acuracidade >= Decimal('95'):
        conclusoes.append('Acuracidade dentro da meta.')
    elif acuracidade is not None and acuracidade < Decimal('90'):
        conclusoes.append('Divergências acima do esperado.')
    if getattr(resumo, 'skus_divergentes', 0) > 0:
        conclusoes.append('Necessária recontagem de itens críticos.')
    if getattr(resumo, 'percentual_executado', 0) >= Decimal('70'):
        conclusoes.append('O ciclo apresenta tendência positiva.')
    elif getattr(resumo, 'percentual_executado', 0) < Decimal('30'):
        conclusoes.append('Execução do ciclo abaixo do planejado.')
    if not conclusoes:
        conclusoes.append('Ciclo em fase inicial de execução.')
    return conclusoes


def _gerar_conclusao_canal(por_canal: list[LinhaCanalRelatorio]) -> str:
    total_div = sum(item.divergentes for item in por_canal)
    if total_div == 0:
        return 'Nenhuma divergência registrada por canal neste ciclo.'
    maior = max(por_canal, key=lambda item: item.divergentes)
    if maior.divergentes == 0:
        return 'Divergências distribuídas de forma equilibrada entre os canais.'
    pct = (Decimal(maior.divergentes) / Decimal(total_div) * Decimal('100')).quantize(Decimal('0.01'))
    return f'O canal {maior.canal} concentrou {pct}% das divergências do ciclo.'


def _gerar_conclusao_executiva(
    relatorio: RelatorioExecutivoCiclo,
) -> list[str]:
    resumo = relatorio.resumo
    linhas: list[str] = []

    if relatorio.acuracidade_geral is not None:
        linhas.append(f'O ciclo apresentou acuracidade de {relatorio.acuracidade_geral}%.')
    else:
        linhas.append('Acuracidade ainda não calculável — aguardando mais contagens.')

    if relatorio.ranking_embalagens_divergencia:
        nomes = ', '.join(
            item.embalagem for item in relatorio.ranking_embalagens_divergencia[:3]
            if item.divergentes > 0
        )
        if nomes:
            linhas.append(f'As divergências concentraram-se nas embalagens {nomes}.')

    recontagens = sum(item.recontagens for item in relatorio.produtividade_usuarios)
    if recontagens:
        linhas.append(f'Foi necessária recontagem em {recontagens} registro(s) de auditoria.')

    if relatorio.resumo.skus_pendentes == 0 and relatorio.resumo.skus_divergentes == 0:
        linhas.append('O processo encontra-se dentro dos padrões operacionais.')
    elif relatorio.resumo.skus_pendentes > 0:
        linhas.append(f'Permanecem {relatorio.resumo.skus_pendentes} SKU(s) pendentes de contagem.')

    if relatorio.resumo.skus_divergentes > 0:
        linhas.append(
            f'Existem {relatorio.resumo.skus_divergentes} SKU(s) com divergência pendente de tratativa.',
        )

    if relatorio.produtividade_usuarios:
        top = relatorio.produtividade_usuarios[0]
        linhas.append(f'Principal participação operacional: {top.usuario} ({top.participacao}%).')

    linhas.append(f'Percentual de execução do ciclo: {resumo.percentual_executado}%.')
    linhas.append(f'Produtos auditados (contados): {resumo.skus_contados}.')
    if relatorio.historico_ajustes.ajustes_estoque_fisico:
        linhas.append(
            'Produtos ajustados no Estoque Físico: '
            f'{relatorio.historico_ajustes.ajustes_estoque_fisico}.',
        )
    return linhas


def _montar_indicadores(skus: list[CicloInventarioSku]) -> list[IndicadorRelatorio]:
    total = len(skus) or 1
    verde = laranja = vermelho = 0
    for sku in skus:
        if sku.quantidade_fisica is None:
            continue
        _, indicador, _ = _calcular_indicador_confronto(
            sku.quantidade_fisica,
            sku.quantidade_sap,
        )
        if indicador == IndicadorConfronto.VERDE:
            verde += 1
        elif indicador == IndicadorConfronto.LARANJA:
            laranja += 1
        elif indicador == IndicadorConfronto.VERMELHO:
            vermelho += 1

    base = verde + laranja + vermelho or total
    return [
        IndicadorRelatorio(
            'Físico = SAP', '🟢', verde,
            (Decimal(verde) / Decimal(base) * Decimal('100')).quantize(Decimal('0.01')),
            '#28a745',
        ),
        IndicadorRelatorio(
            'Físico > SAP', '🟠', laranja,
            (Decimal(laranja) / Decimal(base) * Decimal('100')).quantize(Decimal('0.01')),
            '#fd7e14',
        ),
        IndicadorRelatorio(
            'Físico < SAP', '🔴', vermelho,
            (Decimal(vermelho) / Decimal(base) * Decimal('100')).quantize(Decimal('0.01')),
            '#dc3545',
        ),
    ]


def _montar_embalagens(skus: list[CicloInventarioSku], total_geral: int) -> list[LinhaEmbalagemRelatorio]:
    emb_map: dict[str, dict[str, int | Decimal]] = {}
    for sku in skus:
        chave = (sku.embalagem or 'Sem embalagem').upper()
        bucket = emb_map.setdefault(
            chave,
            {
                'planejados': 0,
                'contados': 0,
                'validados': 0,
                'divergentes': 0,
                'pendentes': 0,
                'diferenca_total': Decimal('0'),
            },
        )
        bucket['planejados'] += 1
        if _contado(sku):
            bucket['contados'] += 1
        if _validado(sku):
            bucket['validados'] += 1
        if _divergente(sku):
            bucket['divergentes'] += 1
        if _pendente(sku):
            bucket['pendentes'] += 1
        if sku.diferenca is not None and _contado(sku):
            bucket['diferenca_total'] += abs(sku.diferenca)

    linhas = []
    for chave, dados in sorted(emb_map.items()):
        pct = (
            Decimal(dados['planejados']) / Decimal(total_geral or 1) * Decimal('100')
        ).quantize(Decimal('0.01'))
        linhas.append(LinhaEmbalagemRelatorio(
            embalagem=chave,
            planejados=dados['planejados'],
            contados=dados['contados'],
            validados=dados['validados'],
            divergentes=dados['divergentes'],
            pendentes=dados['pendentes'],
            acuracidade=_acuracidade(dados['contados'], dados['divergentes']),
            percentual=pct,
            diferenca_total=Decimal(dados['diferenca_total']).quantize(Decimal('0.01')),
        ))
    return linhas


def _montar_canais(skus: list[CicloInventarioSku]) -> list[LinhaCanalRelatorio]:
    filtros = {
        'Cosan': lambda sku: (sku.quantidade_cosan or Decimal('0')) > 0,
        'Brida': lambda sku: (sku.quantidade_brida or Decimal('0')) > 0,
    }
    linhas: list[LinhaCanalRelatorio] = []
    for nome, filtro in filtros.items():
        subset = [sku for sku in skus if filtro(sku)]
        contados = sum(1 for sku in subset if _contado(sku))
        validados = sum(1 for sku in subset if _validado(sku))
        divergentes = sum(1 for sku in subset if _divergente(sku))
        linhas.append(LinhaCanalRelatorio(
            canal=nome,
            planejados=len(subset),
            contados=contados,
            validados=validados,
            divergentes=divergentes,
            acuracidade=_acuracidade(contados, divergentes),
        ))
    return linhas


def _montar_ranking_divergencias(skus: list[CicloInventarioSku]) -> list[LinhaDivergenciaRanking]:
    ranking: list[LinhaDivergenciaRanking] = []
    for sku in skus:
        if sku.quantidade_fisica is None or sku.diferenca is None or sku.diferenca == 0:
            continue
        sap = sku.quantidade_sap
        percentual = (
            (sku.diferenca / sap * Decimal('100')) if sap else Decimal('0')
        ).quantize(Decimal('0.01'))
        _, indicador, _ = _calcular_indicador_confronto(
            sku.quantidade_fisica,
            sku.quantidade_sap,
        )
        _, _, usuario, data, _ = _obter_ultima_contagem_sku_info(sku)
        ranking.append(LinhaDivergenciaRanking(
            codigo_produto=sku.codigo_produto,
            descricao=sku.descricao,
            embalagem=sku.embalagem or '',
            canal=_canal_sku(sku),
            sap=sap,
            cosan=sku.quantidade_cosan,
            brida=sku.quantidade_brida,
            fisico=sku.quantidade_fisica,
            diferenca=sku.diferenca,
            percentual=percentual,
            valor_absoluto=abs(sku.diferenca),
            indicador=indicador or '',
            status=StatusItemCiclico.LABELS[sku.status_contagem],
            usuario=usuario or '—',
            data=data,
        ))
    ranking.sort(key=lambda item: item.valor_absoluto, reverse=True)
    return ranking[:20]


def _montar_produtividade(
    ciclo: CicloInventario,
    sku_ids: set[int] | None = None,
) -> list[LinhaProdutividadeUsuario]:
    stats: dict[str, dict[str, int]] = {}
    historico = _historico_queryset_filtrado(ciclo, sku_ids).select_related(
        'usuario',
        'usuario__perfil_operacional',
        'ciclo_sku',
    )

    for registro in historico:
        nome = _historico_para_dto(registro).usuario
        bucket = stats.setdefault(
            nome,
            {'contagens': 0, 'recontagens': 0, 'validacoes': 0, 'divergencias': 0},
        )
        if registro.tipo in (
            CicloAuditoriaHistorico.TipoRegistro.CONTAGEM,
            CicloAuditoriaHistorico.TipoRegistro.CONSOLIDACAO,
        ):
            bucket['contagens'] += 1
            if registro.diferenca and registro.diferenca != 0:
                bucket['divergencias'] += 1
        elif registro.tipo == CicloAuditoriaHistorico.TipoRegistro.RECONTAGEM:
            bucket['recontagens'] += 1
            if registro.diferenca and registro.diferenca != 0:
                bucket['divergencias'] += 1
        elif registro.tipo == CicloAuditoriaHistorico.TipoRegistro.VALIDACAO:
            bucket['validacoes'] += 1

    total_contagens = sum(item['contagens'] + item['recontagens'] for item in stats.values()) or 1
    linhas = [
        LinhaProdutividadeUsuario(
            usuario=nome,
            contagens=dados['contagens'],
            recontagens=dados['recontagens'],
            validacoes=dados['validacoes'],
            divergencias=dados['divergencias'],
            participacao=(
                Decimal(dados['contagens'] + dados['recontagens']) / Decimal(total_contagens) * Decimal('100')
            ).quantize(Decimal('0.01')),
        )
        for nome, dados in stats.items()
        if nome != 'Não informado'
    ]
    linhas.sort(key=lambda item: item.contagens + item.recontagens, reverse=True)
    return linhas


def _montar_linha_tempo(
    ciclo: CicloInventario,
    sku_ids: set[int] | None = None,
) -> list[EventoLinhaTempo]:
    eventos: list[EventoLinhaTempo] = [
        EventoLinhaTempo(
            data_hora=ciclo.data_criacao,
            descricao='Ciclo criado',
            usuario=_nome_usuario_ciclo(ciclo),
            detalhe=f'{ciclo.quantidade_skus_planejados or 0} SKU(s) planejados',
        ),
    ]

    historico = _historico_queryset_filtrado(ciclo, sku_ids).select_related(
        'ciclo_sku',
        'usuario',
        'usuario__perfil_operacional',
    ).order_by('data_hora')

    rotulos = {
        CicloAuditoriaHistorico.TipoRegistro.CONTAGEM: 'Contagem registrada',
        CicloAuditoriaHistorico.TipoRegistro.RECONTAGEM: 'Recontagem executada',
        CicloAuditoriaHistorico.TipoRegistro.CONSOLIDACAO: 'Consolidação de SKU',
        CicloAuditoriaHistorico.TipoRegistro.EXCLUSAO: 'SKU excluído do ciclo',
        CicloAuditoriaHistorico.TipoRegistro.VALIDACAO: 'Divergência aceita / validação',
        CicloAuditoriaHistorico.TipoRegistro.EDICAO: 'Edição manual registrada',
    }

    for registro in historico:
        sku = registro.ciclo_sku
        detalhe = registro.get_tipo_display()
        if sku:
            detalhe = f'{sku.codigo_produto} — {detalhe}'
            if registro.codigo_posicao:
                detalhe += f' ({registro.codigo_posicao})'
        eventos.append(EventoLinhaTempo(
            data_hora=registro.data_hora,
            descricao=rotulos.get(registro.tipo, registro.get_tipo_display()),
            usuario=_historico_para_dto(registro).usuario,
            detalhe=detalhe,
        ))

    if ciclo.data_encerramento:
        eventos.append(EventoLinhaTempo(
            data_hora=ciclo.data_encerramento,
            descricao='Ciclo encerrado',
            usuario='Sistema',
            detalhe=ciclo.get_status_ciclo_display(),
        ))

    eventos.sort(key=lambda item: item.data_hora)
    return eventos


def _montar_excluidos(
    ciclo: CicloInventario,
    sku_ids: set[int] | None = None,
) -> list[LinhaExcluido]:
    skus = CicloInventarioSku.objects.filter(
        ciclo=ciclo,
        status_contagem=StatusItemCiclico.EXCLUIDO,
    ).select_related('usuario_exclusao', 'usuario_exclusao__perfil_operacional')
    if sku_ids is not None:
        skus = skus.filter(pk__in=sku_ids)

    return [
        LinhaExcluido(
            codigo_produto=sku.codigo_produto,
            descricao=sku.descricao,
            motivo=sku.motivo_exclusao or '—',
            usuario=_nome_usuario(sku.usuario_exclusao),
            data=sku.data_exclusao,
        )
        for sku in skus
    ]


def _montar_historico_ajustes_resumo(
    ciclo: CicloInventario,
    sku_ids: set[int] | None = None,
) -> HistoricoAjustesResumo:
    historico = _historico_queryset_filtrado(ciclo, sku_ids)
    ajustes = CicloEstoqueFisicoAjuste.objects.filter(ciclo=ciclo)
    if sku_ids is not None:
        ajustes = ajustes.filter(ciclo_sku_id__in=sku_ids)
    return HistoricoAjustesResumo(
        recontagens=historico.filter(
            tipo=CicloAuditoriaHistorico.TipoRegistro.RECONTAGEM,
        ).count(),
        edicoes=historico.filter(
            tipo=CicloAuditoriaHistorico.TipoRegistro.EDICAO,
        ).count(),
        aceites=historico.filter(
            tipo=CicloAuditoriaHistorico.TipoRegistro.VALIDACAO,
        ).count(),
        ajustes_estoque_fisico=ajustes.count(),
    )


def _montar_alteracoes(
    ciclo: CicloInventario,
    sku_ids: set[int] | None = None,
) -> list[LinhaAlteracao]:
    registros = _historico_queryset_filtrado(ciclo, sku_ids).filter(
        tipo=CicloAuditoriaHistorico.TipoRegistro.EDICAO,
    ).select_related(
        'ciclo_sku',
        'usuario',
        'usuario__perfil_operacional',
    ).order_by('data_hora')

    linhas: list[LinhaAlteracao] = []
    for registro in registros:
        sku = registro.ciclo_sku
        qtd_ant, qtd_nova = _parse_edicao_quantidades(registro.motivo)
        linhas.append(LinhaAlteracao(
            codigo_produto=sku.codigo_produto if sku else '—',
            descricao=sku.descricao if sku else '—',
            quantidade_anterior=qtd_ant or '—',
            quantidade_nova=qtd_nova or str(registro.quantidade_fisica),
            motivo=_motivo_edicao_limpo(registro.motivo) or '—',
            usuario=_historico_para_dto(registro).usuario,
            data_hora=registro.data_hora,
        ))
    return linhas


def obter_relatorio_executivo(
    ciclo_id: int,
    filtros: FiltrosCicloConsulta | None = None,
    usuario_emissor: AbstractBaseUser | None = None,
) -> RelatorioExecutivoCiclo:
    ciclo = obter_ciclo_consulta(ciclo_id)
    if ciclo is None:
        raise CiclicoError('Ciclo não encontrado.')

    skus = _obter_skus_filtrados_relatorio(ciclo_id, filtros)
    sku_ids = _ids_skus(skus)
    filtros_ativos = (
        filtros is not None
        and _descricao_filtros_aplicados(filtros)[0]
        != 'Nenhum filtro adicional — universo completo do ciclo.'
    )

    resumo = calcular_resumo_skus(skus)
    indicadores = _montar_indicadores(skus)
    por_embalagem = _montar_embalagens(skus, resumo.total_skus)
    por_canal = _montar_canais(skus)
    ranking_divergencias = _montar_ranking_divergencias(skus)
    acuracidade = _taxa_acuracidade_ciclo(ciclo, resumo, usar_snapshot=not filtros_ativos)

    ranking_div_emb = sorted(
        [item for item in por_embalagem if item.divergentes > 0],
        key=lambda item: item.divergentes,
        reverse=True,
    )[:5]
    ranking_acu_emb = sorted(
        [item for item in por_embalagem if item.contados > 0],
        key=lambda item: item.acuracidade,
        reverse=True,
    )[:5]

    relatorio = RelatorioExecutivoCiclo(
        ciclo=ciclo,
        responsavel=_nome_usuario_ciclo(ciclo),
        data_emissao=timezone.now(),
        resumo=resumo,
        acuracidade_geral=acuracidade,
        indicadores=indicadores,
        por_embalagem=por_embalagem,
        ranking_embalagens_divergencia=ranking_div_emb,
        ranking_embalagens_acuracidade=ranking_acu_emb,
        por_canal=por_canal,
        ranking_divergencias=ranking_divergencias,
        produtividade_usuarios=_montar_produtividade(ciclo, sku_ids if filtros_ativos else None),
        linha_tempo=_montar_linha_tempo(ciclo, sku_ids if filtros_ativos else None),
        itens_excluidos=_montar_excluidos(ciclo, sku_ids if filtros_ativos else None),
        alteracoes=_montar_alteracoes(ciclo, sku_ids if filtros_ativos else None),
        historico_ajustes=_montar_historico_ajustes_resumo(
            ciclo,
            sku_ids if filtros_ativos else None,
        ),
        usuario_emissor=_nome_usuario(usuario_emissor),
        filtros_aplicados=_descricao_filtros_aplicados(filtros),
        periodo_analisado=_periodo_analisado(ciclo, filtros),
    )
    relatorio.conclusoes_resumo = _gerar_conclusoes_resumo(resumo, acuracidade)
    relatorio.conclusao_canal = _gerar_conclusao_canal(por_canal)
    relatorio.conclusao_executiva = _gerar_conclusao_executiva(relatorio)
    return relatorio


def obter_grupos_consulta_ciclo(
    ciclo_id: int,
    filtros: FiltrosCicloConsulta | None = None,
    *,
    incluir_historico: bool = True,
) -> list[GrupoProdutoCiclo]:
    ciclo = obter_ciclo_consulta(ciclo_id)
    if ciclo is None:
        return []

    posicoes_qs = CicloInventarioItem.objects.select_related(
        'usuario_contagem',
        'usuario_contagem__perfil_operacional',
    ).order_by('codigo_posicao')
    prefetch: list = [Prefetch('posicoes', queryset=posicoes_qs)]
    if incluir_historico:
        historico_qs = CicloAuditoriaHistorico.objects.select_related(
            'usuario',
            'usuario__perfil_operacional',
        ).order_by('-data_hora')
        prefetch.append(Prefetch('historico', queryset=historico_qs))

    skus = CicloInventarioSku.objects.filter(ciclo=ciclo).prefetch_related(
        *prefetch,
    ).order_by('ordem_planejamento', 'codigo_produto')

    skus_list = _aplicar_filtros_consulta(list(skus), filtros)
    canais_por_produto = _precarregar_canais_skus(skus_list)
    grupos: list[GrupoProdutoCiclo] = []
    for sku in skus_list:
        dto = _sku_para_dto(
            sku,
            incluir_posicoes=True,
            incluir_historico=incluir_historico,
            canais_por_produto=canais_por_produto,
        )
        grupos.append(GrupoProdutoCiclo(
            pk=sku.pk,
            codigo_produto=sku.codigo_produto,
            descricao=sku.descricao,
            embalagem=sku.embalagem,
            sap_total=sku.quantidade_sap,
            cosan_total=dto.quantidade_cosan,
            brida_total=dto.quantidade_brida,
            fisico_total=sku.quantidade_fisica,
            diferenca_cosan=dto.diferenca_cosan,
            indicador_cosan=dto.indicador_sap,
            indicador_cosan_tooltip=dto.indicador_sap_tooltip,
            indicador_sap=dto.indicador_sap,
            indicador_sap_tooltip=dto.indicador_sap_tooltip,
            setor=sku.setor,
            status_contagem=sku.status_contagem,
            status_label=StatusItemCiclico.LABELS[sku.status_contagem],
            status_classe=StatusItemCiclico.CLASSES[sku.status_contagem],
            usuarios=sku.usuarios_contagem_nomes,
            ultima_contagem=dto.ultima_data,
            ultima_origem_label=dto.ultima_origem_label,
            ultimo_dispositivo=dto.ultimo_dispositivo,
            posicoes=dto.posicoes,
            historico=dto.historico,
        ))
    return grupos


def _fmt_data(valor: timezone.datetime | None) -> str:
    if valor is None:
        return '—'
    return timezone.localtime(valor).strftime('%d/%m/%Y %H:%M')


def _linhas_resumo_relatorio(relatorio: RelatorioExecutivoCiclo) -> list[dict]:
    ciclo = relatorio.ciclo
    resumo = relatorio.resumo
    periodo_fim = ciclo.data_encerramento or timezone.now()
    return [
        {'Campo': 'Ciclo', 'Valor': f'#{ciclo.pk}'},
        {'Campo': 'Status', 'Valor': ciclo.get_status_ciclo_display()},
        {'Campo': 'Responsável', 'Valor': relatorio.responsavel},
        {
            'Campo': 'Período',
            'Valor': (
                f'{timezone.localtime(ciclo.data_criacao).strftime("%d/%m/%Y")}'
                f' — {timezone.localtime(periodo_fim).strftime("%d/%m/%Y")}'
            ),
        },
        {'Campo': 'Data emissão', 'Valor': _fmt_data(relatorio.data_emissao)},
        {'Campo': 'SKUs planejados', 'Valor': resumo.total_skus},
        {'Campo': 'SKUs contados', 'Valor': resumo.skus_contados},
        {'Campo': 'SKUs validados', 'Valor': resumo.skus_validados},
        {'Campo': 'SKUs divergentes', 'Valor': resumo.skus_divergentes},
        {'Campo': 'SKUs pendentes', 'Valor': resumo.skus_pendentes},
        {'Campo': 'SKUs excluídos', 'Valor': resumo.skus_excluidos},
        {'Campo': 'Percentual executado', 'Valor': f'{resumo.percentual_executado}%'},
        {
            'Campo': 'Acuracidade geral',
            'Valor': (
                f'{relatorio.acuracidade_geral}%'
                if relatorio.acuracidade_geral is not None
                else '—'
            ),
        },
        {'Campo': 'Critério', 'Valor': ciclo.criterio_utilizado or '—'},
        {'Campo': 'Canal', 'Valor': ciclo.canal_utilizado or '—'},
        {
            'Campo': 'Embalagens',
            'Valor': ', '.join(ciclo.embalagens_filtro) if ciclo.embalagens_filtro else 'Todas',
        },
        {
            'Campo': 'SKUs por dia',
            'Valor': ciclo.skus_por_dia if ciclo.skus_por_dia else '—',
        },
        {'Campo': 'Conclusões', 'Valor': ' | '.join(relatorio.conclusoes_resumo)},
    ]


def obter_dados_exportacao_premium(
    ciclo_id: int,
    filtros: FiltrosCicloConsulta | None = None,
) -> dict[str, list[dict]]:
    relatorio = obter_relatorio_executivo(ciclo_id, filtros)
    ciclo = relatorio.ciclo
    sku_ids = None
    if filtros is not None and _descricao_filtros_aplicados(filtros)[0] != (
        'Nenhum filtro adicional — universo completo do ciclo.'
    ):
        sku_ids = _ids_skus(_obter_skus_filtrados_relatorio(ciclo_id, filtros))

    aba_indicadores = [
        {
            'Indicador': item.rotulo,
            'Quantidade': item.quantidade,
            'Percentual (%)': float(item.percentual),
        }
        for item in relatorio.indicadores
    ]
    aba_embalagens = [
        {
            'Embalagem': item.embalagem,
            'Planejados': item.planejados,
            'Contados': item.contados,
            'Validados': item.validados,
            'Divergentes': item.divergentes,
            'Pendentes': item.pendentes,
            'Acuracidade (%)': float(item.acuracidade),
            'Percentual (%)': float(item.percentual),
            'Diferença Total': float(item.diferenca_total),
        }
        for item in relatorio.por_embalagem
    ]
    aba_canais = [
        {
            'Canal': item.canal,
            'Planejados': item.planejados,
            'Contados': item.contados,
            'Validados': item.validados,
            'Divergentes': item.divergentes,
            'Acuracidade (%)': float(item.acuracidade),
        }
        for item in relatorio.por_canal
    ]
    aba_divergencias = [
        {
            'SKU': item.codigo_produto,
            'Descrição': item.descricao,
            'Embalagem': item.embalagem,
            'Canal': item.canal,
            'SAP': float(item.sap),
            'Cosan': float(item.cosan) if item.cosan is not None else None,
            'Brida': float(item.brida) if item.brida is not None else None,
            'Físico': float(item.fisico),
            'Diferença': float(item.diferenca),
            '%': float(item.percentual),
            'Status': item.status,
            'Usuário': item.usuario,
            'Data': _fmt_data(item.data),
        }
        for item in relatorio.ranking_divergencias
    ]
    aba_usuarios = [
        {
            'Usuário': item.usuario,
            'Contagens': item.contagens,
            'Recontagens': item.recontagens,
            'Validações': item.validacoes,
            'Divergências': item.divergencias,
            'Participação (%)': float(item.participacao),
        }
        for item in relatorio.produtividade_usuarios
    ]
    aba_alteracoes = [
        {
            'SKU': item.codigo_produto,
            'Descrição': item.descricao,
            'Qtd. anterior': item.quantidade_anterior,
            'Qtd. nova': item.quantidade_nova,
            'Motivo': item.motivo,
            'Usuário': item.usuario,
            'Data/Hora': _fmt_data(item.data_hora),
        }
        for item in relatorio.alteracoes
    ]

    aba_contagens = []
    aba_recontagens = []
    aba_auditoria = []

    historico_qs = _historico_queryset_filtrado(ciclo, sku_ids).select_related(
        'ciclo_sku',
        'usuario',
    ).order_by('data_hora')

    for registro in historico_qs:
        sku = registro.ciclo_sku
        linha_base = {
            'SKU': sku.codigo_produto if sku else '—',
            'Descrição': sku.descricao if sku else '—',
            'Tipo': registro.get_tipo_display(),
            'Posição': registro.codigo_posicao,
            'Quantidade': float(registro.quantidade_fisica),
            'Usuário': _historico_para_dto(registro).usuario,
            'Data/Hora': _fmt_data(registro.data_hora),
            'Motivo': registro.motivo,
        }
        if registro.tipo == CicloAuditoriaHistorico.TipoRegistro.RECONTAGEM:
            aba_recontagens.append(linha_base)
        elif registro.tipo in (
            CicloAuditoriaHistorico.TipoRegistro.CONTAGEM,
            CicloAuditoriaHistorico.TipoRegistro.CONSOLIDACAO,
        ):
            aba_contagens.append(linha_base)
        aba_auditoria.append(linha_base)

    for exc in relatorio.itens_excluidos:
        aba_auditoria.append({
            'SKU': exc.codigo_produto,
            'Descrição': exc.descricao,
            'Tipo': 'Exclusão',
            'Posição': '',
            'Quantidade': '',
            'Usuário': exc.usuario,
            'Data/Hora': _fmt_data(exc.data),
            'Motivo': exc.motivo,
        })

    return {
        '01_RESUMO': _linhas_resumo_relatorio(relatorio),
        '02_INDICADORES': aba_indicadores,
        '03_EMBALAGENS': aba_embalagens,
        '04_CANAIS': aba_canais,
        '05_DIVERGENCIAS': aba_divergencias,
        '06_CONTAGENS': aba_contagens,
        '07_RECONTAGENS': aba_recontagens,
        '08_USUARIOS': aba_usuarios,
        '09_ALTERACOES': aba_alteracoes,
        '10_AUDITORIA': aba_auditoria,
    }
