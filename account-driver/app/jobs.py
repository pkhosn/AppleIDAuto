import asyncio
import multiprocessing
import os
import re
import signal
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from uuid import uuid4
from .process_worker import child_main


@dataclass
class CaptchaChallenge:
    id: str
    job_id: str
    image: bytes
    attempt: int
    previous_rejected: bool
    event: threading.Event = field(default_factory=threading.Event)
    answer: str | None = None
    error_code: str | None = None


class CaptchaChallengeManager:
    """Thread-safe bridge between a synchronous browser and the HTTP API."""

    def __init__(self):
        self._lock = threading.RLock()
        self._challenges: dict[str, CaptchaChallenge] = {}

    def wait_for_answer(self, job_id: str, image: bytes, attempt: int,
                        previous_rejected: bool, timeout: float) -> str:
        challenge = self.begin(job_id, image, attempt, previous_rejected)
        if not challenge.event.wait(timeout):
            self.finish(job_id, challenge)
            raise TimeoutError("captcha_human_timeout")
        self.finish(job_id, challenge)
        if challenge.error_code:
            raise RuntimeError(challenge.error_code)
        if challenge.answer is None:
            raise RuntimeError("captcha_human_unavailable")
        return challenge.answer

    def begin(self, job_id, image, attempt, previous_rejected):
        challenge = CaptchaChallenge(str(uuid4()), job_id, image, attempt, previous_rejected)
        with self._lock:
            old = self._challenges.pop(job_id, None)
            if old:
                old.error_code = "captcha_challenge_replaced"
                old.event.set()
            self._challenges[job_id] = challenge
        return challenge

    def finish(self, job_id, challenge):
        with self._lock:
            if self._challenges.get(job_id) is challenge:
                self._challenges.pop(job_id, None)

    def metadata(self, job_id: str) -> dict | None:
        with self._lock:
            challenge = self._challenges.get(job_id)
            if not challenge or challenge.event.is_set():
                return None
            return {
                "challenge_id": challenge.id,
                "status": "waiting",
                "attempt": challenge.attempt,
                "previous_rejected": challenge.previous_rejected,
            }

    def image(self, job_id: str, challenge_id: str) -> bytes | None:
        with self._lock:
            challenge = self._challenges.get(job_id)
            if not challenge or challenge.id != challenge_id or challenge.event.is_set():
                return None
            return challenge.image

    def answer(self, job_id: str, challenge_id: str, answer: str) -> bool:
        normalized = re.sub(r"[^A-Za-z0-9]", "", str(answer or "")).upper()
        if normalized != str(answer or "").strip().upper() or not 4 <= len(normalized) <= 8:
            raise ValueError("invalid_captcha_answer")
        with self._lock:
            challenge = self._challenges.get(job_id)
            if not challenge or challenge.id != challenge_id or challenge.event.is_set():
                return False
            challenge.answer = normalized
            challenge.event.set()
            return True

    def cancel(self, job_id: str, challenge_id: str, error_code: str) -> bool:
        allowed = {
            "telegram_not_configured", "telegram_send_failed", "telegram_polling_conflict",
            "captcha_human_timeout",
        }
        if error_code not in allowed:
            raise ValueError("invalid_captcha_cancel_reason")
        with self._lock:
            challenge = self._challenges.get(job_id)
            if not challenge or challenge.id != challenge_id or challenge.event.is_set():
                return False
            challenge.error_code = error_code
            challenge.event.set()
            return True

    def cancel_job(self, job_id: str):
        with self._lock:
            challenge = self._challenges.pop(job_id, None)
            if challenge:
                challenge.error_code = "captcha_challenge_cancelled"
                challenge.event.set()


@dataclass
class Job:
    id: str
    action: str
    status: str = "queued"
    result: dict | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    started_at: float | None = None
    heartbeat_at: float | None = None
    stage: str = "queued"
    current_feature: str = ""
    step_results: dict = field(default_factory=dict)
    mutation_state: str = 'none'
    mutation_feature: str = ''
    mutation_ever_started: bool = False
    authorization_request: dict | None = None
    authorization_event: threading.Event | None = field(default=None, repr=False)
    authorization_approved: bool | None = field(default=None, repr=False)

    def public(self) -> dict:
        data = {
            "status": True, "job_id": self.id, "job_status": self.status,
            "queued_at": self.created_at, "started_at": self.started_at,
            "heartbeat_at": self.heartbeat_at, "stage": self.stage,
            "current_feature": self.current_feature,
            "step_results": dict(self.step_results),
            "mutation_state": self.mutation_state, "mutation_feature": self.mutation_feature,
            "mutation_ever_started": self.mutation_ever_started,
        }
        if self.authorization_request:
            data["authorization_request"] = dict(self.authorization_request)
        if self.result is not None:
            data["result"] = self.result
        return data


class JobManager:
    def __init__(self, runner: Callable, concurrency: int, ttl: int, artifact_dir: str, artifact_days: int,
                 action_timeout: int = 300, captcha_challenges: CaptchaChallengeManager | None = None):
        self.runner = runner
        self.concurrency = concurrency
        self.ttl = ttl
        self.artifact_dir = Path(artifact_dir)
        self.artifact_days = artifact_days
        self.action_timeout = action_timeout
        self.jobs: dict[str, Job] = {}
        self.queue: asyncio.Queue = asyncio.Queue()
        self.tasks: list[asyncio.Task] = []
        self.captcha_challenges = captcha_challenges
        self.process_context = multiprocessing.get_context("spawn")

    async def start(self):
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        self._cleanup_artifacts()
        self.tasks = [asyncio.create_task(self._worker(slot)) for slot in range(self.concurrency)]
        self.tasks.append(asyncio.create_task(self._cleanup_loop()))

    async def stop(self):
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)

    async def submit(self, action: str, payload: dict) -> Job:
        job = Job(str(uuid4()), action)
        self.jobs[job.id] = job
        await self.queue.put((job.id, action, payload))
        return job

    def progress(self, job_id: str, stage: str, feature: str = "", result: dict | None = None):
        job = self.jobs.get(job_id)
        if not job:
            return
        if stage == 'mutation_submitted':
            job.mutation_state, job.mutation_feature = 'submitted', feature
            job.mutation_ever_started = True
        elif (stage == 'result_confirmation' and feature == job.mutation_feature
              and isinstance(result, dict) and result.get('status') == 'success'):
            job.mutation_state = 'confirmed'
        job.stage = str(stage or "running")[:80]
        job.current_feature = str(feature or "")[:80]
        job.heartbeat_at = job.updated_at = time.time()
        if feature and isinstance(result, dict):
            job.step_results[str(feature)] = dict(result)

    def request_authorization(self, job_id: str, feature: str, timeout: float = 60) -> bool:
        job = self.jobs.get(job_id)
        if not job:
            return False
        challenge_id = str(uuid4())
        event = threading.Event()
        job.authorization_event = event
        job.authorization_approved = None
        job.authorization_request = {
            "challenge_id": challenge_id, "feature": str(feature), "status": "waiting",
        }
        self.progress(job_id, "authorization", feature)
        if not event.wait(timeout):
            approved = False
        else:
            approved = job.authorization_approved is True
        job.authorization_request = None
        job.authorization_event = None
        job.authorization_approved = None
        job.heartbeat_at = job.updated_at = time.time()
        return approved

    def authorize(self, job_id: str, challenge_id: str, approved: bool) -> bool:
        job = self.jobs.get(job_id)
        request = job.authorization_request if job else None
        if (not job or not request or request.get("challenge_id") != challenge_id
                or not job.authorization_event or job.authorization_event.is_set()):
            return False
        job.authorization_approved = bool(approved)
        job.authorization_event.set()
        job.updated_at = time.time()
        return True

    def cancel_authorization(self, job_id: str):
        job = self.jobs.get(job_id)
        if job and job.authorization_event and not job.authorization_event.is_set():
            job.authorization_approved = False
            job.authorization_event.set()

    def get(self, job_id: str) -> Job | None:
        job = self.jobs.get(job_id)
        if job and time.time() - job.updated_at > self.ttl:
            self.jobs.pop(job_id, None)
            return None
        return job

    async def _worker(self, slot=0):
        while True:
            job_id, action, payload = await self.queue.get()
            job = self.jobs.get(job_id)
            if not job:
                self.queue.task_done()
                continue
            job.status, job.updated_at = "running", time.time()
            job.started_at = job.heartbeat_at = job.updated_at
            job.stage = "preflight"
            heartbeat = asyncio.create_task(self._heartbeat(job_id))
            try:
                job.result = await self._run_process(job, action, {**payload, "_process_slot": slot})
                job.status = 'succeeded' if job.result.get('status') else 'failed'
            except asyncio.CancelledError:
                job.status = 'failed'
                job.result = self._failure_result(job, action, 'driver_cancelled')
                raise
            except TimeoutError:
                job.status = 'failed'
                job.result = self._failure_result(job, action, 'driver_timeout')
            except Exception:
                job.status = 'failed'
                job.result = self._failure_result(job, action, 'driver_error')
            finally:
                heartbeat.cancel()
                await asyncio.gather(heartbeat, return_exceptions=True)
                if self.captcha_challenges:
                    self.captcha_challenges.cancel_job(job_id)
                job.stage = "completed"
                job.current_feature = ""
                job.heartbeat_at = job.updated_at = time.time()
                self.queue.task_done()

    @staticmethod
    def _failure_result(job, action, code):
        steps = dict(job.step_results)
        feature = job.mutation_feature or job.current_feature
        # If the child died before it could prove a legacy mutation did not
        # happen, preserve the conservative uncertainty contract.
        submitted = job.mutation_state == 'submitted'
        if action != 'workflow':
            feature = {'update_password': 'password_update', 'delete_devices': 'device_cleanup',
                       'complete_email_reset': 'password_update',
                       'complete_security_rollback': 'password_update'}.get(action, feature)
            submitted = bool(feature) or submitted
        if submitted and feature:
            steps[feature] = {**steps.get(feature, {}), 'status': 'uncertain',
                              'stage': job.stage, 'error_code': code}
        return {'status': False, 'state': 'normal' if steps else 'unknown',
                'error_code': code, 'message': '浏览器任务已停止，已提交的修改需核验',
                'stage': job.stage, 'step_results': steps, 'retryable': False,
                'password_submitted': bool((submitted or job.mutation_state == 'confirmed') and feature == 'password_update'),
                'password_change_started': bool((submitted or job.mutation_state == 'confirmed') and feature == 'password_update'),
                'device_removal_started': bool(submitted and feature == 'device_cleanup'),
                'workflow_mutation_started': submitted, 'workflow_mutation_feature': feature if submitted else '',
                'workflow_mutation_ever_started': job.mutation_ever_started or submitted or any(
                    key in {'profile_restore', 'device_cleanup', 'password_update'} and value.get('status') == 'success'
                    for key, value in steps.items())}

    async def _run_process(self, job, action, payload):
        parent, child = self.process_context.Pipe()
        process = self.process_context.Process(target=child_main,
            args=(self.runner, action, payload, job.id, child))
        grouped = False
        sessions = set()
        challenge = None
        challenge_request_id = authorization_request_id = None
        result = None
        try:
            process.start()
            child.close()
            deadline = time.monotonic() + self.action_timeout
            while process.is_alive() or parent.poll():
                if time.monotonic() >= deadline:
                    raise TimeoutError('driver_timeout')
                if parent.poll():
                    try:
                        event = parent.recv()
                    except EOFError:
                        break
                    kind = event.get('event')
                    if kind == 'ready':
                        grouped = True
                    elif kind == 'progress':
                        self.progress(job.id, event['stage'], event.get('feature', ''), event.get('result'))
                    elif kind == 'authorize':
                        authorization_request_id = event['request_id']
                        job.authorization_event = threading.Event()
                        job.authorization_approved = None
                        job.authorization_request = {'challenge_id': str(uuid4()),
                            'feature': event['feature'], 'status': 'waiting'}
                        self.progress(job.id, 'authorization', event['feature'])
                    elif kind == 'captcha':
                        challenge_request_id = event['request_id']
                        if self.captcha_challenges is None:
                            parent.send({'request_id': challenge_request_id, 'error': 'telegram_not_configured'})
                        else:
                            challenge = self.captcha_challenges.begin(job.id, event['image'],
                                event['attempt'], event['previous_rejected'])
                    elif kind == 'session_opened':
                        session_id = str(event.get('session_id') or '')
                        if re.fullmatch(r'[A-Za-z0-9_-]{1,128}', session_id):
                            sessions.add(session_id)
                    elif kind == 'session_closed':
                        sessions.discard(str(event.get('session_id') or ''))
                    elif kind == 'result':
                        result = event['result']
                    elif kind == 'error':
                        raise RuntimeError('driver_error')
                if job.authorization_event and job.authorization_event.is_set():
                    parent.send({'request_id': authorization_request_id, 'approved': job.authorization_approved is True})
                    job.authorization_request = job.authorization_event = None
                    job.authorization_approved = None
                if challenge and challenge.event.is_set():
                    parent.send({'request_id': challenge_request_id, 'answer': challenge.answer, 'error': challenge.error_code})
                    self.captcha_challenges.finish(job.id, challenge)
                    challenge = None
                await asyncio.sleep(.01)
            if not isinstance(result, dict) or not isinstance(result.get('status'), bool):
                raise RuntimeError('driver_result_missing')
            return result
        finally:
            self.cancel_authorization(job.id)
            job.authorization_request = job.authorization_event = None
            if self.captcha_challenges:
                self.captcha_challenges.cancel_job(job.id)
            if process.pid:
                # Kill the complete group (including authenticated proxy bridges),
                # then join before allowing another job to occupy this slot.
                for sig, grace in ((signal.SIGTERM, 1), (signal.SIGKILL, 1)):
                    try:
                        in_group = grouped
                        if not in_group and process.is_alive():
                            in_group = os.getpgid(process.pid) == process.pid
                        if in_group: os.killpg(process.pid, sig)
                        elif process.is_alive(): os.kill(process.pid, sig)
                    except ProcessLookupError:
                        pass
                    stop_at = time.monotonic() + grace
                    while process.is_alive() and time.monotonic() < stop_at:
                        await asyncio.sleep(.01)
                process.join()
                process.close()
            # A mutation marker can be queued just before the deadline. Drain
            # after termination so a lost poll can never turn a submitted write
            # into a safe-to-retry read-only failure.
            while parent.poll():
                try:
                    event = parent.recv()
                except (EOFError, OSError):
                    break
                if event.get('event') == 'progress':
                    self.progress(job.id, event['stage'], event.get('feature', ''), event.get('result'))
                elif event.get('event') == 'session_opened':
                    sid = str(event.get('session_id') or '')
                    if re.fullmatch(r'[A-Za-z0-9_-]{1,128}', sid): sessions.add(sid)
                elif event.get('event') == 'session_closed':
                    sessions.discard(str(event.get('session_id') or ''))
            parent.close()
            child.close()
            if sessions:
                import httpx
                from .config import settings
                async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
                    for session_id in sessions:
                        try:
                            await client.delete(settings.selenium_url.rstrip('/') + '/session/' + session_id)
                        except Exception:
                            pass  # Result remains uncertain; never replay the mutation.

    async def _heartbeat(self, job_id: str):
        while True:
            await asyncio.sleep(5)
            job = self.jobs.get(job_id)
            if not job or job.status != "running":
                return
            job.heartbeat_at = job.updated_at = time.time()

    async def _cleanup_loop(self):
        while True:
            await asyncio.sleep(3600)
            cutoff = time.time() - self.ttl
            self.jobs = {job_id: job for job_id, job in self.jobs.items() if job.updated_at >= cutoff}
            self._cleanup_artifacts()

    def _cleanup_artifacts(self):
        """删除超过保留期的全部产物：截图与探针/诊断 JSON 一视同仁。"""
        cutoff = time.time() - self.artifact_days * 86400
        for path in self.artifact_dir.iterdir():
            try:
                if path.is_file() and path.stat().st_mtime < cutoff:
                    path.unlink()
            except OSError:
                pass
