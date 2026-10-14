import math

from django import template
from django.utils import timezone

from inventario.services.comparativo_contagens import formatar_quantidade

register = template.Library()


@register.filter
def qtd(valor):
    """Quantidade pt-BR; ausência aparece como '—' e nunca como zero."""
    return formatar_quantidade(valor)


@register.filter
def pct(valor):
    """Percentual pt-BR com uma casa (ex.: 97,5%); ausência vira '—'."""
    from inventario.services.comparativo_contagens import formatar_percentual
    return formatar_percentual(valor)


@register.filter
def pct_css(valor):
    """Largura de barra em CSS (ponto decimal), limitada a 0–100."""
    if valor is None:
        return '0'
    return f'{max(0.0, min(100.0, float(valor))):.2f}'


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


# ---------------------------------------------------------------- velocímetro
# Faixas com extensão proporcional ao intervalo real (0–60, 60–90, 90–98, 98–100).
_FAIXAS_ARCO = (
    (0, 60, 'vermelho'),
    (60, 90, 'laranja'),
    (90, 98, 'amarelo'),
    (98, 100, 'verde'),
)
_CENTRO_X, _CENTRO_Y, _RAIO = 100.0, 100.0, 78.0


def _ponto(percentual: float, raio: float) -> tuple[float, float]:
    angulo = math.pi * (1 - percentual / 100)
    return (
        round(_CENTRO_X + raio * math.cos(angulo), 2),
        round(_CENTRO_Y - raio * math.sin(angulo), 2),
    )


def _num(valor: float) -> str:
    """Número SVG sempre com ponto decimal, independente da localização."""
    return f'{valor:.2f}'.rstrip('0').rstrip('.')


def _arco(inicio: float, fim: float, raio: float) -> str:
    x1, y1 = _ponto(inicio, raio)
    x2, y2 = _ponto(fim, raio)
    return f'M {_num(x1)} {_num(y1)} A {_num(raio)} {_num(raio)} 0 0 1 {_num(x2)} {_num(y2)}'


@register.inclusion_tag('inventario/comparativo/_velocimetro.html')
def velocimetro(indicador, tamanho='grande'):
    """Velocímetro semicircular 0–100% em SVG (sem bibliotecas)."""
    disponivel = indicador is not None and indicador.disponivel
    faixas = [
        {'d': _arco(inicio, fim, _RAIO), 'faixa': faixa}
        for inicio, fim, faixa in _FAIXAS_ARCO
    ]
    marcas = []
    for valor in (0, 20, 40, 60, 80, 100):
        x, y = _ponto(valor, _RAIO + 15)
        marcas.append({'x': _num(x), 'y': _num(y + 3), 'valor': valor})
    ponteiro = None
    progresso = None
    if disponivel:
        valor = max(0.0, min(100.0, float(indicador.percentual)))
        ponta_x, ponta_y = _ponto(valor, _RAIO - 14)
        ponteiro = {'x': _num(ponta_x), 'y': _num(ponta_y)}
        if valor > 0:
            progresso = _arco(0, valor, _RAIO - 13)
    return {
        'ind': indicador,
        'disponivel': disponivel,
        'tamanho': tamanho,
        'faixas': faixas,
        'marcas': marcas,
        'ponteiro': ponteiro,
        'progresso': progresso,
        'cx': _num(_CENTRO_X),
        'cy': _num(_CENTRO_Y),
    }
