from django.contrib import admin

from produtos.models import AuditoriaHomologacao, Produto


@admin.register(Produto)
class ProdutoAdmin(admin.ModelAdmin):
    list_display = (
        'codigo_produto',
        'descricao',
        'embalagem',
        'setor',
        'codigo_ean',
        'status_homologacao',
        'ativo',
        'participa_ciclico',
        'data_atualizacao',
    )
    list_filter = ('ativo', 'status_homologacao', 'participa_ciclico', 'setor', 'embalagem')
    search_fields = (
        'codigo_produto',
        'descricao',
        'embalagem',
        'codigo_ean',
        'setor',
    )
    list_filter = ('ativo', 'setor', 'embalagem')
    ordering = ('codigo_produto',)
    readonly_fields = ('data_criacao', 'data_atualizacao', 'data_precadastro')


@admin.register(AuditoriaHomologacao)
class AuditoriaHomologacaoAdmin(admin.ModelAdmin):
    list_display = (
        'data_hora',
        'tipo_cadastro',
        'acao',
        'usuario',
        'origem',
        'status_novo',
    )
    list_filter = ('tipo_cadastro', 'acao', 'origem')
    search_fields = ('produto__codigo_produto', 'posicao__codigo', 'usuario__nome')
    readonly_fields = (
        'tipo_cadastro',
        'produto',
        'posicao',
        'usuario',
        'data_hora',
        'equipamento',
        'origem',
        'acao',
        'status_anterior',
        'status_novo',
        'observacao',
        'dados_alterados',
    )

    def has_delete_permission(self, request, obj=None):
        return False
