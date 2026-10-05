from pathlib import Path
import json
import re
import uuid
from datetime import datetime, timezone
from .config import PROJECT_ROOT

# Project ids are always uuid4().hex. Anything else (".." etc.) is rejected so a crafted
# id can never point outside PROJECT_ROOT.
_PROJECT_ID_RE = re.compile(r"[0-9a-f]{32}")

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
    if not isinstance(project_id, str) or not _PROJECT_ID_RE.fullmatch(project_id):
        raise FileNotFoundError(project_id)
    return PROJECT_ROOT / project_id

def require_project_dir(project_id: str) -> Path:
    """Like project_dir, but also requires the project to actually exist.

    Write paths must use this so a bogus id can't create stray directories.
    """
    root = project_dir(project_id)
    if not (root / "project.json").is_file():
        raise FileNotFoundError(project_id)
    return root

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
    return [p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()]
