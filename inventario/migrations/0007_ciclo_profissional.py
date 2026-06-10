import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('inventario', '0006_ciclo_sap_execucao_diaria'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='cicloinventario',
            name='canais_filtro',
            field=models.JSONField(blank=True, default=list, verbose_name='canais do ciclo'),
        ),
        migrations.AddField(
            model_name='cicloinventario',
            name='completar_lote_automaticamente',
            field=models.BooleanField(
                default=False,
                verbose_name='completar lote automaticamente',
            ),
        ),
        migrations.AddField(
            model_name='cicloinventario',
            name='embalagens_filtro',
            field=models.JSONField(blank=True, default=list, verbose_name='embalagens do ciclo'),
        ),
        migrations.AddField(
            model_name='cicloinventario',
            name='respeitar_somente_embalagens',
            field=models.BooleanField(
                default=False,
                verbose_name='respeitar somente embalagens selecionadas',
            ),
        ),
        migrations.AddField(
            model_name='cicloinventariosku',
            name='data_exclusao',
            field=models.DateTimeField(blank=True, null=True, verbose_name='data da exclusão'),
        ),
        migrations.AddField(
            model_name='cicloinventariosku',
            name='motivo_exclusao',
            field=models.TextField(blank=True, verbose_name='motivo da exclusão'),
        ),
        migrations.AddField(
            model_name='cicloinventariosku',
            name='quantidade_brida',
            field=models.DecimalField(
                blank=True,
                decimal_places=3,
                max_digits=12,
                null=True,
                verbose_name='quantidade Brida congelada',
            ),
        ),
        migrations.AddField(
            model_name='cicloinventariosku',
            name='quantidade_cosan',
            field=models.DecimalField(
                blank=True,
                decimal_places=3,
                max_digits=12,
                null=True,
                verbose_name='quantidade Cosan congelada',
            ),
        ),
        migrations.AddField(
            model_name='cicloinventariosku',
            name='usuario_exclusao',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='skus_ciclo_excluidos',
                to=settings.AUTH_USER_MODEL,
                verbose_name='usuário da exclusão',
            ),
        ),
        migrations.AddField(
            model_name='cicloauditoriahistorico',
            name='motivo',
            field=models.TextField(blank=True, verbose_name='motivo'),
        ),
        migrations.AlterField(
            model_name='cicloauditoriahistorico',
            name='tipo',
            field=models.CharField(
                choices=[
                    ('CONTAGEM', 'Contagem'),
                    ('RECONTAGEM', 'Recontagem'),
                    ('CONSOLIDACAO', 'Consolidação'),
                    ('EXCLUSAO', 'Exclusão'),
                    ('VALIDACAO', 'Validação'),
                ],
                max_length=20,
                verbose_name='tipo',
            ),
        ),
        migrations.AlterField(
            model_name='cicloinventariosku',
            name='status_contagem',
            field=models.CharField(
                choices=[
                    ('PENDENTE', 'Pendente'),
                    ('CONTADO', 'Contado'),
                    ('DIVERGENTE', 'Divergente'),
                    ('RECONTAGEM', 'Recontagem'),
                    ('VALIDADO', 'Validado'),
                    ('EXCLUIDO', 'Excluído'),
                ],
                default='PENDENTE',
                max_length=20,
                verbose_name='status da contagem',
            ),
        ),
    ]
