from dataclasses import dataclass, field

import pandas as pd
from django.db import transaction

from posicoes.models import Posicao

COLUNAS_OBRIGATORIAS = ['codigo', 'posicao']


@dataclass
class LinhaImportacao:
    linha: int
    codigo: str
    posicao: str
    valida: bool
    erros: list[str] = field(default_factory=list)


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


def _validar_linha(linha: LinhaImportacao) -> LinhaImportacao:
    erros = []

    if not linha.codigo:
        erros.append('Código é obrigatório.')
    if not linha.posicao:
        erros.append('Posição é obrigatória.')

    linha.valida = not erros
    linha.erros = erros
    return linha


def processar_arquivo(arquivo) -> ResultadoPreview:
    dataframe = pd.read_excel(arquivo, dtype=str)
    dataframe = _normalizar_colunas(dataframe)
    _validar_colunas(dataframe)
    dataframe = dataframe.fillna('')

    linhas = []
    for indice, registro in dataframe.iterrows():
        linha = LinhaImportacao(
            linha=int(indice) + 2,
            codigo=_limpar_valor(registro.get('codigo')),
            posicao=_limpar_valor(registro.get('posicao')),
            valida=False,
        )
        linhas.append(_validar_linha(linha))

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
        }
        for linha in preview.linhas
        if linha.valida
    ]


@transaction.atomic
def importar_dados(linhas_validas: list[dict], rejeitados: int = 0) -> ResultadoImportacao:
    inseridos = 0
    atualizados = 0

    for dados in linhas_validas:
        posicao, criada = Posicao.objects.update_or_create(
            codigo=dados['codigo'],
            defaults={
                'posicao': dados['posicao'],
            },
        )

        if criada:
            inseridos += 1
        else:
            atualizados += 1

    return ResultadoImportacao(
        inseridos=inseridos,
        atualizados=atualizados,
        rejeitados=rejeitados,
    )
