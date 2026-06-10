from django.db import models

from accounts.models import Usuario
from posicoes.models import Posicao
from produtos.models import Produto


class Movimentacao(models.Model):
    class TipoMovimento(models.TextChoices):
        ENTRADA = 'ENTRADA', 'Entrada'
        SAIDA = 'SAIDA', 'Saída'
        INVENTARIO = 'INVENTARIO', 'Inventário'
        AJUSTE = 'AJUSTE', 'Ajuste'

    usuario = models.ForeignKey(
        Usuario,
        on_delete=models.PROTECT,
        related_name='movimentacoes',
        verbose_name='usuário',
    )
    produto = models.ForeignKey(
        Produto,
        on_delete=models.PROTECT,
        related_name='movimentacoes',
        verbose_name='produto',
    )
    posicao = models.ForeignKey(
        Posicao,
        on_delete=models.PROTECT,
        related_name='movimentacoes',
        verbose_name='posição',
    )
    quantidade = models.DecimalField(
        'quantidade', max_digits=12, decimal_places=3, default=0
    )
    tipo_movimento = models.CharField(
        'tipo de movimento',
        max_length=20,
        choices=TipoMovimento.choices,
    )
    observacao = models.TextField('observação', blank=True)
    data_movimento = models.DateTimeField('data da movimentação', auto_now_add=True)

    class Meta:
        verbose_name = 'movimentação'
        verbose_name_plural = 'movimentações'
        ordering = ['-data_movimento']

    def __str__(self):
        return f'{self.get_tipo_movimento_display()} - {self.produto}'
