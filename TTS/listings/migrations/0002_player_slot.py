from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("listings", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="PlayerSlot",
            fields=[
                (
                    "player_number",
                    models.PositiveSmallIntegerField(primary_key=True, serialize=False),
                ),
                ("username", models.CharField(max_length=25, unique=True)),
                ("display_name", models.CharField(max_length=50)),
            ],
            options={
                "ordering": ["player_number"],
            },
        ),
    ]
