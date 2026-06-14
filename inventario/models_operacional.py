"""Modelos operacionais multiusuário: locks, tarefas e auditoria."""

from django.conf import settings
from django.db import models

from posicoes.models import Posicao
from produtos.models import Produto


class InventarioLock(models.Model):
    class TipoInventario(models.TextChoices):
        GERAL = 'GERAL', 'Inventário Geral'
        CICLICO = 'CICLICO', 'Inventário Cíclico'

    class MotivoLiberacao(models.TextChoices):
        MANUAL = 'MANUAL', 'Liberação manual'
        TIMEOUT = 'TIMEOUT', 'Timeout automático'
        CONCLUIDO = 'CONCLUIDO', 'Contagem concluída'
        SESSAO = 'SESSAO', 'Encerramento de sessão'
        ADMIN = 'ADMIN', 'Liberação administrativa'

    tipo_inventario = models.CharField(
        'tipo de inventário',
        max_length=10,
        choices=TipoInventario.choices,
    )
    inventario = models.ForeignKey(
        'inventario.Inventario',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='locks',
        verbose_name='inventário',
    )
    ciclo = models.ForeignKey(
        'inventario.CicloInventario',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='locks',
        verbose_name='ciclo',
    )
    ciclo_item = models.ForeignKey(
        'inventario.CicloInventarioItem',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='locks',
        verbose_name='item cíclico',
    )
    tarefa = models.ForeignKey(
        'inventario.InventarioTarefa',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='locks',
        verbose_name='tarefa',
    )
    posicao = models.ForeignKey(
        Posicao,
        on_delete=models.PROTECT,
        related_name='locks_inventario',
        verbose_name='posição',
    )
    produto = models.ForeignKey(
        Produto,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='locks_inventario',
        verbose_name='produto',
    )
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='locks_inventario',
        verbose_name='usuário',
    )
    dispositivo = models.CharField('dispositivo', max_length=200, blank=True)
    session_key = models.CharField('chave de sessão', max_length=64, blank=True)
    adquirido_em = models.DateTimeField('adquirido em')
    renovado_em = models.DateTimeField('renovado em')
    expira_em = models.DateTimeField('expira em')
    ativo = models.BooleanField('ativo', default=True)
    liberado_em = models.DateTimeField('liberado em', null=True, blank=True)
    motivo_liberacao = models.CharField(
        'motivo da liberação',
        max_length=20,
        blank=True,
        choices=MotivoLiberacao.choices,
    )

    class Meta:
        verbose_name = 'lock de inventário'
        verbose_name_plural = 'locks de inventário'
        ordering = ['-adquirido_em']
        indexes = [
            models.Index(
                fields=['ativo', 'expira_em'],
                name='idx_lock_ativo_expira',
            ),
            models.Index(
                fields=['tipo_inventario', 'inventario', 'posicao', 'ativo'],
                name='idx_lock_geral_pos',
            ),
            models.Index(
                fields=['tipo_inventario', 'ciclo', 'posicao', 'ativo'],
                name='idx_lock_ciclico_pos',
            ),
            models.Index(
                fields=['usuario', 'ativo'],
                name='idx_lock_usuario_ativo',
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['tipo_inventario', 'inventario', 'posicao', 'produto'],
                condition=models.Q(ativo=True, tipo_inventario='GERAL'),
                name='uniq_lock_geral_pos_prod_ativo',
            ),
            models.UniqueConstraint(
                fields=['tipo_inventario', 'ciclo', 'posicao', 'produto'],
                condition=models.Q(ativo=True, tipo_inventario='CICLICO'),
                name='uniq_lock_ciclico_pos_prod_ativo',
            ),
        ]

    def __str__(self):
        return f'Lock {self.posicao_id} — {self.usuario_id} ({self.tipo_inventario})'


class InventarioTarefa(models.Model):
    class TipoInventario(models.TextChoices):
        GERAL = 'GERAL', 'Inventário Geral'
        CICLICO = 'CICLICO', 'Inventário Cíclico'

    class Status(models.TextChoices):
        PENDENTE = 'PENDENTE', 'Pendente'
        EM_CONTAGEM = 'EM_CONTAGEM', 'Em Contagem'
        CONTADA = 'CONTADA', 'Contada'
        DIVERGENTE = 'DIVERGENTE', 'Divergente'
        EM_RECONTAGEM = 'EM_RECONTAGEM', 'Em Recontagem'
        APROVADA = 'APROVADA', 'Aprovada'
        FINALIZADA = 'FINALIZADA', 'Finalizada'

    class ModoAtribuicao(models.TextChoices):
        MANUAL = 'MANUAL', 'Manual'
        AUTOMATICA = 'AUTOMATICA', 'Automática'
        HIBRIDA = 'HIBRIDA', 'Híbrida'

    tipo_inventario = models.CharField(
        'tipo de inventário',
        max_length=10,
        choices=TipoInventario.choices,
    )
    inventario = models.ForeignKey(
        'inventario.Inventario',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='tarefas',
        verbose_name='inventário',
    )
    ciclo = models.ForeignKey(
        'inventario.CicloInventario',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='tarefas',
        verbose_name='ciclo',
    )
    ciclo_item = models.ForeignKey(
        'inventario.CicloInventarioItem',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='tarefas',
        verbose_name='item cíclico',
    )
    posicao = models.ForeignKey(
        Posicao,
        on_delete=models.PROTECT,
        related_name='tarefas_inventario',
        verbose_name='posição',
    )
    produto = models.ForeignKey(
        Produto,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='tarefas_inventario',
        verbose_name='produto',
    )
    operador = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='tarefas_inventario',
        verbose_name='operador',
    )
    status = models.CharField(
        'status',
        max_length=20,
        choices=Status.choices,
        default=Status.PENDENTE,
    )
    modo_atribuicao = models.CharField(
        'modo de atribuição',
        max_length=15,
        choices=ModoAtribuicao.choices,
        default=ModoAtribuicao.AUTOMATICA,
    )
    area_criterio = models.JSONField('critério de área', default=dict, blank=True)
    atribuido_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='tarefas_atribuidas',
        verbose_name='atribuído por',
    )
    atribuido_em = models.DateTimeField('atribuído em', auto_now_add=True)
    iniciado_em = models.DateTimeField('iniciado em', null=True, blank=True)
    finalizado_em = models.DateTimeField('finalizado em', null=True, blank=True)
    ordem = models.PositiveIntegerField('ordem', default=0)
    eh_recontagem = models.BooleanField('é recontagem', default=False)
    operadores_anteriores = models.JSONField(
        'operadores anteriores',
        default=list,
        blank=True,
    )

    class Meta:
        verbose_name = 'tarefa de inventário'
        verbose_name_plural = 'tarefas de inventário'
        ordering = ['ordem', 'pk']
        indexes = [
            models.Index(
                fields=['tipo_inventario', 'inventario', 'operador', 'status'],
                name='idx_tarefa_geral_op',
            ),
            models.Index(
                fields=['tipo_inventario', 'ciclo', 'operador', 'status'],
                name='idx_tarefa_ciclico_op',
            ),
            models.Index(
                fields=['status'],
                name='idx_tarefa_status',
            ),
        ]

    def __str__(self):
        return f'Tarefa {self.pk} — {self.posicao_id} ({self.get_status_display()})'


class InventarioAuditoriaEvento(models.Model):
    class TipoInventario(models.TextChoices):
        GERAL = 'GERAL', 'Inventário Geral'
        CICLICO = 'CICLICO', 'Inventário Cíclico'

    class Evento(models.TextChoices):
        LOCK_ADQUIRIDO = 'LOCK_ADQUIRIDO', 'Lock adquirido'
        LOCK_RENOVADO = 'LOCK_RENOVADO', 'Lock renovado'
        LOCK_LIBERADO = 'LOCK_LIBERADO', 'Lock liberado'
        LOCK_TIMEOUT = 'LOCK_TIMEOUT', 'Lock expirado (timeout)'
        TAREFA_ATRIBUIDA = 'TAREFA_ATRIBUIDA', 'Tarefa atribuída'
        TAREFA_INICIADA = 'TAREFA_INICIADA', 'Tarefa iniciada'
        TAREFA_FINALIZADA = 'TAREFA_FINALIZADA', 'Tarefa finalizada'
        CONTAGEM = 'CONTAGEM', 'Contagem registrada'
        CONTAGEM_REJEITADA = 'CONTAGEM_REJEITADA', 'Contagem rejeitada'
        STATUS_ALTERADO = 'STATUS_ALTERADO', 'Status alterado'
        RECONTAGEM_GERADA = 'RECONTAGEM_GERADA', 'Recontagem gerada'
        DIVERGENCIA = 'DIVERGENCIA', 'Divergência detectada'

    tipo_inventario = models.CharField(
        'tipo de inventário',
        max_length=10,
        choices=TipoInventario.choices,
    )
    inventario = models.ForeignKey(
        'inventario.Inventario',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='eventos_auditoria',
        verbose_name='inventário',
    )
    ciclo = models.ForeignKey(
        'inventario.CicloInventario',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='eventos_auditoria',
        verbose_name='ciclo',
    )
    tarefa = models.ForeignKey(
        InventarioTarefa,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='eventos_auditoria',
        verbose_name='tarefa',
    )
    lock = models.ForeignKey(
        InventarioLock,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='eventos_auditoria',
        verbose_name='lock',
    )
    evento = models.CharField('evento', max_length=25, choices=Evento.choices)
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='eventos_auditoria_inventario',
        verbose_name='usuário',
    )
    dispositivo = models.CharField('dispositivo', max_length=200, blank=True)
    ip = models.GenericIPAddressField('IP', null=True, blank=True)
    posicao = models.ForeignKey(
        Posicao,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='eventos_auditoria_inventario',
        verbose_name='posição',
    )
    produto = models.ForeignKey(
        Produto,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='eventos_auditoria_inventario',
        verbose_name='produto',
    )
    lote = models.CharField('lote', max_length=100, blank=True)
    quantidade = models.DecimalField(
        'quantidade',
        max_digits=12,
        decimal_places=3,
        null=True,
        blank=True,
    )
    status_anterior = models.CharField('status anterior', max_length=30, blank=True)
    status_novo = models.CharField('status novo', max_length=30, blank=True)
    dados_extras = models.JSONField('dados extras', default=dict, blank=True)
    data_hora = models.DateTimeField('data e hora')

    class Meta:
        verbose_name = 'evento de auditoria operacional'
        verbose_name_plural = 'eventos de auditoria operacional'
        ordering = ['-data_hora']
        indexes = [
            models.Index(fields=['tipo_inventario', 'inventario', 'data_hora']),
            models.Index(fields=['tipo_inventario', 'ciclo', 'data_hora']),
            models.Index(fields=['evento', 'data_hora']),
            models.Index(fields=['usuario', 'data_hora']),
        ]

    def __str__(self):
        return f'{self.get_evento_display()} — {self.data_hora:%d/%m/%Y %H:%M}'
