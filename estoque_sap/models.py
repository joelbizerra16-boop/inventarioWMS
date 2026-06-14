from django.db import models

from produtos.models import Produto


class EstoqueSAP(models.Model):
    produto = models.ForeignKey(
        Produto,
        on_delete=models.PROTECT,
        related_name='estoques_sap',
        verbose_name='produto',
    )
    canal_0 = models.DecimalField(
        'canal 0', max_digits=12, decimal_places=3, default=0
    )
    canal_1 = models.DecimalField(
        'canal 1', max_digits=12, decimal_places=3, default=0
    )
    canal_2 = models.DecimalField(
        'canal 2', max_digits=12, decimal_places=3, default=0
    )
    canal_66 = models.DecimalField(
        'canal 66', max_digits=12, decimal_places=3, default=0
    )
    canal_80 = models.DecimalField(
        'canal 80', max_digits=12, decimal_places=3, default=0
    )
    canal_81 = models.DecimalField(
        'canal 81', max_digits=12, decimal_places=3, default=0
    )
    canal_82 = models.DecimalField(
        'canal 82', max_digits=12, decimal_places=3, default=0
    )
    canal_99 = models.DecimalField(
        'canal 99', max_digits=12, decimal_places=3, default=0
    )
    canal_110 = models.DecimalField(
        'canal 110', max_digits=12, decimal_places=3, default=0
    )
    total = models.DecimalField(
        'total', max_digits=12, decimal_places=3, default=0
    )
    arquivo_origem = models.CharField('arquivo de origem', max_length=255)
    data_importacao = models.DateTimeField('data de importação', auto_now_add=True)

    class Meta:
        verbose_name = 'estoque SAP'
        verbose_name_plural = 'estoques SAP'
        ordering = ['-data_importacao']

    def __str__(self):
        return str(self.produto)
