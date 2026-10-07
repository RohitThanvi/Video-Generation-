"""Deterministic frame-by-frame renderer.

The project is served from a small HTTP server inside the sandbox (loopback only; the
container has no network). Every page gets a virtual clock (runtime.js). For each video
frame the clock is advanced by 1/fps, the page is captured as a PNG, and the PNGs are
piped into ffmpeg together with the narration audio.

Compared with recording the browser in real time this gives exact fps, no dropped frames
for heavy WebGL/3D scenes, higher quality, and identical output on every run.
"""
import argparse
import base64
import json
import mimetypes
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

HERE = Path(__file__).resolve().parent
MAX_BROWSER_MESSAGES = 20
READY_TIMEOUT_MS = 60_000
# Narration starts this long after its scene starts, so the scene's cross-fade and first
# entrance animations finish before the voice begins. Keep in sync with app/validation.py.
NARRATION_LEAD_SECONDS = 0.6
# Tolerated overlap between consecutive clips / the end of the video (float noise).
OVERLAP_EPSILON = 0.05

# JS libraries available to every project under /vendor/<name>/... (no CDN needed).
DEFAULT_LIBS_DIR = "/opt/lib/node_modules"

IMPORT_MAP = {
    "imports": {
        "three": "/vendor/three/build/three.module.js",
        "three/addons/": "/vendor/three/examples/jsm/",
        "gsap": "/vendor/gsap/index.js",
        "gsap/": "/vendor/gsap/",
        "katex": "/vendor/katex/dist/katex.mjs",
        "mathkit": "/vendor/mathkit/mathkit.js",
    }
}

EXTRA_MIME = {
    ".js": "text/javascript",
    ".mjs": "text/javascript",
    ".css": "text/css",
    ".json": "application/json",
    ".svg": "image/svg+xml",
    ".wasm": "application/wasm",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".ttf": "font/ttf",
    ".otf": "font/otf",
    ".glb": "model/gltf-binary",
    ".gltf": "model/gltf+json",
    ".wav": "audio/wav",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
}


# ------------------------------------------------------------------ storyboard / audio

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

    A scene can set "narration": "<file>.wav"; that clip starts NARRATION_LEAD_SECONDS
    (or the scene's own "narration_offset") after the scene starts. If no scene references
    narration but narration/*.wav exist, the clips play back to back from the start, in
    file-name order. Returns [(path, start_seconds), ...].

    Clips must never overlap each other or run past the end of the video: that is what made
    narration play over itself. Instead of mixing a mess, this raises an error that tells
    the author which scene needs to be longer.
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
            offset = float(scene.get("narration_offset", NARRATION_LEAD_SECONDS))
            tracks.append((clip, start + max(0.0, offset), scene.get("id")))
        start += float(scene["duration_seconds"])
    total = start
    if not referenced:
        t = 0.0
        for clip in available.values():
            tracks.append((clip, t, None))
            t += wav_duration(clip)

    for i, (clip, begin, scene_id) in enumerate(tracks):
        end = begin + wav_duration(clip)
        limit = tracks[i + 1][1] if i + 1 < len(tracks) else total
        if end > limit + OVERLAP_EPSILON:
            what = "the next narration clip starts" if i + 1 < len(tracks) else "the video ends"
            raise RuntimeError(
                f"Narration {clip.name} (scene {scene_id}) runs until {end:.2f}s but {what} at {limit:.2f}s. "
                f"Increase that scene's duration_seconds by at least {end - limit + 0.5:.1f}s or shorten the text."
            )
    return [(clip, begin) for clip, begin, _ in tracks]


# ------------------------------------------------------------------ ffmpeg

def build_ffmpeg_cmd(dst: Path, fps: int, total: float, tracks):
    """ffmpeg command that reads PNG frames from stdin and mixes the narration tracks."""
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "image2pipe", "-framerate", str(fps), "-vcodec", "png", "-i", "-",
    ]
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
        # Screenshots are full-range sRGB; players expect limited-range BT.709 for HD video.
        # Converting explicitly keeps colours (and pure black backgrounds) correct.
        "-vf", "scale=in_range=full:out_range=tv:out_color_matrix=bt709,format=yuv420p",
        "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "16",
        "-r", str(fps),
        "-t", f"{total:.3f}",
        "-movflags", "+faststart",
        str(dst),
    ]
    return cmd


class Encoder:
    """Streams PNG frames into ffmpeg."""

    def __init__(self, dst: Path, fps: int, total: float, tracks):
        self._log = tempfile.TemporaryFile()
        self._proc = subprocess.Popen(
            build_ffmpeg_cmd(dst, fps, total, tracks),
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=self._log,
        )

    def _error(self) -> str:
        self._log.seek(0)
        return self._log.read().decode("utf-8", "replace")[-3000:]

    def write(self, png: bytes):
        try:
            self._proc.stdin.write(png)
        except (BrokenPipeError, OSError) as exc:
            self._proc.wait()
            raise RuntimeError(f"ffmpeg stopped accepting frames:\n{self._error()}") from exc

    def close(self):
        try:
            self._proc.stdin.close()
        except (BrokenPipeError, OSError):
            pass
        code = self._proc.wait()
        if code != 0:
            raise RuntimeError(f"ffmpeg failed (exit {code}):\n{self._error()}")

    def abort(self):
        try:
            self._proc.kill()
        except OSError:
            pass
        self._proc.wait()


# ------------------------------------------------------------------ HTTP server

def vendor_dirs() -> dict:
    libs = Path(os.environ.get("VIDEO_LIBS_DIR", DEFAULT_LIBS_DIR))
    return {
        "three": libs / "three",
        "gsap": libs / "gsap",
        "d3": libs / "d3",
        "katex": libs / "katex",
        "mathkit": HERE / "mathkit",
    }


def inject_import_map(html: bytes) -> bytes:
    """Add the import map unless the page ships its own, so `import ... from 'three'` works."""
    text = html.decode("utf-8", "replace")
    if "importmap" in text:
        return html
    tag = '<script type="importmap">' + json.dumps(IMPORT_MAP) + "</script>"
    match = re.search(r"<head[^>]*>", text, re.IGNORECASE)
    if match:
        text = text[: match.end()] + tag + text[match.end():]
    else:
        text = tag + text
    return text.encode("utf-8")


def _inside(base: Path, target: Path) -> bool:
    try:
        target.relative_to(base)
        return True
    except ValueError:
        return False


def make_handler(project: Path, vendor: dict):
    project = project.resolve()
    vendor = {k: v.resolve() for k, v in vendor.items() if v.exists()}

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def _resolve(self):
            rel = unquote(urlparse(self.path).path).lstrip("/")
            if rel.startswith("vendor/"):
                parts = rel.split("/", 2)
                base = vendor.get(parts[1])
                sub = parts[2] if len(parts) > 2 else ""
            else:
                base, sub = project, rel
            if base is None:
                return None
            target = (base / sub).resolve()
            if target.is_dir():
                target = target / "index.html"
            if not _inside(base, target) or not target.is_file():
                return None
            return target

        def _send(self, with_body: bool):
            target = self._resolve()
            if target is None and urlparse(self.path).path == "/favicon.ico":
                self.send_response(204)  # browsers always ask; don't log it as an error
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if target is None:
                body = b"not found"
                self.send_response(404)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if with_body:
                    self.wfile.write(body)
                return

            ctype = EXTRA_MIME.get(target.suffix.lower()) or mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            if target.suffix.lower() in {".html", ".htm"}:
                data = inject_import_map(target.read_bytes())
                ctype = "text/html; charset=utf-8"
            else:
                data = None
            size = len(data) if data is not None else target.stat().st_size

            start, end, status = 0, size - 1, 200
            range_header = self.headers.get("Range")
            if range_header and (m := re.fullmatch(r"bytes=(\d*)-(\d*)", range_header.strip())):
                if m.group(1) == "" and m.group(2):
                    start = max(0, size - int(m.group(2)))
                else:
                    start = int(m.group(1) or 0)
                    end = int(m.group(2)) if m.group(2) else size - 1
                end = min(end, size - 1)
                if start <= end:
                    status = 206

            length = end - start + 1
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(length))
            self.send_header("Cache-Control", "no-store")
            if status == 206:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            if not with_body:
                return
            if data is not None:
                self.wfile.write(data[start:end + 1])
            else:
                with open(target, "rb") as f:
                    f.seek(start)
                    remaining = length
                    while remaining > 0:
                        chunk = f.read(min(1 << 20, remaining))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        remaining -= len(chunk)

        def do_GET(self):
            try:
                self._send(True)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_HEAD(self):
            try:
                self._send(False)
            except (BrokenPipeError, ConnectionResetError):
                pass

    return Handler


def start_server(project: Path, vendor: dict | None = None):
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(project, vendor or vendor_dirs()))
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


# ------------------------------------------------------------------ browser

CHROMIUM_ARGS = [
    "--no-sandbox",
    "--disable-dev-shm-usage",
    # No GPU in the container: use the software (SwiftShader) WebGL implementation.
    "--use-gl=angle",
    "--use-angle=swiftshader",
    "--enable-unsafe-swiftshader",
    "--ignore-gpu-blocklist",
    "--enable-webgl",
    # Stable, resolution-independent text and colours.
    "--font-render-hinting=none",
    "--force-color-profile=srgb",
    "--hide-scrollbars",
    "--autoplay-policy=no-user-gesture-required",
]


def chromium_path() -> str | None:
    """Chromium binary: $CHROMIUM_PATH, else the apt one in the sandbox image."""
    return os.environ.get("CHROMIUM_PATH") or "/usr/bin/chromium"


WAIT_READY_JS = """async (timeoutMs) => {
  const real = window.__vclock.realSetTimeout;
  const timeout = new Promise((_, reject) => real(() => reject(new Error('window.__VIDEO_READY did not resolve in time')), timeoutMs));
  const settle = async () => {
    if (window.__VIDEO_READY) await window.__VIDEO_READY;
    await document.fonts.ready;
    await Promise.all([...document.images].map((img) => img.decode().catch(() => {})));
  };
  await Promise.race([settle(), timeout]);
}"""


def render(project: Path, width: int, height: int, fps: int):
    # Imported here so the pure helpers above can be used (and tested) without Playwright.
    from playwright.sync_api import sync_playwright

    html = project / "source" / "index.html"
    if not html.exists():
        raise RuntimeError("source/index.html is missing.")

    storyboard, total = load_storyboard(project)
    tracks = plan_audio(project, storyboard["scenes"])
    frames = max(1, round(total * fps))

    out_dir = project / "renders" / "final"
    out_dir.mkdir(parents=True, exist_ok=True)
    mp4 = out_dir / "final.mp4"
    mp4.unlink(missing_ok=True)

    # Surface what went wrong inside the page (missing assets, JS errors) so a blank or
    # broken video can be diagnosed instead of silently "succeeding".
    browser_messages = []

    def note(kind, text):
        if len(browser_messages) < MAX_BROWSER_MESSAGES:
            browser_messages.append(f"{kind}: {str(text)[:300]}")

    runtime_js = (HERE / "runtime.js").read_text(encoding="utf-8")
    scenes_json = json.dumps(
        [{"id": sc.get("id"), "duration": float(sc["duration_seconds"])} for sc in storyboard["scenes"]]
    )
    server = start_server(project)
    port = server.server_address[1]
    encoder = Encoder(mp4, fps, total, tracks)
    started = time.monotonic()

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                executable_path=chromium_path(), headless=True, args=CHROMIUM_ARGS
            )
            context = browser.new_context(
                viewport={"width": width, "height": height}, device_scale_factor=1
            )
            context.add_init_script(
                script=(
                    "window.__VIDEO_EXPORT__ = true;"
                    f"window.__VIDEO_FPS__ = {int(fps)};"
                    f"window.__VIDEO_TOTAL__ = {float(total)};"
                    f"window.__VIDEO_FRAMES__ = {int(frames)};"
                    f"window.__VIDEO_WIDTH__ = {int(width)};"
                    f"window.__VIDEO_HEIGHT__ = {int(height)};"
                    # Scene ids + durations from storyboard.json: the page's scene engine
                    # (kit.js) reads these, so HTML timing cannot disagree with the storyboard.
                    f"window.__VIDEO_SCENES__ = {scenes_json};"
                )
                + runtime_js
            )
            page = context.new_page()
            page.on("console", lambda m: note("console.error", m.text) if m.type == "error" else None)
            page.on("pageerror", lambda e: note("pageerror", e))
            page.on("requestfailed", lambda r: note("requestfailed", f"{r.url} ({r.failure})"))
            page.on(
                "response",
                lambda r: note("http %d" % r.status, r.url) if r.status >= 400 else None,
            )

            page.goto(f"http://127.0.0.1:{port}/source/index.html", wait_until="load")
            page.evaluate(WAIT_READY_JS, READY_TIMEOUT_MS)
            # First tick: runs initial rAF callbacks / timers due at t=0 and lays the page
            # out, which is when any web fonts actually start loading.
            page.evaluate("() => window.__vclock.advance(0, 0)")
            page.evaluate(WAIT_READY_JS, READY_TIMEOUT_MS)

            cdp = context.new_cdp_session(page)
            for i in range(frames):
                if i > 0:
                    page.evaluate("([t, i]) => window.__vclock.advance(t, i)", [i * 1000.0 / fps, i])
                shot = cdp.send("Page.captureScreenshot", {"format": "png", "optimizeForSpeed": True})
                encoder.write(base64.b64decode(shot["data"]))
                if frames >= 20 and i % max(1, frames // 10) == 0:
                    print(f"frame {i}/{frames}", file=sys.stderr, flush=True)

            context.close()
            browser.close()
        encoder.close()
    except BaseException:
        encoder.abort()
        raise
    finally:
        server.shutdown()

    print(json.dumps({
        "ok": True,
        "duration_seconds": total,
        "frames": frames,
        "fps": fps,
        "render_seconds": round(time.monotonic() - started, 1),
        "output": str(mp4),
        "audio": [{"file": clip.name, "start_seconds": round(start, 3)} for clip, start in tracks],
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
