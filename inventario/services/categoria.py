"""Categoria efetiva do produto e escopo de categoria — serviço único.

Toda regra de "a qual categoria um produto pertence" e "qual produto pode
entrar em qual inventário" passa por aqui, para que web, Pocket, tarefas,
locks, relatórios, Confronto, Comparativo e publicação usem o mesmo critério.

Ordem de decisão da categoria efetiva (nunca regrava ``Produto.setor`` e
nunca usa nome/descrição):

1. ``Produto.setor`` com categoria canônica válida → categoria oficial.
2. Sem classificação → categoria da(s) posição(ões) onde o produto está
   fisicamente: estoque físico publicado ou contagem em inventário não
   finalizado. Uma única categoria → categoria efetiva. Mais de uma →
   CONFLITO (nenhuma é escolhida silenciosamente).
3. Sem posição → inclusão explícita vale somente no inventário onde foi feita.
4. Sem setor válido, posição ou inclusão → "Sem classificação".

Produto oficialmente classificado em posição de outra categoria é
INCOMPATÍVEL e bloqueia a operação.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from django.db import IntegrityError, transaction
from django.db.models import Q

from core.categorias import (
    CategoriaInventario,
    categoria_do_produto,
    q_produto_classificado,
    q_produto_da_categoria,
    rotulo_categoria,
    validar_categoria,
)


class CategoriaError(Exception):
    """Violação de escopo de categoria (mensagem pronta para o usuário)."""


class OrigemCategoria:
    OFICIAL = 'OFICIAL'
    POSICAO = 'POSICAO'
    INCLUSAO = 'INCLUSAO'
    CONFLITO = 'CONFLITO'
    SEM_CLASSIFICACAO = 'SEM_CLASSIFICACAO'

    ROTULOS = {
        OFICIAL: 'Setor oficial',
        POSICAO: 'Pela posição física',
        INCLUSAO: 'Inclusão explícita no inventário',
        CONFLITO: 'Conflito de categoria',
        SEM_CLASSIFICACAO: 'Sem classificação',
    }


@dataclass(frozen=True)
class CategoriaEfetiva:
    produto_id: int
    categoria: str | None
    origem: str
    categoria_oficial: str | None = None
    categorias_fisicas: frozenset = field(default_factory=frozenset)

    @property
    def conflito(self) -> bool:
        """Sem classificação e localizado em mais de uma categoria."""
        return self.origem == OrigemCategoria.CONFLITO

    @property
    def incompativel(self) -> bool:
        """Classificado oficialmente, mas localizado em posição de outra categoria."""
        return bool(
            self.categoria_oficial
            and self.categorias_fisicas - {self.categoria_oficial}
        )

    @property
    def rotulo(self) -> str:
        if self.conflito:
            categorias = ', '.join(rotulo_categoria(c) for c in sorted(self.categorias_fisicas))
            return f'Conflito ({categorias})'
        return rotulo_categoria(self.categoria)

    @property
    def origem_rotulo(self) -> str:
        return OrigemCategoria.ROTULOS.get(self.origem, self.origem)


# --- Evidência física (posição) ---------------------------------------------

def _estoque_fisico_por_categoria(categoria: str):
    from estoque_fisico.models import EstoqueFisico

    return EstoqueFisico.objects.filter(posicao__categoria=categoria).values('produto_id')


def _contagens_abertas_por_categoria(categoria: str):
    from inventario.models import Inventario, InventarioItem

    return (
        InventarioItem.objects.filter(posicao__categoria=categoria)
        .exclude(inventario__status=Inventario.Status.FINALIZADO)
        .values('produto_id')
    )


def q_evidencia_fisica(categoria: str, prefixo: str = '') -> Q:
    """Produto (via ``prefixo``) está fisicamente em posição da categoria."""
    categoria = validar_categoria(categoria)
    return (
        Q(**{f'{prefixo}pk__in': _estoque_fisico_por_categoria(categoria)})
        | Q(**{f'{prefixo}pk__in': _contagens_abertas_por_categoria(categoria)})
    )


def q_evidencia_fisica_qualquer(prefixo: str = '') -> Q:
    consulta = Q()
    for categoria in CategoriaInventario.values:
        consulta |= q_evidencia_fisica(categoria, prefixo)
    return consulta


def categorias_fisicas_por_produto(produto_ids=None) -> dict[int, set[str]]:
    """Mapa produto → categorias das posições onde está fisicamente."""
    from estoque_fisico.models import EstoqueFisico
    from inventario.models import Inventario, InventarioItem

    estoques = EstoqueFisico.objects.all()
    contagens = InventarioItem.objects.exclude(inventario__status=Inventario.Status.FINALIZADO)
    if produto_ids is not None:
        produto_ids = list(produto_ids)
        estoques = estoques.filter(produto_id__in=produto_ids)
        contagens = contagens.filter(produto_id__in=produto_ids)
    mapa: dict[int, set[str]] = {}
    for consulta in (estoques, contagens):
        for produto_id, categoria in consulta.values_list('produto_id', 'posicao__categoria').distinct():
            mapa.setdefault(produto_id, set()).add(categoria)
    return mapa


# --- Categoria efetiva ------------------------------------------------------

def _resolver(produto_id: int, setor, fisicas: set[str]) -> CategoriaEfetiva:
    from core.categorias import categoria_do_setor

    oficial = categoria_do_setor(setor)
    fisicas = frozenset(fisicas or ())
    if oficial is not None:
        return CategoriaEfetiva(produto_id, oficial, OrigemCategoria.OFICIAL, oficial, fisicas)
    if len(fisicas) == 1:
        return CategoriaEfetiva(produto_id, next(iter(fisicas)), OrigemCategoria.POSICAO, None, fisicas)
    if len(fisicas) > 1:
        return CategoriaEfetiva(produto_id, None, OrigemCategoria.CONFLITO, None, fisicas)
    return CategoriaEfetiva(produto_id, None, OrigemCategoria.SEM_CLASSIFICACAO, None, fisicas)


def categoria_efetiva(produto, inventario=None) -> CategoriaEfetiva:
    """Categoria efetiva de um produto (com inclusão do inventário, se houver)."""
    fisicas = categorias_fisicas_por_produto([produto.pk]).get(produto.pk, set())
    efetiva = _resolver(produto.pk, produto.setor, fisicas)
    if (
        inventario is not None
        and efetiva.origem == OrigemCategoria.SEM_CLASSIFICACAO
        and produto_incluido_explicitamente(inventario, produto)
    ):
        return CategoriaEfetiva(produto.pk, inventario.categoria, OrigemCategoria.INCLUSAO, None, efetiva.categorias_fisicas)
    return efetiva


def categorias_efetivas(produtos) -> dict[int, CategoriaEfetiva]:
    """Versão em lote: ``produtos`` é iterável de Produto (ou dicts com id/setor)."""
    itens = [
        (p['id'], p.get('setor')) if isinstance(p, dict) else (p.pk, p.setor)
        for p in produtos
    ]
    fisicas = categorias_fisicas_por_produto([pid for pid, _ in itens])
    return {pid: _resolver(pid, setor, fisicas.get(pid, set())) for pid, setor in itens}


def anotar_categoria_efetiva(produtos) -> list:
    """Anexa ``categoria_efetiva`` a cada Produto (para telas e exportações)."""
    produtos = list(produtos)
    mapa = categorias_efetivas(produtos)
    for produto in produtos:
        produto.categoria_efetiva = mapa[produto.pk]
    return produtos


def q_produto_categoria_efetiva(categoria: str, prefixo: str = '') -> Q:
    """Produtos que pertencem SOMENTE a esta categoria.

    - Oficiais: setor válido da categoria (independe da posição).
    - Sem classificação: localizados em posição desta categoria e em nenhuma
      posição de outra. Produtos em conflito NÃO aparecem em nenhuma categoria;
      ficam na visão própria de conflitos (``q_produto_conflito_categoria``).
    """
    categoria = validar_categoria(categoria)
    somente_nesta = ~q_produto_classificado(prefixo) & q_evidencia_fisica(categoria, prefixo)
    for outra in CategoriaInventario.values:
        if outra != categoria:
            somente_nesta &= ~q_evidencia_fisica(outra, prefixo)
    return q_produto_da_categoria(categoria, prefixo) | somente_nesta


def q_produto_conflito_categoria(categoria: str | None = None, prefixo: str = '') -> Q:
    """Sem classificação em posições de duas ou mais categorias (conflito).

    Com ``categoria``: somente conflitos que envolvem essa categoria.
    """
    categorias = list(CategoriaInventario.values)
    pares = Q(**{f'{prefixo}pk__in': []})
    for indice, primeira in enumerate(categorias):
        for segunda in categorias[indice + 1:]:
            if categoria and categoria not in (primeira, segunda):
                continue
            pares |= q_evidencia_fisica(primeira, prefixo) & q_evidencia_fisica(segunda, prefixo)
    return ~q_produto_classificado(prefixo) & pares


def q_produto_sem_classificacao_efetiva(prefixo: str = '') -> Q:
    """Sem setor válido e sem posição física em nenhuma categoria."""
    return ~q_produto_classificado(prefixo) & ~q_evidencia_fisica_qualquer(prefixo)


def listar_conflitos_categoria(categoria: str | None = None):
    """Conflitos para a tela administrativa.

    - CONFLITO: sem classificação em posições de categorias diferentes.
    - INCOMPATÍVEL: classificado oficialmente em posição de outra categoria.

    Com ``categoria``: somente os que envolvem essa categoria (oficial ou
    posição), para que a sessão não mostre conflitos de outras categorias.
    """
    from produtos.models import Produto

    fisicas = categorias_fisicas_por_produto()
    candidatos = Produto.objects.filter(pk__in=list(fisicas)).only('pk', 'codigo_produto', 'descricao', 'setor')
    conflitos = []
    for produto in candidatos.order_by('codigo_produto'):
        efetiva = _resolver(produto.pk, produto.setor, fisicas.get(produto.pk, set()))
        if not (efetiva.conflito or efetiva.incompativel):
            continue
        envolvidas = set(efetiva.categorias_fisicas) | ({efetiva.categoria_oficial} - {None})
        if categoria and categoria not in envolvidas:
            continue
        produto.categoria_efetiva = efetiva
        conflitos.append(produto)
    return conflitos


# --- Escopo do inventário -----------------------------------------------------

def q_produtos_do_inventario(inventario, prefixo: str = '') -> Q:
    """Produtos do escopo do inventário: categoria efetiva + inclusões dele."""
    from inventario.models import InventarioProdutoInclusao

    consulta = q_produto_categoria_efetiva(inventario.categoria, prefixo)
    if inventario.pk:
        # Subconsulta (não join) para não duplicar linhas da consulta externa.
        incluidos = InventarioProdutoInclusao.objects.filter(
            inventario_id=inventario.pk,
        ).values('produto_id')
        consulta |= Q(**{f'{prefixo}pk__in': incluidos})
    return consulta


def q_produtos_contaveis_no_inventario(inventario, prefixo: str = '') -> Q:
    """Produtos que podem ser bipados/selecionados para contagem.

    Escopo do inventário + sem classificação ainda sem posição física (a
    posição da contagem, da mesma categoria, passa a ser a evidência).
    """
    return q_produtos_do_inventario(inventario, prefixo) | q_produto_sem_classificacao_efetiva(prefixo)


def produtos_do_inventario(inventario, queryset=None):
    from produtos.models import Produto

    base = queryset if queryset is not None else Produto.objects.elegiveis_para_inventario()
    return base.filter(q_produtos_do_inventario(inventario))


def produtos_contaveis_no_inventario(inventario, queryset=None):
    from produtos.models import Produto

    base = queryset if queryset is not None else Produto.objects.elegiveis_para_inventario()
    return base.filter(q_produtos_contaveis_no_inventario(inventario))


def produto_incluido_explicitamente(inventario, produto) -> bool:
    from inventario.models import InventarioProdutoInclusao

    if not inventario.pk or produto is None or not getattr(produto, 'pk', None):
        return False
    return InventarioProdutoInclusao.objects.filter(
        inventario_id=inventario.pk,
        produto_id=produto.pk,
    ).exists()


# --- Validações (backend) -----------------------------------------------------

def _mensagem_conflito(produto, categorias) -> str:
    nomes = ', '.join(rotulo_categoria(c) for c in sorted(categorias))
    return (
        f'Conflito de categoria: o produto {produto.codigo_produto} está sem classificação '
        f'e já se encontra em posições de {nomes}. Regularize o setor do produto ou as '
        'posições antes de continuar.'
    )


def validar_produto_no_inventario(inventario, produto, posicao=None) -> None:
    """Bloqueia produto incompatível com a categoria do inventário.

    - Oficial de outra categoria → bloqueado (incompatível).
    - Sem classificação localizado em outra categoria → bloqueado (conflito).
    - Sem classificação sem posição → aceito: a posição da contagem (da mesma
      categoria do inventário) passa a ser a evidência física.
    """
    if produto is None:
        return
    rotulo_inv = rotulo_categoria(inventario.categoria)
    oficial = categoria_do_produto(produto)
    if oficial is not None:
        if oficial != inventario.categoria:
            raise CategoriaError(
                f'Produto {produto.codigo_produto} pertence a {rotulo_categoria(oficial)} '
                f'e não pode ser contado em inventário de {rotulo_inv}.'
            )
        return
    fisicas = categorias_fisicas_por_produto([produto.pk]).get(produto.pk, set())
    outras = fisicas - {inventario.categoria}
    if outras:
        raise CategoriaError(_mensagem_conflito(produto, fisicas | {inventario.categoria}))


def validar_posicao_no_inventario(inventario, posicao) -> None:
    """Bloqueia posição de outra categoria (área de inventário da posição)."""
    if posicao is None:
        return
    categoria_posicao = getattr(posicao, 'categoria', None)
    if categoria_posicao is None:
        return
    if categoria_posicao != inventario.categoria:
        raise CategoriaError(
            f'Posição {posicao.codigo} pertence a {rotulo_categoria(categoria_posicao)} '
            f'e não pode ser usada em inventário de {rotulo_categoria(inventario.categoria)}.'
        )


def validar_contagem_na_categoria(inventario, posicao, produto) -> None:
    validar_posicao_no_inventario(inventario, posicao)
    validar_produto_no_inventario(inventario, produto, posicao)


def validar_publicacao_categoria(inventario, agregados: list[dict]) -> None:
    """Valida, antes de apagar qualquer saldo, todos os itens a publicar.

    Bloqueia (atomicamente, sem alterar nada) posição de outra categoria,
    produto oficial incompatível e produto sem classificação em conflito.
    """
    from posicoes.models import Posicao
    from produtos.models import Produto

    if not agregados:
        return
    categoria = inventario.categoria
    posicao_ids = {item['posicao_id'] for item in agregados}
    produto_ids = {item['produto_id'] for item in agregados}
    problemas: list[str] = []
    for posicao in Posicao.objects.filter(pk__in=posicao_ids).exclude(categoria=categoria):
        problemas.append(
            f'posição {posicao.codigo} pertence a {rotulo_categoria(posicao.categoria)}'
        )
    fisicas = categorias_fisicas_por_produto(produto_ids)
    for produto in Produto.objects.filter(pk__in=produto_ids).only('pk', 'codigo_produto', 'setor'):
        oficial = categoria_do_produto(produto)
        if oficial is not None and oficial != categoria:
            problemas.append(
                f'produto {produto.codigo_produto} pertence a {rotulo_categoria(oficial)}'
            )
        elif oficial is None:
            outras = fisicas.get(produto.pk, set()) - {categoria}
            if outras:
                nomes = ', '.join(rotulo_categoria(c) for c in sorted(outras))
                problemas.append(
                    f'produto {produto.codigo_produto} sem classificação também está em {nomes} (conflito)'
                )
    if problemas:
        exemplos = '; '.join(problemas[:5])
        excedente = f' e mais {len(problemas) - 5}' if len(problemas) > 5 else ''
        raise CategoriaError(
            f'Publicação bloqueada por incompatibilidade de categoria ({len(problemas)}): '
            f'{exemplos}{excedente}. Nenhum estoque foi alterado.'
        )


def inventarios_da_categoria(queryset, categoria: str):
    return queryset.filter(categoria=validar_categoria(categoria))


# --- Inclusão explícita -------------------------------------------------------

class OrigemInclusao:
    WEB = 'WEB_INCLUSAO'
    POCKET_PRECADASTRO = 'POCKET_PRECADASTRO'


def incluir_produto_sem_classificacao(inventario, produto, usuario, origem: str = OrigemInclusao.WEB):
    """Inclui explicitamente um produto sem classificação SOMENTE neste inventário.

    Não altera ``Produto.setor``. Produto oficialmente classificado não precisa
    (nem pode) ser incluído; produto em conflito ou localizado em outra
    categoria é recusado.
    """
    from inventario.models import Inventario, InventarioProdutoInclusao

    if inventario.status == Inventario.Status.FINALIZADO:
        raise CategoriaError('Inventário finalizado não pode receber novos produtos.')
    oficial = categoria_do_produto(produto)
    if oficial is not None:
        if oficial == inventario.categoria:
            raise CategoriaError(
                f'Produto {produto.codigo_produto} já pertence a {rotulo_categoria(oficial)}; '
                'não precisa de inclusão explícita.'
            )
        raise CategoriaError(
            f'Produto {produto.codigo_produto} pertence a {rotulo_categoria(oficial)} '
            f'e não pode ser incluído em inventário de {rotulo_categoria(inventario.categoria)}.'
        )
    validar_produto_no_inventario(inventario, produto)
    try:
        with transaction.atomic():
            inclusao, criada = InventarioProdutoInclusao.objects.get_or_create(
                inventario=inventario,
                produto=produto,
                defaults={
                    'usuario': usuario,
                    'setor_no_momento': (produto.setor or '')[:100],
                    'origem': origem,
                },
            )
    except IntegrityError:
        inclusao = InventarioProdutoInclusao.objects.get(inventario=inventario, produto=produto)
        criada = False
    return inclusao, criada


def produtos_sem_classificacao(queryset=None):
    """Produtos sem setor válido e sem posição física (filtro "Sem classificação")."""
    from produtos.models import Produto

    base = queryset if queryset is not None else Produto.objects.all()
    return base.filter(q_produto_sem_classificacao_efetiva())
