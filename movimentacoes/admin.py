from django.contrib import admin

from movimentacoes.models import Movimentacao


@admin.register(Movimentacao)
class MovimentacaoAdmin(admin.ModelAdmin):
    list_display = (
        'tipo_movimento',
        'produto',
        'posicao',
        'quantidade',
        'usuario',
        'data_movimento',
    )
    search_fields = (
        'produto__codigo_produto',
        'posicao__codigo',
        'usuario__nome',
        'usuario__login',
        'observacao',
    )
    list_filter = ('tipo_movimento', 'data_movimento')
    ordering = ('-data_movimento',)
    readonly_fields = ('data_movimento',)
