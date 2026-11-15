"""Isolated same-host backup/restore drill. Never overwrite a populated target.

Permanent independent storage, retention and RPO/RTO targets are owner decisions.
All credentials remain in referenced secret files, outside the bundle and Git.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tarfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen

REPO = Path(__file__).resolve().parents[1]
WRITERS = {"web", "api", "upload", "worker", "relay", "notifier", "cleanup", "migrate"}


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.write("\n"); stream.flush(); os.fsync(stream.fileno())
    temporary.replace(path)


def valid_tar(path):
    with tarfile.open(path) as archive:
        for member in archive:
            parts = Path(member.name).parts
            if Path(member.name).is_absolute() or ".." in parts or not (member.isdir() or member.isfile()):
                raise RuntimeError("Unsafe archive member")


def assert_fenced_records(records, project):
    for item in records:
        labels = item["Config"]["Labels"]
        if labels.get("com.docker.compose.project") != project:
            raise RuntimeError("Container belongs to a different source project")
        if labels.get("com.docker.compose.service") in WRITERS:
            if item["State"]["Running"] or item["HostConfig"]["RestartPolicy"]["Name"] not in ("", "no"):
                raise RuntimeError("Source writer is running or can automatically restart")


class Stand:
    def __init__(self, specification, reports, restore=False):
        self.spec, self.reports = specification, reports
        self.project = specification["project"]
        if not re.fullmatch(r"vidharbor-[a-z0-9_-]+", self.project):
            raise ValueError("An explicitly isolated vidharbor-* test project is required")
        self.env = {**os.environ, **{k: str(v) for k, v in specification.get("env", {}).items()},
                    "COMPOSE_PROJECT_NAME": self.project, "VIDHARBOR_RESTORE_MODE": "1" if restore else "0"}
        self.base = ["docker", "compose", "--env-file", str(REPO / ".env.example")]
        for filename in specification.get("compose_files", [str(REPO / "compose.yaml")]):
            self.base += ["-f", filename]
        self.configuration = json.loads(self.run(["config", "--format", "json"], label="config").decode())

    def run(self, arguments, *, label, stdin=None, output=None):
        print(json.dumps({"project": self.project, "step": label}), flush=True)
        log = self.reports / (self.project + "-" + label + ".log")
        self.reports.mkdir(parents=True, exist_ok=True)
        with log.open("wb") as errors:
            if output is None:
                result = subprocess.run(self.base + arguments, cwd=REPO, env=self.env,
                                        input=stdin, stdout=subprocess.PIPE, stderr=errors)
                raw = result.stdout
                if raw: errors.write(raw)
            else:
                with output.open("wb") as stream:
                    result = subprocess.run(self.base + arguments, cwd=REPO, env=self.env,
                                            stdout=stream, stderr=errors)
                raw = b""
        if result.returncode:
            raise RuntimeError(f"{label} failed ({result.returncode}); see {log}")
        return raw

    def backend(self, action, *, stdin=None, apply=False):
        arguments = ["run", "--rm", "--no-deps", "-T", "upload", "python", "manage.py", "recovery", action]
        if apply: arguments.append("--apply")
        raw = self.run(arguments, label="recovery-" + action + ("-apply" if apply else ""), stdin=stdin)
        return json.loads(raw.decode().splitlines()[-1])

    def running(self):
        raw = self.run(["ps", "--format", "json"], label="running").decode().strip()
        if not raw: return set()
        values = json.loads(raw) if raw.startswith("[") else [json.loads(line) for line in raw.splitlines()]
        return {value["Service"] for value in values if value["State"] in ("running", "restarting")}

    def quiesce(self):
        active = sorted(self.running() & WRITERS)
        if active: self.run(["stop", "--timeout", "30", *active], label="quiesce")
        if self.running() & WRITERS: raise RuntimeError("A source writer remains alive")
        return active

    def containers(self):
        ids = self.run(["ps", "--all", "--quiet"], label="container-ids").decode().split()
        if not ids:
            return []
        return json.loads(subprocess.check_output(["docker", "inspect", *ids]))

    def fence(self):
        records = self.containers()
        writers = []
        for item in records:
            labels = item["Config"]["Labels"]
            if labels.get("com.docker.compose.project") != self.project:
                raise RuntimeError("Refusing to fence another project")
            if labels.get("com.docker.compose.service") in WRITERS:
                writers.append(item)
        receipt = self.reports / (self.project + "-fence.json")
        if not receipt.exists():
            save(receipt, {"project": self.project, "containers": [
                {"id": item["Id"], "service": item["Config"]["Labels"]["com.docker.compose.service"],
                 "previous_restart_policy": item["HostConfig"]["RestartPolicy"]} for item in writers]})
        if writers:
            subprocess.run(["docker", "update", "--restart=no", *[item["Id"] for item in writers]],
                           check=True, stdout=subprocess.DEVNULL)
        stopped = self.quiesce()
        self.assert_fenced()
        return {"stopped": stopped, "restart_disabled": len(writers), "receipt": str(receipt)}

    def assert_fenced(self):
        assert_fenced_records(self.containers(), self.project)

    def volume(self, key):
        value = self.configuration["volumes"][key]
        if value.get("external"): raise RuntimeError("External volumes are not allowed in this isolated drill")
        name = value["name"]
        if not name.startswith(self.project + "_"): raise RuntimeError("Volume does not belong to this test stand")
        return name

    def archive(self, key, path):
        name = self.volume(key)
        image = self.configuration["services"]["db"]["image"]
        # Write on the same host directly. Streaming multi-GB tar data through
        # the Docker API needlessly prolongs the write barrier in rootless Docker.
        temporary = path.with_suffix(path.suffix + ".tmp")
        # Keep the host user's ownership across rootless bind mounts. If Docker
        # creates the output itself, the executor may see an unmapped UID.
        with temporary.open("xb") as stream:
            os.fchmod(stream.fileno(), 0o600)
        subprocess.run(["docker", "run", "--rm", "--network", "none", "--mount",
            f"type=volume,source={name},target=/data,readonly", "--mount",
            f"type=bind,source={path.parent.resolve()},target=/bundle", image,
            "tar", "-C", "/data", "-cf", "/bundle/" + temporary.name, "."], check=True)
        temporary.replace(path)
        path.chmod(0o600)
        valid_tar(path)

    def restore_volume(self, key, path):
        name = self.volume(key)
        exists = subprocess.run(["docker", "volume", "inspect", name], capture_output=True).returncode == 0
        if exists: raise RuntimeError("Refusing to overwrite an existing restore volume: " + name)
        subprocess.run(["docker", "volume", "create", name], check=True, stdout=subprocess.DEVNULL)
        valid_tar(path)
        image = self.configuration["services"]["db"]["image"]
        subprocess.run(["docker", "run", "--rm", "--network", "none", "--mount",
            f"type=volume,source={name},target=/data", "--mount",
            f"type=bind,source={path.parent.resolve()},target=/bundle,readonly", image,
            "tar", "-C", "/data", "-xf", "/bundle/" + path.name], check=True)

    def postgres(self, program, *arguments):
        service = self.configuration["services"]["db"]["environment"]
        return ["exec", "-T", "db", program, "-U", service["POSTGRES_USER"], "-d", service["POSTGRES_DB"], *arguments]


def backup(source, destination, resume):
    destination.mkdir(mode=0o700, parents=True, exist_ok=False)
    started = time.monotonic(); active = source.quiesce(); broker_running = "rabbitmq" in source.running()
    manifest = {"backup_id": destination.name, "source_project": source.project,
                "boundary_utc": datetime.now(timezone.utc).isoformat(), "complete": False, "source_resumed": False,
                "scope": "isolated same-host drill; independent carrier/retention/RPO/RTO targets not approved"}
    try:
        snapshot = source.backend("describe"); save(destination / "snapshot.json", snapshot)
        needed = sum(value["bytes"] for value in snapshot["files"].values()) * 1.1 + 1024**3
        if shutil.disk_usage(destination).free < needed:
            raise RuntimeError("Insufficient measured space for the snapshot and reserve")
        source.run(source.postgres("pg_dump", "-Fc", "--no-owner"), label="pg-dump", output=destination / "postgres.dump")
        source.archive("media_data", destination / "media.tar")
        if broker_running: source.run(["stop", "--timeout", "30", "rabbitmq"], label="broker-stop")
        source.archive("rabbitmq_data", destination / "rabbitmq.tar")
        # Authority is exported separately/currently; an old bundle must not roll it back.
        manifest["journal_head"] = snapshot["journal_head"]
        manifest["hashes"] = {p.name: digest(p) for p in destination.iterdir() if p.is_file()}
        manifest["postgres_image"] = source.configuration["services"]["db"]["image"]
        manifest["rabbitmq_image"] = source.configuration["services"]["rabbitmq"]["image"]
        manifest["source_head"] = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip()
        manifest["complete"] = True
        save(destination / "manifest.json", manifest)
    finally:
        try:
            if resume:
                services = (["rabbitmq"] if broker_running else []) + active
                if services: source.run(["up", "-d", "--no-deps", "--wait", "--wait-timeout", "240", *services], label="resume")
                manifest["source_resumed"] = True
        finally:
            manifest["pause_seconds"] = time.monotonic() - started
            save(destination / "manifest.json", manifest)
    return manifest


def export_authority(source, archive):
    if source.running() & WRITERS: raise RuntimeError("Pause all source writers before exporting current authority")
    state = source.backend("journal")
    source.archive("journal_data", archive)
    save(archive.with_suffix(".json"), {"head": state["head"], "sha256": digest(archive),
                                      "exported_utc": datetime.now(timezone.utc).isoformat()})
    return state["head"]


def restore(source, target, bundle, authority, state_path):
    started = time.monotonic()
    if source.project == target.project: raise RuntimeError("Source and restore target must differ")
    if source.running() & WRITERS: raise RuntimeError("Fence source writers before restoring/opening another stand")
    source.assert_fenced()
    if target.running(): raise RuntimeError("Restore target must be new and stopped")
    if subprocess.run(["docker", "volume", "inspect", target.volume("postgres_data")], capture_output=True).returncode == 0:
        raise RuntimeError("Target PostgreSQL volume already exists; choose a new isolated target")
    manifest = json.loads((bundle / "manifest.json").read_text())
    if not manifest["complete"]: raise RuntimeError("Incomplete backup")
    if set(manifest["hashes"]) != {"snapshot.json", "postgres.dump", "media.tar", "rabbitmq.tar"}:
        raise RuntimeError("Unexpected backup components")
    if manifest["source_project"] != source.project: raise RuntimeError("Backup belongs to a different source")
    for name, expected in manifest["hashes"].items():
        if digest(bundle / name) != expected: raise RuntimeError("Backup checksum mismatch: " + name)
    authority_metadata = json.loads(authority.with_suffix(".json").read_text())
    if digest(authority) != authority_metadata["sha256"]: raise RuntimeError("Authority checksum mismatch")
    if source.backend("journal")["head"] != authority_metadata["head"]:
        raise RuntimeError("Provided deletion authority is stale")
    if target.configuration["services"]["db"]["image"] != manifest["postgres_image"]:
        raise RuntimeError("PostgreSQL version differs")
    if target.configuration["services"]["rabbitmq"]["image"] != manifest["rabbitmq_image"]:
        raise RuntimeError("Cold RabbitMQ image identity/version differs")
    needed = (bundle / "media.tar").stat().st_size + (bundle / "rabbitmq.tar").stat().st_size * 2 + 1024**3
    if shutil.disk_usage(bundle).free < needed: raise RuntimeError("Insufficient space for full restore and reserve")
    target.run(["up", "-d", "--wait", "--wait-timeout", "120", "db"], label="start-clean-db")
    tables = target.run(target.postgres("psql", "-Atc", "SELECT count(*) FROM pg_tables WHERE schemaname='public'"), label="assert-empty-db").strip()
    if tables != b"0": raise RuntimeError("Restore requires a clean separate database")
    dump = bundle / "postgres.dump"
    log = target.reports / (target.project + "-pg-restore.log")
    with dump.open("rb") as stream, log.open("wb") as output:
        subprocess.run(target.base + target.postgres("pg_restore", "--exit-on-error", "--no-owner", "--jobs=1"),
                       cwd=REPO, env=target.env, stdin=stream, stdout=output, stderr=output, check=True)
    target.restore_volume("media_data", bundle / "media.tar")
    target.restore_volume("rabbitmq_data", bundle / "rabbitmq.tar")
    target.restore_volume("journal_data", authority)
    target.run(["up", "-d", "--wait", "--wait-timeout", "180", "rabbitmq"], label="start-restored-broker")
    target.run(["up", "-d", "--no-deps", "--wait", "--wait-timeout", "120", "api", "upload", "web"], label="start-closed-http")
    try:
        with urlopen("http://127.0.0.1:" + str(target.spec["env"]["VIDHARBOR_PORT"]) + "/api/catalog", timeout=10) as response:
            raise RuntimeError("Restore gate unexpectedly served public data: " + str(response.status))
    except HTTPError as error:
        if error.code != 503: raise
    verification = target.backend("verify", stdin=(bundle / "snapshot.json").read_bytes())
    journal_state = target.backend("journal")
    old = manifest["journal_head"]; entries = journal_state["entries"]
    if old["sequence"] and (len(entries) < old["sequence"] or entries[old["sequence"] - 1]["digest"] != old["digest"]):
        raise RuntimeError("Current authority does not extend the backup watermark")
    if journal_state["head"] != authority_metadata["head"]: raise RuntimeError("Authority watermark differs")
    deletions = target.backend("apply-deletions")
    dry_run = target.backend("reconcile")
    applied = target.backend("reconcile", apply=True)
    state = {"backup_id": manifest["backup_id"], "target_project": target.project,
             "verified": verification, "deletions": deletions, "reconcile_dry_run": dry_run,
             "reconcile_applied": applied, "restore_seconds": time.monotonic() - started,
             "http_open": False, "notifier_open": False, "external_sheets": "Not Run"}
    save(state_path, state)
    return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("action", choices=["preflight", "backup", "quiesce", "fence", "export-journal", "restore", "open"])
    parser.add_argument("--bundle", type=Path)
    parser.add_argument("--authority", type=Path)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args(); config = json.loads(args.config.read_text())
    reports = Path(config["reports"]); reports.mkdir(parents=True, exist_ok=True)
    source = Stand(config["source"], reports)
    if args.action == "preflight":
        target = Stand(config["target"], reports, restore=True)
        if source.project == target.project: raise RuntimeError("Source and target coincide")
        if source.spec["env"]["VIDHARBOR_PORT"] == target.spec["env"]["VIDHARBOR_PORT"]:
            raise RuntimeError("Source and target public ports coincide")
        result = {"source": source.project, "target": target.project,
                  "scope": "isolated cloud drill", "independent_carrier": "Not approved", "RPO_RTO_targets": "Not approved"}
    elif args.action == "backup":
        bundle = args.bundle or Path(config["backup_root"]) / ("backup-" + uuid.uuid4().hex)
        result = backup(source, bundle, args.resume)
    elif args.action == "quiesce": result = {"stopped": source.quiesce()}
    elif args.action == "fence": result = source.fence()
    elif args.action == "export-journal":
        if not args.authority: parser.error("--authority is required")
        result = export_authority(source, args.authority)
    else:
        target = Stand(config["target"], reports, restore=args.action == "restore")
        state_path = reports / (target.project + "-restore-state.json")
        if args.action == "restore":
            if not args.bundle or not args.authority: parser.error("--bundle and --authority are required")
            result = restore(source, target, args.bundle, args.authority, state_path)
        else:
            if source.running() & WRITERS: raise RuntimeError("Old writers must remain fenced")
            source.assert_fenced()
            state = json.loads(state_path.read_text())
            if state["target_project"] != target.project or not state.get("verified"):
                raise RuntimeError("Target has no completed verification gate")
            alias = target.spec.get("public_alias_port")
            if alias is not None:
                if str(alias) != str(source.spec["env"]["VIDHARBOR_PORT"]):
                    raise RuntimeError("Drill cutover alias must be the fenced source's local port")
                override = reports / (target.project + "-cutover-ports.json")
                save(override, {"services": {"web": {"ports": ["127.0.0.1:" + str(alias) + ":80"]}}})
                files = config["target"]["compose_files"]
                if str(override) not in files: files.append(str(override))
                save(args.config, config)
                target = Stand(config["target"], reports)
            started = time.monotonic()
            target.run(["up", "-d", "--no-deps", "--force-recreate", "--wait", "--wait-timeout", "240", "api", "upload", "web", "relay", "worker", "cleanup"], label="open-verified-http")
            with urlopen("http://127.0.0.1:" + str(target.spec["env"]["VIDHARBOR_PORT"]) + "/health/ready", timeout=15) as response:
                if response.status != 200: raise RuntimeError("Restored application is not ready")
            state.update(http_open=True, open_seconds=time.monotonic() - started)
            # Optional notifier profile is deliberately not started: real external
            # state still needs the owner's reconciliation/access approval.
            save(state_path, state); result = state
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
