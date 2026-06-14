from django import template

from core.choices import StatusHomologacao

register = template.Library()

ROTULOS = {
    StatusHomologacao.PENDENTE: 'Pré-cadastro',
    StatusHomologacao.HOMOLOGADO: 'Homologado',
    StatusHomologacao.REJEITADO: 'Rejeitado',
}

CLASSES = {
    StatusHomologacao.PENDENTE: 'homologacao-dot--pendente',
    StatusHomologacao.HOMOLOGADO: 'homologacao-dot--homologado',
    StatusHomologacao.REJEITADO: 'homologacao-dot--rejeitado',
}


@register.inclusion_tag('includes/homologacao_status.html')
def homologacao_status(objeto):
    status = getattr(objeto, 'status_homologacao', StatusHomologacao.HOMOLOGADO)
    return {
        'classe': CLASSES.get(status, 'homologacao-dot--homologado'),
        'rotulo': ROTULOS.get(status, 'Homologado'),
    }
