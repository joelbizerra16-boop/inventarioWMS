from django.conf import settings
from django.db import models

from accounts.models import Usuario
from posicoes.models import Posicao
from produtos.models import Produto


class Inventario(models.Model):
    class Status(models.TextChoices):
        ABERTO = 'ABERTO', 'Aberto'
        EM_ANDAMENTO = 'EM_ANDAMENTO', 'Em andamento'
        FINALIZADO = 'FINALIZADO', 'Finalizado'

    class StatusAprovacao(models.TextChoices):
        PENDENTE_APROVACAO = 'PENDENTE_APROVACAO', 'Pendente de Aprovação'
        APROVADO = 'APROVADO', 'Aprovado'

    usuario = models.ForeignKey(
        Usuario,
        on_delete=models.PROTECT,
        related_name='inventarios',
        verbose_name='usuário',
    )
    observacao = models.TextField('observação', blank=True)
    status = models.CharField(
        'status',
        max_length=20,
        choices=Status.choices,
    )
    status_aprovacao = models.CharField(
        'status de aprovação',
        max_length=30,
        blank=True,
        choices=StatusAprovacao.choices,
    )
    confronto_executado_em = models.DateTimeField(
        'confronto executado em',
        null=True,
        blank=True,
    )
    data_finalizacao = models.DateTimeField(
        'data de finalização',
        null=True,
        blank=True,
    )
    usuario_finalizacao = models.ForeignKey(
        Usuario,
        on_delete=models.PROTECT,
        related_name='inventarios_finalizados',
        verbose_name='usuário de finalização',
        null=True,
        blank=True,
    )
    quantidade_itens = models.PositiveIntegerField(
        'quantidade de itens (snapshot)',
        null=True,
        blank=True,
    )
    quantidade_produtos = models.PositiveIntegerField(
        'quantidade de produtos (snapshot)',
        null=True,
        blank=True,
    )
    quantidade_conciliados = models.PositiveIntegerField(
        'produtos conciliados (snapshot)',
        null=True,
        blank=True,
    )
    quantidade_divergentes = models.PositiveIntegerField(
        'produtos divergentes (snapshot)',
        null=True,
        blank=True,
    )
    taxa_acuracidade = models.DecimalField(
        'taxa de acuracidade (snapshot)',
        max_digits=6,
        decimal_places=2,
        null=True,
        blank=True,
    )
    snapshot_resultado = models.JSONField(
        'snapshot do resultado',
        null=True,
        blank=True,
    )
    data_criacao = models.DateTimeField('data de criação', auto_now_add=True)

    class Meta:
        verbose_name = 'inventário'
        verbose_name_plural = 'inventários'
        ordering = ['-data_criacao']

    def __str__(self):
        return f'{self.usuario} - {self.data_criacao:%d/%m/%Y %H:%M}'


class InventarioItem(models.Model):
    class OrigemContagem(models.TextChoices):
        POCKET = 'POCKET', 'Pocket'
        WEB = 'WEB', 'Web'

    inventario = models.ForeignKey(
        Inventario,
        on_delete=models.CASCADE,
        related_name='itens',
        verbose_name='inventário',
    )
    posicao = models.ForeignKey(
        Posicao,
        on_delete=models.PROTECT,
        related_name='itens_inventario',
        verbose_name='posição',
    )
    produto = models.ForeignKey(
        Produto,
        on_delete=models.PROTECT,
        related_name='itens_inventario',
        verbose_name='produto',
    )
    quantidade_fisica = models.DecimalField(
        'quantidade física', max_digits=12, decimal_places=3, default=0
    )
    usuario_contagem = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='itens_inventario_contados',
        verbose_name='usuário da contagem',
    )
    data_contagem = models.DateTimeField(
        'data da contagem',
        null=True,
        blank=True,
    )
    origem_contagem = models.CharField(
        'origem da contagem',
        max_length=10,
        blank=True,
        choices=OrigemContagem.choices,
    )

    class Meta:
        verbose_name = 'item de inventário'
        verbose_name_plural = 'itens de inventário'
        ordering = ['inventario', 'produto']
        constraints = [
            models.UniqueConstraint(
                fields=['inventario', 'posicao', 'produto'],
                name='uniq_inventarioitem_inventario_posicao_produto',
            ),
        ]

    def __str__(self):
        return str(self.produto)

    @property
    def usuario_contagem_nome(self) -> str:
        if not self.usuario_contagem_id:
            return 'Não informado'
        perfil = getattr(self.usuario_contagem, 'perfil_operacional', None)
        if perfil:
            return perfil.nome
        return (
            self.usuario_contagem.get_full_name()
            or self.usuario_contagem.get_username()
        )

    @property
    def origem_contagem_rotulo(self) -> str:
        if not self.origem_contagem:
            return 'Legado'
        return self.get_origem_contagem_display()


class CicloInventario(models.Model):
    class StatusCiclo(models.TextChoices):
        ATIVO = 'ATIVO', 'Ativo'
        ENCERRADO = 'ENCERRADO', 'Encerrado'
        ARQUIVADO = 'ARQUIVADO', 'Arquivado'

    data_criacao = models.DateTimeField('data de criação', auto_now_add=True)
    data_encerramento = models.DateTimeField(
        'data de encerramento',
        null=True,
        blank=True,
    )
    quantidade_skus_planejados = models.PositiveIntegerField(
        'quantidade de SKUs planejados',
        null=True,
        blank=True,
    )
    skus_por_dia = models.PositiveIntegerField(
        'SKUs por dia na execução',
        null=True,
        blank=True,
    )
    dia_execucao = models.PositiveIntegerField(
        'dia de execução atual',
        default=1,
    )
    embalagens_filtro = models.JSONField(
        'embalagens do ciclo',
        default=list,
        blank=True,
    )
    canais_filtro = models.JSONField(
        'canais do ciclo',
        default=list,
        blank=True,
    )
    completar_lote_automaticamente = models.BooleanField(
        'completar lote automaticamente',
        default=False,
    )
    respeitar_somente_embalagens = models.BooleanField(
        'respeitar somente embalagens selecionadas',
        default=False,
    )
    ativo = models.BooleanField('ativo', default=True)
    status_ciclo = models.CharField(
        'status do ciclo',
        max_length=20,
        choices=StatusCiclo.choices,
        default=StatusCiclo.ATIVO,
    )
    quantidade_skus_contados = models.PositiveIntegerField(
        'SKUs contados (snapshot)',
        null=True,
        blank=True,
    )
    quantidade_skus_divergentes = models.PositiveIntegerField(
        'SKUs divergentes (snapshot)',
        null=True,
        blank=True,
    )
    quantidade_skus_validados = models.PositiveIntegerField(
        'SKUs validados (snapshot)',
        null=True,
        blank=True,
    )
    percentual_executado = models.DecimalField(
        'percentual executado (snapshot)',
        max_digits=6,
        decimal_places=2,
        null=True,
        blank=True,
    )
    taxa_acuracidade = models.DecimalField(
        'taxa de acuracidade (snapshot)',
        max_digits=6,
        decimal_places=2,
        null=True,
        blank=True,
    )
    criterio_utilizado = models.TextField('critério utilizado', blank=True)
    canal_utilizado = models.CharField('canal utilizado', max_length=100, blank=True)
    usuario_criacao = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='ciclos_inventario_criados',
        verbose_name='usuário de criação',
    )
    usuario_encerramento = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='ciclos_inventario_encerrados',
        verbose_name='usuário de encerramento',
    )

    class Meta:
        verbose_name = 'ciclo de inventário cíclico'
        verbose_name_plural = 'ciclos de inventário cíclico'
        ordering = ['-data_criacao']

    def __str__(self):
        return f'Ciclo #{self.pk}'


class CicloInventarioSku(models.Model):
    class StatusContagem(models.TextChoices):
        PENDENTE = 'PENDENTE', 'Pendente'
        CONTADO = 'CONTADO', 'Contado'
        DIVERGENTE = 'DIVERGENTE', 'Divergente'
        RECONTAGEM = 'RECONTAGEM', 'Recontagem'
        VALIDADO = 'VALIDADO', 'Validado'
        VALIDADO_DIVERGENCIA = 'VALIDADO_DIVERGENCIA', 'Validado c/ divergência'
        EXCLUIDO = 'EXCLUIDO', 'Excluído'

    ciclo = models.ForeignKey(
        CicloInventario,
        on_delete=models.CASCADE,
        related_name='skus',
        verbose_name='ciclo',
    )
    produto = models.ForeignKey(
        Produto,
        on_delete=models.PROTECT,
        related_name='skus_ciclo_inventario',
        verbose_name='produto',
    )
    codigo_produto = models.CharField('código do produto', max_length=50)
    descricao = models.CharField('descrição', max_length=255)
    embalagem = models.CharField('embalagem', max_length=100, blank=True)
    setor = models.CharField('setor', max_length=100, blank=True)
    quantidade_sap = models.DecimalField(
        'quantidade SAP', max_digits=12, decimal_places=3, default=0
    )
    quantidade_cosan = models.DecimalField(
        'quantidade Cosan congelada',
        max_digits=12,
        decimal_places=3,
        null=True,
        blank=True,
    )
    quantidade_brida = models.DecimalField(
        'quantidade Brida congelada',
        max_digits=12,
        decimal_places=3,
        null=True,
        blank=True,
    )
    quantidade_fisica = models.DecimalField(
        'quantidade física consolidada',
        max_digits=12,
        decimal_places=3,
        null=True,
        blank=True,
    )
    diferenca = models.DecimalField(
        'diferença',
        max_digits=12,
        decimal_places=3,
        null=True,
        blank=True,
    )
    data_atualizacao_sap = models.DateTimeField(
        'data de atualização SAP',
        null=True,
        blank=True,
    )
    status_contagem = models.CharField(
        'status da contagem',
        max_length=20,
        choices=StatusContagem.choices,
        default=StatusContagem.PENDENTE,
    )
    usuario_recontagem = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='skus_ciclo_recontados',
        verbose_name='usuário da recontagem',
    )
    data_recontagem = models.DateTimeField('data da recontagem', null=True, blank=True)
    ordem_planejamento = models.PositiveIntegerField(
        'ordem no planejamento',
        default=0,
    )
    motivo_exclusao = models.TextField('motivo da exclusão', blank=True)
    usuario_exclusao = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='skus_ciclo_excluidos',
        verbose_name='usuário da exclusão',
    )
    data_exclusao = models.DateTimeField('data da exclusão', null=True, blank=True)

    class Meta:
        verbose_name = 'SKU do ciclo cíclico'
        verbose_name_plural = 'SKUs do ciclo cíclico'
        ordering = ['ciclo', 'codigo_produto']
        constraints = [
            models.UniqueConstraint(
                fields=['ciclo', 'produto'],
                name='uniq_ciclosku_ciclo_produto',
            ),
        ]

    def __str__(self):
        return self.codigo_produto

    @property
    def usuarios_contagem_nomes(self) -> list[str]:
        nomes = []
        vistos = set()
        for posicao in self.posicoes.select_related(
            'usuario_contagem',
            'usuario_contagem__perfil_operacional',
        ):
            nome = posicao.usuario_contagem_nome
            if nome != 'Não informado' and nome not in vistos:
                vistos.add(nome)
                nomes.append(nome)
        return nomes


class CicloInventarioItem(models.Model):
    class StatusContagem(models.TextChoices):
        PENDENTE = 'PENDENTE', 'Pendente'
        EM_CONTAGEM = 'EM_CONTAGEM', 'Em Contagem'
        CONTADO = 'CONTADO', 'Contado'
        DIVERGENTE = 'DIVERGENTE', 'Divergente'
        RECONTAGEM = 'RECONTAGEM', 'Em Recontagem'
        APROVADA = 'APROVADA', 'Aprovada'
        FINALIZADA = 'FINALIZADA', 'Finalizada'
        VALIDADO = 'VALIDADO', 'Validado'

    class OrigemContagem(models.TextChoices):
        POCKET = 'POCKET', 'Pocket'
        WEB = 'WEB', 'Web'
        IMPORTACAO = 'IMPORTACAO', 'Importação'
        RECONTAGEM = 'RECONTAGEM', 'Recontagem'

    ciclo = models.ForeignKey(
        CicloInventario,
        on_delete=models.CASCADE,
        related_name='itens',
        verbose_name='ciclo',
    )
    ciclo_sku = models.ForeignKey(
        CicloInventarioSku,
        on_delete=models.CASCADE,
        related_name='posicoes',
        verbose_name='SKU do ciclo',
        null=True,
        blank=True,
    )
    produto = models.ForeignKey(
        Produto,
        on_delete=models.PROTECT,
        related_name='itens_ciclo_inventario',
        verbose_name='produto',
    )
    codigo_produto = models.CharField('código do produto', max_length=50)
    descricao = models.CharField('descrição', max_length=255)
    embalagem = models.CharField('embalagem', max_length=100, blank=True)
    posicao = models.ForeignKey(
        Posicao,
        on_delete=models.PROTECT,
        related_name='itens_ciclo_inventario',
        verbose_name='posição',
    )
    codigo_posicao = models.CharField('código da posição', max_length=50)
    alocacao = models.CharField('alocação', max_length=100, blank=True)
    setor = models.CharField('setor', max_length=100, blank=True)
    quantidade_sap = models.DecimalField(
        'quantidade SAP', max_digits=12, decimal_places=3, default=0
    )
    quantidade_fisica = models.DecimalField(
        'quantidade física',
        max_digits=12,
        decimal_places=3,
        null=True,
        blank=True,
    )
    diferenca = models.DecimalField(
        'diferença',
        max_digits=12,
        decimal_places=3,
        null=True,
        blank=True,
    )
    usuario_contagem = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='itens_ciclo_contados',
        verbose_name='usuário da contagem',
    )
    data_contagem = models.DateTimeField('data da contagem', null=True, blank=True)
    origem_contagem = models.CharField(
        'origem da contagem',
        max_length=15,
        blank=True,
        choices=OrigemContagem.choices,
    )
    dispositivo_contagem = models.CharField(
        'dispositivo da contagem',
        max_length=100,
        blank=True,
    )
    data_atualizacao_sap = models.DateTimeField(
        'data de atualização SAP',
        null=True,
        blank=True,
    )
    status_contagem = models.CharField(
        'status da contagem',
        max_length=20,
        choices=StatusContagem.choices,
        default=StatusContagem.PENDENTE,
    )
    usuario_recontagem = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='itens_ciclo_recontados',
        verbose_name='usuário da recontagem',
    )
    data_recontagem = models.DateTimeField('data da recontagem', null=True, blank=True)
    quantidade_recontagem = models.DecimalField(
        'quantidade da recontagem',
        max_digits=12,
        decimal_places=3,
        null=True,
        blank=True,
    )

    class Meta:
        verbose_name = 'posição do ciclo cíclico'
        verbose_name_plural = 'posições do ciclo cíclico'
        ordering = ['ciclo', 'codigo_produto', 'codigo_posicao']
        constraints = [
            models.UniqueConstraint(
                fields=['ciclo', 'produto', 'posicao'],
                name='uniq_cicloitem_ciclo_produto_posicao',
            ),
        ]

    def __str__(self):
        return f'{self.codigo_produto} — {self.codigo_posicao}'

    @property
    def usuario_contagem_nome(self) -> str:
        if not self.usuario_contagem_id:
            return 'Não informado'
        perfil = getattr(self.usuario_contagem, 'perfil_operacional', None)
        if perfil:
            return perfil.nome
        return (
            self.usuario_contagem.get_full_name()
            or self.usuario_contagem.get_username()
        )

    @property
    def origem_contagem_rotulo(self) -> str:
        if not self.origem_contagem:
            return ''
        return self.get_origem_contagem_display()


class CicloLoteExecucao(models.Model):
    class Status(models.TextChoices):
        ATIVO = 'ATIVO', 'Ativo'
        SUBSTITUIDO = 'SUBSTITUIDO', 'Substituído'
        ENCERRADO = 'ENCERRADO', 'Encerrado'

    ciclo = models.ForeignKey(
        CicloInventario,
        on_delete=models.CASCADE,
        related_name='lotes_execucao',
        verbose_name='ciclo',
    )
    data_geracao = models.DateTimeField('data de geração', auto_now_add=True)
    status = models.CharField(
        'status',
        max_length=20,
        choices=Status.choices,
        default=Status.ATIVO,
    )
    quantidade_solicitada = models.PositiveIntegerField(
        'quantidade solicitada',
        null=True,
        blank=True,
    )
    embalagens = models.JSONField('embalagens', default=list, blank=True)
    canal = models.CharField('canal', max_length=100, blank=True)
    respeitar_somente_embalagens = models.BooleanField(
        'respeitar somente embalagens',
        default=False,
    )
    usuario_geracao = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='lotes_ciclico_gerados',
        verbose_name='usuário de geração',
    )

    class Meta:
        verbose_name = 'lote de execução cíclica'
        verbose_name_plural = 'lotes de execução cíclica'
        ordering = ['-data_geracao']
        indexes = [
            models.Index(
                fields=['ciclo', 'status', '-data_geracao'],
                name='idx_ciclo_lote_ativo',
            ),
        ]

    def __str__(self):
        return f'Lote #{self.pk} — Ciclo #{self.ciclo_id}'


class CicloLoteExecucaoItem(models.Model):
    class Status(models.TextChoices):
        PENDENTE = 'PENDENTE', 'Pendente'
        CONTADO = 'CONTADO', 'Contado'
        EXCLUIDO = 'EXCLUIDO', 'Excluído'

    lote = models.ForeignKey(
        CicloLoteExecucao,
        on_delete=models.CASCADE,
        related_name='itens',
        verbose_name='lote',
    )
    ciclo_sku = models.ForeignKey(
        CicloInventarioSku,
        on_delete=models.CASCADE,
        related_name='lotes_execucao_itens',
        verbose_name='SKU do ciclo',
    )
    sequencia = models.PositiveIntegerField('sequência')
    status = models.CharField(
        'status',
        max_length=20,
        choices=Status.choices,
        default=Status.PENDENTE,
    )

    class Meta:
        verbose_name = 'item do lote cíclico'
        verbose_name_plural = 'itens do lote cíclico'
        ordering = ['sequencia']
        constraints = [
            models.UniqueConstraint(
                fields=['lote', 'ciclo_sku'],
                name='uniq_ciclo_lote_item_sku',
            ),
        ]

    def __str__(self):
        return f'{self.lote_id} — {self.ciclo_sku.codigo_produto}'


class CicloAuditoriaHistorico(models.Model):
    class TipoRegistro(models.TextChoices):
        CONTAGEM = 'CONTAGEM', 'Contagem'
        RECONTAGEM = 'RECONTAGEM', 'Recontagem'
        CONSOLIDACAO = 'CONSOLIDACAO', 'Consolidação'
        EXCLUSAO = 'EXCLUSAO', 'Exclusão'
        VALIDACAO = 'VALIDACAO', 'Validação'
        EDICAO = 'EDICAO', 'Edição'

    ciclo_sku = models.ForeignKey(
        CicloInventarioSku,
        on_delete=models.CASCADE,
        related_name='historico',
        verbose_name='SKU do ciclo',
        null=True,
        blank=True,
    )
    item = models.ForeignKey(
        CicloInventarioItem,
        on_delete=models.CASCADE,
        related_name='historico',
        verbose_name='posição do ciclo',
        null=True,
        blank=True,
    )
    codigo_posicao = models.CharField('código da posição', max_length=50, blank=True)
    tipo = models.CharField('tipo', max_length=20, choices=TipoRegistro.choices)
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='historico_ciclo_auditoria',
        verbose_name='usuário',
    )
    data_hora = models.DateTimeField('data e hora')
    quantidade_sap_momento = models.DecimalField(
        'quantidade SAP no momento',
        max_digits=12,
        decimal_places=3,
        default=0,
    )
    quantidade_fisica = models.DecimalField(
        'quantidade física',
        max_digits=12,
        decimal_places=3,
        default=0,
    )
    diferenca = models.DecimalField(
        'diferença',
        max_digits=12,
        decimal_places=3,
        default=0,
    )
    origem_contagem = models.CharField(
        'origem da contagem',
        max_length=15,
        blank=True,
        choices=CicloInventarioItem.OrigemContagem.choices,
    )
    dispositivo_contagem = models.CharField(
        'dispositivo da contagem',
        max_length=100,
        blank=True,
    )
    motivo = models.TextField('motivo', blank=True)

    class Meta:
        verbose_name = 'histórico de auditoria cíclica'
        verbose_name_plural = 'históricos de auditoria cíclica'
        ordering = ['-data_hora']

    def __str__(self):
        if self.item_id:
            return f'{self.item} — {self.get_tipo_display()}'
        return f'{self.ciclo_sku} — {self.get_tipo_display()}'


class CicloEstoqueFisicoAjuste(models.Model):
    class OrigemAjuste(models.TextChoices):
        INVENTARIO_CICLICO = 'INVENTARIO_CICLICO', 'Inventário cíclico'

    ciclo = models.ForeignKey(
        CicloInventario,
        on_delete=models.PROTECT,
        related_name='ajustes_estoque_fisico',
        verbose_name='ciclo',
    )
    ciclo_sku = models.ForeignKey(
        CicloInventarioSku,
        on_delete=models.PROTECT,
        related_name='ajustes_estoque_fisico',
        verbose_name='SKU do ciclo',
    )
    item = models.ForeignKey(
        CicloInventarioItem,
        on_delete=models.SET_NULL,
        related_name='ajustes_estoque_fisico',
        verbose_name='posição auditada',
        null=True,
        blank=True,
    )
    produto = models.ForeignKey(
        Produto,
        on_delete=models.PROTECT,
        related_name='ajustes_ciclico_estoque_fisico',
        verbose_name='produto',
    )
    posicao = models.ForeignKey(
        Posicao,
        on_delete=models.PROTECT,
        related_name='ajustes_ciclico_estoque_fisico',
        verbose_name='posição',
    )
    codigo_produto = models.CharField('código do produto', max_length=50)
    codigo_posicao = models.CharField('código da posição', max_length=50)
    quantidade_anterior = models.DecimalField(
        'quantidade anterior',
        max_digits=12,
        decimal_places=3,
        default=0,
    )
    quantidade_nova = models.DecimalField(
        'quantidade nova',
        max_digits=12,
        decimal_places=3,
        default=0,
    )
    diferenca = models.DecimalField(
        'diferença',
        max_digits=12,
        decimal_places=3,
        default=0,
    )
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='ajustes_ciclico_estoque_fisico',
        verbose_name='usuário',
    )
    data_hora = models.DateTimeField('data e hora')
    origem = models.CharField(
        'origem',
        max_length=30,
        choices=OrigemAjuste.choices,
        default=OrigemAjuste.INVENTARIO_CICLICO,
    )
    motivo = models.TextField('motivo', blank=True)

    class Meta:
        verbose_name = 'ajuste de estoque físico (cíclico)'
        verbose_name_plural = 'ajustes de estoque físico (cíclico)'
        ordering = ['-data_hora']

    def __str__(self):
        return f'{self.codigo_produto} @ {self.codigo_posicao} ({self.diferenca:+})'


from inventario.models_operacional import (  # noqa: E402, F401
    InventarioAuditoriaEvento,
    InventarioLock,
    InventarioTarefa,
)
