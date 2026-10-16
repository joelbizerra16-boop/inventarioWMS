"""Comparativo de contagens (C1, C2 e C3 opcional) e confronto com a fotografia SAP.

Regras centrais (compartilhadas entre a tela e o Excel):

- C1, C2 e C3 são medições independentes do mesmo estoque: nunca são somadas
  entre si. Dentro de uma mesma contagem, as posições de um SKU são somadas.
- Duas contagens iguais bastam para a contagem ficar OK; a terceira, se
  existir e divergir, é preservada e destacada sem retirar o OK.
- O Status SAP compara as contagens registradas com o saldo do canal
  selecionado (COSAN = canal 110, BRIDA = canal 1, Ambas = soma dos dois).
- Ausência de registro nunca vira zero; zero registrado é zero.
- Nenhuma classificação ajusta estoque ou altera contagens.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from itertools import combinations

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Count, Max, Sum
from django.utils import timezone

from estoque_sap.models import EstoqueSAP
from inventario.models import Inventario, InventarioItem
from inventario.models_operacional import InventarioTarefa
from inventario.models_comparativo import (
    ComparativoAcuracidade,
    ComparativoContagem,
    ComparativoContagemEvento,
    ComparativoContagemInventario,
    ReferenciaSAP,
    ReferenciaSAPItem,
)
from produtos.models import Produto

ORDENS = (1, 2, 3)
NAO_REGISTRADO = 'Não registrado'
DESCRICAO_NAO_CADASTRADA = 'Descrição não cadastrada'

# Canais SAP do comparativo: chave interna -> campo da importação.
CAMPO_CANAL = {'cosan': 'canal_110', 'brida': 'canal_1'}
NOME_CANAL = {'cosan': 'COSAN', 'brida': 'BRIDA'}
FUSO_ROTULO = 'Horário de Brasília (America/Sao_Paulo)'
PRAZO_REABERTURA = timedelta(days=7)


class ComparativoError(Exception):
    pass


class Canal:
    COSAN = 'cosan'
    BRIDA = 'brida'
    AMBAS = 'ambas'
    DEFAULT = AMBAS

    ROTULOS = {COSAN: 'COSAN', BRIDA: 'BRIDA', AMBAS: 'Ambas'}
    DESCRICOES = {
        COSAN: 'COSAN · Canal 110',
        BRIDA: 'BRIDA · Canal 1',
        AMBAS: 'Ambas · Canal 110 + Canal 1',
    }

    @classmethod
    def normalizar(cls, valor) -> str:
        valor = (valor or '').strip().lower()
        return valor if valor in cls.ROTULOS else cls.DEFAULT

    @classmethod
    def colunas_sap(cls, canal: str) -> list[tuple[str, str]]:
        """Colunas SAP na ordem obrigatória, logo após SKU / descrição."""
        if canal == cls.COSAN:
            return [('cosan', 'COSAN')]
        if canal == cls.BRIDA:
            return [('brida', 'BRIDA')]
        return [('cosan', 'COSAN'), ('brida', 'BRIDA'), ('total', 'Total SAP')]

    @classmethod
    def chave_saldo(cls, canal: str) -> str:
        return {cls.COSAN: 'cosan', cls.BRIDA: 'brida'}.get(canal, 'total')


class Visao:
    SKU = 'sku'
    POSICAO = 'posicao'
    DEFAULT = SKU

    @classmethod
    def normalizar(cls, valor) -> str:
        valor = (valor or '').strip().lower()
        return valor if valor in (cls.SKU, cls.POSICAO) else cls.DEFAULT


class StatusContagem:
    OK = 'OK'
    DIVERGENTE = 'DIVERGENTE'
    AGUARDANDO = 'AGUARDANDO'

    ROTULOS = {
        OK: 'Contagem OK',
        DIVERGENTE: 'Contagens divergentes',
        AGUARDANDO: 'Aguardando contagem',
    }


class StatusSAP:
    OK = 'OK'
    PARCIAL = 'PARCIAL'
    DIVERGENTE = 'DIVERGENTE'
    NEUTRO = 'NEUTRO'

    ROTULOS = {
        OK: 'SAP OK',
        PARCIAL: 'SAP coincide com uma contagem',
        DIVERGENTE: 'SAP não coincide',
        NEUTRO: 'Sem comparação SAP',
    }


# Estados visuais das células de quantidade.
CELULA_OK = 'ok'
CELULA_DIVERGENTE = 'divergente'
CELULA_NEUTRA = 'neutra'
CELULA_AUSENTE = 'ausente'


def rotulo_ordem(ordem: int) -> str:
    return f'C{ordem}'


def formatar_quantidade(valor: Decimal | None) -> str:
    """Formato pt-BR sem zeros supérfluos; ausência vira '—', nunca '0'."""
    if valor is None:
        return '—'
    valor = Decimal(valor)
    if valor == valor.to_integral_value():
        texto = f'{int(valor):,}'.replace(',', '.')
        return texto
    inteiro, _, fracao = f'{valor.normalize():f}'.partition('.')
    sinal = '-' if inteiro.startswith('-') else ''
    inteiro = inteiro.lstrip('-')
    inteiro = f'{int(inteiro):,}'.replace(',', '.')
    return f'{sinal}{inteiro},{fracao}'


def formatar_diferenca(valor: Decimal) -> str:
    if valor > 0:
        return f'+{formatar_quantidade(valor)}'
    if valor < 0:
        return f'−{formatar_quantidade(-valor)}'
    return '0'


def formatar_data_hora(valor: datetime | None) -> str:
    if valor is None:
        return NAO_REGISTRADO
    return timezone.localtime(valor).strftime('%d/%m/%Y %H:%M')


# ---------------------------------------------------------------------------
# Classificação
# ---------------------------------------------------------------------------


@dataclass
class ClassificacaoContagens:
    status: str
    rotulo: str
    dupla: str
    orientacao: str
    necessita_c3: bool
    diferencas: list[str]
    celulas: dict[int, str]
    pares_iguais: list[tuple[int, int]]
    valor_consenso: Decimal | None


def classificar_contagens(
    quantidades: dict[int, Decimal | None],
    ordens_associadas: set[int] | frozenset[int],
) -> ClassificacaoContagens:
    registradas = {o: quantidades.get(o) for o in ORDENS if quantidades.get(o) is not None}
    celulas = {
        o: (CELULA_NEUTRA if o in registradas else CELULA_AUSENTE)
        for o in ORDENS
    }
    pares = [
        (a, b) for a, b in combinations(sorted(registradas), 2)
        if registradas[a] == registradas[b]
    ]
    faltantes = [o for o in ORDENS if o not in registradas]

    if len(registradas) < 2:
        if not registradas:
            orientacao = 'Sem contagem registrada'
        elif 2 not in ordens_associadas and 2 in faltantes:
            orientacao = 'Associar C2'
        else:
            orientacao = f'Aguardando {rotulo_ordem(faltantes[0])}'
        return ClassificacaoContagens(
            status=StatusContagem.AGUARDANDO,
            rotulo=StatusContagem.ROTULOS[StatusContagem.AGUARDANDO],
            dupla='',
            orientacao=orientacao,
            necessita_c3=False,
            diferencas=[],
            celulas=celulas,
            pares_iguais=[],
            valor_consenso=None,
        )

    if pares:
        a, b = pares[0]
        consenso = registradas[a]
        diferencas = []
        if len(pares) == 3:
            dupla = 'Todas coincidem'
            orientacao = ''
        else:
            dupla = f'{rotulo_ordem(a)} = {rotulo_ordem(b)}'
            orientacao = ''
        for ordem, valor in registradas.items():
            if valor == consenso:
                celulas[ordem] = CELULA_OK
            else:
                celulas[ordem] = CELULA_DIVERGENTE
                orientacao = f'{rotulo_ordem(ordem)} divergente · valor preservado'
                diferencas.append(
                    f'{rotulo_ordem(ordem)} − {rotulo_ordem(a)}: '
                    f'{formatar_diferenca(valor - consenso)}'
                )
        if not orientacao and faltantes:
            orientacao = ' · '.join(f'{rotulo_ordem(o)} dispensada' for o in faltantes)
        return ClassificacaoContagens(
            status=StatusContagem.OK,
            rotulo=StatusContagem.ROTULOS[StatusContagem.OK],
            dupla=dupla,
            orientacao=orientacao,
            necessita_c3=False,
            diferencas=diferencas,
            celulas=celulas,
            pares_iguais=pares,
            valor_consenso=consenso,
        )

    for ordem in registradas:
        celulas[ordem] = CELULA_DIVERGENTE
    ordens = sorted(registradas)
    diferencas = [
        f'{rotulo_ordem(b)} − {rotulo_ordem(a)}: '
        f'{formatar_diferenca(registradas[b] - registradas[a])}'
        for a, b in combinations(ordens, 2)
    ]
    necessita_c3 = False
    if len(registradas) == 3:
        orientacao = 'Revisão necessária'
    else:
        faltante = faltantes[0]
        if faltante == 3:
            necessita_c3 = True
            orientacao = 'Aguardando C3' if 3 in ordens_associadas else 'Solicitar C3'
        else:
            orientacao = f'Aguardando {rotulo_ordem(faltante)}'
    return ClassificacaoContagens(
        status=StatusContagem.DIVERGENTE,
        rotulo=StatusContagem.ROTULOS[StatusContagem.DIVERGENTE],
        dupla='',
        orientacao=orientacao,
        necessita_c3=necessita_c3,
        diferencas=diferencas,
        celulas=celulas,
        pares_iguais=[],
        valor_consenso=None,
    )


@dataclass
class ClassificacaoSAP:
    status: str
    rotulo: str
    detalhe: str
    provisoria: bool
    coincidentes: list[int]


def classificar_sap(
    quantidades: dict[int, Decimal | None],
    saldo: Decimal | None,
    contagens: ClassificacaoContagens,
    motivo_neutro: str = '',
) -> ClassificacaoSAP:
    registradas = {o: quantidades.get(o) for o in ORDENS if quantidades.get(o) is not None}
    if motivo_neutro or saldo is None:
        return ClassificacaoSAP(
            status=StatusSAP.NEUTRO,
            rotulo=motivo_neutro or 'Sem referência SAP',
            detalhe='',
            provisoria=False,
            coincidentes=[],
        )
    if not registradas:
        return ClassificacaoSAP(
            status=StatusSAP.NEUTRO,
            rotulo='Sem contagens',
            detalhe='',
            provisoria=False,
            coincidentes=[],
        )

    coincidentes = [o for o, valor in registradas.items() if valor == saldo]
    provisoria = (
        contagens.status != StatusContagem.OK
        and len(registradas) < len(ORDENS)
    )
    if len(coincidentes) >= 2:
        status = StatusSAP.OK
        detalhe = ' e '.join(rotulo_ordem(o) for o in coincidentes) + ' = SAP'
        provisoria = False
    elif len(coincidentes) == 1:
        status = StatusSAP.PARCIAL
        detalhe = f'{rotulo_ordem(coincidentes[0])} = SAP'
    else:
        status = StatusSAP.DIVERGENTE
        if contagens.valor_consenso is not None:
            detalhe = (
                f'Dupla {contagens.dupla} − SAP: '
                f'{formatar_diferenca(contagens.valor_consenso - saldo)}'
            )
        else:
            detalhe = 'Nenhuma contagem igual ao SAP'
    return ClassificacaoSAP(
        status=status,
        rotulo=StatusSAP.ROTULOS[status],
        detalhe=detalhe,
        provisoria=provisoria,
        coincidentes=coincidentes,
    )


# ---------------------------------------------------------------------------
# Referência SAP (fotografia imutável)
# ---------------------------------------------------------------------------


def obter_ultima_importacao_sap() -> EstoqueSAP | None:
    return EstoqueSAP.objects.order_by('-data_importacao').first()


def _canais_da_importacao(ultima: EstoqueSAP) -> list[str] | None:
    """Campos de canal presentes na planilha da importação (None = não registrado)."""
    importacao = getattr(ultima, 'importacao', None)
    if importacao is None or importacao.canais_carregados is None:
        return None
    return list(importacao.canais_carregados)


def _canais_faltantes(canais: list[str] | None) -> list[str]:
    """Canais do comparativo ausentes de uma importação ([] se completa ou legado)."""
    if canais is None:
        return []
    return [chave for chave, campo in CAMPO_CANAL.items() if campo not in canais]


@transaction.atomic
def capturar_referencia_sap(usuario=None) -> ReferenciaSAP:
    """Fotografa os saldos dos canais 1 (BRIDA) e 110 (COSAN) da base SAP vigente.

    A importação SAP substitui a tabela inteira; por isso a conferência guarda
    a própria cópia. Se a importação vigente já foi fotografada, reutiliza.
    Todas as linhas são copiadas (inclusive de produtos inativos e sem saldo
    nesses canais): a política de inativos e o filtro por canal são aplicados
    na análise, que informa quantos SKUs ficaram de fora.
    """
    ultima = EstoqueSAP.objects.select_related('importacao').order_by('-data_importacao').first()
    if ultima is None:
        raise ComparativoError('Não há base SAP importada para fotografar.')

    existente = ReferenciaSAP.objects.filter(
        arquivo_origem=ultima.arquivo_origem,
        data_importacao_origem=ultima.data_importacao,
    ).first()
    if existente is not None:
        return existente

    registros = EstoqueSAP.objects.select_related('produto').order_by('produto_id', '-data_importacao')
    vistos: set[int] = set()
    itens: list[ReferenciaSAPItem] = []
    referencia = ReferenciaSAP.objects.create(
        descricao=(
            f'{ultima.arquivo_origem or "SAP"} · importado em '
            f'{formatar_data_hora(ultima.data_importacao)}'
        ),
        arquivo_origem=ultima.arquivo_origem,
        data_importacao_origem=ultima.data_importacao,
        usuario_captura=usuario if getattr(usuario, 'is_authenticated', False) else None,
        canais_carregados=_canais_da_importacao(ultima),
    )
    for registro in registros:
        if registro.produto_id in vistos:
            continue
        vistos.add(registro.produto_id)
        itens.append(ReferenciaSAPItem(
            referencia=referencia,
            produto_id=registro.produto_id,
            codigo_produto=registro.produto.codigo_produto,
            canal_1=registro.canal_1,
            canal_110=registro.canal_110,
        ))
    ReferenciaSAPItem.objects.bulk_create(itens, batch_size=1000)
    referencia.quantidade_skus = len(itens)
    referencia.save(update_fields=['quantidade_skus'])
    return referencia


def _referencia_completa(referencia: ReferenciaSAP | None) -> bool:
    return referencia is not None and not _canais_faltantes(referencia.canais_carregados)


def sincronizar_referencia_sap(comparativo: ComparativoContagem, usuario=None, motivo: str = 'automatica'):
    """Mantém a conferência aberta na importação SAP concluída mais recente.

    Retorna (alterou, aviso). A referência anterior é preservada (as
    fotografias não são apagadas) e a troca é registrada como evento.
    Importação sem COSAN ou BRIDA não substitui uma referência completa.
    Conferência finalizada mantém a referência histórica.
    """
    if comparativo.snapshot_resultado is not None:
        return False, ''
    ultima = EstoqueSAP.objects.select_related('importacao').order_by('-data_importacao').first()
    if ultima is None:
        return False, ''
    atual = comparativo.referencia_sap
    if (
        atual is not None
        and atual.arquivo_origem == ultima.arquivo_origem
        and atual.data_importacao_origem == ultima.data_importacao
    ):
        return False, ''
    faltantes = _canais_faltantes(_canais_da_importacao(ultima))
    if faltantes and _referencia_completa(atual):
        nomes = ' e '.join(NOME_CANAL[c] for c in faltantes)
        return False, (
            f'A importação SAP mais recente ({ultima.arquivo_origem}, '
            f'{formatar_data_hora(ultima.data_importacao)}) não trouxe {nomes}: '
            'a referência anterior completa foi mantida.'
        )
    with transaction.atomic():
        travado = ComparativoContagem.objects.select_for_update().get(pk=comparativo.pk)
        if travado.snapshot_resultado is not None:
            return False, ''
        nova = capturar_referencia_sap(usuario)
        if travado.referencia_sap_id == nova.pk:
            comparativo.referencia_sap = nova
            return False, ''
        anterior = travado.referencia_sap_id
        travado.referencia_sap = nova
        travado.save(update_fields=['referencia_sap', 'data_atualizacao'])
        _registrar_evento(
            travado, usuario, 'REFERENCIA_SAP_ATUALIZADA',
            antes=anterior, depois=nova.pk, motivo=motivo,
            arquivo=nova.arquivo_origem, canais=nova.canais_carregados,
        )
    comparativo.referencia_sap = nova
    return True, ''


def versao_dados(comparativo: ComparativoContagem) -> str:
    """Impressão leve das fontes: muda quando contagens, SAP ou associação mudam."""
    if comparativo.snapshot_resultado is not None:
        return f'final{comparativo.pk}x{comparativo.data_finalizacao:%Y%m%d%H%M%S%f}'
    inventarios = sorted(comparativo.associacoes.values_list('ordem', 'inventario_id'))
    agregado = InventarioItem.objects.filter(
        inventario_id__in=[inv for _, inv in inventarios],
    ).aggregate(
        n=Count('id'), maior=Max('id'), soma=Sum('quantidade_fisica'),
        contagem=Max('data_contagem'), alteracao=Max('data_alteracao'),
    )
    ultima = EstoqueSAP.objects.order_by('-data_importacao').values_list('data_importacao', flat=True).first()
    bruto = '|'.join(str(v) for v in (
        comparativo.referencia_sap_id, ultima, inventarios,
        agregado['n'], agregado['maior'], agregado['soma'], agregado['contagem'], agregado['alteracao'],
    ))
    return hashlib.sha1(bruto.encode('utf-8')).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Configuração da conferência
# ---------------------------------------------------------------------------

OPCAO_NOVA_REFERENCIA = 'nova'
OPCAO_SEM_REFERENCIA = ''


def _registrar_evento(comparativo, usuario, acao: str, **dados) -> None:
    ComparativoContagemEvento.objects.create(
        comparativo=comparativo,
        usuario=usuario if getattr(usuario, 'is_authenticated', False) else None,
        acao=acao,
        dados=dados,
    )


def validar_inventarios(inventarios: dict[int, Inventario | None]) -> None:
    if inventarios.get(1) is None:
        raise ComparativoError('Selecione o inventário da contagem C1.')
    if inventarios.get(3) is not None and inventarios.get(2) is None:
        raise ComparativoError('Associe a C2 antes de associar a C3.')
    usados: dict[int, int] = {}
    for ordem, inventario in inventarios.items():
        if inventario is None:
            continue
        if inventario.pk in usados:
            raise ComparativoError(
                f'O inventário #{inventario.pk} já ocupa a ordem '
                f'{rotulo_ordem(usados[inventario.pk])}; cada inventário só pode '
                'ocupar uma ordem.'
            )
        usados[inventario.pk] = ordem


@transaction.atomic
def salvar_comparativo(
    *,
    comparativo: ComparativoContagem | None,
    nome: str,
    inventarios: dict[int, Inventario | None],
    referencia_opcao: str,
    estado: str | None,
    usuario,
) -> ComparativoContagem:
    nome = (nome or '').strip()
    if not nome:
        raise ComparativoError('Informe o nome da conferência.')
    validar_inventarios(inventarios)

    novo = comparativo is None
    if novo:
        comparativo = ComparativoContagem(
            nome=nome,
            usuario_criacao=usuario if getattr(usuario, 'is_authenticated', False) else None,
        )
    else:
        comparativo = ComparativoContagem.objects.select_for_update().get(pk=comparativo.pk)
        if comparativo.snapshot_resultado is not None:
            raise ComparativoError('Conferência salva e finalizada não pode ser alterada.')

    referencia_anterior = comparativo.referencia_sap_id
    if referencia_opcao == OPCAO_NOVA_REFERENCIA:
        comparativo.referencia_sap = capturar_referencia_sap(usuario)
    elif referencia_opcao and str(referencia_opcao).isdigit():
        try:
            comparativo.referencia_sap = ReferenciaSAP.objects.get(pk=int(referencia_opcao))
        except ReferenciaSAP.DoesNotExist as exc:
            raise ComparativoError('Referência SAP não encontrada.') from exc
    elif novo:
        comparativo.referencia_sap = None

    nome_anterior = comparativo.nome
    comparativo.nome = nome
    if estado in ComparativoContagem.Estado.values:
        estado_anterior = comparativo.estado
        comparativo.estado = estado
    else:
        estado_anterior = comparativo.estado
    comparativo.save()

    if novo:
        _registrar_evento(comparativo, usuario, 'CRIACAO', nome=nome)
    else:
        if nome_anterior != nome:
            _registrar_evento(comparativo, usuario, 'NOME_ALTERADO', antes=nome_anterior, depois=nome)
        if estado_anterior != comparativo.estado:
            _registrar_evento(
                comparativo, usuario, 'ESTADO_ALTERADO',
                antes=estado_anterior, depois=comparativo.estado,
            )
    if referencia_anterior != comparativo.referencia_sap_id:
        _registrar_evento(
            comparativo, usuario, 'REFERENCIA_SAP_ALTERADA',
            antes=referencia_anterior, depois=comparativo.referencia_sap_id,
        )

    atuais = {a.ordem: a for a in comparativo.associacoes.all()} if not novo else {}
    alteradas = [
        ordem for ordem in ORDENS
        if (atuais[ordem].inventario_id if ordem in atuais else None)
        != (inventarios.get(ordem).pk if inventarios.get(ordem) else None)
    ]
    # Remove antes de recriar: permite trocar inventários de ordem sem violar
    # a unicidade (comparativo, inventário).
    for ordem in alteradas:
        if ordem in atuais:
            atuais[ordem].delete()
    for ordem in alteradas:
        inventario = inventarios.get(ordem)
        if inventario is not None:
            ComparativoContagemInventario.objects.create(
                comparativo=comparativo,
                inventario=inventario,
                ordem=ordem,
                usuario_associacao=usuario if getattr(usuario, 'is_authenticated', False) else None,
            )
        _registrar_evento(
            comparativo, usuario, 'ASSOCIACAO_ALTERADA',
            ordem=rotulo_ordem(ordem),
            antes=atuais[ordem].inventario_id if ordem in atuais else None,
            depois=inventario.pk if inventario is not None else None,
        )
    return comparativo


# ---------------------------------------------------------------------------
# Carga e consolidação
# ---------------------------------------------------------------------------


@dataclass
class Lancamento:
    id: int
    inventario_id: int
    ordem: int
    posicao_id: int
    codigo_posicao: str
    descricao_posicao: str
    produto_id: int
    quantidade: Decimal
    operador: str
    data_registro: datetime | None
    autor_alteracao: str
    data_alteracao: datetime | None
    origem: str

    @property
    def rotulo_ordem(self) -> str:
        return rotulo_ordem(self.ordem)

    @property
    def situacao(self) -> str:
        return 'Válido · corrigido' if self.data_alteracao else 'Válido'


@dataclass
class Medicao:
    """Uma contagem (C1, C2 ou C3) de uma linha do comparativo."""

    ordem: int
    associada: bool
    quantidade: Decimal | None = None
    operadores: list[str] = field(default_factory=list)
    primeira: datetime | None = None
    ultima: datetime | None = None
    sem_data: bool = False
    alterada: bool = False
    lancamentos: list[Lancamento] = field(default_factory=list)
    celula: str = CELULA_AUSENTE

    @property
    def registrada(self) -> bool:
        return self.quantidade is not None

    @property
    def intervalo(self) -> bool:
        return (
            self.primeira is not None
            and self.ultima is not None
            and self.primeira != self.ultima
        )

    @property
    def operadores_texto(self) -> str:
        return ', '.join(self.operadores) if self.operadores else NAO_REGISTRADO


def _medicao(ordem: int, associada: bool, lancamentos: list[Lancamento]) -> Medicao:
    medicao = Medicao(ordem=ordem, associada=associada)
    if not lancamentos:
        return medicao
    lancamentos = sorted(
        lancamentos,
        key=lambda l: (l.data_registro is None, l.data_registro or timezone.now(), l.id),
    )
    medicao.lancamentos = lancamentos
    medicao.quantidade = sum((l.quantidade for l in lancamentos), Decimal('0'))
    for lancamento in lancamentos:
        if lancamento.operador not in medicao.operadores:
            medicao.operadores.append(lancamento.operador)
        if lancamento.data_alteracao:
            medicao.alterada = True
    datas = [l.data_registro for l in lancamentos if l.data_registro is not None]
    medicao.sem_data = len(datas) < len(lancamentos)
    if datas:
        medicao.primeira = min(datas)
        medicao.ultima = max(datas)
    return medicao


@dataclass
class ContagemAssociada:
    ordem: int
    inventario: Inventario
    total_lancamentos: int

    @property
    def rotulo(self) -> str:
        return rotulo_ordem(self.ordem)

    @property
    def provisoria(self) -> bool:
        return self.inventario.status != Inventario.Status.FINALIZADO


@dataclass
class DadosComparativo:
    """Fontes já agregadas por SKU.

    `sap`: {produto_id: {'cosan': Decimal, 'brida': Decimal}} somente para SKUs
    com saldo diferente de zero em COSAN ou BRIDA. Na importação completa
    (uma planilha com todos os canais) ausência de registro no canal é saldo
    zero; `canais_carregados` indica quais canais vieram de fato.
    """

    comparativo: ComparativoContagem
    contagens: dict[int, ContagemAssociada]
    lancamentos: list[Lancamento]
    produtos: dict[int, dict]
    sap: dict[int, dict]
    gerado_em: datetime
    canais_carregados: frozenset[str] | None = None
    canais_registrados: bool = True
    sap_inativos_excluidos: int = 0
    sap_fora_canais: int = 0
    # Posições atribuídas por tarefa nos inventários associados:
    # {posicao_id: (codigo, descricao)}. Vazio quando não há planejamento.
    posicoes_planejadas: dict[int, tuple[str, str]] = field(default_factory=dict)

    @property
    def ordens_associadas(self) -> frozenset[int]:
        return frozenset(self.contagens)

    @property
    def tem_referencia(self) -> bool:
        return self.canais_carregados is not None

    def canal_carregado(self, chave: str) -> bool:
        return self.canais_carregados is not None and chave in self.canais_carregados

    def canais_da_selecao(self, canal: str) -> tuple[str, ...]:
        canal = Canal.normalizar(canal)
        return ('cosan', 'brida') if canal == Canal.AMBAS else (Canal.chave_saldo(canal),)

    def skus_canal(self, chave: str) -> set[int]:
        if not self.canal_carregado(chave):
            return set()
        return {pid for pid, saldo in self.sap.items() if saldo[chave] != 0}

    def skus_sap(self, canal: str) -> set[int]:
        """SKUs SAP do universo da aba: união dos canais selecionados."""
        resultado: set[int] = set()
        for chave in self.canais_da_selecao(canal):
            resultado |= self.skus_canal(chave)
        return resultado

    def motivo_sap(self, canal: str) -> str:
        """Por que o SAP selecionado não pode ser comparado ('' = pode)."""
        if not self.tem_referencia:
            return 'Sem referência SAP'
        faltantes = [c for c in self.canais_da_selecao(canal) if not self.canal_carregado(c)]
        if not faltantes:
            return ''
        nomes = ' e '.join(NOME_CANAL[c] for c in faltantes)
        if Canal.normalizar(canal) == Canal.AMBAS:
            return f'Base SAP parcial: {nomes} não carregado na importação'
        return f'Canal {nomes} não carregado na importação SAP'

    def saldos(self, produto_id: int) -> tuple[dict, dict]:
        """(valores, presença) de COSAN, BRIDA e Total para um SKU.

        Canal não carregado: valor None (nunca zero). Canal carregado sem
        registro do SKU: valor 0 e presença False (regra da importação completa).
        """
        bruto = self.sap.get(produto_id)
        valores: dict[str, Decimal | None] = {}
        presenca: dict[str, bool] = {}
        for chave in ('cosan', 'brida'):
            if not self.canal_carregado(chave):
                valores[chave] = None
                presenca[chave] = False
            else:
                valor = bruto[chave] if bruto else Decimal('0')
                valores[chave] = valor
                presenca[chave] = valor != 0
        if valores['cosan'] is None or valores['brida'] is None:
            valores['total'] = None
        else:
            valores['total'] = valores['cosan'] + valores['brida']
        presenca['total'] = presenca['cosan'] or presenca['brida']
        return valores, presenca

    @property
    def c3_associada(self) -> bool:
        return 3 in self.contagens


def _nomes_usuarios(ids: set[int]) -> dict[int, str]:
    if not ids:
        return {}
    usuarios = get_user_model().objects.filter(pk__in=ids).values(
        'id', 'username', 'first_name', 'last_name', 'perfil_operacional__nome',
    )
    nomes = {}
    for usuario in usuarios:
        nome = usuario['perfil_operacional__nome'] or (
            f"{usuario['first_name']} {usuario['last_name']}".strip()
        ) or usuario['username']
        nomes[usuario['id']] = nome
    return nomes


def carregar_dados(comparativo: ComparativoContagem) -> DadosComparativo:
    """Lê uma única vez os lançamentos válidos, produtos e a fotografia SAP.

    Os registros vigentes de `InventarioItem` são os lançamentos válidos: a
    edição substitui a quantidade no mesmo registro e a exclusão o remove,
    então não há histórico ou versões substituídas a descartar.

    Conferência finalizada: usa somente o resultado gravado na finalização.
    """
    if comparativo.snapshot_resultado is not None:
        return dados_do_snapshot(comparativo)
    gerado_em = timezone.now()
    associacoes = list(
        comparativo.associacoes.select_related('inventario', 'inventario__usuario')
        .order_by('ordem')
    )
    ordem_por_inventario = {a.inventario_id: a.ordem for a in associacoes}

    registros = list(
        InventarioItem.objects.filter(
            inventario_id__in=list(ordem_por_inventario),
        ).values(
            'id', 'inventario_id', 'posicao_id', 'posicao__codigo', 'posicao__posicao',
            'produto_id', 'quantidade_fisica', 'usuario_contagem_id', 'data_contagem',
            'usuario_registro_id', 'data_registro', 'usuario_alteracao_id',
            'data_alteracao', 'origem_contagem',
        ).order_by('id')
    )

    ids_usuarios: set[int] = set()
    for registro in registros:
        for chave in ('usuario_contagem_id', 'usuario_registro_id', 'usuario_alteracao_id'):
            if registro[chave]:
                ids_usuarios.add(registro[chave])
    nomes = _nomes_usuarios(ids_usuarios)

    lancamentos: list[Lancamento] = []
    totais: dict[int, int] = {}
    for registro in registros:
        # Autoria original: campos de registro (novos lançamentos e correções
        # posteriores à captura); senão, a gravação vigente do legado.
        if registro['data_registro'] is not None or registro['usuario_registro_id']:
            usuario_id = registro['usuario_registro_id']
            data = registro['data_registro']
        else:
            usuario_id = registro['usuario_contagem_id']
            data = registro['data_contagem']
        ordem = ordem_por_inventario[registro['inventario_id']]
        totais[ordem] = totais.get(ordem, 0) + 1
        lancamentos.append(Lancamento(
            id=registro['id'],
            inventario_id=registro['inventario_id'],
            ordem=ordem,
            posicao_id=registro['posicao_id'],
            codigo_posicao=registro['posicao__codigo'] or '',
            descricao_posicao=registro['posicao__posicao'] or '',
            produto_id=registro['produto_id'],
            quantidade=Decimal(registro['quantidade_fisica']),
            operador=nomes.get(usuario_id, NAO_REGISTRADO) if usuario_id else NAO_REGISTRADO,
            data_registro=data,
            autor_alteracao=(
                nomes.get(registro['usuario_alteracao_id'], NAO_REGISTRADO)
                if registro['data_alteracao'] else ''
            ),
            data_alteracao=registro['data_alteracao'],
            origem=registro['origem_contagem'] or 'Legado',
        ))

    # SAP agregado por SKU e canal a partir da fotografia (uma linha por SKU).
    sap: dict[int, dict] = {}
    canais_carregados = None
    canais_registrados = True
    fora_canais = 0
    referencia = comparativo.referencia_sap if comparativo.referencia_sap_id else None
    if referencia is not None:
        if referencia.canais_carregados is None:
            # Fotografia anterior ao registro de canais: planilha única, tratada
            # como completa e sinalizada na tela.
            canais_carregados = frozenset(CAMPO_CANAL)
            canais_registrados = False
        else:
            canais_carregados = frozenset(
                chave for chave, campo in CAMPO_CANAL.items()
                if campo in referencia.canais_carregados
            )
        for item in ReferenciaSAPItem.objects.filter(
            referencia_id=referencia.pk,
        ).values('produto_id', 'canal_1', 'canal_110'):
            cosan = Decimal(item['canal_110'])
            brida = Decimal(item['canal_1'])
            if cosan == 0 and brida == 0:
                fora_canais += 1  # saldo só em outros canais (0, 2, 66...) ou zerado
                continue
            atual = sap.setdefault(item['produto_id'], {'cosan': Decimal('0'), 'brida': Decimal('0')})
            atual['cosan'] += cosan
            atual['brida'] += brida

    ids_produtos = {l.produto_id for l in lancamentos} | set(sap)
    produtos = {
        p['id']: p
        for p in Produto.objects.filter(pk__in=ids_produtos).values(
            'id', 'codigo_produto', 'descricao', 'embalagem', 'ativo',
        )
    }
    # Política de inativos (igual ao confronto): saldo SAP só entra para
    # produto elegível; lançamentos físicos existentes são sempre exibidos.
    contados = {l.produto_id for l in lancamentos}
    inativos = {
        pid for pid in sap
        if pid not in contados and not produtos.get(pid, {}).get('ativo')
    }
    sap = {pid: saldo for pid, saldo in sap.items() if pid not in inativos}

    contagens = {
        a.ordem: ContagemAssociada(
            ordem=a.ordem,
            inventario=a.inventario,
            total_lancamentos=totais.get(a.ordem, 0),
        )
        for a in associacoes
    }
    posicoes_planejadas = {
        t['posicao_id']: (t['posicao__codigo'] or '', t['posicao__posicao'] or '')
        for t in InventarioTarefa.objects.filter(
            tipo_inventario=InventarioTarefa.TipoInventario.GERAL,
            inventario_id__in=list(ordem_por_inventario),
        ).values('posicao_id', 'posicao__codigo', 'posicao__posicao').distinct()
    }
    return DadosComparativo(
        comparativo=comparativo,
        contagens=contagens,
        lancamentos=lancamentos,
        produtos=produtos,
        sap=sap,
        gerado_em=gerado_em,
        canais_carregados=canais_carregados,
        canais_registrados=canais_registrados,
        sap_inativos_excluidos=len(inativos),
        sap_fora_canais=fora_canais,
        posicoes_planejadas=posicoes_planejadas,
    )


# ---------------------------------------------------------------------------
# Resultado gravado (finalização)
# ---------------------------------------------------------------------------

VERSAO_SNAPSHOT = 2


def _iso(valor: datetime | None) -> str | None:
    return valor.isoformat() if valor is not None else None


def _data(valor: str | None) -> datetime | None:
    return datetime.fromisoformat(valor) if valor else None


def serializar_dados(dados: DadosComparativo) -> dict:
    """Tudo o que o cálculo usa, em JSON (quantidades como texto/Decimal)."""
    return {
        'versao': VERSAO_SNAPSHOT,
        'gerado_em': _iso(dados.gerado_em),
        'contagens': {
            str(ordem): {
                'inventario_id': c.inventario.pk,
                'inventario_status': c.inventario.status,
                'total_lancamentos': c.total_lancamentos,
            }
            for ordem, c in dados.contagens.items()
        },
        'lancamentos': [
            {
                'id': l.id,
                'inventario_id': l.inventario_id,
                'ordem': l.ordem,
                'posicao_id': l.posicao_id,
                'codigo_posicao': l.codigo_posicao,
                'descricao_posicao': l.descricao_posicao,
                'produto_id': l.produto_id,
                'quantidade': str(l.quantidade),
                'operador': l.operador,
                'data_registro': _iso(l.data_registro),
                'autor_alteracao': l.autor_alteracao,
                'data_alteracao': _iso(l.data_alteracao),
                'origem': l.origem,
            }
            for l in dados.lancamentos
        ],
        'produtos': {
            str(pid): {
                'id': p['id'],
                'codigo_produto': p['codigo_produto'],
                'descricao': p['descricao'],
                'embalagem': p['embalagem'],
                'ativo': p['ativo'],
            }
            for pid, p in dados.produtos.items()
        },
        'sap': {
            str(pid): {chave: str(saldo[chave]) for chave in ('cosan', 'brida')}
            for pid, saldo in dados.sap.items()
        },
        'sap_meta': {
            'canais_carregados': (
                sorted(dados.canais_carregados) if dados.canais_carregados is not None else None
            ),
            'canais_registrados': dados.canais_registrados,
            'inativos_excluidos': dados.sap_inativos_excluidos,
            'fora_canais': dados.sap_fora_canais,
        },
        'posicoes_planejadas': [
            [pid, codigo, descricao] for pid, (codigo, descricao) in dados.posicoes_planejadas.items()
        ],
    }


def dados_do_snapshot(comparativo: ComparativoContagem) -> DadosComparativo:
    snap = comparativo.snapshot_resultado
    contagens = {}
    for ordem, info in snap['contagens'].items():
        # Instância não salva: só carrega id e estado no momento da gravação.
        inventario = Inventario(pk=info['inventario_id'], status=info['inventario_status'])
        contagens[int(ordem)] = ContagemAssociada(
            ordem=int(ordem),
            inventario=inventario,
            total_lancamentos=info['total_lancamentos'],
        )
    lancamentos = [
        Lancamento(
            id=l['id'],
            inventario_id=l['inventario_id'],
            ordem=l['ordem'],
            posicao_id=l['posicao_id'],
            codigo_posicao=l['codigo_posicao'],
            descricao_posicao=l['descricao_posicao'],
            produto_id=l['produto_id'],
            quantidade=Decimal(l['quantidade']),
            operador=l['operador'],
            data_registro=_data(l['data_registro']),
            autor_alteracao=l['autor_alteracao'],
            data_alteracao=_data(l['data_alteracao']),
            origem=l['origem'],
        )
        for l in snap['lancamentos']
    ]
    meta = snap.get('sap_meta')
    if meta is None:
        # Gravação da versão 1: canais não registrados.
        canais = frozenset(CAMPO_CANAL) if comparativo.referencia_sap_id else None
        meta = {'canais_registrados': False, 'inativos_excluidos': 0, 'fora_canais': 0}
    else:
        canais = (
            frozenset(meta['canais_carregados']) if meta['canais_carregados'] is not None else None
        )
    sap = {}
    for pid, saldo in snap['sap'].items():
        cosan = Decimal(saldo.get('cosan', '0'))
        brida = Decimal(saldo.get('brida', '0'))
        if cosan != 0 or brida != 0:
            sap[int(pid)] = {'cosan': cosan, 'brida': brida}
    return DadosComparativo(
        comparativo=comparativo,
        contagens=contagens,
        lancamentos=lancamentos,
        produtos={int(pid): p for pid, p in snap['produtos'].items()},
        sap=sap,
        gerado_em=_data(snap['gerado_em']),
        canais_carregados=canais,
        canais_registrados=meta['canais_registrados'],
        sap_inativos_excluidos=meta['inativos_excluidos'],
        sap_fora_canais=meta['fora_canais'],
        posicoes_planejadas={
            pid: (codigo, descricao) for pid, codigo, descricao in snap.get('posicoes_planejadas', [])
        },
    )


# ---------------------------------------------------------------------------
# Linhas
# ---------------------------------------------------------------------------


@dataclass
class PosicaoDetalhe:
    codigo: str
    descricao: str
    quantidades: dict[int, Decimal | None]


@dataclass
class LinhaComparativo:
    chave: str
    produto_id: int
    codigo_produto: str
    descricao: str
    embalagem: str
    posicoes: list[tuple[str, str]]
    medicoes: dict[int, Medicao]
    contagens: ClassificacaoContagens
    sap: dict[str, Decimal | None] = field(default_factory=dict)
    sap_presenca: dict[str, bool] = field(default_factory=dict)
    presente_sap: bool = False
    presente_fisico: bool = False
    saldo_selecionado: Decimal | None = None
    posicao_id: int | None = None
    status_sap: ClassificacaoSAP | None = None
    distribuicao_divergente: str = ''
    detalhe_posicoes: list[PosicaoDetalhe] = field(default_factory=list)

    @property
    def lancamentos(self) -> list[Lancamento]:
        todos = []
        for ordem in ORDENS:
            todos.extend(self.medicoes[ordem].lancamentos)
        return todos

    @property
    def quantidades(self) -> dict[int, Decimal | None]:
        return {o: self.medicoes[o].quantidade for o in ORDENS}

    @property
    def codigos_posicao(self) -> list[str]:
        return [codigo for codigo, _ in self.posicoes]

    @property
    def nomes_posicao(self) -> list[str]:
        """Posição de armazenagem (ex.: 01.03.101); cai no código se vazia."""
        return [descricao or codigo for codigo, descricao in self.posicoes]

    def texto_busca(self) -> str:
        partes = [self.codigo_produto, self.descricao]
        for codigo, descricao in self.posicoes:
            partes.extend([codigo, descricao])
        return ' '.join(partes).lower()


def _produto_info(dados: DadosComparativo, produto_id: int) -> dict:
    return dados.produtos.get(produto_id) or {
        'codigo_produto': str(produto_id),
        'descricao': DESCRICAO_NAO_CADASTRADA,
        'embalagem': '',
        'ativo': False,
    }


def _aplicar_celulas(medicoes: dict[int, Medicao], classificacao: ClassificacaoContagens):
    for ordem, medicao in medicoes.items():
        medicao.celula = classificacao.celulas[ordem]


def _distribuicao_divergente(
    por_ordem: dict[int, list[Lancamento]],
    pares: list[tuple[int, int]],
) -> str:
    divergentes = []
    for a, b in pares:
        dist_a: dict[int, Decimal] = {}
        dist_b: dict[int, Decimal] = {}
        for lancamento in por_ordem.get(a, []):
            dist_a[lancamento.posicao_id] = dist_a.get(lancamento.posicao_id, 0) + lancamento.quantidade
        for lancamento in por_ordem.get(b, []):
            dist_b[lancamento.posicao_id] = dist_b.get(lancamento.posicao_id, 0) + lancamento.quantidade
        if dist_a != dist_b:
            divergentes.append(f'{rotulo_ordem(a)} × {rotulo_ordem(b)}')
    if not divergentes:
        return ''
    return 'Totais iguais com distribuição por posição diferente (' + ', '.join(divergentes) + ')'


def montar_linhas_sku(dados: DadosComparativo, canal: str) -> list[LinhaComparativo]:
    canal = Canal.normalizar(canal)
    chave_saldo = Canal.chave_saldo(canal)
    associadas = dados.ordens_associadas

    por_produto: dict[int, dict[int, list[Lancamento]]] = {}
    for lancamento in dados.lancamentos:
        por_produto.setdefault(lancamento.produto_id, {}).setdefault(
            lancamento.ordem, [],
        ).append(lancamento)

    # Universo da aba: SKUs SAP dos canais selecionados + SKUs contados.
    # Total SAP nunca é fonte de SKUs; nenhuma junção descarta exclusivos.
    sap_selecionado = dados.skus_sap(canal)
    universo = sap_selecionado | set(por_produto)
    motivo = dados.motivo_sap(canal)
    linhas = []
    for produto_id in universo:
        info = _produto_info(dados, produto_id)
        por_ordem = por_produto.get(produto_id, {})
        medicoes = {
            o: _medicao(o, o in associadas, por_ordem.get(o, []))
            for o in ORDENS
        }
        quantidades = {o: medicoes[o].quantidade for o in ORDENS}
        classificacao = classificar_contagens(quantidades, associadas)
        _aplicar_celulas(medicoes, classificacao)

        posicoes_map: dict[int, PosicaoDetalhe] = {}
        for ordem, lancamentos in por_ordem.items():
            for lancamento in lancamentos:
                detalhe = posicoes_map.setdefault(lancamento.posicao_id, PosicaoDetalhe(
                    codigo=lancamento.codigo_posicao,
                    descricao=lancamento.descricao_posicao,
                    quantidades={o: None for o in ORDENS},
                ))
                atual = detalhe.quantidades[ordem]
                detalhe.quantidades[ordem] = (atual or Decimal('0')) + lancamento.quantidade
        detalhe_posicoes = sorted(
            posicoes_map.values(), key=lambda d: (d.descricao or d.codigo, d.codigo),
        )

        sap, presenca = dados.saldos(produto_id)
        saldo = sap[chave_saldo]
        status_sap = classificar_sap(quantidades, saldo, classificacao, motivo)
        presente_sap = produto_id in sap_selecionado
        if not motivo and not presente_sap and status_sap.status != StatusSAP.NEUTRO:
            status_sap.detalhe = ' · '.join(
                parte for parte in ('Sem registro no SAP selecionado', status_sap.detalhe) if parte
            )

        linhas.append(LinhaComparativo(
            chave=f'sku-{produto_id}',
            produto_id=produto_id,
            codigo_produto=str(info['codigo_produto']),
            descricao=info['descricao'] or DESCRICAO_NAO_CADASTRADA,
            embalagem=info['embalagem'] or '',
            posicoes=[(d.codigo, d.descricao) for d in detalhe_posicoes],
            medicoes=medicoes,
            contagens=classificacao,
            sap=sap,
            sap_presenca=presenca,
            presente_sap=presente_sap,
            presente_fisico=bool(por_ordem),
            saldo_selecionado=saldo,
            status_sap=status_sap,
            distribuicao_divergente=(
                _distribuicao_divergente(por_ordem, classificacao.pares_iguais)
                if len(detalhe_posicoes) > 1 else ''
            ),
            detalhe_posicoes=detalhe_posicoes,
        ))
    linhas.sort(key=lambda l: (l.codigo_produto, l.produto_id))
    return linhas


def montar_linhas_posicao(dados: DadosComparativo) -> list[LinhaComparativo]:
    """Uma linha por posição + SKU, somente com as contagens físicas.

    A visão por posição acompanha as contagens; o SAP não tem saldo por
    posição e por isso não participa desta visão.
    """
    associadas = dados.ordens_associadas
    grupos: dict[tuple[int, int], dict[int, list[Lancamento]]] = {}
    for lancamento in dados.lancamentos:
        grupos.setdefault(
            (lancamento.posicao_id, lancamento.produto_id), {},
        ).setdefault(lancamento.ordem, []).append(lancamento)

    linhas = []
    for (posicao_id, produto_id), por_ordem in grupos.items():
        info = _produto_info(dados, produto_id)
        primeiro = next(iter(por_ordem.values()))[0]
        medicoes = {
            o: _medicao(o, o in associadas, por_ordem.get(o, []))
            for o in ORDENS
        }
        quantidades = {o: medicoes[o].quantidade for o in ORDENS}
        classificacao = classificar_contagens(quantidades, associadas)
        _aplicar_celulas(medicoes, classificacao)
        linhas.append(LinhaComparativo(
            chave=f'pos-{posicao_id}-{produto_id}',
            produto_id=produto_id,
            codigo_produto=str(info['codigo_produto']),
            descricao=info['descricao'] or DESCRICAO_NAO_CADASTRADA,
            embalagem=info['embalagem'] or '',
            posicoes=[(primeiro.codigo_posicao, primeiro.descricao_posicao)],
            presente_fisico=True,
            posicao_id=posicao_id,
            medicoes=medicoes,
            contagens=classificacao,
        ))
    linhas.sort(key=lambda l: (
        l.posicoes[0][1] or l.posicoes[0][0], l.posicoes[0][0], l.codigo_produto, l.produto_id,
    ))
    return linhas


# ---------------------------------------------------------------------------
# Filtros e resumo
# ---------------------------------------------------------------------------


@dataclass
class FiltrosComparativo:
    termo: str = ''
    status_contagem: str = ''
    status_sap: str = ''
    necessita_c3: bool = False

    @classmethod
    def de_parametros(cls, params, visao: str) -> 'FiltrosComparativo':
        status_contagem = (params.get('status_contagem') or '').strip().upper()
        if status_contagem not in StatusContagem.ROTULOS:
            status_contagem = ''
        status_sap = (params.get('status_sap') or '').strip().upper()
        if status_sap not in StatusSAP.ROTULOS or visao == Visao.POSICAO:
            status_sap = ''
        return cls(
            termo=(params.get('q') or '').strip(),
            status_contagem=status_contagem,
            status_sap=status_sap,
            necessita_c3=(params.get('necessita_c3') or '') in ('1', 'on', 'true'),
        )

    def descricao(self) -> str:
        partes = []
        if self.termo:
            partes.append(f'Pesquisa: {self.termo}')
        if self.status_contagem:
            partes.append(f'Status das contagens: {StatusContagem.ROTULOS[self.status_contagem]}')
        if self.status_sap:
            partes.append(f'Status SAP: {StatusSAP.ROTULOS[self.status_sap]}')
        if self.necessita_c3:
            partes.append('Necessita C3')
        return ' · '.join(partes) or 'Sem filtros'


def filtrar_linhas(linhas: list[LinhaComparativo], filtros: FiltrosComparativo) -> list[LinhaComparativo]:
    termo = filtros.termo.lower()
    resultado = []
    for linha in linhas:
        if termo and termo not in linha.texto_busca():
            continue
        if filtros.status_contagem and linha.contagens.status != filtros.status_contagem:
            continue
        if filtros.status_sap and (
            linha.status_sap is None or linha.status_sap.status != filtros.status_sap
        ):
            continue
        if filtros.necessita_c3 and not linha.contagens.necessita_c3:
            continue
        resultado.append(linha)
    return resultado


@dataclass
class ResumoComparativo:
    total: int
    contagem_ok: int
    contagem_divergente: int
    contagem_aguardando: int
    necessita_c3: int
    sap_ok: int
    sap_parcial: int
    sap_divergente: int
    sap_neutro: int


def resumir(linhas: list[LinhaComparativo]) -> ResumoComparativo:
    def conta(cond):
        return sum(1 for linha in linhas if cond(linha))

    def sap(status):
        return conta(lambda l: l.status_sap is not None and l.status_sap.status == status)

    return ResumoComparativo(
        total=len(linhas),
        contagem_ok=conta(lambda l: l.contagens.status == StatusContagem.OK),
        contagem_divergente=conta(lambda l: l.contagens.status == StatusContagem.DIVERGENTE),
        contagem_aguardando=conta(lambda l: l.contagens.status == StatusContagem.AGUARDANDO),
        necessita_c3=conta(lambda l: l.contagens.necessita_c3),
        sap_ok=sap(StatusSAP.OK),
        sap_parcial=sap(StatusSAP.PARCIAL),
        sap_divergente=sap(StatusSAP.DIVERGENTE),
        sap_neutro=sap(StatusSAP.NEUTRO),
    )


@dataclass
class Acuracidade:
    """Acuracidade do estoque contado em relação ao SAP do canal selecionado.

    Base: SKUs com consenso de contagem (duas contagens iguais). Acerto: o
    valor do consenso é igual ao saldo SAP. SKUs sem consenso ficam fora do
    cálculo e são informados à parte. SKU com consenso e ausente na base SAP
    conta como divergente, exceto quando o consenso é zero.
    """

    canal: str
    disponivel: bool
    motivo: str
    avaliados: int
    acertos: int
    divergentes: int
    sem_consenso: int
    total: int
    percentual: Decimal | None


def calcular_acuracidade(
    linhas_sku: list[LinhaComparativo],
    canal: str,
    tem_referencia: bool | str,
) -> Acuracidade:
    """`tem_referencia`: True/False ou o motivo de indisponibilidade da base."""
    canal = Canal.normalizar(canal)
    chave = Canal.chave_saldo(canal)
    avaliados = acertos = sem_consenso = 0
    for linha in linhas_sku:
        consenso = linha.contagens.valor_consenso
        if linha.contagens.status != StatusContagem.OK or consenso is None:
            sem_consenso += 1
            continue
        avaliados += 1
        saldo = linha.sap.get(chave)
        # Ausente na base SAP: só acerta se o estoque contado também for zero.
        if consenso == (saldo if saldo is not None else Decimal('0')):
            acertos += 1
    if isinstance(tem_referencia, str) and tem_referencia:
        motivo = tem_referencia
    elif tem_referencia is False:
        motivo = 'Sem referência SAP'
    elif not avaliados:
        motivo = 'Nenhum SKU com duas contagens iguais'
    else:
        motivo = ''
    percentual = None
    if not motivo:
        percentual = (Decimal(acertos) * 100 / Decimal(avaliados)).quantize(Decimal('0.01'))
    return Acuracidade(
        canal=canal,
        disponivel=not motivo,
        motivo=motivo,
        avaliados=avaliados,
        acertos=acertos,
        divergentes=avaliados - acertos,
        sem_consenso=sem_consenso,
        total=len(linhas_sku),
        percentual=percentual,
    )


def salvar_acuracidade(
    comparativo: ComparativoContagem,
    canal: str,
    usuario,
    dados: DadosComparativo | None = None,
) -> ComparativoAcuracidade:
    """Grava o cálculo atual (somente leitura das contagens) como registro."""
    canal = Canal.normalizar(canal)
    dados = dados or carregar_dados(comparativo)
    acuracidade = calcular_acuracidade(
        montar_linhas_sku(dados, canal), canal, dados.motivo_sap(canal) or True,
    )
    if not acuracidade.disponivel:
        raise ComparativoError(f'Acuracidade indisponível: {acuracidade.motivo}.')
    registro = ComparativoAcuracidade.objects.create(
        comparativo=comparativo,
        canal=canal,
        referencia_sap_id=comparativo.referencia_sap_id,
        percentual=acuracidade.percentual,
        skus_avaliados=acuracidade.avaliados,
        skus_acertos=acuracidade.acertos,
        skus_divergentes=acuracidade.divergentes,
        skus_sem_consenso=acuracidade.sem_consenso,
        skus_total=acuracidade.total,
        usuario=usuario if getattr(usuario, 'is_authenticated', False) else None,
    )
    _registrar_evento(
        comparativo, usuario, 'ACURACIDADE_SALVA',
        canal=canal, percentual=str(acuracidade.percentual), registro=registro.pk,
    )
    return registro


@transaction.atomic
def finalizar_comparativo(comparativo: ComparativoContagem, usuario) -> ComparativoContagem:
    """Grava o resultado atual e finaliza a conferência.

    Não altera contagens, estoque nem os inventários de origem. A acuracidade
    de cada canal disponível é registrada com os mesmos dados gravados.
    """
    comparativo = ComparativoContagem.objects.select_for_update().get(pk=comparativo.pk)
    if comparativo.snapshot_resultado is not None:
        raise ComparativoError('Esta conferência já foi salva e finalizada.')
    if not comparativo.associacoes.exists():
        raise ComparativoError('Associe ao menos a C1 antes de salvar.')
    dados = carregar_dados(comparativo)
    acuracidades = []
    for canal in (Canal.COSAN, Canal.BRIDA, Canal.AMBAS):
        acuracidade = calcular_acuracidade(
            montar_linhas_sku(dados, canal), canal, dados.motivo_sap(canal) or True,
        )
        if acuracidade.disponivel:
            acuracidades.append(salvar_acuracidade(comparativo, canal, usuario, dados=dados))
    estado_anterior = comparativo.estado
    comparativo.snapshot_resultado = serializar_dados(dados)
    comparativo.data_finalizacao = dados.gerado_em
    comparativo.usuario_finalizacao = usuario if getattr(usuario, 'is_authenticated', False) else None
    comparativo.estado = ComparativoContagem.Estado.ENCERRADA
    comparativo.save(update_fields=[
        'snapshot_resultado', 'data_finalizacao', 'usuario_finalizacao', 'estado', 'data_atualizacao',
    ])
    _registrar_evento(
        comparativo, usuario, 'FINALIZADA',
        estado_anterior=estado_anterior,
        lancamentos=len(dados.lancamentos),
        acuracidades=[a.pk for a in acuracidades],
    )
    return comparativo


def pode_reabrir_comparativo(comparativo: ComparativoContagem, agora=None) -> bool:
    """Conferência finalizada só pode ser reaberta nos 7 dias seguintes ao fechamento."""
    if comparativo.snapshot_resultado is None or comparativo.data_finalizacao is None:
        return False
    agora = agora or timezone.now()
    fechado_em = comparativo.data_finalizacao
    if timezone.is_naive(fechado_em):
        fechado_em = timezone.make_aware(fechado_em, timezone.get_current_timezone())
    return agora - fechado_em <= PRAZO_REABERTURA


@transaction.atomic
def reabrir_comparativo(comparativo: ComparativoContagem, usuario) -> ComparativoContagem:
    """Descarta o resultado gravado e devolve a conferência ao estado aberto.

    Não apaga inventários nem contagens. Depois de reaberta, inventários
    ainda abertos ou em andamento podem ser excluídos.
    """
    comparativo = ComparativoContagem.objects.select_for_update().get(pk=comparativo.pk)
    if comparativo.snapshot_resultado is None:
        raise ComparativoError('Esta conferência não está finalizada.')
    if not pode_reabrir_comparativo(comparativo):
        raise ComparativoError(
            'Conferência fechada há mais de 7 dias não pode ser reaberta.',
        )
    comparativo.snapshot_resultado = None
    comparativo.data_finalizacao = None
    comparativo.usuario_finalizacao = None
    comparativo.estado = ComparativoContagem.Estado.ABERTA
    comparativo.save(update_fields=[
        'snapshot_resultado', 'data_finalizacao', 'usuario_finalizacao', 'estado', 'data_atualizacao',
    ])
    _registrar_evento(comparativo, usuario, 'REABERTA')
    return comparativo


@transaction.atomic
def desvincular_inventario_de_conferencias_abertas(inventario: Inventario) -> None:
    """Tira o inventário de conferências abertas para a exclusão poder seguir.

    Conferência finalizada continua protegendo o inventário até ser reaberta.
    """
    associacoes = list(
        ComparativoContagemInventario.objects.select_for_update()
        .select_related('comparativo')
        .filter(inventario=inventario)
    )
    bloqueadas = sorted({
        associacao.comparativo.nome
        for associacao in associacoes
        if associacao.comparativo.snapshot_resultado is not None
    })
    if bloqueadas:
        nomes = ', '.join(f'"{nome}"' for nome in bloqueadas)
        raise ComparativoError(
            'Este inventário está na conferência finalizada '
            f'{nomes}. Reabra a conferência para excluir este inventário de teste.'
        )
    if associacoes:
        ComparativoContagemInventario.objects.filter(
            pk__in=[associacao.pk for associacao in associacoes],
        ).delete()


@dataclass
class Cobertura:
    """Quantidades de SKUs distintos (não de registros nem de unidades)."""

    skus_cosan: int
    skus_brida: int
    skus_sap_selecionado: int
    skus_fisicos: int
    intersecao: int
    somente_sap: int
    somente_fisico: int
    total: int
    sap_inativos_excluidos: int
    sap_fora_canais: int


def calcular_cobertura(dados: DadosComparativo, canal: str) -> Cobertura:
    sap = dados.skus_sap(canal)
    fisicos = {l.produto_id for l in dados.lancamentos}
    return Cobertura(
        skus_cosan=len(dados.skus_canal('cosan')),
        skus_brida=len(dados.skus_canal('brida')),
        skus_sap_selecionado=len(sap),
        skus_fisicos=len(fisicos),
        intersecao=len(sap & fisicos),
        somente_sap=len(sap - fisicos),
        somente_fisico=len(fisicos - sap),
        total=len(sap | fisicos),
        sap_inativos_excluidos=dados.sap_inativos_excluidos,
        sap_fora_canais=dados.sap_fora_canais,
    )


# ---------------------------------------------------------------------------
# Indicadores gerais (velocímetros)
# ---------------------------------------------------------------------------

FAIXA_VERMELHA = 'vermelho'
FAIXA_LARANJA = 'laranja'
FAIXA_AMARELA = 'amarelo'
FAIXA_VERDE = 'verde'
FAIXA_NEUTRA = 'neutro'


def faixa_acuracidade(percentual: Decimal | None) -> str:
    """Faixa pelo valor exato (antes do arredondamento de exibição).

    0–60 inclusive: vermelho; >60–90 inclusive: laranja;
    >90 e <98: amarelo-esverdeado; 98–100: verde. 97,99 não é verde.
    """
    if percentual is None:
        return FAIXA_NEUTRA
    if percentual <= 60:
        return FAIXA_VERMELHA
    if percentual <= 90:
        return FAIXA_LARANJA
    if percentual < 98:
        return FAIXA_AMARELA
    return FAIXA_VERDE


def formatar_percentual(percentual: Decimal | None) -> str:
    if percentual is None:
        return '—'
    arredondado = percentual.quantize(Decimal('0.1'))
    return f'{arredondado:f}'.replace('.', ',') + '%'


@dataclass
class Indicador:
    """Indicador geral da conferência (não depende de pesquisa, filtros ou página)."""

    chave: str
    titulo: str
    subtitulo: str
    numerador: int | None
    denominador: int | None
    percentual: Decimal | None  # valor exato, sem arredondamento
    motivo: str
    descricao_base: str
    linhas: list[str]
    provisorio: bool
    # SKU: SKUs com alguma contagem; posição: posições com algum registro.
    auxiliar: int | None = None
    rotulo_escopo: str = ''

    @property
    def disponivel(self) -> bool:
        return self.percentual is not None

    @property
    def faixa(self) -> str:
        return faixa_acuracidade(self.percentual)

    @property
    def percentual_texto(self) -> str:
        return formatar_percentual(self.percentual)

    @property
    def descricao_acessivel(self) -> str:
        if not self.disponivel:
            return f'{self.titulo}: sem dados. {self.motivo}.'
        texto = f'{self.titulo}: {self.percentual_texto}. ' + '. '.join(self.linhas)
        return texto + ('. Resultado provisório.' if self.provisorio else '.')


def _percentual(numerador: int, denominador: int) -> Decimal | None:
    if not denominador:
        return None
    return Decimal(numerador) * 100 / Decimal(denominador)


def calcular_indicador_sku(
    linhas_sku: list[LinhaComparativo],
    dados: DadosComparativo,
    canal: str,
) -> Indicador:
    """SKUs com Status SAP verde / todos os SKUs do universo da aba × 100.

    O universo inclui conformes, divergentes, não contados e exclusivos
    físicos (uma linha por SKU), por isso é provisório durante a operação.
    """
    canal = Canal.normalizar(canal)
    total = len(linhas_sku)
    conformes = sum(
        1 for l in linhas_sku if l.status_sap is not None and l.status_sap.status == StatusSAP.OK
    )
    contados = sum(1 for l in linhas_sku if any(m.registrada for m in l.medicoes.values()))
    motivo = dados.motivo_sap(canal)
    if not motivo and not total:
        motivo = 'Nenhum SKU no comparativo'
    percentual = None if motivo else _percentual(conformes, total)
    return Indicador(
        chave='sku',
        titulo='Acuracidade por SKU',
        subtitulo=f'Contagens × SAP {Canal.ROTULOS[canal]}',
        numerador=conformes if not motivo else None,
        denominador=total if not motivo else None,
        percentual=percentual,
        motivo=motivo,
        descricao_base=(
            'SKUs com duas contagens iguais ao SAP ÷ todos os SKUs do SAP selecionado '
            'e das contagens (inclui não contados).'
        ),
        linhas=[
            f'{conformes} de {total} SKUs conformes com SAP',
            f'{contados} de {total} SKUs com alguma contagem',
        ],
        provisorio=dados.comparativo.snapshot_resultado is None,
        auxiliar=contados,
        rotulo_escopo='SKUs do SAP selecionado e das contagens',
    )


def calcular_indicador_posicao(
    linhas_posicao: list[LinhaComparativo],
    dados: DadosComparativo,
) -> Indicador:
    """Posições conformes / posições do escopo × 100 (concordância física).

    Posição conforme: tem registros e todos os SKUs dela têm uma dupla de
    contagens coincidente. Escopo: posições planejadas (tarefas dos
    inventários associados) somadas às posições com registros. Não usa SAP,
    então não muda ao trocar o canal.
    """
    por_posicao: dict[int, list[LinhaComparativo]] = {}
    for linha in linhas_posicao:
        por_posicao.setdefault(linha.posicao_id, []).append(linha)
    escopo = set(por_posicao) | set(dados.posicoes_planejadas)
    conformes = sum(
        1 for pid in escopo
        if por_posicao.get(pid)
        and all(l.contagens.status == StatusContagem.OK for l in por_posicao[pid])
    )
    com_registro = len(por_posicao)
    total = len(escopo)
    planejadas = bool(dados.posicoes_planejadas)
    rotulo_escopo = 'posições planejadas e com registros' if planejadas else 'posições com registros'
    motivo = '' if total else 'Nenhuma posição com registros'
    linhas = [f'{conformes} de {total} {rotulo_escopo} conformes']
    if planejadas:
        linhas.append(f'{com_registro} de {total} posições com algum registro')
    return Indicador(
        chave='posicao',
        titulo='Acuracidade por posição',
        subtitulo='Concordância entre contagens',
        numerador=conformes if total else None,
        denominador=total if total else None,
        percentual=_percentual(conformes, total),
        motivo=motivo,
        descricao_base=(
            'Posições em que todos os SKUs têm duas contagens iguais ÷ '
            + ('posições planejadas (tarefas) e com registros.' if planejadas else 'posições com registros.')
            + ' Não usa SAP.'
        ),
        linhas=linhas,
        provisorio=dados.comparativo.snapshot_resultado is None,
        auxiliar=com_registro,
        rotulo_escopo=rotulo_escopo.capitalize(),
    )


@dataclass
class ResultadoComparativo:
    dados: DadosComparativo
    canal: str
    visao: str
    filtros: FiltrosComparativo
    linhas: list[LinhaComparativo]
    resumo: ResumoComparativo
    acuracidade: Acuracidade | None = None
    cobertura: Cobertura | None = None
    total_sem_filtro: int = 0
    indicador_sku: Indicador | None = None
    indicador_posicao: Indicador | None = None

    @property
    def filtrado(self) -> bool:
        return len(self.linhas) != self.total_sem_filtro

    @property
    def colunas_sap(self) -> list[tuple[str, str]]:
        if self.visao == Visao.POSICAO:
            return []
        return Canal.colunas_sap(self.canal)


def analisar(
    comparativo: ComparativoContagem,
    *,
    canal: str,
    visao: str,
    filtros: FiltrosComparativo,
    dados: DadosComparativo | None = None,
) -> ResultadoComparativo:
    canal = Canal.normalizar(canal)
    visao = Visao.normalizar(visao)
    dados = dados or carregar_dados(comparativo)
    # Indicadores gerais: calculados sobre todas as linhas (antes de pesquisa,
    # filtros e paginação) e a partir da mesma leitura de dados da tabela.
    linhas_sku = montar_linhas_sku(dados, canal)
    linhas_posicao = montar_linhas_posicao(dados)
    todas = linhas_posicao if visao == Visao.POSICAO else linhas_sku
    acuracidade = None
    cobertura = None
    if visao == Visao.SKU:
        acuracidade = calcular_acuracidade(todas, canal, dados.motivo_sap(canal) or True)
        cobertura = calcular_cobertura(dados, canal)
    return ResultadoComparativo(
        dados=dados,
        canal=canal,
        visao=visao,
        filtros=filtros,
        linhas=filtrar_linhas(todas, filtros),
        resumo=resumir(todas),
        acuracidade=acuracidade,
        cobertura=cobertura,
        total_sem_filtro=len(todas),
        indicador_sku=calcular_indicador_sku(linhas_sku, dados, canal),
        indicador_posicao=calcular_indicador_posicao(linhas_posicao, dados),
    )
