"""Durable per-feature cadence; injected clock allows tests without Apple/network calls."""
import hashlib
from datetime import datetime, timedelta, timezone
from .config import settings

ORDER = ("two_factor", "password_check", "profile_restore", "device_cleanup", "password_update")
FLAGS = dict(zip(ORDER, ("enable_auto_disable_two_factor", "check_password_correct",
                         "enable_auto_restore_profile", "enable_delete_devices",
                         "enable_auto_update_password")))


class FeatureSchedule:
    def __init__(self, store, clock=None):
        self.db = store.db
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.db.execute("""CREATE TABLE IF NOT EXISTS feature_schedule (
            account_id INTEGER NOT NULL, feature TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 0, minutes INTEGER NOT NULL,
            source TEXT NOT NULL, last_attempt_at TEXT, last_finished_at TEXT,
            next_run_at TEXT, result TEXT NOT NULL DEFAULT 'never',
            message TEXT NOT NULL DEFAULT '', force_run INTEGER NOT NULL DEFAULT 0,
            retry_count INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(account_id,feature))""")
        columns = {r[1] for r in self.db.execute('PRAGMA table_info(feature_schedule)')}
        if 'retry_count' not in columns:
            self.db.execute('ALTER TABLE feature_schedule ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0')
        if 'reconcile_at' not in columns:
            self.db.execute('ALTER TABLE feature_schedule ADD COLUMN reconcile_at TEXT')
        if 'reconcile_attempts' not in columns:
            self.db.execute('ALTER TABLE feature_schedule ADD COLUMN reconcile_attempts INTEGER NOT NULL DEFAULT 0')
        self.db.commit()

    def rows(self, account_id):
        return {r['feature']: dict(r) for r in self.db.execute(
            'SELECT * FROM feature_schedule WHERE account_id=?', (account_id,))}

    def sync(self, account_id, schedule, account_enabled=True, legacy=None):
        now = self.clock()
        existing = self.rows(account_id)
        for feature in ORDER:
            item = schedule['features'][feature]
            minutes = int(item['effective_minutes'])
            if not 1 <= minutes <= 43200:
                raise ValueError('invalid interval')
            enabled = bool(account_enabled and item['enabled'])
            old = existing.get(feature)
            if old is None:
                # First rollout retains the legacy due time; new accounts start now.
                next_run = (legacy or {}).get('next_run_at') or now.isoformat()
                self.db.execute('''INSERT INTO feature_schedule
                    (account_id,feature,enabled,minutes,source,next_run_at) VALUES(?,?,?,?,?,?)''',
                    (account_id, feature, enabled, minutes, item['source'], next_run if enabled else None))
                continue
            due = old['next_run_at']
            if not enabled:
                due = None
            elif not old['enabled']:
                due = now.isoformat()
            elif minutes != old['minutes']:
                base = datetime.fromisoformat(old['last_finished_at']) if old['last_finished_at'] else now
                due = (base + timedelta(minutes=minutes)).isoformat() if old['last_finished_at'] else now.isoformat()
            if old['result'] == 'uncertain':
                due = None
            self.db.execute('''UPDATE feature_schedule SET enabled=?,minutes=?,source=?,next_run_at=?,
                force_run=CASE WHEN ?=0 THEN 0 ELSE force_run END WHERE account_id=? AND feature=?''',
                (enabled, minutes, item['source'], due, enabled, account_id, feature))
            # Legacy uncertain rows predate automatic reconciliation. Seed only
            # untouched profile rows, after syncing current account authorization.
            if (feature == 'profile_restore' and enabled and old['result'] == 'uncertain'
                    and not old.get('reconcile_at') and not old.get('reconcile_attempts')):
                self.db.execute('''UPDATE feature_schedule SET reconcile_at=?
                    WHERE account_id=? AND feature=?''', (now.isoformat(), account_id, feature))
        self.db.commit()

    def due(self, account_id, coalesce_seconds=0):
        rows, now = self.rows(account_id), self.clock() + timedelta(seconds=max(int(coalesce_seconds), 0))
        return [key for key in ORDER if key in rows and rows[key]['enabled'] and
                rows[key]['result'] not in {'running', 'uncertain'} and
                (rows[key]['force_run'] or (rows[key]['next_run_at'] and
                 datetime.fromisoformat(rows[key]['next_run_at']) <= now))]

    def next_run(self, account_id):
        times = []
        for row in self.rows(account_id).values():
            if not row['enabled']:
                continue
            if row['result'] == 'uncertain' and row.get('reconcile_at'):
                times.append(row['reconcile_at'])
            elif row['result'] != 'running' and row['next_run_at']:
                times.append(row['next_run_at'])
        return min(times) if times else None

    def reconciliation_due(self, account_id):
        now = self.clock()
        return [key for key, row in self.rows(account_id).items()
                if row['enabled'] and row['result'] == 'uncertain' and row.get('reconcile_at')
                and datetime.fromisoformat(row['reconcile_at']) <= now]

    def force(self, account_id, features=None, allow_uncertain=False):
        selected = list(features or ORDER)
        placeholders = ','.join('?' for _ in selected)
        uncertain_sql = "result=CASE WHEN result='uncertain' THEN 'never' ELSE result END," if allow_uncertain else ''
        self.db.execute(f"""UPDATE feature_schedule SET force_run=1,next_run_at=?,{uncertain_sql}
            reconcile_at=NULL,reconcile_attempts=0
            WHERE account_id=? AND enabled=1 AND feature IN ({placeholders})
            AND (?=1 OR result!='uncertain')""",
            (self.clock().isoformat(), account_id, *selected, 1 if allow_uncertain else 0))
        self.db.commit()

    def force_reconciliation(self, account_id, feature):
        self.db.execute("""UPDATE feature_schedule SET reconcile_at=?
            WHERE account_id=? AND feature=? AND enabled=1 AND result='uncertain'""",
            (self.clock().isoformat(), account_id, feature))
        self.db.commit()

    def start(self, account_id, features):
        for feature in features:
            self.db.execute("""UPDATE feature_schedule SET last_attempt_at=?,result='running',force_run=0
                WHERE account_id=? AND feature=?""", (self.clock().isoformat(), account_id, feature))
        self.db.commit()

    def finish(self, account_id, features, result, message=''):
        now = self.clock()
        rows = self.rows(account_id)
        for feature in features:
            row = rows[feature]
            jitter_cap = min(settings.schedule_jitter_seconds, max(int(row['minutes'] * 6), 0))
            seed = hashlib.sha256(f"{account_id}:{feature}".encode()).digest()
            jitter = int.from_bytes(seed[:4], 'big') % (jitter_cap + 1) if jitter_cap else 0
            due = (now + timedelta(minutes=row['minutes'], seconds=jitter)).isoformat() if row['enabled'] and result != 'uncertain' else None
            reconcile_at = ((now + timedelta(seconds=settings.reconciliation_cooldown_seconds)).isoformat()
                            if row['enabled'] and result == 'uncertain' else None)
            self.db.execute('''UPDATE feature_schedule SET last_finished_at=?,result=?,message=?,next_run_at=?,
                reconcile_at=?,reconcile_attempts=CASE WHEN ?='uncertain' THEN reconcile_attempts ELSE 0 END,
                retry_count=CASE WHEN ?='success' THEN 0 ELSE retry_count END
                WHERE account_id=? AND feature=?''',
                (now.isoformat(), result, message, due, reconcile_at, result, result, account_id, feature))
        self.db.commit()

    def finish_reconciliation(self, account_id, feature, result, message):
        row = self.rows(account_id)[feature]
        now = self.clock()
        if result == 'uncertain':
            attempts = int(row.get('reconcile_attempts') or 0) + 1
            reconcile_at = ((now + timedelta(seconds=settings.reconciliation_cooldown_seconds)).isoformat()
                            if attempts < 2 else None)
            self.db.execute('''UPDATE feature_schedule SET message=?,reconcile_at=?,reconcile_attempts=?
                WHERE account_id=? AND feature=?''', (message, reconcile_at, attempts, account_id, feature))
        else:
            due = (now if feature == 'profile_restore' and result == 'failed'
                   else now + timedelta(minutes=int(row['minutes']))).isoformat() if row['enabled'] else None
            self.db.execute('''UPDATE feature_schedule SET result=?,message=?,last_finished_at=?,next_run_at=?,
                reconcile_at=NULL,reconcile_attempts=0 WHERE account_id=? AND feature=?''',
                (result, message, now.isoformat(), due, account_id, feature))
        self.db.commit()

    def retry_technical(self, account_id, features, seconds, limit):
        """Only call for failures known to precede a mutation; bounded per cycle."""
        delays = tuple(seconds) if isinstance(seconds, (tuple, list)) else (int(seconds),)
        rows = self.rows(account_id)
        for key in features:
            row = rows[key]
            retries = row['retry_count'] + 1
            delay = delays[min(int(row['retry_count'] or 0), len(delays) - 1)]
            next_run = (self.clock() + timedelta(seconds=delay)).isoformat()
            if retries <= limit and row['enabled']:
                self.db.execute('UPDATE feature_schedule SET retry_count=?,next_run_at=? WHERE account_id=? AND feature=?',
                                (retries, next_run, account_id, key))
            else:
                self.db.execute('UPDATE feature_schedule SET retry_count=0 WHERE account_id=? AND feature=?', (account_id, key))
        self.db.commit()

    def recover_interrupted(self):
        # Only an explicitly journaled, unconfirmed mutation is uncertain.
        # Queued/login/read-only work is safe to schedule again after restart.
        now = self.clock().isoformat()
        interrupted = [r[0] for r in self.db.execute(
            "SELECT DISTINCT account_id FROM feature_schedule WHERE result='running'"
        )]
        for account_id in interrupted:
            active = self.db.execute("""SELECT * FROM task_runs WHERE account_id=?
                AND status IN ('queued','running','reporting','reconciling')
                ORDER BY queued_at DESC LIMIT 1""", (account_id,)).fetchone()
            rows = self.db.execute(
                "SELECT feature FROM feature_schedule WHERE account_id=? AND result='running'", (account_id,)
            ).fetchall()
            for item in rows:
                feature = item[0]
                mutation_uncertain = bool(
                    active and active['mutation_state'] == 'submitted'
                    and active['mutation_feature'] == feature
                )
                result = 'uncertain' if mutation_uncertain else 'failed'
                next_run = None if mutation_uncertain else now
                reconcile_at = ((self.clock() + timedelta(seconds=settings.reconciliation_cooldown_seconds)).isoformat()
                                if mutation_uncertain else None)
                message = (
                    '服务中断，修改结果待只读核验；不会自动重放'
                    if mutation_uncertain else '服务中断于修改前或只读阶段，已安全重新排期'
                )
                self.db.execute("""UPDATE feature_schedule SET result=?,next_run_at=?,reconcile_at=?,message=?
                    WHERE account_id=? AND feature=?""",
                    (result, next_run, reconcile_at, message, account_id, feature))
            if active:
                self.db.execute("""UPDATE task_runs SET status='interrupted',stage='interrupted',
                    error_code='service_restarted',message='服务重启，已按持久化阶段恢复',finished_at=?,updated_at=?
                    WHERE run_id=?""", (now, now, active['run_id']))
            self.db.execute("""UPDATE tasks SET status='failed',next_run_at=?,
                message='服务重启，任务已按持久化阶段恢复' WHERE account_id=?""", (now, account_id))
        self.db.commit()

    @staticmethod
    def mask(info, due):
        output = dict(info)
        for feature, flag in FLAGS.items():
            output[flag] = feature in due
        if 'profile_restore' not in due:
            output.pop('profile_restore', None)
        return output
