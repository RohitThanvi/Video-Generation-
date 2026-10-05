from pathlib import Path
import json
import mimetypes
import shutil
from .config import MAX_FILE_BYTES
from .project import require_project_dir, load_project, save_project
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

# Text saved as a "narration" asset is the script, not audio (audio comes from generate_narration).
NARRATION_TEXT_EXTENSIONS = {".txt"}

class ValidationFailed(ValueError):
    """Raised by render() when the project does not pass validation."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("Project validation failed: " + "; ".join(errors))

def _safe_name(name: str) -> str:
    name = Path(name).name
    if not name or name in {".", ".."}:
        raise ValueError("Invalid filename.")
    return name

def write_source(project_id: str, relative_path: str, content: str):
    root = require_project_dir(project_id)
    p = Path(relative_path)
    # p.drive/p.root also catch Windows forms like "C:evil.js" or "\evil.js" that
    # is_absolute() reports as relative but that replace the base when joined.
    if p.is_absolute() or p.drive or p.root or ".." in p.parts:
        raise ValueError("Path traversal is not allowed.")
    if p.suffix.lower() not in {".html", ".css", ".js", ".mjs", ".json", ".txt"}:
        raise ValueError("Unsupported source extension.")
    source_root = (root / "source").resolve()
    target = (source_root / p).resolve()
    if not target.is_relative_to(source_root):
        raise ValueError("Path traversal is not allowed.")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return {"path": target.relative_to(root.resolve()).as_posix()}

def write_storyboard(project_id: str, storyboard: dict):
    # Some models send the object as a JSON string.
    if isinstance(storyboard, str):
        try:
            storyboard = json.loads(storyboard)
        except json.JSONDecodeError as exc:
            raise ValueError(f"storyboard must be a JSON object: {exc}") from exc
    if (
        not isinstance(storyboard, dict)
        or not isinstance(storyboard.get("scenes"), list)
        or not storyboard["scenes"]
    ):
        raise ValueError("Storyboard must be an object containing a non-empty scenes list.")
    root = require_project_dir(project_id)
    (root / "storyboard.json").write_text(json.dumps(storyboard, indent=2), encoding="utf-8")
    manifest = load_project(project_id)
    manifest["scenes"] = storyboard["scenes"]
    save_project(manifest)
    return {"path": "storyboard.json"}

def save_text_asset(project_id: str, asset_type: str, filename: str, content: str):
    if asset_type == "narration":
        allowed = NARRATION_TEXT_EXTENSIONS
    elif asset_type in CATEGORY_EXTENSIONS:
        allowed = CATEGORY_EXTENSIONS[asset_type]
    else:
        raise ValueError(f"Unsupported asset type: {asset_type}")
    filename = _safe_name(filename)
    if Path(filename).suffix.lower() not in allowed:
        raise ValueError(
            f"{filename} is not an allowed {asset_type} file (allowed: {', '.join(sorted(allowed))})."
        )
    if len(content.encode("utf-8")) > MAX_FILE_BYTES:
        raise ValueError("Content exceeds MAX_FILE_BYTES.")
    root = require_project_dir(project_id)
    target = root / CATEGORY_DIRS[asset_type] / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return {"path": target.relative_to(root).as_posix()}

def ingest_host_asset(project_id: str, asset_type: str, source_path: str, filename: str | None = None):
    if asset_type not in CATEGORY_EXTENSIONS:
        raise ValueError(f"Unsupported binary asset type: {asset_type}")
    root = require_project_dir(project_id)
    source = Path(source_path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(str(source))
    name = _safe_name(filename or source.name)
    suffix = Path(name).suffix.lower()
    if suffix not in CATEGORY_EXTENSIONS[asset_type]:
        raise ValueError(f"{name} is not an allowed {asset_type} file.")
    if source.stat().st_size > MAX_FILE_BYTES:
        raise ValueError(f"{name} exceeds the {MAX_FILE_BYTES} byte file limit.")
    target = root / CATEGORY_DIRS[asset_type] / name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return {"path": target.relative_to(root).as_posix(), "mime": mimetypes.guess_type(name)[0]}

def generate_narration(project_id: str, filename: str, text: str, rate: int = 170, volume: float = 1.0):
    filename = _safe_name(filename)
    if Path(filename).suffix.lower() != ".wav":
        raise ValueError("Narration output must be .wav")
    if not text or not text.strip():
        raise ValueError("Narration text is empty.")
    rate = max(80, min(int(rate), 400))
    volume = max(0.0, min(float(volume), 1.0))
    root = require_project_dir(project_id)
    (root / "narration").mkdir(parents=True, exist_ok=True)
    text_path = root / "narration" / f"{Path(filename).stem}.txt"
    text_path.write_text(text, encoding="utf-8")
    audio_path = root / "narration" / filename
    # Remove any previous file first so a stale clip can never be mistaken for fresh output.
    audio_path.unlink(missing_ok=True)
    # The text is passed as a file, not a CLI argument: that avoids command-line length
    # limits and argparse treating text that starts with "-" as an option.
    result = run_in_sandbox(
        project_id,
        [
            "python", "/opt/renderer/tts.py",
            "--text-file", f"/workspace/narration/{text_path.name}",
            "--output", f"/workspace/narration/{filename}",
            "--rate", str(rate),
            "--volume", str(volume),
        ],
    )
    if not audio_path.exists() or audio_path.stat().st_size == 0:
        raise RuntimeError("TTS command returned but the narration file is missing or empty.")
    manifest = load_project(project_id)
    entry = {
        "filename": filename,
        "text_file": text_path.relative_to(root).as_posix(),
        "audio_path": f"narration/{filename}",
    }
    # Regenerating a clip replaces its entry instead of adding a duplicate.
    manifest["narration"] = [
        n for n in manifest.get("narration", []) if n.get("filename") != filename
    ] + [entry]
    save_project(manifest)
    return {
        "path": f"narration/{filename}",
        "text_path": entry["text_file"],
        "stdout": result["stdout"],
    }

def validate(project_id: str):
    return validate_project(project_id)

def check_render_params(width: int, height: int, fps: int):
    if not (64 <= width <= 7680 and 64 <= height <= 4320):
        raise ValueError("width and height must be between 64 and 7680x4320.")
    if width % 2 or height % 2:
        raise ValueError("width and height must be even numbers (required by H.264 yuv420p).")
    if not 1 <= fps <= 120:
        raise ValueError("fps must be between 1 and 120.")

def render(project_id: str, width: int, height: int, fps: int):
    check_render_params(width, height, fps)
    validation = validate_project(project_id, require_renderable=True)
    if not validation["valid"]:
        raise ValidationFailed(validation["errors"])
    root = require_project_dir(project_id)
    result = run_in_sandbox(
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
    video = root / "renders" / "final" / "final.mp4"
    if not video.is_file():
        raise RuntimeError("Render finished but renders/final/final.mp4 is missing.")
    result["video_url"] = f"/projects/{project_id}/video"
    result["size_bytes"] = video.stat().st_size
    return result
