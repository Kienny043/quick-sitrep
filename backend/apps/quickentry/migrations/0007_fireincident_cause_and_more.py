from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('quickentry', '0006_alter_manualentry_options_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='fireincident',
            name='cause',
            field=models.CharField(default='(not specified — migrated when ipo/dtr/ted/tas were dropped)', max_length=255),
            preserve_default=False,
        ),
        migrations.RemoveField(
            model_name='fireincident',
            name='ipo',
        ),
        migrations.RemoveField(
            model_name='fireincident',
            name='dtr',
        ),
        migrations.RemoveField(
            model_name='fireincident',
            name='ted',
        ),
        migrations.RemoveField(
            model_name='fireincident',
            name='tas',
        ),
    ]
