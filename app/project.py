from pathlib import Path
import json
import uuid
from datetime import datetime, timezone
from .config import PROJECT_ROOT

DIRS = (
    "source",
    "source/scenes",
    "assets/images",
    "assets/video",
    "assets/audio",
    "assets/fonts",
    "assets/models3d",
    "assets/data",
    "narration",
    "renders/preview",
    "renders/final",
)

def now():
    return datetime.now(timezone.utc).isoformat()

def project_dir(project_id: str) -> Path:
    return PROJECT_ROOT / project_id

def create_project(prompt: str) -> dict:
    project_id = uuid.uuid4().hex
    root = project_dir(project_id)
    for d in DIRS:
        (root / d).mkdir(parents=True, exist_ok=True)
    manifest = {
        "id": project_id,
        "prompt": prompt,
        "created_at": now(),
        "updated_at": now(),
        "version": 1,
        "settings": {
            "width": 1920,
            "height": 1080,
            "fps": 30,
        },
        "assets": {
            "images": [],
            "video": [],
            "audio": [],
            "fonts": [],
            "models3d": [],
            "data": [],
        },
        "narration": [],
        "scenes": [],
        "renders": [],
    }
    (root / "project.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest

def load_project(project_id: str) -> dict:
    path = project_dir(project_id) / "project.json"
    if not path.exists():
        raise FileNotFoundError(project_id)
    return json.loads(path.read_text(encoding="utf-8"))

def save_project(manifest: dict):
    manifest["updated_at"] = now()
    path = project_dir(manifest["id"]) / "project.json"
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

def list_files(project_id: str):
    root = project_dir(project_id)
    return [str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()]
