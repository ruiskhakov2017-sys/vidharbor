"""S04 -> integrated S05/S06 migration on a dedicated disposable PostgreSQL DB."""

import json
import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "vidharbor.settings")

import django

django.setup()

from django.conf import settings
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor


def main():
    assert settings.DATABASES["default"]["NAME"] == "vidharbor_s05_upgrade"
    executor = MigrationExecutor(connection)
    old_target = [("vidharbor", "0003_video_publication")]
    executor.migrate(old_target)
    apps = executor.loader.project_state(old_target).apps
    user = apps.get_model("auth", "User").objects.create(username="migration-owner")
    upload = apps.get_model("vidharbor", "Upload").objects.create(
        owner_id=user.id, title="Existing original", expected_size=123, size=123, sha256="a" * 64,
        state="COMPLETE",
    )
    video = apps.get_model("vidharbor", "Video").objects.create(
        owner_id=user.id, upload_id=upload.id, title="Existing video",
    )
    job = apps.get_model("vidharbor", "Job").objects.create(video_id=video.id, generation=1, state="FAILED")
    apps.get_model("vidharbor", "Attempt").objects.create(job_id=job.id, number=1, state="FAILED", reason="disk_full")
    apps.get_model("vidharbor", "Outbox").objects.create(
        job_id=job.id, kind="video.failed.v1", attempt_no=1, payload={"migration": True},
    )
    names = ("Upload", "Video", "Job", "Attempt", "Outbox")
    before = {name: list(apps.get_model("vidharbor", name).objects.values()) for name in names}
    executor = MigrationExecutor(connection)
    target = [("vidharbor", "0006_notifications")]
    executor.migrate(target)
    current = executor.loader.project_state(target).apps
    for name in names:
        after = list(current.get_model("vidharbor", name).objects.values())
        if name == "Job":
            assert all(item.pop("retry_of_id") is None for item in after)
        if name == "Video":
            assert all(item.pop("deleted_at") is None for item in after)
        assert after == before[name], name
    Job = current.get_model("vidharbor", "Job")
    child = Job.objects.create(video_id=video.id, generation=2, retry_of_id=job.id)
    try:
        with transaction.atomic():
            Job.objects.create(video_id=video.id, generation=3, retry_of_id=job.id)
    except IntegrityError:
        pass
    else:
        raise AssertionError("Missing unique retry_of constraint")
    assert Job.objects.get(retry_of_id=job.id).id == child.id
    print(json.dumps({"upgrade":"0003 -> 0006","preserved":list(names),"nullable_existing_jobs":True,"unique_successor":True}))


if __name__ == "__main__":
    main()
