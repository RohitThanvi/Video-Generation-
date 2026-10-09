from pathlib import Path
import tempfile

from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .models import ProjectCreate, AgentRequest, RenderRequest, JobRequest
from .project import create_project, load_project, project_dir, require_project_dir
from .config import PROJECT_ROOT, MAX_FILE_BYTES
from .agent import run_agent
from . import jobs
from .tools import validate, render, ingest_host_asset, ValidationFailed, CATEGORY_EXTENSIONS

app = FastAPI(title="AI Video Compiler", version="0.2.0")
STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

def _http_error(exc: Exception) -> HTTPException:
    """Map domain errors to meaningful status codes instead of a blanket 500."""
    if isinstance(exc, FileNotFoundError):
        return HTTPException(status_code=404, detail="Project not found.")
    if isinstance(exc, ValidationFailed):
        return HTTPException(status_code=422, detail=str(exc))
    if isinstance(exc, ValueError):
        return HTTPException(status_code=400, detail=str(exc))
    return HTTPException(status_code=500, detail=str(exc))

@app.get("/")
def frontend():
    return FileResponse(STATIC_DIR / "index.html")

@app.get("/health")
def health():
    return {"ok": True}

@app.get("/projects")
def list_projects():
    projects = []
    PROJECT_ROOT.mkdir(parents=True, exist_ok=True)
    for path in PROJECT_ROOT.iterdir():
        if path.is_dir() and (path / "project.json").exists():
            try:
                projects.append(load_project(path.name))
            except Exception:
                continue
    projects.sort(key=lambda p: p.get("updated_at", ""), reverse=True)
    return projects

@app.post("/projects")
def create(req: ProjectCreate):
    return create_project(req.prompt)

@app.get("/projects/{project_id}")
def get_project(project_id: str):
    try:
        return load_project(project_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Project not found.")

@app.post("/projects/{project_id}/agent")
def agent(project_id: str, req: AgentRequest | None = None):
    try:
        return run_agent(project_id, req.instruction if req else None)
    except Exception as exc:
        raise _http_error(exc)

@app.post("/projects/{project_id}/jobs", status_code=202)
def start_job(project_id: str, req: JobRequest):
    """Start a long-running agent/render job in the background; poll GET /jobs/{id}."""
    try:
        require_project_dir(project_id)
        if req.kind == "agent":
            fn = lambda emit: run_agent(project_id, req.instruction, on_event=emit)
        else:
            def fn(emit):
                emit(f"Rendering {req.width}x{req.height} at {req.fps} fps (frame by frame, this takes a while)…")
                return render(project_id, req.width, req.height, req.fps)
        job = jobs.manager.submit(project_id, req.kind, fn)
    except jobs.JobConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except Exception as exc:
        raise _http_error(exc)
    return job.snapshot()

@app.get("/jobs/{job_id}")
def get_job(job_id: str, since: int = 0):
    job = jobs.manager.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    return job.snapshot(max(0, since))

@app.get("/projects/{project_id}/job")
def active_job(project_id: str):
    """The project's running job, if any (lets the UI re-attach after a page reload)."""
    job = jobs.manager.active_for_project(project_id)
    return job.snapshot() if job else {"status": "idle"}

@app.post("/projects/{project_id}/validate")
def validate_endpoint(project_id: str):
    try:
        return validate(project_id)
    except Exception as exc:
        raise _http_error(exc)

@app.post("/projects/{project_id}/render")
def render_endpoint(project_id: str, req: RenderRequest | None = None):
    req = req or RenderRequest()
    try:
        return render(project_id, req.width, req.height, req.fps)
    except Exception as exc:
        raise _http_error(exc)

@app.get("/projects/{project_id}/video")
def project_video(project_id: str):
    try:
        path = project_dir(project_id) / "renders" / "final" / "final.mp4"
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Project not found.")
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Rendered video not found.")
    # no-store: a re-render replaces the file at the same URL.
    return FileResponse(
        path,
        media_type="video/mp4",
        filename="final.mp4",
        headers={"Cache-Control": "no-store"},
    )

@app.post("/projects/{project_id}/assets")
async def upload_asset(
    project_id: str,
    asset_type: str = Form(...),
    file: UploadFile = File(...),
):
    # Check the project first: otherwise a bogus id would create stray directories.
    try:
        load_project(project_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Project not found.")

    suffix = Path(file.filename or "").suffix.lower()
    if asset_type not in CATEGORY_EXTENSIONS or suffix not in CATEGORY_EXTENSIONS[asset_type]:
        raise HTTPException(status_code=400, detail="Unsupported asset type or extension.")

    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            temp_path = Path(tmp.name)
            total = 0
            while chunk := await file.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_FILE_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=f"File exceeds the {MAX_FILE_BYTES // (1024 * 1024)} MB limit.",
                    )
                tmp.write(chunk)
        try:
            return ingest_host_asset(project_id, asset_type, str(temp_path), file.filename)
        except Exception as exc:
            raise _http_error(exc)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
