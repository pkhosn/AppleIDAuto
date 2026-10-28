"""Run real Git/Bash/file operations in disposable repositories, never live services."""
import base64
import fcntl
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1]
REAL_PHP = shutil.which("php")


class UpgradeTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="upgrade-fixture-")
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.root = self.base / "checkout"
        self.seed = self.base / "seed"
        self.remote = self.base / "remote.git"
        self.bin = self.base / "bin"
        self.bin.mkdir()
        self.root.mkdir()
        self.entry = self.root / "upgrade.sh"
        self.environment = os.environ.copy()
        self.environment.update(
            PATH=str(self.bin) + os.pathsep + os.environ["PATH"], UPGRADE_FIXTURE=str(self.base),
            REAL_PHP=REAL_PHP or "php", FIXTURE_SECRET="secret-fixture-must-never-appear",
            GIT_AUTHOR_NAME="Upgrade Fixture", GIT_AUTHOR_EMAIL="fixture@example.test",
            GIT_COMMITTER_NAME="Upgrade Fixture", GIT_COMMITTER_EMAIL="fixture@example.test",
        )
        for flag in ["FAIL_STEP", "ENABLED_ACCOUNTS", "RECOVERY_LOCK", "QUEUE_SIZE", "DRIVER_QUEUE", "GRID_BUSY",
                     "DRIVER_RUNNING", "LEGACY_DRIVER_HEALTH"]:
            self.environment.pop(flag, None)
        self.environment.pop("UPGRADE_PROJECT_ROOT", None)
        for name in ["php", "composer", "npm", "node", "docker"]:
            target = self.bin / name
            shutil.copy(SOURCE / "tests/fixtures/upgrade_tool.py", target)
            target.chmod(0o755)
        for name in ["upgrade.sh", "scripts/upgrade_helper.php", "scripts/upgrade_probe.py"]:
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(SOURCE / name, target)
        for name, value in {
            ".gitignore": ".env\nvendor/\nruntime/\n",
            "think": "<?php // test project marker",
            "app.txt": "initial source",
            "db/update/review_fixes.php": "<?php // never executed against a database",
            "frontend/package.json": "{}",
            "automation-server/docker-compose.yml": "services: {}",
            "public/control-app/index.html": "initial control app",
            "public/react-preview/index.html": "initial preview",
        }.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(value)
        self.git(self.base, "init", "--bare", "--initial-branch=main", str(self.remote))
        self.git(self.root, "init", "--initial-branch=main")
        self.git(self.root, "add", "-A")
        self.git(self.root, "commit", "-m", "fixture initial")
        self.before = self.git(self.root, "rev-parse", "HEAD").strip()
        self.git(self.root, "remote", "add", "origin", str(self.remote))
        self.git(self.root, "push", "-u", "origin", "main")
        self.git(self.base, "clone", str(self.remote), str(self.seed))
        (self.seed / "app.txt").write_text("latest source")
        self.git(self.seed, "commit", "-am", "fixture latest")
        self.git(self.seed, "push", "origin", "main")
        self.target = self.git(self.seed, "rev-parse", "HEAD").strip()
        panel = (SOURCE / ".env.example").read_text()
        panel = panel.replace("replace-with-panel-api-key", "panel-" + "x" * 40)
        panel = panel.replace("replace-with-automation-token", "automation-" + "x" * 40)
        panel = panel.replace("replace-with-32-byte-base64-credentials-cipher-key", base64.b64encode(b"C" * 32).decode())
        panel = panel.replace("replace-with-32-byte-base64-mailbox-cipher-key", base64.b64encode(b"M" * 32).decode())
        (self.root / ".env").write_text(panel)
        automation = "\n".join(["PANEL_URL=https://fixture.example.test", "PANEL_API_KEY=panel-" + "x" * 40,
            "AUTOMATION_TOKEN=automation-" + "x" * 40, "ACCOUNT_DRIVER_TOKEN=driver-" + "x" * 40,
            "ACCOUNT_DRIVER_URL=http://account-driver:3940"])
        (self.root / "automation-server/.env").write_text(automation)
        for path in [self.root / ".env", self.root / "automation-server/.env"]:
            path.chmod(0o600)
        (self.root / "vendor").mkdir()
        (self.root / "vendor/autoload.php").write_text("<?php // old vendor")
        (self.root / "runtime").mkdir()
        (self.root / "runtime/install.lock").write_text("keep installation")
        (self.root / "runtime/session").mkdir()
        (self.root / "runtime/session/fixture").write_text("keep session")
        (self.base / "containers.json").write_text(json.dumps(dict.fromkeys(
            ["automation-server", "account-driver", "selenium", "redis"], True)))
        with sqlite3.connect(self.base / "tasks.sqlite3") as database:
            database.executescript("""CREATE TABLE tasks(status TEXT);
                CREATE TABLE feature_schedule(result TEXT);
                CREATE TABLE task_runs(status TEXT);
                CREATE TABLE task_logs(id INTEGER,account_id INTEGER,action TEXT,status TEXT);""")

    def git(self, directory, *arguments):
        result = subprocess.run(["git", "-C", str(directory), *arguments], env=self.environment,
                                capture_output=True, text=True, check=True)
        return result.stdout

    def run_upgrade(self, *arguments, **variables):
        environment = self.environment | variables
        result = subprocess.run(["bash", str(self.entry), *arguments],
                                env=environment, capture_output=True, text=True, timeout=45)
        self.assertNotIn(self.environment["FIXTURE_SECRET"], result.stdout + result.stderr)
        for path in self.backups():
            for log in path.glob("*.log"):
                self.assertNotIn(self.environment["FIXTURE_SECRET"], log.read_text())
        return result

    def events(self):
        path = self.base / "events.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def backups(self):
        path = self.base / "checkout-upgrade-backups"
        return [item for item in path.iterdir() if item.is_dir()] if path.exists() else []

    def assert_head(self, expected):
        self.assertEqual(expected, self.git(self.root, "rev-parse", "HEAD").strip())

    def assert_not_stopped(self):
        self.assertFalse(any("stop" in event for event in self.events()))

    def test_full_upgrade_and_repeat_preserve_configuration_and_runtime(self):
        originals = {name: ((self.root / name).read_bytes(), (self.root / name).stat())
                     for name in [".env", "automation-server/.env"]}
        result = self.run_upgrade()
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assert_head(self.target)
        self.assertIn("fixture build:production", (self.root / "public/control-app/index.html").read_text())
        self.assertEqual("keep installation", (self.root / "runtime/install.lock").read_text())
        self.assertEqual("keep session", (self.root / "runtime/session/fixture").read_text())
        for name, (content, stat) in originals.items():
            current = (self.root / name).stat()
            self.assertEqual(content, (self.root / name).read_bytes())
            self.assertEqual((stat.st_mode, stat.st_uid, stat.st_gid), (current.st_mode, current.st_uid, current.st_gid))
        backup = self.backups()[0]
        self.assertEqual(0o700, backup.stat().st_mode & 0o777)
        for name in ["panel.env", "automation.env", "config-metadata.json"]:
            self.assertEqual(0o600, (backup / name).stat().st_mode & 0o777)
        self.assertIn(self.before, (backup / "versions.txt").read_text())
        self.assertIn(self.target, (backup / "versions.txt").read_text())
        result = self.run_upgrade()
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual(2, len((self.base / "migrations").read_text().splitlines()))
        self.assertEqual(2, len(self.backups()))

    def test_source_only_checkout_can_upgrade_and_repeat(self):
        self.git(self.root, "fetch", "origin", "main")
        self.git(self.root, "merge", "--ff-only", self.target)
        self.git(self.root, "rm", "-r", "public/control-app", "public/react-preview")
        with (self.root / ".gitignore").open("a") as output:
            output.write("/public/control-app/\n/public/react-preview/\n")
        self.git(self.root, "add", ".gitignore")
        self.git(self.root, "commit", "-m", "fixture source-only distribution")
        self.git(self.root, "push", "origin", "main")
        self.before = self.git(self.root, "rev-parse", "HEAD").strip()
        for directory in ["public/control-app", "public/react-preview"]:
            path = self.root / directory
            path.mkdir(parents=True)
            (path / "index.html").write_text("generated initial frontend")
        state = self.root / ".git/appleid-upgrade-artifacts.json"
        subprocess.run([self.environment["REAL_PHP"], str(self.root / "scripts/upgrade_helper.php"),
                        str(self.root), "record-artifacts", str(state)], check=True, capture_output=True)
        self.git(self.seed, "fetch", "origin", "main")
        self.git(self.seed, "merge", "--ff-only", self.before)
        (self.seed / "app.txt").write_text("updated source-only release")
        self.git(self.seed, "commit", "-am", "fixture next source release")
        self.git(self.seed, "push", "origin", "main")
        self.target = self.git(self.seed, "rev-parse", "HEAD").strip()
        for _ in range(2):
            result = self.run_upgrade()
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            self.assert_head(self.target)
            self.assertIn("fixture build:production", (self.root / "public/control-app/index.html").read_text())
            self.assertEqual("keep installation", (self.root / "runtime/install.lock").read_text())

    def test_check_does_not_fetch_build_backup_or_stop(self):
        result = self.run_upgrade("--check")
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assert_head(self.before)
        self.assertFalse((self.root / ".git/FETCH_HEAD").exists())
        self.assertFalse(self.backups())
        self.assert_not_stopped()
        self.assertFalse(any(event[0] == "npm" and "ci" in event for event in self.events()))

    def test_source_and_staged_changes_are_rejected(self):
        (self.root / "app.txt").write_text("local customization")
        result = self.run_upgrade()
        self.assertNotEqual(0, result.returncode)
        self.assert_head(self.before)
        self.assertEqual("local customization", (self.root / "app.txt").read_text())
        self.assert_not_stopped()
        self.git(self.root, "add", "app.txt")
        self.assertNotEqual(0, self.run_upgrade().returncode)

    def test_divergence_and_wrong_branch_are_rejected(self):
        (self.root / "local.txt").write_text("local commit")
        self.git(self.root, "add", "local.txt")
        self.git(self.root, "commit", "-m", "local history")
        self.assertNotEqual(0, self.run_upgrade().returncode)
        self.git(self.root, "checkout", "-b", "other")
        self.assertNotEqual(0, self.run_upgrade().returncode)
        self.assert_not_stopped()

    def test_only_own_unchanged_generated_artifacts_can_be_replaced(self):
        result = self.run_upgrade()
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        (self.root / "public/control-app/assets/built.js").write_text("manual change")
        result = self.run_upgrade()
        self.assertNotEqual(0, result.returncode)
        self.assertEqual("manual change", (self.root / "public/control-app/assets/built.js").read_text())

    def test_ignored_extra_artifact_is_not_discarded(self):
        with (self.root / ".git/info/exclude").open("a") as file:
            file.write("\n*.local\n")
        (self.root / "public/control-app/notes.local").write_text("keep this")
        self.assertNotEqual(0, self.run_upgrade().returncode)
        self.assertEqual("keep this", (self.root / "public/control-app/notes.local").read_text())
        self.assert_not_stopped()

    def test_lock_prevents_concurrent_execution(self):
        with (self.root / ".git/appleid-upgrade.lock").open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = self.run_upgrade()
        self.assertNotEqual(0, result.returncode)
        self.assertIn("已有升级", result.stderr)
        self.assert_not_stopped()

    def test_enabled_accounts_and_recovery_lock_refuse_stop(self):
        for variables in [{"ENABLED_ACCOUNTS": "1"}, {"RECOVERY_LOCK": "1"}, {"QUEUE_SIZE": "1"},
                          {"DRIVER_QUEUE": "1"}, {"GRID_BUSY": "1"}, {"DRIVER_RUNNING": "1"}]:
            with self.subTest(variables=variables):
                result = self.run_upgrade("--check", **variables)
                self.assertNotEqual(0, result.returncode)
                self.assert_head(self.before)
                self.assert_not_stopped()

    def test_legacy_driver_needs_explicit_stop_before_upgrade(self):
        result = self.run_upgrade("--check", LEGACY_DRIVER_HEALTH="1")
        self.assertNotEqual(0, result.returncode)
        self.assertIn("driver_idle_status_unavailable", result.stdout)
        state = json.loads((self.base / "containers.json").read_text())
        state["account-driver"] = False
        (self.base / "containers.json").write_text(json.dumps(state))
        result = self.run_upgrade("--check", LEGACY_DRIVER_HEALTH="1")
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)

    def test_uncertain_and_active_manual_recovery_refuse_stop(self):
        for sql in ["INSERT INTO feature_schedule VALUES('uncertain')",
                    "INSERT INTO task_runs VALUES('running')",
                    "INSERT INTO task_logs VALUES(1,1,'security_rollback','running')"]:
            with self.subTest(sql=sql):
                with sqlite3.connect(self.base / "tasks.sqlite3") as db:
                    db.executescript("DELETE FROM feature_schedule; DELETE FROM task_runs; DELETE FROM task_logs;" + sql)
                self.assertNotEqual(0, self.run_upgrade("--check").returncode)
                self.assert_not_stopped()

    def test_completed_recovery_history_does_not_block_upgrade_check(self):
        with sqlite3.connect(self.base / "tasks.sqlite3") as db:
            db.executescript("""INSERT INTO task_logs VALUES(1,1,'security_rollback','running');
                INSERT INTO task_logs VALUES(2,1,'security_rollback','success');
                INSERT INTO task_runs VALUES('uncertain');""")
        result = self.run_upgrade("--check")
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)

    def test_external_bootstrap_updates_old_checkout_without_installing_tools_first(self):
        self.git(self.root, "fetch", "origin", "main")
        self.git(self.root, "merge", "--ff-only", self.target)
        self.git(self.root, "rm", "upgrade.sh", "scripts/upgrade_helper.php", "scripts/upgrade_probe.py")
        self.git(self.root, "commit", "-m", "fixture old checkout without upgrade tools")
        self.before = self.git(self.root, "rev-parse", "HEAD").strip()
        bootstrap = self.base / "bootstrap"
        bootstrap.mkdir()
        for name in ["upgrade.sh", "scripts/upgrade_helper.php", "scripts/upgrade_probe.py"]:
            target = self.root / name
            target.parent.mkdir(exist_ok=True)
            shutil.copy(SOURCE / name, target)
            external = bootstrap / name
            external.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(SOURCE / name, external)
        self.git(self.root, "add", "upgrade.sh", "scripts")
        self.git(self.root, "commit", "-m", "fixture introduce upgrade tools")
        self.target = self.git(self.root, "rev-parse", "HEAD").strip()
        self.git(self.root, "push", "origin", "main")
        self.git(self.root, "reset", "--hard", self.before)
        self.entry = bootstrap / "upgrade.sh"
        self.assertFalse((self.root / "upgrade.sh").exists())
        result = self.run_upgrade("--check", UPGRADE_PROJECT_ROOT=str(self.root))
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertFalse((self.root / "upgrade.sh").exists())
        result = self.run_upgrade(UPGRADE_PROJECT_ROOT=str(self.root))
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assert_head(self.target)

    def test_backup_directory_inside_project_is_rejected(self):
        result = self.run_upgrade("--backup-dir", str(self.root / "public/local-backup"))
        self.assertNotEqual(0, result.returncode)
        self.assert_head(self.before)
        self.assert_not_stopped()
        self.assertFalse((self.root / "public/local-backup/panel.env").exists())

    def test_preparation_failures_keep_existing_deployment(self):
        for step in ["composer", "npm", "build:production", "build", "docker-build", "pull"]:
            with self.subTest(step=step):
                result = self.run_upgrade(FAIL_STEP=step)
                self.assertNotEqual(0, result.returncode)
                self.assert_head(self.before)
                self.assertEqual("initial control app", (self.root / "public/control-app/index.html").read_text())
                self.assert_not_stopped()

    def test_migration_failure_keeps_services_stopped_and_can_retry(self):
        result = self.run_upgrade(FAIL_STEP="migration")
        self.assertNotEqual(0, result.returncode)
        self.assertIn("幂等迁移", result.stdout)
        self.assert_head(self.target)
        state = json.loads((self.base / "containers.json").read_text())
        self.assertFalse(state["automation-server"])
        self.assertTrue(state["redis"])
        result = self.run_upgrade()
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)

    def test_hook_and_health_failures_report_failure_without_rollback(self):
        for step in ["hooks", "up", "health", "bootstrap"]:
            with self.subTest(step=step):
                result = self.run_upgrade(FAIL_STEP=step)
                self.assertNotEqual(0, result.returncode)
                self.assert_head(self.target)
                self.assertNotIn("升级成功", result.stdout)
                self.assertTrue(self.backups())


if __name__ == "__main__":
    unittest.main()
