from pathlib import Path
import mimetypes
import shutil
from .project import project_dir, load_project, save_project
from .validation import validate_project
from .docker_runner import run_in_sandbox

CATEGORY_DIRS = {
    "image": "assets/images",
    "video": "assets/video",
    "audio": "assets/audio",
    "font": "assets/fonts",
    "model3d": "assets/models3d",
    "data": "assets/data",
    "narration": "narration",
    "source": "source",
}

CATEGORY_EXTENSIONS = {
    "image": {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"},
    "video": {".mp4", ".webm", ".mov"},
    "audio": {".wav", ".mp3", ".ogg", ".m4a"},
    "font": {".ttf", ".otf", ".woff", ".woff2"},
    "model3d": {".glb", ".gltf", ".obj", ".fbx"},
    "data": {".json", ".csv", ".txt"},
}

def _safe_name(name: str) -> str:
    name = Path(name).name
    if not name or name in {".", ".."}:
        raise ValueError("Invalid filename.")
    return name

def write_source(project_id: str, relative_path: str, content: str):
    root = project_dir(project_id)
    p = Path(relative_path)
    if p.is_absolute() or ".." in p.parts:
        raise ValueError("Path traversal is not allowed.")
    if p.suffix.lower() not in {".html", ".css", ".js", ".mjs", ".json", ".txt"}:
        raise ValueError("Unsupported source extension.")
    target = root / "source" / p
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return {"path": str(target.relative_to(root))}

def write_storyboard(project_id: str, storyboard: dict):
    if not isinstance(storyboard.get("scenes"), list) or not storyboard["scenes"]:
        raise ValueError("Storyboard must contain a non-empty scenes list.")
    root = project_dir(project_id)
    (root / "storyboard.json").write_text(
        __import__("json").dumps(storyboard, indent=2), encoding="utf-8"
    )
    manifest = load_project(project_id)
    manifest["scenes"] = storyboard["scenes"]
    save_project(manifest)
    return {"path": "storyboard.json"}

def save_text_asset(project_id: str, asset_type: str, filename: str, content: str):
    if asset_type not in CATEGORY_DIRS:
        raise ValueError(f"Unsupported asset type: {asset_type}")
    filename = _safe_name(filename)
    root = project_dir(project_id)
    target = root / CATEGORY_DIRS[asset_type] / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return {"path": str(target.relative_to(root))}

def ingest_host_asset(project_id: str, asset_type: str, source_path: str, filename: str | None = None):
    if asset_type not in CATEGORY_EXTENSIONS:
        raise ValueError(f"Unsupported binary asset type: {asset_type}")
    source = Path(source_path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(str(source))
    name = _safe_name(filename or source.name)
    suffix = Path(name).suffix.lower()
    if suffix not in CATEGORY_EXTENSIONS[asset_type]:
        raise ValueError(f"{name} is not an allowed {asset_type} file.")
    root = project_dir(project_id)
    target = root / CATEGORY_DIRS[asset_type] / name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return {"path": str(target.relative_to(root)), "mime": mimetypes.guess_type(name)[0]}

def generate_narration(project_id: str, filename: str, text: str, rate: int = 170, volume: float = 1.0):
    filename = _safe_name(filename)
    if Path(filename).suffix.lower() != ".wav":
        raise ValueError("Narration output must be .wav")
    root = project_dir(project_id)
    text_path = root / "narration" / f"{Path(filename).stem}.txt"
    text_path.write_text(text, encoding="utf-8")
    result = run_in_sandbox(
        project_id,
        [
            "python", "/opt/renderer/tts.py",
            "--text", text,
            "--output", f"/workspace/narration/{filename}",
            "--rate", str(rate),
            "--volume", str(volume),
        ],
    )
    if not (root / "narration" / filename).exists():
        raise RuntimeError("TTS command returned but the narration file is missing.")
    manifest = load_project(project_id)
    manifest["narration"].append({
        "filename": filename,
        "text_file": str(text_path.relative_to(root)),
        "audio_path": f"narration/{filename}",
    })
    save_project(manifest)
    return {
        "path": f"narration/{filename}",
        "text_path": str(text_path.relative_to(root)),
        "stdout": result["stdout"],
    }

def validate(project_id: str):
    return validate_project(project_id)

def render(project_id: str, width: int, height: int, fps: int):
    validation = validate_project(project_id)
    if not validation["valid"]:
        raise ValueError(__import__("json").dumps(validation, indent=2))
    return run_in_sandbox(
        project_id,
        [
            "python", "/opt/renderer/render.py",
            "--project", "/workspace",
            "--width", str(width),
            "--height", str(height),
            "--fps", str(fps),
        ],
        output_name="renders/final/final.mp4",
    )
