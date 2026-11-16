from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("vidharbor", "0004_job_retry_of")]
    operations = [
        migrations.AddField(model_name="video", name="deleted_at", field=models.DateTimeField(null=True)),
        migrations.AlterField(model_name="video", name="state", field=models.CharField(
            choices=[("QUEUED", "Queued"), ("READY", "Ready"), ("DELETING", "Deleting"), ("DELETED", "Deleted")],
            default="QUEUED", max_length=12)),
        migrations.AlterField(model_name="job", name="state", field=models.CharField(
            choices=[("QUEUED", "Queued"), ("PROCESSING", "Processing"), ("RETRY_WAIT", "Retry Wait"),
                     ("FAILED", "Failed"), ("SUCCEEDED", "Succeeded"), ("CANCELLED", "Cancelled")],
            default="QUEUED", max_length=12)),
    ]
