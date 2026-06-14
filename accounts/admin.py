from django.contrib import admin

from accounts.models import Usuario


@admin.register(Usuario)
class UsuarioAdmin(admin.ModelAdmin):
    list_display = ('nome', 'login', 'setor', 'perfil', 'ativo', 'data_criacao')
    search_fields = ('nome', 'login', 'setor')
    list_filter = ('perfil', 'ativo', 'setor')
    ordering = ('nome',)
    readonly_fields = ('data_criacao',)
