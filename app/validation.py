from pathlib import Path
import json
import mimetypes
from .project import project_dir, load_project
from .config import MAX_PROJECT_BYTES, MAX_FILE_BYTES

ALLOWED = {
    "images": {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"},
    "video": {".mp4", ".webm", ".mov"},
    "audio": {".wav", ".mp3", ".ogg", ".m4a"},
    "fonts": {".ttf", ".otf", ".woff", ".woff2"},
    "models3d": {".glb", ".gltf", ".obj", ".fbx"},
    "data": {".json", ".csv", ".txt"},
}

SOURCE_ALLOWED = {".html", ".css", ".js", ".mjs", ".json", ".txt"}

def validate_project(project_id: str) -> dict:
    root = project_dir(project_id)
    manifest = load_project(project_id)
    errors = []
    warnings = []

    total = 0
    for p in root.rglob("*"):
        if p.is_file():
            size = p.stat().st_size
            total += size
            if size > MAX_FILE_BYTES:
                errors.append(f"File exceeds MAX_FILE_BYTES: {p.relative_to(root)}")

    if total > MAX_PROJECT_BYTES:
        errors.append("Project exceeds MAX_PROJECT_BYTES.")

    for category, extensions in ALLOWED.items():
        category_root = root / "assets" / category
        for p in category_root.rglob("*"):
            if p.is_file() and p.suffix.lower() not in extensions:
                errors.append(
                    f"Invalid {category} asset extension: {p.relative_to(root)}"
                )

    for p in (root / "source").rglob("*"):
        if p.is_file() and p.suffix.lower() not in SOURCE_ALLOWED:
            errors.append(f"Unsupported source file: {p.relative_to(root)}")

    index = root / "source" / "index.html"
    if not index.exists():
        warnings.append("source/index.html does not exist yet.")

    storyboard = root / "storyboard.json"
    if storyboard.exists():
        try:
            data = json.loads(storyboard.read_text(encoding="utf-8"))
            scenes = data.get("scenes", [])
            for i, scene in enumerate(scenes):
                if not scene.get("id"):
                    errors.append(f"Storyboard scene {i} has no id.")
                duration = scene.get("duration_seconds")
                if not isinstance(duration, (int, float)) or duration <= 0:
                    errors.append(f"Storyboard scene {i} has invalid duration_seconds.")
        except json.JSONDecodeError as exc:
            errors.append(f"Invalid storyboard.json: {exc}")

    return {
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "total_bytes": total,
        "file_count": sum(1 for p in root.rglob("*") if p.is_file()),
    }
