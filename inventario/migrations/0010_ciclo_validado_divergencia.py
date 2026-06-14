from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('inventario', '0009_normalizar_status_recontagem'),
    ]

    operations = [
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
                    ('VALIDADO_DIVERGENCIA', 'Validado c/ divergência'),
                    ('EXCLUIDO', 'Excluído'),
                ],
                default='PENDENTE',
                max_length=20,
                verbose_name='status da contagem',
            ),
        ),
    ]
