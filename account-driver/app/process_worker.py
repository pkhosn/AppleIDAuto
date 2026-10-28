"""Private, credential-safe IPC for one disposable browser process per job."""
import os
import time
from uuid import uuid4


def receive_reply(connection, request_id, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not connection.poll(max(0, deadline-time.monotonic())):
            break
        response = connection.recv()
        if response.get('request_id') == request_id:
            return response
    return None


class RemoteCaptcha:
    def __init__(self, connection):
        self.connection = connection

    def wait_for_answer(self, job_id, image, attempt, previous_rejected, timeout):
        request_id = uuid4().hex
        self.connection.send({"event": "captcha", "request_id": request_id, "image": image, "attempt": attempt,
                              "previous_rejected": previous_rejected, "timeout": timeout})
        response = receive_reply(self.connection, request_id, timeout)
        if response is None:
            raise TimeoutError("captcha_human_timeout")
        if response.get("error"):
            raise RuntimeError(response["error"])
        return response["answer"]


def run_browser(action, payload, job_id):
    # Import OCR/Selenium dependencies only inside the isolated process.
    from queue import Queue
    from . import browser
    from .config import settings
    # Each worker owns a distinct bridge port across disposable processes.
    browser._BRIDGE_PORTS = Queue()
    browser._BRIDGE_PORTS.put(settings.proxy_bridge_port_start + int(payload.pop('_process_slot')))
    return browser.AppleFlow(payload["_job_context"]["captcha"]).run(action, payload, job_id)


def child_main(runner, action, payload, job_id, connection):
    os.setsid()  # Includes pproxy descendants in the job's process group.
    connection.send({"event": "ready"})

    def progress(stage, feature="", result=None):
        connection.send({"event": "progress", "stage": stage, "feature": feature, "result": result})

    def authorize(feature):
        request_id = uuid4().hex
        connection.send({"event": "authorize", "feature": feature, "request_id": request_id})
        response = receive_reply(connection, request_id, 60)
        return bool(response and response.get("approved"))

    def session_opened(session_id):
        connection.send({"event": "session_opened", "session_id": session_id})

    def session_closed(session_id):
        connection.send({"event": "session_closed", "session_id": session_id})

    context = {"progress": progress, "authorize": authorize, "captcha": RemoteCaptcha(connection),
               "session_opened": session_opened, "session_closed": session_closed,
               "mutation_started": False, "mutation_feature": "", "mutation_ever_started": False}

    def tracked_progress(stage, feature="", result=None):
        if stage == "mutation_submitted":
            context.update(mutation_started=True, mutation_feature=feature, mutation_ever_started=True)
        elif (stage == "result_confirmation" and context["mutation_feature"] == feature
              and isinstance(result, dict) and result.get("status") == "success"):
            context.update(mutation_started=False, mutation_feature="")
        progress(stage, feature, result)

    context["progress"] = tracked_progress
    payload = {**payload, "_job_context": context}
    try:
        result = runner(action, payload, job_id)
        connection.send({"event": "result", "result": result})
    except BaseException:
        # Neither exceptions nor their arguments may leak credentials into IPC logs.
        connection.send({"event": "error"})
    finally:
        connection.close()
