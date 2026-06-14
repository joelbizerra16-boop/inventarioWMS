from django.db import migrations


def sincronizar_status_itens_validados(apps, schema_editor):
    CicloInventarioSku = apps.get_model('inventario', 'CicloInventarioSku')
    CicloInventarioItem = apps.get_model('inventario', 'CicloInventarioItem')

    status_validados = ('VALIDADO', 'VALIDADO_DIVERGENCIA', 'CONTADO')
    codigo_generico = 'CICLICO-SEM-POS'

    for sku in CicloInventarioSku.objects.filter(status_contagem__in=status_validados):
        CicloInventarioItem.objects.filter(
            ciclo_sku=sku,
            quantidade_fisica__isnull=False,
        ).exclude(
            codigo_posicao=codigo_generico,
        ).update(status_contagem=sku.status_contagem)


class Migration(migrations.Migration):

    dependencies = [
        ('inventario', '0016_normalizar_recontagem_conciliada'),
    ]

    operations = [
        migrations.RunPython(
            sincronizar_status_itens_validados,
            migrations.RunPython.noop,
        ),
    ]
