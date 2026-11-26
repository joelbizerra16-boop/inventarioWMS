"""Fotografia imutável do fechamento do comparativo.

Conferência finalizada não relê SAP, estoque, produtos, posições nem contagens.
Fechamento novo (versão 3) guarda os resultados já calculados. Fechamento antigo
usa o registro de acuracidade ligado ao evento FINALIZADA e os dados brutos
do snapshot, sem regravar o JSON.
"""

from __future__ import annotations

from decimal import Decimal

from inventario.models_comparativo import ComparativoAcuracidade, ComparativoContagem

MENSAGEM_INDISPONIVEL = 'Dado não disponível no fechamento'
MENSAGEM_CANAL_AUSENTE = 'Este canal não estava disponível no fechamento'
PRIORIDADE_CANAIS = ('ambas', 'cosan', 'brida')


def _dec(valor):
    if valor is None or valor == '':
        return None
    return Decimal(str(valor))


def _jsonavel(valor):
    if isinstance(valor, Decimal):
        return str(valor)
    if isinstance(valor, dict):
        return {str(chave): _jsonavel(item) for chave, item in valor.items()}
    if isinstance(valor, (list, tuple)):
        return [_jsonavel(item) for item in valor]
    return valor


def registros_finalizacao(comparativo: ComparativoContagem) -> dict[str, ComparativoAcuracidade]:
    """Registros de acuracidade citados pelo evento FINALIZADA mais recente."""
    evento = None
    for candidato in comparativo.eventos.all():
        if candidato.acao != 'FINALIZADA':
            continue
        if evento is None or (candidato.data_hora, candidato.pk) > (evento.data_hora, evento.pk):
            evento = candidato
    if evento is None:
        evento = (
            comparativo.eventos.filter(acao='FINALIZADA')
            .order_by('-data_hora', '-pk')
            .first()
        )
    if evento is None:
        return {}
    ids = (evento.dados or {}).get('acuracidades') or []
    if not ids:
        return {}
    return {registro.canal: registro for registro in comparativo.acuracidades.filter(pk__in=ids)}


def _canais_do_snapshot_antigo(comparativo: ComparativoContagem) -> list[str]:
    snap = comparativo.snapshot_resultado or {}
    meta = snap.get('sap_meta')
    if meta is None:
        if comparativo.referencia_sap_id:
            return list(PRIORIDADE_CANAIS)
        return []
    carregados = meta.get('canais_carregados')
    if carregados is None:
        return list(PRIORIDADE_CANAIS) if comparativo.referencia_sap_id else []
    presentes = set(carregados)
    saida = []
    if 'cosan' in presentes and 'brida' in presentes:
        saida.append('ambas')
    for canal in ('cosan', 'brida'):
        if canal in presentes:
            saida.append(canal)
    return [canal for canal in PRIORIDADE_CANAIS if canal in saida]


def canais_com_resultado(comparativo: ComparativoContagem) -> list[str]:
    if comparativo.snapshot_resultado is None:
        return []
    snap = comparativo.snapshot_resultado or {}
    canais = ((snap.get('fechamento') or {}).get('canais')) or {}
    if canais:
        return [
            canal for canal in PRIORIDADE_CANAIS
            if (canais.get(canal) or {}).get('resultado_salvo')
        ]
    registros = registros_finalizacao(comparativo)
    if registros:
        return [canal for canal in PRIORIDADE_CANAIS if canal in registros]
    return _canais_do_snapshot_antigo(comparativo)


def resolver_canal(comparativo, canal_pedido) -> tuple[str, str]:
    """Canal efetivo e aviso. Sem parâmetro, o primeiro resultado salvo na ordem Ambas, COSAN, BRIDA."""
    from inventario.services.comparativo_contagens import Canal

    if comparativo is None or comparativo.snapshot_resultado is None:
        return Canal.normalizar(canal_pedido), ''
    salvos = canais_com_resultado(comparativo)
    if canal_pedido is None:
        for canal in PRIORIDADE_CANAIS:
            if canal in salvos:
                return canal, ''
        return Canal.AMBAS, MENSAGEM_INDISPONIVEL
    pedido = Canal.normalizar(canal_pedido)
    if pedido not in salvos:
        return pedido, MENSAGEM_CANAL_AUSENTE
    return pedido, ''


def acuracidade_exibida(comparativo: ComparativoContagem):
    """Na lista, o fechamento mostra o registro da FINALIZADA, não o salvamento mais novo."""
    if comparativo.snapshot_resultado is None:
        return next(iter(comparativo.acuracidades.all()), None)
    registros = registros_finalizacao(comparativo)
    for canal in PRIORIDADE_CANAIS:
        if canal in registros:
            return registros[canal]
    return None


def _acuracidade_registro(registro: ComparativoAcuracidade, canal: str):
    from inventario.services.comparativo_contagens import Acuracidade

    return Acuracidade(
        canal=canal,
        disponivel=registro.percentual is not None,
        motivo='' if registro.percentual is not None else MENSAGEM_INDISPONIVEL,
        avaliados=registro.skus_avaliados,
        acertos=registro.skus_acertos,
        divergentes=registro.skus_divergentes,
        sem_consenso=registro.skus_sem_consenso,
        total=registro.skus_total,
        percentual=registro.percentual,
    )


def _acuracidade_dict(bruto: dict, canal: str):
    from inventario.services.comparativo_contagens import Acuracidade

    if not bruto:
        return _acuracidade_vazia(canal, MENSAGEM_INDISPONIVEL)
    motivo = bruto.get('motivo') or ''
    percentual = _dec(bruto.get('percentual'))
    return Acuracidade(
        canal=bruto.get('canal') or canal,
        disponivel=bool(bruto.get('disponivel')) and percentual is not None,
        motivo=motivo,
        avaliados=int(bruto.get('avaliados') or 0),
        acertos=int(bruto.get('acertos') or 0),
        divergentes=int(bruto.get('divergentes') or 0),
        sem_consenso=int(bruto.get('sem_consenso') or 0),
        total=int(bruto.get('total') or 0),
        percentual=percentual,
    )


def _acuracidade_vazia(canal: str, motivo: str):
    from inventario.services.comparativo_contagens import Acuracidade

    return Acuracidade(
        canal=canal, disponivel=False, motivo=motivo, avaliados=0, acertos=0,
        divergentes=0, sem_consenso=0, total=0, percentual=None,
    )


def acuracidade_do_fechamento(comparativo, canal: str, bloco: dict | None = None):
    registros = registros_finalizacao(comparativo)
    if canal in registros:
        return _acuracidade_registro(registros[canal], canal)
    if bloco and bloco.get('acuracidade'):
        return _acuracidade_dict(bloco['acuracidade'], canal)
    return _acuracidade_vazia(canal, MENSAGEM_INDISPONIVEL)


def _indicador_vazio(motivo: str, chave: str):
    from inventario.services.comparativo_contagens import Indicador

    return Indicador(
        chave=chave, titulo='Acuracidade por SKU' if chave == 'sku' else 'Acuracidade por posição',
        subtitulo='', numerador=None, denominador=None, percentual=None, motivo=motivo,
        descricao_base='', linhas=[], provisorio=False,
    )


def _indicador_dict(bruto: dict | None, chave: str):
    from inventario.services.comparativo_contagens import Indicador

    if not bruto:
        return _indicador_vazio(MENSAGEM_INDISPONIVEL, chave)
    return Indicador(
        chave=bruto.get('chave') or chave,
        titulo=bruto.get('titulo') or '',
        subtitulo=bruto.get('subtitulo') or '',
        numerador=bruto.get('numerador'),
        denominador=bruto.get('denominador'),
        percentual=_dec(bruto.get('percentual')),
        motivo=bruto.get('motivo') or '',
        descricao_base=bruto.get('descricao_base') or '',
        linhas=list(bruto.get('linhas') or []),
        provisorio=False,
        auxiliar=bruto.get('auxiliar'),
        rotulo_escopo=bruto.get('rotulo_escopo') or '',
    )


def _indicador_json(indicador) -> dict:
    return _jsonavel({
        'chave': indicador.chave,
        'titulo': indicador.titulo,
        'subtitulo': indicador.subtitulo,
        'numerador': indicador.numerador,
        'denominador': indicador.denominador,
        'percentual': indicador.percentual,
        'motivo': indicador.motivo,
        'descricao_base': indicador.descricao_base,
        'linhas': list(indicador.linhas),
        'auxiliar': indicador.auxiliar,
        'rotulo_escopo': indicador.rotulo_escopo,
    })


def _acuracidade_json(acuracidade) -> dict:
    return _jsonavel({
        'canal': acuracidade.canal,
        'disponivel': acuracidade.disponivel,
        'motivo': acuracidade.motivo,
        'avaliados': acuracidade.avaliados,
        'acertos': acuracidade.acertos,
        'divergentes': acuracidade.divergentes,
        'sem_consenso': acuracidade.sem_consenso,
        'total': acuracidade.total,
        'percentual': acuracidade.percentual,
    })


def _linha_json(linha) -> dict:
    from inventario.services.comparativo_contagens import _iso

    def medicao(ordem, item):
        return {
            'associada': item.associada,
            'quantidade': None if item.quantidade is None else str(item.quantidade),
            'operadores': list(item.operadores),
            'primeira': _iso(item.primeira),
            'ultima': _iso(item.ultima),
            'sem_data': item.sem_data,
            'alterada': item.alterada,
            'celula': item.celula,
            'lancamento_ids': [lancamento.id for lancamento in item.lancamentos],
        }

    contagens = linha.contagens
    status = linha.status_sap
    return {
        'chave': linha.chave,
        'produto_id': linha.produto_id,
        'codigo_produto': linha.codigo_produto,
        'descricao': linha.descricao,
        'embalagem': linha.embalagem,
        'posicoes': [[codigo, descricao] for codigo, descricao in linha.posicoes],
        'posicao_id': linha.posicao_id,
        'sap': {chave: (None if valor is None else str(valor)) for chave, valor in linha.sap.items()},
        'sap_presenca': dict(linha.sap_presenca),
        'presente_sap': linha.presente_sap,
        'presente_fisico': linha.presente_fisico,
        'saldo_selecionado': None if linha.saldo_selecionado is None else str(linha.saldo_selecionado),
        'distribuicao_divergente': linha.distribuicao_divergente,
        'contagens': {
            'status': contagens.status,
            'rotulo': contagens.rotulo,
            'dupla': contagens.dupla,
            'orientacao': contagens.orientacao,
            'necessita_c3': contagens.necessita_c3,
            'diferencas': list(contagens.diferencas),
            'celulas': {str(ordem): celula for ordem, celula in contagens.celulas.items()},
            'pares_iguais': [list(par) for par in contagens.pares_iguais],
            'valor_consenso': None if contagens.valor_consenso is None else str(contagens.valor_consenso),
        },
        'status_sap': None if status is None else {
            'status': status.status,
            'rotulo': status.rotulo,
            'detalhe': status.detalhe,
            'provisoria': status.provisoria,
            'coincidentes': list(status.coincidentes),
        },
        'medicoes': {str(ordem): medicao(ordem, linha.medicoes[ordem]) for ordem in linha.medicoes},
        'detalhe_posicoes': [
            {
                'codigo': detalhe.codigo,
                'descricao': detalhe.descricao,
                'quantidades': {
                    str(ordem): (None if qtd is None else str(qtd))
                    for ordem, qtd in detalhe.quantidades.items()
                },
            }
            for detalhe in linha.detalhe_posicoes
        ],
    }


def _linha_de_json(dados, bruto: dict):
    from inventario.services.comparativo_contagens import (
        ORDENS,
        ClassificacaoContagens,
        ClassificacaoSAP,
        LinhaComparativo,
        Medicao,
        PosicaoDetalhe,
        _data,
    )

    por_id = {lancamento.id: lancamento for lancamento in dados.lancamentos}
    medicoes = {}
    for ordem in ORDENS:
        info = (bruto.get('medicoes') or {}).get(str(ordem)) or {}
        medicoes[ordem] = Medicao(
            ordem=ordem,
            associada=bool(info.get('associada')),
            quantidade=_dec(info.get('quantidade')),
            operadores=list(info.get('operadores') or []),
            primeira=_data(info.get('primeira')),
            ultima=_data(info.get('ultima')),
            sem_data=bool(info.get('sem_data')),
            alterada=bool(info.get('alterada')),
            lancamentos=[por_id[item] for item in info.get('lancamento_ids') or [] if item in por_id],
            celula=info.get('celula') or 'ausente',
        )
    contagens = bruto.get('contagens') or {}
    status = bruto.get('status_sap')
    return LinhaComparativo(
        chave=bruto.get('chave') or '',
        produto_id=int(bruto.get('produto_id') or 0),
        codigo_produto=bruto.get('codigo_produto') or '',
        descricao=bruto.get('descricao') if 'descricao' in bruto else MENSAGEM_INDISPONIVEL,
        embalagem=bruto.get('embalagem') or '',
        posicoes=[(par[0], par[1]) for par in bruto.get('posicoes') or []],
        medicoes=medicoes,
        contagens=ClassificacaoContagens(
            status=contagens.get('status') or 'AGUARDANDO',
            rotulo=contagens.get('rotulo') or '',
            dupla=contagens.get('dupla') or '',
            orientacao=contagens.get('orientacao') or '',
            necessita_c3=bool(contagens.get('necessita_c3')),
            diferencas=list(contagens.get('diferencas') or []),
            celulas={int(ordem): celula for ordem, celula in (contagens.get('celulas') or {}).items()},
            pares_iguais=[tuple(par) for par in contagens.get('pares_iguais') or []],
            valor_consenso=_dec(contagens.get('valor_consenso')),
        ),
        sap={chave: _dec(valor) for chave, valor in (bruto.get('sap') or {}).items()},
        sap_presenca=dict(bruto.get('sap_presenca') or {}),
        presente_sap=bool(bruto.get('presente_sap')),
        presente_fisico=bool(bruto.get('presente_fisico')),
        saldo_selecionado=_dec(bruto.get('saldo_selecionado')),
        posicao_id=bruto.get('posicao_id'),
        status_sap=None if not status else ClassificacaoSAP(
            status=status.get('status') or 'NEUTRO',
            rotulo=status.get('rotulo') or '',
            detalhe=status.get('detalhe') or '',
            provisoria=bool(status.get('provisoria')),
            coincidentes=list(status.get('coincidentes') or []),
        ),
        distribuicao_divergente=bruto.get('distribuicao_divergente') or '',
        detalhe_posicoes=[
            PosicaoDetalhe(
                codigo=item.get('codigo') or '',
                descricao=item.get('descricao') or '',
                quantidades={int(ordem): _dec(qtd) for ordem, qtd in (item.get('quantidades') or {}).items()},
            )
            for item in bruto.get('detalhe_posicoes') or []
        ],
    )


def _resumo_dict(resumo) -> dict:
    return {
        'total': resumo.total,
        'contagem_ok': resumo.contagem_ok,
        'contagem_divergente': resumo.contagem_divergente,
        'contagem_aguardando': resumo.contagem_aguardando,
        'necessita_c3': resumo.necessita_c3,
        'sap_ok': resumo.sap_ok,
        'sap_parcial': resumo.sap_parcial,
        'sap_divergente': resumo.sap_divergente,
        'sap_neutro': resumo.sap_neutro,
    }


def _resumo_de(bruto: dict | None):
    from inventario.services.comparativo_contagens import ResumoComparativo

    bruto = bruto or {}
    return ResumoComparativo(
        total=int(bruto.get('total') or 0),
        contagem_ok=int(bruto.get('contagem_ok') or 0),
        contagem_divergente=int(bruto.get('contagem_divergente') or 0),
        contagem_aguardando=int(bruto.get('contagem_aguardando') or 0),
        necessita_c3=int(bruto.get('necessita_c3') or 0),
        sap_ok=int(bruto.get('sap_ok') or 0),
        sap_parcial=int(bruto.get('sap_parcial') or 0),
        sap_divergente=int(bruto.get('sap_divergente') or 0),
        sap_neutro=int(bruto.get('sap_neutro') or 0),
    )


def _cobertura_de(bruto: dict | None):
    from inventario.services.comparativo_contagens import Cobertura

    bruto = bruto or {}
    return Cobertura(**{chave: int(bruto.get(chave) or 0) for chave in (
        'skus_cosan', 'skus_brida', 'skus_sap_selecionado', 'skus_fisicos',
        'intersecao', 'somente_sap', 'somente_fisico', 'total',
        'sap_inativos_excluidos', 'sap_fora_canais',
    )})


def anexar_fechamento(bruto: dict, dados, comparativo) -> dict:
    """Acrescenta os resultados derivados. Não altera snapshots já gravados."""
    from dataclasses import asdict

    from inventario.services.comparativo_contagens import (
        Canal,
        _iso,
        calcular_acuracidade,
        calcular_cobertura,
        calcular_indicador_posicao,
        calcular_indicador_sku,
        montar_linhas_posicao,
        montar_linhas_sku,
        resumir,
    )
    from dashboard.services.painel_inventario import (
        montar_avanco,
        montar_divergencias_contagens,
        montar_divergencias_sap,
        montar_evolucao,
        montar_ranking,
        montar_situacao,
    )

    linhas_posicao = montar_linhas_posicao(dados)
    indicador_posicao = calcular_indicador_posicao(linhas_posicao, dados)
    ranking, total_lancamentos = montar_ranking(dados)
    from inventario.services.comparativo_contagens import NAO_REGISTRADO
    operadores = {
        lancamento.operador for lancamento in dados.lancamentos
        if lancamento.operador and lancamento.operador != NAO_REGISTRADO
    }
    referencia = comparativo.referencia_sap
    canais = {}
    for canal in PRIORIDADE_CANAIS:
        linhas = montar_linhas_sku(dados, canal)
        motivo = dados.motivo_sap(canal)
        acuracidade = calcular_acuracidade(linhas, canal, motivo or True)
        indicador = calcular_indicador_sku(linhas, dados, canal)
        cobertura = calcular_cobertura(dados, canal)
        evolucao = montar_evolucao(linhas, len(linhas))
        canais[canal] = {
            'resultado_salvo': acuracidade.disponivel,
            'motivo_sap': motivo,
            'acuracidade': _acuracidade_json(acuracidade),
            'indicador_sku': _indicador_json(indicador),
            'divergencias_sap': _jsonavel([asdict(item) for item in montar_divergencias_sap(linhas, motivo)]),
            'divergencias_contagens': _jsonavel([
                asdict(item) for item in montar_divergencias_contagens(linhas)
            ]),
            'totais': {
                'skus': len(linhas),
                'posicoes': indicador_posicao.denominador or 0,
                'posicoes_rotulo': indicador_posicao.rotulo_escopo or 'Posições',
                'operadores': len(operadores),
                'lancamentos': len(dados.lancamentos),
            },
            'situacao': _jsonavel([asdict(fatia) for fatia in montar_situacao(linhas)]),
            'evolucao': _jsonavel({
                'disponivel': evolucao.disponivel,
                'motivo': evolucao.motivo,
                'granularidade': evolucao.granularidade,
                'sem_data': evolucao.sem_data,
                'eixo_x': evolucao.eixo_x,
                'eixo_y': evolucao.eixo_y,
                'series': [asdict(serie) for serie in evolucao.series],
            }),
            'avanco': _jsonavel([asdict(item) for item in montar_avanco(linhas, dados)]),
            'cobertura': asdict(cobertura),
            'resumo': _resumo_dict(resumir(linhas)),
            'linhas': [_linha_json(linha) for linha in linhas],
        }
    bruto['versao'] = 3
    bruto['fechamento'] = {
        'versao_derivados': 1,
        'calculado_em': _iso(dados.gerado_em),
        'categoria': comparativo.categoria,
        'referencia': {
            'id': comparativo.referencia_sap_id,
            'descricao': referencia.descricao if referencia else '',
            'data_importacao': _iso(referencia.data_importacao_origem) if referencia else None,
            'arquivo': referencia.arquivo_origem if referencia else '',
        },
        'ranking': _jsonavel([asdict(item) for item in ranking]),
        'total_lancamentos': total_lancamentos,
        'posicao': {
            'indicador': _indicador_json(indicador_posicao),
            'resumo': _resumo_dict(resumir(linhas_posicao)),
            'linhas': [_linha_json(linha) for linha in linhas_posicao],
        },
        'canais': canais,
    }
    return bruto


def _resultado_indisponivel(dados, canal: str, visao: str, filtros, motivo: str):
    from inventario.services.comparativo_contagens import ResultadoComparativo

    return ResultadoComparativo(
        dados=dados,
        canal=canal,
        visao=visao,
        filtros=filtros,
        linhas=[],
        resumo=_resumo_de({}),
        acuracidade=_acuracidade_vazia(canal, motivo),
        cobertura=_cobertura_de({}),
        total_sem_filtro=0,
        indicador_sku=_indicador_vazio(motivo, 'sku'),
        indicador_posicao=_indicador_vazio(motivo, 'posicao'),
    )


def _linhas_v3(dados, bloco_linhas: list) -> list:
    if not bloco_linhas:
        return []
    return [_linha_de_json(dados, bruto) for bruto in bloco_linhas]


def _divergencias_sap(bruto: list):
    from dashboard.services.painel_inventario import DivergenciaSAP

    return [
        DivergenciaSAP(
            codigo_produto=item.get('codigo_produto') or '',
            descricao=item.get('descricao') if 'descricao' in item else MENSAGEM_INDISPONIVEL,
            fisico=_dec(item.get('fisico')) or Decimal('0'),
            contabil=_dec(item.get('contabil')) or Decimal('0'),
            diferenca=_dec(item.get('diferenca')) or Decimal('0'),
            status_classe=item.get('status_classe') or '',
            status_label=item.get('status_label') or '',
        )
        for item in bruto or []
    ]


def _divergencias_contagens(bruto: list):
    from dashboard.services.painel_inventario import DivergenciaContagens

    return [
        DivergenciaContagens(
            codigo_produto=item.get('codigo_produto') or '',
            descricao=item.get('descricao') if 'descricao' in item else MENSAGEM_INDISPONIVEL,
            posicoes=item.get('posicoes') or '—',
            quantidades={int(ordem): _dec(qtd) for ordem, qtd in (item.get('quantidades') or {}).items()},
            amplitude=_dec(item.get('amplitude')) or Decimal('0'),
            situacao=item.get('situacao') or '',
        )
        for item in bruto or []
    ]


def resultado_congelado(comparativo, dados, canal: str, visao: str, filtros):
    from inventario.services.comparativo_contagens import (
        ResultadoComparativo,
        Visao,
        filtrar_linhas,
        montar_linhas_posicao,
        montar_linhas_sku,
        resumir,
        calcular_cobertura,
        calcular_indicador_posicao,
        calcular_indicador_sku,
    )

    salvos = canais_com_resultado(comparativo)
    if canal not in salvos:
        motivo = MENSAGEM_CANAL_AUSENTE if salvos else MENSAGEM_INDISPONIVEL
        return _resultado_indisponivel(dados, canal, visao, filtros, motivo)

    fechamento = (dados.snapshot or {}).get('fechamento') or {}
    bloco = (fechamento.get('canais') or {}).get(canal)
    if bloco:
        if visao == Visao.POSICAO:
            todas = _linhas_v3(dados, (fechamento.get('posicao') or {}).get('linhas'))
            resumo = _resumo_de((fechamento.get('posicao') or {}).get('resumo'))
            acuracidade = None
            cobertura = None
        else:
            todas = _linhas_v3(dados, bloco.get('linhas'))
            resumo = _resumo_de(bloco.get('resumo'))
            acuracidade = acuracidade_do_fechamento(comparativo, canal, bloco)
            cobertura = _cobertura_de(bloco.get('cobertura'))
        return ResultadoComparativo(
            dados=dados,
            canal=canal,
            visao=visao,
            filtros=filtros,
            linhas=filtrar_linhas(todas, filtros),
            resumo=resumo,
            acuracidade=acuracidade,
            cobertura=cobertura,
            total_sem_filtro=len(todas),
            indicador_sku=_indicador_dict(bloco.get('indicador_sku'), 'sku'),
            indicador_posicao=_indicador_dict((fechamento.get('posicao') or {}).get('indicador'), 'posicao'),
        )

    linhas_sku = montar_linhas_sku(dados, canal)
    linhas_posicao = montar_linhas_posicao(dados)
    todas = linhas_posicao if visao == Visao.POSICAO else linhas_sku
    acuracidade = None if visao == Visao.POSICAO else acuracidade_do_fechamento(comparativo, canal)
    return ResultadoComparativo(
        dados=dados,
        canal=canal,
        visao=visao,
        filtros=filtros,
        linhas=filtrar_linhas(todas, filtros),
        resumo=resumir(todas),
        acuracidade=acuracidade,
        cobertura=None if visao == Visao.POSICAO else calcular_cobertura(dados, canal),
        total_sem_filtro=len(todas),
        indicador_sku=_indicador_vazio(MENSAGEM_INDISPONIVEL, 'sku')
        if acuracidade is None or not acuracidade.disponivel
        else _indicador_dict({
            'chave': 'sku',
            'titulo': 'Acuracidade por SKU',
            'subtitulo': 'Registro do fechamento',
            'numerador': acuracidade.acertos,
            'denominador': acuracidade.avaliados,
            'percentual': acuracidade.percentual,
            'motivo': '',
            'descricao_base': 'Registro vinculado à finalização.',
            'linhas': [f'{acuracidade.acertos} de {acuracidade.avaliados} SKUs iguais ao SAP'],
        }, 'sku'),
        indicador_posicao=calcular_indicador_posicao(linhas_posicao, dados),
    )


def preencher_painel_congelado(painel, comparativo, dados, canal: str) -> bool:
    """Preenche o painel sem consultar tabelas atuais. Devolve False se o snapshot não tem derivados."""
    from dashboard.services.painel_inventario import (
        AvancoContagem,
        Evolucao,
        FatiaSituacao,
        ItemRanking,
        SerieEvolucao,
        _barras_cobertura,
    )

    fechamento = (dados.snapshot or {}).get('fechamento') or {}
    if not fechamento.get('canais'):
        return False
    salvos = canais_com_resultado(comparativo)
    if canal not in salvos:
        painel.aviso = MENSAGEM_CANAL_AUSENTE if salvos else MENSAGEM_INDISPONIVEL
        painel.dados = dados
        painel.gerado_em = dados.gerado_em
        return True
    bloco = (fechamento.get('canais') or {}).get(canal)
    if not bloco:
        painel.aviso = MENSAGEM_INDISPONIVEL
        return True
    painel.dados = dados
    painel.gerado_em = dados.gerado_em
    painel.totais = dict(bloco.get('totais') or {})
    painel.indicador_sku = _indicador_dict(bloco.get('indicador_sku'), 'sku')
    painel.indicador_posicao = _indicador_dict((fechamento.get('posicao') or {}).get('indicador'), 'posicao')
    painel.acuracidade_fechamento = acuracidade_do_fechamento(comparativo, canal, bloco)
    painel.situacao = [
        FatiaSituacao(
            chave=item.get('chave') or '',
            rotulo=item.get('rotulo') or '',
            quantidade=int(item.get('quantidade') or 0),
            percentual=_dec(item.get('percentual')),
            dasharray=item.get('dasharray') or '',
            dashoffset=item.get('dashoffset') or '',
        )
        for item in bloco.get('situacao') or []
    ]
    evolucao = bloco.get('evolucao') or {}
    painel.evolucao = Evolucao(
        disponivel=bool(evolucao.get('disponivel')),
        motivo=evolucao.get('motivo') or '',
        series=[SerieEvolucao(**serie) for serie in evolucao.get('series') or []],
        eixo_x=list(evolucao.get('eixo_x') or []),
        eixo_y=list(evolucao.get('eixo_y') or []),
        granularidade=evolucao.get('granularidade') or '',
        sem_data=int(evolucao.get('sem_data') or 0),
    )
    painel.avanco = [
        AvancoContagem(
            rotulo=item.get('rotulo') or '',
            inventario_id=item.get('inventario_id'),
            numerador=int(item.get('numerador') or 0),
            denominador=int(item.get('denominador') or 0),
            percentual=_dec(item.get('percentual')),
            nota=item.get('nota') or '',
            associada=bool(item.get('associada', True)),
        )
        for item in bloco.get('avanco') or []
    ]
    painel.ranking = [
        ItemRanking(
            operador=item.get('operador') or '',
            etapa=item.get('etapa') or '',
            quantidade=int(item.get('quantidade') or 0),
            largura=_dec(item.get('largura')) or Decimal('0'),
        )
        for item in fechamento.get('ranking') or []
    ]
    painel.total_lancamentos = int(fechamento.get('total_lancamentos') or 0)
    painel.cobertura = _cobertura_de(bloco.get('cobertura'))
    painel.cobertura_barras = _barras_cobertura(painel.cobertura)
    painel.motivo_sap = bloco.get('motivo_sap') or ''
    painel.divergencias_sap = _divergencias_sap(bloco.get('divergencias_sap'))
    painel.divergencias_contagens = _divergencias_contagens(bloco.get('divergencias_contagens'))
    return True


def referencia_congelada(comparativo, dados) -> dict:
    from inventario.services.comparativo_contagens import _data

    fechamento = ((getattr(dados, 'snapshot', None) or {}).get('fechamento')) or {}
    referencia = fechamento.get('referencia') or {}
    if referencia:
        return {
            'descricao': referencia.get('descricao') or MENSAGEM_INDISPONIVEL,
            'data_importacao': _data(referencia.get('data_importacao')),
        }
    viva = comparativo.referencia_sap if comparativo.referencia_sap_id else None
    if viva is None:
        return {'descricao': MENSAGEM_INDISPONIVEL, 'data_importacao': None}
    return {'descricao': viva.descricao, 'data_importacao': viva.data_importacao_origem}
