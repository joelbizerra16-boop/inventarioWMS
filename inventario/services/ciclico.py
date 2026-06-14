from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from core.logging_auditoria import registrar_evento

from estoque_fisico.models import EstoqueFisico
from estoque_sap.models import EstoqueSAP
from produtos.models import Produto
from inventario.models import (
    CicloAuditoriaHistorico,
    CicloInventario,
    CicloInventarioItem,
    CicloInventarioSku,
    CicloLoteExecucao,
    CicloLoteExecucaoItem,
)
from posicoes.models import Posicao


class StatusItemCiclico:
    PENDENTE = CicloInventarioSku.StatusContagem.PENDENTE
    CONTADO = CicloInventarioSku.StatusContagem.CONTADO
    DIVERGENTE = CicloInventarioSku.StatusContagem.DIVERGENTE
    RECONTAGEM = CicloInventarioSku.StatusContagem.RECONTAGEM
    VALIDADO = CicloInventarioSku.StatusContagem.VALIDADO
    VALIDADO_DIVERGENCIA = CicloInventarioSku.StatusContagem.VALIDADO_DIVERGENCIA
    EXCLUIDO = CicloInventarioSku.StatusContagem.EXCLUIDO

    # Status por posição (CicloInventarioItem)
    POS_EM_CONTAGEM = CicloInventarioItem.StatusContagem.EM_CONTAGEM
    POS_APROVADA = CicloInventarioItem.StatusContagem.APROVADA
    POS_FINALIZADA = CicloInventarioItem.StatusContagem.FINALIZADA

    LABELS = {
        PENDENTE: 'Pendente',
        CONTADO: 'Contado',
        DIVERGENTE: 'Divergente',
        RECONTAGEM: 'Em Recontagem',
        VALIDADO: 'Validado',
        VALIDADO_DIVERGENCIA: 'Validado c/ divergência',
        EXCLUIDO: 'Excluído',
        POS_EM_CONTAGEM: 'Em Contagem',
        POS_APROVADA: 'Aprovada',
        POS_FINALIZADA: 'Finalizada',
    }

    CLASSES = {
        PENDENTE: 'secondary',
        CONTADO: 'success',
        DIVERGENTE: 'danger',
        RECONTAGEM: 'warning',
        VALIDADO: 'primary',
        VALIDADO_DIVERGENCIA: 'info',
        EXCLUIDO: 'dark',
    }

    CONGELADOS_SAP = frozenset({
        CONTADO,
        DIVERGENTE,
        RECONTAGEM,
        VALIDADO,
        VALIDADO_DIVERGENCIA,
        EXCLUIDO,
    })

    FINALIZADOS = frozenset({
        CONTADO,
        VALIDADO,
        VALIDADO_DIVERGENCIA,
        EXCLUIDO,
    })

    EM_CONTAGEM = frozenset({
        PENDENTE,
        RECONTAGEM,
        POS_EM_CONTAGEM,
    })


CODIGO_POSICAO_GENERICO = 'CICLICO-SEM-POS'
CAMPO_CANAL_COSAN = 'canal_110'
CAMPO_CANAL_BRIDA = 'canal_1'
OrigemContagem = CicloInventarioItem.OrigemContagem


class CiclicoError(Exception):
    pass


class CiclicoContagemDuplicadaError(CiclicoError):
    def __init__(self, *, contexto_auditoria: dict | None = None):
        from inventario.services.contagem import MENSAGEM_DUPLICADA
        super().__init__(MENSAGEM_DUPLICADA)
        self.contexto_auditoria = contexto_auditoria or {}


StatusCiclo = CicloInventario.StatusCiclo


class IndicadorConfronto:
    VERDE = 'verde'
    LARANJA = 'laranja'
    VERMELHO = 'vermelho'

    LABELS = {
        VERDE: 'Saldo conciliado',
        LARANJA: 'Físico maior que SAP',
        VERMELHO: 'Físico menor que SAP',
    }

    EMOJI = {
        VERDE: '🟢',
        LARANJA: '🟠',
        VERMELHO: '🔴',
    }


IndicadorCosan = IndicadorConfronto


@dataclass
class ConfiguracaoExecucao:
    embalagens: list[str] | None = None
    canal: str = ''
    quantidade_skus: int | None = None
    respeitar_somente_embalagens: bool = False


ConfiguracaoCiclo = ConfiguracaoExecucao


@dataclass
class InfoSapCiclo:
    total_skus: int
    ultima_importacao: datetime | None


@dataclass
class LoteExecucaoInfo:
    skus_no_lote: int
    quantidade_solicitada: int | None
    embalagens: list[str]
    canal: str
    respeitar_somente_embalagens: bool


SESSION_POCKET_SKU_ATIVO = 'ciclico_pocket_sku_ativo'


@dataclass
class FiltrosCicloConsulta:
    sku: str = ''
    descricao: str = ''
    embalagem: str = ''
    setor: str = ''
    canal: str = ''
    status: str = ''
    usuario: str = ''
    origem: str = ''
    data: str = ''
    data_inicial: str = ''
    data_final: str = ''
    divergente: bool = False
    somente_divergentes: bool = False
    somente_recontagens: bool = False
    somente_validados: bool = False
    status_ciclo: str = ''
    ciclo_id: int | None = None


@dataclass
class CicloResumo:
    total_skus: int
    skus_pendentes: int
    skus_contados: int
    skus_divergentes: int
    skus_validados: int
    skus_excluidos: int
    percentual_executado: Decimal
    skus_conciliados: int = 0
    skus_acima_sap: int = 0
    skus_abaixo_sap: int = 0
    por_embalagem: dict[str, int] = field(default_factory=dict)
    por_setor: dict[str, int] = field(default_factory=dict)
    por_canal_cosan: int = 0
    por_canal_brida: int = 0
    skus_conciliados_cosan: int = 0
    skus_acima_cosan: int = 0
    skus_abaixo_cosan: int = 0
    dias_restantes: int = 0
    estimativa_termino: datetime | None = None
    por_usuario: dict[str, int] = field(default_factory=dict)
    por_origem: dict[str, int] = field(default_factory=dict)


@dataclass
class LoteDiarioInfo:
    dia: int
    skus_por_dia: int | None
    ordem_inicio: int
    ordem_fim: int
    total_dias: int
    skus_no_lote: int


@dataclass
class CicloInfo:
    pk: int
    data_criacao: datetime
    data_encerramento: datetime | None
    ativo: bool
    quantidade_skus_planejados: int | None
    skus_por_dia: int | None
    dia_execucao: int
    embalagens_filtro: list[str]
    canais_filtro: list[str]
    completar_lote_automaticamente: bool
    respeitar_somente_embalagens: bool
    lote_diario: LoteDiarioInfo | None = None
    lote_execucao: LoteExecucaoInfo | None = None


@dataclass
class PosicaoCicloDetalhe:
    pk: int
    posicao_id: int
    codigo_posicao: str
    alocacao: str
    quantidade_fisica: Decimal | None
    usuario_contagem_nome: str
    data_contagem: datetime | None
    origem_contagem: str = ''
    origem_contagem_label: str = ''
    dispositivo_contagem: str = ''


@dataclass
class SkuCicloDetalhe:
    pk: int
    codigo_produto: str
    descricao: str
    embalagem: str
    setor: str
    quantidade_sap: Decimal
    quantidade_cosan: Decimal | None
    quantidade_brida: Decimal | None
    quantidade_fisica: Decimal | None
    diferenca: Decimal | None
    diferenca_cosan: Decimal | None
    indicador_cosan: str | None
    indicador_cosan_tooltip: str
    indicador_sap: str | None
    indicador_sap_tooltip: str
    status_contagem: str
    status_label: str
    status_classe: str
    ordem_planejamento: int
    usuarios: list[str] = field(default_factory=list)
    posicoes: list[PosicaoCicloDetalhe] = field(default_factory=list)
    ultima_origem: str = ''
    ultima_origem_label: str = ''
    ultimo_usuario: str = ''
    ultima_data: datetime | None = None
    ultimo_dispositivo: str = ''
    historico: list['HistoricoCicloLinha'] = field(default_factory=list)
    pode_editar: bool = False
    pode_excluir: bool = False


@dataclass
class HistoricoCicloLinha:
    tipo: str
    tipo_label: str
    usuario: str
    data_hora: datetime
    quantidade_fisica: Decimal
    posicao: str
    motivo: str
    origem: str = ''
    dispositivo: str = ''


@dataclass
class GrupoProdutoCiclo:
    pk: int
    codigo_produto: str
    descricao: str
    embalagem: str
    sap_total: Decimal
    cosan_total: Decimal | None
    brida_total: Decimal | None
    fisico_total: Decimal | None
    diferenca_cosan: Decimal | None
    indicador_cosan: str | None
    indicador_cosan_tooltip: str
    indicador_sap: str | None
    indicador_sap_tooltip: str
    setor: str
    status_contagem: str
    status_label: str
    status_classe: str
    usuarios: list[str]
    ultima_contagem: datetime | None
    ultima_origem_label: str
    ultimo_dispositivo: str
    posicoes: list[PosicaoCicloDetalhe]
    historico: list[HistoricoCicloLinha]


@dataclass
class IndicadoresCiclicoDashboard:
    itens_planejados: int
    itens_contados: int
    percentual_concluido: Decimal


def limpar_estado_ciclico() -> None:
    from inventario.models import CicloEstoqueFisicoAjuste

    CicloLoteExecucaoItem.objects.all().delete()
    CicloLoteExecucao.objects.all().delete()
    CicloEstoqueFisicoAjuste.objects.all().delete()
    CicloAuditoriaHistorico.objects.all().delete()
    CicloInventarioItem.objects.all().delete()
    CicloInventarioSku.objects.all().delete()
    CicloInventario.objects.all().delete()


def obter_lote_execucao_ativo(
    ciclo: CicloInventario | None = None,
) -> CicloLoteExecucao | None:
    if ciclo is None:
        ciclo = _obter_ciclo_ativo()
    if ciclo is None:
        return None
    return (
        CicloLoteExecucao.objects.filter(
            ciclo=ciclo,
            status=CicloLoteExecucao.Status.ATIVO,
        )
        .order_by('-data_geracao')
        .first()
    )


def _lote_para_info(lote: CicloLoteExecucao) -> dict:
    return {
        'skus_no_lote': lote.itens.count(),
        'quantidade_solicitada': lote.quantidade_solicitada,
        'embalagens': lote.embalagens or [],
        'canal': lote.canal or '',
        'respeitar_somente_embalagens': lote.respeitar_somente_embalagens,
    }


def encerrar_lotes_execucao_ciclo(ciclo: CicloInventario) -> None:
    CicloLoteExecucao.objects.filter(
        ciclo=ciclo,
        status=CicloLoteExecucao.Status.ATIVO,
    ).update(status=CicloLoteExecucao.Status.ENCERRADO)


@transaction.atomic
def _persistir_lote_ciclo_db(
    ciclo: CicloInventario,
    skus: list[CicloInventarioSku],
    config: 'ConfiguracaoExecucao',
    *,
    usuario=None,
) -> CicloLoteExecucao:
    CicloLoteExecucao.objects.filter(
        ciclo=ciclo,
        status=CicloLoteExecucao.Status.ATIVO,
    ).update(status=CicloLoteExecucao.Status.SUBSTITUIDO)

    lote = CicloLoteExecucao.objects.create(
        ciclo=ciclo,
        status=CicloLoteExecucao.Status.ATIVO,
        quantidade_solicitada=config.quantidade_skus,
        embalagens=config.embalagens or [],
        canal=config.canal or '',
        respeitar_somente_embalagens=config.respeitar_somente_embalagens,
        usuario_geracao=usuario,
    )
    CicloLoteExecucaoItem.objects.bulk_create([
        CicloLoteExecucaoItem(
            lote=lote,
            ciclo_sku=sku,
            sequencia=sequencia,
            status=CicloLoteExecucaoItem.Status.PENDENTE,
        )
        for sequencia, sku in enumerate(skus, start=1)
    ])
    sku_ids = [sku.pk for sku in skus]
    registrar_evento(
        'ciclico_lote_criado',
        usuario=usuario,
        lote_id=lote.pk,
        ciclo_id=ciclo.pk,
        ciclo_ativo=ciclo.ativo,
        ciclo_status=ciclo.status_ciclo,
        skus_no_lote=len(sku_ids),
        sku_ids=','.join(str(pk) for pk in sku_ids),
        canal=config.canal or '',
        embalagens=','.join(config.embalagens or []),
    )
    return lote


def obter_lote_sku_ids(ciclo: CicloInventario | None = None) -> list[int]:
    lote = obter_lote_execucao_ativo(ciclo)
    if lote is None:
        return []
    return list(
        lote.itens.order_by('sequencia').values_list('ciclo_sku_id', flat=True)
    )


def _decimal(valor) -> Decimal:
    if valor is None:
        return Decimal('0')
    return Decimal(valor)


def _posicao_generica_sem_contagem(item: CicloInventarioItem) -> bool:
    return (
        item.codigo_posicao == CODIGO_POSICAO_GENERICO
        and item.quantidade_fisica is None
    )


def _posicoes_operacionais_sku(sku: CicloInventarioSku) -> list[CicloInventarioItem]:
    return [
        posicao for posicao in sku.posicoes.all()
        if not _posicao_generica_sem_contagem(posicao)
    ]


def sku_contagem_pocket_completa(sku: CicloInventarioSku) -> bool:
    posicoes = [
        posicao for posicao in CicloInventarioItem.objects.filter(ciclo_sku=sku)
        if not _posicao_generica_sem_contagem(posicao)
    ]
    if not posicoes:
        return True
    return all(posicao.quantidade_fisica is not None for posicao in posicoes)


def _filtrar_posicoes_ui(posicoes) -> list:
    return [p for p in posicoes if not _posicao_generica_sem_contagem(p)]


def _obter_ultima_contagem_sku_info(
    sku: CicloInventarioSku,
) -> tuple[str, str, str, datetime | None, str]:
    melhor_data = None
    melhor_item = None
    for posicao in sku.posicoes.all():
        if posicao.data_contagem is None:
            continue
        if melhor_data is None or posicao.data_contagem > melhor_data:
            melhor_data = posicao.data_contagem
            melhor_item = posicao
    if melhor_item is None:
        return '', '', '', None, ''
    return (
        melhor_item.origem_contagem or '',
        melhor_item.origem_contagem_rotulo or '',
        melhor_item.usuario_contagem_nome,
        melhor_item.data_contagem,
        melhor_item.dispositivo_contagem or '',
    )


def _rotulo_origem(codigo: str) -> str:
    if not codigo:
        return ''
    try:
        return OrigemContagem(codigo).label
    except ValueError:
        return codigo


def _normalizar_embalagem(embalagem: str) -> str:
    return (embalagem or '').strip().upper()


def _embalagem_em_lista(embalagem: str, embalagens: list[str]) -> bool:
    if not embalagens:
        return True
    normalizada = _normalizar_embalagem(embalagem)
    return any(
        _normalizar_embalagem(item) == normalizada
        for item in embalagens
    )


def obter_embalagens_disponiveis() -> list[str]:
    produto_ids = EstoqueSAP.objects.values_list('produto_id', flat=True).distinct()
    embalagens = (
        Produto.objects.filter(
            pk__in=produto_ids,
            participa_ciclico=True,
        )
        .exclude(embalagem='')
        .values_list('embalagem', flat=True)
        .distinct()
        .order_by('embalagem')
    )
    return list(embalagens)


def _obter_ciclo_ativo() -> CicloInventario | None:
    return (
        CicloInventario.objects.filter(
            ativo=True,
            status_ciclo=StatusCiclo.ATIVO,
        )
        .order_by('-data_criacao')
        .first()
    )


def obter_ciclo_consulta(ciclo_id: int | None = None) -> CicloInventario | None:
    if ciclo_id:
        return CicloInventario.objects.filter(pk=ciclo_id).first()
    return _obter_ciclo_ativo()


def listar_ciclos_historico(status_ciclo: str = '') -> list[CicloInventario]:
    queryset = CicloInventario.objects.select_related(
        'usuario_criacao',
        'usuario_criacao__perfil_operacional',
    ).order_by('-data_criacao')
    if status_ciclo:
        queryset = queryset.filter(status_ciclo=status_ciclo)
    return list(queryset)


def _obter_canais_sap_por_produto(
    produto_ids: set[int] | list[int],
) -> dict[int, tuple[Decimal, Decimal]]:
    if not produto_ids:
        return {}

    return {
        registro['produto_id']: (
            _decimal(registro[CAMPO_CANAL_COSAN]),
            _decimal(registro[CAMPO_CANAL_BRIDA]),
        )
        for registro in EstoqueSAP.objects.filter(
            produto_id__in=produto_ids,
        ).values('produto_id', CAMPO_CANAL_COSAN, CAMPO_CANAL_BRIDA)
    }


def _calcular_indicador_confronto(
    quantidade_fisica: Decimal | None,
    referencia: Decimal | None,
) -> tuple[Decimal | None, str | None, str]:
    if quantidade_fisica is None or referencia is None:
        return None, None, ''

    diferenca = quantidade_fisica - referencia
    if diferenca == 0:
        indicador = IndicadorConfronto.VERDE
    elif diferenca > 0:
        indicador = IndicadorConfronto.LARANJA
    else:
        indicador = IndicadorConfronto.VERMELHO

    return diferenca, indicador, IndicadorConfronto.LABELS[indicador]


_calcular_analise_cosan = _calcular_indicador_confronto


def _obter_canais_sku(sku: CicloInventarioSku) -> tuple[Decimal | None, Decimal | None]:
    if sku.quantidade_cosan is not None or sku.quantidade_brida is not None:
        return sku.quantidade_cosan, sku.quantidade_brida
    canais = _obter_canais_sap_por_produto({sku.produto_id}).get(sku.produto_id)
    if canais is None:
        return None, None
    return canais


def _produto_elegivel_ciclo(
    sap: EstoqueSAP,
    embalagens: list[str] | None,
    canais: list[str] | None,
) -> bool:
    produto = sap.produto
    if not produto.participa_ciclico:
        return False

    if embalagens and not _embalagem_em_lista(produto.embalagem or '', embalagens):
        return False

    if canais:
        cosan = _decimal(sap.canal_110)
        brida = _decimal(sap.canal_1)
        possui_canal = False
        if 'cosan' in canais and cosan > 0:
            possui_canal = True
        if 'brida' in canais and brida > 0:
            possui_canal = True
        if not possui_canal:
            return False

    return True


def obter_info_sap_para_ciclo() -> InfoSapCiclo:
    sap_por_produto = _obter_estoque_sap_por_produto()
    total = sum(
        1 for sap in sap_por_produto.values()
        if sap.produto.participa_ciclico
    )
    ultima = (
        EstoqueSAP.objects.order_by('-data_importacao')
        .values_list('data_importacao', flat=True)
        .first()
    )
    return InfoSapCiclo(total_skus=total, ultima_importacao=ultima)


def _obter_pendentes_ciclo(ciclo: CicloInventario) -> list[CicloInventarioSku]:
    return list(
        CicloInventarioSku.objects.filter(
            ciclo=ciclo,
            status_contagem=StatusItemCiclico.PENDENTE,
        ).order_by('ordem_planejamento', 'codigo_produto'),
    )


def _filtrar_skus_por_canal(
    skus: list[CicloInventarioSku],
    canal: str,
) -> list[CicloInventarioSku]:
    if canal == 'cosan':
        return [
            sku for sku in skus
            if (sku.quantidade_cosan or Decimal('0')) > 0
        ]
    if canal == 'brida':
        return [
            sku for sku in skus
            if (sku.quantidade_brida or Decimal('0')) > 0
        ]
    return skus


def _montar_lote_execucao(
    pendentes: list[CicloInventarioSku],
    config: ConfiguracaoExecucao,
) -> list[CicloInventarioSku]:
    if not pendentes:
        return []

    embalagens = config.embalagens or []
    if embalagens:
        primarios = [
            sku for sku in pendentes
            if _embalagem_em_lista(sku.embalagem, embalagens)
        ]
        secundarios = [sku for sku in pendentes if sku not in primarios]
    else:
        primarios = pendentes
        secundarios = []

    meta = config.quantidade_skus
    if meta is None or meta <= 0:
        if config.respeitar_somente_embalagens and embalagens:
            return primarios
        return pendentes

    if config.respeitar_somente_embalagens and embalagens:
        return primarios[:meta]

    lote = list(primarios[:meta])
    if len(lote) < meta and secundarios:
        lote.extend(secundarios[: meta - len(lote)])
    return lote


def limpar_lote_sessao(session) -> None:
    ciclo = _obter_ciclo_ativo()
    if ciclo is not None:
        encerrar_lotes_execucao_ciclo(ciclo)
    limpar_pocket_sessao_contagem(session)
    if session is not None and hasattr(session, 'modified'):
        session.modified = True


def limpar_pocket_sessao_contagem(session) -> None:
    if session is None:
        return
    session.pop(SESSION_POCKET_SKU_ATIVO, None)
    session.pop('pocket_ciclico_manter_sessao', None)
    if hasattr(session, 'modified'):
        session.modified = True


def limpar_recontagem_pocket_itens(session, item_ids: list[int] | None = None) -> None:
    """Compatibilidade: reinicia sessão pocket ao salvar recontagem pela web."""
    limpar_pocket_sessao_contagem(session)


def _pocket_sessao_sku_ativa(session, sku_id: int) -> bool:
    if session is None:
        return False
    return session.get(SESSION_POCKET_SKU_ATIVO) == sku_id


def _iniciar_pocket_sessao_sku(session, sku_id: int) -> None:
    session[SESSION_POCKET_SKU_ATIVO] = sku_id
    if hasattr(session, 'modified'):
        session.modified = True


def _calcular_quantidade_acumulada_pocket(
    item: CicloInventarioItem,
    quantidade_bipada: Decimal,
    *,
    recontagem: bool,
    session,
    sku_id: int,
) -> Decimal:
    quantidade_bipada = _decimal(quantidade_bipada)
    sessao_ativa = _pocket_sessao_sku_ativa(session, sku_id)

    if sessao_ativa:
        base = (
            _decimal(item.quantidade_fisica)
            if item.quantidade_fisica is not None
            else Decimal('0')
        )
    elif recontagem:
        base = Decimal('0')
    else:
        base = (
            _decimal(item.quantidade_fisica)
            if item.quantidade_fisica is not None
            else Decimal('0')
        )

    _iniciar_pocket_sessao_sku(session, sku_id)
    return base + quantidade_bipada


def obter_lote_sessao(session=None) -> list[int]:
    return obter_lote_sku_ids()


def obter_lote_execucao_info(session=None) -> LoteExecucaoInfo | None:
    lote = obter_lote_execucao_ativo()
    if lote is None:
        return None
    return LoteExecucaoInfo(**_lote_para_info(lote))


def gerar_lote_execucao(
    session,
    config: ConfiguracaoExecucao,
    *,
    usuario=None,
) -> list[CicloInventarioSku]:
    ciclo = _obter_ciclo_ativo()
    if ciclo is None:
        raise CiclicoError('Nenhum ciclo cíclico ativo.')

    if config.quantidade_skus is not None and config.quantidade_skus <= 0:
        raise CiclicoError('Informe uma quantidade de SKUs maior que zero.')

    pendentes = _filtrar_skus_por_canal(
        _obter_pendentes_ciclo(ciclo),
        config.canal,
    )
    lote = _montar_lote_execucao(pendentes, config)
    if not lote:
        raise CiclicoError('Nenhum SKU pendente encontrado com os filtros informados.')

    _persistir_lote_ciclo_db(ciclo, lote, config, usuario=usuario)
    return lote


def _obter_skus_lote_sessao(session=None) -> list[CicloInventarioSku]:
    ciclo = _obter_ciclo_ativo()
    if ciclo is None:
        return []
    ids = obter_lote_sku_ids(ciclo)
    if not ids:
        return []
    skus = {
        sku.pk: sku
        for sku in CicloInventarioSku.objects.filter(ciclo=ciclo, pk__in=ids).select_related('produto')
    }
    return [skus[pk] for pk in ids if pk in skus]


def _resolver_produtos_congelamento(
    sap_por_produto: dict[int, EstoqueSAP],
) -> list[int]:
    return sorted(
        [
            produto_id
            for produto_id, sap in sap_por_produto.items()
            if sap.produto.participa_ciclico
        ],
        key=lambda pid: sap_por_produto[pid].produto.codigo_produto,
    )


def _obter_pool_execucao(ciclo: CicloInventario) -> list[CicloInventarioSku]:
    skus = list(
        CicloInventarioSku.objects.filter(ciclo=ciclo)
        .exclude(status_contagem=StatusItemCiclico.EXCLUIDO)
        .order_by('ordem_planejamento', 'codigo_produto'),
    )
    embalagens = ciclo.embalagens_filtro or []
    if not embalagens:
        return skus

    primarios = [sku for sku in skus if _embalagem_em_lista(sku.embalagem, embalagens)]
    secundarios = [sku for sku in skus if not _embalagem_em_lista(sku.embalagem, embalagens)]

    if ciclo.respeitar_somente_embalagens:
        return primarios
    if ciclo.completar_lote_automaticamente:
        return primarios + secundarios
    return primarios


def _aplicar_filtros_consulta(
    skus: list[CicloInventarioSku],
    filtros: FiltrosCicloConsulta | None,
) -> list[CicloInventarioSku]:
    if filtros is None:
        return skus

    resultado = skus
    if filtros.sku:
        termo = filtros.sku.strip().lower()
        resultado = [
            sku for sku in resultado
            if termo in sku.codigo_produto.lower()
        ]
    if filtros.descricao:
        termo = filtros.descricao.strip().lower()
        resultado = [
            sku for sku in resultado
            if termo in sku.descricao.lower()
        ]
    if filtros.embalagem:
        resultado = [
            sku for sku in resultado
            if _embalagem_em_lista(sku.embalagem, [filtros.embalagem])
        ]
    if filtros.setor:
        resultado = [sku for sku in resultado if sku.setor == filtros.setor]
    if filtros.canal == 'cosan':
        resultado = [
            sku for sku in resultado
            if (sku.quantidade_cosan or Decimal('0')) > 0
        ]
    elif filtros.canal == 'brida':
        resultado = [
            sku for sku in resultado
            if (sku.quantidade_brida or Decimal('0')) > 0
        ]
    if filtros.status:
        resultado = [
            sku for sku in resultado
            if sku.status_contagem == filtros.status
        ]
    if filtros.divergente or filtros.somente_divergentes:
        resultado = [
            sku for sku in resultado
            if sku.status_contagem == StatusItemCiclico.DIVERGENTE
        ]
    if filtros.somente_recontagens:
        resultado = [
            sku for sku in resultado
            if sku.status_contagem == StatusItemCiclico.RECONTAGEM
        ]
    if filtros.somente_validados:
        resultado = [
            sku for sku in resultado
            if sku.status_contagem in (
                StatusItemCiclico.VALIDADO,
                StatusItemCiclico.VALIDADO_DIVERGENCIA,
            )
        ]
    if filtros.usuario:
        termo = filtros.usuario.strip().lower()
        resultado = [
            sku for sku in resultado
            if any(termo in nome.lower() for nome in sku.usuarios_contagem_nomes)
        ]
    if filtros.origem:
        origem_filtro = filtros.origem.strip().upper()
        resultado = [
            sku for sku in resultado
            if _obter_ultima_contagem_sku_info(sku)[0] == origem_filtro
        ]
    if filtros.data:
        resultado = [
            sku for sku in resultado
            if _filtrar_sku_por_data(sku, filtros.data)
        ]
    if filtros.data_inicial or filtros.data_final:
        resultado = [
            sku for sku in resultado
            if _filtrar_sku_por_periodo(sku, filtros.data_inicial, filtros.data_final)
        ]
    return resultado


def _filtrar_sku_por_periodo(
    sku: CicloInventarioSku,
    data_inicial: str,
    data_final: str,
) -> bool:
    ultima = _obter_ultima_contagem_sku(sku)
    if ultima is None:
        return False
    data_ultima = timezone.localtime(ultima).date()
    if data_inicial:
        try:
            inicio = datetime.strptime(data_inicial, '%Y-%m-%d').date()
            if data_ultima < inicio:
                return False
        except ValueError:
            pass
    if data_final:
        try:
            fim = datetime.strptime(data_final, '%Y-%m-%d').date()
            if data_ultima > fim:
                return False
        except ValueError:
            pass
    return True


def _filtrar_sku_por_data(sku: CicloInventarioSku, data_filtro: str) -> bool:
    ultima = _obter_ultima_contagem_sku(sku)
    if ultima is None:
        return False
    return timezone.localtime(ultima).strftime('%Y-%m-%d') == data_filtro


def _historico_para_dto(registro: CicloAuditoriaHistorico) -> HistoricoCicloLinha:
    usuario_nome = 'Não informado'
    if registro.usuario_id:
        perfil = getattr(registro.usuario, 'perfil_operacional', None)
        if perfil:
            usuario_nome = perfil.nome
        else:
            usuario_nome = (
                registro.usuario.get_full_name()
                or registro.usuario.get_username()
            )
    return HistoricoCicloLinha(
        tipo=registro.tipo,
        tipo_label=registro.get_tipo_display(),
        usuario=usuario_nome,
        data_hora=registro.data_hora,
        quantidade_fisica=registro.quantidade_fisica,
        posicao=registro.codigo_posicao,
        motivo=registro.motivo,
        origem=_rotulo_origem(registro.origem_contagem),
        dispositivo=registro.dispositivo_contagem or '',
    )


def _obter_ultima_contagem_sku(sku: CicloInventarioSku) -> datetime | None:
    datas = [
        posicao.data_contagem
        for posicao in sku.posicoes.all()
        if posicao.data_contagem is not None
    ]
    return max(datas) if datas else None


def _obter_estoque_sap_por_produto() -> dict[int, EstoqueSAP]:
    estoques: dict[int, EstoqueSAP] = {}
    registros = EstoqueSAP.objects.select_related('produto').order_by(
        'produto__codigo_produto',
        '-data_importacao',
    )
    for registro in registros:
        if registro.produto_id not in estoques:
            estoques[registro.produto_id] = registro
    return estoques


def _obter_fisicos_por_produto() -> dict[int, list[EstoqueFisico]]:
    resultado: dict[int, list[EstoqueFisico]] = {}
    registros = EstoqueFisico.objects.select_related('produto', 'posicao').order_by(
        'produto__codigo_produto',
        'posicao__codigo',
    )
    for registro in registros:
        resultado.setdefault(registro.produto_id, []).append(registro)
    return resultado


def _obter_posicao_contagem_generica() -> Posicao:
    posicao, _ = Posicao.objects.get_or_create(
        codigo=CODIGO_POSICAO_GENERICO,
        defaults={
            'posicao': 'Sem posição definida',
            'ativo': True,
        },
    )
    return posicao


def _calcular_lote_diario(ciclo: CicloInventario) -> LoteDiarioInfo:
    pool = _obter_pool_execucao(ciclo)
    total_skus = len(pool)

    if not ciclo.skus_por_dia:
        ordem_inicio = pool[0].ordem_planejamento if pool else 1
        ordem_fim = pool[-1].ordem_planejamento if pool else 0
        return LoteDiarioInfo(
            dia=1,
            skus_por_dia=None,
            ordem_inicio=ordem_inicio,
            ordem_fim=ordem_fim,
            total_dias=1,
            skus_no_lote=total_skus,
        )

    total_dias = max(
        (total_skus + ciclo.skus_por_dia - 1) // ciclo.skus_por_dia,
        1,
    )
    dia = min(max(ciclo.dia_execucao, 1), total_dias)
    inicio = (dia - 1) * ciclo.skus_por_dia
    fim = min(dia * ciclo.skus_por_dia, total_skus)
    lote = pool[inicio:fim]
    ordem_inicio = lote[0].ordem_planejamento if lote else 0
    ordem_fim = lote[-1].ordem_planejamento if lote else 0
    return LoteDiarioInfo(
        dia=dia,
        skus_por_dia=ciclo.skus_por_dia,
        ordem_inicio=ordem_inicio,
        ordem_fim=ordem_fim,
        total_dias=total_dias,
        skus_no_lote=len(lote),
    )


def _sku_para_dto(
    sku: CicloInventarioSku,
    incluir_posicoes: bool = False,
    incluir_historico: bool = False,
    usuario=None,
) -> SkuCicloDetalhe:
    posicoes_dto: list[PosicaoCicloDetalhe] = []
    historico_dto: list[HistoricoCicloLinha] = []
    if incluir_posicoes:
        posicoes_visiveis = _filtrar_posicoes_ui(
            sku.posicoes.select_related(
                'usuario_contagem',
                'usuario_contagem__perfil_operacional',
            ).order_by('codigo_posicao'),
        )
        for posicao in posicoes_visiveis:
            posicoes_dto.append(PosicaoCicloDetalhe(
                pk=posicao.pk,
                posicao_id=posicao.posicao_id,
                codigo_posicao=posicao.codigo_posicao,
                alocacao=posicao.alocacao,
                quantidade_fisica=posicao.quantidade_fisica,
                usuario_contagem_nome=posicao.usuario_contagem_nome,
                data_contagem=posicao.data_contagem,
                origem_contagem=posicao.origem_contagem or '',
                origem_contagem_label=posicao.origem_contagem_rotulo or '',
                dispositivo_contagem=posicao.dispositivo_contagem or '',
            ))
    if incluir_historico:
        historico_dto = [
            _historico_para_dto(registro)
            for registro in sku.historico.order_by('-data_hora')
        ]

    ultima_origem, ultima_origem_label, ultimo_usuario, ultima_data, ultimo_dispositivo = (
        _obter_ultima_contagem_sku_info(sku)
    )
    quantidade_cosan, quantidade_brida = _obter_canais_sku(sku)
    diferenca_sap, indicador_sap, indicador_sap_tooltip = _calcular_indicador_confronto(
        sku.quantidade_fisica,
        sku.quantidade_sap,
    )

    return SkuCicloDetalhe(
        pk=sku.pk,
        codigo_produto=sku.codigo_produto,
        descricao=sku.descricao,
        embalagem=sku.embalagem,
        setor=sku.setor,
        quantidade_sap=sku.quantidade_sap,
        quantidade_cosan=quantidade_cosan,
        quantidade_brida=quantidade_brida,
        quantidade_fisica=sku.quantidade_fisica,
        diferenca=sku.diferenca,
        diferenca_cosan=diferenca_sap,
        indicador_cosan=indicador_sap,
        indicador_cosan_tooltip=indicador_sap_tooltip,
        indicador_sap=indicador_sap,
        indicador_sap_tooltip=indicador_sap_tooltip,
        status_contagem=sku.status_contagem,
        status_label=StatusItemCiclico.LABELS[sku.status_contagem],
        status_classe=StatusItemCiclico.CLASSES[sku.status_contagem],
        ordem_planejamento=sku.ordem_planejamento,
        usuarios=sku.usuarios_contagem_nomes,
        posicoes=posicoes_dto,
        ultima_origem=ultima_origem,
        ultima_origem_label=ultima_origem_label,
        ultimo_usuario=ultimo_usuario,
        ultima_data=ultima_data,
        ultimo_dispositivo=ultimo_dispositivo,
        historico=historico_dto,
        pode_editar=usuario_pode_editar_contagem_ciclico(usuario, sku) if usuario else False,
        pode_excluir=usuario_pode_excluir_sku_ciclico(usuario, sku) if usuario else False,
    )


def _registrar_historico_posicao(
    sku: CicloInventarioSku,
    item: CicloInventarioItem,
    tipo: str,
    usuario,
    quantidade_fisica: Decimal,
    data_hora: datetime,
    origem_contagem: str = '',
    dispositivo_contagem: str = '',
) -> CicloAuditoriaHistorico:
    return CicloAuditoriaHistorico.objects.create(
        ciclo_sku=sku,
        item=item,
        codigo_posicao=item.alocacao or item.codigo_posicao,
        tipo=tipo,
        usuario=usuario,
        data_hora=data_hora,
        quantidade_sap_momento=sku.quantidade_sap,
        quantidade_fisica=quantidade_fisica,
        diferenca=Decimal('0'),
        origem_contagem=origem_contagem,
        dispositivo_contagem=dispositivo_contagem,
    )


def _registrar_historico_consolidacao(
    sku: CicloInventarioSku,
    usuario,
    quantidade_fisica: Decimal,
    data_hora: datetime,
    tipo: str,
) -> CicloAuditoriaHistorico:
    diferenca = quantidade_fisica - sku.quantidade_sap
    return CicloAuditoriaHistorico.objects.create(
        ciclo_sku=sku,
        tipo=tipo,
        usuario=usuario,
        data_hora=data_hora,
        quantidade_sap_momento=sku.quantidade_sap,
        quantidade_fisica=quantidade_fisica,
        diferenca=diferenca,
    )


def _consolidacao_e_recontagem(tipo_historico: str) -> bool:
    return tipo_historico == CicloAuditoriaHistorico.TipoRegistro.RECONTAGEM


def _aplicar_status_pos_consolidacao(
    sku: CicloInventarioSku,
    diferenca: Decimal,
    usuario,
    agora: datetime,
    *,
    recontagem: bool,
) -> None:
    if recontagem:
        sku.usuario_recontagem = usuario
        sku.data_recontagem = agora
        sku.status_contagem = (
            StatusItemCiclico.VALIDADO
            if diferenca == 0
            else StatusItemCiclico.DIVERGENTE
        )
        return

    sku.status_contagem = (
        StatusItemCiclico.CONTADO
        if diferenca == 0
        else StatusItemCiclico.DIVERGENTE
    )


def _sincronizar_status_itens_contados_sku(sku: CicloInventarioSku) -> None:
    if sku.status_contagem not in StatusItemCiclico.FINALIZADOS:
        return
    CicloInventarioItem.objects.filter(
        ciclo_sku=sku,
        quantidade_fisica__isnull=False,
    ).exclude(
        codigo_posicao=CODIGO_POSICAO_GENERICO,
    ).update(status_contagem=sku.status_contagem)


def _diferenca_consolidacao_sku(sku: CicloInventarioSku, fisico_total: Decimal) -> Decimal:
    return fisico_total - sku.quantidade_sap


def normalizar_status_sku_recontagem_conciliada(sku: CicloInventarioSku) -> bool:
    """Corrige SKU em recontagem com saldo físico igual ao SAP."""
    if sku.status_contagem != StatusItemCiclico.RECONTAGEM:
        return False
    if sku.quantidade_fisica is None:
        return False

    diferenca = sku.diferenca
    if diferenca is None:
        diferenca = _diferenca_consolidacao_sku(sku, sku.quantidade_fisica)
    if diferenca != 0:
        return False

    sku.status_contagem = StatusItemCiclico.VALIDADO
    sku.diferenca = Decimal('0')
    sku.save(update_fields=['status_contagem', 'diferenca'])
    _sincronizar_status_itens_contados_sku(sku)
    return True


def _consolidar_sku(sku: CicloInventarioSku, usuario, tipo_historico: str) -> None:
    from inventario.services.ciclico_estoque_fisico import (
        tentar_sincronizar_estoque_fisico_pos_finalizacao,
    )

    status_anterior = sku.status_contagem
    posicoes = list(sku.posicoes.all())
    if not posicoes:
        raise CiclicoError('SKU sem posições para consolidar.')

    for posicao in posicoes:
        if posicao.quantidade_fisica is None:
            raise CiclicoError(
                f'Informe a quantidade da posição {posicao.alocacao or posicao.codigo_posicao}.',
            )

    agora = timezone.now()
    fisico_total = sum(_decimal(p.quantidade_fisica) for p in posicoes)
    diferenca = fisico_total - sku.quantidade_sap
    recontagem = _consolidacao_e_recontagem(tipo_historico)

    sku.quantidade_fisica = fisico_total
    sku.diferenca = diferenca
    _aplicar_status_pos_consolidacao(
        sku,
        diferenca,
        usuario,
        agora,
        recontagem=recontagem,
    )
    sku.save()
    _sincronizar_status_itens_contados_sku(sku)

    if (
        sku.status_contagem == StatusItemCiclico.DIVERGENTE
        and not recontagem
    ):
        from inventario.services.recontagem_multiusuario import (
            processar_divergencia_pos_contagem,
        )
        for posicao_item in posicoes:
            processar_divergencia_pos_contagem(posicao_item, gerado_por=usuario)

    _registrar_historico_consolidacao(sku, usuario, fisico_total, agora, tipo_historico)
    tentar_sincronizar_estoque_fisico_pos_finalizacao(sku, status_anterior, usuario)


def _recalcular_consolidacao_sku(
    sku: CicloInventarioSku,
    usuario,
    tipo_historico: str,
    *,
    deferir_status_parcial_pocket: bool = False,
) -> None:
    from inventario.services.ciclico_estoque_fisico import (
        tentar_sincronizar_estoque_fisico_pos_finalizacao,
    )

    status_anterior = sku.status_contagem
    posicoes_contadas = [
        posicao for posicao in CicloInventarioItem.objects.filter(ciclo_sku=sku)
        if posicao.quantidade_fisica is not None
        and not _posicao_generica_sem_contagem(posicao)
    ]
    if not posicoes_contadas:
        return

    agora = timezone.now()
    fisico_total = sum(_decimal(p.quantidade_fisica) for p in posicoes_contadas)
    recontagem = _consolidacao_e_recontagem(tipo_historico)

    if deferir_status_parcial_pocket:
        sku.quantidade_fisica = fisico_total
        sku.diferenca = None
        if recontagem:
            if sku.status_contagem not in StatusItemCiclico.FINALIZADOS:
                sku.status_contagem = StatusItemCiclico.RECONTAGEM
        elif sku.status_contagem not in StatusItemCiclico.FINALIZADOS:
            sku.status_contagem = StatusItemCiclico.PENDENTE
        sku.save(update_fields=['quantidade_fisica', 'diferenca', 'status_contagem'])
        return

    diferenca = _diferenca_consolidacao_sku(sku, fisico_total)
    sku.quantidade_fisica = fisico_total
    sku.diferenca = diferenca

    _aplicar_status_pos_consolidacao(
        sku,
        diferenca,
        usuario,
        agora,
        recontagem=recontagem,
    )
    sku.save()
    _sincronizar_status_itens_contados_sku(sku)
    _registrar_historico_consolidacao(sku, usuario, fisico_total, agora, tipo_historico)
    tentar_sincronizar_estoque_fisico_pos_finalizacao(sku, status_anterior, usuario)


@transaction.atomic
def finalizar_contagem_sku_pocket(
    sku: CicloInventarioSku,
    usuario,
) -> CicloInventarioSku:
    from inventario.services.ciclico_estoque_fisico import (
        tentar_sincronizar_estoque_fisico_pos_finalizacao,
    )
    from inventario.services.recontagem_multiusuario import (
        processar_divergencia_pos_contagem,
    )

    sku = CicloInventarioSku.objects.select_for_update().prefetch_related('posicoes').get(
        pk=sku.pk,
    )
    if sku.status_contagem == StatusItemCiclico.EXCLUIDO:
        raise CiclicoError('SKU excluído do ciclo não pode ser finalizado.')

    recontagem = sku.status_contagem in (
        StatusItemCiclico.DIVERGENTE,
        StatusItemCiclico.RECONTAGEM,
    )
    if sku.status_contagem in StatusItemCiclico.FINALIZADOS:
        raise CiclicoError('Este SKU já foi finalizado.')

    posicoes_contadas = [
        posicao for posicao in sku.posicoes.all()
        if posicao.quantidade_fisica is not None
        and not _posicao_generica_sem_contagem(posicao)
    ]
    if not posicoes_contadas:
        raise CiclicoError('Registre ao menos uma contagem antes de finalizar o SKU.')

    status_anterior = sku.status_contagem
    agora = timezone.now()
    fisico_total = sum(_decimal(p.quantidade_fisica) for p in posicoes_contadas)
    diferenca = _diferenca_consolidacao_sku(sku, fisico_total)
    tipo_historico = (
        CicloAuditoriaHistorico.TipoRegistro.RECONTAGEM
        if recontagem
        else CicloAuditoriaHistorico.TipoRegistro.CONSOLIDACAO
    )

    sku.quantidade_fisica = fisico_total
    sku.diferenca = diferenca
    if diferenca == 0:
        sku.status_contagem = StatusItemCiclico.VALIDADO
    else:
        sku.status_contagem = StatusItemCiclico.DIVERGENTE

    sku.save(update_fields=['quantidade_fisica', 'diferenca', 'status_contagem'])
    _sincronizar_status_itens_contados_sku(sku)

    if sku.status_contagem == StatusItemCiclico.DIVERGENTE and not recontagem:
        for posicao_item in posicoes_contadas:
            processar_divergencia_pos_contagem(posicao_item, gerado_por=usuario)

    _registrar_historico_consolidacao(sku, usuario, fisico_total, agora, tipo_historico)
    tentar_sincronizar_estoque_fisico_pos_finalizacao(sku, status_anterior, usuario)
    return sku


def _sku_esta_no_lote_ativo(sku: CicloInventarioSku, session) -> bool:
    return sku.pk in obter_lote_sessao(session)


def _montar_posicoes_ciclo(
    ciclo: CicloInventario,
    sku: CicloInventarioSku,
    produto,
    registros_fisicos: list[EstoqueFisico],
    posicao_generica: Posicao,
) -> list[CicloInventarioItem]:
    if registros_fisicos:
        return [
            CicloInventarioItem(
                ciclo=ciclo,
                ciclo_sku=sku,
                produto=produto,
                codigo_produto=produto.codigo_produto,
                descricao=produto.descricao,
                embalagem=produto.embalagem or '',
                posicao=registro.posicao,
                codigo_posicao=registro.posicao.codigo,
                alocacao=registro.posicao.posicao,
                setor=produto.setor or '',
            )
            for registro in registros_fisicos
        ]

    return [
        CicloInventarioItem(
            ciclo=ciclo,
            ciclo_sku=sku,
            produto=produto,
            codigo_produto=produto.codigo_produto,
            descricao=produto.descricao,
            embalagem=produto.embalagem or '',
            posicao=posicao_generica,
            codigo_posicao=posicao_generica.codigo,
            alocacao=posicao_generica.posicao,
            setor=produto.setor or '',
        ),
    ]


@transaction.atomic
def criar_ciclo(usuario_criacao=None, **_ignorado) -> CicloInventario:
    sap_por_produto = _obter_estoque_sap_por_produto()
    if not sap_por_produto:
        raise CiclicoError('Não há estoque SAP disponível para gerar o ciclo.')

    produtos_ordenados = _resolver_produtos_congelamento(sap_por_produto)
    if not produtos_ordenados:
        raise CiclicoError('Nenhum SKU elegível encontrado no estoque SAP.')

    fisicos_por_produto = _obter_fisicos_por_produto()
    posicao_generica = _obter_posicao_contagem_generica()

    for ciclo_ativo in CicloInventario.objects.filter(
        ativo=True,
        status_ciclo=StatusCiclo.ATIVO,
    ):
        congelar_snapshot_ciclo(ciclo_ativo)
    ciclo = CicloInventario.objects.create(
        ativo=True,
        status_ciclo=StatusCiclo.ATIVO,
        usuario_criacao=usuario_criacao,
        quantidade_skus_planejados=len(produtos_ordenados),
        skus_por_dia=None,
        dia_execucao=1,
        embalagens_filtro=[],
        canais_filtro=[],
        completar_lote_automaticamente=False,
        respeitar_somente_embalagens=False,
    )

    skus_para_criar: list[CicloInventarioSku] = []
    for ordem, produto_id in enumerate(produtos_ordenados, start=1):
        sap = sap_por_produto[produto_id]
        produto = sap.produto
        skus_para_criar.append(CicloInventarioSku(
            ciclo=ciclo,
            produto=produto,
            codigo_produto=produto.codigo_produto,
            descricao=produto.descricao,
            embalagem=produto.embalagem or '',
            setor=produto.setor or '',
            quantidade_sap=_decimal(sap.total),
            quantidade_cosan=_decimal(sap.canal_110),
            quantidade_brida=_decimal(sap.canal_1),
            data_atualizacao_sap=sap.data_importacao,
            status_contagem=StatusItemCiclico.PENDENTE,
            ordem_planejamento=ordem,
        ))

    CicloInventarioSku.objects.bulk_create(skus_para_criar)
    skus_por_produto = {
        sku.produto_id: sku
        for sku in CicloInventarioSku.objects.filter(ciclo=ciclo)
    }

    posicoes_para_criar: list[CicloInventarioItem] = []
    for produto_id in produtos_ordenados:
        sku = skus_por_produto[produto_id]
        produto = sku.produto
        posicoes_para_criar.extend(
            _montar_posicoes_ciclo(
                ciclo,
                sku,
                produto,
                fisicos_por_produto.get(produto_id, []),
                posicao_generica,
            ),
        )

    CicloInventarioItem.objects.bulk_create(posicoes_para_criar)
    return ciclo


def ciclo_pronto_para_encerramento(ciclo: CicloInventario) -> bool:
    skus = CicloInventarioSku.objects.filter(ciclo=ciclo)
    for sku in skus:
        if sku.status_contagem in StatusItemCiclico.FINALIZADOS:
            continue
        return False
    return skus.exists()


@transaction.atomic
def encerrar_ciclo_automatico(usuario) -> CicloInventario | None:
    ciclo = _obter_ciclo_ativo()
    if ciclo is None or not ciclo_pronto_para_encerramento(ciclo):
        return None
    ciclo = congelar_snapshot_ciclo(ciclo)
    ciclo.usuario_encerramento = usuario
    ciclo.save(update_fields=['usuario_encerramento'])
    return ciclo


@transaction.atomic
def congelar_snapshot_ciclo(ciclo: CicloInventario) -> CicloInventario:
    resumo = calcular_resumo_ciclo(ciclo)
    contados_com_fisico = sum(
        1 for sku in CicloInventarioSku.objects.filter(ciclo=ciclo)
        if sku.status_contagem != StatusItemCiclico.EXCLUIDO
        and sku.quantidade_fisica is not None
    )
    taxa = Decimal('0')
    if contados_com_fisico:
        conciliados = resumo.skus_conciliados
        taxa = (
            Decimal(conciliados) / Decimal(contados_com_fisico) * Decimal('100')
        ).quantize(Decimal('0.01'))

    embalagens = ciclo.embalagens_filtro or []
    canais = ciclo.canais_filtro or []
    criterio_partes = ['Congelamento SAP completo']
    if embalagens:
        criterio_partes.append(f'Embalagens: {", ".join(embalagens)}')
    if ciclo.skus_por_dia:
        criterio_partes.append(f'SKUs/dia: {ciclo.skus_por_dia}')

    ciclo.quantidade_skus_contados = resumo.skus_contados
    ciclo.quantidade_skus_divergentes = resumo.skus_divergentes
    ciclo.quantidade_skus_validados = resumo.skus_validados
    ciclo.percentual_executado = resumo.percentual_executado
    ciclo.taxa_acuracidade = taxa
    ciclo.criterio_utilizado = ' | '.join(criterio_partes)
    ciclo.canal_utilizado = ', '.join(canais) if canais else 'Todos'
    ciclo.status_ciclo = StatusCiclo.ENCERRADO
    ciclo.ativo = False
    if ciclo.data_encerramento is None:
        ciclo.data_encerramento = timezone.now()
    ciclo.save(update_fields=[
        'quantidade_skus_contados',
        'quantidade_skus_divergentes',
        'quantidade_skus_validados',
        'percentual_executado',
        'taxa_acuracidade',
        'criterio_utilizado',
        'canal_utilizado',
        'status_ciclo',
        'ativo',
        'data_encerramento',
    ])
    encerrar_lotes_execucao_ciclo(ciclo)
    return ciclo


@transaction.atomic
def encerrar_ciclo() -> CicloInventario:
    ciclo = _obter_ciclo_ativo()
    if ciclo is None:
        raise CiclicoError('Nenhum ciclo cíclico ativo.')
    return congelar_snapshot_ciclo(ciclo)


@transaction.atomic
def arquivar_ciclo(ciclo_id: int) -> CicloInventario:
    try:
        ciclo = CicloInventario.objects.get(pk=ciclo_id)
    except CicloInventario.DoesNotExist as exc:
        raise CiclicoError('Ciclo não encontrado.') from exc
    if ciclo.status_ciclo != StatusCiclo.ENCERRADO:
        raise CiclicoError('Somente ciclos encerrados podem ser arquivados.')
    ciclo.status_ciclo = StatusCiclo.ARQUIVADO
    ciclo.save(update_fields=['status_ciclo'])
    return ciclo


@transaction.atomic
def reabrir_ciclo(ciclo_id: int) -> CicloInventario:
    if _obter_ciclo_ativo() is not None:
        raise CiclicoError('Já existe um ciclo ativo. Encerre-o antes de reabrir outro.')
    try:
        ciclo = CicloInventario.objects.get(pk=ciclo_id)
    except CicloInventario.DoesNotExist as exc:
        raise CiclicoError('Ciclo não encontrado.') from exc
    if ciclo.status_ciclo == StatusCiclo.ARQUIVADO:
        raise CiclicoError('Ciclos arquivados não podem ser reabertos.')
    if ciclo.status_ciclo == StatusCiclo.ATIVO:
        raise CiclicoError('Este ciclo já está ativo.')
    ciclo.status_ciclo = StatusCiclo.ATIVO
    ciclo.ativo = True
    ciclo.data_encerramento = None
    ciclo.save(update_fields=['status_ciclo', 'ativo', 'data_encerramento'])
    return ciclo


@transaction.atomic
def definir_dia_execucao(dia: int) -> CicloInventario:
    ciclo = _obter_ciclo_ativo()
    if ciclo is None:
        raise CiclicoError('Nenhum ciclo cíclico ativo.')
    if dia <= 0:
        raise CiclicoError('Informe um dia de execução válido.')

    lote = _calcular_lote_diario(ciclo)
    if dia > lote.total_dias:
        raise CiclicoError(f'O ciclo possui apenas {lote.total_dias} dia(s) de execução.')

    ciclo.dia_execucao = dia
    ciclo.save(update_fields=['dia_execucao'])
    return ciclo


def obter_ciclo_atual(session=None) -> CicloInfo | None:
    ciclo = _obter_ciclo_ativo()
    if ciclo is None:
        return None

    lote_execucao = obter_lote_execucao_info()
    return CicloInfo(
        pk=ciclo.pk,
        data_criacao=ciclo.data_criacao,
        data_encerramento=ciclo.data_encerramento,
        ativo=ciclo.ativo,
        quantidade_skus_planejados=ciclo.quantidade_skus_planejados,
        skus_por_dia=None,
        dia_execucao=ciclo.dia_execucao,
        embalagens_filtro=[],
        canais_filtro=[],
        completar_lote_automaticamente=False,
        respeitar_somente_embalagens=False,
        lote_diario=None,
        lote_execucao=lote_execucao,
    )


def calcular_resumo_skus(skus: list[CicloInventarioSku]) -> CicloResumo:
    ativos = [sku for sku in skus if sku.status_contagem != StatusItemCiclico.EXCLUIDO]
    total_skus = len(ativos)
    pendentes = sum(1 for sku in ativos if sku.status_contagem == StatusItemCiclico.PENDENTE)
    divergentes = sum(
        1 for sku in ativos
        if sku.status_contagem in (
            StatusItemCiclico.DIVERGENTE,
            StatusItemCiclico.RECONTAGEM,
        )
    )
    validados = sum(
        1 for sku in ativos
        if sku.status_contagem in (
            StatusItemCiclico.VALIDADO,
            StatusItemCiclico.VALIDADO_DIVERGENCIA,
        )
    )
    excluidos = sum(
        1 for sku in skus if sku.status_contagem == StatusItemCiclico.EXCLUIDO
    )
    contados = sum(
        1 for sku in ativos
        if sku.status_contagem not in (
            StatusItemCiclico.PENDENTE,
            StatusItemCiclico.EXCLUIDO,
        )
    )

    if total_skus == 0:
        percentual = Decimal('0')
    else:
        percentual = (
            Decimal(contados) / Decimal(total_skus) * Decimal('100')
        ).quantize(Decimal('0.01'))

    conciliados = acima = abaixo = 0
    por_embalagem: dict[str, int] = {}
    por_setor: dict[str, int] = {}
    por_usuario: dict[str, int] = {}
    por_origem: dict[str, int] = {}
    por_canal_cosan = por_canal_brida = 0

    for sku in ativos:
        embalagem = sku.embalagem or 'Sem embalagem'
        por_embalagem[embalagem] = por_embalagem.get(embalagem, 0) + 1
        setor = sku.setor or 'Sem setor'
        por_setor[setor] = por_setor.get(setor, 0) + 1
        cosan, brida = _obter_canais_sku(sku)
        if cosan and cosan > 0:
            por_canal_cosan += 1
        if brida and brida > 0:
            por_canal_brida += 1

        for nome in sku.usuarios_contagem_nomes:
            if nome != 'Não informado':
                por_usuario[nome] = por_usuario.get(nome, 0) + 1
        _, origem_label, _, _, _ = _obter_ultima_contagem_sku_info(sku)
        if origem_label:
            por_origem[origem_label] = por_origem.get(origem_label, 0) + 1

        if sku.quantidade_fisica is None:
            continue
        _, indicador, _ = _calcular_indicador_confronto(
            sku.quantidade_fisica,
            sku.quantidade_sap,
        )
        if indicador == IndicadorConfronto.VERDE:
            conciliados += 1
        elif indicador == IndicadorConfronto.LARANJA:
            acima += 1
        elif indicador == IndicadorConfronto.VERMELHO:
            abaixo += 1

    return CicloResumo(
        total_skus=total_skus,
        skus_pendentes=pendentes,
        skus_contados=contados,
        skus_divergentes=divergentes,
        skus_validados=validados,
        skus_excluidos=excluidos,
        percentual_executado=percentual,
        skus_conciliados=conciliados,
        skus_acima_sap=acima,
        skus_abaixo_sap=abaixo,
        por_embalagem=por_embalagem,
        por_setor=por_setor,
        por_canal_cosan=por_canal_cosan,
        por_canal_brida=por_canal_brida,
        skus_conciliados_cosan=conciliados,
        skus_acima_cosan=acima,
        skus_abaixo_cosan=abaixo,
        dias_restantes=0,
        estimativa_termino=None,
        por_usuario=por_usuario,
        por_origem=por_origem,
    )


def calcular_resumo_ciclo(ciclo: CicloInventario) -> CicloResumo:
    skus = list(CicloInventarioSku.objects.filter(ciclo=ciclo))
    return calcular_resumo_skus(skus)


def obter_resumo_ciclico(ciclo_id: int | None = None) -> CicloResumo:
    ciclo = obter_ciclo_consulta(ciclo_id)
    if ciclo is None:
        return CicloResumo(0, 0, 0, 0, 0, 0, Decimal('0'))
    return calcular_resumo_ciclo(ciclo)


def obter_indicadores_ciclico_dashboard() -> IndicadoresCiclicoDashboard:
    resumo = obter_resumo_ciclico()
    return IndicadoresCiclicoDashboard(
        itens_planejados=resumo.total_skus,
        itens_contados=resumo.skus_contados,
        percentual_concluido=resumo.percentual_executado,
    )


def _queryset_skus_lote(ciclo: CicloInventario):
    lote = _calcular_lote_diario(ciclo)
    pool = _obter_pool_execucao(ciclo)
    if not ciclo.skus_por_dia:
        return pool, lote

    inicio = (lote.dia - 1) * ciclo.skus_por_dia
    fim = inicio + lote.skus_no_lote
    return pool[inicio:fim], lote


def obter_skus_ciclo(
    session=None,
    apenas_lote_diario: bool = True,
    filtros: FiltrosCicloConsulta | None = None,
    incluir_posicoes: bool = False,
    usuario=None,
) -> list[SkuCicloDetalhe]:
    ciclo = _obter_ciclo_ativo()
    if ciclo is None:
        return []

    if apenas_lote_diario:
        skus_list = _obter_skus_lote_sessao(session)
    else:
        skus_list = list(
            CicloInventarioSku.objects.filter(ciclo=ciclo)
            .exclude(status_contagem=StatusItemCiclico.EXCLUIDO)
            .order_by('ordem_planejamento', 'codigo_produto'),
        )

    skus_list = _aplicar_filtros_consulta(skus_list, filtros)
    return [
        _sku_para_dto(sku, incluir_posicoes=incluir_posicoes, usuario=usuario)
        for sku in skus_list
    ]


def obter_sku_detalhe(sku_id: int, ciclo_id: int | None = None) -> SkuCicloDetalhe:
    try:
        if ciclo_id:
            sku = CicloInventarioSku.objects.get(pk=sku_id, ciclo_id=ciclo_id)
        else:
            sku = CicloInventarioSku.objects.get(pk=sku_id)
    except CicloInventarioSku.DoesNotExist as exc:
        raise CiclicoError('SKU não encontrado no ciclo cíclico.') from exc

    return _sku_para_dto(sku, incluir_posicoes=True, incluir_historico=True)


MSG_CICLO_ENCERRADO = (
    'Esta contagem pertence a um ciclo encerrado e não pode mais ser alterada.'
)


def _validar_ciclo_aberto_para_edicao(sku: CicloInventarioSku) -> CicloInventario:
    ciclo = _obter_ciclo_ativo()
    if ciclo is None or sku.ciclo_id != ciclo.pk or not ciclo.ativo:
        raise CiclicoError(MSG_CICLO_ENCERRADO)
    return ciclo


def usuario_pode_editar_contagem_ciclico(usuario, sku: CicloInventarioSku) -> bool:
    from accounts.models import Usuario
    from accounts.services.perfil import obter_perfil_usuario, usuario_pode_escrever_inventario

    if not usuario or not getattr(usuario, 'is_authenticated', False):
        return False
    if not usuario_pode_escrever_inventario(usuario):
        return False
    if sku.status_contagem == StatusItemCiclico.EXCLUIDO:
        return False
    try:
        _validar_ciclo_aberto_para_edicao(sku)
    except CiclicoError:
        return False

    perfil = obter_perfil_usuario(usuario)
    if perfil == Usuario.Perfil.ADMINISTRADOR:
        return sku.posicoes.filter(quantidade_fisica__isnull=False).exists()
    if perfil == Usuario.Perfil.INVENTARIO:
        return sku.posicoes.filter(
            quantidade_fisica__isnull=False,
            usuario_contagem=usuario,
        ).exists()
    return False


def usuario_pode_excluir_sku_ciclico(usuario, sku: CicloInventarioSku) -> bool:
    from accounts.services.perfil import usuario_pode_escrever_inventario

    if not usuario or not getattr(usuario, 'is_authenticated', False):
        return False
    if not usuario_pode_escrever_inventario(usuario):
        return False
    if sku.status_contagem == StatusItemCiclico.EXCLUIDO:
        return False
    try:
        _validar_ciclo_aberto_para_edicao(sku)
    except CiclicoError:
        return False
    return True


@transaction.atomic
def editar_contagem_ciclico(
    sku_id: int,
    edicoes: dict[int, dict],
    motivo: str,
    usuario,
) -> SkuCicloDetalhe:
    motivo = motivo.strip()
    if not motivo:
        raise CiclicoError('Informe o motivo da alteração.')

    sku = CicloInventarioSku.objects.prefetch_related(
        'posicoes',
        'posicoes__posicao',
    ).get(pk=sku_id)
    _validar_ciclo_aberto_para_edicao(sku)

    if not usuario_pode_editar_contagem_ciclico(usuario, sku):
        raise CiclicoError('Sem permissão para editar esta contagem.')

    from accounts.models import Usuario

    perfil = None
    if usuario and usuario.is_authenticated:
        from accounts.services.perfil import obter_perfil_usuario
        perfil = obter_perfil_usuario(usuario)

    agora = timezone.now()
    posicoes_por_id = {item.pk: item for item in sku.posicoes.all()}
    houve_alteracao = False

    for item_id_raw, dados in edicoes.items():
        item_id = int(item_id_raw)
        if item_id not in posicoes_por_id:
            raise CiclicoError('Posição do SKU inválida.')
        item = posicoes_por_id[item_id]

        if item.quantidade_fisica is None:
            continue

        if perfil == Usuario.Perfil.INVENTARIO and item.usuario_contagem_id != usuario.pk:
            raise CiclicoError('Sem permissão para editar esta posição.')

        posicao_id = int(dados['posicao_id'])
        quantidade_nova = _decimal(dados['quantidade'])
        nova_posicao = Posicao.objects.get(pk=posicao_id, ativo=True)

        quantidade_anterior = item.quantidade_fisica
        posicao_anterior = item.codigo_posicao
        alteracoes: list[str] = []

        if quantidade_anterior != quantidade_nova:
            alteracoes.append(f'Quantidade:\n{quantidade_anterior} → {quantidade_nova}')
        if item.posicao_id != nova_posicao.pk:
            alteracoes.append(
                f'Posição:\n{posicao_anterior} → {nova_posicao.codigo}',
            )

        if not alteracoes:
            continue

        houve_alteracao = True
        item.quantidade_fisica = quantidade_nova
        item.posicao = nova_posicao
        item.codigo_posicao = nova_posicao.codigo
        item.alocacao = nova_posicao.posicao
        item.save(update_fields=[
            'quantidade_fisica',
            'posicao',
            'codigo_posicao',
            'alocacao',
        ])

        texto_motivo = '\n\n'.join(alteracoes) + f'\n\nMotivo:\n{motivo}'
        CicloAuditoriaHistorico.objects.create(
            ciclo_sku=sku,
            item=item,
            codigo_posicao=nova_posicao.codigo,
            tipo=CicloAuditoriaHistorico.TipoRegistro.EDICAO,
            usuario=usuario,
            data_hora=agora,
            quantidade_sap_momento=sku.quantidade_sap,
            quantidade_fisica=quantidade_nova,
            diferenca=quantidade_nova - sku.quantidade_sap,
            origem_contagem=item.origem_contagem or '',
            motivo=texto_motivo,
        )

    if not houve_alteracao:
        raise CiclicoError('Nenhuma alteração informada.')

    _recalcular_consolidacao_sku(
        sku,
        usuario,
        CicloAuditoriaHistorico.TipoRegistro.CONSOLIDACAO,
    )
    sku.refresh_from_db()
    return _sku_para_dto(sku, incluir_posicoes=True, incluir_historico=True, usuario=usuario)


def obter_consulta_agrupada_por_sku(
    filtros: FiltrosCicloConsulta | None = None,
    ciclo_id: int | None = None,
) -> list[GrupoProdutoCiclo]:
    from inventario.services.ciclico_relatorio import obter_grupos_consulta_ciclo

    alvo = ciclo_id
    if filtros and filtros.ciclo_id:
        alvo = filtros.ciclo_id
    ciclo = obter_ciclo_consulta(alvo)
    if ciclo is None:
        return []
    return obter_grupos_consulta_ciclo(ciclo.pk, filtros)


def obter_dados_exportacao_ciclo() -> list[dict]:
    ciclo = _obter_ciclo_ativo()
    if ciclo is None:
        return []

    skus = CicloInventarioSku.objects.filter(ciclo=ciclo).prefetch_related(
        'posicoes',
        'posicoes__usuario_contagem',
        'posicoes__usuario_contagem__perfil_operacional',
    ).order_by('ordem_planejamento', 'codigo_produto')
    skus_list = list(skus)

    linhas: list[dict] = []
    for sku in skus_list:
        dto = _sku_para_dto(sku)
        ultima_contagem = _obter_ultima_contagem_sku(sku)
        linhas.append({
            'SKU': dto.codigo_produto,
            'Descrição': dto.descricao,
            'Embalagem': dto.embalagem,
            'Setor': dto.setor,
            'SAP': float(dto.quantidade_sap),
            'COSAN': float(dto.quantidade_cosan) if dto.quantidade_cosan is not None else None,
            'BRIDA': float(dto.quantidade_brida) if dto.quantidade_brida is not None else None,
            'FÍSICO': float(dto.quantidade_fisica) if dto.quantidade_fisica is not None else None,
            'Diferença': float(dto.diferenca_cosan) if dto.diferenca_cosan is not None else None,
            'Indicador': IndicadorConfronto.EMOJI.get(dto.indicador_sap, ''),
            'Usuários': ', '.join(dto.usuarios),
            'Data Contagem': (
                timezone.localtime(ultima_contagem).strftime('%d/%m/%Y %H:%M')
                if ultima_contagem
                else ''
            ),
            'Status': dto.status_label,
        })

    return linhas


@transaction.atomic
def excluir_sku_do_ciclo(
    sku_id: int,
    motivo: str,
    usuario,
) -> CicloInventarioSku:
    ciclo = _obter_ciclo_ativo()
    if ciclo is None:
        raise CiclicoError('Nenhum ciclo cíclico ativo.')

    motivo = motivo.strip()
    if not motivo:
        raise CiclicoError('Informe o motivo da exclusão.')

    try:
        sku = CicloInventarioSku.objects.get(pk=sku_id, ciclo=ciclo)
    except CicloInventarioSku.DoesNotExist as exc:
        raise CiclicoError('SKU não encontrado no ciclo cíclico.') from exc

    if sku.status_contagem == StatusItemCiclico.EXCLUIDO:
        raise CiclicoError('SKU já está excluído do ciclo.')

    agora = timezone.now()
    sku.status_contagem = StatusItemCiclico.EXCLUIDO
    sku.motivo_exclusao = motivo
    sku.usuario_exclusao = usuario
    sku.data_exclusao = agora
    sku.save(update_fields=[
        'status_contagem',
        'motivo_exclusao',
        'usuario_exclusao',
        'data_exclusao',
    ])

    CicloAuditoriaHistorico.objects.create(
        ciclo_sku=sku,
        tipo=CicloAuditoriaHistorico.TipoRegistro.EXCLUSAO,
        usuario=usuario,
        data_hora=agora,
        quantidade_sap_momento=sku.quantidade_sap,
        quantidade_fisica=sku.quantidade_fisica or Decimal('0'),
        diferenca=sku.diferenca or Decimal('0'),
        motivo=motivo,
    )
    return sku


@transaction.atomic
def salvar_contagem_sku(
    sku_id: int,
    contagens_posicao: dict[int, Decimal],
    usuario,
    recontagem: bool = False,
    origem_contagem: str = OrigemContagem.WEB,
    dispositivo_contagem: str = '',
) -> SkuCicloDetalhe:
    ciclo = _obter_ciclo_ativo()
    if ciclo is None:
        raise CiclicoError('Nenhum ciclo cíclico ativo.')

    try:
        sku = CicloInventarioSku.objects.select_for_update().prefetch_related('posicoes').get(
            pk=sku_id,
            ciclo=ciclo,
        )
    except CicloInventarioSku.DoesNotExist as exc:
        raise CiclicoError('SKU não encontrado no ciclo cíclico.') from exc

    if sku.status_contagem == StatusItemCiclico.EXCLUIDO:
        raise CiclicoError('SKU excluído do ciclo não pode receber contagem.')

    if recontagem:
        if sku.status_contagem not in (
            StatusItemCiclico.DIVERGENTE,
            StatusItemCiclico.RECONTAGEM,
        ):
            raise CiclicoError('Recontagem permitida apenas para SKUs divergentes.')
        tipo_posicao = CicloAuditoriaHistorico.TipoRegistro.RECONTAGEM
        tipo_consolidacao = CicloAuditoriaHistorico.TipoRegistro.RECONTAGEM
    else:
        if sku.status_contagem != StatusItemCiclico.PENDENTE:
            raise CiclicoError('Somente SKUs pendentes podem receber contagem inicial.')
        tipo_posicao = CicloAuditoriaHistorico.TipoRegistro.CONTAGEM
        tipo_consolidacao = CicloAuditoriaHistorico.TipoRegistro.CONSOLIDACAO

    posicoes_por_id = {posicao.pk: posicao for posicao in sku.posicoes.all()}
    if set(contagens_posicao.keys()) != set(posicoes_por_id.keys()):
        raise CiclicoError('Informe a quantidade de todas as posições do SKU.')

    agora = timezone.now()
    campos_item = [
        'quantidade_fisica',
        'usuario_contagem',
        'data_contagem',
        'origem_contagem',
        'dispositivo_contagem',
    ]
    if recontagem:
        campos_item.extend([
            'quantidade_recontagem',
            'usuario_recontagem',
            'data_recontagem',
            'status_contagem',
        ])
    for item_id, quantidade in contagens_posicao.items():
        item = posicoes_por_id[item_id]
        quantidade = _decimal(quantidade)
        item.quantidade_fisica = quantidade
        item.usuario_contagem = usuario
        item.data_contagem = agora
        item.origem_contagem = origem_contagem
        item.dispositivo_contagem = dispositivo_contagem
        if recontagem:
            item.quantidade_recontagem = quantidade
            item.usuario_recontagem = usuario
            item.data_recontagem = agora
            item.status_contagem = StatusItemCiclico.RECONTAGEM
        item.save(update_fields=campos_item)
        _registrar_historico_posicao(
            sku,
            item,
            tipo_posicao,
            usuario,
            quantidade,
            agora,
            origem_contagem=origem_contagem,
            dispositivo_contagem=dispositivo_contagem,
        )

    _consolidar_sku(sku, usuario, tipo_consolidacao)
    sku.refresh_from_db()
    return _sku_para_dto(sku, incluir_posicoes=True, incluir_historico=True)


@transaction.atomic
def registrar_contagem_pocket_ciclico(
    session,
    posicao: Posicao,
    produto: Produto,
    quantidade: Decimal,
    usuario,
    dispositivo: str = '',
) -> SkuCicloDetalhe:
    ciclo = _obter_ciclo_ativo()
    if ciclo is None:
        raise CiclicoError('Nenhum ciclo cíclico ativo.')

    sku = CicloInventarioSku.objects.filter(
        ciclo=ciclo,
        produto=produto,
    ).first()
    if sku is None:
        raise CiclicoError('Produto não pertence ao ciclo cíclico ativo.')
    if sku.status_contagem == StatusItemCiclico.EXCLUIDO:
        raise CiclicoError('SKU excluído do ciclo não pode receber contagem.')

    recontagem = sku.status_contagem in (
        StatusItemCiclico.DIVERGENTE,
        StatusItemCiclico.RECONTAGEM,
    )
    if not recontagem and not _sku_esta_no_lote_ativo(sku, session):
        raise CiclicoError('SKU fora do lote diário de execução.')
    if recontagem:
        tipo_posicao = CicloAuditoriaHistorico.TipoRegistro.RECONTAGEM
        tipo_consolidacao = CicloAuditoriaHistorico.TipoRegistro.RECONTAGEM
        origem = OrigemContagem.RECONTAGEM
    elif sku.status_contagem == StatusItemCiclico.PENDENTE:
        tipo_posicao = CicloAuditoriaHistorico.TipoRegistro.CONTAGEM
        tipo_consolidacao = CicloAuditoriaHistorico.TipoRegistro.CONSOLIDACAO
        origem = OrigemContagem.POCKET
    else:
        tipo_posicao = CicloAuditoriaHistorico.TipoRegistro.CONTAGEM
        tipo_consolidacao = CicloAuditoriaHistorico.TipoRegistro.CONSOLIDACAO
        origem = OrigemContagem.POCKET

    sku = CicloInventarioSku.objects.select_for_update().prefetch_related('posicoes').get(pk=sku.pk)
    item = sku.posicoes.filter(posicao=posicao).first()
    if item is None:
        item = CicloInventarioItem.objects.create(
            ciclo=ciclo,
            ciclo_sku=sku,
            produto=produto,
            codigo_produto=produto.codigo_produto,
            descricao=produto.descricao,
            embalagem=produto.embalagem or '',
            posicao=posicao,
            codigo_posicao=posicao.codigo,
            alocacao=posicao.posicao,
            setor=produto.setor or '',
        )

    agora = timezone.now()
    quantidade_bipada = _decimal(quantidade)

    if (
        not recontagem
        and item.data_contagem is not None
        and item.quantidade_fisica is not None
    ):
        raise CiclicoContagemDuplicadaError(
            contexto_auditoria={
                'ciclo': ciclo,
                'usuario': usuario,
                'dispositivo': dispositivo,
                'posicao': posicao,
                'produto': produto,
                'quantidade': quantidade_bipada,
            },
        )

    nova_quantidade = _calcular_quantidade_acumulada_pocket(
        item,
        quantidade_bipada,
        recontagem=recontagem,
        session=session,
        sku_id=sku.pk,
    )
    item.quantidade_fisica = nova_quantidade
    item.usuario_contagem = usuario
    item.data_contagem = agora
    item.origem_contagem = origem
    item.dispositivo_contagem = dispositivo
    campos_item = [
        'quantidade_fisica',
        'usuario_contagem',
        'data_contagem',
        'origem_contagem',
        'dispositivo_contagem',
    ]
    if recontagem:
        item.quantidade_recontagem = nova_quantidade
        item.usuario_recontagem = usuario
        item.data_recontagem = agora
        item.status_contagem = CicloInventarioItem.StatusContagem.RECONTAGEM
        campos_item.extend([
            'quantidade_recontagem',
            'usuario_recontagem',
            'data_recontagem',
            'status_contagem',
        ])
    else:
        item.status_contagem = CicloInventarioItem.StatusContagem.EM_CONTAGEM
        campos_item.append('status_contagem')
    item.save(update_fields=campos_item)
    _registrar_historico_posicao(
        sku,
        item,
        tipo_posicao,
        usuario,
        quantidade_bipada,
        agora,
        origem_contagem=origem,
        dispositivo_contagem=dispositivo,
    )
    _recalcular_consolidacao_sku(
        sku,
        usuario,
        tipo_consolidacao,
        deferir_status_parcial_pocket=True,
    )
    sku.refresh_from_db()
    return _sku_para_dto(sku, incluir_posicoes=True, incluir_historico=True)


def listar_skus_pocket_ciclico(session) -> list[SkuCicloDetalhe]:
    return obter_skus_ciclo(session=session, incluir_posicoes=False)


def buscar_sku_lote_por_produto(session, produto: Produto) -> CicloInventarioSku | None:
    ciclo = _obter_ciclo_ativo()
    if ciclo is None:
        return None
    ids = set(obter_lote_sessao(session))
    if not ids:
        return None
    return CicloInventarioSku.objects.filter(
        ciclo=ciclo,
        pk__in=ids,
        produto=produto,
    ).first()


@transaction.atomic
def validar_sku(sku_id: int) -> SkuCicloDetalhe:
    ciclo = _obter_ciclo_ativo()
    if ciclo is None:
        raise CiclicoError('Nenhum ciclo cíclico ativo.')

    try:
        sku = CicloInventarioSku.objects.get(pk=sku_id, ciclo=ciclo)
    except CicloInventarioSku.DoesNotExist as exc:
        raise CiclicoError('SKU não encontrado no ciclo cíclico.') from exc

    from inventario.services.ciclico_estoque_fisico import (
        tentar_sincronizar_estoque_fisico_pos_finalizacao,
    )

    agora = timezone.now()
    status_anterior = sku.status_contagem
    sku.status_contagem = StatusItemCiclico.VALIDADO
    sku.save(update_fields=['status_contagem'])
    CicloAuditoriaHistorico.objects.create(
        ciclo_sku=sku,
        tipo=CicloAuditoriaHistorico.TipoRegistro.VALIDACAO,
        usuario=None,
        data_hora=agora,
        quantidade_sap_momento=sku.quantidade_sap,
        quantidade_fisica=sku.quantidade_fisica or Decimal('0'),
        diferenca=sku.diferenca or Decimal('0'),
    )
    tentar_sincronizar_estoque_fisico_pos_finalizacao(sku, status_anterior, None)
    return _sku_para_dto(sku, incluir_posicoes=True)


def registrar_contagem(item_id: int, quantidade_fisica: Decimal, usuario):
    item = CicloInventarioItem.objects.select_related('ciclo_sku').get(pk=item_id)
    sku = item.ciclo_sku
    contagens = {
        posicao.pk: (
            _decimal(quantidade_fisica)
            if posicao.pk == item_id
            else (posicao.quantidade_fisica or Decimal('0'))
        )
        for posicao in sku.posicoes.all()
    }
    recontagem = sku.status_contagem in (
        StatusItemCiclico.DIVERGENTE,
        StatusItemCiclico.RECONTAGEM,
    )
    return salvar_contagem_sku(sku.pk, contagens, usuario, recontagem=recontagem)


def registrar_recontagem(item_id: int, quantidade_recontagem: Decimal, usuario):
    return registrar_contagem(item_id, quantidade_recontagem, usuario)


def validar_item(item_id: int):
    item = CicloInventarioItem.objects.select_related('ciclo_sku').get(pk=item_id)
    return validar_sku(item.ciclo_sku_id)


def obter_itens_ciclo():
    return obter_skus_ciclo(apenas_lote_diario=False)


@transaction.atomic
def sincronizar_sap_ciclo_ativo(produto_ids: list[int] | None = None) -> int:
    ciclo = _obter_ciclo_ativo()
    if ciclo is None:
        return 0

    sap_por_produto = _obter_estoque_sap_por_produto()
    if produto_ids is not None:
        sap_por_produto = {
            pid: sap for pid, sap in sap_por_produto.items() if pid in produto_ids
        }

    atualizados = 0
    agora = timezone.now()

    for produto_id, sap in sap_por_produto.items():
        try:
            sku = CicloInventarioSku.objects.get(ciclo=ciclo, produto_id=produto_id)
        except CicloInventarioSku.DoesNotExist:
            continue

        if sku.status_contagem not in (StatusItemCiclico.PENDENTE,):
            continue

        sku.quantidade_sap = _decimal(sap.total)
        sku.data_atualizacao_sap = agora
        sku.save(update_fields=['quantidade_sap', 'data_atualizacao_sap'])
        atualizados += 1

    return atualizados


def obter_historico_sku(sku_id: int) -> list[CicloAuditoriaHistorico]:
    ciclo = _obter_ciclo_ativo()
    if ciclo is None:
        return []
    return list(
        CicloAuditoriaHistorico.objects.filter(
            ciclo_sku_id=sku_id,
            ciclo_sku__ciclo=ciclo,
        ).select_related(
            'usuario',
            'usuario__perfil_operacional',
            'item',
        ).order_by('-data_hora'),
    )
