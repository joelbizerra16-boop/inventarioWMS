from decimal import Decimal

from django.db import migrations


def normalizar_status_recontagem(apps, schema_editor):
    CicloInventarioSku = apps.get_model('inventario', 'CicloInventarioSku')
    status_recontagem = 'RECONTAGEM'
    status_divergente = 'DIVERGENTE'
    status_validado = 'VALIDADO'

    for sku in CicloInventarioSku.objects.filter(status_contagem=status_recontagem):
        if sku.quantidade_fisica is None:
            continue
        diferenca = sku.diferenca
        if diferenca is None:
            diferenca = sku.quantidade_fisica - sku.quantidade_sap
        if Decimal(diferenca) == 0:
            sku.status_contagem = status_validado
        else:
            sku.status_contagem = status_divergente
        sku.save(update_fields=['status_contagem'])


class Migration(migrations.Migration):

    dependencies = [
        ('inventario', '0008_ciclo_origem_contagem'),
    ]

    operations = [
        migrations.RunPython(
            normalizar_status_recontagem,
            migrations.RunPython.noop,
        ),
    ]
