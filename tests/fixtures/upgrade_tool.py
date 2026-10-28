#!/usr/bin/env python3
"""Fake external tools for upgrade integration tests; never contacts services."""
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import types

name = Path(sys.argv[0]).name
args = sys.argv[1:]
directory = Path(os.environ["UPGRADE_FIXTURE"])
with (directory / "events.jsonl").open("a") as log:
    log.write(json.dumps([name, *args]) + "\n")


def fail(step):
    if os.environ.get("FAIL_STEP") == step:
        print(os.environ["FIXTURE_SECRET"], file=sys.stderr)
        print(os.environ["FIXTURE_SECRET"])
        sys.exit(1)


if name == "php":
    cleaned = list(args)
    while cleaned and cleaned[0] == "-d":
        cleaned = cleaned[2:]
    if cleaned and Path(cleaned[0]).name in {"upgrade_helper.php", "helper.php"}:
        root, action = Path(cleaned[1]), cleaned[2]
        if action == "panel-idle":
            if os.environ.get("ENABLED_ACCOUNTS"):
                print("请先停用全部账号自动任务", file=sys.stderr)
                sys.exit(1)
            sys.exit(0)
        if action == "bootstrap":
            if os.environ.get("FAIL_STEP") == "bootstrap":
                print("PHP bootstrap 健康检查失败", file=sys.stderr)
                sys.exit(1)
            sys.exit(0)
    if cleaned and cleaned[0] == "db/update/review_fixes.php":
        fail("migration")
        with (directory / "migrations").open("a") as log:
            log.write("idempotent\n")
        sys.exit(0)
    if cleaned and cleaned[0] == "think":
        assert cleaned == ["think", "clear", "--cache"]
        sys.exit(0)
    os.execv(os.environ["REAL_PHP"], [os.environ["REAL_PHP"], *args])
elif name == "node":
    print("22")
elif name == "composer":
    if "install" in args:
        fail("composer")
        path = Path(next(arg.split("=", 1)[1] for arg in args if arg.startswith("--working-dir=")))
        (path / "vendor").mkdir()
        (path / "vendor/autoload.php").write_text("<?php // fixture vendor")
    elif "run-script" in args:
        fail("hooks")
elif name == "npm":
    if "--version" in args:
        print("10")
    elif args[0] == "ci":
        fail("npm")
    else:
        mode = args[1]
        fail(mode)
        source = Path(args[args.index("--prefix") + 1]).parent
        destination = source / "public" / ("control-app" if mode == "build:production" else "react-preview")
        (destination / "assets").mkdir(exist_ok=True)
        (destination / "index.html").write_text(f"fixture {mode} from staged source")
        (destination / "assets/built.js").write_text("fixture build output")
elif name == "docker":
    state_path = directory / "containers.json"
    state = json.loads(state_path.read_text())
    ids = {service: hashlib.sha256(service.encode()).hexdigest() for service in state}
    if args[0] == "inspect":
        service = next(service for service, value in ids.items() if value == args[-1])
        if args[2] == "{{.State.Running}} {{.State.Paused}}":
            print("true false" if state[service] else "false false")
        else:
            print(ids[service], "sha256:" + ids[service])
    elif args[0] in {"info"}:
        pass
    else:
        assert args[0] == "compose"
        options = args[1:]
        while options and options[0] in {"--project-directory", "--env-file", "-f", "-p"}:
            if options[0] == "-p":
                assert options[1] == "fixture-project", options
            options = options[2:]
        operation = options[0]
        if operation == "config" and "--format" in options:
            print(json.dumps({"name": "fixture-project"}))
        elif operation == "ps":
            print(ids[options[-1]])
        elif operation == "build":
            fail("docker-build")
        elif operation == "pull":
            fail("pull")
        elif operation == "stop":
            for service in options[1:]:
                state[service] = False
            state_path.write_text(json.dumps(state))
        elif operation == "up":
            fail("up")
            state_path.write_text(json.dumps(dict.fromkeys(state, True)))
        elif operation in {"exec", "run"}:
            script = sys.stdin.read()
            if "check_database" not in script:
                fail("health")
                sys.exit(0)
            # Execute the real read-only probe with fake Redis and HTTP only.
            class Redis:
                @classmethod
                def from_url(cls, *_args, **_kwargs): return cls()
                def ping(self): return True
                def llen(self, _key): return int(os.environ.get("QUEUE_SIZE", "0"))
                def scan_iter(self, *_args, **_kwargs):
                    return iter(["fixture-lock"] if os.environ.get("RECOVERY_LOCK") else [])
            sys.modules["redis"] = types.SimpleNamespace(Redis=Redis)
            import urllib.request
            def urlopen(request, **_kwargs):
                url = request.full_url if hasattr(request, "full_url") else request
                if "3940" in url:
                    health = {"status": True, "queued": int(os.environ.get("DRIVER_QUEUE", "0")),
                              "running": int(os.environ.get("DRIVER_RUNNING", "0"))}
                    if os.environ.get("LEGACY_DRIVER_HEALTH"):
                        del health["running"]
                    return io.BytesIO(json.dumps(health).encode())
                return io.BytesIO(json.dumps({"value": {"ready": True, "nodes": [{"slots": [
                    {"session": {"id": "fixture"} if os.environ.get("GRID_BUSY") else None}]}]}}).encode())
            urllib.request.urlopen = urlopen
            os.environ.update(REDIS_URL="redis://fixture.invalid/0", DATABASE_PATH=str(directory / "tasks.sqlite3"),
                              ACCOUNT_DRIVER_URL="http://account-driver:3940")
            sys.argv = ["-", *options[options.index("-") + 1:]]
            exec(compile(script, "<upgrade-probe>", "exec"), {"__name__": "__main__"})
