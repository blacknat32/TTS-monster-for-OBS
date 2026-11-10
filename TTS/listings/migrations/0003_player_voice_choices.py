from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("listings", "0002_player_slot"),
    ]

    operations = [
        migrations.AlterField(
            model_name="playerslot",
            name="username",
            field=models.CharField(
                blank=True,
                max_length=25,
                null=True,
                unique=True,
            ),
        ),
        migrations.AlterField(
            model_name="playerslot",
            name="display_name",
            field=models.CharField(
                blank=True,
                default="",
                max_length=50,
            ),
        ),
        migrations.AddField(
            model_name="playerslot",
            name="voice_id",
            field=models.CharField(
                default="faa92dd8-0517-49da-8f01-1fb03f0e0096",
                max_length=36,
            ),
        ),
    ]
