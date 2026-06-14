from django.db import models



from posicoes.models import Posicao

from produtos.models import Produto





class EstoqueFisico(models.Model):

    posicao = models.ForeignKey(

        Posicao,

        on_delete=models.PROTECT,

        related_name='estoques_fisicos',

        verbose_name='posição',

    )

    produto = models.ForeignKey(

        Produto,

        on_delete=models.PROTECT,

        related_name='estoques_fisicos',

        verbose_name='produto',

    )

    quantidade = models.DecimalField(

        'quantidade', max_digits=12, decimal_places=3, default=0

    )

    inventario_origem = models.ForeignKey(

        'inventario.Inventario',

        on_delete=models.PROTECT,

        related_name='estoques_fisicos_publicados',

        verbose_name='inventário de origem',

        null=True,

        blank=True,

    )

    data_publicacao = models.DateTimeField('data de publicação', null=True, blank=True)

    data_contagem = models.DateTimeField('data da contagem')

    data_atualizacao = models.DateTimeField('data de atualização', auto_now=True)



    class Meta:

        verbose_name = 'estoque físico'

        verbose_name_plural = 'estoques físicos'

        ordering = ['-data_atualizacao']

        constraints = [

            models.UniqueConstraint(

                fields=['produto', 'posicao'],

                name='uniq_estoquefisico_produto_posicao',

            ),

        ]



    def __str__(self):

        return f'{self.posicao} - {self.produto}'


