"""Comparativo de contagens: agrupa inventários existentes como C1, C2 e C3.

Os registros de contagem continuam em `InventarioItem`; aqui só ficam a
associação ordenada e a fotografia imutável dos saldos SAP usada como
referência (a importação SAP substitui a tabela inteira a cada carga).
"""

from django.conf import settings
from django.db import models


class ReferenciaSAP(models.Model):
    """Fotografia persistente dos saldos SAP (canais 1 e 110) em um instante."""

    descricao = models.CharField('descrição', max_length=255)
    arquivo_origem = models.CharField('arquivo de origem', max_length=255, blank=True)
    data_importacao_origem = models.DateTimeField(
        'data da importação SAP de origem',
        null=True,
        blank=True,
    )
    data_captura = models.DateTimeField('data da captura', auto_now_add=True)
    usuario_captura = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='referencias_sap_capturadas',
        verbose_name='usuário da captura',
    )
    quantidade_skus = models.PositiveIntegerField('quantidade de SKUs', default=0)
    # Campos de canal carregados na importação de origem (ex.: canal_1,
    # canal_110). None: importação anterior ao registro de canais.
    canais_carregados = models.JSONField('canais carregados', null=True, blank=True)

    class Meta:
        verbose_name = 'referência SAP (fotografia)'
        verbose_name_plural = 'referências SAP (fotografias)'
        ordering = ['-data_captura']

    def __str__(self):
        return self.descricao


class ReferenciaSAPItem(models.Model):
    referencia = models.ForeignKey(
        ReferenciaSAP,
        on_delete=models.CASCADE,
        related_name='itens',
        verbose_name='referência SAP',
    )
    produto = models.ForeignKey(
        'produtos.Produto',
        on_delete=models.PROTECT,
        related_name='referencias_sap_itens',
        verbose_name='produto',
    )
    codigo_produto = models.CharField('código do produto', max_length=50)
    canal_1 = models.DecimalField('canal 1 (BRIDA)', max_digits=12, decimal_places=3)
    canal_110 = models.DecimalField('canal 110 (COSAN)', max_digits=12, decimal_places=3)

    class Meta:
        verbose_name = 'saldo da referência SAP'
        verbose_name_plural = 'saldos da referência SAP'
        ordering = ['referencia', 'codigo_produto']
        constraints = [
            models.UniqueConstraint(
                fields=['referencia', 'produto'],
                name='uniq_referenciasapitem_referencia_produto',
            ),
        ]

    def __str__(self):
        return f'{self.codigo_produto} @ {self.referencia_id}'


class ComparativoContagem(models.Model):
    class Estado(models.TextChoices):
        ABERTA = 'ABERTA', 'Aberta'
        ENCERRADA = 'ENCERRADA', 'Finalizada'

    nome = models.CharField('nome', max_length=150)
    referencia_sap = models.ForeignKey(
        ReferenciaSAP,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='comparativos',
        verbose_name='referência SAP',
    )
    estado = models.CharField(
        'estado operacional',
        max_length=20,
        choices=Estado.choices,
        default=Estado.ABERTA,
    )
    usuario_criacao = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='comparativos_contagem_criados',
        verbose_name='usuário de criação',
    )
    data_criacao = models.DateTimeField('data de criação', auto_now_add=True)
    data_atualizacao = models.DateTimeField('data de alteração', auto_now=True)
    # Resultado gravado ao salvar/finalizar: lançamentos, saldos SAP e
    # produtos usados no cálculo. Depois de gravado, telas e Excel leem daqui.
    snapshot_resultado = models.JSONField('resultado gravado', null=True, blank=True)
    data_finalizacao = models.DateTimeField('data da finalização', null=True, blank=True)
    usuario_finalizacao = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='comparativos_contagem_finalizados',
        verbose_name='usuário da finalização',
    )

    class Meta:
        verbose_name = 'comparativo de contagens'
        verbose_name_plural = 'comparativos de contagens'
        ordering = ['-data_criacao']

    def __str__(self):
        return self.nome

    @property
    def finalizado(self) -> bool:
        return self.snapshot_resultado is not None


class ComparativoContagemInventario(models.Model):
    """Associação ordenada: ordem 1 = C1, 2 = C2, 3 = C3 (opcional)."""

    ORDEM_MAXIMA = 3

    comparativo = models.ForeignKey(
        ComparativoContagem,
        on_delete=models.CASCADE,
        related_name='associacoes',
        verbose_name='comparativo',
    )
    inventario = models.ForeignKey(
        'inventario.Inventario',
        on_delete=models.PROTECT,
        related_name='associacoes_comparativo',
        verbose_name='inventário',
    )
    ordem = models.PositiveSmallIntegerField('ordem da contagem')
    usuario_associacao = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='associacoes_comparativo_feitas',
        verbose_name='usuário da associação',
    )
    data_associacao = models.DateTimeField('data da associação', auto_now_add=True)

    class Meta:
        verbose_name = 'contagem do comparativo'
        verbose_name_plural = 'contagens do comparativo'
        ordering = ['comparativo', 'ordem']
        constraints = [
            models.UniqueConstraint(
                fields=['comparativo', 'ordem'],
                name='uniq_comparativo_ordem',
            ),
            models.UniqueConstraint(
                fields=['comparativo', 'inventario'],
                name='uniq_comparativo_inventario',
            ),
            models.CheckConstraint(
                condition=models.Q(ordem__gte=1, ordem__lte=3),
                name='comparativo_ordem_1_a_3',
            ),
        ]

    def __str__(self):
        return f'C{self.ordem} — inventário #{self.inventario_id}'


class ComparativoContagemEvento(models.Model):
    """Auditoria das alterações de configuração do comparativo."""

    comparativo = models.ForeignKey(
        ComparativoContagem,
        on_delete=models.CASCADE,
        related_name='eventos',
        verbose_name='comparativo',
    )
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='eventos_comparativo',
        verbose_name='usuário',
    )
    acao = models.CharField('ação', max_length=40)
    dados = models.JSONField('dados', default=dict, blank=True)
    data_hora = models.DateTimeField('data e hora', auto_now_add=True)

    class Meta:
        verbose_name = 'evento do comparativo'
        verbose_name_plural = 'eventos do comparativo'
        ordering = ['-data_hora']

    def __str__(self):
        return f'{self.acao} — {self.comparativo_id}'


class ComparativoAcuracidade(models.Model):
    """Registro salvo da acuracidade do estoque contado em relação ao SAP.

    Fotografia do cálculo no momento do registro: não muda se as contagens
    ou a referência SAP mudarem depois.
    """

    comparativo = models.ForeignKey(
        ComparativoContagem,
        on_delete=models.CASCADE,
        related_name='acuracidades',
        verbose_name='comparativo',
    )
    canal = models.CharField('canal SAP', max_length=10)
    referencia_sap = models.ForeignKey(
        ReferenciaSAP,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='acuracidades',
        verbose_name='referência SAP',
    )
    percentual = models.DecimalField(
        'acuracidade (%)', max_digits=6, decimal_places=2, null=True, blank=True,
    )
    skus_avaliados = models.PositiveIntegerField('SKUs avaliados', default=0)
    skus_acertos = models.PositiveIntegerField('SKUs iguais ao SAP', default=0)
    skus_divergentes = models.PositiveIntegerField('SKUs diferentes do SAP', default=0)
    skus_sem_consenso = models.PositiveIntegerField('SKUs sem consenso', default=0)
    skus_total = models.PositiveIntegerField('SKUs no comparativo', default=0)
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='acuracidades_comparativo',
        verbose_name='usuário',
    )
    data_hora = models.DateTimeField('data e hora', auto_now_add=True)

    class Meta:
        verbose_name = 'acuracidade do comparativo'
        verbose_name_plural = 'acuracidades do comparativo'
        ordering = ['-data_hora']

    def __str__(self):
        return f'{self.comparativo_id} · {self.canal} · {self.percentual}%'
