from django.utils import timezone

from accounts.models import Usuario
from core.choices import StatusHomologacao
from posicoes.models import Posicao
from produtos.models import AuditoriaHomologacao


class HomologacaoPosicaoError(Exception):
    pass


def montar_codigo_estruturado(rua: str, predio: str, nivel: str, apto: str) -> str:
    partes = [valor.strip().upper() for valor in (rua, predio, nivel, apto) if valor and valor.strip()]
    return '-'.join(partes)


def montar_descricao_estruturada(rua: str, predio: str, nivel: str, apto: str) -> str:
    partes = []
    if rua.strip():
        partes.append(f'Rua {rua.strip()}')
    if predio.strip():
        partes.append(f'Prédio {predio.strip()}')
    if nivel.strip():
        partes.append(f'Nível {nivel.strip()}')
    if apto.strip():
        partes.append(f'Apto {apto.strip()}')
    return ' | '.join(partes)


def _registrar_auditoria_posicao(
    *,
    posicao: Posicao,
    usuario: Usuario,
    acao: str,
    status_anterior: str,
    status_novo: str,
    origem: str,
    equipamento: str = '',
    observacao: str = '',
    dados_alterados: dict | None = None,
) -> AuditoriaHomologacao:
    return AuditoriaHomologacao.objects.create(
        tipo_cadastro=AuditoriaHomologacao.TipoCadastro.POSICAO,
        posicao=posicao,
        usuario=usuario,
        equipamento=equipamento[:255],
        origem=origem,
        acao=acao,
        status_anterior=status_anterior,
        status_novo=status_novo,
        observacao=observacao,
        dados_alterados=dados_alterados or {},
    )


def _homologar_imediato_pocket(origem: str) -> bool:
    return origem.startswith('POCKET')


def criar_precadastro_posicao(
    *,
    usuario: Usuario,
    origem: str,
    equipamento: str = '',
    codigo_completo: str = '',
    posicao_descricao: str = '',
    rua: str = '',
    predio: str = '',
    nivel: str = '',
    apto: str = '',
    observacao: str = '',
) -> Posicao:
    codigo = codigo_completo.strip().upper()
    descricao = posicao_descricao.strip()

    if not codigo:
        codigo = montar_codigo_estruturado(rua, predio, nivel, apto)
        if not descricao:
            descricao = montar_descricao_estruturada(rua, predio, nivel, apto)

    if not codigo:
        raise HomologacaoPosicaoError('Informe o código completo ou Rua/Prédio/Nível/Apto.')

    if not descricao:
        descricao = codigo

    existente = Posicao.objects.filter(codigo=codigo).first()
    if existente:
        if existente.status_homologacao == StatusHomologacao.REJEITADO:
            raise HomologacaoPosicaoError('Posição rejeitada. Solicite revisão ao administrador.')
        return existente

    agora = timezone.now()
    homologado = _homologar_imediato_pocket(origem)
    status_inicial = (
        StatusHomologacao.HOMOLOGADO
        if homologado
        else StatusHomologacao.PENDENTE
    )
    posicao = Posicao.objects.create(
        codigo=codigo,
        posicao=descricao,
        rua=rua.strip(),
        predio=predio.strip(),
        nivel=nivel.strip(),
        apto=apto.strip(),
        ativo=True,
        status_homologacao=status_inicial,
        observacao_precadastro=observacao.strip(),
        usuario_precadastro=usuario,
        data_precadastro=agora,
        origem_precadastro=origem,
    )
    dados_alterados = {
        'codigo': codigo,
        'posicao': descricao,
        'rua': rua.strip(),
        'predio': predio.strip(),
        'nivel': nivel.strip(),
        'apto': apto.strip(),
    }
    if homologado:
        _registrar_auditoria_posicao(
            posicao=posicao,
            usuario=usuario,
            acao=AuditoriaHomologacao.Acao.APROVACAO,
            status_anterior='',
            status_novo=StatusHomologacao.HOMOLOGADO,
            origem=origem,
            equipamento=equipamento,
            observacao=observacao.strip(),
            dados_alterados=dados_alterados,
        )
    else:
        _registrar_auditoria_posicao(
            posicao=posicao,
            usuario=usuario,
            acao=AuditoriaHomologacao.Acao.PRECADASTRO,
            status_anterior='',
            status_novo=StatusHomologacao.PENDENTE,
            origem=origem,
            equipamento=equipamento,
            observacao=observacao.strip(),
            dados_alterados=dados_alterados,
        )
    return posicao


def aprovar_posicao(
    posicao: Posicao,
    usuario: Usuario,
    *,
    origem: str = 'ADMIN_PENDENCIAS',
    equipamento: str = '',
) -> Posicao:
    status_anterior = posicao.status_homologacao
    posicao.status_homologacao = StatusHomologacao.HOMOLOGADO
    posicao.save(update_fields=['status_homologacao'])
    _registrar_auditoria_posicao(
        posicao=posicao,
        usuario=usuario,
        acao=AuditoriaHomologacao.Acao.APROVACAO,
        status_anterior=status_anterior,
        status_novo=StatusHomologacao.HOMOLOGADO,
        origem=origem,
        equipamento=equipamento,
    )
    return posicao


def rejeitar_posicao(
    posicao: Posicao,
    usuario: Usuario,
    *,
    origem: str = 'ADMIN_PENDENCIAS',
    equipamento: str = '',
    observacao: str = '',
) -> Posicao:
    status_anterior = posicao.status_homologacao
    posicao.status_homologacao = StatusHomologacao.REJEITADO
    posicao.ativo = False
    posicao.save(update_fields=['status_homologacao', 'ativo'])
    _registrar_auditoria_posicao(
        posicao=posicao,
        usuario=usuario,
        acao=AuditoriaHomologacao.Acao.REJEICAO,
        status_anterior=status_anterior,
        status_novo=StatusHomologacao.REJEITADO,
        origem=origem,
        equipamento=equipamento,
        observacao=observacao.strip(),
    )
    return posicao


def editar_posicao_homologacao(
    posicao: Posicao,
    usuario: Usuario,
    dados: dict,
    *,
    origem: str = 'ADMIN_PENDENCIAS',
    equipamento: str = '',
) -> Posicao:
    status_anterior = posicao.status_homologacao
    alterados = {}
    aprovar = bool(dados.pop('aprovar', False))
    for campo in ('codigo', 'posicao', 'rua', 'predio', 'nivel', 'apto', 'observacao_precadastro'):
        if campo not in dados:
            continue
        valor = dados[campo]
        if getattr(posicao, campo) != valor:
            alterados[campo] = valor
            setattr(posicao, campo, valor)
    if aprovar:
        posicao.status_homologacao = StatusHomologacao.HOMOLOGADO
    posicao.save()
    _registrar_auditoria_posicao(
        posicao=posicao,
        usuario=usuario,
        acao=AuditoriaHomologacao.Acao.EDICAO,
        status_anterior=status_anterior,
        status_novo=posicao.status_homologacao,
        origem=origem,
        equipamento=equipamento,
        dados_alterados=alterados,
    )
    return posicao


def listar_posicoes_pendentes():
    return Posicao.objects.filter(
        status_homologacao=StatusHomologacao.PENDENTE,
    ).select_related('usuario_precadastro').order_by('-data_precadastro', 'codigo')
