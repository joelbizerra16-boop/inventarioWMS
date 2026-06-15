import logging
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
from django.db import transaction
from django.utils import timezone

from produtos.models import Produto

logger = logging.getLogger(__name__)

BATCH_SIZE_IMPORTACAO = 500

COLUNAS_OBRIGATORIAS = [
    'codigo_produto',
    'descricao',
    'embalagem',
    'setor',
]

COLUNAS_OBRIGATORIAS_EXIBICAO = {
    'codigo_produto': 'Cod_prod',
    'descricao': 'Descrição',
    'embalagem': 'Embalagem',
    'setor': 'SETOR',
}

EXTENSOES_PERMITIDAS = frozenset({'xlsx', 'xls'})

MAPEAMENTO_COLUNAS = {
    'cod_prod': 'codigo_produto',
    'cod prod': 'codigo_produto',
    'cod': 'codigo_produto',
    'codigo': 'codigo_produto',
    'codigo produto': 'codigo_produto',
    'codigo_produto': 'codigo_produto',
    'descricao': 'descricao',
    'embalagem': 'embalagem',
    'codigo ean 13 (un)': 'ean',
    'codigo ean': 'ean',
    'codigo_ean': 'ean',
    'ean': 'ean',
    'setor': 'setor',
    'empresa': 'empresa_legado',
}


class ImportacaoProdutosError(Exception):
    """Erro de validação do arquivo de importação com mensagem amigável."""

    def __init__(self, mensagem: str):
        self.mensagem = mensagem
        super().__init__(mensagem)


@dataclass
class LinhaImportacao:
    linha: int
    codigo_produto: str
    descricao: str
    embalagem: str
    setor: str
    codigo_ean: str
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
    texto = str(valor).strip()
    if texto.lower() in ('nan', 'none'):
        return ''
    return texto


def _normalizar_valor_ean(valor) -> str:
    if pd.isna(valor) or valor is None:
        return ''

    if isinstance(valor, bool):
        return ''

    if isinstance(valor, int):
        return str(valor)

    if isinstance(valor, float):
        if valor.is_integer():
            return str(int(valor))
        return format(valor, '.0f').rstrip('0').rstrip('.')

    texto = _limpar_valor(valor)
    if not texto:
        return ''

    if texto.endswith('.0') and texto[:-2].isdigit():
        return texto[:-2]

    try:
        if 'e' in texto.lower():
            numero = float(texto)
            if numero.is_integer():
                return str(int(numero))
    except ValueError:
        pass

    return texto


def _ler_valor_ean(registro) -> str:
    for chave in ('ean', 'codigo_ean'):
        if chave in registro and not pd.isna(registro.get(chave)):
            valor = _normalizar_valor_ean(registro.get(chave))
            if valor:
                return valor
    return ''


def _normalizar_nome_coluna(nome: str) -> str:
    texto = str(nome).strip().lower()
    texto = unicodedata.normalize('NFKD', texto)
    texto = ''.join(caractere for caractere in texto if not unicodedata.combining(caractere))
    texto = re.sub(r'\s+', ' ', texto).strip()
    texto = texto.rstrip('.')
    return texto


def _detectar_extensao(nome_arquivo: str) -> str:
    return Path(nome_arquivo or '').suffix.lower().lstrip('.')


def _mensagem_coluna_ausente(coluna: str) -> str:
    nome_exibicao = COLUNAS_OBRIGATORIAS_EXIBICAO.get(coluna, coluna)
    if coluna == 'setor':
        return f'Coluna {nome_exibicao} ausente.'
    return f'Coluna {nome_exibicao} não encontrada.'


def _log_diagnostico_importacao(**contexto) -> None:
    logger.info(
        '[importacao_produtos] diagnostico: nome_arquivo=%s extensao=%s abas=%s '
        'colunas_originais=%s colunas_normalizadas=%s colunas_obrigatorias=%s motivo=%s',
        contexto.get('nome_arquivo'),
        contexto.get('extensao'),
        contexto.get('abas'),
        contexto.get('colunas_originais'),
        contexto.get('colunas_normalizadas'),
        list(COLUNAS_OBRIGATORIAS_EXIBICAO.values()),
        contexto.get('motivo'),
    )


def _normalizar_colunas(dataframe: pd.DataFrame) -> pd.DataFrame:
    renomear = {}
    for coluna in dataframe.columns:
        chave = _normalizar_nome_coluna(coluna)
        renomear[coluna] = MAPEAMENTO_COLUNAS.get(chave, chave)

    dataframe = dataframe.rename(columns=renomear)

    if 'empresa_legado' in dataframe.columns and 'setor' not in dataframe.columns:
        dataframe = dataframe.rename(columns={'empresa_legado': 'setor'})

    return dataframe


def _validar_colunas(dataframe: pd.DataFrame) -> None:
    ausentes = [
        coluna for coluna in COLUNAS_OBRIGATORIAS if coluna not in dataframe.columns
    ]
    if not ausentes:
        return

    if len(ausentes) == 1:
        raise ImportacaoProdutosError(_mensagem_coluna_ausente(ausentes[0]))

    nomes = [
        COLUNAS_OBRIGATORIAS_EXIBICAO.get(coluna, coluna)
        for coluna in ausentes
    ]
    raise ImportacaoProdutosError(
        f'Colunas obrigatórias ausentes: {", ".join(nomes)}.'
    )


def _resolver_setor(registro) -> str:
    setor = _limpar_valor(registro.get('setor'))
    if setor:
        return setor
    return _limpar_valor(registro.get('empresa_legado'))


def _validar_linha(linha: LinhaImportacao) -> LinhaImportacao:
    erros = []

    if not linha.codigo_produto:
        erros.append('Código do produto é obrigatório.')
    if not linha.descricao:
        erros.append('Descrição é obrigatória.')
    if not linha.embalagem:
        erros.append('Embalagem é obrigatória.')
    if not linha.setor:
        erros.append('Setor é obrigatório.')

    linha.valida = not erros
    linha.erros = erros
    return linha


def processar_arquivo(arquivo) -> ResultadoPreview:
    nome_arquivo = getattr(arquivo, 'name', '') or ''
    extensao = _detectar_extensao(nome_arquivo)

    if extensao and extensao not in EXTENSOES_PERMITIDAS:
        _log_diagnostico_importacao(
            nome_arquivo=nome_arquivo,
            extensao=extensao,
            abas=None,
            colunas_originais=None,
            colunas_normalizadas=None,
            motivo='Extensão não permitida',
        )
        raise ImportacaoProdutosError('Arquivo não é XLSX.')

    try:
        excel = pd.ExcelFile(arquivo)
    except Exception as exc:
        _log_diagnostico_importacao(
            nome_arquivo=nome_arquivo,
            extensao=extensao,
            abas=None,
            colunas_originais=None,
            colunas_normalizadas=None,
            motivo=f'Falha ao abrir Excel: {exc}',
        )
        raise ImportacaoProdutosError('Arquivo não é XLSX.') from exc

    abas = list(excel.sheet_names)
    colunas_originais = None
    colunas_normalizadas = None

    try:
        dataframe = pd.read_excel(excel, dtype=str)
        colunas_originais = [str(coluna) for coluna in dataframe.columns]

        if dataframe.empty:
            _log_diagnostico_importacao(
                nome_arquivo=nome_arquivo,
                extensao=extensao,
                abas=abas,
                colunas_originais=colunas_originais,
                colunas_normalizadas=None,
                motivo='Planilha vazia',
            )
            raise ImportacaoProdutosError('Planilha vazia.')

        dataframe = _normalizar_colunas(dataframe)
        if 'ean' in dataframe.columns:
            dataframe['ean'] = [
                _normalizar_valor_ean(valor)
                for valor in dataframe['ean'].tolist()
            ]
        colunas_normalizadas = [str(coluna) for coluna in dataframe.columns]
        _validar_colunas(dataframe)
    except ImportacaoProdutosError as exc:
        _log_diagnostico_importacao(
            nome_arquivo=nome_arquivo,
            extensao=extensao,
            abas=abas,
            colunas_originais=colunas_originais,
            colunas_normalizadas=colunas_normalizadas,
            motivo=exc.mensagem,
        )
        raise

    dataframe = dataframe.fillna('')

    linhas = []
    for indice, registro in dataframe.iterrows():
        codigo_produto = _limpar_valor(registro.get('codigo_produto'))
        codigo_ean = _ler_valor_ean(registro)
        linha = LinhaImportacao(
            linha=int(indice) + 2,
            codigo_produto=codigo_produto,
            descricao=_limpar_valor(registro.get('descricao')),
            embalagem=_limpar_valor(registro.get('embalagem')),
            setor=_resolver_setor(registro),
            codigo_ean=codigo_ean,
            valida=False,
        )
        if codigo_produto == '110267':
            logger.info(
                '[importacao_produtos] Produto: %s EAN lido: %s',
                codigo_produto,
                codigo_ean or '—',
            )
        linhas.append(_validar_linha(linha))

    if not linhas:
        _log_diagnostico_importacao(
            nome_arquivo=nome_arquivo,
            extensao=extensao,
            abas=abas,
            colunas_originais=colunas_originais,
            colunas_normalizadas=colunas_normalizadas,
            motivo='Planilha vazia',
        )
        raise ImportacaoProdutosError('Planilha vazia.')

    _log_diagnostico_importacao(
        nome_arquivo=nome_arquivo,
        extensao=extensao,
        abas=abas,
        colunas_originais=colunas_originais,
        colunas_normalizadas=colunas_normalizadas,
        motivo='Arquivo validado com sucesso',
    )

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
            'codigo_produto': linha.codigo_produto,
            'descricao': linha.descricao,
            'embalagem': linha.embalagem,
            'setor': linha.setor,
            'codigo_ean': linha.codigo_ean,
        }
        for linha in preview.linhas
        if linha.valida
    ]


@transaction.atomic
def importar_dados(linhas_validas: list[dict], rejeitados: int = 0) -> ResultadoImportacao:
    if not linhas_validas:
        return ResultadoImportacao(inseridos=0, atualizados=0, rejeitados=rejeitados)

    agora = timezone.now()
    inseridos = 0
    atualizados = 0

    codigos_unicos = {dados['codigo_produto'] for dados in linhas_validas}
    existentes = {
        produto.codigo_produto: produto
        for produto in Produto.objects.filter(codigo_produto__in=codigos_unicos)
    }

    criar: list[Produto] = []
    criados_no_lote: dict[str, Produto] = {}
    atualizar: dict[str, Produto] = {}

    for dados in linhas_validas:
        codigo = dados['codigo_produto']
        codigo_ean = _normalizar_valor_ean(dados.get('codigo_ean')) or None
        produto = existentes.get(codigo) or criados_no_lote.get(codigo)

        if produto is None:
            produto = Produto(
                codigo_produto=codigo,
                descricao=dados['descricao'],
                embalagem=dados['embalagem'],
                setor=dados['setor'],
                codigo_ean=codigo_ean,
                data_criacao=agora,
                data_atualizacao=agora,
            )
            criar.append(produto)
            criados_no_lote[codigo] = produto
            inseridos += 1
            continue

        produto.descricao = dados['descricao']
        produto.embalagem = dados['embalagem']
        produto.setor = dados['setor']
        produto.codigo_ean = codigo_ean
        produto.data_atualizacao = agora
        if codigo in existentes:
            atualizar[codigo] = produto
        atualizados += 1

    if criar:
        Produto.objects.bulk_create(criar, batch_size=BATCH_SIZE_IMPORTACAO)
    if atualizar:
        Produto.objects.bulk_update(
            list(atualizar.values()),
            ['descricao', 'embalagem', 'setor', 'codigo_ean', 'data_atualizacao'],
            batch_size=BATCH_SIZE_IMPORTACAO,
        )

    return ResultadoImportacao(
        inseridos=inseridos,
        atualizados=atualizados,
        rejeitados=rejeitados,
    )
