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

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from itertools import combinations

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from estoque_sap.models import EstoqueSAP
from inventario.models import Inventario, InventarioItem
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
FUSO_ROTULO = 'Horário de Brasília (America/Sao_Paulo)'


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


@transaction.atomic
def capturar_referencia_sap(usuario=None) -> ReferenciaSAP:
    """Fotografa os saldos dos canais 1 e 110 da base SAP vigente.

    A importação SAP substitui a tabela inteira; por isso a conferência guarda
    a própria cópia. Se a importação vigente já foi fotografada, reutiliza.
    """
    ultima = obter_ultima_importacao_sap()
    if ultima is None:
        raise ComparativoError('Não há base SAP importada para fotografar.')

    existente = ReferenciaSAP.objects.filter(
        arquivo_origem=ultima.arquivo_origem,
        data_importacao_origem=ultima.data_importacao,
    ).first()
    if existente is not None:
        return existente

    registros = EstoqueSAP.objects.filter(
        produto__in=Produto.objects.elegiveis_para_inventario(),
    ).select_related('produto').order_by('produto_id', '-data_importacao')
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
    comparativo: ComparativoContagem
    contagens: dict[int, ContagemAssociada]
    lancamentos: list[Lancamento]
    produtos: dict[int, dict]
    sap: dict[int, dict]
    gerado_em: datetime

    @property
    def ordens_associadas(self) -> frozenset[int]:
        return frozenset(self.contagens)

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
    """
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

    sap: dict[int, dict] = {}
    if comparativo.referencia_sap_id:
        for item in ReferenciaSAPItem.objects.filter(
            referencia_id=comparativo.referencia_sap_id,
        ).values('produto_id', 'canal_1', 'canal_110'):
            sap[item['produto_id']] = {
                'cosan': Decimal(item['canal_110']),
                'brida': Decimal(item['canal_1']),
                'total': Decimal(item['canal_110']) + Decimal(item['canal_1']),
            }

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
    sap = {
        pid: saldo for pid, saldo in sap.items()
        if pid in contados or produtos.get(pid, {}).get('ativo')
    }

    contagens = {
        a.ordem: ContagemAssociada(
            ordem=a.ordem,
            inventario=a.inventario,
            total_lancamentos=totais.get(a.ordem, 0),
        )
        for a in associacoes
    }
    return DadosComparativo(
        comparativo=comparativo,
        contagens=contagens,
        lancamentos=lancamentos,
        produtos=produtos,
        sap=sap,
        gerado_em=gerado_em,
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
    saldo_selecionado: Decimal | None = None
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
        'descricao': 'PRODUTO NÃO CADASTRADO',
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

    universo = set(por_produto) | set(dados.sap)
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

        if dados.comparativo.referencia_sap_id is None:
            sap = {'cosan': None, 'brida': None, 'total': None}
            motivo = 'Sem referência SAP'
        elif produto_id not in dados.sap:
            sap = {'cosan': None, 'brida': None, 'total': None}
            motivo = 'SKU ausente na base SAP'
        else:
            sap = dados.sap[produto_id]
            motivo = ''
        saldo = sap[chave_saldo]

        linhas.append(LinhaComparativo(
            chave=f'sku-{produto_id}',
            produto_id=produto_id,
            codigo_produto=str(info['codigo_produto']),
            descricao=info['descricao'] or '',
            embalagem=info['embalagem'] or '',
            posicoes=[(d.codigo, d.descricao) for d in detalhe_posicoes],
            medicoes=medicoes,
            contagens=classificacao,
            sap=sap,
            saldo_selecionado=saldo,
            status_sap=classificar_sap(quantidades, saldo, classificacao, motivo),
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
            descricao=info['descricao'] or '',
            embalagem=info['embalagem'] or '',
            posicoes=[(primeiro.codigo_posicao, primeiro.descricao_posicao)],
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
    tem_referencia: bool,
) -> Acuracidade:
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
    if not tem_referencia:
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


def salvar_acuracidade(comparativo: ComparativoContagem, canal: str, usuario) -> ComparativoAcuracidade:
    """Grava o cálculo atual (somente leitura das contagens) como registro."""
    canal = Canal.normalizar(canal)
    dados = carregar_dados(comparativo)
    acuracidade = calcular_acuracidade(
        montar_linhas_sku(dados, canal), canal, comparativo.referencia_sap_id is not None,
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


@dataclass
class ResultadoComparativo:
    dados: DadosComparativo
    canal: str
    visao: str
    filtros: FiltrosComparativo
    linhas: list[LinhaComparativo]
    resumo: ResumoComparativo
    acuracidade: Acuracidade | None = None

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
    acuracidade = None
    if visao == Visao.POSICAO:
        todas = montar_linhas_posicao(dados)
    else:
        todas = montar_linhas_sku(dados, canal)
        acuracidade = calcular_acuracidade(
            todas, canal, comparativo.referencia_sap_id is not None,
        )
    return ResultadoComparativo(
        dados=dados,
        canal=canal,
        visao=visao,
        filtros=filtros,
        linhas=filtrar_linhas(todas, filtros),
        resumo=resumir(todas),
        acuracidade=acuracidade,
    )
