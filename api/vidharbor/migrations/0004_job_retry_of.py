from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("vidharbor", "0003_video_publication")]

    operations = [
        migrations.AddField(
            model_name="job", name="retry_of",
            field=models.OneToOneField(
                blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                related_name="manual_retry", to="vidharbor.job",
            ),
        ),
    ]
