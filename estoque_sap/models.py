from django.db import models

from produtos.models import Produto


class ImportacaoEstoqueSAP(models.Model):
    """Lote de importação SAP concluído (gravado na mesma transação dos saldos).

    `canais_carregados` lista os campos de canal que existiam na planilha;
    um canal ausente da planilha é gravado como zero em EstoqueSAP, então este
    registro é o que permite distinguir "canal não carregado" de saldo zero.
    """

    arquivo_origem = models.CharField('arquivo de origem', max_length=255, blank=True)
    data_importacao = models.DateTimeField('data de importação', db_index=True)
    canais_carregados = models.JSONField('canais carregados', null=True, blank=True)
    quantidade_produtos = models.PositiveIntegerField('produtos importados', default=0)

    class Meta:
        verbose_name = 'importação de estoque SAP'
        verbose_name_plural = 'importações de estoque SAP'
        ordering = ['-data_importacao']

    def __str__(self):
        return f'{self.arquivo_origem} · {self.data_importacao:%d/%m/%Y %H:%M}'


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
    importacao = models.ForeignKey(
        ImportacaoEstoqueSAP,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='saldos',
        verbose_name='importação',
    )

    class Meta:
        verbose_name = 'estoque SAP'
        verbose_name_plural = 'estoques SAP'
        ordering = ['-data_importacao']

    def __str__(self):
        return str(self.produto)
