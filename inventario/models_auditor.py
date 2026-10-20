"""Painel do Auditor: acesso, publicação programada e trilha de eventos.

O auditor acompanha somente contagens físicas de uma conferência do
Comparativo (C1, C2 e C3 são inventários associados à conferência). As
quantidades, operadores e datas continuam no lançamento original
(`InventarioItem`): aqui só ficam referências, a agenda de disponibilização
e os eventos — nada é copiado, alterado ou retroagido.
"""

from django.conf import settings
from django.db import models


class AuditorAcesso(models.Model):
    """Auditor autorizado a acompanhar uma conferência."""

    comparativo = models.ForeignKey(
        'inventario.ComparativoContagem',
        on_delete=models.PROTECT,
        related_name='acessos_auditor',
        verbose_name='conferência',
    )
    auditor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='acessos_auditoria',
        verbose_name='auditor',
    )
    concedido_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='acessos_auditoria_concedidos',
        verbose_name='concedido por',
    )
    concedido_em = models.DateTimeField('concedido em', auto_now_add=True)
    ativo = models.BooleanField('ativo', default=True)

    class Meta:
        verbose_name = 'acesso de auditor'
        verbose_name_plural = 'acessos de auditor'
        constraints = [
            models.UniqueConstraint(fields=['comparativo', 'auditor'], name='uniq_auditor_acesso'),
        ]
        indexes = [models.Index(fields=['auditor', 'ativo'])]

    def __str__(self):
        return f'{self.auditor_id} → conferência {self.comparativo_id}'


class PublicacaoAuditoria(models.Model):
    """Configuração e estado da publicação de uma conferência ao auditor."""

    class Modo(models.TextChoices):
        IMEDIATA = 'IMEDIATA', 'Acompanhamento em tempo real'
        PROGRAMADA = 'PROGRAMADA', 'Publicação programada'

    class Status(models.TextChoices):
        RASCUNHO = 'RASCUNHO', 'Rascunho'
        PREPARADA = 'PREPARADA', 'Preparada'
        AGENDADA = 'AGENDADA', 'Agendada'
        EM_PUBLICACAO = 'EM_PUBLICACAO', 'Em publicação'
        PAUSADA = 'PAUSADA', 'Pausada'
        CONCLUIDA = 'CONCLUIDA', 'Concluída'
        CANCELADA = 'CANCELADA', 'Cancelada'

    class Ordem(models.TextChoices):
        POSICAO = 'POSICAO', 'Sequência das posições'
        CRONOLOGICA = 'CRONOLOGICA', 'Sequência cronológica original'
        EQUILIBRADA = 'EQUILIBRADA', 'Distribuição equilibrada'

    comparativo = models.OneToOneField(
        'inventario.ComparativoContagem',
        on_delete=models.PROTECT,
        related_name='publicacao_auditoria',
        verbose_name='conferência',
    )
    # Legado (antes da liberação por contagem): não concede acesso; só é exibido ao gestor.
    liberado = models.BooleanField('liberado para auditor', default=False)
    # Decisão da gestão por conferência: o auditor vê o horário real das contagens
    # (gráfico de evolução e detalhes). Desligado: vê só quando foi disponibilizado.
    mostrar_horario_real = models.BooleanField('mostrar horário real ao auditor', default=False)
    modo = models.CharField('modo', max_length=12, choices=Modo.choices, default=Modo.IMEDIATA)
    status = models.CharField('status', max_length=15, choices=Status.choices, default=Status.RASCUNHO)
    ordem = models.CharField('ordem', max_length=12, choices=Ordem.choices, default=Ordem.POSICAO)
    duracao_minutos = models.PositiveIntegerField('duração planejada (min)', default=60)
    inicio_previsto = models.DateTimeField('início previsto', null=True, blank=True)
    inicio_real = models.DateTimeField('início real', null=True, blank=True)
    termino_previsto = models.DateTimeField('término previsto', null=True, blank=True)
    termino_real = models.DateTimeField('término real', null=True, blank=True)
    pausado_em = models.DateTimeField('pausado em', null=True, blank=True)
    preparado_em = models.DateTimeField('preparado em', null=True, blank=True)
    total_previsto = models.PositiveIntegerField('total previsto', default=0)
    total_publicado = models.PositiveIntegerField('total publicado', default=0)
    criado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='publicacoes_auditoria_criadas', verbose_name='criado por',
    )
    preparado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='publicacoes_auditoria_preparadas', verbose_name='preparado por',
    )
    iniciado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='publicacoes_auditoria_iniciadas', verbose_name='iniciado por',
    )
    pausado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='publicacoes_auditoria_pausadas', verbose_name='pausado por',
    )
    concluido_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='publicacoes_auditoria_concluidas', verbose_name='concluído por',
    )
    criado_em = models.DateTimeField('criado em', auto_now_add=True)
    atualizado_em = models.DateTimeField('atualizado em', auto_now=True)

    class Meta:
        verbose_name = 'publicação ao auditor'
        verbose_name_plural = 'publicações ao auditor'
        indexes = [models.Index(fields=['status'])]

    def __str__(self):
        return f'Publicação da conferência {self.comparativo_id} ({self.status})'

    @property
    def programada(self) -> bool:
        return self.modo == self.Modo.PROGRAMADA

    @property
    def percentual(self):
        if not self.total_previsto:
            return None
        return round(self.total_publicado * 100 / self.total_previsto, 1)


class PublicacaoAuditoriaItem(models.Model):
    """Agenda de disponibilização de um lançamento físico original.

    `item` aponta para o `InventarioItem` original (quantidade, operador e
    `capturado_em` são lidos de lá). `publicado_em` registra só o momento em
    que o lançamento ficou visível ao auditor — nunca substitui a captura.
    """

    class Status(models.TextChoices):
        PENDENTE = 'PENDENTE', 'Pendente'
        PUBLICADO = 'PUBLICADO', 'Publicado'
        CANCELADO = 'CANCELADO', 'Cancelado'

    publicacao = models.ForeignKey(
        PublicacaoAuditoria, on_delete=models.PROTECT, related_name='itens', verbose_name='publicação',
    )
    # Programação por contagem: a qual liberação (e a qual ciclo dela) o item pertence.
    liberacao = models.ForeignKey(
        'inventario.LiberacaoContagemAuditor', on_delete=models.PROTECT, null=True, blank=True,
        related_name='itens', verbose_name='liberação da contagem',
    )
    ciclo = models.PositiveIntegerField('ciclo da liberação', default=0)
    item = models.ForeignKey(
        'inventario.InventarioItem', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='publicacoes_auditoria', verbose_name='lançamento original',
    )
    # Identificador original preservado mesmo se o lançamento for excluído.
    item_original_id = models.PositiveBigIntegerField('ID do lançamento original')
    inventario_id_original = models.PositiveBigIntegerField('ID do inventário')
    etapa = models.PositiveSmallIntegerField('etapa (1=C1, 2=C2, 3=C3)')
    ordem = models.PositiveIntegerField('ordem')
    lote = models.PositiveIntegerField('lote')
    previsto_para = models.DateTimeField('previsto para', null=True, blank=True)
    publicado_em = models.DateTimeField('publicado em', null=True, blank=True)
    status = models.CharField('status', max_length=10, choices=Status.choices, default=Status.PENDENTE)
    tentativas = models.PositiveIntegerField('tentativas', default=0)
    erro_tecnico = models.CharField('erro técnico', max_length=255, blank=True)

    class Meta:
        verbose_name = 'item da publicação ao auditor'
        verbose_name_plural = 'itens da publicação ao auditor'
        ordering = ['publicacao', 'ordem']
        constraints = [
            models.UniqueConstraint(
                fields=['publicacao', 'liberacao', 'ciclo', 'item_original_id'], name='uniq_publicacao_item_ciclo',
            ),
        ]
        indexes = [
            models.Index(fields=['liberacao', 'ciclo', 'status', 'previsto_para']),
            models.Index(fields=['publicacao', 'status', 'previsto_para']),
            models.Index(fields=['publicacao', 'lote']),
            models.Index(fields=['publicacao', 'publicado_em']),
        ]

    def __str__(self):
        return f'{self.publicacao_id}:{self.item_original_id} lote {self.lote}'


class LiberacaoContagemAuditor(models.Model):
    """Liberação de uma contagem (C1, C2 ou C3) de uma conferência ao auditor.

    Sem registro = não liberada. A liberação vale para o inventário associado
    no momento (`inventario_id`): se a associação da etapa mudar, a contagem
    volta a ser tratada como não liberada até nova liberação explícita.
    """

    class Status(models.TextChoices):
        NAO_LIBERADA = 'NAO_LIBERADA', 'Não liberada'
        LIBERADA = 'LIBERADA', 'Liberada para acompanhamento'
        SUSPENSA = 'SUSPENSA', 'Acesso suspenso'

    class Modo(models.TextChoices):
        TEMPO_REAL = 'TEMPO_REAL', 'Tempo real'
        PROGRAMADA = 'PROGRAMADA', 'Liberação programada'

    class Programacao(models.TextChoices):
        SEM = '', 'Sem programação'
        AGENDADA = 'AGENDADA', 'Agendada'
        EM_LIBERACAO = 'EM_LIBERACAO', 'Em liberação'
        PAUSADA = 'PAUSADA', 'Pausada'
        CONCLUIDA = 'CONCLUIDA', 'Concluída'
        INTERROMPIDA = 'INTERROMPIDA', 'Interrompida'

    comparativo = models.ForeignKey(
        'inventario.ComparativoContagem', on_delete=models.PROTECT,
        related_name='liberacoes_auditor', verbose_name='conferência',
    )
    etapa = models.PositiveSmallIntegerField('etapa (1=C1, 2=C2, 3=C3)')
    inventario_id_liberado = models.PositiveBigIntegerField('inventário liberado', null=True, blank=True)
    status = models.CharField('status', max_length=13, choices=Status.choices, default=Status.NAO_LIBERADA)
    liberado_em = models.DateTimeField('liberado em', null=True, blank=True)
    liberado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='contagens_auditor_liberadas', verbose_name='liberado por',
    )
    suspenso_em = models.DateTimeField('suspenso em', null=True, blank=True)
    suspenso_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='contagens_auditor_suspensas', verbose_name='suspenso por',
    )
    motivo_suspensao = models.CharField('motivo da suspensão', max_length=255, blank=True)
    # Como a contagem chega ao auditor (definido a cada liberação).
    modo = models.CharField('modo', max_length=12, choices=Modo.choices, default=Modo.TEMPO_REAL)
    ordem = models.CharField('ordem', max_length=12, choices=PublicacaoAuditoria.Ordem.choices, default=PublicacaoAuditoria.Ordem.POSICAO)
    duracao_minutos = models.PositiveIntegerField('duração planejada (min)', default=60)
    ciclo = models.PositiveIntegerField('ciclo', default=0)
    programacao = models.CharField('programação', max_length=12, choices=Programacao.choices, default=Programacao.SEM, blank=True)
    inicio_previsto = models.DateTimeField('início previsto', null=True, blank=True)
    inicio_real = models.DateTimeField('início real', null=True, blank=True)
    termino_previsto = models.DateTimeField('término previsto', null=True, blank=True)
    termino_real = models.DateTimeField('término real', null=True, blank=True)
    pausado_em = models.DateTimeField('pausado em', null=True, blank=True)
    total_programado = models.PositiveIntegerField('total programado', default=0)
    total_publicado = models.PositiveIntegerField('total liberado', default=0)
    atualizado_em = models.DateTimeField('atualizado em', auto_now=True)

    class Meta:
        verbose_name = 'liberação de contagem ao auditor'
        verbose_name_plural = 'liberações de contagem ao auditor'
        ordering = ['comparativo', 'etapa']
        constraints = [
            models.UniqueConstraint(fields=['comparativo', 'etapa'], name='uniq_liberacao_contagem_auditor'),
            models.CheckConstraint(condition=models.Q(etapa__in=[1, 2, 3]), name='liberacao_auditor_etapa_valida'),
        ]
        indexes = [models.Index(fields=['comparativo', 'status']), models.Index(fields=['status', 'programacao'])]

    def __str__(self):
        return f'Conferência {self.comparativo_id} · C{self.etapa} · {self.status}'

    @property
    def programada(self) -> bool:
        return self.modo == self.Modo.PROGRAMADA

    @property
    def percentual(self):
        if not self.total_programado:
            return None
        from decimal import Decimal
        return Decimal(self.total_publicado) * 100 / Decimal(self.total_programado)


class EventoAuditoria(models.Model):
    """Trilha imutável (somente inclusão) da publicação ao auditor."""

    class Tipo(models.TextChoices):
        CONFIGURADA = 'CONFIGURADA', 'Configuração alterada'
        ACESSO_CONCEDIDO = 'ACESSO_CONCEDIDO', 'Acesso concedido'
        ACESSO_REVOGADO = 'ACESSO_REVOGADO', 'Acesso revogado'
        PREPARADA = 'PREPARADA', 'Publicação preparada'
        PROGRAMACAO_ATUALIZADA = 'PROGRAMACAO_ATUALIZADA', 'Programação atualizada'
        AGENDADA = 'AGENDADA', 'Publicação agendada'
        INICIADA = 'INICIADA', 'Publicação iniciada'
        LOTE_PUBLICADO = 'LOTE_PUBLICADO', 'Itens disponibilizados ao auditor'
        PAUSADA = 'PAUSADA', 'Publicação pausada'
        RETOMADA = 'RETOMADA', 'Publicação retomada'
        CANCELADA = 'CANCELADA', 'Futuras liberações canceladas'
        CONCLUIDA = 'CONCLUIDA', 'Publicação concluída'
        ACESSO_NEGADO = 'ACESSO_NEGADO', 'Acesso negado'
        CONTAGEM_LIBERADA = 'CONTAGEM_LIBERADA', 'Contagem liberada ao auditor'
        CONTAGEM_SUSPENSA = 'CONTAGEM_SUSPENSA', 'Acesso à contagem suspenso'

    comparativo = models.ForeignKey(
        'inventario.ComparativoContagem', on_delete=models.PROTECT,
        related_name='eventos_auditor', verbose_name='conferência',
    )
    publicacao = models.ForeignKey(
        PublicacaoAuditoria, on_delete=models.PROTECT, null=True, blank=True,
        related_name='eventos', verbose_name='publicação',
    )
    tipo = models.CharField('tipo', max_length=25, choices=Tipo.choices)
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='eventos_auditor', verbose_name='usuário',
    )
    data_hora = models.DateTimeField('data e hora')
    lote = models.PositiveIntegerField('lote', null=True, blank=True)
    dados = models.JSONField('metadados', default=dict, blank=True)

    class Meta:
        verbose_name = 'evento de auditoria'
        verbose_name_plural = 'eventos de auditoria'
        ordering = ['-data_hora', '-pk']
        constraints = [
            # Um lote só gera um evento de disponibilização, mesmo com dois workers.
            models.UniqueConstraint(
                fields=['publicacao', 'tipo', 'lote'],
                condition=models.Q(tipo='LOTE_PUBLICADO'),
                name='uniq_evento_lote_publicado',
            ),
        ]
        indexes = [models.Index(fields=['comparativo', 'data_hora'])]

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValueError('Eventos de auditoria são imutáveis.')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError('Eventos de auditoria são imutáveis.')

    def __str__(self):
        return f'{self.tipo} · {self.data_hora:%d/%m/%Y %H:%M}'
