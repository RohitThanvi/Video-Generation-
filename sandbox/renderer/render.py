import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

def load_storyboard(root: Path):
    path = root / "storyboard.json"
    if not path.exists():
        raise RuntimeError("storyboard.json is required.")
    data = json.loads(path.read_text(encoding="utf-8"))
    scenes = data.get("scenes")
    if not scenes:
        raise RuntimeError("storyboard.json has no scenes.")
    total = 0.0
    for scene in scenes:
        duration = float(scene["duration_seconds"])
        if duration <= 0:
            raise RuntimeError(f"Invalid duration for scene {scene.get('id')}.")
        total += duration
    return data, total

def render(project: Path, width: int, height: int, fps: int):
    html = project / "source" / "index.html"
    if not html.exists():
        raise RuntimeError("source/index.html is missing.")

    storyboard, total = load_storyboard(project)

    out_dir = project / "renders" / "final"
    out_dir.mkdir(parents=True, exist_ok=True)
    webm = out_dir / "browser.webm"
    mp4 = out_dir / "final.mp4"

    with sync_playwright() as p:
        browser = p.chromium.launch(
            executable_path="/usr/bin/chromium",
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
            ],
        )

        context = browser.new_context(
            viewport={"width": width, "height": height},
            record_video_dir=str(out_dir),
            record_video_size={"width": width, "height": height},
            device_scale_factor=1,
        )

        page = context.new_page()
        page.goto(html.as_uri(), wait_until="load")
        page.wait_for_function("document.fonts ? document.fonts.status === 'loaded' : true")
        page.wait_for_timeout(500)

        page.evaluate(
            """({fps, total}) => {
                window.__VIDEO_EXPORT__ = true;
                window.__VIDEO_FPS__ = fps;
                window.__VIDEO_TOTAL__ = total;
            }""",
            {"fps": fps, "total": total},
        )

        page.wait_for_timeout(int(total * 1000))

        context.close()
        browser.close()

    # Playwright chooses the filename internally; locate the newest WebM.
    candidates = sorted(out_dir.glob("*.webm"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        raise RuntimeError("Browser did not produce a WebM recording.")
    browser_video = candidates[0]

    cmd = [
        "ffmpeg", "-y",
        "-i", str(browser_video),
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "18",
        "-pix_fmt", "yuv420p",
        "-r", str(fps),
        "-movflags", "+faststart",
        str(mp4),
    ]
    completed = subprocess.run(cmd, capture_output=True, text=True)
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr)

    if browser_video != webm and browser_video.exists():
        browser_video.unlink()

    print(json.dumps({
        "ok": True,
        "duration_seconds": total,
        "output": str(mp4),
    }))

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--width", type=int, required=True)
    parser.add_argument("--height", type=int, required=True)
    parser.add_argument("--fps", type=int, required=True)
    args = parser.parse_args()
    render(Path(args.project), args.width, args.height, args.fps)
