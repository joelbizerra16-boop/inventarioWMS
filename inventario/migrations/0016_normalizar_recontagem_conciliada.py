from decimal import Decimal

from django.db import migrations


def normalizar_recontagem_conciliada(apps, schema_editor):
    CicloInventarioSku = apps.get_model('inventario', 'CicloInventarioSku')
    CicloInventarioItem = apps.get_model('inventario', 'CicloInventarioItem')

    status_recontagem = 'RECONTAGEM'
    status_validado = 'VALIDADO'
    codigo_generico = 'CICLICO-SEM-POS'

    for sku in CicloInventarioSku.objects.filter(status_contagem=status_recontagem):
        if sku.quantidade_fisica is None:
            continue

        diferenca = sku.diferenca
        if diferenca is None:
            diferenca = sku.quantidade_fisica - sku.quantidade_sap
        if Decimal(diferenca) != 0:
            continue

        sku.status_contagem = status_validado
        sku.diferenca = Decimal('0')
        sku.save(update_fields=['status_contagem', 'diferenca'])

        CicloInventarioItem.objects.filter(
            ciclo_sku=sku,
            quantidade_fisica__isnull=False,
        ).exclude(
            codigo_posicao=codigo_generico,
        ).update(status_contagem=status_validado)


class Migration(migrations.Migration):

    dependencies = [
        ('inventario', '0015_ciclo_ajuste_item_set_null'),
    ]

    operations = [
        migrations.RunPython(
            normalizar_recontagem_conciliada,
            migrations.RunPython.noop,
        ),
    ]
