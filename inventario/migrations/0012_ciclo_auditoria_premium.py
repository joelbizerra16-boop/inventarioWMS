from django.db import migrations, models


def popular_status_ciclo(apps, schema_editor):
    CicloInventario = apps.get_model('inventario', 'CicloInventario')
    for ciclo in CicloInventario.objects.all():
        if ciclo.ativo:
            ciclo.status_ciclo = 'ATIVO'
        else:
            ciclo.status_ciclo = 'ENCERRADO'
        ciclo.save(update_fields=['status_ciclo'])


class Migration(migrations.Migration):

    dependencies = [
        ('inventario', '0011_ciclo_historico_edicao'),
    ]

    operations = [
        migrations.AddField(
            model_name='cicloinventario',
            name='canal_utilizado',
            field=models.CharField(blank=True, max_length=100, verbose_name='canal utilizado'),
        ),
        migrations.AddField(
            model_name='cicloinventario',
            name='criterio_utilizado',
            field=models.TextField(blank=True, verbose_name='critério utilizado'),
        ),
        migrations.AddField(
            model_name='cicloinventario',
            name='percentual_executado',
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                max_digits=6,
                null=True,
                verbose_name='percentual executado (snapshot)',
            ),
        ),
        migrations.AddField(
            model_name='cicloinventario',
            name='quantidade_skus_contados',
            field=models.PositiveIntegerField(
                blank=True,
                null=True,
                verbose_name='SKUs contados (snapshot)',
            ),
        ),
        migrations.AddField(
            model_name='cicloinventario',
            name='quantidade_skus_divergentes',
            field=models.PositiveIntegerField(
                blank=True,
                null=True,
                verbose_name='SKUs divergentes (snapshot)',
            ),
        ),
        migrations.AddField(
            model_name='cicloinventario',
            name='quantidade_skus_validados',
            field=models.PositiveIntegerField(
                blank=True,
                null=True,
                verbose_name='SKUs validados (snapshot)',
            ),
        ),
        migrations.AddField(
            model_name='cicloinventario',
            name='status_ciclo',
            field=models.CharField(
                choices=[
                    ('ATIVO', 'Ativo'),
                    ('ENCERRADO', 'Encerrado'),
                    ('ARQUIVADO', 'Arquivado'),
                ],
                default='ATIVO',
                max_length=20,
                verbose_name='status do ciclo',
            ),
        ),
        migrations.AddField(
            model_name='cicloinventario',
            name='taxa_acuracidade',
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                max_digits=6,
                null=True,
                verbose_name='taxa de acuracidade (snapshot)',
            ),
        ),
        migrations.RunPython(popular_status_ciclo, migrations.RunPython.noop),
    ]
