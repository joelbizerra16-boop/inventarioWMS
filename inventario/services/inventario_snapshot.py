"""Snapshot imutável do inventário geral ao finalizar."""

from collections import defaultdict
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from inventario.models import Inventario, InventarioItem
from inventario.services.confronto import (
    LinhaConfronto,
    ResumoConfronto,
    ResultadoConfronto,
    _aplicar_filtro_status,
    _aplicar_pesquisa,
    _calcular_resumo,
    _calcular_status,
    _ordenar_linhas,
    executar_confronto,
)

MENSAGEM_INDISPONIVEL = 'Dado não disponível no fechamento'
MENSAGEM_CANAL_AUSENTE = 'Este canal não estava disponível no fechamento'


def _foto_canal(fisico: Decimal, saldo: Decimal) -> dict:
    diferenca, classe, rotulo, diverge = _calcular_status(fisico, saldo)
    return {
        'sap': str(saldo),
        'diferenca': str(diferenca),
        'status_classe': classe,
        'status_label': rotulo,
        'possui_divergencia': diverge,
    }


def _fotos_linha(linha) -> dict:
    return {
        'total': _foto_canal(linha.fisico, linha.total_contabil),
        '110': _foto_canal(linha.fisico, linha.canal_110),
        '1': _foto_canal(linha.fisico, linha.canal_1),
        'ambos': _foto_canal(linha.fisico, linha.canal_110 + linha.canal_1),
    }


def _status_exibicao_confronto(label: str) -> str:
    if label == 'Correto':
        return 'Conciliado'
    return 'Divergente'


def _montar_posicoes_por_produto(inventario: Inventario) -> dict[int, list[dict]]:
    agrupado: dict[int, list[dict]] = defaultdict(list)
    itens = (
        InventarioItem.objects.filter(inventario=inventario)
        .select_related('produto', 'posicao')
        .order_by('produto__codigo_produto', 'posicao__posicao')
    )
    for item in itens:
        agrupado[item.produto_id].append({
            'codigo': item.posicao.codigo,
            'alocacao': item.posicao.posicao,
            'quantidade': str(item.quantidade_fisica),
        })
    return agrupado


@transaction.atomic
def congelar_snapshot_inventario(inventario: Inventario, usuario) -> Inventario:
    if inventario.status != Inventario.Status.FINALIZADO:
        raise ValueError('Somente inventários finalizados recebem snapshot.')

    resultado = executar_confronto(inventario_id=inventario.pk)
    posicoes_por_produto = _montar_posicoes_por_produto(inventario)

    produtos_snapshot = []
    for linha in resultado.linhas:
        produtos_snapshot.append({
            'produto_id': linha.produto_id,
            'codigo_produto': linha.codigo_produto,
            'descricao': linha.descricao,
            'embalagem': linha.embalagem or '—',
            'sap': str(linha.total_contabil),
            'contado': str(linha.fisico),
            'diferenca': str(linha.diferenca),
            'status': _status_exibicao_confronto(linha.status_label),
            'canal_110': str(linha.canal_110),
            'canal_1': str(linha.canal_1),
            'fotos': _fotos_linha(linha),
            'posicoes': posicoes_por_produto.get(linha.produto_id, []),
        })

    inventario.data_finalizacao = timezone.now()
    inventario.usuario_finalizacao = usuario
    inventario.quantidade_itens = InventarioItem.objects.filter(inventario=inventario).count()
    inventario.quantidade_produtos = resultado.resumo.total_produtos
    inventario.quantidade_conciliados = resultado.resumo.produtos_corretos
    inventario.quantidade_divergentes = resultado.resumo.produtos_divergentes
    inventario.taxa_acuracidade = resultado.resumo.acuracidade
    inventario.snapshot_resultado = {
        'resumo': {
            'total_produtos': resultado.resumo.total_produtos,
            'conciliados': resultado.resumo.produtos_corretos,
            'divergentes': resultado.resumo.produtos_divergentes,
            'acuracidade': str(resultado.resumo.acuracidade),
        },
        'produtos': produtos_snapshot,
    }
    inventario.save(update_fields=[
        'data_finalizacao',
        'usuario_finalizacao',
        'quantidade_itens',
        'quantidade_produtos',
        'quantidade_conciliados',
        'quantidade_divergentes',
        'taxa_acuracidade',
        'snapshot_resultado',
    ])
    return inventario


def _chave_foto(canal: str | None) -> str:
    if canal is None:
        return 'total'
    if canal in ('110', 'cosan'):
        return '110'
    if canal in ('1', 'brida'):
        return '1'
    return 'ambos'


def _resumo_vazio() -> ResumoConfronto:
    return ResumoConfronto(
        total_produtos=0,
        produtos_corretos=0,
        produtos_divergentes=0,
        produtos_excesso_fisico=0,
        produtos_deficit_fisico=0,
        acuracidade=None,
    )


def _resultado_aviso(mensagem: str) -> tuple[ResultadoConfronto, str]:
    resultado = ResultadoConfronto(linhas=[], resumo=_resumo_vazio())
    resultado.aviso = mensagem
    return resultado, mensagem


def leitura_inventario_congelado(
    inventario: Inventario,
    canal: str | None = None,
    filtro_status: str = 'todos',
    termo_busca: str = '',
) -> tuple[ResultadoConfronto, str]:
    """Lê o confronto só do snapshot. Sem snapshot, não consulta SAP nem contagens."""
    snap = inventario.snapshot_resultado if isinstance(inventario.snapshot_resultado, dict) else None
    if not snap or 'produtos' not in snap:
        return _resultado_aviso(MENSAGEM_INDISPONIVEL)

    chave = _chave_foto(canal)
    produtos = [item for item in snap.get('produtos') or [] if isinstance(item, dict)]
    tem_fotos = any(item.get('fotos') for item in produtos)
    if not tem_fotos and chave not in ('total', 'ambos'):
        return _resultado_aviso(MENSAGEM_CANAL_AUSENTE)

    linhas: list[LinhaConfronto] = []
    for item in produtos:
        fotos = item.get('fotos') or {}
        foto = fotos.get(chave)
        if foto is None and tem_fotos:
            return _resultado_aviso(MENSAGEM_CANAL_AUSENTE)
        if foto:
            sap = Decimal(str(foto.get('sap') or '0'))
            diferenca = Decimal(str(foto.get('diferenca') or '0'))
            classe = foto.get('status_classe') or ''
            rotulo = foto.get('status_label') or ''
            diverge = bool(foto.get('possui_divergencia'))
        elif 'sap' not in item or 'contado' not in item:
            return _resultado_aviso(MENSAGEM_INDISPONIVEL)
        else:
            sap = Decimal(str(item.get('sap') or '0'))
            diferenca = Decimal(str(item.get('diferenca') or '0'))
            if diferenca == 0:
                classe, rotulo, diverge = 'success', 'Correto', False
            elif diferenca > 0:
                classe, rotulo, diverge = 'warning', 'Excesso físico', True
            else:
                classe, rotulo, diverge = 'danger', 'Déficit físico', True
        linhas.append(LinhaConfronto(
            produto_id=int(item.get('produto_id') or 0),
            codigo_produto=item.get('codigo_produto') or '',
            descricao=item['descricao'] if 'descricao' in item else MENSAGEM_INDISPONIVEL,
            embalagem=item.get('embalagem') or '',
            setor=item.get('setor') or '',
            fisico=Decimal(str(item.get('contado') or '0')),
            canal_0=Decimal('0'),
            canal_1=Decimal(str(item.get('canal_1') or '0')),
            canal_2=Decimal('0'),
            canal_66=Decimal('0'),
            canal_80=Decimal('0'),
            canal_81=Decimal('0'),
            canal_82=Decimal('0'),
            canal_99=Decimal('0'),
            canal_110=Decimal(str(item.get('canal_110') or '0')),
            total_contabil=sap,
            diferenca=diferenca,
            status_classe=classe,
            status_label=rotulo,
            possui_divergencia=diverge,
        ))

    linhas = _ordenar_linhas(linhas)
    if canal is None and isinstance(snap.get('resumo'), dict):
        gravado = snap['resumo']
        resumo = ResumoConfronto(
            total_produtos=int(gravado.get('total_produtos') or len(linhas)),
            produtos_corretos=int(gravado.get('conciliados') or 0),
            produtos_divergentes=int(gravado.get('divergentes') or 0),
            produtos_excesso_fisico=sum(1 for linha in linhas if linha.status_label == 'Excesso físico'),
            produtos_deficit_fisico=sum(1 for linha in linhas if linha.status_label == 'Déficit físico'),
            acuracidade=Decimal(str(gravado.get('acuracidade') or '0')),
        )
    else:
        resumo = _calcular_resumo(linhas)
    linhas = _aplicar_filtro_status(linhas, filtro_status)
    linhas = _aplicar_pesquisa(linhas, (termo_busca or '').strip())
    resultado = ResultadoConfronto(linhas=linhas, resumo=resumo)
    resultado.aviso = ''
    return resultado, ''
