from pathlib import Path
import json
import mimetypes
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from .config import MAX_FILE_BYTES, RENDER_CONCURRENCY
from .project import require_project_dir, load_project, save_project
from .validation import validate_project, wav_seconds, min_scene_seconds
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

KIT_DIR = Path(__file__).resolve().parent / "kit"
KIT_FILES = ("kit.css", "kit.js")

# generate_narrations runs clips in parallel; manifest read-modify-write must not interleave.
_MANIFEST_LOCK = threading.Lock()
# Rendering is CPU/RAM heavy: more than RENDER_CONCURRENCY at once only makes everything slower.
_RENDER_SLOTS = threading.BoundedSemaphore(max(1, RENDER_CONCURRENCY))

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

def _source_target(project_id: str, relative_path: str):
    root = require_project_dir(project_id)
    p = Path(relative_path)
    # p.drive/p.root also catch Windows forms like "C:evil.js" or "\\evil.js" that
    # is_absolute() reports as relative but that replace the base when joined.
    if p.is_absolute() or p.drive or p.root or ".." in p.parts:
        raise ValueError("Path traversal is not allowed.")
    if p.suffix.lower() not in {".html", ".css", ".js", ".mjs", ".json", ".txt"}:
        raise ValueError("Unsupported source extension.")
    source_root = (root / "source").resolve()
    target = (source_root / p).resolve()
    if not target.is_relative_to(source_root):
        raise ValueError("Path traversal is not allowed.")
    return root, target

def write_source(project_id: str, relative_path: str, content: str):
    root, target = _source_target(project_id, relative_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return {"path": target.relative_to(root.resolve()).as_posix(), "chars": len(content)}

READ_SOURCE_LIMIT = 24000

def read_source(project_id: str, relative_path: str):
    """Return a source file (the agent's history only keeps a stub of what it wrote)."""
    root, target = _source_target(project_id, relative_path)
    if not target.is_file():
        raise FileNotFoundError(f"{relative_path} does not exist.")
    text = target.read_text(encoding="utf-8")
    return {
        "path": target.relative_to(root.resolve()).as_posix(),
        "content": text[:READ_SOURCE_LIMIT],
        "truncated": len(text) > READ_SOURCE_LIMIT,
    }

def patch_source(project_id: str, relative_path: str, old: str, new: str, replace_all: bool = False):
    """Replace exact text in a source file: a small fix costs a few tokens, not a full rewrite."""
    root, target = _source_target(project_id, relative_path)
    if not target.is_file():
        raise FileNotFoundError(f"{relative_path} does not exist.")
    if not old:
        raise ValueError("old must not be empty.")
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count == 0:
        raise ValueError("old text not found (it must match exactly, including whitespace).")
    if count > 1 and not replace_all:
        raise ValueError(f"old text matches {count} places; add more context or set replace_all.")
    target.write_text(text.replace(old, new) if replace_all else text.replace(old, new, 1), encoding="utf-8")
    return {"path": target.relative_to(root.resolve()).as_posix(), "replacements": count if replace_all else 1}

def install_design_kit(project_id: str):
    """Copy the design system (kit.css) and scene engine (kit.js) into source/."""
    root = require_project_dir(project_id)
    target = root / "source"
    target.mkdir(parents=True, exist_ok=True)
    for name in KIT_FILES:
        shutil.copyfile(KIT_DIR / name, target / name)
    return {
        "files": [f"source/{n}" for n in KIT_FILES],
        "html_head": '<link rel="stylesheet" href="kit.css">',
        "html_end_of_body": '<script src="kit.js"></script>',
        "note": "Use the kit classes from the system prompt. Do not re-implement the engine or the CSS.",
    }

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
    entry = {
        "filename": filename,
        "text_file": text_path.relative_to(root).as_posix(),
        "audio_path": f"narration/{filename}",
    }
    with _MANIFEST_LOCK:
        manifest = load_project(project_id)
        # Regenerating a clip replaces its entry instead of adding a duplicate.
        manifest["narration"] = [
            n for n in manifest.get("narration", []) if n.get("filename") != filename
        ] + [entry]
        save_project(manifest)
    seconds = wav_seconds(audio_path)
    info = {
        "path": f"narration/{filename}",
        "text_path": entry["text_file"],
        "stdout": result["stdout"],
    }
    if seconds is not None:
        # The storyboard scene that plays this clip must be at least this long, otherwise
        # the voice runs into the next scene's narration.
        info["narration_seconds"] = round(seconds, 2)
        info["min_scene_duration_seconds"] = min_scene_seconds(seconds)
    return info

def generate_narrations(project_id: str, clips: list):
    """Generate several narration clips in one tool call, in parallel (one LLM turn, not N)."""
    if not isinstance(clips, list) or not clips:
        raise ValueError("clips must be a non-empty list of {filename, text}.")
    if len(clips) > 20:
        raise ValueError("At most 20 clips per call.")

    def one(clip):
        try:
            if not isinstance(clip, dict):
                raise ValueError("each clip must be an object with filename and text")
            rate, volume = clip.get("rate"), clip.get("volume")
            info = generate_narration(
                project_id, clip["filename"], clip["text"],
                170 if rate is None else int(rate), 1.0 if volume is None else float(volume),
            )
            info.pop("stdout", None)
            return {"filename": clip["filename"], "ok": True, **info}
        except Exception as exc:
            name = clip.get("filename") if isinstance(clip, dict) else None
            return {"filename": name, "ok": False, "error": str(exc)}

    with ThreadPoolExecutor(max_workers=min(4, len(clips))) as pool:
        return {"clips": list(pool.map(one, clips))}

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
    with _RENDER_SLOTS:  # queue here when other renders are already using the CPU
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
    # render.py prints one JSON line (progress goes to stderr). Surface the useful parts,
    # above all the page's own console errors (missing assets, JS errors, a timeline whose
    # length differs from the storyboard), so the agent can fix them instead of guessing.
    for line in reversed((result.get("stdout") or "").strip().splitlines()):
        try:
            info = json.loads(line)
        except ValueError:
            continue
        if isinstance(info, dict) and info.get("ok"):
            for key in ("frames", "fps", "render_seconds", "browser_messages"):
                if key in info:
                    result[key] = info[key]
            break
    return result
