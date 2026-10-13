from django import template
from django.utils import timezone

from inventario.services.comparativo_contagens import formatar_quantidade

register = template.Library()


@register.filter
def qtd(valor):
    """Quantidade pt-BR; ausência aparece como '—' e nunca como zero."""
    return formatar_quantidade(valor)


@register.filter
def item(mapa, chave):
    if mapa is None:
        return None
    return mapa.get(chave)


@register.filter
def data_hora(valor):
    if valor is None:
        return 'Não registrado'
    return timezone.localtime(valor).strftime('%d/%m/%Y %H:%M')
