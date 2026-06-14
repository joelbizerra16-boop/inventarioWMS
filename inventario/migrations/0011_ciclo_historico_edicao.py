from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('inventario', '0010_ciclo_validado_divergencia'),
    ]

    operations = [
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
                    ('EDICAO', 'Edição'),
                ],
                max_length=20,
                verbose_name='tipo',
            ),
        ),
    ]
