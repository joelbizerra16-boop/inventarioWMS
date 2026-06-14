from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('inventario', '0019_ciclo_lote_execucao'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='inventario',
            name='data_finalizacao',
            field=models.DateTimeField(blank=True, null=True, verbose_name='data de finalização'),
        ),
        migrations.AddField(
            model_name='inventario',
            name='usuario_finalizacao',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='inventarios_finalizados',
                to=settings.AUTH_USER_MODEL,
                verbose_name='usuário de finalização',
            ),
        ),
        migrations.AddField(
            model_name='inventario',
            name='quantidade_itens',
            field=models.PositiveIntegerField(blank=True, null=True, verbose_name='quantidade de itens (snapshot)'),
        ),
        migrations.AddField(
            model_name='inventario',
            name='quantidade_produtos',
            field=models.PositiveIntegerField(blank=True, null=True, verbose_name='quantidade de produtos (snapshot)'),
        ),
        migrations.AddField(
            model_name='inventario',
            name='quantidade_conciliados',
            field=models.PositiveIntegerField(blank=True, null=True, verbose_name='produtos conciliados (snapshot)'),
        ),
        migrations.AddField(
            model_name='inventario',
            name='quantidade_divergentes',
            field=models.PositiveIntegerField(blank=True, null=True, verbose_name='produtos divergentes (snapshot)'),
        ),
        migrations.AddField(
            model_name='inventario',
            name='taxa_acuracidade',
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                max_digits=6,
                null=True,
                verbose_name='taxa de acuracidade (snapshot)',
            ),
        ),
        migrations.AddField(
            model_name='inventario',
            name='snapshot_resultado',
            field=models.JSONField(blank=True, null=True, verbose_name='snapshot do resultado'),
        ),
    ]
