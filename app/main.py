from pathlib import Path

from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .models import ProjectCreate, AgentRequest, RenderRequest
from .project import create_project, load_project, project_dir
from .config import PROJECT_ROOT
from .agent import run_agent
from .tools import validate, render, ingest_host_asset

app = FastAPI(title="AI Video Compiler", version="0.2.0")
STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

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
def agent(project_id: str, req: AgentRequest):
    try:
        return run_agent(project_id, req.instruction)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

@app.post("/projects/{project_id}/validate")
def validate_endpoint(project_id: str):
    try:
        return validate(project_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

@app.post("/projects/{project_id}/render")
def render_endpoint(project_id: str, req: RenderRequest):
    try:
        return render(project_id, req.width, req.height, req.fps)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

@app.get("/projects/{project_id}/video")
def project_video(project_id: str):
    path = project_dir(project_id) / "renders" / "final" / "final.mp4"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Rendered video not found.")
    return FileResponse(path, media_type="video/mp4", filename="final.mp4")

@app.post("/projects/{project_id}/assets")
async def upload_asset(
    project_id: str,
    asset_type: str = Form(...),
    file: UploadFile = File(...),
):
    suffix = Path(file.filename or "").suffix.lower()
    allowed = {
        "image": {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"},
        "video": {".mp4", ".webm", ".mov"},
        "audio": {".wav", ".mp3", ".ogg", ".m4a"},
        "font": {".ttf", ".otf", ".woff", ".woff2"},
        "model3d": {".glb", ".gltf", ".obj", ".fbx"},
        "data": {".json", ".csv", ".txt"},
    }
    if asset_type not in allowed or suffix not in allowed[asset_type]:
        raise HTTPException(status_code=400, detail="Unsupported asset type or extension.")

    import tempfile
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        temp_path = Path(tmp.name)
        total = 0
        while chunk := await file.read(1024 * 1024):
            total += len(chunk)
            tmp.write(chunk)
    try:
        return ingest_host_asset(project_id, asset_type, str(temp_path), file.filename)
    finally:
        temp_path.unlink(missing_ok=True)
