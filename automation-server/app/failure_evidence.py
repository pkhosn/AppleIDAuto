"""Read-only, account-scoped access to existing driver PNG artifacts."""
import re
from datetime import datetime, timezone
from pathlib import Path


JOB_ID = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')
IMAGE_NAME = re.compile(r'([0-9a-f-]{36})(?:-[A-Za-z0-9_-]{1,140})?\.png')
MAX_IMAGE_BYTES = 5 * 1024 * 1024


def associated_jobs(store, account_id):
    jobs = store.driver_jobs(account_id)
    known = {job['job_id'] for job in jobs}
    # Older workflow runs already have a durable account/job association.
    for run in store.recent_runs(account_id, 10):
        job_id = run.get('driver_job_id', '')
        if job_id and job_id not in known and JOB_ID.fullmatch(job_id):
            jobs.append({'job_id': job_id, 'account_id': account_id, 'action': 'workflow',
                         'status': 'success' if run['status'] == 'success' else 'failed',
                         'stage': run.get('stage', ''), 'error_code': run.get('error_code', ''),
                         'created_at': run['queued_at'], 'updated_at': run['updated_at']})
            known.add(job_id)
    return sorted(jobs, key=lambda job: job['created_at'], reverse=True)


def image_path(directory, name):
    if not IMAGE_NAME.fullmatch(name):
        return None
    root = Path(directory).resolve()
    path = root / name
    # Refuse symlinks as well as traversal. Never expose diagnostics or secrets.
    if path.is_symlink() or path.resolve().parent != root or not path.is_file():
        return None
    if not 0 < path.stat().st_size <= MAX_IMAGE_BYTES:
        return None
    return path


def failure_details(store, account_id, directory):
    row = store.get(account_id)
    failure = store.latest_failure_log(account_id)
    jobs = associated_jobs(store, account_id)
    latest = next((job for job in jobs if job['status'] == 'failed'), None)
    images = []
    if latest and JOB_ID.fullmatch(latest['job_id']):
        for path in sorted(Path(directory).glob(latest['job_id'] + '*.png')):
            try:
                safe = image_path(directory, path.name)
                if safe is None or IMAGE_NAME.fullmatch(path.name).group(1) != latest['job_id']:
                    continue
                images.append({'name': path.name, 'stage': path.stem[len(latest['job_id']):].lstrip('-') or latest['stage'] or latest['action'],
                               'captured_at': datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()})
                if len(images) >= 20:
                    break
            except OSError:
                continue
    return {'message': failure['message'] if failure else (row['message'] if row else ''),
            'failed_at': failure['created_at'] if failure else (latest['updated_at'] if latest else (row['updated_at'] if row else '')),
            'stage': latest['stage'] if latest else '', 'error_code': latest['error_code'] if latest else '',
            'evidence_at': latest['updated_at'] if latest else '', 'images': images}


def read_account_image(store, account_id, directory, name):
    match = IMAGE_NAME.fullmatch(name)
    if not match or not any(job['job_id'] == match.group(1) for job in associated_jobs(store, account_id)):
        return None
    try:
        path = image_path(directory, name)
        if path is None:
            return None
        data = path.read_bytes()
        return data if len(data) <= MAX_IMAGE_BYTES and data.startswith(b'\x89PNG\r\n\x1a\n') else None
    except OSError:
        return None
