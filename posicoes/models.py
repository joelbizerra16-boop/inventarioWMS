from django.db import models

from core.choices import StatusHomologacao


class Posicao(models.Model):
    codigo = models.CharField('código', max_length=50, unique=True)
    posicao = models.CharField('posição', max_length=100)
    rua = models.CharField('rua', max_length=50, blank=True, default='')
    predio = models.CharField('prédio', max_length=50, blank=True, default='')
    nivel = models.CharField('nível', max_length=50, blank=True, default='')
    apto = models.CharField('apto', max_length=50, blank=True, default='')
    ativo = models.BooleanField('ativo', default=True)
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
        related_name='posicoes_precadastradas',
        verbose_name='usuário do pré-cadastro',
    )
    data_precadastro = models.DateTimeField('data do pré-cadastro', null=True, blank=True)
    origem_precadastro = models.CharField('origem do pré-cadastro', max_length=50, blank=True, default='')
    data_criacao = models.DateTimeField('data de criação', auto_now_add=True)

    class Meta:
        verbose_name = 'posição'
        verbose_name_plural = 'posições'
        ordering = ['codigo']

    def __str__(self):
        return self.codigo

    @property
    def eh_precadastro_pendente(self) -> bool:
        return self.status_homologacao == StatusHomologacao.PENDENTE
