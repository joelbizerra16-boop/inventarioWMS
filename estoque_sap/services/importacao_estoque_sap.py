from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
import logging
import re
import unicodedata

import pandas as pd
from django.db import connection, transaction
from django.utils import timezone

from core.services.importacao_excel import (
    ResultadoImportacao,
    ResultadoPreview,
    limpar_valor,
)
from core.services.perf_diagnostico import medir_etapa
from estoque_sap.models import EstoqueSAP
from produtos.models import Produto

logger = logging.getLogger(__name__)

BATCH_SIZE_IMPORTACAO = 500
LOCK_SNAPSHOT_SAP = 73924501
MAX_LINHAS_CABECALHO = 20

COLUNAS_OBRIGATORIAS = [
    'codigo_produto',
    'descricao',
]

CAMPOS_CANAIS = [
    'canal_0',
    'canal_1',
    'canal_2',
    'canal_66',
    'canal_80',
    'canal_81',
    'canal_82',
    'canal_99',
    'canal_110',
]

MAPEAMENTO_COLUNAS = {
    'codproduto': 'codigo_produto',
    'cod produto': 'codigo_produto',
    'codigo produto': 'codigo_produto',
    'codigo_produto': 'codigo_produto',
    'sku': 'codigo_produto',
    'descricao': 'descricao',
    '0': 'canal_0',
    'canal 0': 'canal_0',
    'canal_0': 'canal_0',
    '1': 'canal_1',
    'canal 1': 'canal_1',
    'canal_1': 'canal_1',
    'brida': 'canal_1',
    '2': 'canal_2',
    'canal 2': 'canal_2',
    'canal_2': 'canal_2',
    '66': 'canal_66',
    'canal 66': 'canal_66',
    'canal_66': 'canal_66',
    '80': 'canal_80',
    'canal 80': 'canal_80',
    'canal_80': 'canal_80',
    '81': 'canal_81',
    'canal 81': 'canal_81',
    'canal_81': 'canal_81',
    '82': 'canal_82',
    'canal 82': 'canal_82',
    'canal_82': 'canal_82',
    '99': 'canal_99',
    'canal 99': 'canal_99',
    'canal_99': 'canal_99',
    '110': 'canal_110',
    'canal 110': 'canal_110',
    'canal_110': 'canal_110',
    'cosan': 'canal_110',
    'total': 'total',
}


@dataclass
class LinhaImportacao:
    linha: int
    codigo_produto: str
    descricao: str
    canais: dict[str, Decimal]
    total: Decimal
    valida: bool
    erros: list[str] = field(default_factory=list)
    ignorada: bool = False
    status: str = 'Inválido'


@dataclass
class ResultadoPreviewSAP(ResultadoPreview):
    colunas_detectadas: list[str] = field(default_factory=list)
    colunas_normalizadas: list[str] = field(default_factory=list)
    aba: str = ''


def _normalizar_nome_coluna(nome) -> str:
    texto = str(nome).strip().lower()
    if texto.lower() in ('nan', 'none', 'unnamed: 0'):
        return ''
    texto = unicodedata.normalize('NFKD', texto)
    texto = ''.join(caractere for caractere in texto if not unicodedata.combining(caractere))
    texto = re.sub(r'\s+', ' ', texto).strip()
    if texto.endswith('.0') and texto[:-2].isdigit():
        texto = texto[:-2]
    return texto


def normalizar_codigo_produto(valor) -> str:
    texto = limpar_valor(valor).replace('\xa0', ' ').strip()
    if texto.lower() in ('nan', 'none'):
        return ''
    if texto.endswith('.0') and texto[:-2].lstrip('-').isdigit():
        texto = texto[:-2]
    try:
        if 'e' in texto.lower():
            numero = float(texto)
            if numero.is_integer():
                texto = str(int(numero))
    except ValueError:
        pass
    return texto.strip()


def normalizar_colunas_importacao(dataframe: pd.DataFrame) -> pd.DataFrame:
    renomear = {}
    usados = set()
    for coluna in dataframe.columns:
        chave = _normalizar_nome_coluna(coluna)
        destino = MAPEAMENTO_COLUNAS.get(chave, chave)
        if destino in usados:
            continue
        renomear[coluna] = destino
        if destino:
            usados.add(destino)
    return dataframe.rename(columns=renomear)


def _tem_layout_sap(colunas: list[str]) -> bool:
    normalizadas = set(colunas)
    return 'codigo_produto' in normalizadas and any(
        campo in normalizadas for campo in CAMPOS_CANAIS
    )


def _detectar_linha_cabecalho(bruto: pd.DataFrame) -> int | None:
    limite = min(len(bruto), MAX_LINHAS_CABECALHO)
    for indice in range(limite):
        valores = [_normalizar_nome_coluna(valor) for valor in bruto.iloc[indice].tolist()]
        mapeadas = [MAPEAMENTO_COLUNAS.get(valor, valor) for valor in valores]
        if _tem_layout_sap(mapeadas):
            return indice
    return None


def _dataframe_da_aba(excel: pd.ExcelFile, sheet_name: str) -> tuple[pd.DataFrame, list[str]] | None:
    bruto = pd.read_excel(excel, sheet_name=sheet_name, dtype=str, header=None)
    if bruto.empty:
        return None
    indice_cabecalho = _detectar_linha_cabecalho(bruto)
    if indice_cabecalho is None:
        return None
    cabecalho = [str(valor).strip() if not pd.isna(valor) else '' for valor in bruto.iloc[indice_cabecalho].tolist()]
    dataframe = bruto.iloc[indice_cabecalho + 1:].copy()
    dataframe.columns = cabecalho
    dataframe = dataframe.reset_index(drop=True)
    colunas_detectadas = [str(coluna) for coluna in dataframe.columns]
    dataframe = normalizar_colunas_importacao(dataframe)
    if not _tem_layout_sap(list(dataframe.columns)):
        return None
    for campo in CAMPOS_CANAIS:
        if campo not in dataframe.columns:
            dataframe[campo] = ''
    if 'descricao' not in dataframe.columns:
        dataframe['descricao'] = ''
    return dataframe.fillna(''), colunas_detectadas


def _ler_planilha(arquivo) -> tuple[pd.DataFrame, list[str], list[str], str]:
    excel = pd.ExcelFile(arquivo)
    melhor = None
    for sheet_name in excel.sheet_names:
        lido = _dataframe_da_aba(excel, sheet_name)
        if lido is None:
            continue
        dataframe, colunas_detectadas = lido
        linhas_com_codigo = sum(
            1
            for valor in dataframe.get('codigo_produto', [])
            if normalizar_codigo_produto(valor)
        )
        candidato = (linhas_com_codigo, sheet_name, dataframe, colunas_detectadas)
        if melhor is None or linhas_com_codigo > melhor[0]:
            melhor = candidato

    if melhor is None:
        raise ValueError(
            'Não foi possível localizar as colunas do SAP (CodProduto e canais) em nenhuma aba.'
        )

    linhas_com_codigo, sheet_name, dataframe, colunas_detectadas = melhor
    logger.info(
        'IMPORTACAO_SAP_ABA aba=%s linhas_com_codigo=%s total_linhas=%s',
        sheet_name,
        linhas_com_codigo,
        len(dataframe),
    )
    return dataframe, colunas_detectadas, list(dataframe.columns), str(sheet_name)


def _converter_decimal(valor, nome_campo: str) -> tuple[Decimal | None, str | None]:
    if valor is None or limpar_valor(valor) == '':
        return Decimal('0'), None

    try:
        valor_normalizado = limpar_valor(valor).replace(',', '.')
        return Decimal(valor_normalizado), None
    except (InvalidOperation, ValueError):
        return None, f'{nome_campo} possui valor numérico inválido.'


def _calcular_total(canais: dict[str, Decimal]) -> Decimal:
    return sum(canais.values(), Decimal('0'))


def obter_status_linha(valida: bool, erros: list[str], ignorada: bool = False) -> str:
    if ignorada:
        return 'Excluída'
    if valida:
        return 'Válido'

    erros_texto = ' '.join(erros)
    if 'Produto não cadastrado' in erros_texto:
        return 'Produto não encontrado'
    if 'numérico inválido' in erros_texto:
        return 'Dados inconsistentes'
    if 'Código do produto é obrigatório' in erros_texto:
        return 'Inválido'
    if 'Descrição' in erros_texto:
        return 'Inválido'
    return 'Inválido'


def _validar_linha_dict(linha: dict, produtos_existentes: set[str]) -> dict:
    erros = [
        erro for erro in linha.get('erros', [])
        if 'numérico inválido' in erro
    ]

    if not linha.get('codigo_produto'):
        erros.append('Código do produto é obrigatório.')
    elif not _produto_cadastrado(linha['codigo_produto'], produtos_existentes):
        erros.append('Produto não cadastrado.')

    linha['erros'] = erros
    linha['valida'] = not erros and not linha.get('ignorada', False)
    linha['status'] = obter_status_linha(
        linha['valida'],
        linha['erros'],
        linha.get('ignorada', False),
    )
    return linha


def _validar_linha(linha: LinhaImportacao, produtos_existentes: set[str]) -> LinhaImportacao:
    erros = list(linha.erros)

    if not linha.codigo_produto:
        erros.append('Código do produto é obrigatório.')
    elif not _produto_cadastrado(linha.codigo_produto, produtos_existentes):
        erros.append('Produto não cadastrado.')

    linha.valida = not erros
    linha.erros = erros
    linha.status = obter_status_linha(linha.valida, linha.erros, linha.ignorada)
    return linha


def serializar_linha_preview(linha: LinhaImportacao) -> dict:
    return {
        'linha': linha.linha,
        'codigo_produto': linha.codigo_produto,
        'descricao': linha.descricao,
        'canal_1': str(linha.canais['canal_1']),
        'canal_110': str(linha.canais['canal_110']),
        'total': str(linha.total),
        'canais': {campo: str(linha.canais[campo]) for campo in CAMPOS_CANAIS},
        'valida': linha.valida,
        'ignorada': linha.ignorada,
        'erros': linha.erros,
        'status': linha.status,
    }


def serializar_preview_sessao(preview: ResultadoPreviewSAP) -> list[dict]:
    return [serializar_linha_preview(linha) for linha in preview.linhas]


def _chave_codigo(codigo: str) -> str:
    return normalizar_codigo_produto(codigo).upper()


def _produto_cadastrado(codigo: str, existentes: set[str]) -> bool:
    if not codigo:
        return False
    if codigo in existentes:
        return True
    chave = _chave_codigo(codigo)
    return chave in existentes


def _mapa_codigos_produto() -> dict[str, str]:
    mapa: dict[str, str] = {}
    for codigo in Produto.objects.values_list('codigo_produto', flat=True):
        chave = _chave_codigo(codigo)
        if chave and chave not in mapa:
            mapa[chave] = codigo
    return mapa


def _carregar_produtos_existentes(linhas: list[dict] | None = None) -> set[str]:
    mapa = _mapa_codigos_produto()
    return set(mapa.keys()) | set(mapa.values())


def _codigo_canonico(codigo: str, mapa: dict[str, str] | None = None) -> str:
    normalizado = normalizar_codigo_produto(codigo)
    if not normalizado:
        return ''
    mapa = mapa if mapa is not None else _mapa_codigos_produto()
    return mapa.get(_chave_codigo(normalizado), normalizado)


def montar_preview_sessao(
    linhas: list[dict],
    colunas_detectadas: list[str] | None = None,
    colunas_normalizadas: list[str] | None = None,
    aba: str = '',
) -> ResultadoPreviewSAP:
    linhas_visiveis = [linha for linha in linhas if not linha.get('ignorada')]
    linhas_exibicao = []

    for linha in linhas_visiveis:
        linhas_exibicao.append(
            LinhaImportacao(
                linha=linha['linha'],
                codigo_produto=linha.get('codigo_produto', ''),
                descricao=linha.get('descricao', ''),
                canais={
                    campo: Decimal(str((linha.get('canais') or {}).get(campo, '0') or '0'))
                    for campo in CAMPOS_CANAIS
                },
                total=Decimal(linha['total']),
                valida=linha.get('valida', False),
                erros=linha.get('erros', []),
                ignorada=linha.get('ignorada', False),
                status=linha.get(
                    'status',
                    obter_status_linha(
                        linha.get('valida', False),
                        linha.get('erros', []),
                        linha.get('ignorada', False),
                    ),
                ),
            )
        )

    validas = sum(1 for linha in linhas_visiveis if linha.get('valida'))
    invalidas = len(linhas_visiveis) - validas

    return ResultadoPreviewSAP(
        total_linhas=len(linhas_visiveis),
        linhas_validas=validas,
        linhas_invalidas=invalidas,
        linhas=linhas_exibicao,
        colunas_detectadas=colunas_detectadas or [],
        colunas_normalizadas=colunas_normalizadas or [],
        aba=aba,
    )


def criar_precadastro_produto(codigo_produto: str, descricao: str) -> Produto:
    from produtos.services.homologacao import criar_precadastro_produto as criar_precadastro_operacional
    from accounts.models import Usuario

    usuario_sistema = Usuario.objects.filter(perfil=Usuario.Perfil.ADMINISTRADOR).order_by('pk').first()
    if usuario_sistema is None:
        produto, _ = Produto.objects.get_or_create(
            codigo_produto=codigo_produto,
            defaults={
                'descricao': descricao,
                'setor': 'PRÉ-CADASTRO',
                'embalagem': '',
                'ativo': True,
            },
        )
        return produto

    return criar_precadastro_operacional(
        codigo_produto=codigo_produto,
        descricao=descricao,
        usuario=usuario_sistema,
        origem='IMPORTACAO_SAP',
    )


def _criar_precadastro_linha(linha: dict) -> None:
    from produtos.services.homologacao import HomologacaoError

    codigo = normalizar_codigo_produto(linha.get('codigo_produto', ''))
    descricao = limpar_valor(linha.get('descricao', ''))
    if not codigo or not descricao:
        return
    try:
        criar_precadastro_produto(codigo, descricao)
    except HomologacaoError:
        return
    linha['codigo_produto'] = codigo


def validar_produto_preview(linhas: list[dict], numero_linha: int) -> list[dict]:
    for linha in linhas:
        if linha['linha'] != numero_linha or linha.get('ignorada'):
            continue

        if not any('Produto não cadastrado' in erro for erro in linha.get('erros', [])):
            break

        _criar_precadastro_linha(linha)
        linha['codigo_produto'] = _codigo_canonico(linha.get('codigo_produto', ''))
        produtos_existentes = _carregar_produtos_existentes()
        linha['erros'] = [
            erro for erro in linha.get('erros', [])
            if 'numérico inválido' in erro
        ]
        _validar_linha_dict(linha, produtos_existentes)
        break

    return linhas


def garantir_produtos_das_linhas(linhas: list[dict]) -> int:
    criados = 0
    for linha in linhas:
        if linha.get('ignorada'):
            continue
        if not any('Produto não cadastrado' in erro for erro in linha.get('erros', [])):
            continue
        codigo = normalizar_codigo_produto(linha.get('codigo_produto', ''))
        if not codigo:
            continue
        existia = Produto.objects.filter(codigo_produto=codigo).exists()
        _criar_precadastro_linha(linha)
        if not existia and Produto.objects.filter(codigo_produto=codigo).exists():
            criados += 1

    mapa = _mapa_codigos_produto()
    existentes = set(mapa.keys()) | set(mapa.values())
    for linha in linhas:
        if linha.get('ignorada'):
            continue
        linha['codigo_produto'] = _codigo_canonico(linha.get('codigo_produto', ''), mapa)
        linha['erros'] = [
            erro for erro in linha.get('erros', [])
            if 'numérico inválido' in erro
        ]
        _validar_linha_dict(linha, existentes)
    return criados


def excluir_linha_preview(linhas: list[dict], numero_linha: int) -> list[dict]:
    for linha in linhas:
        if linha['linha'] == numero_linha:
            linha['ignorada'] = True
            linha['valida'] = False
            linha['erros'] = []
            linha['status'] = 'Excluída'
            break
    return linhas


def filtrar_linhas_para_importacao(linhas: list[dict]) -> tuple[list[dict], int]:
    importaveis = []
    rejeitados = 0

    for linha in linhas:
        if linha.get('ignorada'):
            rejeitados += 1
            continue
        if not linha.get('valida'):
            rejeitados += 1
            continue

        dados = {
            'codigo_produto': linha['codigo_produto'],
            'total': linha['total'],
        }
        for campo in CAMPOS_CANAIS:
            dados[campo] = linha['canais'][campo]
        importaveis.append(dados)

    return importaveis, rejeitados


def linha_permite_validar_produto(linha: LinhaImportacao) -> bool:
    return (
        not linha.valida
        and not linha.ignorada
        and any('Produto não cadastrado' in erro for erro in linha.erros)
    )


def processar_arquivo(arquivo) -> ResultadoPreviewSAP:
    dataframe, colunas_detectadas, colunas_normalizadas, aba = _ler_planilha(arquivo)

    linhas_parciais = []
    mapa_produtos = _mapa_codigos_produto()
    produtos_existentes = set(mapa_produtos.keys()) | set(mapa_produtos.values())

    for indice, registro in dataframe.iterrows():
        erros_numericos = []
        canais = {}

        for campo in CAMPOS_CANAIS:
            valor, erro = _converter_decimal(registro.get(campo), campo)
            if erro:
                erros_numericos.append(erro)
                canais[campo] = Decimal('0')
            else:
                canais[campo] = valor

        codigo_produto = _codigo_canonico(registro.get('codigo_produto'), mapa_produtos)
        descricao = limpar_valor(registro.get('descricao'))
        if not codigo_produto and not descricao and all(valor == 0 for valor in canais.values()):
            continue

        linhas_parciais.append({
            'linha': int(indice) + 2,
            'codigo_produto': codigo_produto,
            'descricao': descricao,
            'canais': canais,
            'total': _calcular_total(canais),
            'erros_numericos': erros_numericos,
        })

    linhas = []
    for dados in linhas_parciais:
        linha = LinhaImportacao(
            linha=dados['linha'],
            codigo_produto=dados['codigo_produto'],
            descricao=dados['descricao'],
            canais=dados['canais'],
            total=dados['total'],
            valida=False,
            erros=dados['erros_numericos'],
        )

        if dados['erros_numericos']:
            linha.valida = False
            linha.erros = dados['erros_numericos']
            linha.status = obter_status_linha(False, linha.erros)
        else:
            linha = _validar_linha(linha, produtos_existentes)

        linhas.append(linha)

    validas = sum(1 for linha in linhas if linha.valida)
    invalidas = len(linhas) - validas

    return ResultadoPreviewSAP(
        total_linhas=len(linhas),
        linhas_validas=validas,
        linhas_invalidas=invalidas,
        linhas=linhas,
        colunas_detectadas=colunas_detectadas,
        colunas_normalizadas=colunas_normalizadas,
        aba=aba,
    )


def serializar_linhas_validas(preview: ResultadoPreview) -> list[dict]:
    linhas_serializadas = []

    for linha in preview.linhas:
        if not linha.valida:
            continue

        dados = {
            'codigo_produto': linha.codigo_produto,
            'total': str(linha.total),
        }

        for campo in CAMPOS_CANAIS:
            dados[campo] = str(linha.canais[campo])

        linhas_serializadas.append(dados)

    return linhas_serializadas


def _adquirir_lock_snapshot_sap() -> None:
    if connection.vendor != 'postgresql':
        return
    with connection.cursor() as cursor:
        cursor.execute('SELECT pg_advisory_xact_lock(%s)', [LOCK_SNAPSHOT_SAP])


def _montar_registros_snapshot(
    linhas_validas: list[dict],
    *,
    arquivo_origem: str,
    agora,
    produtos_por_codigo: dict[str, Produto],
) -> tuple[list[EstoqueSAP], set[int]]:
    linhas_por_codigo: dict[str, dict] = {}
    for dados in linhas_validas:
        linhas_por_codigo[dados['codigo_produto']] = dados

    registros: list[EstoqueSAP] = []
    produto_ids_importados: set[int] = set()

    for dados in linhas_por_codigo.values():
        produto = produtos_por_codigo.get(dados['codigo_produto'])
        if produto is None:
            continue

        produto_ids_importados.add(produto.pk)
        valores = {
            'arquivo_origem': arquivo_origem,
            'data_importacao': agora,
            'total': Decimal(dados['total']),
        }
        for campo in CAMPOS_CANAIS:
            valores[campo] = Decimal(dados[campo])

        registros.append(EstoqueSAP(produto=produto, **valores))

    return registros, produto_ids_importados


@transaction.atomic
def importar_dados(
    linhas_validas: list[dict],
    arquivo_origem: str,
    rejeitados: int = 0,
) -> ResultadoImportacao:
    if not linhas_validas:
        return ResultadoImportacao(inseridos=0, atualizados=0, rejeitados=rejeitados)

    _adquirir_lock_snapshot_sap()

    agora = timezone.now()
    mapa = _mapa_codigos_produto()
    for dados in linhas_validas:
        dados['codigo_produto'] = _codigo_canonico(dados['codigo_produto'], mapa)
    codigos_unicos = {dados['codigo_produto'] for dados in linhas_validas}
    produtos_por_codigo = {
        produto.codigo_produto: produto
        for produto in Produto.objects.filter(codigo_produto__in=codigos_unicos)
    }

    registros, produto_ids_importados = _montar_registros_snapshot(
        linhas_validas,
        arquivo_origem=arquivo_origem,
        agora=agora,
        produtos_por_codigo=produtos_por_codigo,
    )

    if not produto_ids_importados:
        return ResultadoImportacao(inseridos=0, atualizados=0, rejeitados=rejeitados)

    produtos_com_estoque_antes = set(
        EstoqueSAP.objects.values_list('produto_id', flat=True)
    )
    inseridos = len(produto_ids_importados - produtos_com_estoque_antes)
    atualizados = len(produto_ids_importados & produtos_com_estoque_antes)

    with medir_etapa('estoque_sap.importar.confirmar.substituir_snapshot'):
        EstoqueSAP.objects.exclude(produto_id__in=produto_ids_importados).delete()
        EstoqueSAP.objects.filter(produto_id__in=produto_ids_importados).delete()
        EstoqueSAP.objects.bulk_create(
            registros,
            batch_size=BATCH_SIZE_IMPORTACAO,
        )

    with medir_etapa('estoque_sap.importar.confirmar.sincronizar_ciclo'):
        from inventario.services.ciclico import sincronizar_sap_ciclo_ativo

        sincronizar_sap_ciclo_ativo(list(produto_ids_importados))

    return ResultadoImportacao(
        inseridos=inseridos,
        atualizados=atualizados,
        rejeitados=rejeitados,
    )
