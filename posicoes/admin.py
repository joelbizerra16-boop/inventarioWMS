from django.contrib import admin

from posicoes.models import Posicao


@admin.register(Posicao)
class PosicaoAdmin(admin.ModelAdmin):
    list_display = ('codigo', 'posicao', 'ativo', 'data_criacao')
    search_fields = ('codigo', 'posicao')
    list_filter = ('ativo',)
    ordering = ('codigo',)
    readonly_fields = ('data_criacao',)
