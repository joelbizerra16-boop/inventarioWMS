"""Consulta de histórico de ciclos cíclicos (separado do Inventário Geral)."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from django.db.models import Q

from inventario.models import (
    CicloAuditoriaHistorico,
    CicloEstoqueFisicoAjuste,
    CicloInventario,
)
from inventario.services.ciclico import (
    StatusCiclo,
    calcular_resumo_ciclo,
)
from inventario.services.ciclico_relatorio import _nome_usuario


class StatusHistoricoCiclo:
    EM_ANDAMENTO = 'EM_ANDAMENTO'
    ENCERRADO = 'ENCERRADO'
    CANCELADO = 'CANCELADO'
    REABERTO = 'REABERTO'

    LABELS = {
        EM_ANDAMENTO: 'Em andamento',
        ENCERRADO: 'Encerrado',
        CANCELADO: 'Cancelado',
        REABERTO: 'Reaberto',
    }

    BADGES = {
        EM_ANDAMENTO: 'primary',
        ENCERRADO: 'success',
        CANCELADO: 'dark',
        REABERTO: 'warning',
    }

    FILTROS = [
        ('', 'Todos'),
        (EM_ANDAMENTO, 'Em andamento'),
        (ENCERRADO, 'Encerrado'),
        (CANCELADO, 'Cancelado'),
        (REABERTO, 'Reaberto'),
    ]


def obter_status_exibicao_ciclo(ciclo: CicloInventario) -> tuple[str, str]:
    if ciclo.status_ciclo == StatusCiclo.ARQUIVADO:
        return StatusHistoricoCiclo.CANCELADO, StatusHistoricoCiclo.LABELS[StatusHistoricoCiclo.CANCELADO]
    if ciclo.status_ciclo == StatusCiclo.ENCERRADO:
        return StatusHistoricoCiclo.ENCERRADO, StatusHistoricoCiclo.LABELS[StatusHistoricoCiclo.ENCERRADO]
    if (
        ciclo.status_ciclo == StatusCiclo.ATIVO
        and ciclo.data_encerramento is None
        and ciclo.quantidade_skus_contados is not None
    ):
        return StatusHistoricoCiclo.REABERTO, StatusHistoricoCiclo.LABELS[StatusHistoricoCiclo.REABERTO]
    return StatusHistoricoCiclo.EM_ANDAMENTO, StatusHistoricoCiclo.LABELS[StatusHistoricoCiclo.EM_ANDAMENTO]


def _usuario_responsavel(ciclo: CicloInventario) -> str:
    if ciclo.usuario_encerramento_id:
        return _nome_usuario(ciclo.usuario_encerramento)
    return _nome_usuario(ciclo.usuario_criacao)


def _metricas_ciclo(ciclo: CicloInventario) -> dict[str, int | Decimal | None]:
    if ciclo.status_ciclo in (StatusCiclo.ENCERRADO, StatusCiclo.ARQUIVADO):
        return {
            'planejados': ciclo.quantidade_skus_planejados or 0,
            'contados': ciclo.quantidade_skus_contados or 0,
            'validados': ciclo.quantidade_skus_validados or 0,
            'divergentes': ciclo.quantidade_skus_divergentes or 0,
            'pendentes': max(
                (ciclo.quantidade_skus_planejados or 0) - (ciclo.quantidade_skus_contados or 0),
                0,
            ),
            'acuracidade': ciclo.taxa_acuracidade,
        }

    resumo = calcular_resumo_ciclo(ciclo)
    return {
        'planejados': resumo.total_skus,
        'contados': resumo.skus_contados,
        'validados': resumo.skus_validados,
        'divergentes': resumo.skus_divergentes,
        'pendentes': resumo.skus_pendentes,
        'acuracidade': ciclo.taxa_acuracidade,
    }


@dataclass
class CicloHistoricoLinha:
    pk: int
    data_criacao: datetime
    data_encerramento: datetime | None
    usuario_responsavel: str
    skus_planejados: int
    skus_contados: int
    validados: int
    divergentes: int
    acuracidade: Decimal | None
    status_codigo: str
    status_label: str
    status_badge: str


def _linha_de_ciclo(ciclo: CicloInventario) -> CicloHistoricoLinha:
    metricas = _metricas_ciclo(ciclo)
    status_codigo, status_label = obter_status_exibicao_ciclo(ciclo)
    return CicloHistoricoLinha(
        pk=ciclo.pk,
        data_criacao=ciclo.data_criacao,
        data_encerramento=ciclo.data_encerramento,
        usuario_responsavel=_usuario_responsavel(ciclo),
        skus_planejados=int(metricas['planejados']),
        skus_contados=int(metricas['contados']),
        validados=int(metricas['validados']),
        divergentes=int(metricas['divergentes']),
        acuracidade=metricas['acuracidade'],
        status_codigo=status_codigo,
        status_label=status_label,
        status_badge=StatusHistoricoCiclo.BADGES[status_codigo],
    )


def _filtrar_por_status_exibicao(
    ciclos: list[CicloInventario],
    status_filtro: str,
) -> list[CicloInventario]:
    if not status_filtro:
        return ciclos
    return [
        ciclo for ciclo in ciclos
        if obter_status_exibicao_ciclo(ciclo)[0] == status_filtro
    ]


def listar_historico_ciclos(
    *,
    termo: str = '',
    status_filtro: str = '',
) -> list[CicloHistoricoLinha]:
    queryset = CicloInventario.objects.select_related(
        'usuario_criacao',
        'usuario_criacao__perfil_operacional',
        'usuario_encerramento',
        'usuario_encerramento__perfil_operacional',
    ).order_by('-data_criacao')

    termo = termo.strip()
    if termo:
        filtros = Q()
        if termo.isdigit():
            filtros |= Q(pk=int(termo))
        filtros |= Q(usuario_criacao__perfil_operacional__nome__icontains=termo)
        filtros |= Q(usuario_encerramento__perfil_operacional__nome__icontains=termo)
        queryset = queryset.filter(filtros)

    ciclos = _filtrar_por_status_exibicao(list(queryset), status_filtro)
    return [_linha_de_ciclo(ciclo) for ciclo in ciclos]


@dataclass
class CicloHistoricoDetalhe:
    ciclo: CicloInventario
    linha: CicloHistoricoLinha
    planejados: int
    contados: int
    pendentes: int
    validados: int
    divergentes: int
    acuracidade: Decimal | None
    usuario_criacao: str
    usuario_encerramento: str
    criterio_utilizado: str
    canal_utilizado: str


def obter_detalhe_historico_ciclo(ciclo_id: int) -> CicloHistoricoDetalhe | None:
    ciclo = CicloInventario.objects.select_related(
        'usuario_criacao',
        'usuario_criacao__perfil_operacional',
        'usuario_encerramento',
        'usuario_encerramento__perfil_operacional',
    ).filter(pk=ciclo_id).first()
    if ciclo is None:
        return None

    metricas = _metricas_ciclo(ciclo)
    return CicloHistoricoDetalhe(
        ciclo=ciclo,
        linha=_linha_de_ciclo(ciclo),
        planejados=int(metricas['planejados']),
        contados=int(metricas['contados']),
        pendentes=int(metricas['pendentes']),
        validados=int(metricas['validados']),
        divergentes=int(metricas['divergentes']),
        acuracidade=metricas['acuracidade'],
        usuario_criacao=_nome_usuario(ciclo.usuario_criacao),
        usuario_encerramento=_nome_usuario(ciclo.usuario_encerramento),
        criterio_utilizado=ciclo.criterio_utilizado or '—',
        canal_utilizado=ciclo.canal_utilizado or '—',
    )


@dataclass
class AuditoriaRegistroHistorico:
    data_hora: datetime
    resumo: str
    detalhe: str
    usuario: str
    sku: str
    posicao: str


@dataclass
class AuditoriaSecaoHistorico:
    titulo: str
    registros: list[AuditoriaRegistroHistorico]


def _registro_historico(
    *,
    data_hora: datetime,
    resumo: str,
    detalhe: str = '',
    usuario=None,
    sku: str = '—',
    posicao: str = '—',
) -> AuditoriaRegistroHistorico:
    return AuditoriaRegistroHistorico(
        data_hora=data_hora,
        resumo=resumo,
        detalhe=detalhe,
        usuario=_nome_usuario(usuario),
        sku=sku,
        posicao=posicao,
    )


def obter_auditoria_historico_ciclo(ciclo_id: int) -> tuple[CicloHistoricoDetalhe | None, list[AuditoriaSecaoHistorico]]:
    detalhe = obter_detalhe_historico_ciclo(ciclo_id)
    if detalhe is None:
        return None, []

    ciclo = detalhe.ciclo
    secoes: list[AuditoriaSecaoHistorico] = []

    secoes.append(AuditoriaSecaoHistorico(
        titulo='Criação do ciclo',
        registros=[
            _registro_historico(
                data_hora=ciclo.data_criacao,
                resumo=f'Ciclo #{ciclo.pk} criado',
                detalhe=(
                    f'{ciclo.quantidade_skus_planejados or 0} SKU(s) planejados '
                    f'congelados do SAP.'
                ),
                usuario=ciclo.usuario_criacao,
            ),
        ],
    ))

    lote_detalhe = []
    if ciclo.skus_por_dia:
        lote_detalhe.append(f'Meta diária: {ciclo.skus_por_dia} SKU(s)/dia')
    if ciclo.dia_execucao:
        lote_detalhe.append(f'Dia de execução atual: {ciclo.dia_execucao}')
    if ciclo.embalagens_filtro:
        lote_detalhe.append(f'Embalagens: {", ".join(ciclo.embalagens_filtro)}')
    if ciclo.canais_filtro:
        lote_detalhe.append(f'Canais: {", ".join(ciclo.canais_filtro)}')

    secoes.append(AuditoriaSecaoHistorico(
        titulo='Geração dos lotes',
        registros=[
            _registro_historico(
                data_hora=ciclo.data_criacao,
                resumo='Configuração operacional de lotes diários',
                detalhe='\n'.join(lote_detalhe) if lote_detalhe else 'Lotes gerados durante a execução diária.',
                usuario=ciclo.usuario_criacao,
            ),
        ],
    ))

    historico = CicloAuditoriaHistorico.objects.filter(
        ciclo_sku__ciclo=ciclo,
    ).select_related(
        'ciclo_sku',
        'item',
        'usuario',
        'usuario__perfil_operacional',
    ).order_by('data_hora')

    contagens: list[AuditoriaRegistroHistorico] = []
    edicoes: list[AuditoriaRegistroHistorico] = []
    recontagens: list[AuditoriaRegistroHistorico] = []
    aceites: list[AuditoriaRegistroHistorico] = []

    for registro in historico:
        sku_codigo = registro.ciclo_sku.codigo_produto if registro.ciclo_sku_id else '—'
        posicao = registro.codigo_posicao or (
            registro.item.codigo_posicao if registro.item_id else '—'
        )
        detalhe_registro = registro.motivo.strip() if registro.motivo else ''
        if not detalhe_registro:
            detalhe_registro = (
                f'Físico: {registro.quantidade_fisica} | '
                f'SAP: {registro.quantidade_sap_momento} | '
                f'Dif.: {registro.diferenca}'
            )

        item = _registro_historico(
            data_hora=registro.data_hora,
            resumo=registro.get_tipo_display(),
            detalhe=detalhe_registro,
            usuario=registro.usuario,
            sku=sku_codigo,
            posicao=posicao,
        )

        if registro.tipo == CicloAuditoriaHistorico.TipoRegistro.CONTAGEM:
            contagens.append(item)
        elif registro.tipo == CicloAuditoriaHistorico.TipoRegistro.CONSOLIDACAO:
            contagens.append(item)
        elif registro.tipo == CicloAuditoriaHistorico.TipoRegistro.EDICAO:
            edicoes.append(item)
        elif registro.tipo == CicloAuditoriaHistorico.TipoRegistro.RECONTAGEM:
            recontagens.append(item)
        elif registro.tipo == CicloAuditoriaHistorico.TipoRegistro.VALIDACAO:
            motivo = (registro.motivo or '').lower()
            if 'aceita' in motivo or 'aceito' in motivo:
                aceites.append(item)
            else:
                aceites.append(item)
        elif registro.tipo == CicloAuditoriaHistorico.TipoRegistro.EXCLUSAO:
            edicoes.append(item)

    for titulo, registros in (
        ('Contagens', contagens),
        ('Edições', edicoes),
        ('Recontagens', recontagens),
        ('Aceites', aceites),
    ):
        secoes.append(AuditoriaSecaoHistorico(titulo=titulo, registros=registros))

    ajustes = CicloEstoqueFisicoAjuste.objects.filter(ciclo=ciclo).select_related(
        'usuario',
        'usuario__perfil_operacional',
    ).order_by('data_hora')
    secoes.append(AuditoriaSecaoHistorico(
        titulo='Ajustes no Estoque Físico',
        registros=[
            _registro_historico(
                data_hora=ajuste.data_hora,
                resumo=f'{ajuste.codigo_produto} @ {ajuste.codigo_posicao}',
                detalhe=(
                    f'{ajuste.quantidade_anterior} → {ajuste.quantidade_nova} '
                    f'(Δ {ajuste.diferenca:+}). {ajuste.motivo}'
                ).strip(),
                usuario=ajuste.usuario,
                sku=ajuste.codigo_produto,
                posicao=ajuste.codigo_posicao,
            )
            for ajuste in ajustes
        ],
    ))

    if ciclo.data_encerramento:
        secoes.append(AuditoriaSecaoHistorico(
            titulo='Encerramento',
            registros=[
                _registro_historico(
                    data_hora=ciclo.data_encerramento,
                    resumo=f'Ciclo #{ciclo.pk} encerrado',
                    detalhe=(
                        f'Executado: {ciclo.percentual_executado or 0}% | '
                        f'Acuracidade: {ciclo.taxa_acuracidade or 0}%'
                    ),
                    usuario=ciclo.usuario_encerramento,
                ),
            ],
        ))

    return detalhe, secoes
