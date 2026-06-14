from dataclasses import dataclass

import pandas as pd


@dataclass
class ResultadoPreview:
    total_linhas: int
    linhas_validas: int
    linhas_invalidas: int
    linhas: list


@dataclass
class ResultadoImportacao:
    inseridos: int
    atualizados: int
    rejeitados: int


def limpar_valor(valor) -> str:
    if pd.isna(valor):
        return ''
    return str(valor).strip()


def normalizar_colunas(dataframe: pd.DataFrame) -> pd.DataFrame:
    dataframe.columns = [str(coluna).strip().lower() for coluna in dataframe.columns]
    return dataframe


def ler_planilha_excel(arquivo) -> pd.DataFrame:
    dataframe = pd.read_excel(arquivo, dtype=str)
    dataframe = normalizar_colunas(dataframe)
    return dataframe.fillna('')


def validar_colunas_obrigatorias(dataframe: pd.DataFrame, colunas: list[str]) -> None:
    ausentes = [coluna for coluna in colunas if coluna not in dataframe.columns]
    if ausentes:
        raise ValueError(
            f'Colunas obrigatórias ausentes: {", ".join(ausentes)}'
        )
