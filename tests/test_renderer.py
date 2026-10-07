"""Tests for the pure parts of the sandbox renderer and TTS scripts.

These run on the host: they need no Chromium, Playwright, Docker or speech engine.
The ffmpeg tests are skipped when ffmpeg/ffprobe are unavailable.
"""
import importlib.util
import json
import shutil
import subprocess
import sys
import wave
from pathlib import Path

import pytest

RENDERER_DIR = Path(__file__).resolve().parents[1] / "sandbox" / "renderer"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, RENDERER_DIR / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


render = _load("render")
tts = _load("tts")


def make_wav(path, seconds=0.5, rate=22050, freq=440):
    import math
    import struct

    frames = b"".join(
        struct.pack("<h", int(12000 * math.sin(2 * math.pi * freq * i / rate)))
        for i in range(int(rate * seconds))
    )
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(frames)
    return path


# ------------------------------------------------------------------ storyboard / audio plan

def test_load_storyboard_sums_durations(tmp_path):
    (tmp_path / "storyboard.json").write_text(
        json.dumps({"scenes": [{"id": "a", "duration_seconds": 2}, {"id": "b", "duration_seconds": 3.5}]})
    )
    _, total = render.load_storyboard(tmp_path)
    assert total == pytest.approx(5.5)


@pytest.mark.parametrize("bad", [[], {"scenes": []}, {"scenes": ["x"]}, {"scenes": [{"id": "a", "duration_seconds": 0}]}])
def test_load_storyboard_rejects_bad_input(tmp_path, bad):
    (tmp_path / "storyboard.json").write_text(json.dumps(bad))
    with pytest.raises(RuntimeError):
        render.load_storyboard(tmp_path)


def test_plan_audio_places_clip_at_its_scene_start(tmp_path):
    (tmp_path / "narration").mkdir()
    make_wav(tmp_path / "narration" / "two.wav")
    make_wav(tmp_path / "narration" / "three.wav")
    scenes = [
        {"id": "a", "duration_seconds": 4},
        {"id": "b", "duration_seconds": 5, "narration": "two.wav"},
        {"id": "c", "duration_seconds": 6, "narration": "narration/three.wav"},
    ]
    plan = render.plan_audio(tmp_path, scenes)
    lead = render.NARRATION_LEAD_SECONDS
    assert [(p.name, s) for p, s in plan] == [("two.wav", 4.0 + lead), ("three.wav", 9.0 + lead)]


def test_plan_audio_falls_back_to_back_to_back_clips(tmp_path):
    (tmp_path / "narration").mkdir()
    make_wav(tmp_path / "narration" / "a.wav", seconds=1.0)
    make_wav(tmp_path / "narration" / "b.wav", seconds=2.0)
    plan = render.plan_audio(tmp_path, [{"id": "s", "duration_seconds": 10}])
    assert [(p.name, round(s, 2)) for p, s in plan] == [("a.wav", 0.0), ("b.wav", 1.0)]


def test_plan_audio_rejects_clips_that_would_overlap(tmp_path):
    (tmp_path / "narration").mkdir()
    make_wav(tmp_path / "narration" / "long.wav", seconds=3.0)
    make_wav(tmp_path / "narration" / "next.wav", seconds=0.5)
    scenes = [
        {"id": "a", "duration_seconds": 2, "narration": "long.wav"},
        {"id": "b", "duration_seconds": 4, "narration": "next.wav"},
    ]
    with pytest.raises(RuntimeError, match="scene a.*Increase that scene"):
        render.plan_audio(tmp_path, scenes)


def test_plan_audio_rejects_clip_running_past_the_end(tmp_path):
    (tmp_path / "narration").mkdir()
    make_wav(tmp_path / "narration" / "long.wav", seconds=3.0)
    with pytest.raises(RuntimeError, match="video ends"):
        render.plan_audio(tmp_path, [{"id": "a", "duration_seconds": 3, "narration": "long.wav"}])


def test_plan_audio_allows_back_to_back_clips_that_fit(tmp_path):
    (tmp_path / "narration").mkdir()
    make_wav(tmp_path / "narration" / "a.wav", seconds=2.0)
    make_wav(tmp_path / "narration" / "b.wav", seconds=2.0)
    scenes = [
        {"id": "a", "duration_seconds": 4, "narration": "a.wav"},
        {"id": "b", "duration_seconds": 4, "narration": "b.wav"},
    ]
    assert [round(st, 2) for _, st in render.plan_audio(tmp_path, scenes)] == [0.6, 4.6]


def test_plan_audio_without_any_narration_is_empty(tmp_path):
    assert render.plan_audio(tmp_path, [{"id": "s", "duration_seconds": 3}]) == []


def test_plan_audio_rejects_missing_referenced_clip(tmp_path):
    with pytest.raises(RuntimeError, match="missing narration"):
        render.plan_audio(tmp_path, [{"id": "s", "duration_seconds": 3, "narration": "nope.wav"}])


# ------------------------------------------------------------------ ffmpeg command

def test_ffmpeg_cmd_without_audio_is_silent_and_trimmed(tmp_path):
    cmd = render.build_ffmpeg_cmd(tmp_path / "out.mp4", 30, 8.0, [])
    assert "-an" in cmd and "-filter_complex" not in cmd
    assert cmd[cmd.index("-t") + 1] == "8.000"
    assert "image2pipe" in cmd and cmd[cmd.index("-i") + 1] == "-"


def test_ffmpeg_cmd_mixes_every_clip_with_its_delay(tmp_path):
    tracks = [(tmp_path / "a.wav", 0.0), (tmp_path / "b.wav", 2.5)]
    cmd = render.build_ffmpeg_cmd(tmp_path / "out.mp4", 30, 8.0, tracks)
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert "adelay=0|0" in graph and "adelay=2500|2500" in graph
    assert "amix=inputs=2:normalize=0" in graph


# ------------------------------------------------------------------ real ffmpeg

def _ffmpeg_can_encode(tmp_path):
    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        return False
    probe = subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=duration=0.2:size=64x64:rate=10",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(tmp_path / "probe.mp4")],
        capture_output=True,
    )
    return probe.returncode == 0


def _ffprobe(path, entries):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", entries, "-of", "default=nw=1", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout
    return dict(line.split("=", 1) for line in out.splitlines() if "=" in line)


@pytest.fixture
def frames(tmp_path):
    """60 PNG frames (2 s at 30 fps), produced by ffmpeg itself."""
    if not _ffmpeg_can_encode(tmp_path):
        pytest.skip("ffmpeg with libx264 and ffprobe is required")
    out = tmp_path / "frames"
    out.mkdir()
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=duration=2:size=320x240:rate=30",
         str(out / "f%03d.png")],
        capture_output=True, check=True,
    )
    return [p.read_bytes() for p in sorted(out.glob("f*.png"))]


def _encode(dst, frames, fps, total, tracks):
    enc = render.Encoder(dst, fps, total, tracks)
    for png in frames:
        enc.write(png)
    enc.close()


def test_encoder_produces_h264_with_exact_length_and_mixed_audio(tmp_path, frames):
    a = make_wav(tmp_path / "a.wav", seconds=0.5)
    b = make_wav(tmp_path / "b.wav", seconds=0.5, freq=660)
    out = tmp_path / "final.mp4"
    _encode(out, frames, 30, 2.0, [(a, 0.0), (b, 1.0)])

    assert abs(float(_ffprobe(out, "format=duration")["duration"]) - 2.0) < 0.15
    video_frames = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=nb_frames",
         "-of", "csv=p=0", str(out)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert video_frames == "60"
    streams = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,codec_name,channels,sample_rate",
         "-of", "json", str(out)],
        capture_output=True, text=True, check=True,
    ).stdout
    info = {s["codec_type"]: s for s in json.loads(streams)["streams"]}
    assert info["video"]["codec_name"] == "h264"
    assert info["audio"]["codec_name"] == "aac"
    assert info["audio"]["channels"] == 2 and info["audio"]["sample_rate"] == "48000"


def test_encoder_places_audio_at_the_requested_time(tmp_path, frames):
    clip = make_wav(tmp_path / "tone.wav", seconds=0.5)
    out = tmp_path / "final.mp4"
    _encode(out, frames, 30, 2.0, [(clip, 1.0)])

    def peak(start, dur):
        res = subprocess.run(
            ["ffmpeg", "-hide_banner", "-nostats", "-ss", str(start), "-t", str(dur), "-i", str(out),
             "-vn", "-af", "volumedetect", "-f", "null", "-"],
            capture_output=True, text=True,
        )
        for line in res.stderr.splitlines():
            if "max_volume" in line:
                return float(line.split("max_volume:")[1].split("dB")[0])
        return float("-inf")

    before, during = peak(0.0, 0.8), peak(1.05, 0.4)
    assert before != float("-inf") and during != float("-inf"), "volumedetect produced no reading"
    assert before < -60
    assert during > -20


def test_encoder_without_tracks_has_no_audio_stream(tmp_path, frames):
    out = tmp_path / "silent.mp4"
    _encode(out, frames, 30, 2.0, [])
    codec_types = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(out)],
        capture_output=True, text=True, check=True,
    ).stdout.split()
    assert codec_types == ["video"]


def test_encoder_surfaces_ffmpeg_errors(tmp_path):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg is required")
    enc = render.Encoder(tmp_path / "no-such-dir" / "out.mp4", 30, 1.0, [])
    with pytest.raises(RuntimeError):
        for _ in range(50):
            enc.write(b"not a png")
        enc.close()


# ------------------------------------------------------------------ import map / HTTP server

def test_import_map_is_injected_once_into_head():
    html = b"<html><head><title>x</title></head><body></body></html>"
    out = render.inject_import_map(html).decode()
    assert out.index("importmap") < out.index("<title>")
    for name in ("three", "gsap", "katex", "mathkit"):
        assert f'"{name}"' in out
    assert render.inject_import_map(out.encode()).decode() == out  # idempotent
    assert "importmap" in render.inject_import_map(b"<p>no head</p>").decode()


@pytest.fixture
def server(tmp_path):
    import urllib.request  # noqa: F401

    project = tmp_path / "proj"
    (project / "source").mkdir(parents=True)
    (project / "source" / "index.html").write_text("<html><head></head><body>hi</body></html>")
    (project / "source" / "data.bin").write_bytes(bytes(range(100)))
    (tmp_path / "secret.txt").write_text("secret")
    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "x.js").write_text("export default 1")
    srv = render.start_server(project, {"gsap": lib})
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def _get(url, headers=None):
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers or {})) as r:
            return r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, b"", {}


def test_server_serves_project_vendor_and_injects_import_map(server):
    status, body, _ = _get(server + "/source/index.html")
    assert status == 200 and b"importmap" in body
    assert _get(server + "/vendor/gsap/x.js")[1] == b"export default 1"


def test_server_blocks_traversal_unknown_vendor_and_missing_files(server):
    assert _get(server + "/source/../../secret.txt")[0] == 404
    assert _get(server + "/%2e%2e/secret.txt")[0] == 404
    assert _get(server + "/vendor/gsap/../../secret.txt")[0] == 404
    assert _get(server + "/vendor/nope/x.js")[0] == 404
    assert _get(server + "/source/missing.js")[0] == 404


def test_server_favicon_is_204_and_ranges_work(server):
    assert _get(server + "/favicon.ico")[0] == 204
    status, body, _ = _get(server + "/source/data.bin", {"Range": "bytes=10-19"})
    assert status == 206 and body == bytes(range(10, 20))


# ------------------------------------------------------------------ real browser (optional)

def _chromium():
    try:
        import playwright  # noqa: F401
    except ImportError:
        return None
    import os

    for cand in (os.environ.get("CHROMIUM_PATH"), shutil.which("chromium"), "/opt/pw-browsers/chromium"):
        if cand and Path(cand).exists():
            return cand
    return None


def test_virtual_clock_makes_rendering_deterministic(tmp_path, monkeypatch, capsys):
    chromium = _chromium()
    if not chromium or not shutil.which("ffmpeg"):
        pytest.skip("Chromium + playwright + ffmpeg are required")
    monkeypatch.setenv("CHROMIUM_PATH", chromium)
    proj = tmp_path / "p"
    (proj / "source").mkdir(parents=True)
    (proj / "source" / "index.html").write_text(
        "<html><body style='margin:0;background:#000'>"
        "<div id=b style='width:50px;height:50px;background:#f00;position:absolute;"
        "animation:m 1s linear forwards'></div>"
        "<style>@keyframes m{from{left:0}to{left:200px}}</style>"
        "<script>let n=0;setTimeout(()=>{document.body.style.background='#00f'},500)</script>"
        "</body></html>"
    )
    (proj / "storyboard.json").write_text(
        json.dumps({"scenes": [{"id": "s", "duration_seconds": 1}]})
    )
    def run():
        render.render(proj, 320, 180, 10)
        return json.loads(capsys.readouterr().out.strip().splitlines()[-1])

    first = run()
    assert first["frames"] == 10 and first["browser_messages"] == []
    assert abs(float(_ffprobe(first["output"], "format=duration")["duration"]) - 1.0) < 0.15
    ref = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", first["output"], "-f", "framemd5", "-"],
        capture_output=True, text=True, check=True,
    ).stdout
    again = run()
    ref2 = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", again["output"], "-f", "framemd5", "-"],
        capture_output=True, text=True, check=True,
    ).stdout
    assert ref == ref2


# ------------------------------------------------------------------ tts

def test_tts_accepts_text_file_or_text_but_not_both_or_neither():
    args = tts.parse_args(["--text-file", "/x.txt", "--output", "/o.wav"])
    assert args.text_file == "/x.txt" and args.text is None
    args = tts.parse_args(["--text=-5 degrees", "--output", "/o.wav"])
    assert args.text == "-5 degrees"
    with pytest.raises(SystemExit):
        tts.parse_args(["--output", "/o.wav"])
    with pytest.raises(SystemExit):
        tts.parse_args(["--text", "a", "--text-file", "b", "--output", "/o.wav"])


def _stub_espeak(monkeypatch, writes, returncode=0, stderr=""):
    """Replace subprocess.run so no real espeak-ng is needed; records the call."""
    calls = {}

    def fake_run(cmd, input=None, **kwargs):
        calls["cmd"], calls["input"] = cmd, input
        if writes:
            Path(cmd[cmd.index("-w") + 1]).write_bytes(b"RIFFdata")
        return subprocess.CompletedProcess(cmd, returncode, stdout="", stderr=stderr)

    monkeypatch.setattr(tts.subprocess, "run", fake_run)
    return calls


def test_tts_writes_the_file_and_clamps_volume(tmp_path, monkeypatch):
    calls = _stub_espeak(monkeypatch, writes=True)
    out = tmp_path / "n" / "a.wav"
    tts.synthesize("hello", out, rate=150, volume=7.0)
    assert out.read_bytes() == b"RIFFdata"
    cmd = calls["cmd"]
    assert cmd[cmd.index("-s") + 1] == "150"
    assert cmd[cmd.index("-a") + 1] == "200"  # volume clamped to 1.0 -> amplitude 200
    assert calls["input"] == "hello"


def test_tts_reads_stdin_not_a_file_named_dash(tmp_path, monkeypatch):
    # Regression: espeak-ng treats "-f -" as a file called "-" ("Failed to stat() file '-'").
    calls = _stub_espeak(monkeypatch, writes=True)
    tts.synthesize("hello", tmp_path / "a.wav")
    assert "--stdin" in calls["cmd"]
    assert "-f" not in calls["cmd"]


def test_tts_does_not_mistake_a_stale_file_for_success(tmp_path, monkeypatch):
    _stub_espeak(monkeypatch, writes=False)
    out = tmp_path / "a.wav"
    out.write_bytes(b"old")
    with pytest.raises(RuntimeError):
        tts.synthesize("hello", out)


def test_tts_surfaces_espeak_errors(tmp_path, monkeypatch):
    _stub_espeak(monkeypatch, writes=False, returncode=1, stderr="boom")
    with pytest.raises(RuntimeError, match="boom"):
        tts.synthesize("hello", tmp_path / "a.wav")


def test_tts_rejects_empty_text(tmp_path, monkeypatch):
    _stub_espeak(monkeypatch, writes=True)
    with pytest.raises(RuntimeError):
        tts.synthesize("  \n ", tmp_path / "a.wav")
