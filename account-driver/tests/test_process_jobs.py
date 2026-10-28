import asyncio
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, patch

from app.jobs import CaptchaChallengeManager, JobManager


def interactive_job(action, payload, job_id):
    context = payload['_job_context']
    approved = context['authorize']('device_cleanup')
    answer = context['captcha'].wait_for_answer(job_id, b'fixture-png', 1, False, 3)
    context['progress']('mutation_submitted', 'device_cleanup')
    context['progress']('result_confirmation', 'device_cleanup', {'status': 'success'})
    return {'status': True, 'approved': approved, 'answer': answer, 'pid': os.getpid()}


def hanging_job(action, payload, job_id):
    context = payload['_job_context']
    Path(payload['pid_file']).write_text(str(os.getpid()))
    bridge = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
    Path(payload['bridge_file']).write_text(str(bridge.pid))
    context['session_opened']('fixture-grid-session')
    context['progress']('mutation_submitted', 'password_update')
    time.sleep(2)
    Path(payload['late_file']).write_text('must not happen')
    return {'status': True}


def failing_job(action, payload, job_id):
    payload['_job_context']['progress']('mutation_submitted', 'device_cleanup')
    raise RuntimeError('SECRET_MUST_NOT_ESCAPE')


def waiting_job(action, payload, job_id):
    context = payload['_job_context']
    if payload['kind'] == 'authorize':
        context['authorize']('device_cleanup')
    else:
        context['captcha'].wait_for_answer(job_id, b'fixture-png', 1, False, 20)
    return {'status': True}


def process_stopped(pid):
    try:
        # A dead descendant may briefly remain a zombie until the container init reaps it.
        return Path(f'/proc/{pid}/stat').read_text().split()[2] == 'Z'
    except FileNotFoundError:
        return True


class ProcessJobTest(unittest.IsolatedAsyncioTestCase):
    async def test_timeout_revokes_pending_authorization_and_captcha(self):
        for kind in ('authorize', 'captcha'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                captcha = CaptchaChallengeManager()
                manager = JobManager(waiting_job, 1, 60, directory, 1, .8, captcha)
                await manager.start()
                try:
                    job = await manager.submit('workflow', {'kind': kind})
                    request = None
                    for _ in range(200):
                        request = job.authorization_request if kind == 'authorize' else captcha.metadata(job.id)
                        if request: break
                        await asyncio.sleep(.005)
                    self.assertIsNotNone(request)
                    await asyncio.wait_for(manager.queue.join(), 4)
                    self.assertEqual('driver_timeout', job.result['error_code'])
                    self.assertIsNone(job.authorization_request)
                    self.assertIsNone(captcha.metadata(job.id))
                    if kind == 'authorize':
                        self.assertFalse(manager.authorize(job.id, request['challenge_id'], True))
                    else:
                        self.assertFalse(captcha.answer(job.id, request['challenge_id'], 'AB12'))
                finally:
                    await manager.stop()

    async def test_authorization_captcha_and_progress_cross_process_boundary(self):
        with tempfile.TemporaryDirectory() as directory:
            captcha = CaptchaChallengeManager()
            manager = JobManager(interactive_job, 1, 60, directory, 1, 5, captcha)
            await manager.start()
            try:
                job = await manager.submit('workflow', {})
                deadline = time.monotonic()+4
                while job.status in {'queued', 'running'} and time.monotonic()<deadline:
                    request = job.authorization_request
                    if request:
                        self.assertTrue(manager.authorize(job.id, request['challenge_id'], True))
                    challenge = captcha.metadata(job.id)
                    if challenge:
                        self.assertEqual(b'fixture-png', captcha.image(job.id, challenge['challenge_id']))
                        self.assertTrue(captcha.answer(job.id, challenge['challenge_id'], 'AB12'))
                    await asyncio.sleep(.01)
                self.assertEqual('succeeded', job.status)
                self.assertNotEqual(os.getpid(), job.result['pid'])
                self.assertTrue(job.result['approved'])
                self.assertEqual('AB12', job.result['answer'])
                self.assertEqual('confirmed', job.public()['mutation_state'])
                self.assertIsNone(captcha.metadata(job.id))
            finally:
                await manager.stop()

    async def test_timeout_kills_runner_and_bridge_before_completing_job(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = {key: str(Path(directory)/key) for key in ('pid_file','bridge_file','late_file')}
            manager = JobManager(hanging_job, 1, 60, directory, 1, .8)
            client = AsyncMock()
            with patch('httpx.AsyncClient') as factory:
                factory.return_value.__aenter__ = AsyncMock(return_value=client)
                factory.return_value.__aexit__ = AsyncMock(return_value=False)
                await manager.start()
                try:
                    job = await manager.submit('workflow', paths)
                    await asyncio.wait_for(manager.queue.join(), 5)
                    self.assertEqual('driver_timeout', job.result['error_code'])
                    self.assertTrue(process_stopped(int(Path(paths['pid_file']).read_text())))
                    self.assertTrue(process_stopped(int(Path(paths['bridge_file']).read_text())))
                    self.assertEqual('uncertain', job.result['step_results']['password_update']['status'])
                    self.assertTrue(job.result['password_change_started'])
                    self.assertFalse(Path(paths['late_file']).exists())
                    client.delete.assert_awaited_once()
                    self.assertTrue(client.delete.call_args.args[0].endswith('/session/fixture-grid-session'))
                finally:
                    await manager.stop()

    async def test_child_exception_keeps_uncertainty_without_exposing_exception(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = JobManager(failing_job, 1, 60, directory, 1, 5)
            await manager.start()
            try:
                job = await manager.submit('workflow', {})
                await asyncio.wait_for(manager.queue.join(), 5)
                self.assertTrue(job.result['device_removal_started'])
                self.assertEqual('uncertain', job.result['step_results']['device_cleanup']['status'])
                self.assertNotIn('SECRET', repr(job.public()))
            finally:
                await manager.stop()
