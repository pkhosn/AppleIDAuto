"""Read-only maintenance probe, piped to Python in the existing Compose network.

Do not import the service's app: its constructors initialize/write SQLite tables.
Only controlled error codes leave the container; never print exception details.
"""
import json
import os
from pathlib import Path
import sqlite3
import sys
import urllib.request


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def check_database(connection):
    checks = {
        "tasks": "status IN ('pending','queued','running','reporting','reconciling','uncertain')",
        "feature_schedule": "result = 'running'",
        "task_runs": "status IN ('queued','running','reporting','reconciling')",
    }
    for table, predicate in checks.items():
        require(not connection.execute(f"SELECT 1 FROM {table} WHERE {predicate} LIMIT 1").fetchone(),
                "task_active_or_uncertain")
    # A disabled profile-recovery result can be retained across a code upgrade.
    # It remains uncertain and must never be rescheduled or marked successful here.
    cursor = connection.execute("SELECT * FROM feature_schedule WHERE result='uncertain'")
    names = [column[0] for column in cursor.description]
    uncertain = [dict(zip(names, row)) for row in cursor.fetchall()]
    for row in uncertain:
        require(row.get('feature') == 'profile_restore' and row.get('enabled') == 0
                and row.get('force_run') == 0 and 'reconcile_at' in row and row['reconcile_at'] is None,
                "task_active_or_uncertain")
    # Each manual recovery writes a start and a terminal log for its account/kind.
    require(not connection.execute("""SELECT 1 FROM task_logs AS l WHERE l.action IN
        ('email_password_recovery','security_rollback') AND l.status='running'
        AND NOT EXISTS (SELECT 1 FROM task_logs AS n WHERE n.account_id=l.account_id
        AND n.action=l.action AND n.id>l.id) LIMIT 1""").fetchone(), "manual_recovery_active")
    return len(uncertain)


def main():
    import redis

    client = redis.Redis.from_url(os.environ["REDIS_URL"], socket_timeout=5, socket_connect_timeout=5)
    require(client.ping(), "redis_unavailable")
    require(client.llen("automation:queue") == 0, "queue_not_empty")
    require(next(client.scan_iter("automation:lock:*", count=100), None) is None, "account_lock_active")
    path = Path(os.environ["DATABASE_PATH"]).resolve()
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5) as connection:
        retained_uncertain = check_database(connection)
    require(not any((path.parent / "scheduled-password-escrow").glob("*.secret")),
            "password_writeback_pending")

    if sys.argv[1] == "running":
        driver = os.environ.get("ACCOUNT_DRIVER_URL", "http://account-driver:3940").rstrip("/")
        require(driver == "http://account-driver:3940", "external_driver_not_supported")
        request = urllib.request.Request(driver + "/health")
        with urllib.request.urlopen(request, timeout=5) as response:
            health = json.load(response)
        require(type(health.get("running")) is int, "driver_idle_status_unavailable")
        require(health.get("status") is True and health.get("queued") == 0
                and health["running"] == 0, "driver_not_idle")
    if sys.argv[2] == "running":
        with urllib.request.urlopen("http://selenium:4444/status", timeout=5) as response:
            grid = json.load(response)["value"]
        require(grid.get("ready") is True and bool(grid.get("nodes")), "grid_unavailable")
        require(all(not slot.get("session") for node in grid["nodes"] for slot in node["slots"]),
                "selenium_session_active")
    if retained_uncertain:
        print(f"保留 {retained_uncertain} 条已停用的资料恢复待核验记录，未改变结果或恢复任务。")
    print("任务、恢复锁、队列和浏览器均空闲。")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        known = {
            "redis_unavailable", "queue_not_empty", "account_lock_active", "task_active_or_uncertain",
            "manual_recovery_active", "password_writeback_pending", "external_driver_not_supported",
            "driver_not_idle", "grid_unavailable", "selenium_session_active",
            "driver_idle_status_unavailable",
        }
        code = str(error) if type(error) is RuntimeError and str(error) in known else "probe_unavailable"
        print("升级检查未通过：" + code + "；请先处理任务状态或服务连接。")
        sys.exit(1)
