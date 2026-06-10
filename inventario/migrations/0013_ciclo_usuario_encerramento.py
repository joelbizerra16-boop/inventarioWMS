from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('inventario', '0012_ciclo_auditoria_premium'),
    ]

    operations = [
        migrations.AddField(
            model_name='cicloinventario',
            name='usuario_encerramento',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='ciclos_inventario_encerrados',
                to=settings.AUTH_USER_MODEL,
                verbose_name='usuário de encerramento',
            ),
        ),
    ]
