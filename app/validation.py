from pathlib import Path
import json
import math
import re
import wave
from .project import require_project_dir
from .config import MAX_PROJECT_BYTES, MAX_FILE_BYTES

ALLOWED = {
    "images": {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"},
    "video": {".mp4", ".webm", ".mov"},
    "audio": {".wav", ".mp3", ".ogg", ".m4a"},
    "fonts": {".ttf", ".otf", ".woff", ".woff2"},
    "models3d": {".glb", ".gltf", ".obj", ".fbx"},
    "data": {".json", ".csv", ".txt"},
}

# Narration starts this long after its scene starts (see sandbox/renderer/render.py) and the
# scene needs a short hold after the last word before the cross-fade into the next scene.
NARRATION_LEAD_SECONDS = 0.6
NARRATION_TAIL_SECONDS = 0.8


def wav_seconds(path: Path) -> float | None:
    try:
        with wave.open(str(path), "rb") as w:
            return w.getnframes() / float(w.getframerate())
    except (wave.Error, EOFError, OSError, ZeroDivisionError):
        return None


def min_scene_seconds(narration_seconds: float, offset: float = NARRATION_LEAD_SECONDS) -> float:
    """Shortest scene (rounded up to 0.5s) that fits its narration with lead-in and hold."""
    return math.ceil((offset + narration_seconds + NARRATION_TAIL_SECONDS) * 2) / 2


SOURCE_ALLOWED = {".html", ".css", ".js", ".mjs", ".json", ".txt"}

def validate_project(project_id: str, require_renderable: bool = False) -> dict:
    """Check a project's files and storyboard.

    With require_renderable=True (used right before rendering) a missing
    source/index.html or storyboard.json is an error instead of a warning, so the
    problem is reported before a Docker container is started.
    """
    root = require_project_dir(project_id)
    errors = []
    warnings = []

    renders_root = root / "renders"
    total = 0
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        # renders/ holds engine output (final.mp4 can legitimately be large); the size
        # limits are for authored/uploaded input, otherwise one big render would make
        # every later validation - and therefore every later render - fail.
        if renders_root in p.parents:
            continue
        size = p.stat().st_size
        total += size
        if size > MAX_FILE_BYTES:
            errors.append(f"File exceeds MAX_FILE_BYTES: {p.relative_to(root).as_posix()}")

    if total > MAX_PROJECT_BYTES:
        errors.append("Project exceeds MAX_PROJECT_BYTES.")

    for category, extensions in ALLOWED.items():
        category_root = root / "assets" / category
        for p in category_root.rglob("*"):
            if p.is_file() and p.suffix.lower() not in extensions:
                errors.append(
                    f"Invalid {category} asset extension: {p.relative_to(root).as_posix()}"
                )

    for p in (root / "source").rglob("*"):
        if p.is_file() and p.suffix.lower() not in SOURCE_ALLOWED:
            errors.append(f"Unsupported source file: {p.relative_to(root).as_posix()}")

    index = root / "source" / "index.html"
    if not index.exists():
        if require_renderable:
            errors.append("source/index.html is required to render.")
        else:
            warnings.append("source/index.html does not exist yet.")

    story_scenes: list = []
    storyboard = root / "storyboard.json"
    if not storyboard.exists():
        if require_renderable:
            errors.append("storyboard.json is required to render. Create it with write_storyboard.")
        else:
            warnings.append("storyboard.json does not exist yet.")
    else:
        try:
            data = json.loads(storyboard.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            errors.append(f"Invalid storyboard.json: {exc}")
        else:
            scenes = data.get("scenes") if isinstance(data, dict) else None
            if not isinstance(scenes, list) or not scenes:
                errors.append("storyboard.json must be an object with a non-empty 'scenes' list.")
            else:
                for i, scene in enumerate(scenes):
                    if not isinstance(scene, dict):
                        errors.append(f"Storyboard scene {i} must be an object.")
                        continue
                    if not scene.get("id"):
                        errors.append(f"Storyboard scene {i} has no id.")
                    duration = scene.get("duration_seconds")
                    # bool is a subclass of int; True would otherwise pass as 1 second.
                    if isinstance(duration, bool) or not isinstance(duration, (int, float)) or duration <= 0:
                        errors.append(f"Storyboard scene {i} has invalid duration_seconds.")
                    narration = scene.get("narration")
                    if narration:
                        clip = root / "narration" / Path(str(narration)).name
                        if not clip.is_file():
                            errors.append(
                                f"Storyboard scene {i} references missing narration file: {narration}"
                            )
                        elif isinstance(duration, (int, float)) and not isinstance(duration, bool):
                            seconds = wav_seconds(clip)
                            offset = scene.get("narration_offset", NARRATION_LEAD_SECONDS)
                            if seconds is not None and isinstance(offset, (int, float)):
                                need = min_scene_seconds(seconds, offset)
                                if duration + 1e-6 < need:
                                    errors.append(
                                        f"Scene '{scene.get('id')}': narration {Path(str(narration)).name} is "
                                        f"{seconds:.1f}s long but the scene is only {duration}s. Set duration_seconds "
                                        f"to at least {need} so the voice finishes before the transition."
                                    )

                story_scenes = scenes

    _check_scene_markup(root, story_scenes, errors, warnings)

    return {
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "total_bytes": total,
        "file_count": sum(1 for p in root.rglob("*") if p.is_file()),
    }


def _check_scene_markup(root: Path, scenes: list, errors: list, warnings: list) -> None:
    """Make sure index.html uses the design kit and has one data-scene section per storyboard scene."""
    index = root / "source" / "index.html"
    if not index.is_file():
        return
    html = index.read_text(encoding="utf-8", errors="replace")
    if "kit.js" not in html or "kit.css" not in html:
        warnings.append(
            "index.html does not load kit.css/kit.js. Call install_design_kit and link both files "
            "to get the design system, animations and scene transitions."
        )
        return
    story_ids = [str(s.get("id")) for s in scenes if isinstance(s, dict) and s.get("id")]
    if not story_ids:
        return
    html_ids = re.findall(r"""data-scene\s*=\s*["']([^"']+)["']""", html)
    missing = [i for i in story_ids if i not in html_ids]
    extra = [i for i in html_ids if i not in story_ids]
    if missing:
        errors.append(
            "index.html has no <section class=\"scene\" data-scene=\"ID\"> for storyboard scene(s): "
            + ", ".join(missing)
        )
    if extra:
        errors.append("index.html has data-scene ids that are not in the storyboard: " + ", ".join(extra))
