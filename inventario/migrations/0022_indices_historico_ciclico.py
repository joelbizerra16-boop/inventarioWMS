from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('inventario', '0021_alarga_dispositivo_contagem_ciclico'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='cicloinventarioitem',
            index=models.Index(
                fields=['ciclo_sku', 'codigo_posicao'],
                name='idx_ciclo_item_sku_pos',
            ),
        ),
        migrations.AddIndex(
            model_name='cicloauditoriahistorico',
            index=models.Index(
                fields=['ciclo_sku', '-data_hora'],
                name='idx_ciclo_hist_sku_data',
            ),
        ),
    ]
