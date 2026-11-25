"""Categorias de inventário (Lubrificantes, Filtros, Agregados) — definição única.

Regras centrais:
- Lubrificantes é a categoria padrão inicial da sessão.
- A categoria escolhida fica na sessão do usuário; cada inventário grava a sua
  categoria de forma definitiva e as telas internas usam sempre a gravada.
- Não reutiliza o tipo interno GERAL/CICLICO (``tipo_inventario``).
- ``Produto.setor`` é texto livre legado: a categoria é *interpretada* a partir
  dele por compatibilidade, sem nunca regravar o campo. Valores ambíguos ou
  desconhecidos (ex.: ``PENDENTE``, ``A``, vazio) ficam sem classificação.
"""
from __future__ import annotations

import re
import unicodedata

from django.db import models
from django.db.models import Q


class CategoriaInventario(models.TextChoices):
    LUBRIFICANTES = 'LUBRIFICANTES', 'Lubrificantes'
    FILTROS = 'FILTROS', 'Filtros'
    AGREGADO = 'AGREGADO', 'Agregados'


CATEGORIA_PADRAO = CategoriaInventario.LUBRIFICANTES
CATEGORIA_CHOICES = CategoriaInventario.choices
CATEGORIAS_VALIDAS = frozenset(CategoriaInventario.values)
MAX_LENGTH_CATEGORIA = 20

SESSAO_CHAVE_CATEGORIA = 'categoria_inventario'

ROTULO_SEM_CLASSIFICACAO = 'Sem classificação'

# Valores de ``Produto.setor`` que identificam, sem ambiguidade, cada categoria.
# A comparação ignora espaços nas pontas, maiúsculas/minúsculas e acentos.
# ``A`` NÃO significa Agregado e ``PENDENTE`` não é categoria.
SETORES_POR_CATEGORIA: dict[str, tuple[str, ...]] = {
    CategoriaInventario.LUBRIFICANTES: ('LUBRIFICANTE', 'LUBRIFICANTES'),
    CategoriaInventario.FILTROS: ('FILTRO', 'FILTROS'),
    CategoriaInventario.AGREGADO: ('AGREGADO', 'AGREGADOS'),
}


def normalizar_categoria(valor) -> str | None:
    """Retorna o código da categoria se ``valor`` for um código válido; senão None."""
    if valor is None:
        return None
    texto = str(valor).strip().upper()
    return texto if texto in CATEGORIAS_VALIDAS else None


def validar_categoria(valor) -> str:
    categoria = normalizar_categoria(valor)
    if categoria is None:
        raise ValueError(f'Categoria de inventário inválida: {valor!r}.')
    return categoria


def rotulo_categoria(valor) -> str:
    categoria = normalizar_categoria(valor)
    if categoria is None:
        return ROTULO_SEM_CLASSIFICACAO
    return CategoriaInventario(categoria).label


def _normalizar_setor(setor) -> str:
    if setor is None:
        return ''
    texto = unicodedata.normalize('NFKD', str(setor))
    texto = ''.join(c for c in texto if not unicodedata.combining(c))
    return ' '.join(texto.split()).upper()


def categoria_do_setor(setor) -> str | None:
    """Categoria inferida do ``Produto.setor`` legado, ou None (sem classificação)."""
    normalizado = _normalizar_setor(setor)
    if not normalizado:
        return None
    for categoria, setores in SETORES_POR_CATEGORIA.items():
        if normalizado in setores:
            return str(categoria)
    return None


def interpretar_categoria_informada(valor) -> str | None:
    """Categoria escolhida explicitamente (código ou nome; ignora caixa e acento).

    Usada em cadastros/importações onde o usuário informa a categoria. Aceita
    ``LUBRIFICANTES``/``Lubrificantes``/``Lubrificante``, ``Filtros``/``Filtro``,
    ``Agregado``/``Agregados``. Qualquer outro valor retorna None.
    """
    return categoria_do_setor(valor)


def categoria_do_produto(produto) -> str | None:
    return categoria_do_setor(getattr(produto, 'setor', None))


def _regex_setores(categoria: str) -> str:
    setores = SETORES_POR_CATEGORIA[validar_categoria(categoria)]
    alternativas = '|'.join(re.escape(s) for s in setores)
    # Espaços nas pontas e caixa são ignorados (iregex). Acentos não ocorrem
    # nos valores canônicos acima.
    return rf'^\s*({alternativas})\s*$'


def q_produto_da_categoria(categoria: str, prefixo: str = '') -> Q:
    """Q que filtra produtos (ou relações via ``prefixo``) classificados na categoria."""
    return Q(**{f'{prefixo}setor__iregex': _regex_setores(categoria)})


def q_produto_classificado(prefixo: str = '') -> Q:
    consulta = Q()
    for categoria in CategoriaInventario.values:
        consulta |= q_produto_da_categoria(categoria, prefixo)
    return consulta


def q_produto_sem_classificacao(prefixo: str = '') -> Q:
    return ~q_produto_classificado(prefixo)


# --- Sessão -----------------------------------------------------------------

def obter_categoria_sessao(request) -> str:
    """Categoria selecionada na sessão; Lubrificantes quando ainda não escolhida."""
    sessao = getattr(request, 'session', None)
    if sessao is None:
        return str(CATEGORIA_PADRAO)
    return normalizar_categoria(sessao.get(SESSAO_CHAVE_CATEGORIA)) or str(CATEGORIA_PADRAO)


def definir_categoria_sessao(request, valor) -> str:
    categoria = validar_categoria(valor)
    request.session[SESSAO_CHAVE_CATEGORIA] = categoria
    return categoria


def alinhar_categoria_sessao(request, categoria) -> bool:
    """Tela interna aberta direto (link/URL) de outra categoria: a sessão passa
    a usar a categoria gravada no objeto, para que o seletor e o conteúdo nunca
    mostrem categorias diferentes. Retorna True quando houve troca (com aviso).
    """
    categoria = normalizar_categoria(categoria)
    sessao = getattr(request, 'session', None)
    if categoria is None or sessao is None:
        return False
    if obter_categoria_sessao(request) == categoria:
        return False
    sessao[SESSAO_CHAVE_CATEGORIA] = categoria
    try:
        from django.contrib import messages

        messages.info(
            request,
            f'Categoria alterada para {rotulo_categoria(categoria)} conforme o registro aberto.',
        )
    except Exception:  # pragma: no cover - requisição sem framework de mensagens
        pass
    return True


def opcoes_categoria() -> list[dict]:
    return [{'valor': valor, 'rotulo': rotulo} for valor, rotulo in CATEGORIA_CHOICES]
