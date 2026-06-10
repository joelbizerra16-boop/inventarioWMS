from django.contrib import admin

from estoque_fisico.models import EstoqueFisico


@admin.register(EstoqueFisico)
class EstoqueFisicoAdmin(admin.ModelAdmin):
    list_display = (
        'posicao',
        'produto',
        'quantidade',
        'inventario_origem',
        'data_publicacao',
        'data_atualizacao',
    )
    search_fields = (
        'posicao__codigo',
        'produto__codigo_produto',
        'produto__descricao',
    )
    list_filter = ('data_contagem', 'posicao')
    ordering = ('-data_atualizacao',)
    readonly_fields = ('data_atualizacao',)
