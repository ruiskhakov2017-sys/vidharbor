from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("vidharbor", "0002_alter_job_state_attempt")]

    operations = [
        migrations.AddField(
            model_name="video",
            name="published_generation",
            field=models.PositiveIntegerField(null=True),
        ),
        migrations.AddField(
            model_name="video",
            name="ready_at",
            field=models.DateTimeField(null=True),
        ),
        migrations.AlterField(
            model_name="video",
            name="state",
            field=models.CharField(choices=[("QUEUED", "Queued"), ("READY", "Ready")], default="QUEUED", max_length=12),
        ),
    ]
