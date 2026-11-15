import io
import tarfile
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from s08 import valid_tar, restore, assert_fenced_records


class RestoreSafetyTests(unittest.TestCase):
    def test_archive_rejects_parent_escape_and_links(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "unsafe.tar"
            for name, kind in (("../escape", tarfile.REGTYPE), ("/escape", tarfile.REGTYPE), ("link", tarfile.SYMTYPE)):
                with self.subTest(name=name, kind=kind):
                    with tarfile.open(path, "w") as archive:
                        info = tarfile.TarInfo(name); info.type = kind; info.linkname = "../escape"
                        archive.addfile(info)
                    with self.assertRaises(RuntimeError): valid_tar(path)

    def test_restore_refuses_source_target_alias_before_commands(self):
        stand = SimpleNamespace(project="vidharbor-same")
        with self.assertRaisesRegex(RuntimeError, "must differ"):
            restore(stand, stand, None, None, None)

    def test_restore_refuses_live_old_writer(self):
        source = SimpleNamespace(project="vidharbor-source", running=lambda: {"worker"})
        target = SimpleNamespace(project="vidharbor-target")
        with self.assertRaisesRegex(RuntimeError, "Fence source"):
            restore(source, target, None, None, None)

    def test_stopped_source_with_restart_policy_is_not_fenced(self):
        record = {"Config": {"Labels": {"com.docker.compose.project": "vidharbor-source",
                  "com.docker.compose.service": "worker"}}, "State": {"Running": False},
                  "HostConfig": {"RestartPolicy": {"Name": "on-failure"}}}
        for service in ("worker", "migrate"):
            record["Config"]["Labels"]["com.docker.compose.service"] = service
            with self.subTest(service=service), self.assertRaisesRegex(RuntimeError, "automatically restart"):
                assert_fenced_records([record], "vidharbor-source")
        record["HostConfig"]["RestartPolicy"]["Name"] = "no"
        assert_fenced_records([record], "vidharbor-source")
        record["State"]["Running"] = True
        with self.assertRaisesRegex(RuntimeError, "running"):
            assert_fenced_records([record], "vidharbor-source")

    def test_fence_rejects_foreign_container(self):
        record = {"Config": {"Labels": {"com.docker.compose.project": "vidharbor-other"}}}
        with self.assertRaisesRegex(RuntimeError, "different source project"):
            assert_fenced_records([record], "vidharbor-source")


if __name__ == "__main__":
    unittest.main(verbosity=2)
