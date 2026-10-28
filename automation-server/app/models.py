import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class TaskStore:
    def __init__(self, path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("""CREATE TABLE IF NOT EXISTS tasks (
            account_id INTEGER PRIMARY KEY, status TEXT NOT NULL,
            next_run_at TEXT, retry_count INTEGER NOT NULL DEFAULT 0,
            message TEXT, updated_at TEXT NOT NULL)""")
        self.db.execute("""CREATE TABLE IF NOT EXISTS task_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, account_id INTEGER,
            action TEXT NOT NULL, status TEXT NOT NULL, message TEXT,
            created_at TEXT NOT NULL)""")
        self.db.execute("""CREATE TABLE IF NOT EXISTS security_rollback_seen (
            account_id INTEGER NOT NULL, message_id TEXT NOT NULL,
            seen_at TEXT NOT NULL,
            PRIMARY KEY (account_id, message_id))""")
        self.db.execute("""CREATE TABLE IF NOT EXISTS task_runs (
            run_id TEXT PRIMARY KEY, account_id INTEGER NOT NULL,
            status TEXT NOT NULL, stage TEXT NOT NULL, error_code TEXT NOT NULL DEFAULT '',
            message TEXT NOT NULL DEFAULT '', requested_features TEXT NOT NULL,
            current_feature TEXT NOT NULL DEFAULT '', config_revision TEXT NOT NULL DEFAULT '',
            driver_job_id TEXT NOT NULL DEFAULT '', retry_decision TEXT NOT NULL DEFAULT '',
            mutation_feature TEXT NOT NULL DEFAULT '', mutation_state TEXT NOT NULL DEFAULT 'none',
            queued_at TEXT NOT NULL, started_at TEXT, heartbeat_at TEXT NOT NULL,
            finished_at TEXT, updated_at TEXT NOT NULL)""")
        self.db.execute("""CREATE INDEX IF NOT EXISTS task_runs_account_idx
            ON task_runs(account_id, queued_at DESC)""")
        self.db.execute("""CREATE TABLE IF NOT EXISTS task_run_steps (
            run_id TEXT NOT NULL, feature TEXT NOT NULL, status TEXT NOT NULL,
            stage TEXT NOT NULL DEFAULT '', error_code TEXT NOT NULL DEFAULT '',
            message TEXT NOT NULL DEFAULT '', attempts INTEGER NOT NULL DEFAULT 0,
            metadata TEXT NOT NULL DEFAULT '{}', started_at TEXT, finished_at TEXT, duration_ms INTEGER,
            PRIMARY KEY(run_id,feature), FOREIGN KEY(run_id) REFERENCES task_runs(run_id))""")
        step_columns = {row[1] for row in self.db.execute("PRAGMA table_info(task_run_steps)").fetchall()}
        if "metadata" not in step_columns:
            self.db.execute("ALTER TABLE task_run_steps ADD COLUMN metadata TEXT NOT NULL DEFAULT '{}'")
        self.db.commit()

    def upsert(self, account_id: int, status: str, message: str = "", next_run_at=None):
        self.db.execute("""INSERT INTO tasks(account_id,status,next_run_at,message,updated_at)
            VALUES(?,?,?,?,?) ON CONFLICT(account_id) DO UPDATE SET status=excluded.status,
            next_run_at=excluded.next_run_at,message=excluded.message,updated_at=excluded.updated_at""",
            (account_id, status, next_run_at, message, utc_now()))
        self.db.commit()

    def set_retry_count(self, account_id: int, retry_count: int):
        self.db.execute("UPDATE tasks SET retry_count=? WHERE account_id=?", (retry_count, account_id))
        self.db.commit()

    def get(self, account_id: int):
        return self.db.execute("SELECT * FROM tasks WHERE account_id=?", (account_id,)).fetchone()

    def all(self):
        return self.db.execute("SELECT * FROM tasks ORDER BY account_id").fetchall()

    def log(self, account_id: int, action: str, status: str, message: str):
        self.db.execute("INSERT INTO task_logs(account_id,action,status,message,created_at) VALUES(?,?,?,?,?)",
                        (account_id, action, status, message, utc_now()))
        self.db.commit()

    def manual_recovery_logs(self, account_id: int, limit: int = 20):
        safe_limit = min(max(int(limit), 1), 100)
        rows = self.db.execute(
            """SELECT action,status,message,created_at FROM task_logs
               WHERE account_id=? AND action IN ('email_password_recovery', 'security_rollback')
               ORDER BY id DESC LIMIT ?""",
            (account_id, safe_limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def rollback_seen_message_ids(self, account_id: int) -> set[str]:
        rows = self.db.execute(
            "SELECT message_id FROM security_rollback_seen WHERE account_id=?",
            (account_id,),
        ).fetchall()
        return {str(row["message_id"]) for row in rows}

    def mark_rollback_seen(self, account_id: int, message_id: str):
        message_id = str(message_id or "").strip()
        if not message_id:
            return
        self.db.execute(
            """INSERT OR IGNORE INTO security_rollback_seen(account_id, message_id, seen_at)
               VALUES(?,?,?)""",
            (account_id, message_id, utc_now()),
        )
        self.db.commit()

    def create_run(self, run_id: str, account_id: int, features: list[str], config_revision: str = ""):
        now = utc_now()
        self.db.execute("""INSERT OR IGNORE INTO task_runs
            (run_id,account_id,status,stage,requested_features,config_revision,queued_at,heartbeat_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?)""", (
                run_id, account_id, 'queued', 'queued', json.dumps(features),
                str(config_revision or ''), now, now, now,
            ))
        for feature in features:
            self.db.execute("""INSERT OR IGNORE INTO task_run_steps
                (run_id,feature,status,stage) VALUES(?,?,?,?)""",
                (run_id, feature, 'queued', 'queued'))
        self.db.commit()

    def update_run(self, run_id: str, *, status: str | None = None, stage: str | None = None,
                   feature: str | None = None, error_code: str | None = None,
                   message: str | None = None, driver_job_id: str | None = None,
                   retry_decision: str | None = None, mutation_feature: str | None = None,
                   mutation_state: str | None = None, started: bool = False,
                   finished: bool = False):
        now = utc_now()
        values = {'heartbeat_at': now, 'updated_at': now}
        optional = {
            'status': status, 'stage': stage, 'current_feature': feature,
            'error_code': error_code, 'message': message, 'driver_job_id': driver_job_id,
            'retry_decision': retry_decision, 'mutation_feature': mutation_feature,
            'mutation_state': mutation_state,
        }
        values.update({key: value for key, value in optional.items() if value is not None})
        if started:
            values['started_at'] = now
        if finished:
            values['finished_at'] = now
        assignments = ','.join(f'{key}=?' for key in values)
        self.db.execute(f'UPDATE task_runs SET {assignments} WHERE run_id=?', (*values.values(), run_id))
        self.db.commit()

    def update_run_step(self, run_id: str, feature: str, status: str, *, stage: str = '',
                        error_code: str = '', message: str = '', attempts: int | None = None,
                        started: bool = False, finished: bool = False, metadata: dict | None = None):
        now = utc_now()
        row = self.db.execute(
            'SELECT started_at,attempts,metadata FROM task_run_steps WHERE run_id=? AND feature=?',
            (run_id, feature),
        ).fetchone()
        if row is None:
            self.db.execute("""INSERT INTO task_run_steps
                (run_id,feature,status,stage,error_code,message,attempts,metadata,started_at,finished_at)
                VALUES(?,?,?,?,?,?,?,?,?,?)""", (
                    run_id, feature, status, stage, error_code, message,
                    attempts if attempts is not None else (1 if started else 0),
                    json.dumps(metadata or {}), now if started else None, now if finished else None,
                ))
        else:
            started_at = row['started_at'] or (now if started else None)
            attempt_count = attempts if attempts is not None else int(row['attempts'] or 0) + (1 if started else 0)
            duration_ms = None
            if finished and started_at:
                duration_ms = max(int((datetime.fromisoformat(now) - datetime.fromisoformat(started_at)).total_seconds() * 1000), 0)
            encoded_metadata = json.dumps(metadata) if metadata is not None else str(row['metadata'] or '{}')
            self.db.execute("""UPDATE task_run_steps SET status=?,stage=?,error_code=?,message=?,
                attempts=?,metadata=?,started_at=?,finished_at=?,duration_ms=? WHERE run_id=? AND feature=?""", (
                    status, stage, error_code, message, attempt_count, encoded_metadata, started_at,
                    now if finished else None, duration_ms, run_id, feature,
                ))
        self.db.commit()

    def run(self, run_id: str):
        row = self.db.execute('SELECT * FROM task_runs WHERE run_id=?', (run_id,)).fetchone()
        return dict(row) if row else None

    def active_run(self, account_id: int):
        row = self.db.execute("""SELECT * FROM task_runs WHERE account_id=?
            AND status IN ('queued','running','reporting','reconciling')
            ORDER BY queued_at DESC LIMIT 1""", (account_id,)).fetchone()
        return dict(row) if row else None

    def recent_runs(self, account_id: int, limit: int = 10):
        safe_limit = min(max(int(limit), 1), 50)
        runs = [dict(row) for row in self.db.execute(
            'SELECT * FROM task_runs WHERE account_id=? ORDER BY queued_at DESC LIMIT ?',
            (account_id, safe_limit),
        ).fetchall()]
        for run in runs:
            run['requested_features'] = json.loads(run.get('requested_features') or '[]')
            run['steps'] = [dict(row) for row in self.db.execute(
                'SELECT * FROM task_run_steps WHERE run_id=? ORDER BY rowid', (run['run_id'],),
            ).fetchall()]
            for step in run['steps']:
                step['metadata'] = json.loads(step.get('metadata') or '{}')
        return runs

    def interrupted_runs(self):
        return [dict(row) for row in self.db.execute("""SELECT * FROM task_runs
            WHERE status IN ('queued','running','reporting','reconciling') ORDER BY queued_at""").fetchall()]
