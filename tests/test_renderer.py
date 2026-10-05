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
    assert [(p.name, s) for p, s in plan] == [("two.wav", 4.0), ("three.wav", 9.0)]


def test_plan_audio_falls_back_to_back_to_back_clips(tmp_path):
    (tmp_path / "narration").mkdir()
    make_wav(tmp_path / "narration" / "a.wav", seconds=1.0)
    make_wav(tmp_path / "narration" / "b.wav", seconds=2.0)
    plan = render.plan_audio(tmp_path, [{"id": "s", "duration_seconds": 10}])
    assert [(p.name, round(s, 2)) for p, s in plan] == [("a.wav", 0.0), ("b.wav", 1.0)]


def test_plan_audio_without_any_narration_is_empty(tmp_path):
    assert render.plan_audio(tmp_path, [{"id": "s", "duration_seconds": 3}]) == []


def test_plan_audio_rejects_missing_referenced_clip(tmp_path):
    with pytest.raises(RuntimeError, match="missing narration"):
        render.plan_audio(tmp_path, [{"id": "s", "duration_seconds": 3, "narration": "nope.wav"}])


# ------------------------------------------------------------------ ffmpeg command

def test_ffmpeg_cmd_without_audio_is_silent_and_trimmed(tmp_path):
    cmd = render.build_ffmpeg_cmd(tmp_path / "in.webm", tmp_path / "out.mp4", 30, 8.0, 0.4, [])
    assert "-an" in cmd and "-filter_complex" not in cmd
    assert cmd[cmd.index("-ss") + 1] == "0.400"
    assert cmd[cmd.index("-t") + 1] == "8.000"
    assert cmd.index("-ss") < cmd.index("-i")  # input option, applies to the video


def test_ffmpeg_cmd_mixes_every_clip_with_its_delay(tmp_path):
    tracks = [(tmp_path / "a.wav", 0.0), (tmp_path / "b.wav", 2.5)]
    cmd = render.build_ffmpeg_cmd(tmp_path / "in.webm", tmp_path / "out.mp4", 30, 8.0, 0.0, tracks)
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert "adelay=0|0" in graph and "adelay=2500|2500" in graph
    assert "amix=inputs=2:normalize=0" in graph
    assert "-ss" not in cmd  # zero offset means no trimming


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
def source_video(tmp_path):
    if not _ffmpeg_can_encode(tmp_path):
        pytest.skip("ffmpeg with libx264 and ffprobe is required")
    src = tmp_path / "recording.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=duration=4:size=320x240:rate=25",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(src)],
        capture_output=True, check=True,
    )
    return src


def test_encode_trims_to_storyboard_length_and_mixes_audio(tmp_path, source_video):
    a = make_wav(tmp_path / "a.wav", seconds=0.5)
    b = make_wav(tmp_path / "b.wav", seconds=0.5, freq=660)
    out = tmp_path / "final.mp4"
    render.encode(source_video, out, 30, 2.0, 0.5, [(a, 0.0), (b, 1.0)])

    assert abs(float(_ffprobe(out, "format=duration")["duration"]) - 2.0) < 0.15
    streams = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,codec_name,channels,sample_rate",
         "-of", "json", str(out)],
        capture_output=True, text=True, check=True,
    ).stdout
    info = {s["codec_type"]: s for s in json.loads(streams)["streams"]}
    assert info["video"]["codec_name"] == "h264"
    assert info["audio"]["codec_name"] == "aac"
    assert info["audio"]["channels"] == 2 and info["audio"]["sample_rate"] == "48000"


def test_encode_places_audio_at_the_requested_time(tmp_path, source_video):
    """The clip is delayed by 1s, so the first second of audio must be silent and later audio loud."""
    clip = make_wav(tmp_path / "tone.wav", seconds=0.5)
    out = tmp_path / "final.mp4"
    render.encode(source_video, out, 30, 2.0, 0.0, [(clip, 1.0)])

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
    assert before < -60  # silence before the clip starts
    assert during > -20  # tone while it plays


def test_encode_without_tracks_has_no_audio_stream(tmp_path, source_video):
    out = tmp_path / "silent.mp4"
    render.encode(source_video, out, 30, 2.0, 0.0, [])
    codec_types = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(out)],
        capture_output=True, text=True, check=True,
    ).stdout.split()
    assert codec_types == ["video"]


def test_encode_surfaces_ffmpeg_errors(tmp_path):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg is required")
    with pytest.raises(RuntimeError):
        render.encode(tmp_path / "missing.webm", tmp_path / "out.mp4", 30, 1.0, 0.0, [])


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


class _StubEngine:
    def __init__(self, writes):
        self.writes = writes
        self.path = None
        self.props = {}

    def setProperty(self, key, value):
        self.props[key] = value

    def save_to_file(self, text, path):
        self.path = path

    def runAndWait(self):
        if self.writes:
            Path(self.path).write_bytes(b"RIFFdata")


def _stub_pyttsx3(monkeypatch, writes):
    engine = _StubEngine(writes)
    module = type(sys)("pyttsx3")
    module.init = lambda: engine
    monkeypatch.setitem(sys.modules, "pyttsx3", module)
    return engine


def test_tts_writes_the_file_and_clamps_volume(tmp_path, monkeypatch):
    engine = _stub_pyttsx3(monkeypatch, writes=True)
    out = tmp_path / "n" / "a.wav"
    tts.synthesize("hello", out, rate=150, volume=7.0)
    assert out.read_bytes() == b"RIFFdata"
    assert engine.props == {"rate": 150, "volume": 1.0}


def test_tts_does_not_mistake_a_stale_file_for_success(tmp_path, monkeypatch):
    _stub_pyttsx3(monkeypatch, writes=False)
    out = tmp_path / "a.wav"
    out.write_bytes(b"old")
    with pytest.raises(RuntimeError):
        tts.synthesize("hello", out)


def test_tts_rejects_empty_text(tmp_path, monkeypatch):
    _stub_pyttsx3(monkeypatch, writes=True)
    with pytest.raises(RuntimeError):
        tts.synthesize("  \n ", tmp_path / "a.wav")
