from django.db import models

from core.choices import StatusHomologacao


class Produto(models.Model):
    codigo_produto = models.CharField('código do produto', max_length=50, unique=True)
    descricao = models.CharField('descrição', max_length=255)
    embalagem = models.CharField('embalagem', max_length=100, blank=True, default='')
    setor = models.CharField('setor', max_length=100)
    codigo_ean = models.CharField('código EAN', max_length=50, blank=True, null=True)
    ativo = models.BooleanField('ativo', default=True)
    participa_ciclico = models.BooleanField(
        'participa do inventário cíclico',
        default=True,
    )
    status_homologacao = models.CharField(
        'status de homologação',
        max_length=20,
        choices=StatusHomologacao.choices,
        default=StatusHomologacao.HOMOLOGADO,
    )
    observacao_precadastro = models.TextField('observação do pré-cadastro', blank=True, default='')
    usuario_precadastro = models.ForeignKey(
        'accounts.Usuario',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='produtos_precadastrados',
        verbose_name='usuário do pré-cadastro',
    )
    data_precadastro = models.DateTimeField('data do pré-cadastro', null=True, blank=True)
    origem_precadastro = models.CharField('origem do pré-cadastro', max_length=50, blank=True, default='')
    data_criacao = models.DateTimeField('data de criação', auto_now_add=True)
    data_atualizacao = models.DateTimeField('data de atualização', auto_now=True)

    class Meta:
        verbose_name = 'produto'
        verbose_name_plural = 'produtos'
        ordering = ['codigo_produto']

    def __str__(self):
        return self.codigo_produto

    @property
    def eh_precadastro_pendente(self) -> bool:
        return self.status_homologacao == StatusHomologacao.PENDENTE


class AuditoriaHomologacao(models.Model):
    class TipoCadastro(models.TextChoices):
        PRODUTO = 'PRODUTO', 'Produto'
        POSICAO = 'POSICAO', 'Posição'

    class Acao(models.TextChoices):
        PRECADASTRO = 'PRECADASTRO', 'Pré-cadastro'
        APROVACAO = 'APROVACAO', 'Aprovação'
        EDICAO = 'EDICAO', 'Edição'
        REJEICAO = 'REJEICAO', 'Rejeição'

    tipo_cadastro = models.CharField('tipo', max_length=20, choices=TipoCadastro.choices)
    produto = models.ForeignKey(
        Produto,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='auditorias_homologacao',
    )
    posicao = models.ForeignKey(
        'posicoes.Posicao',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='auditorias_homologacao',
    )
    usuario = models.ForeignKey(
        'accounts.Usuario',
        on_delete=models.PROTECT,
        related_name='auditorias_homologacao',
        verbose_name='usuário',
    )
    data_hora = models.DateTimeField('data/hora', auto_now_add=True)
    equipamento = models.CharField('equipamento', max_length=255, blank=True, default='')
    origem = models.CharField('origem', max_length=50, blank=True, default='')
    acao = models.CharField('ação', max_length=20, choices=Acao.choices)
    status_anterior = models.CharField('status anterior', max_length=20, blank=True, default='')
    status_novo = models.CharField('status novo', max_length=20, blank=True, default='')
    observacao = models.TextField('observação', blank=True, default='')
    dados_alterados = models.JSONField('dados alterados', default=dict, blank=True)

    class Meta:
        verbose_name = 'auditoria de homologação'
        verbose_name_plural = 'auditorias de homologação'
        ordering = ['-data_hora']

    def __str__(self):
        return f'{self.get_tipo_cadastro_display()} — {self.get_acao_display()} — {self.data_hora:%d/%m/%Y %H:%M}'
