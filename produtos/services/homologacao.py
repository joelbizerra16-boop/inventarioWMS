from django.utils import timezone

from accounts.models import Usuario
from core.choices import StatusHomologacao
from produtos.models import AuditoriaHomologacao, Produto


class HomologacaoError(Exception):
    pass


def _registrar_auditoria_produto(
    *,
    produto: Produto,
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
        tipo_cadastro=AuditoriaHomologacao.TipoCadastro.PRODUTO,
        produto=produto,
        usuario=usuario,
        equipamento=equipamento[:255],
        origem=origem,
        acao=acao,
        status_anterior=status_anterior,
        status_novo=status_novo,
        observacao=observacao,
        dados_alterados=dados_alterados or {},
    )


def criar_precadastro_produto(
    *,
    codigo_produto: str,
    descricao: str,
    usuario: Usuario,
    origem: str,
    equipamento: str = '',
    codigo_ean: str = '',
    embalagem: str = '',
    observacao: str = '',
) -> Produto:
    codigo = codigo_produto.strip().upper()
    if not codigo:
        raise HomologacaoError('SKU é obrigatório.')
    if not descricao.strip():
        raise HomologacaoError('Descrição é obrigatória.')

    existente = Produto.objects.filter(codigo_produto=codigo).first()
    if existente:
        if existente.status_homologacao == StatusHomologacao.REJEITADO:
            raise HomologacaoError('Produto rejeitado. Solicite revisão ao administrador.')
        return existente

    agora = timezone.now()
    produto = Produto.objects.create(
        codigo_produto=codigo,
        descricao=descricao.strip(),
        embalagem=embalagem.strip(),
        setor='PRÉ-CADASTRO',
        codigo_ean=codigo_ean.strip() or None,
        ativo=True,
        status_homologacao=StatusHomologacao.PENDENTE,
        observacao_precadastro=observacao.strip(),
        usuario_precadastro=usuario,
        data_precadastro=agora,
        origem_precadastro=origem,
    )
    _registrar_auditoria_produto(
        produto=produto,
        usuario=usuario,
        acao=AuditoriaHomologacao.Acao.PRECADASTRO,
        status_anterior='',
        status_novo=StatusHomologacao.PENDENTE,
        origem=origem,
        equipamento=equipamento,
        observacao=observacao.strip(),
        dados_alterados={
            'codigo_produto': codigo,
            'descricao': descricao.strip(),
            'codigo_ean': codigo_ean.strip(),
            'embalagem': embalagem.strip(),
        },
    )
    return produto


def aprovar_produto(
    produto: Produto,
    usuario: Usuario,
    *,
    origem: str = 'ADMIN_PENDENCIAS',
    equipamento: str = '',
    setor: str = '',
) -> Produto:
    status_anterior = produto.status_homologacao
    produto.status_homologacao = StatusHomologacao.HOMOLOGADO
    if setor.strip():
        produto.setor = setor.strip()
    elif produto.setor == 'PRÉ-CADASTRO':
        produto.setor = 'GERAL'
    produto.save(update_fields=['status_homologacao', 'setor', 'data_atualizacao'])
    _registrar_auditoria_produto(
        produto=produto,
        usuario=usuario,
        acao=AuditoriaHomologacao.Acao.APROVACAO,
        status_anterior=status_anterior,
        status_novo=StatusHomologacao.HOMOLOGADO,
        origem=origem,
        equipamento=equipamento,
    )
    return produto


def rejeitar_produto(
    produto: Produto,
    usuario: Usuario,
    *,
    origem: str = 'ADMIN_PENDENCIAS',
    equipamento: str = '',
    observacao: str = '',
) -> Produto:
    status_anterior = produto.status_homologacao
    produto.status_homologacao = StatusHomologacao.REJEITADO
    produto.ativo = False
    produto.save(update_fields=['status_homologacao', 'ativo', 'data_atualizacao'])
    _registrar_auditoria_produto(
        produto=produto,
        usuario=usuario,
        acao=AuditoriaHomologacao.Acao.REJEICAO,
        status_anterior=status_anterior,
        status_novo=StatusHomologacao.REJEITADO,
        origem=origem,
        equipamento=equipamento,
        observacao=observacao.strip(),
    )
    return produto


def editar_produto_homologacao(
    produto: Produto,
    usuario: Usuario,
    dados: dict,
    *,
    origem: str = 'ADMIN_PENDENCIAS',
    equipamento: str = '',
) -> Produto:
    status_anterior = produto.status_homologacao
    alterados = {}
    aprovar = bool(dados.pop('aprovar', False))
    for campo in ('descricao', 'embalagem', 'setor', 'codigo_ean', 'observacao_precadastro'):
        if campo not in dados:
            continue
        valor = dados[campo]
        if getattr(produto, campo) != valor:
            alterados[campo] = valor
            setattr(produto, campo, valor)
    if aprovar:
        produto.status_homologacao = StatusHomologacao.HOMOLOGADO
        if produto.setor == 'PRÉ-CADASTRO':
            produto.setor = str(dados.get('setor', 'GERAL')).strip() or 'GERAL'
    produto.save()
    _registrar_auditoria_produto(
        produto=produto,
        usuario=usuario,
        acao=AuditoriaHomologacao.Acao.EDICAO,
        status_anterior=status_anterior,
        status_novo=produto.status_homologacao,
        origem=origem,
        equipamento=equipamento,
        dados_alterados=alterados,
    )
    return produto


def listar_produtos_pendentes():
    return Produto.objects.filter(
        status_homologacao=StatusHomologacao.PENDENTE,
    ).select_related('usuario_precadastro').order_by('-data_precadastro', 'codigo_produto')
