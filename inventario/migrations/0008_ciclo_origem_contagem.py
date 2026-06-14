from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('inventario', '0007_ciclo_profissional'),
    ]

    operations = [
        migrations.AddField(
            model_name='cicloinventarioitem',
            name='dispositivo_contagem',
            field=models.CharField(blank=True, max_length=100, verbose_name='dispositivo da contagem'),
        ),
        migrations.AddField(
            model_name='cicloinventarioitem',
            name='origem_contagem',
            field=models.CharField(
                blank=True,
                choices=[
                    ('POCKET', 'Pocket'),
                    ('WEB', 'Web'),
                    ('IMPORTACAO', 'Importação'),
                    ('RECONTAGEM', 'Recontagem'),
                ],
                max_length=15,
                verbose_name='origem da contagem',
            ),
        ),
        migrations.AddField(
            model_name='cicloauditoriahistorico',
            name='dispositivo_contagem',
            field=models.CharField(blank=True, max_length=100, verbose_name='dispositivo da contagem'),
        ),
        migrations.AddField(
            model_name='cicloauditoriahistorico',
            name='origem_contagem',
            field=models.CharField(
                blank=True,
                choices=[
                    ('POCKET', 'Pocket'),
                    ('WEB', 'Web'),
                    ('IMPORTACAO', 'Importação'),
                    ('RECONTAGEM', 'Recontagem'),
                ],
                max_length=15,
                verbose_name='origem da contagem',
            ),
        ),
    ]
