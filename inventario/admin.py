from django.contrib import admin

from inventario.models import (
    CicloAuditoriaHistorico,
    CicloEstoqueFisicoAjuste,
    CicloInventario,
    CicloInventarioItem,
    CicloInventarioSku,
    Inventario,
    InventarioItem,
)


@admin.register(Inventario)
class InventarioAdmin(admin.ModelAdmin):
    list_display = ('usuario', 'status', 'data_criacao')
    search_fields = ('usuario__nome', 'usuario__login', 'observacao')
    list_filter = ('status',)
    ordering = ('-data_criacao',)
    readonly_fields = ('data_criacao',)


@admin.register(InventarioItem)
class InventarioItemAdmin(admin.ModelAdmin):
    list_display = ('inventario', 'posicao', 'produto', 'quantidade_fisica')
    search_fields = (
        'produto__codigo_produto',
        'posicao__codigo',
        'inventario__usuario__nome',
    )
    list_filter = ('inventario', 'posicao')
    ordering = ('inventario', 'produto')


@admin.register(CicloInventario)
class CicloInventarioAdmin(admin.ModelAdmin):
    list_display = (
        'pk',
        'ativo',
        'quantidade_skus_planejados',
        'skus_por_dia',
        'dia_execucao',
        'data_criacao',
        'data_encerramento',
    )
    list_filter = ('ativo',)
    ordering = ('-data_criacao',)


@admin.register(CicloInventarioSku)
class CicloInventarioSkuAdmin(admin.ModelAdmin):
    list_display = (
        'ciclo',
        'ordem_planejamento',
        'codigo_produto',
        'quantidade_sap',
        'quantidade_fisica',
        'diferenca',
        'status_contagem',
    )
    list_filter = ('ciclo', 'status_contagem')
    search_fields = ('codigo_produto', 'descricao')
    ordering = ('ciclo', 'codigo_produto')


@admin.register(CicloInventarioItem)
class CicloInventarioItemAdmin(admin.ModelAdmin):
    list_display = (
        'ciclo_sku',
        'codigo_produto',
        'codigo_posicao',
        'quantidade_fisica',
    )
    list_filter = ('ciclo',)
    search_fields = ('codigo_produto', 'codigo_posicao', 'descricao')
    ordering = ('ciclo', 'codigo_produto')


@admin.register(CicloEstoqueFisicoAjuste)
class CicloEstoqueFisicoAjusteAdmin(admin.ModelAdmin):
    list_display = (
        'ciclo',
        'codigo_produto',
        'codigo_posicao',
        'quantidade_anterior',
        'quantidade_nova',
        'diferenca',
        'usuario',
        'data_hora',
    )
    list_filter = ('origem', 'ciclo')
    ordering = ('-data_hora',)
    readonly_fields = (
        'ciclo',
        'ciclo_sku',
        'item',
        'produto',
        'posicao',
        'codigo_produto',
        'codigo_posicao',
        'quantidade_anterior',
        'quantidade_nova',
        'diferenca',
        'usuario',
        'data_hora',
        'origem',
        'motivo',
    )


@admin.register(CicloAuditoriaHistorico)
class CicloAuditoriaHistoricoAdmin(admin.ModelAdmin):
    list_display = (
        'ciclo_sku',
        'codigo_posicao',
        'tipo',
        'usuario',
        'data_hora',
        'diferenca',
    )
    list_filter = ('tipo',)
    ordering = ('-data_hora',)
