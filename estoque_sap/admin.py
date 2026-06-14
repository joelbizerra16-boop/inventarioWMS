from django.contrib import admin

from estoque_sap.models import EstoqueSAP


@admin.register(EstoqueSAP)
class EstoqueSAPAdmin(admin.ModelAdmin):
    list_display = ('produto', 'total', 'arquivo_origem', 'data_importacao')
    search_fields = (
        'produto__codigo_produto',
        'produto__descricao',
        'arquivo_origem',
    )
    list_filter = ('data_importacao',)
    ordering = ('-data_importacao',)
    readonly_fields = ('data_importacao',)
