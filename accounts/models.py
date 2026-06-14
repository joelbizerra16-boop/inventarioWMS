from django.conf import settings
from django.db import models


class Usuario(models.Model):
    class Perfil(models.TextChoices):
        ADMINISTRADOR = 'ADMINISTRADOR', 'Administrador'
        SUPERVISOR = 'SUPERVISOR', 'Supervisor'
        OPERADOR = 'OPERADOR', 'Operador (Pocket)'
        INVENTARIO = 'INVENTARIO', 'Operador de Inventário'
        CONSULTA = 'CONSULTA', 'Consulta'

    nome = models.CharField('nome', max_length=150)
    login = models.CharField('login', max_length=100)
    setor = models.CharField('setor', max_length=100)
    perfil = models.CharField(
        'perfil',
        max_length=20,
        choices=Perfil.choices,
    )
    ativo = models.BooleanField('ativo', default=True)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='perfil_operacional',
        verbose_name='usuário de autenticação',
    )
    data_criacao = models.DateTimeField('data de criação', auto_now_add=True)

    class Meta:
        verbose_name = 'usuário'
        verbose_name_plural = 'usuários'
        ordering = ['nome']

    def __str__(self):
        return self.nome
