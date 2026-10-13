"""Video engine pipeline: scene spec in, renderable project out.

One call replaces what used to take the model ~15 tool turns (install kit, search/download photos,
write every narration, TTS, storyboard, hand-written HTML):

  spec -> normalize -> fetch photos -> write narration (small model) -> TTS -> storyboard -> page

The strong model supplies only the creative decisions; the mechanics are software. Everything the
engine does can be overridden: a scene may use layout "custom" with its own HTML, and the page can
still be hand-edited afterwards with patch_source.
"""
import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import scenes as sc_mod
from .project import require_project_dir
from .validation import min_scene_seconds, wav_seconds

SPEC_PATH = "source/spec.json"


def _clip(n, lo, hi):
    return max(lo, min(hi, n))


def target_words(duration: float) -> int:
    return int(_clip(round((duration - 1.5) * 2.6), 14, 70))


def wants_voice(sc: dict) -> bool:
    """Character/animation (stage) scenes are silent unless the model gave them something to say."""
    return sc["layout"] != "stage" or bool(sc["say"] or sc["narration"])


def fallback_narration(sc: dict) -> str:
    """Deterministic spoken text when no model is available: the scene's own words."""
    text = sc["narration"] or sc["say"] or ""
    if not text and wants_voice(sc):
        bits = [sc["headline"], sc["body"]] + [i["title"] for i in sc.get("items", [])]
        text = ". ".join(b.rstrip(".") for b in bits if b)
    return " ".join(text.split())[:600]


_WRITER_SYSTEM = (
    "You write the spoken narration for a short explainer video, one passage per scene. "
    "Reply with ONLY a JSON object mapping each scene id to its narration text. "
    "Natural, vivid spoken sentences (no markdown, no emoji, no stage directions, no scene numbers), "
    "each close to the requested word count, and cover that scene's talking points in order."
)


def make_narration_writer(client, usage=None, emit=None):
    """A writer(items)->{id: text} backed by the small model (see llm.ask_small)."""
    from . import llm

    def writer(items: list[dict]) -> dict:
        brief = json.dumps(
            [{"id": i["id"], "headline": i["headline"], "points": i["say"] or i["body"], "words": i["words"]} for i in items],
            ensure_ascii=False,
        )
        raw = llm.ask_small(client, _WRITER_SYSTEM, f"Topic: {items[0].get('topic', '')}\nScenes:\n{brief}", usage, on_wait=emit,
                            max_tokens=min(3500, 120 + sum(i["words"] for i in items) * 3))
        m = re.search(r"\{.*\}", raw, re.S)
        data = json.loads(m.group(0)) if m else {}
        return {k: " ".join(str(v).split()) for k, v in data.items() if isinstance(v, str) and v.strip()}

    return writer


def _fetch_photo(project_id: str, sc: dict, emit):
    """Download the first usable openly licensed photo for the scene's visual. None on any failure."""
    from . import web_assets

    v = sc["visual"]
    root = require_project_dir(project_id)
    if v.get("file") and (root / "assets" / "images" / Path(v["file"]).name).is_file():
        return Path(v["file"]).name
    try:
        results = web_assets.search_free_images(v["query"], 6)
    except Exception as exc:  # offline, disabled or rate-limited: the scene falls back to a blob
        emit(f"No photo for '{v['query'][:40]}': {str(exc)[:80]}")
        return None
    for r in results[:4]:
        try:
            info = web_assets.download_asset(project_id, "image", r["url"], f"{sc['id']}.jpg")
            credit = {"file": Path(info["path"]).name, "title": r.get("title"), "creator": r.get("creator"),
                      "license": r.get("license"), "source": r.get("source_page")}
            credits = root / "assets" / "data" / "credits.json"
            credits.parent.mkdir(parents=True, exist_ok=True)
            prev = json.loads(credits.read_text()) if credits.is_file() else []
            credits.write_text(json.dumps([c for c in prev if c["file"] != credit["file"]] + [credit], indent=1))
            return Path(info["path"]).name
        except Exception:
            continue
    emit(f"No downloadable photo for '{v['query'][:40]}'")
    return None


def build_video(project_id: str, spec, write_narration=None, emit=None, narrate: bool = True) -> dict:
    """Compile `spec` into storyboard.json, narration, source/index.html. Returns a short summary."""
    from . import tools

    emit = emit or (lambda m: None)
    spec = sc_mod.normalize_spec(spec)
    root = require_project_dir(project_id)
    scenes = spec["scenes"]
    tools.install_design_kit(project_id)

    # 1. Photos, in parallel.
    photo_scenes = [s for s in scenes if s["visual"]["type"] == "photo"]
    if photo_scenes:
        with ThreadPoolExecutor(max_workers=min(4, len(photo_scenes))) as pool:
            for s, name in zip(photo_scenes, pool.map(lambda s: _fetch_photo(project_id, s, emit), photo_scenes)):
                s["visual"]["file"] = name or ""

    def image_for(v):
        name = Path(v.get("file") or "").name
        return name if name and (root / "assets" / "images" / name).is_file() else None

    # 2. Narration text: given by the scene, else written by the small model, else the scene's own words.
    notes = []
    seconds: dict[str, float] = {}
    if narrate:
        topic = spec["title"]
        need = [s for s in scenes if not s["narration"] and wants_voice(s)]
        if need and write_narration:
            try:
                written = write_narration([dict(id=s["id"], headline=s["headline"], say=s["say"], body=s["body"],
                                               words=target_words(s["duration"]), topic=topic) for s in need])
                for s in need:
                    s["narration"] = written.get(s["id"], "")
            except Exception as exc:  # the small model being down must not stop the video
                notes.append(f"narration writer failed ({str(exc)[:80]}); used the scene text")
                emit(notes[-1])
        for s in scenes:
            if not s["narration"]:
                s["narration"] = fallback_narration(s)

        # 3. TTS, reusing clips whose text did not change (edits re-voice only the scenes they touch).
        clips = []
        for s in scenes:
            if not s["narration"]:
                continue
            txt, wav = root / "narration" / f"{s['id']}.txt", root / "narration" / f"{s['id']}.wav"
            if wav.is_file() and txt.is_file() and txt.read_text(encoding="utf-8") == s["narration"]:
                seconds[s["id"]] = wav_seconds(wav) or 0
            else:
                clips.append({"filename": f"{s['id']}.wav", "text": s["narration"]})
        if clips:
            emit(f"Generating {len(clips)} narration clip(s)…")
            try:
                for r in tools.generate_narrations(project_id, clips)["clips"]:
                    if r.get("ok") and r.get("narration_seconds"):
                        seconds[r["filename"][:-4]] = r["narration_seconds"]
                    else:
                        notes.append(f"no audio for {r.get('filename')}: {str(r.get('error'))[:80]}")
            except Exception as exc:
                notes.append(f"narration failed: {str(exc)[:100]}")
                emit(notes[-1])

    # 4. Storyboard: each scene as long as its narration needs.
    board = []
    for s in scenes:
        entry = {"id": s["id"], "title": s["headline"] or s["id"]}
        dur = s["duration"]
        if s["id"] in seconds:
            dur = max(dur, min_scene_seconds(seconds[s["id"]]))
            entry["narration"] = f"{s['id']}.wav"
        entry["duration_seconds"] = dur
        board.append(entry)
    tools.write_storyboard(project_id, {"scenes": board})

    # 5. Page + the spec itself (so later edits are small patches, not rewrites).
    timing = {b["id"]: max(1.0, b["duration_seconds"] - 1.8) for b in board}  # silent scenes: pace reveals to the scene
    timing.update(seconds)
    html = sc_mod.compile_html(spec, timing, image_for, durations={b["id"]: b["duration_seconds"] for b in board})
    page = root / "source" / "index.html"
    page.write_text(html, encoding="utf-8")
    (root / SPEC_PATH).write_text(json.dumps(spec, indent=1, ensure_ascii=False), encoding="utf-8")
    return {
        "scenes": len(scenes), "theme": spec["theme"], "background": spec["bg"],
        "total_seconds": round(sum(b["duration_seconds"] for b in board), 1),
        "layouts": [s["layout"] for s in scenes],
        "visuals": [s["visual"]["type"] for s in scenes],
        "narrated": sorted(seconds), "notes": notes,
    }


def load_spec(project_id: str) -> dict:
    path = require_project_dir(project_id) / SPEC_PATH
    if not path.is_file():
        raise ValueError("No scene spec yet: call build_video first.")
    return json.loads(path.read_text(encoding="utf-8"))


def patch_scene(project_id: str, scene_id: str, fields: dict | None = None, insert_after: str | None = None,
                remove: bool = False, video: dict | None = None, write_narration=None, emit=None) -> dict:
    """Change one scene (or the whole look) of the saved spec and rebuild. Unchanged scenes keep their
    narration, photos and audio, so an edit costs one small call, not a new video."""
    spec = load_spec(project_id)
    items = spec["scenes"]
    idx = next((i for i, s in enumerate(items) if s["id"] == scene_id), None)
    if video:
        for k in ("theme", "bg", "post", "title"):
            if k in video:
                spec[k] = video[k]
    if remove:
        if idx is None:
            raise ValueError(f"No scene '{scene_id}'.")
        items.pop(idx)
    elif fields is not None:
        fields = dict(fields)
        if idx is None:  # new scene
            fields.setdefault("id", scene_id)
            pos = next((i + 1 for i, s in enumerate(items) if s["id"] == insert_after), len(items))
            items.insert(pos, fields)
        else:
            old = items[idx]
            changed_words = any(k in fields and fields[k] != old.get(k) for k in ("headline", "say", "body", "narration"))
            if changed_words and "narration" not in fields:
                old["narration"] = ""  # re-write the voice-over for the new content
            if isinstance(fields.get("stage"), dict) and isinstance(old.get("stage"), dict):
                fields["stage"] = {**old["stage"], **fields["stage"]}  # tweak the actor or the weather, keep the rest
            old.update(fields)
            if isinstance(fields.get("visual"), dict):
                old["visual"] = fields["visual"]  # replace, do not merge stale chart data
    return build_video(project_id, spec, write_narration=write_narration, emit=emit)


def spec_from_storyboard(project_id: str) -> dict:
    """A spec built from a bare storyboard + narration texts (used when the model never produced a page)."""
    root = require_project_dir(project_id)
    board = json.loads((root / "storyboard.json").read_text(encoding="utf-8")).get("scenes") or []
    if not board:
        raise ValueError("storyboard has no scenes.")
    images = sorted(p.name for p in (root / "assets" / "images").glob("*") if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"})
    out = []
    for i, b in enumerate(board):
        stem = Path(str(b.get("narration") or b.get("id"))).stem
        txt = root / "narration" / f"{stem}.txt"
        body = ""
        if txt.is_file():
            t = " ".join(txt.read_text(encoding="utf-8").split())
            cut = t[:170]
            end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
            body = t if len(t) <= 170 else (cut[: end + 1] if end > 40 else cut.rsplit(" ", 1)[0] + "…")
        visual = {"type": "photo", "file": images[(i // 2) % len(images)]} if images and i % 2 == 1 else {}
        out.append({"id": str(b.get("id")), "headline": b.get("title") or str(b.get("id")), "body": body,
                    "kicker": f"{i + 1:02d} / {len(board):02d}", "layout": "title" if i == 0 else "split",
                    "visual": visual, "duration": b.get("duration_seconds") or 6,
                    "narration": ""})
    return {"scenes": out}
