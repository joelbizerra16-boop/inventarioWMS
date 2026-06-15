from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
import re
import unicodedata

import pandas as pd
from django.db import transaction
from django.utils import timezone

from core.services.importacao_excel import (
    ResultadoImportacao,
    ResultadoPreview,
    limpar_valor,
    validar_colunas_obrigatorias,
)
from estoque_sap.models import EstoqueSAP
from produtos.models import Produto

BATCH_SIZE_IMPORTACAO = 500

COLUNAS_OBRIGATORIAS = [
    'codigo_produto',
    'descricao',
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
    'descricao': 'descricao',
    '0': 'canal_0',
    'canal_0': 'canal_0',
    '1': 'canal_1',
    'canal_1': 'canal_1',
    '2': 'canal_2',
    'canal_2': 'canal_2',
    '66': 'canal_66',
    'canal_66': 'canal_66',
    '80': 'canal_80',
    'canal_80': 'canal_80',
    '81': 'canal_81',
    'canal_81': 'canal_81',
    '82': 'canal_82',
    'canal_82': 'canal_82',
    '99': 'canal_99',
    'canal_99': 'canal_99',
    '110': 'canal_110',
    'canal_110': 'canal_110',
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


def _normalizar_nome_coluna(nome) -> str:
    texto = str(nome).strip().lower()
    texto = unicodedata.normalize('NFKD', texto)
    texto = ''.join(caractere for caractere in texto if not unicodedata.combining(caractere))
    texto = re.sub(r'\s+', ' ', texto).strip()
    return texto


def normalizar_colunas_importacao(dataframe: pd.DataFrame) -> pd.DataFrame:
    renomear = {}
    for coluna in dataframe.columns:
        chave = _normalizar_nome_coluna(coluna)
        renomear[coluna] = MAPEAMENTO_COLUNAS.get(chave, chave)
    return dataframe.rename(columns=renomear)


def _ler_planilha(arquivo) -> tuple[pd.DataFrame, list[str], list[str]]:
    dataframe = pd.read_excel(arquivo, dtype=str)
    colunas_detectadas = [str(coluna) for coluna in dataframe.columns]
    dataframe = normalizar_colunas_importacao(dataframe)
    colunas_normalizadas = list(dataframe.columns)
    return dataframe.fillna(''), colunas_detectadas, colunas_normalizadas


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
    elif linha['codigo_produto'] not in produtos_existentes:
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
    elif linha.codigo_produto not in produtos_existentes:
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


def _carregar_produtos_existentes(linhas: list[dict]) -> set[str]:
    codigos = [
        linha['codigo_produto']
        for linha in linhas
        if linha.get('codigo_produto') and not linha.get('ignorada')
    ]
    return set(
        Produto.objects.filter(
            codigo_produto__in=codigos,
        ).values_list('codigo_produto', flat=True)
    )


def montar_preview_sessao(
    linhas: list[dict],
    colunas_detectadas: list[str] | None = None,
    colunas_normalizadas: list[str] | None = None,
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
                    campo: Decimal(linha['canais'][campo])
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


def validar_produto_preview(linhas: list[dict], numero_linha: int) -> list[dict]:
    for linha in linhas:
        if linha['linha'] != numero_linha or linha.get('ignorada'):
            continue

        if not any('Produto não cadastrado' in erro for erro in linha.get('erros', [])):
            break

        criar_precadastro_produto(linha['codigo_produto'], linha['descricao'])
        produtos_existentes = _carregar_produtos_existentes(linhas)
        linha['erros'] = [
            erro for erro in linha.get('erros', [])
            if 'numérico inválido' in erro
        ]
        _validar_linha_dict(linha, produtos_existentes)
        break

    return linhas


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
    dataframe, colunas_detectadas, colunas_normalizadas = _ler_planilha(arquivo)
    validar_colunas_obrigatorias(dataframe, COLUNAS_OBRIGATORIAS)

    linhas_parciais = []
    codigos_produto = []

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

        codigo_produto = limpar_valor(registro.get('codigo_produto'))
        if codigo_produto:
            codigos_produto.append(codigo_produto)

        linhas_parciais.append({
            'linha': int(indice) + 2,
            'codigo_produto': codigo_produto,
            'descricao': limpar_valor(registro.get('descricao')),
            'canais': canais,
            'total': _calcular_total(canais),
            'erros_numericos': erros_numericos,
        })

    produtos_existentes = set(
        Produto.objects.filter(
            codigo_produto__in=codigos_produto
        ).values_list('codigo_produto', flat=True)
    )

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


@transaction.atomic
def importar_dados(
    linhas_validas: list[dict],
    arquivo_origem: str,
    rejeitados: int = 0,
) -> ResultadoImportacao:
    if not linhas_validas:
        return ResultadoImportacao(inseridos=0, atualizados=0, rejeitados=rejeitados)

    agora = timezone.now()
    inseridos = 0
    atualizados = 0
    produto_ids: list[int] = []

    codigos_unicos = {dados['codigo_produto'] for dados in linhas_validas}
    produtos_por_codigo = {
        produto.codigo_produto: produto
        for produto in Produto.objects.filter(codigo_produto__in=codigos_unicos)
    }
    estoques_existentes = {
        estoque.produto_id: estoque
        for estoque in EstoqueSAP.objects.filter(
            produto_id__in=[produto.pk for produto in produtos_por_codigo.values()],
        )
    }

    campos_atualizacao = ['arquivo_origem', 'data_importacao', 'total', *CAMPOS_CANAIS]
    criar: list[EstoqueSAP] = []
    criados_no_lote: dict[int, EstoqueSAP] = {}
    atualizar: dict[int, EstoqueSAP] = {}

    for dados in linhas_validas:
        produto = produtos_por_codigo.get(dados['codigo_produto'])
        if produto is None:
            continue

        produto_ids.append(produto.pk)
        valores = {
            'arquivo_origem': arquivo_origem,
            'data_importacao': agora,
            'total': Decimal(dados['total']),
        }
        for campo in CAMPOS_CANAIS:
            valores[campo] = Decimal(dados[campo])

        estoque = estoques_existentes.get(produto.pk) or criados_no_lote.get(produto.pk)

        if estoque is None:
            estoque = EstoqueSAP(produto=produto, **valores)
            criar.append(estoque)
            criados_no_lote[produto.pk] = estoque
            inseridos += 1
            continue

        for campo, valor in valores.items():
            setattr(estoque, campo, valor)
        if produto.pk in estoques_existentes:
            atualizar[produto.pk] = estoque
        atualizados += 1

    if criar:
        EstoqueSAP.objects.bulk_create(criar, batch_size=BATCH_SIZE_IMPORTACAO)
    if atualizar:
        EstoqueSAP.objects.bulk_update(
            list(atualizar.values()),
            campos_atualizacao,
            batch_size=BATCH_SIZE_IMPORTACAO,
        )

    from inventario.services.ciclico import sincronizar_sap_ciclo_ativo

    sincronizar_sap_ciclo_ativo(produto_ids)

    return ResultadoImportacao(
        inseridos=inseridos,
        atualizados=atualizados,
        rejeitados=rejeitados,
    )
