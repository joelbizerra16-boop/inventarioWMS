from django.db import migrations, models


def migrar_empresa_para_setor(apps, schema_editor):
    Produto = apps.get_model('produtos', 'Produto')
    for produto in Produto.objects.all():
        setor = (produto.setor or '').strip()
        empresa = (produto.empresa or '').strip()
        if not setor and empresa:
            produto.setor = empresa
            produto.save(update_fields=['setor'])


class Migration(migrations.Migration):

    dependencies = [
        ('produtos', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='produto',
            name='embalagem',
            field=models.CharField(blank=True, default='', max_length=100, verbose_name='embalagem'),
        ),
        migrations.RunPython(migrar_empresa_para_setor, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name='produto',
            name='empresa',
        ),
    ]
