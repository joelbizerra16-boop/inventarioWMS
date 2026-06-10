from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('produtos', '0002_embalagem_remove_empresa'),
    ]

    operations = [
        migrations.AddField(
            model_name='produto',
            name='participa_ciclico',
            field=models.BooleanField(
                default=True,
                verbose_name='participa do inventário cíclico',
            ),
        ),
    ]
