import json
import subprocess
from types import SimpleNamespace

import pytest

from app import agent, docker_runner, tools, validation
from app.config import PROJECT_ROOT
from app.project import project_dir, create_project

from conftest import make_renderable


# --------------------------------------------------------------------------- project ids

def test_project_id_must_be_a_uuid_hex():
    for bad in ["..", "../x", "a", "g" * 32, "A" * 32, ""]:
        with pytest.raises(FileNotFoundError):
            project_dir(bad)
    assert project_dir("a" * 32) == PROJECT_ROOT / ("a" * 32)


def test_write_paths_do_not_create_directories_for_unknown_projects():
    ghost = "0" * 32
    with pytest.raises(FileNotFoundError):
        tools.write_source(ghost, "index.html", "x")
    with pytest.raises(FileNotFoundError):
        tools.save_text_asset(ghost, "data", "a.json", "{}")
    assert not (PROJECT_ROOT / ghost).exists()


# --------------------------------------------------------------------------- validation

@pytest.mark.parametrize(
    "storyboard",
    [
        [],
        "just a string",
        {"scenes": []},
        {"scenes": ["oops"]},
        {"scenes": [{"id": "a", "duration_seconds": True}]},
        {"scenes": [{"id": "a", "duration_seconds": 0}]},
        {"scenes": [{"duration_seconds": 3}]},
    ],
)
def test_malformed_storyboards_are_reported_not_raised(project, storyboard):
    (project_dir(project) / "storyboard.json").write_text(json.dumps(storyboard))
    result = validation.validate_project(project)
    assert result["valid"] is False
    assert result["errors"]


def test_large_render_output_does_not_break_validation(project):
    root = make_renderable(project)
    big = root / "renders" / "final" / "final.mp4"
    with open(big, "wb") as f:
        f.truncate(60 * 1024 * 1024)  # > default MAX_FILE_BYTES (50 MB)
    assert validation.validate_project(project)["valid"] is True


def test_oversized_input_file_is_still_rejected(project):
    root = make_renderable(project)
    with open(root / "assets" / "video" / "huge.mp4", "wb") as f:
        f.truncate(60 * 1024 * 1024)
    result = validation.validate_project(project)
    assert result["valid"] is False
    assert any("huge.mp4" in e for e in result["errors"])


def test_missing_entry_files_are_warnings_until_render_time(project):
    soft = validation.validate_project(project)
    assert soft["valid"] is True and len(soft["warnings"]) == 2
    strict = validation.validate_project(project, require_renderable=True)
    assert strict["valid"] is False
    assert any("index.html" in e for e in strict["errors"])
    assert any("storyboard.json" in e for e in strict["errors"])


def test_scene_narration_must_exist(project):
    root = make_renderable(project)
    (root / "storyboard.json").write_text(
        json.dumps({"scenes": [{"id": "s1", "duration_seconds": 2, "narration": "s1.wav"}]})
    )
    assert validation.validate_project(project)["valid"] is False
    (root / "narration" / "s1.wav").write_bytes(b"x")
    assert validation.validate_project(project)["valid"] is True


# --------------------------------------------------------------------------- tools

def test_write_source_blocks_traversal_and_bad_extensions(project):
    for bad in ["../x.js", "/abs/x.js", "a/../../x.js", "run.exe", "noext"]:
        with pytest.raises(ValueError):
            tools.write_source(project, bad, "x")
    out = tools.write_source(project, "scenes/intro.js", "console.log(1)")
    assert out["path"] == "source/scenes/intro.js"
    assert (project_dir(project) / "source" / "scenes" / "intro.js").is_file()


def test_save_text_asset_enforces_extensions(project):
    assert tools.save_text_asset(project, "data", "d.json", "{}")["path"] == "assets/data/d.json"
    assert tools.save_text_asset(project, "narration", "n.txt", "hi")["path"] == "narration/n.txt"
    for asset_type, name in [("data", "evil.exe"), ("image", "x.html"), ("narration", "x.wav")]:
        with pytest.raises(ValueError):
            tools.save_text_asset(project, asset_type, name, "x")
    with pytest.raises(ValueError):  # "source" is reachable only through write_source
        tools.save_text_asset(project, "source", "index.html", "x")


def test_write_storyboard_accepts_json_string_and_rejects_junk(project):
    sb = {"scenes": [{"id": "s1", "duration_seconds": 3}]}
    tools.write_storyboard(project, json.dumps(sb))
    assert json.loads((project_dir(project) / "storyboard.json").read_text()) == sb
    for bad in [{}, {"scenes": []}, "not json", [1, 2]]:
        with pytest.raises(ValueError):
            tools.write_storyboard(project, bad)


def test_generate_narration_passes_text_as_file_and_dedupes_manifest(project, fake_sandbox):
    fake_sandbox.creates = ["narration/intro.wav"]
    tools.generate_narration(project, "intro.wav", "-5 degrees is cold")
    tools.generate_narration(project, "intro.wav", "second take")
    cmd = fake_sandbox.calls[0]
    assert "--text-file" in cmd and "--text" not in cmd
    assert "/workspace/narration/intro.txt" in cmd
    assert (project_dir(project) / "narration" / "intro.txt").read_text() == "second take"
    manifest = json.loads((project_dir(project) / "project.json").read_text())
    assert [n["filename"] for n in manifest["narration"]] == ["intro.wav"]


def test_generate_narration_ignores_stale_audio(project, fake_sandbox):
    stale = project_dir(project) / "narration" / "intro.wav"
    stale.write_bytes(b"old audio")
    fake_sandbox.creates = []  # the TTS run "succeeds" but produces nothing
    with pytest.raises(RuntimeError):
        tools.generate_narration(project, "intro.wav", "hello")


def test_generate_narration_validates_input(project, fake_sandbox):
    with pytest.raises(ValueError):
        tools.generate_narration(project, "intro.mp3", "hello")
    with pytest.raises(ValueError):
        tools.generate_narration(project, "intro.wav", "   ")
    assert fake_sandbox.calls == []


def test_render_checks_project_and_params_before_starting_docker(project, fake_sandbox):
    with pytest.raises(tools.ValidationFailed):
        tools.render(project, 1920, 1080, 30)  # no index.html / storyboard.json yet
    make_renderable(project)
    for w, h, fps in [(1921, 1080, 30), (1920, 1081, 30), (1920, 1080, 0), (10, 10, 30), (1920, 1080, 500)]:
        with pytest.raises(ValueError):
            tools.render(project, w, h, fps)
    assert fake_sandbox.calls == []


def test_render_success_reports_video(project, fake_sandbox):
    make_renderable(project)
    fake_sandbox.creates = ["renders/final/final.mp4"]
    result = tools.render(project, 1280, 720, 30)
    assert result["video_url"] == f"/projects/{project}/video"
    assert result["size_bytes"] == 4
    assert "--width" in fake_sandbox.calls[0]


def test_render_fails_loudly_when_no_video_appears(project, fake_sandbox):
    make_renderable(project)
    fake_sandbox.creates = []
    with pytest.raises(RuntimeError):
        tools.render(project, 1280, 720, 30)


# --------------------------------------------------------------------------- docker runner

def _capture_run(monkeypatch, results):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        result = results[min(len(calls) - 1, len(results) - 1)]
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(docker_runner.subprocess, "run", fake_run)
    return calls


def _done(code=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(["docker"], code, stdout, stderr)


def test_timeout_kills_the_named_container(project, monkeypatch):
    calls = _capture_run(
        monkeypatch, [subprocess.TimeoutExpired("docker", 1), _done()]
    )
    with pytest.raises(docker_runner.DockerError, match="timed out"):
        docker_runner.run_in_sandbox(project, ["true"])
    run_cmd, kill_cmd = calls
    name = run_cmd[run_cmd.index("--name") + 1]
    assert kill_cmd == ["docker", "kill", name]


def test_sandbox_command_is_locked_down_and_has_writable_home(project, monkeypatch):
    calls = _capture_run(monkeypatch, [_done()])
    docker_runner.run_in_sandbox(project, ["true"])
    cmd = calls[0]
    assert cmd[cmd.index("--network") + 1] == "none"
    assert "--read-only" in cmd
    assert cmd[cmd.index("-e") + 1] == "HOME=/tmp"
    assert not any("docker.sock" in part for part in cmd)


def test_host_project_root_is_used_for_the_bind_mount(project, monkeypatch):
    monkeypatch.setattr(docker_runner, "HOST_PROJECT_ROOT", "/host/data/projects/")
    calls = _capture_run(monkeypatch, [_done()])
    docker_runner.run_in_sandbox(project, ["true"])
    cmd = calls[0]
    assert cmd[cmd.index("-v") + 1] == f"/host/data/projects/{project}:/workspace:rw"


def test_errors_are_truncated_and_hinted(project, monkeypatch):
    noisy = "x" * 50_000 + "\nUnable to find image 'ai-video-sandbox:latest' locally"
    _capture_run(monkeypatch, [_done(125, stderr=noisy)])
    with pytest.raises(docker_runner.DockerError) as info:
        docker_runner.run_in_sandbox(project, ["true"])
    message = str(info.value)
    assert len(message) < 10_000
    assert "docker build" in message


@pytest.mark.parametrize(
    "stderr",
    [
        "Cannot connect to the Docker daemon at unix:///var/run/docker.sock. Is the docker daemon running?",
        'error during connect: Get "http://%2F%2F.%2Fpipe%2FdockerDesktopLinuxEngine/v1.46/": open //./pipe/x',
        "failed to connect to the docker API at npipe:////./pipe/dockerDesktopLinuxEngine; check if the path is correct",
    ],
)
def test_daemon_not_running_gets_a_hint(project, monkeypatch, stderr):
    _capture_run(monkeypatch, [_done(1, stderr=stderr)])
    with pytest.raises(docker_runner.DockerError, match="Docker Desktop"):
        docker_runner.run_in_sandbox(project, ["true"])


# --------------------------------------------------------------------------- HTTP API

def test_upload_to_unknown_project_is_404_and_creates_nothing(client):
    ghost = "1" * 32
    r = client.post(f"/projects/{ghost}/assets", data={"asset_type": "image"}, files={"file": ("a.png", b"x")})
    assert r.status_code == 404
    assert not (PROJECT_ROOT / ghost).exists()


def test_upload_validates_type_and_size(client, project, monkeypatch):
    url = f"/projects/{project}/assets"
    bad = client.post(url, data={"asset_type": "image"}, files={"file": ("a.exe", b"x")})
    assert bad.status_code == 400

    ok = client.post(url, data={"asset_type": "image"}, files={"file": ("logo.png", b"\x89PNG")})
    assert ok.status_code == 200
    assert (project_dir(project) / "assets" / "images" / "logo.png").is_file()

    monkeypatch.setattr("app.main.MAX_FILE_BYTES", 10)
    big = client.post(url, data={"asset_type": "data"}, files={"file": ("d.txt", b"y" * 100)})
    assert big.status_code == 413
    assert not (project_dir(project) / "assets" / "data" / "d.txt").exists()


def test_validate_endpoint_shape_matches_what_the_ui_reads(client, project):
    body = client.post(f"/projects/{project}/validate").json()
    assert "valid" in body and "ok" not in body


def test_unknown_project_is_404_everywhere(client):
    ghost = "2" * 32
    assert client.get(f"/projects/{ghost}").status_code == 404
    assert client.post(f"/projects/{ghost}/validate").status_code == 404
    assert client.post(f"/projects/{ghost}/render", json={}).status_code == 404
    assert client.get(f"/projects/{ghost}/video").status_code == 404
    assert client.get("/projects/not-an-id/video").status_code == 404


def test_request_bodies_are_optional(client, project):
    r = client.post(f"/projects/{project}/render")
    assert r.status_code == 422 and "validation failed" in r.json()["detail"].lower()
    r = client.post(f"/projects/{project}/agent")
    assert r.status_code == 500 and "GROQ_API_KEY" in r.json()["detail"]


def test_render_rejects_bad_dimensions(client, project):
    assert client.post(f"/projects/{project}/render", json={"width": 1921}).status_code == 422
    assert client.post(f"/projects/{project}/render", json={"fps": 0}).status_code == 422


def test_video_endpoint_serves_the_render_without_caching(client, project):
    root = make_renderable(project)
    (root / "renders" / "final" / "final.mp4").write_bytes(b"mp4data")
    r = client.get(f"/projects/{project}/video")
    assert r.status_code == 200 and r.content == b"mp4data"
    assert r.headers["cache-control"] == "no-store"


def test_render_endpoint_end_to_end_with_fake_sandbox(client, project, fake_sandbox):
    make_renderable(project)
    fake_sandbox.creates = ["renders/final/final.mp4"]
    r = client.post(f"/projects/{project}/render", json={"width": 1280, "height": 720, "fps": 24})
    assert r.status_code == 200
    assert r.json()["video_url"] == f"/projects/{project}/video"


# --------------------------------------------------------------------------- agent loop

def _tool_call(call_id, name, arguments):
    return SimpleNamespace(
        id=call_id, function=SimpleNamespace(name=name, arguments=arguments)
    )


def _response(content="", tool_calls=None):
    msg = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


def _install_fake_llm(monkeypatch, script):
    """script: list of responses. Returns the list of message-histories seen per call."""
    seen = []
    remaining = list(script)

    class FakeClient:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

        def _create(self, **kwargs):
            seen.append(json.loads(json.dumps(kwargs["messages"], default=str)))
            return remaining.pop(0)

    monkeypatch.setattr(agent, "OpenAI", FakeClient)
    monkeypatch.setattr(agent, "GROQ_API_KEY", "test-key")
    return seen


def _last_tool_output(history):
    tool_msgs = [m for m in history if m["role"] == "tool"]
    return json.loads(tool_msgs[-1]["content"])


def test_agent_survives_empty_arguments_for_parameterless_tools(project, monkeypatch):
    seen = _install_fake_llm(
        monkeypatch,
        [_response(tool_calls=[_tool_call("c1", "validate_project", "")]), _response("all good")],
    )
    result = agent.run_agent(project, "check it")
    assert result["status"] == "completed" and result["message"] == "all good"
    assert result["usage"]["llm_calls"] == 2
    out = _last_tool_output(seen[1])
    assert out["ok"] is True and "valid" in out["result"]


def test_agent_reports_unknown_tools_by_name(project, monkeypatch):
    seen = _install_fake_llm(
        monkeypatch,
        [_response(tool_calls=[_tool_call("c1", "rm_rf", "{}")]), _response("ok")],
    )
    agent.run_agent(project, None)
    out = _last_tool_output(seen[1])
    assert out["ok"] is False and "rm_rf" in out["error"]


def test_agent_can_write_storyboard_sent_as_a_string(project, monkeypatch):
    sb = json.dumps({"scenes": [{"id": "s1", "duration_seconds": 4}]})
    args = json.dumps({"storyboard": sb})
    seen = _install_fake_llm(
        monkeypatch,
        [_response(tool_calls=[_tool_call("c1", "write_storyboard", args)]), _response("done")],
    )
    agent.run_agent(project, None)
    assert _last_tool_output(seen[1])["ok"] is True
    assert (project_dir(project) / "storyboard.json").is_file()


def test_agent_cannot_copy_arbitrary_host_files(project, monkeypatch, tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("top secret")
    args = json.dumps({"asset_type": "data", "source_path": str(secret)})
    seen = _install_fake_llm(
        monkeypatch,
        [_response(tool_calls=[_tool_call("c1", "ingest_host_asset", args)]), _response("ok")],
    )
    agent.run_agent(project, "make a video about cats")
    out = _last_tool_output(seen[1])
    assert out["ok"] is False and "not supplied" in out["error"]
    assert not (project_dir(project) / "assets" / "data" / "secret.txt").exists()


def test_agent_can_ingest_a_path_the_user_typed(project, monkeypatch, tmp_path):
    asset = tmp_path / "numbers.txt"
    asset.write_text("1 2 3")
    args = json.dumps({"asset_type": "data", "source_path": str(asset)})
    seen = _install_fake_llm(
        monkeypatch,
        [_response(tool_calls=[_tool_call("c1", "ingest_host_asset", args)]), _response("ok")],
    )
    agent.run_agent(project, f"chart the numbers in {asset}")
    assert _last_tool_output(seen[1])["ok"] is True
    assert (project_dir(project) / "assets" / "data" / "numbers.txt").read_text() == "1 2 3"


def test_agent_tool_names_match_dispatcher():
    schema_names = {t["function"]["name"] for t in agent._tool_schemas()}
    assert schema_names == {
        "write_storyboard", "write_source", "save_text_asset", "generate_narration",
        "ingest_host_asset", "validate_project", "render_video", "install_design_kit",
        "search_free_images", "download_asset", "read_source", "patch_source", "generate_narrations",
    }


# ------------------------------------------------------------------ design kit / audio sync

def _wav(path, seconds):
    import wave
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\x00\x00" * int(8000 * seconds))


def test_install_design_kit_copies_css_and_engine(project):
    result = tools.install_design_kit(project)
    root = project_dir(project) / "source"
    assert (root / "kit.css").read_text().count(".fade-in-up") > 0
    assert "__VIDEO_SCENES__" in (root / "kit.js").read_text()
    assert result["files"] == ["source/kit.css", "source/kit.js"]


def test_validation_rejects_narration_longer_than_its_scene(project):
    root = project_dir(project)
    _wav(root / "narration" / "s1.wav", 6.0)
    (root / "storyboard.json").write_text(
        json.dumps({"scenes": [{"id": "s1", "duration_seconds": 6, "narration": "s1.wav"}]})
    )
    result = validation.validate_project(project)
    assert not result["valid"]
    assert "at least 7.5" in " ".join(result["errors"])  # 0.6 lead + 6.0 + 0.8 hold -> 7.5


def test_validation_accepts_narration_that_fits(project):
    root = project_dir(project)
    _wav(root / "narration" / "s1.wav", 6.0)
    (root / "storyboard.json").write_text(
        json.dumps({"scenes": [{"id": "s1", "duration_seconds": 7.5, "narration": "s1.wav"}]})
    )
    assert validation.validate_project(project)["valid"]


def test_validation_checks_scene_ids_in_index_html(project):
    root = project_dir(project)
    tools.install_design_kit(project)
    (root / "storyboard.json").write_text(
        json.dumps({"scenes": [{"id": "a", "duration_seconds": 3}, {"id": "b", "duration_seconds": 3}]})
    )
    html = (
        '<link rel="stylesheet" href="kit.css"><section class="scene" data-scene="a"></section>'
        '<section class="scene" data-scene="zzz"></section><script src="kit.js"></script>'
    )
    (root / "source" / "index.html").write_text(html)
    errors = " ".join(validation.validate_project(project)["errors"])
    assert "b" in errors and "zzz" in errors


def test_validation_warns_when_kit_is_not_used(project):
    root = project_dir(project)
    (root / "source").mkdir(exist_ok=True)
    (root / "source" / "index.html").write_text("<html></html>")
    assert any("kit.css" in w for w in validation.validate_project(project)["warnings"])


def test_system_prompt_enforces_the_design_and_sync_rules():
    text = agent.SYSTEM
    for needle in ["install_design_kit", "fade-in-up", "cubic-bezier", "data-scene", "min_scene_duration_seconds", "glass", "inline <svg"]:
        assert needle in text, needle


def test_agent_prompt_says_bundled_libraries_need_no_upload():
    from app import agent

    prompt = agent.SYSTEM
    assert "import * as THREE from 'three'" in prompt
    assert "never ask the user to upload" in prompt
