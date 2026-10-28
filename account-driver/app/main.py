from contextlib import asynccontextmanager
import secrets

from fastapi import FastAPI, Header, HTTPException, Response
from pydantic import BaseModel

from .process_worker import run_browser
from .config import settings
from .jobs import CaptchaChallengeManager, JobManager

captcha_challenges = CaptchaChallengeManager()
manager = JobManager(
    run_browser, settings.concurrency, settings.job_ttl,
    settings.artifact_dir, settings.artifact_days, settings.action_timeout, captcha_challenges,
)


class CaptchaAnswer(BaseModel):
    answer: str


class CaptchaCancel(BaseModel):
    error_code: str


class AuthorizationDecision(BaseModel):
    approved: bool


def authorize(authorization: str | None):
    configured = settings.token.strip()
    if len(configured) < 32 or configured == "change-me":
        raise HTTPException(status_code=503, detail="driver_token_not_configured")
    supplied = authorization.removeprefix("Bearer ") if authorization and authorization.startswith("Bearer ") else ""
    if not supplied or not secrets.compare_digest(supplied, configured):
        raise HTTPException(status_code=401, detail="invalid token")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await manager.start()
    yield
    await manager.stop()


app = FastAPI(title="Account Driver", version="1.1.0", lifespan=lifespan)


@app.get("/health")
async def health():
    return {"status": True, "workers": settings.concurrency, "queued": manager.queue.qsize(),
            "running": sum(job.status == "running" for job in manager.jobs.values())}


@app.post("/{action}", status_code=202)
async def submit(action: str, payload: dict, authorization: str | None = Header(default=None)):
    authorize(authorization)
    if action not in {"workflow", "check", "recover", "update_password", "delete_devices", "request_email_reset", "complete_email_reset", "complete_security_rollback"}:
        raise HTTPException(status_code=404, detail="unknown action")
    required = {
        "check": ("username", "password"),
        "workflow": ("username", "password", "run_id", "features"),
        "recover": ("username", "password", "dob", "q1", "a1", "q2", "a2", "q3", "a3"),
        "update_password": ("username", "password"),
        "delete_devices": ("username", "password"),
        "request_email_reset": ("username",),
        "complete_email_reset": ("reset_url", "new_password"),
        "complete_security_rollback": ("reset_url",),
    }[action]
    missing = [field for field in required if not payload.get(field)]
    if missing:
        raise HTTPException(status_code=422, detail="missing fields: " + ", ".join(missing))
    job = await manager.submit(action, payload)
    return job.public()


@app.get("/jobs/{job_id}")
async def get_job(job_id: str, authorization: str | None = Header(default=None)):
    authorize(authorization)
    job = manager.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    data = job.public()
    challenge = captcha_challenges.metadata(job_id)
    if challenge:
        data["captcha_challenge"] = challenge
    return data


@app.post("/jobs/{job_id}/authorization/{challenge_id}")
async def authorize_step(job_id: str, challenge_id: str, payload: AuthorizationDecision,
                         authorization: str | None = Header(default=None)):
    authorize(authorization)
    if not manager.authorize(job_id, challenge_id, payload.approved):
        raise HTTPException(status_code=409, detail="authorization_request_expired")
    return {"status": True}


@app.get("/jobs/{job_id}/captcha/{challenge_id}/image")
async def captcha_image(job_id: str, challenge_id: str, authorization: str | None = Header(default=None)):
    authorize(authorization)
    image = captcha_challenges.image(job_id, challenge_id)
    if image is None:
        raise HTTPException(status_code=409, detail="captcha_challenge_expired")
    return Response(content=image, media_type="image/png")


@app.post("/jobs/{job_id}/captcha/{challenge_id}/answer")
async def captcha_answer(job_id: str, challenge_id: str, payload: CaptchaAnswer,
                         authorization: str | None = Header(default=None)):
    authorize(authorization)
    try:
        accepted = captcha_challenges.answer(job_id, challenge_id, payload.answer)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not accepted:
        raise HTTPException(status_code=409, detail="captcha_challenge_expired")
    return {"status": True}


@app.post("/jobs/{job_id}/captcha/{challenge_id}/cancel")
async def captcha_cancel(job_id: str, challenge_id: str, payload: CaptchaCancel,
                         authorization: str | None = Header(default=None)):
    authorize(authorization)
    try:
        accepted = captcha_challenges.cancel(job_id, challenge_id, payload.error_code)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not accepted:
        raise HTTPException(status_code=409, detail="captcha_challenge_expired")
    return {"status": True}
