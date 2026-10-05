import argparse
import json
import subprocess
import time
import wave
from pathlib import Path

# Extra seconds recorded after the storyboard ends so trimming to the exact length never
# cuts the last frames short.
TAIL_SECONDS = 0.5
MAX_BROWSER_MESSAGES = 20


def load_storyboard(root: Path):
    path = root / "storyboard.json"
    if not path.exists():
        raise RuntimeError("storyboard.json is required.")
    data = json.loads(path.read_text(encoding="utf-8"))
    scenes = data.get("scenes") if isinstance(data, dict) else None
    if not isinstance(scenes, list) or not scenes:
        raise RuntimeError("storyboard.json has no scenes.")
    total = 0.0
    for scene in scenes:
        if not isinstance(scene, dict):
            raise RuntimeError("Every storyboard scene must be an object.")
        duration = float(scene["duration_seconds"])
        if duration <= 0:
            raise RuntimeError(f"Invalid duration for scene {scene.get('id')}.")
        total += duration
    return data, total


def wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / float(w.getframerate())


def plan_audio(project: Path, scenes):
    """Decide which narration clips go into the video and when they start.

    A scene can set "narration": "<file>.wav" and that clip starts with the scene. If no
    scene references narration but narration/*.wav exist, the clips play back to back from
    the start, in file-name order. Returns [(path, start_seconds), ...].
    """
    narration_dir = project / "narration"
    available = {p.name: p for p in sorted(narration_dir.glob("*.wav"))} if narration_dir.is_dir() else {}
    tracks = []
    referenced = False
    start = 0.0
    for scene in scenes:
        name = scene.get("narration")
        if name:
            referenced = True
            clip = available.get(Path(str(name)).name)
            if clip is None:
                raise RuntimeError(f"Scene {scene.get('id')} references missing narration file: {name}")
            tracks.append((clip, start))
        start += float(scene["duration_seconds"])
    if not referenced:
        t = 0.0
        for clip in available.values():
            tracks.append((clip, t))
            t += wav_duration(clip)
    return tracks


def build_ffmpeg_cmd(src: Path, dst: Path, fps: int, total: float, offset: float, tracks):
    cmd = ["ffmpeg", "-y"]
    if offset > 0:
        # Input option: drops the blank lead-in before the page timeline began.
        cmd += ["-ss", f"{offset:.3f}"]
    cmd += ["-i", str(src)]
    for clip, _ in tracks:
        cmd += ["-i", str(clip)]

    if tracks:
        parts = []
        for i, (_, start) in enumerate(tracks, start=1):
            ms = int(round(start * 1000))
            parts.append(
                f"[{i}:a]aresample=48000,aformat=channel_layouts=stereo,adelay={ms}|{ms}[a{i}]"
            )
        if len(tracks) > 1:
            labels = "".join(f"[a{i}]" for i in range(1, len(tracks) + 1))
            # normalize=0: otherwise amix divides every input's volume by the track count.
            parts.append(f"{labels}amix=inputs={len(tracks)}:normalize=0:duration=longest[aout]")
            audio_label = "[aout]"
        else:
            audio_label = "[a1]"
        cmd += [
            "-filter_complex", ";".join(parts),
            "-map", "0:v:0", "-map", audio_label,
            "-c:a", "aac", "-b:a", "192k",
        ]
    else:
        cmd += ["-map", "0:v:0", "-an"]

    cmd += [
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "18",
        "-pix_fmt", "yuv420p",
        "-r", str(fps),
        "-t", f"{total:.3f}",
        "-movflags", "+faststart",
        str(dst),
    ]
    return cmd


def encode(src: Path, dst: Path, fps: int, total: float, offset: float, tracks):
    completed = subprocess.run(
        build_ffmpeg_cmd(src, dst, fps, total, offset, tracks),
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr[-3000:])


def render(project: Path, width: int, height: int, fps: int):
    # Imported here so the pure helpers above can be used (and tested) without Playwright.
    from playwright.sync_api import sync_playwright

    html = project / "source" / "index.html"
    if not html.exists():
        raise RuntimeError("source/index.html is missing.")

    storyboard, total = load_storyboard(project)
    tracks = plan_audio(project, storyboard["scenes"])

    out_dir = project / "renders" / "final"
    out_dir.mkdir(parents=True, exist_ok=True)
    mp4 = out_dir / "final.mp4"

    # Surface what went wrong inside the page (blocked remote scripts, missing assets,
    # JS errors) so a blank video can be diagnosed instead of silently "succeeding".
    browser_messages = []

    def note(kind, text):
        if len(browser_messages) < MAX_BROWSER_MESSAGES:
            browser_messages.append(f"{kind}: {str(text)[:300]}")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            executable_path="/usr/bin/chromium",
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                # Needed for ES module scripts / XHR between local files under file://.
                "--allow-file-access-from-files",
            ],
        )

        context = browser.new_context(
            viewport={"width": width, "height": height},
            record_video_dir=str(out_dir),
            record_video_size={"width": width, "height": height},
            device_scale_factor=1,
        )
        # Defined before any page script runs, so pages can read them at load time.
        context.add_init_script(
            script=(
                "window.__VIDEO_EXPORT__ = true;"
                f"window.__VIDEO_FPS__ = {int(fps)};"
                f"window.__VIDEO_TOTAL__ = {float(total)};"
            )
        )

        page = context.new_page()
        t_page = time.monotonic()  # the recording starts with the page
        page.on("console", lambda m: note("console.error", m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: note("pageerror", e))
        page.on("requestfailed", lambda r: note("requestfailed", f"{r.url} ({r.failure})"))

        page.goto(html.as_uri(), wait_until="load")
        page.wait_for_function("document.fonts ? document.fonts.status === 'loaded' : true")

        # performance.now() is the time since navigation began, i.e. how far into the
        # page's own timeline we are. That pins down where the page timeline starts on
        # the recording, so trimming and audio line up with the animations.
        perf_now_ms = page.evaluate("performance.now()")
        nav_start = time.monotonic() - perf_now_ms / 1000.0
        remaining = (nav_start + total + TAIL_SECONDS) - time.monotonic()
        page.wait_for_timeout(max(0, int(remaining * 1000)))

        video_path = Path(page.video.path()) if page.video else None
        context.close()
        browser.close()

    offset = nav_start - t_page
    if not (0 <= offset <= 10):
        offset = 0.0  # implausible measurement: don't trim at all

    if video_path is None or not video_path.exists():
        # Fallback: newest WebM in the output directory.
        candidates = sorted(out_dir.glob("*.webm"), key=lambda q: q.stat().st_mtime, reverse=True)
        if not candidates:
            raise RuntimeError("Browser did not produce a WebM recording.")
        video_path = candidates[0]

    encode(video_path, mp4, fps, total, offset, tracks)
    video_path.unlink(missing_ok=True)

    print(json.dumps({
        "ok": True,
        "duration_seconds": total,
        "output": str(mp4),
        "audio": [{"file": clip.name, "start_seconds": round(start, 3)} for clip, start in tracks],
        "trim_offset_seconds": round(offset, 3),
        "browser_messages": browser_messages,
    }))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--width", type=int, required=True)
    parser.add_argument("--height", type=int, required=True)
    parser.add_argument("--fps", type=int, required=True)
    args = parser.parse_args()
    render(Path(args.project), args.width, args.height, args.fps)
