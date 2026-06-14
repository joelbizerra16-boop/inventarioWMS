from django.db import models


class StatusHomologacao(models.TextChoices):
    HOMOLOGADO = 'HOMOLOGADO', 'Homologado'
    PENDENTE = 'PENDENTE', 'Pendente de homologação'
    REJEITADO = 'REJEITADO', 'Rejeitado'
