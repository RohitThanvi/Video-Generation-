"""Background jobs: long work (agent runs, renders) never holds an HTTP request open.

A browser request that lasts 10+ minutes dies to proxy/browser timeouts and blocks a worker.
Instead the API starts a job (202 + id) and the UI polls /jobs/{id} for live progress events.
Jobs run on a small thread pool; the LLM budget (app.llm) and render slots (app.tools) are the
real throttles, so extra jobs simply queue instead of failing.
"""
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from .config import JOB_WORKERS

MAX_EVENTS = 500
MAX_JOBS_KEPT = 200


class Job:
    def __init__(self, project_id: str, kind: str):
        self.id = uuid.uuid4().hex
        self.project_id = project_id
        self.kind = kind
        self.status = "queued"  # queued -> running -> done | error
        self.events: list[dict] = []
        self.result = None
        self.error: str | None = None
        self.error_status: int | None = None
        self.created = time.time()
        self.started: float | None = None
        self.finished: float | None = None
        self._lock = threading.Lock()

    def emit(self, message: str):
        with self._lock:
            self.events.append({"t": round(time.time() - self.created, 1), "message": str(message)[:300]})
            if len(self.events) > MAX_EVENTS:
                del self.events[: len(self.events) - MAX_EVENTS]

    @property
    def active(self) -> bool:
        return self.status in {"queued", "running"}

    def snapshot(self, since: int = 0) -> dict:
        with self._lock:
            events = self.events[since:]
            total = len(self.events)
        return {
            "id": self.id,
            "project_id": self.project_id,
            "kind": self.kind,
            "status": self.status,
            "events": events,
            "next": total if since <= total else since,
            "result": self.result,
            "error": self.error,
            "elapsed": round((self.finished or time.time()) - self.created, 1),
        }


class JobManager:
    def __init__(self, workers: int = JOB_WORKERS):
        self._pool = ThreadPoolExecutor(max_workers=max(1, workers), thread_name_prefix="job")
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def active_for_project(self, project_id: str) -> Job | None:
        with self._lock:
            return next((j for j in self._jobs.values() if j.project_id == project_id and j.active), None)

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def submit(self, project_id: str, kind: str, fn) -> Job:
        """Run fn(emit) in the background. One active job per project (raises JobConflict)."""
        with self._lock:
            if any(j.project_id == project_id and j.active for j in self._jobs.values()):
                raise JobConflict("This project already has a job running.")
            job = Job(project_id, kind)
            self._jobs[job.id] = job
            finished = [j for j in self._jobs.values() if not j.active]
            for old in sorted(finished, key=lambda j: j.created)[: max(0, len(self._jobs) - MAX_JOBS_KEPT)]:
                del self._jobs[old.id]
        self._pool.submit(self._run, job, fn)
        return job

    @staticmethod
    def _run(job: Job, fn):
        job.status = "running"
        job.started = time.time()
        try:
            job.result = fn(job.emit)
            job.status = "done"
        except Exception as exc:  # surfaced to the UI, never raised into the pool
            job.error = str(exc) or exc.__class__.__name__
            job.status = "error"
        finally:
            job.finished = time.time()


class JobConflict(RuntimeError):
    pass


manager = JobManager()
