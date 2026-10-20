"""Dados do Painel do Auditor — somente contagens físicas liberadas.

Este módulo é o único "serializador" do auditor: lê apenas lançamentos das
contagens (C1/C2/C3) liberadas na conferência, seleciona campos explícitos
(posição, SKU, descrição, quantidade, operador, momento da disponibilização)
e devolve estruturas simples. A tela, a API JSON, a prévia do gestor e a
exportação usam exclusivamente estas estruturas — contagens privadas nunca
saem do servidor. O horário de captura não é exposto ao auditor.

Cada célula de contagem tem um estado explícito, nunca um zero inventado:
- ``valor``: quantidade efetivamente registrada (0 inclusive);
- ``nao_liberada``: a contagem não está liberada ao auditor;
- ``nao_contado``: contagem liberada, sem registro para a posição + SKU;
- ``nao_necessaria``: C3 dispensada pela regra existente (C1 = C2);
- ``nao_associada``: a conferência não tem essa contagem.

Indicador principal (velocímetro) — "Itens com consenso":
  numerador   = combinações posição + SKU em que duas contagens liberadas
                coincidem (regra `classificar_contagens`, a mesma do
                Comparativo; C3 dispensada quando C1 = C2);
  denominador = combinações posição + SKU com ao menos um registro em
                contagem liberada;
  escopo      = somente contagens liberadas; com menos de duas contagens
                liberadas ou denominador zero: "Sem dados suficientes".

Participação (C1, C2 e C3 lado a lado): itens registrados por quem fez o
registro original (operador da coleta) e, separadamente, itens corrigidos
por quem corrigiu. O sistema NÃO registra bipagens individuais: cada
contagem guarda um registro por posição + SKU.
"""

from __future__ import annotations

import hashlib
import io
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.utils import timezone

from inventario.models import ComparativoContagem, InventarioItem
from inventario.models_auditor import EventoAuditoria, LiberacaoContagemAuditor, PublicacaoAuditoria, PublicacaoAuditoriaItem
from inventario.models_operacional import InventarioAuditoriaEvento
from inventario.services.auditor_publicacao import liberacoes_visiveis
from inventario.services.comparativo_contagens import (
    StatusContagem,
    classificar_contagens,
    formatar_quantidade,
)

NAO_REGISTRADO = 'Não registrado'
ETAPAS = (1, 2, 3)
Modo = LiberacaoContagemAuditor.Modo
Programacao = LiberacaoContagemAuditor.Programacao

ROTULOS_CELULA = {
    'nao_liberada': 'Não liberada',
    'nao_contado': 'Não contado',
    'nao_necessaria': 'Não necessária',
    'nao_associada': '—',
}

# Situação por posição + SKU (somente concordância entre contagens físicas).
STATUS_SKU = {
    'C1_REGISTRADA': 'C1 registrada · aguardando C2',
    'AGUARDANDO_PAR': 'Aguardando par',
    'C1_IGUAL_C2': 'C1 = C2',
    'C1_DIFERENTE_C2': 'C1 ≠ C2',
    'AGUARDANDO_C3': 'Aguardando C3',
    'CONSENSO': 'Consenso definido',
    'REVISAO': 'Revisão necessária',
}
_CAMPOS_ITEM = (
    'posicao_id', 'posicao__codigo', 'posicao__posicao', 'produto__codigo_produto', 'produto__descricao',
    'quantidade_fisica', 'usuario_registro_id', 'data_registro', 'usuario_contagem_id', 'data_contagem',
    'data_alteracao', 'usuario_alteracao_id',
)
FUSO = 'Horário de Brasília (America/Sao_Paulo)'

# Status visual de cada célula: cor + ícone + texto (nunca só a cor).
TONS = {
    'confere': ('Concordância confirmada', 'bi-check-circle-fill'),
    'difere': ('Difere do consenso (valor preservado)', 'bi-exclamation-circle-fill'),
    'divergente': ('Divergência aguardando resolução', 'bi-exclamation-circle-fill'),
    'registrado': ('Contado, sem comparação suficiente', 'bi-record-circle-fill'),
    'nao_contado': ('Não contado', 'bi-dash-circle'),
    'nao_liberada': ('Contagem não liberada', 'bi-lock-fill'),
    'nao_necessaria': ('Não necessária pela regra de concordância', 'bi-slash-circle'),
    'nao_associada': ('Contagem não associada', ''),
}


def _local(valor):
    return timezone.localtime(valor) if valor else None


def _fmt(valor) -> str:
    return _local(valor).strftime('%d/%m/%Y %H:%M') if valor else NAO_REGISTRADO


def _nomes(ids: set[int]) -> dict[int, str]:
    if not ids:
        return {}
    nomes = {}
    for u in get_user_model().objects.filter(pk__in=ids).values(
        'id', 'username', 'first_name', 'last_name', 'perfil_operacional__nome',
    ):
        nomes[u['id']] = u['perfil_operacional__nome'] or f"{u['first_name']} {u['last_name']}".strip() or u['username']
    return nomes


def _nome_usuario(usuario) -> str:
    if usuario is None:
        return ''
    return getattr(getattr(usuario, 'perfil_operacional', None), 'nome', '') or usuario.get_username()


def _rotulo_etapas(etapas) -> str:
    nomes = [f'C{e}' for e in sorted(etapas)]
    if not nomes:
        return 'nenhuma contagem'
    return nomes[0] if len(nomes) == 1 else ', '.join(nomes[:-1]) + ' e ' + nomes[-1]


def _pct(parte: int, total: int):
    return (Decimal(parte) * 100 / Decimal(total)) if total else None


@dataclass
class Lancamento:
    id: int
    etapa: int
    posicao_id: int
    posicao: str
    sku: str
    descricao: str
    quantidade: Decimal
    operador: str
    disponibilizado_em: datetime | None
    corrigido: bool
    registrado_em: datetime | None = None  # horário real: só quando a gestão permite
    corrigido_por: str = ''
    corrigido_em: datetime | None = None   # horário real: só quando a gestão permite


def _registros_visiveis(comparativo: ComparativoContagem, liberacoes: dict) -> list[dict]:
    """Registros das contagens liberadas: {id, etapa, base, campos do item}.

    Tempo real: todos os registros da contagem, disponibilizados na gravação
    (nunca antes da liberação). Programada: os registros congelados aparecem
    quando o lote deles é liberado; registros gravados depois do início da
    liberação são novos e aparecem conforme gravados.
    """
    registros = []
    for etapa, liberacao in liberacoes.items():
        itens = InventarioItem.objects.filter(inventario_id=liberacao.inventario_id_liberado).values('id', *_CAMPOS_ITEM)
        if liberacao.modo == Modo.PROGRAMADA:
            agenda = {
                a['item_original_id']: (a['status'], a['publicado_em'])
                for a in PublicacaoAuditoriaItem.objects.filter(liberacao=liberacao, ciclo=liberacao.ciclo)
                .values('item_original_id', 'status', 'publicado_em')
            }
            for r in itens:
                if r['id'] in agenda:
                    status, publicado_em = agenda[r['id']]
                    if status != PublicacaoAuditoriaItem.Status.PUBLICADO:
                        continue
                    base = publicado_em
                else:
                    if liberacao.inicio_real is None:
                        continue
                    base = max(filter(None, [r['data_registro'] or r['data_contagem'], liberacao.inicio_real]))
                registros.append({**r, 'etapa': etapa, 'base': base})
        else:
            for r in itens:
                base = max(filter(None, [r['data_registro'] or r['data_contagem'], liberacao.liberado_em]), default=None)
                registros.append({**r, 'etapa': etapa, 'base': base})
    return registros


def carregar_lancamentos(
    comparativo: ComparativoContagem, liberacoes: dict | None = None, horario_real: bool = False,
) -> list[Lancamento]:
    """Lançamentos originais das contagens liberadas (campos físicos explícitos).

    Sem permissão de horário real, os horários de captura e correção nem
    entram na estrutura (não há como vazarem para tela, API ou Excel).
    """
    liberacoes = liberacoes_visiveis(comparativo) if liberacoes is None else liberacoes
    registros = _registros_visiveis(comparativo, liberacoes)
    nomes = _nomes((
        {r['usuario_registro_id'] or r['usuario_contagem_id'] for r in registros}
        | {r['usuario_alteracao_id'] for r in registros}
    ) - {None})
    lancamentos = []
    for r in registros:
        original = r['data_registro'] is not None or r['usuario_registro_id']
        usuario_id = r['usuario_registro_id'] if original else r['usuario_contagem_id']
        lancamentos.append(Lancamento(
            id=r['id'],
            etapa=r['etapa'],
            posicao_id=r['posicao_id'],
            posicao=r['posicao__posicao'] or r['posicao__codigo'] or '',
            sku=str(r['produto__codigo_produto']),
            descricao=r['produto__descricao'] or 'Descrição não cadastrada',
            quantidade=Decimal(r['quantidade_fisica']),
            operador=nomes.get(usuario_id, NAO_REGISTRADO) if usuario_id else NAO_REGISTRADO,
            disponibilizado_em=r['base'],
            corrigido=r['data_alteracao'] is not None,
            registrado_em=(r['data_registro'] or r['data_contagem']) if horario_real else None,
            corrigido_por=nomes.get(r['usuario_alteracao_id'], '') if r['data_alteracao'] else '',
            corrigido_em=r['data_alteracao'] if horario_real else None,
        ))
    return lancamentos


def contar_visiveis_por_etapa(comparativo, liberacoes: dict) -> dict[int, int]:
    """Quantidade de registros que o auditor vê agora em cada contagem."""
    return dict(Counter(r['etapa'] for r in _registros_visiveis(comparativo, liberacoes)))


# ---------------------------------------------------------------------------
# Linhas por posição + SKU
# ---------------------------------------------------------------------------


def _status_sku(quantidades: dict, associadas: frozenset) -> tuple[str, object]:
    classificacao = classificar_contagens(quantidades, associadas)
    registradas = [e for e in ETAPAS if quantidades.get(e) is not None]
    if classificacao.status == StatusContagem.OK:
        if classificacao.pares_iguais == [(1, 2)] and 3 not in registradas:
            return 'C1_IGUAL_C2', classificacao
        return 'CONSENSO', classificacao
    if classificacao.status == StatusContagem.DIVERGENTE:
        if len(registradas) == 3:
            return 'REVISAO', classificacao
        if classificacao.necessita_c3:
            return ('AGUARDANDO_C3' if 3 in associadas else 'C1_DIFERENTE_C2'), classificacao
        return 'C1_DIFERENTE_C2', classificacao
    if registradas == [1]:
        return 'C1_REGISTRADA', classificacao
    return 'AGUARDANDO_PAR', classificacao


def _tom(chave: str) -> dict:
    rotulo, icone = TONS[chave]
    return {'tom': chave, 'tom_rotulo': rotulo, 'icone': icone}


def _celula(estado: str, lancamento: Lancamento | None = None, tom: str | None = None) -> dict:
    if lancamento is None:
        return {'estado': estado, 'texto': ROTULOS_CELULA[estado], 'quantidade': None, **_tom(estado)}
    return {
        'estado': 'valor',
        'texto': formatar_quantidade(lancamento.quantidade),
        'quantidade': lancamento.quantidade,
        'operador': lancamento.operador,
        'disponibilizado_em': lancamento.disponibilizado_em,
        'registrado_em': lancamento.registrado_em,
        'corrigido': lancamento.corrigido,
        'corrigido_por': lancamento.corrigido_por,
        'corrigido_em': lancamento.corrigido_em,
        **_tom(tom or 'registrado'),
    }


def _tom_valor(classificacao, etapa: int) -> str:
    """Status visual da célula pela regra existente (`classificar_contagens`)."""
    celula = classificacao.celulas[etapa]
    if celula == 'ok':
        return 'confere'
    if celula == 'divergente':
        return 'difere' if classificacao.status == StatusContagem.OK else 'divergente'
    return 'registrado'


@dataclass
class LinhaAuditor:
    chave: str
    posicao_id: int
    posicao: str
    sku: str
    descricao: str
    celulas: dict  # etapa -> {estado, texto, quantidade, ...}
    status: str
    status_rotulo: str
    concluida: bool
    exige_terceira: bool
    consenso: Decimal | None = None

    @property
    def situacao(self) -> str:
        return _status_excel_linha(self)

    @property
    def situacao_rotulo(self) -> str:
        """Coincidente mostra a quantidade correta (consenso); senão o estado."""
        if self.situacao == 'coincidente' and self.consenso is not None:
            return formatar_quantidade(self.consenso)
        return STATUS_EXCEL[self.situacao]

    @property
    def situacao_valor(self):
        """Para o Excel: número quando coincidente, texto nos demais casos."""
        if self.situacao == 'coincidente' and self.consenso is not None:
            return self.consenso
        return STATUS_EXCEL[self.situacao]

    @property
    def contagens(self) -> dict:
        """Somente células com registro (compatível com filtros e histórico)."""
        return {e: (c if c['estado'] == 'valor' else None) for e, c in self.celulas.items()}


def montar_linhas(lancamentos: list[Lancamento], associadas: frozenset, liberadas: set) -> list[LinhaAuditor]:
    grupos: dict[tuple[int, str], list[Lancamento]] = {}
    for l in lancamentos:
        grupos.setdefault((l.posicao_id, l.sku), []).append(l)
    linhas = []
    for (posicao_id, sku), itens in grupos.items():
        por_etapa = {l.etapa: l for l in itens}
        quantidades = {e: (por_etapa[e].quantidade if e in por_etapa else None) for e in ETAPAS}
        status, classificacao = _status_sku(quantidades, associadas)
        celulas = {}
        for e in ETAPAS:
            if e not in associadas:
                celulas[e] = _celula('nao_associada')
            elif e not in liberadas:
                celulas[e] = _celula('nao_liberada')
            elif e in por_etapa:
                celulas[e] = _celula('valor', por_etapa[e], _tom_valor(classificacao, e))
            elif e == 3 and {1, 2} <= liberadas and status == 'C1_IGUAL_C2':
                celulas[e] = _celula('nao_necessaria')  # regra existente: C1 = C2 dispensa C3
            else:
                celulas[e] = _celula('nao_contado')
        primeiro = itens[0]
        linhas.append(LinhaAuditor(
            chave=f'{posicao_id}-{sku}',
            posicao_id=posicao_id,
            posicao=primeiro.posicao,
            sku=sku,
            descricao=primeiro.descricao,
            celulas=celulas,
            status=status,
            status_rotulo=STATUS_SKU[status],
            concluida=classificacao.status == StatusContagem.OK,
            exige_terceira=classificacao.necessita_c3,
            consenso=classificacao.valor_consenso if classificacao.status == StatusContagem.OK else None,
        ))
    linhas.sort(key=lambda l: (l.posicao, l.sku))
    return linhas


def montar_skus(linhas: list[LinhaAuditor]) -> list[dict]:
    """Por SKU: soma de cada contagem nas posições do escopo (reconcilia com a visão por posição)."""
    grupos: dict[str, list[LinhaAuditor]] = {}
    for linha in linhas:
        grupos.setdefault(linha.sku, []).append(linha)
    skus = []
    for sku, itens in grupos.items():
        celulas = {}
        for e in ETAPAS:
            estados = [l.celulas[e] for l in itens]
            valores = [c['quantidade'] for c in estados if c['estado'] == 'valor']
            if valores:
                total = sum(valores, Decimal(0))
                tons = [c['tom'] for c in estados if c['estado'] == 'valor']
                divergentes = sum(1 for t in tons if t in ('divergente', 'difere'))
                if divergentes:
                    tom = 'divergente'
                elif all(t == 'confere' for t in tons) and len(valores) == len(itens):
                    tom = 'confere'
                else:
                    tom = 'registrado'
                celulas[e] = {
                    'estado': 'valor', 'quantidade': total, 'texto': formatar_quantidade(total),
                    'posicoes': len(valores), 'parcial': len(valores) < len(itens),
                    'divergentes': divergentes, **_tom(tom),
                }
                if divergentes:
                    celulas[e]['tom_rotulo'] = f'Divergência em {divergentes} de {len(itens)} posição(ões)'
                elif tom == 'registrado' and len(valores) < len(itens):
                    celulas[e]['tom_rotulo'] = f'Contado em {len(valores)} de {len(itens)} posição(ões)'
            else:
                tipos = {c['estado'] for c in estados}
                estado = tipos.pop() if len(tipos) == 1 else 'nao_contado'
                celulas[e] = {'estado': estado, 'texto': ROTULOS_CELULA[estado], 'quantidade': None, **_tom(estado)}
        situacao = _status_excel_sku(itens)
        # Coincidente em todas as posições: a quantidade correta do SKU é a soma dos consensos.
        consenso = sum((l.consenso for l in itens), Decimal(0)) if situacao == 'coincidente' else None
        skus.append({
            'sku': sku, 'chave': sku, 'descricao': itens[0].descricao,
            'posicoes': len(itens), 'celulas': celulas,
            'situacao': situacao, 'consenso': consenso,
            'situacao_rotulo': formatar_quantidade(consenso) if consenso is not None else STATUS_EXCEL[situacao],
            'situacao_valor': consenso if consenso is not None else STATUS_EXCEL[situacao],
        })
    skus.sort(key=lambda s: s['sku'])
    return skus


# ---------------------------------------------------------------------------
# Filtros
# ---------------------------------------------------------------------------


@dataclass
class FiltrosAuditor:
    etapa: int | None = None
    posicao: str = ''
    termo: str = ''
    operador: str = ''
    status: str = ''
    data: str = ''

    @classmethod
    def de_parametros(cls, params) -> 'FiltrosAuditor':
        etapa = params.get('etapa') or ''
        status = (params.get('status') or '').strip().upper()
        data = (params.get('data') or '').strip()
        try:
            datetime.strptime(data, '%Y-%m-%d')
        except ValueError:
            data = ''
        return cls(
            etapa=int(etapa) if etapa in ('1', '2', '3') else None,
            posicao=(params.get('posicao') or '').strip()[:60],
            termo=(params.get('q') or '').strip()[:80],
            operador=(params.get('operador') or '').strip()[:80],
            status=status if status in STATUS_SKU else '',
            data=data,
        )

    @property
    def ativos(self) -> bool:
        return any([self.etapa, self.posicao, self.termo, self.operador, self.status, self.data])

    def descricao(self) -> str:
        partes = []
        if self.etapa:
            partes.append(f'Contagem C{self.etapa}')
        if self.posicao:
            partes.append(f'Posição: {self.posicao}')
        if self.termo:
            partes.append(f'SKU/descrição: {self.termo}')
        if self.operador:
            partes.append(f'Operador: {self.operador}')
        if self.status:
            partes.append(f'Situação: {STATUS_SKU[self.status]}')
        if self.data:
            partes.append(f'Disponibilizado em: {datetime.strptime(self.data, "%Y-%m-%d"):%d/%m/%Y}')
        return ' · '.join(partes) or 'Sem filtros'

    def aplicar(self, linhas: list[LinhaAuditor]) -> list[LinhaAuditor]:
        resultado = []
        for linha in linhas:
            contagens = [c for e, c in linha.contagens.items() if c and (not self.etapa or e == self.etapa)]
            if self.etapa and not contagens:
                continue
            if self.posicao and self.posicao.lower() not in linha.posicao.lower():
                continue
            if self.termo and self.termo.lower() not in f'{linha.sku} {linha.descricao} {linha.posicao}'.lower():
                continue
            if self.operador and not any(self.operador.lower() in c['operador'].lower() for c in contagens):
                continue
            if self.status and linha.status != self.status:
                continue
            if self.data and not any(
                c['disponibilizado_em'] and _local(c['disponibilizado_em']).strftime('%Y-%m-%d') == self.data
                for c in contagens
            ):
                continue
            resultado.append(linha)
        return resultado


# ---------------------------------------------------------------------------
# Painel
# ---------------------------------------------------------------------------


@dataclass
class PainelAuditor:
    comparativo: ComparativoContagem
    gerado_em: datetime
    lancamentos: list[Lancamento]
    linhas: list[LinhaAuditor]
    linhas_filtradas: list[LinhaAuditor]
    skus: list[dict]
    indicador: dict
    cards: dict
    contagens: list[dict]
    escopo: dict
    filtros: FiltrosAuditor
    liberacoes: dict = field(default_factory=dict)
    horario_real: bool = False
    graficos: dict = field(default_factory=dict)
    cabecalhos: dict = field(default_factory=dict)
    versao: str = ''
    data_oficial: datetime | None = None

    @property
    def liberadas(self) -> dict:
        return {e: l.liberado_em for e, l in self.liberacoes.items()}

    @property
    def programada(self) -> bool:
        return any(l.modo == Modo.PROGRAMADA for l in self.liberacoes.values())


def _contagens_status(associadas: frozenset, liberacoes: dict, visiveis: Counter) -> list[dict]:
    """Situação de cada contagem para o auditor (sem revelar dados de privadas)."""
    resultado = []
    for e in ETAPAS:
        info = {'etapa': e, 'rotulo': f'C{e}', 'visiveis': visiveis.get(e, 0)}
        liberacao = liberacoes.get(e)
        if e not in associadas:
            info.update(estado='nao_associada', estado_rotulo='Não associada')
        elif liberacao is None:
            info.update(estado='nao_liberada', estado_rotulo='Não liberada')
        elif liberacao.modo == Modo.PROGRAMADA:
            info.update(
                estado='programada', estado_rotulo=f'Liberação programada · {liberacao.get_programacao_display().lower()}',
                programacao=liberacao.programacao, liberados=liberacao.total_publicado,
                programados=liberacao.total_programado, percentual=liberacao.percentual,
                inicio=liberacao.inicio_real or liberacao.inicio_previsto,
                termino=liberacao.termino_real or liberacao.termino_previsto,
                liberado_em=liberacao.liberado_em,
            )
        else:
            info.update(estado='tempo_real', estado_rotulo='Tempo real', liberado_em=liberacao.liberado_em)
        resultado.append(info)
    return resultado


# ---------------------------------------------------------------------------
# Participação por contagem (C1, C2 e C3 lado a lado)
# ---------------------------------------------------------------------------


def _num(valor: float) -> str:
    return f'{valor:.2f}'.rstrip('0').rstrip('.')


def _contadores(regs: list[Lancamento]) -> list[dict]:
    itens, quantidades, correcoes = Counter(), {}, Counter()
    for l in regs:
        itens[l.operador] += 1
        quantidades[l.operador] = quantidades.get(l.operador, Decimal(0)) + l.quantidade
        if l.corrigido and l.corrigido_por:
            correcoes[l.corrigido_por] += 1
    maximo = max(list(itens.values()) + list(correcoes.values()) + [1])
    nomes = sorted(set(itens) | set(correcoes), key=str.lower)  # ordem alfabética: sem ranking
    return [{
        'nome': nome,
        'itens': itens.get(nome, 0),
        'quantidade_texto': formatar_quantidade(quantidades.get(nome, Decimal(0))),
        'correcoes': correcoes.get(nome, 0),
        'largura': _num(100 * itens.get(nome, 0) / maximo),
        'largura_correcoes': _num(100 * correcoes.get(nome, 0) / maximo),
    } for nome in nomes]


def montar_graficos(lancamentos: list[Lancamento], etapas: list[int]) -> dict:
    """Por contagem liberada: itens contados, posições e participação dos contadores."""
    por_etapa = {}
    for etapa in etapas:
        regs = [l for l in lancamentos if l.etapa == etapa]
        por_etapa[etapa] = {
            'etapa': etapa,
            'rotulo': f'C{etapa}',
            'total': len(regs),
            'posicoes': len({l.posicao_id for l in regs}),
            'contadores': _contadores(regs),
        }
    return {'por_etapa': por_etapa}


def _cabecalhos(associadas: frozenset, liberacoes: dict) -> dict[int, dict]:
    """Cabeçalho de cada contagem com a data real da autorização de publicação."""
    cabecalhos = {}
    for e in ETAPAS:
        liberacao = liberacoes.get(e)
        if e not in associadas:
            detalhe = 'Não associada'
        elif liberacao is None:
            detalhe = 'Não liberada'
        elif liberacao.liberado_em is None:
            detalhe = 'Data de liberação não registrada'
        else:
            detalhe = f'Liberada em {_local(liberacao.liberado_em):%d/%m/%Y} às {_local(liberacao.liberado_em):%H:%M}'
        cabecalhos[e] = {'rotulo': f'Contagem {e}', 'detalhe': detalhe, 'liberada': liberacao is not None}
    return cabecalhos


def montar_painel(comparativo: ComparativoContagem, filtros: FiltrosAuditor | None = None) -> PainelAuditor:
    filtros = filtros or FiltrosAuditor()
    associadas = frozenset(comparativo.associacoes.values_list('ordem', flat=True))
    liberacoes = liberacoes_visiveis(comparativo)
    liberadas = set(liberacoes)
    publicacao = getattr(comparativo, 'publicacao_auditoria', None)
    horario_real = bool(publicacao and publicacao.mostrar_horario_real)
    lancamentos = carregar_lancamentos(comparativo, liberacoes, horario_real)
    linhas = montar_linhas(lancamentos, associadas, liberadas)
    filtradas = filtros.aplicar(linhas)

    compara = len(liberadas) >= 2
    concluidas = sum(1 for l in linhas if l.concluida)
    indicador = {
        'titulo': 'Itens com consenso',
        'subtitulo': 'Combinações posição + SKU em que duas contagens liberadas coincidem',
        'numerador': concluidas,
        'denominador': len(linhas),
        'percentual': _pct(concluidas, len(linhas)) if compara else None,
        'suficiente': compara and bool(linhas),
        'motivo': (
            'Sem dados suficientes: é preciso ao menos duas contagens liberadas.' if not compara
            else 'Sem dados suficientes: nenhum registro disponibilizado.' if not linhas else ''
        ),
    }
    cards = {
        'posicoes': len({l.posicao_id for l in linhas}),
        'skus': len({l.sku for l in linhas}),
        'itens': len(linhas),
        'exigem_terceira': sum(1 for l in linhas if l.exige_terceira) if {1, 2} <= liberadas else None,
        # Itens (posição + SKU) com contagens liberadas divergentes e sem consenso.
        'divergentes': sum(
            1 for l in linhas if l.status in ('C1_DIFERENTE_C2', 'AGUARDANDO_C3', 'REVISAO')
        ) if compara else None,
    }
    privadas = sorted(set(associadas) - liberadas)
    escopo = {
        'etapas': sorted(liberadas),
        'associadas': sorted(associadas),
        'rotulo': _rotulo_etapas(liberadas),
        'privadas': privadas,
        'rotulo_privadas': _rotulo_etapas(privadas) if privadas else '',
        'completo': not privadas,
        'compara': compara,
    }
    visiveis = Counter(l.etapa for l in lancamentos)
    c1 = liberacoes.get(1)
    data_oficial = comparativo.data_criacao
    if c1 is not None:
        associacao = comparativo.associacoes.filter(ordem=1).select_related('inventario').first()
        data_oficial = associacao.inventario.data_criacao if associacao else data_oficial
    estado_liberacoes = sorted(
        (e, l.modo, l.ciclo, l.programacao, l.total_publicado, l.total_programado, str(l.liberado_em))
        for e, l in liberacoes.items()
    )
    return PainelAuditor(
        comparativo=comparativo,
        gerado_em=timezone.now(),
        lancamentos=lancamentos,
        linhas=linhas,
        linhas_filtradas=filtradas,
        skus=montar_skus(filtradas),
        indicador=indicador,
        cards=cards,
        contagens=_contagens_status(associadas, liberacoes, visiveis),
        escopo=escopo,
        filtros=filtros,
        liberacoes=liberacoes,
        horario_real=horario_real,
        cabecalhos=_cabecalhos(associadas, liberacoes),
        graficos=montar_graficos(lancamentos, sorted(liberadas)),
        versao=hashlib.sha1((
            f'{estado_liberacoes}|{horario_real}|'
            f'{sorted((l.id, l.corrigido_por, str(l.corrigido_em)) for l in lancamentos)}|'
            f'{sorted((l.id, l.etapa, str(l.quantidade), l.corrigido, l.operador, str(l.disponibilizado_em)) for l in lancamentos)}'
        ).encode('utf-8')).hexdigest()[:16],
        data_oficial=data_oficial,
    )


# ---------------------------------------------------------------------------
# Histórico de atividade (somente contagens liberadas)
# ---------------------------------------------------------------------------

ROTULOS_EVENTO_PROGRAMACAO = {
    EventoAuditoria.Tipo.INICIADA: 'liberação programada iniciada',
    EventoAuditoria.Tipo.PAUSADA: 'liberação programada pausada',
    EventoAuditoria.Tipo.RETOMADA: 'liberação programada retomada',
    EventoAuditoria.Tipo.CONCLUIDA: 'liberação programada concluída',
    EventoAuditoria.Tipo.CANCELADA: 'liberações restantes canceladas',
}


def montar_historico(painel: PainelAuditor, mostrar_valores_correcao: bool = False) -> list[dict]:
    """Linha do tempo imutável. Auditor não vê antes/depois de correções nem captura."""
    eventos = []
    for l in painel.lancamentos:
        eventos.append({
            'data_hora': l.disponibilizado_em, 'tipo': 'contagem',
            'titulo': f'C{l.etapa} disponibilizada',
            'detalhe': f'{l.posicao} · {l.sku} · {formatar_quantidade(l.quantidade)}',
            'usuario': l.operador, 'nota': '',
        })
    disponivel = {l.id: l.disponibilizado_em for l in painel.lancamentos}
    if disponivel:
        for ev in InventarioAuditoriaEvento.objects.filter(
            evento=InventarioAuditoriaEvento.Evento.CONTAGEM,
            inventario_id__in=[l.inventario_id_liberado for l in painel.liberacoes.values()],
            dados_extras__acao='CORRECAO',
        ).select_related('usuario', 'usuario__perfil_operacional').order_by('data_hora'):
            item_id = ev.dados_extras.get('item_id')
            if item_id not in disponivel:
                continue
            detalhe = 'Quantidade corrigida com autorização'
            if mostrar_valores_correcao:
                detalhe += f" · antes {ev.dados_extras.get('quantidade_anterior')} · depois {ev.quantidade}"
            momento, nota = ev.data_hora, ''
            if disponivel[item_id] and ev.data_hora < disponivel[item_id]:
                # Correção anterior à disponibilização: listada nela, sem antecipar horários.
                momento, nota = disponivel[item_id], 'correção registrada antes da disponibilização'
            eventos.append({
                'data_hora': momento, 'tipo': 'correcao', 'titulo': 'Correção autorizada',
                'detalhe': detalhe, 'usuario': _nome_usuario(ev.usuario) or NAO_REGISTRADO, 'nota': nota,
            })
    liberacoes = painel.liberacoes
    for ev in painel.comparativo.eventos_auditor.filter(
        tipo__in=[EventoAuditoria.Tipo.CONTAGEM_LIBERADA, *ROTULOS_EVENTO_PROGRAMACAO],
    ).select_related('usuario', 'usuario__perfil_operacional'):
        etapa = ev.dados.get('etapa')
        liberacao = liberacoes.get(etapa)
        # Somente eventos do ciclo atual das contagens liberadas.
        if liberacao is None or ev.dados.get('ciclo', liberacao.ciclo) != liberacao.ciclo:
            continue
        if ev.tipo == EventoAuditoria.Tipo.CONTAGEM_LIBERADA:
            if ev.data_hora > liberacao.liberado_em:
                continue
            titulo = f'C{etapa} liberada para acompanhamento'
        else:
            titulo = f'C{etapa} · {ROTULOS_EVENTO_PROGRAMACAO[ev.tipo]}'
        eventos.append({
            'data_hora': ev.data_hora, 'tipo': 'publicacao', 'titulo': titulo, 'detalhe': '',
            'usuario': _nome_usuario(ev.usuario) or 'Processo automático', 'nota': '',
        })
    minimo = timezone.make_aware(datetime(1970, 1, 1))
    eventos.sort(key=lambda e: e['data_hora'] or minimo, reverse=True)
    return eventos


# ---------------------------------------------------------------------------
# Serialização (API) e exportação
# ---------------------------------------------------------------------------


def _iso(valor):
    return _local(valor).isoformat() if valor else None


def _serializar_celula(c: dict) -> dict:
    dados = {'estado': c['estado'], 'texto': c['texto'], 'tom': c['tom'], 'tom_rotulo': c['tom_rotulo']}
    if c['estado'] == 'valor':
        dados['quantidade'] = str(c['quantidade'])
        if 'operador' in c:
            dados['operador'] = c['operador']
            dados['disponibilizado_em'] = _iso(c['disponibilizado_em'])
            if c.get('registrado_em'):
                dados['registrado_em'] = _iso(c['registrado_em'])
            if c.get('corrigido'):
                dados['corrigido_por'] = c['corrigido_por']
                if c.get('corrigido_em'):
                    dados['corrigido_em'] = _iso(c['corrigido_em'])
        if 'posicoes' in c:
            dados['posicoes'] = c['posicoes']
    return dados


def serializar_linha(linha: LinhaAuditor) -> dict:
    return {
        'chave': linha.chave,
        'posicao': linha.posicao,
        'sku': linha.sku,
        'descricao': linha.descricao,
        'contagens': {f'C{e}': _serializar_celula(c) for e, c in linha.celulas.items()},
        'status': linha.status,
        'status_rotulo': linha.status_rotulo,
    }


def serializar_painel(painel: PainelAuditor, pagina: list[LinhaAuditor]) -> dict:
    indicador = dict(painel.indicador)
    indicador['percentual'] = str(indicador['percentual']) if indicador['percentual'] is not None else None
    contagens = []
    for c in painel.contagens:
        item = {k: c[k] for k in ('rotulo', 'estado', 'estado_rotulo', 'visiveis')}
        if c['estado'] == 'programada':
            item.update(liberados=c['liberados'], programados=c['programados'],
                        inicio=_iso(c['inicio']), termino=_iso(c['termino']))
        contagens.append(item)
    return {
        'conferencia': {'id': painel.comparativo.pk, 'nome': painel.comparativo.nome},
        'escopo': {'contagens_liberadas': [f'C{e}' for e in painel.escopo['etapas']]},
        'contagens': contagens,
        'indicador': indicador,
        'cards': painel.cards,
        'linhas': [serializar_linha(l) for l in pagina],
        'skus': [
            {'sku': s['sku'], 'descricao': s['descricao'],
             'contagens': {f'C{e}': _serializar_celula(c) for e, c in s['celulas'].items()}}
            for s in painel.skus
        ],
        'participacao': [{
            'contagem': g['rotulo'],
            'registros': g['total'],
            'posicoes': g['posicoes'],
            'contadores': [{k: c[k] for k in ('nome', 'itens', 'correcoes')} for c in g['contadores']],
        } for g in painel.graficos.get('por_etapa', {}).values()],
        'total_linhas': len(painel.linhas_filtradas),
        'gerado_em': _iso(painel.gerado_em),
    }


# Sem vírgula no formato: o Excel em pt-BR transformaria ',' e '.' em separadores.
FORMATO_INTEIRO = '0'
FORMATO_DECIMAL = '0"."###'
FORMATO_DATA = 'dd/mm/yyyy hh:mm'


def _escrever(aba, linha, coluna, valor):
    celula = aba.cell(linha, coluna)
    if isinstance(valor, datetime):
        celula.value = _local(valor).replace(tzinfo=None) if timezone.is_aware(valor) else valor
        celula.number_format = FORMATO_DATA
    elif isinstance(valor, (Decimal, int)) and not isinstance(valor, bool):
        numero = Decimal(valor)
        celula.value = numero
        celula.number_format = FORMATO_INTEIRO if numero == numero.to_integral_value() else FORMATO_DECIMAL
    else:
        celula.value = '' if valor is None else str(valor)
        celula.data_type = 's'
    return celula


def _valor_celula(c: dict):
    return c['quantidade'] if c['estado'] == 'valor' else c['texto']


# Status da linha no Excel (mesma regra de concordância da tela).
STATUS_EXCEL = {'coincidente': 'Coincidente', 'divergente': 'Divergente', 'sem_comparacao': 'Sem comparação'}
LINHAS_DIVERGENTES = ('C1_DIFERENTE_C2', 'AGUARDANDO_C3', 'REVISAO')
# Cores de fundo/texto por tom (verde = concordância, laranja = divergência, azul = sem comparação).
CORES_EXCEL = {
    'confere': ('E9F7EE', '166534'), 'coincidente': ('E9F7EE', '166534'),
    'divergente': ('FFF1E8', '9A3412'), 'difere': ('FFF1E8', '9A3412'),
    'registrado': ('E8EFFD', '1E3A8A'), 'sem_comparacao': ('E8EFFD', '1E3A8A'),
}


def _status_excel_linha(linha: LinhaAuditor) -> str:
    if linha.status in LINHAS_DIVERGENTES:
        return 'divergente'
    if linha.concluida:
        return 'coincidente'
    return 'sem_comparacao'


def _status_excel_sku(linhas: list[LinhaAuditor]) -> str:
    """Divergente se qualquer posição diverge (totais iguais não escondem diferenças)."""
    estados = [_status_excel_linha(l) for l in linhas]
    if 'divergente' in estados:
        return 'divergente'
    if estados and all(e == 'coincidente' for e in estados):
        return 'coincidente'
    return 'sem_comparacao'


def _aba(pasta, titulo, cabecalho, linhas, tons=None):
    """Planilha com cabeçalho fixo e filtro; `tons[i][j]` colore a célula (linha i, coluna j)."""
    from openpyxl.styles import Alignment, Font, PatternFill

    aba = pasta.create_sheet(titulo)
    for j, texto in enumerate(cabecalho, start=1):
        celula = aba.cell(1, j, texto)
        celula.font = Font(bold=True)
        celula.fill = PatternFill('solid', fgColor='E7EEF5')
    for i, valores in enumerate(linhas, start=2):
        for j, valor in enumerate(valores, start=1):
            celula = _escrever(aba, i, j, valor)
            if cabecalho[j - 1].startswith('Contagem') and cabecalho[j - 1] != 'Contagem':
                celula.alignment = Alignment(horizontal='right')
            tom = (tons[i - 2].get(j) if tons else None)
            if tom in CORES_EXCEL:
                fundo, texto = CORES_EXCEL[tom]
                celula.fill = PatternFill('solid', fgColor=fundo)
                celula.font = Font(color=texto, bold=cabecalho[j - 1] == 'Status')
    aba.freeze_panes = 'A2'
    aba.auto_filter.ref = f'A1:{aba.cell(1, len(cabecalho)).column_letter}{max(aba.max_row, 1)}'
    for j, texto in enumerate(cabecalho, start=1):
        aba.column_dimensions[aba.cell(1, j).column_letter].width = 40 if texto == 'Descrição' else 18
    return aba


VISOES_EXPORTACAO = {'posicao': 'Por posição', 'sku': 'Por SKU'}


def exportar_xlsx(painel: PainelAuditor, usuario, visao: str = 'posicao') -> bytes:
    """Relatório (visão selecionada primeiro, a outra visão e os lançamentos).

    Só contagens liberadas geram colunas e dados; cada linha traz o Status
    (Coincidente, Divergente ou Sem comparação) e as contagens são coloridas.
    """
    from openpyxl import Workbook

    visao = visao if visao in VISOES_EXPORTACAO else 'posicao'
    etapas = painel.escopo['etapas']
    pasta = Workbook()
    pasta.remove(pasta.active)
    contagens = [f'Contagem {e}' for e in etapas]
    linhas_por_sku: dict[str, list[LinhaAuditor]] = {}
    for linha in painel.linhas_filtradas:
        linhas_por_sku.setdefault(linha.sku, []).append(linha)

    def aba_sku():
        linhas, tons = [], []
        for s in painel.skus:
            status = _status_excel_sku(linhas_por_sku.get(s['sku'], []))
            linhas.append([s['sku'], s['descricao'], *(_valor_celula(s['celulas'][e]) for e in etapas), s['situacao_valor']])
            tons.append({**{3 + k: s['celulas'][e].get('tom') for k, e in enumerate(etapas)}, 3 + len(etapas): status})
        _aba(pasta, 'Por SKU', ['SKU', 'Descrição', *contagens, 'Status'], linhas, tons)

    def aba_posicao():
        linhas, tons = [], []
        for l in painel.linhas_filtradas:
            status = _status_excel_linha(l)
            linhas.append([l.posicao, l.sku, l.descricao, *(_valor_celula(l.celulas[e]) for e in etapas), l.situacao_valor])
            tons.append({**{4 + k: l.celulas[e].get('tom') for k, e in enumerate(etapas)}, 4 + len(etapas): status})
        _aba(pasta, 'Por posição', ['Posição', 'SKU', 'Descrição', *contagens, 'Status'], linhas, tons)

    for montar in ((aba_sku, aba_posicao) if visao == 'sku' else (aba_posicao, aba_sku)):
        montar()
    cabecalho = ['Posição', 'SKU', 'Descrição', 'Contagem', 'Quantidade', 'Status', 'Responsável',
                 'Liberação da contagem', 'Disponibilizado em', 'Correção']
    registros, tons = [], []
    for l in painel.linhas_filtradas:
        for e in etapas:
            c = l.celulas[e]
            if c['estado'] != 'valor':
                continue
            status = {'confere': 'coincidente', 'divergente': 'divergente', 'difere': 'divergente'}.get(c['tom'], 'sem_comparacao')
            correcao = ''
            if c['corrigido']:
                correcao = f"Corrigida por {c['corrigido_por'] or 'Não registrado'}"
                if c.get('corrigido_em'):
                    correcao += f" em {_fmt(c['corrigido_em'])}"
            registros.append([
                l.posicao, l.sku, l.descricao, f'C{e}', c['quantidade'], STATUS_EXCEL[status], c['operador'],
                painel.cabecalhos[e]['detalhe'], c['disponibilizado_em'], correcao,
            ])
            tons.append({5: status, 6: status})
    _aba(pasta, 'Lançamentos', cabecalho, registros, tons)
    buffer = io.BytesIO()
    pasta.save(buffer)
    return buffer.getvalue()
