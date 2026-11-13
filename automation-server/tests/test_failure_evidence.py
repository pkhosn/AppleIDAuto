import asyncio
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from app.models import TaskStore
from app.failure_evidence import failure_details, read_account_image
from app.account_client import AccountClient
from app.config import settings
from test_account_client import Client


JOB = '11111111-1111-4111-8111-111111111111'
OTHER = '22222222-2222-4222-8222-222222222222'
PNG = b'\x89PNG\r\n\x1a\nfixture'


class FailureEvidenceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = TaskStore(str(self.root / 'tasks.sqlite3'))
        self.store.upsert(1, 'failed', 'Apple 页面未加载')
        self.store.record_driver_job(1, JOB, 'check')
        self.store.finish_driver_job(JOB, 'failed', 'login', 'authentication_challenge_unrendered')
        self.store.record_driver_job(2, OTHER, 'check')
        self.store.finish_driver_job(OTHER, 'failed', 'login', 'password_invalid')
        (self.root / (JOB + '.png')).write_bytes(PNG)
        (self.root / (JOB + '-attempt-1.png')).write_bytes(PNG)
        (self.root / (OTHER + '.png')).write_bytes(PNG)

    def tearDown(self):
        self.store.db.close()
        self.tmp.cleanup()

    def test_failure_lists_only_associated_images_and_survives_restart(self):
        self.store.db.close()
        self.store = TaskStore(str(self.root / 'tasks.sqlite3'))
        details = failure_details(self.store, 1, self.root)
        self.assertEqual('Apple 页面未加载', details['message'])
        self.assertEqual('authentication_challenge_unrendered', details['error_code'])
        self.assertEqual({JOB + '.png', JOB + '-attempt-1.png'}, {image['name'] for image in details['images']})
        self.assertEqual(PNG, read_account_image(self.store, 1, self.root, JOB + '.png'))
        self.assertIsNone(read_account_image(self.store, 2, self.root, JOB + '.png'))

    def test_traversal_symlinks_non_png_and_large_files_are_rejected(self):
        for name in ('../' + JOB + '.png', JOB + '-diagnostics.json', '/etc/passwd'):
            self.assertIsNone(read_account_image(self.store, 1, self.root, name))
        link = self.root / (JOB + '-linked.png')
        link.symlink_to(self.root / (JOB + '.png'))
        self.assertIsNone(read_account_image(self.store, 1, self.root, link.name))
        bad = self.root / (JOB + '-bad.png')
        bad.write_text('not a png')
        self.assertIsNone(read_account_image(self.store, 1, self.root, bad.name))
        bad.write_bytes(PNG)
        with patch('app.failure_evidence.MAX_IMAGE_BYTES', 2):
            self.assertIsNone(read_account_image(self.store, 1, self.root, bad.name))

    def test_cleaned_images_keep_failure_reason_and_unknown_history_has_no_images(self):
        for path in self.root.glob('*.png'):
            path.unlink()
        details = failure_details(self.store, 1, self.root)
        self.assertEqual([], details['images'])
        self.assertEqual('Apple 页面未加载', details['message'])
        self.assertIsNone(read_account_image(self.store, 1, self.root, JOB + '.png'))
        self.assertEqual([], failure_details(self.store, 3, self.root)['images'])

    def test_old_workflow_association_is_supported_without_guessing(self):
        self.store.create_run('old-run', 3, ['password_check'])
        self.store.update_run('old-run', status='failed', driver_job_id=JOB, error_code='password_invalid')
        self.assertEqual(2, len(failure_details(self.store, 3, self.root)['images']))
        self.assertIsNone(read_account_image(self.store, 4, self.root, JOB + '.png'))

    def test_client_records_account_job_without_payload_secrets(self):
        class FailedClient(Client):
            async def post(self, url, json, headers):
                self.test_payload = json
                assert '_account_id' not in json
                return await super().post(url, json, headers)
            async def get(self, url, headers):
                from test_account_client import Response
                return Response({'job_status': 'failed', 'result': {
                    'status': False, 'error_code': 'password_form_missing', 'stage': 'password_form',
                }})
        cfg = replace(settings, database_path=str(self.root / 'tasks.sqlite3'), account_driver_url='http://driver',
                      account_driver_poll_interval=0.001)
        with patch('app.account_client.settings', cfg), patch('app.account_client.httpx.AsyncClient', FailedClient):
            result = asyncio.run(AccountClient().run('complete_email_reset', {
                '_account_id': 5, 'username': 'fixture', 'password': 'SECRET', 'reset_url': 'SECRET-LINK',
            }))
        self.assertFalse(result['status'])
        rows = self.store.driver_jobs(5)
        self.assertEqual('password_form_missing', rows[0]['error_code'])
        self.assertEqual('failed', rows[0]['status'])
        self.assertNotIn('SECRET', repr(rows))

    def test_api_requires_authentication_and_keeps_account_image_boundary(self):
        from app import api
        from fastapi import HTTPException
        with patch.object(api, 'store', self.store), patch.object(api, 'settings', replace(settings, driver_artifact_dir=str(self.root))):
            with self.assertRaises(HTTPException) as denied:
                asyncio.run(api.account_failure_details(api.TaskRequest(id=1), 'wrong'))
            self.assertEqual(401, denied.exception.status_code)
            with self.assertRaises(HTTPException) as denied:
                asyncio.run(api.account_failure_image(api.FailureImageRequest(id=2, name=JOB + '.png'), settings.token))
            self.assertEqual(404, denied.exception.status_code)
            result = asyncio.run(api.account_failure_image(api.FailureImageRequest(id=1, name=JOB + '.png'), settings.token))
            self.assertTrue(result['status'])
