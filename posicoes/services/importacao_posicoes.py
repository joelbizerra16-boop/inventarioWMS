from dataclasses import dataclass, field

import pandas as pd
from django.db import transaction

from core.categorias import interpretar_categoria_informada, rotulo_categoria
from posicoes.models import Posicao

COLUNA_CATEGORIA = 'categoria'
COLUNAS_OBRIGATORIAS = ['codigo', 'posicao', COLUNA_CATEGORIA]


@dataclass
class LinhaImportacao:
    linha: int
    codigo: str
    posicao: str
    valida: bool
    erros: list[str] = field(default_factory=list)
    categoria: str = ''
    categoria_informada: str = ''


@dataclass
class ResultadoPreview:
    total_linhas: int
    linhas_validas: int
    linhas_invalidas: int
    linhas: list[LinhaImportacao]


@dataclass
class ResultadoImportacao:
    inseridos: int
    atualizados: int
    rejeitados: int


def _limpar_valor(valor) -> str:
    if pd.isna(valor):
        return ''
    return str(valor).strip()


def _normalizar_colunas(dataframe: pd.DataFrame) -> pd.DataFrame:
    dataframe.columns = [str(coluna).strip().lower() for coluna in dataframe.columns]
    return dataframe


def _validar_colunas(dataframe: pd.DataFrame) -> None:
    ausentes = [
        coluna for coluna in COLUNAS_OBRIGATORIAS if coluna not in dataframe.columns
    ]
    if ausentes:
        raise ValueError(
            f'Colunas obrigatórias ausentes: {", ".join(ausentes)}'
        )


def _erro_categoria(codigo: str, categoria: str, existentes: dict[str, str]) -> str | None:
    """Regras de categoria da posição na importação (sem alteração silenciosa).

    - Toda linha exige categoria válida (Lubrificantes, Filtros ou Agregados).
    - Posição existente mantém a sua categoria; informar outra é erro claro.
    """
    if not categoria:
        return 'Categoria é obrigatória (Lubrificantes, Filtros ou Agregados).'
    atual = existentes.get(codigo.upper())
    if atual is None:
        return None
    if categoria != atual:
        return (
            f'Código {codigo} já pertence a {rotulo_categoria(atual)}; '
            f'não pode ser importado como {rotulo_categoria(categoria)}.'
        )
    return None


def _validar_linha(linha: LinhaImportacao, existentes: dict[str, str] | None = None) -> LinhaImportacao:
    erros = []

    if not linha.codigo:
        erros.append('Código é obrigatório.')
    if not linha.posicao:
        erros.append('Posição é obrigatória.')
    if linha.categoria_informada and not linha.categoria:
        erros.append(
            f'Categoria inválida: {linha.categoria_informada!r} '
            '(use Lubrificantes, Filtros ou Agregados).'
        )
    elif linha.codigo and existentes is not None:
        erro = _erro_categoria(linha.codigo, linha.categoria, existentes)
        if erro:
            erros.append(erro)

    linha.valida = not erros
    linha.erros = erros
    return linha


def _mapa_categorias_existentes(codigos) -> dict[str, str]:
    codigos = {c.upper() for c in codigos if c}
    if not codigos:
        return {}
    mapa = {}
    for codigo, categoria in Posicao.objects.values_list('codigo', 'categoria'):
        if codigo.upper() in codigos:
            mapa[codigo.upper()] = categoria
    return mapa


def processar_arquivo(arquivo) -> ResultadoPreview:
    dataframe = pd.read_excel(arquivo, dtype=str)
    dataframe = _normalizar_colunas(dataframe)
    _validar_colunas(dataframe)
    dataframe = dataframe.fillna('')

    brutas = []
    for indice, registro in dataframe.iterrows():
        informada = _limpar_valor(registro.get(COLUNA_CATEGORIA, ''))
        brutas.append(LinhaImportacao(
            linha=int(indice) + 2,
            codigo=_limpar_valor(registro.get('codigo')),
            posicao=_limpar_valor(registro.get('posicao')),
            valida=False,
            categoria=interpretar_categoria_informada(informada) or '',
            categoria_informada=informada,
        ))

    existentes = _mapa_categorias_existentes(linha.codigo for linha in brutas)
    linhas = [_validar_linha(linha, existentes) for linha in brutas]

    validas = sum(1 for linha in linhas if linha.valida)
    invalidas = len(linhas) - validas

    return ResultadoPreview(
        total_linhas=len(linhas),
        linhas_validas=validas,
        linhas_invalidas=invalidas,
        linhas=linhas,
    )


def serializar_linhas_validas(preview: ResultadoPreview) -> list[dict]:
    return [
        {
            'codigo': linha.codigo,
            'posicao': linha.posicao,
            'categoria': linha.categoria,
        }
        for linha in preview.linhas
        if linha.valida
    ]


@transaction.atomic
def importar_dados(linhas_validas: list[dict], rejeitados: int = 0) -> ResultadoImportacao:
    inseridos = 0
    atualizados = 0

    for dados in linhas_validas:
        categoria = dados.get('categoria') or ''
        existente = Posicao.objects.select_for_update().filter(codigo=dados['codigo']).first()
        # Revalida na confirmação: o cadastro pode ter mudado após a prévia.
        if not categoria:
            rejeitados += 1
            continue
        if existente is None:
            Posicao.objects.create(
                codigo=dados['codigo'],
                posicao=dados['posicao'],
                categoria=categoria,
            )
            inseridos += 1
            continue
        if categoria != existente.categoria:
            rejeitados += 1
            continue
        existente.posicao = dados['posicao']
        existente.save(update_fields=['posicao'])
        atualizados += 1

    return ResultadoImportacao(
        inseridos=inseridos,
        atualizados=atualizados,
        rejeitados=rejeitados,
    )
