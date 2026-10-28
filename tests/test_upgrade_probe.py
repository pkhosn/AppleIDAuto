"""Read-only upgrade checks retain dormant profile recovery results without clearing them."""
import importlib.util
from pathlib import Path
import sqlite3
import unittest

spec = importlib.util.spec_from_file_location('upgrade_probe', Path(__file__).resolve().parents[1] / 'scripts/upgrade_probe.py')
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class UpgradeProbeTest(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        self.db.executescript('''CREATE TABLE tasks(status TEXT);
            CREATE TABLE task_runs(status TEXT);
            CREATE TABLE task_logs(id INTEGER,account_id INTEGER,action TEXT,status TEXT);
            CREATE TABLE feature_schedule(account_id INTEGER, feature TEXT, enabled INTEGER,
                force_run INTEGER,reconcile_at TEXT,result TEXT);''')

    def insert(self, feature='profile_restore', enabled=0, force_run=0, reconcile_at=None, result='uncertain'):
        self.db.execute('INSERT INTO feature_schedule VALUES(1,?,?,?,?,?)',
                        (feature, enabled, force_run, reconcile_at, result))

    def test_disabled_unscheduled_profile_uncertain_is_retained_without_writes(self):
        self.insert()
        before = '\n'.join(self.db.iterdump())
        self.assertEqual(1, probe.check_database(self.db))
        self.assertEqual(before, '\n'.join(self.db.iterdump()))

    def test_enabled_forced_and_reconciling_profile_results_still_block(self):
        for values in [{'enabled': 1}, {'force_run': 1}, {'reconcile_at': '2026-01-01T00:00:00+00:00'}]:
            with self.subTest(values=values):
                self.db.execute('DELETE FROM feature_schedule')
                self.insert(**values)
                with self.assertRaisesRegex(RuntimeError, 'task_active_or_uncertain'):
                    probe.check_database(self.db)

    def test_other_uncertain_features_and_running_tasks_still_block(self):
        for values in [{'feature': 'password_update'}, {'feature': 'two_factor'},
                       {'feature': 'device_cleanup'}, {'result': 'running'}]:
            with self.subTest(values=values):
                self.db.execute('DELETE FROM feature_schedule')
                self.insert(**values)
                with self.assertRaisesRegex(RuntimeError, 'task_active_or_uncertain'):
                    probe.check_database(self.db)

    def test_active_manual_recovery_still_blocks_with_dormant_profile_result(self):
        self.insert()
        self.db.execute("INSERT INTO task_logs VALUES(1,1,'security_rollback','running')")
        with self.assertRaisesRegex(RuntimeError, 'manual_recovery_active'):
            probe.check_database(self.db)

    def test_missing_legacy_fields_do_not_bypass_uncertainty_check(self):
        self.db.execute('DROP TABLE feature_schedule')
        self.db.execute('CREATE TABLE feature_schedule(result TEXT)')
        self.db.execute("INSERT INTO feature_schedule VALUES('uncertain')")
        with self.assertRaisesRegex(RuntimeError, 'task_active_or_uncertain'):
            probe.check_database(self.db)


if __name__ == '__main__':
    unittest.main()
