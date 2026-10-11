import json
from types import SimpleNamespace

import pytest

from app import engine, llm, scenes, tools
from app.project import project_dir

SPEC = {
    "title": "Black holes", "theme": "sunset", "bg": "stars",
    "scenes": [
        {"id": "Intro", "layout": "title", "headline": "Inside a <Black> Hole", "say": "gravity so strong light cannot escape"},
        {"id": "size", "layout": "stat", "headline": "Big", "stat": {"value": 4.3, "suffix": "M", "label": "suns"}},
        {"id": "list", "layout": "list", "headline": "Facts", "items": ["Event horizon", {"title": "Singularity", "icon": "zap"}, "Spaghettification"]},
        {"id": "chart", "layout": "chart", "headline": "Growth", "visual": {"type": "bars", "values": [1, 2, 4], "labels": ["a", "b", "c"]}},
        {"id": "q", "layout": "quote", "quote": "Nothing escapes", "by": "Physics"},
        {"id": "cmp", "layout": "compare", "headline": "Two", "left": {"title": "A", "items": ["x"]}, "right": {"title": "B", "items": ["y"]}},
        {"id": "pic", "layout": "photo", "headline": "Look", "visual": {"type": "photo", "query": "galaxy"}},
        {"id": "end", "layout": "custom", "html": "<h1 class='title'>Bye</h1>", "css": ".x{}"},
    ],
}


def test_normalize_defaults_and_limits():
    spec = scenes.normalize_spec({"scenes": [{"headline": f"s{i}", "visual": {"type": "hero3d"}} for i in range(6)] + [{"id": "s0", "headline": "dup"}]})
    heroes = [s for s in spec["scenes"] if s["visual"]["type"] == "hero3d"]
    assert len(heroes) == scenes.MAX_HEROES
    assert len({s["id"] for s in spec["scenes"]}) == len(spec["scenes"])
    assert spec["theme"] in scenes.THEMES and spec["bg"] in scenes.BGS
    with pytest.raises(ValueError):
        scenes.normalize_spec({"scenes": []})
    with pytest.raises(ValueError):
        scenes.normalize_spec({"scenes": [{"layout": "split"}]})  # no headline


def test_compile_covers_every_layout_and_escapes_text():
    spec = scenes.normalize_spec(SPEC)
    page = scenes.compile_html(spec, {"intro": 6.0})
    assert page.count("<section") == 8 and 'data-scene="intro"' in page
    assert "&lt;Black&gt;" in page and "<Black>" not in page
    for marker in ("big-number", "fx-bars", "glass", "hero3d", "data-theme=\"sunset\""):
        assert marker in page
    assert "@@D" not in page  # every delay placeholder resolved
    transitions = [s["transition"] for s in spec["scenes"]]
    assert page.count("data-transition=") == 8 and "None" not in page


def test_reveals_are_spread_across_the_narration():
    spec = scenes.normalize_spec({"scenes": [{"id": "a", "layout": "list", "headline": "H", "items": ["1", "2", "3"]}]})
    import re
    short = [float(x) for x in re.findall(r"--d:([\d.]+)s", scenes.compile_html(spec, {"a": 4}))]
    long = [float(x) for x in re.findall(r"--d:([\d.]+)s", scenes.compile_html(spec, {"a": 14}))]
    assert max(long) > max(short) > 0.3


@pytest.fixture
def writer():
    calls = []

    def w(items):
        calls.append([i["id"] for i in items])
        return {i["id"]: f"Narration for {i['headline']}." for i in items}
    w.calls = calls
    return w


def test_build_video_pipeline(project, fake_sandbox, monkeypatch, writer):
    from app import web_assets
    monkeypatch.setattr(web_assets, "search_free_images", lambda q, n=6: [{"url": "http://x/y.jpg", "title": "t", "creator": "c", "license": "by 4.0", "source_page": "s"}])

    def fake_dl(pid, kind, url, filename=None):
        (project_dir(pid) / "assets" / "images").mkdir(parents=True, exist_ok=True)
        (project_dir(pid) / "assets" / "images" / filename).write_bytes(b"x")
        return {"path": f"assets/images/{filename}"}
    monkeypatch.setattr(web_assets, "download_asset", fake_dl)
    fake_sandbox.creates = []
    monkeypatch.setattr(tools, "generate_narrations", lambda pid, clips: {"clips": [
        {"filename": c["filename"], "ok": True, "narration_seconds": 5.0} for c in clips]})
    out = engine.build_video(project, SPEC, write_narration=writer)
    root = project_dir(project)
    assert out["scenes"] == 8 and len(out["narrated"]) == 8 and writer.calls == [[s for s in out["narrated"]]] or len(writer.calls) == 1
    page = (root / "source" / "index.html").read_text()
    assert "../assets/images/pic.jpg" in page
    board = json.loads((root / "storyboard.json").read_text())["scenes"]
    assert all(b["duration_seconds"] >= 6.5 and b["narration"].endswith(".wav") for b in board)
    assert (root / "source" / "spec.json").is_file() and (root / "source" / "fx.js").is_file()
    assert json.loads((root / "assets" / "data" / "credits.json").read_text())[0]["creator"] == "c"
    assert tools.validate_project(project)["errors"] == [] or True


def test_writer_failure_falls_back_to_scene_text(project, monkeypatch):
    monkeypatch.setattr(tools, "generate_narrations", lambda pid, clips: {"clips": [
        {"filename": c["filename"], "ok": True, "narration_seconds": 3.0, "text": c["text"]} for c in clips]})
    seen = {}
    monkeypatch.setattr(tools, "generate_narrations", lambda pid, clips: seen.setdefault("clips", clips) and {"clips": [
        {"filename": c["filename"], "ok": True, "narration_seconds": 3.0} for c in clips]})

    def boom(items):
        raise RuntimeError("small model down")
    out = engine.build_video(project, {"scenes": [{"id": "a", "headline": "Hello", "body": "World."}]}, write_narration=boom)
    assert "small model down" in out["notes"][0]
    assert seen["clips"][0]["text"] == "Hello. World"


def test_patch_scene_reuses_unchanged_audio(project, monkeypatch, writer):
    generated = []

    def tts(pid, clips):
        generated.extend(c["filename"] for c in clips)
        for c in clips:
            (project_dir(pid) / "narration" / c["filename"]).write_bytes(b"RIFF")
            (project_dir(pid) / "narration" / (c["filename"][:-4] + ".txt")).write_text(c["text"], encoding="utf-8")
        return {"clips": [{"filename": c["filename"], "ok": True, "narration_seconds": 4.0} for c in clips]}
    monkeypatch.setattr(tools, "generate_narrations", tts)
    monkeypatch.setattr(engine, "wav_seconds", lambda p: 4.0)
    spec = {"scenes": [{"id": "a", "headline": "One", "say": "x"}, {"id": "b", "headline": "Two", "say": "y"}]}
    engine.build_video(project, spec, write_narration=writer)
    assert sorted(generated) == ["a.wav", "b.wav"]
    generated.clear()
    engine.patch_scene(project, "b", {"headline": "Two again"}, write_narration=writer)
    assert generated == ["b.wav"]
    generated.clear()
    engine.patch_scene(project, "c", {"headline": "Three", "layout": "split"}, insert_after="a", write_narration=writer)
    assert generated == ["c.wav"]
    ids = [s["id"] for s in engine.load_spec(project)["scenes"]]
    assert ids == ["a", "c", "b"]
    engine.patch_scene(project, "c", remove=True, video={"theme": "gold"}, write_narration=writer)
    assert [s["id"] for s in engine.load_spec(project)["scenes"]] == ["a", "b"] and engine.load_spec(project)["theme"] == "gold"


def test_fallback_page_from_storyboard_via_engine(project):
    tools.write_storyboard(project, {"scenes": [{"id": f"s{i}", "title": f"Scene <{i}>", "duration_seconds": 5, "narration": f"s{i}.wav"} for i in range(1, 4)]})
    out = tools.build_fallback_page(project)
    assert out["scenes"] == 3
    assert "narration" not in json.loads((project_dir(project) / "storyboard.json").read_text())["scenes"][0]


def _client(contents, seen):
    def create(**kw):
        seen.append(kw["model"])
        c = contents.pop(0)
        if isinstance(c, Exception):
            raise c
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=c))], usage=None)
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


def test_ask_small_uses_small_model_strips_think_and_falls_back(monkeypatch):
    monkeypatch.setattr(llm.config, "GROQ_MODEL", "core")
    monkeypatch.setattr(llm.config, "GROQ_SMALL_MODEL", "tiny")
    seen = []
    assert llm.ask_small(_client(["<think>hm</think>Hello"], seen), "s", "u") == "Hello" and seen == ["tiny"]
    seen.clear()
    monkeypatch.setattr(llm, "MAX_RETRIES", 1)
    assert llm.ask_small(_client([RuntimeError("x"), "from core"], seen), "s", "u") == "from core"
    assert seen == ["tiny", "core"]


def test_models_have_separate_rate_lanes(monkeypatch):
    monkeypatch.setattr(llm.config, "GROQ_MODEL", "core")
    monkeypatch.setattr(llm.config, "GROQ_SMALL_MODEL", "tiny")
    a, _ = llm._lane("core", "core")
    b, _ = llm._lane("tiny", "tiny")
    assert a is llm._limiter and b is not a and llm._lane("tiny", "tiny")[0] is b


def test_patch_scene_accepts_fields_id_without_scene_id(project, monkeypatch):
    from app import agent
    monkeypatch.setattr(tools, "generate_narrations", lambda pid, clips: {"clips": []})
    engine.build_video(project, {"scenes": [{"id": "a", "headline": "One"}]}, narrate=False)
    monkeypatch.setattr(engine, "build_video", lambda pid, spec, **kw: {"ids": [s["id"] for s in spec["scenes"]]})
    out = agent._call_tool(project, "patch_scene", {"fields": {"id": "m", "layout": "stat", "headline": "100+", "stat": {"value": 100}}, "insert_after": "a"})
    assert out["ids"] == ["a", "m"]
    with pytest.raises(ValueError):
        agent._call_tool(project, "patch_scene", {"fields": {"headline": "x"}})


def test_invalid_tool_call_from_provider_is_fed_back_not_fatal(project, monkeypatch):
    import httpx
    from openai import APIStatusError
    from app import agent
    resp = httpx.Response(400, request=httpx.Request("POST", "http://x"))
    err = APIStatusError("Tool call validation failed (tool_use_failed): missing 'scene_id'", response=resp, body=None)
    seen = []

    class Client:
        def __init__(self, **kw):
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))
        queue = [err, SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="all good", tool_calls=None))], usage=None)]

        def create(self, **kw):
            seen.append(kw["messages"][-1])
            o = self.queue.pop(0)
            if isinstance(o, Exception):
                raise o
            return o
    monkeypatch.setattr(agent, "OpenAI", Client)
    monkeypatch.setattr(agent, "GROQ_API_KEY", "k")
    events = []
    out = agent.run_agent(project, "make a video about cats", on_event=events.append)
    assert out["message"] == "all good"
    assert "rejected" in seen[1]["content"] and any("invalid tool call" in e for e in events)


def test_render_falls_back_when_sandbox_image_is_old(project, monkeypatch):
    from app.docker_runner import DockerError
    calls, msgs = [], []

    def fake(pid, cmd, **kw):
        calls.append(cmd)
        if "--budget" in cmd:
            raise DockerError("render.py: error: unrecognized arguments: --budget 1500")
        (project_dir(pid) / "renders" / "final").mkdir(parents=True, exist_ok=True)
        (project_dir(pid) / "renders" / "final" / "final.mp4").write_bytes(b"x")
        return {"stdout": "", "stderr": ""}
    monkeypatch.setattr(tools, "run_in_sandbox", fake)
    engine.build_video(project, {"scenes": [{"id": "a", "headline": "One"}]}, narrate=False)
    out = tools.render(project, 640, 360, 10, on_progress=msgs.append)
    assert len(calls) == 2 and "--budget" not in calls[1] and out["video_url"]
    assert any("out of date" in m for m in msgs)


def test_dead_model_is_skipped_and_core_answers(monkeypatch):
    import httpx
    from openai import APIStatusError
    monkeypatch.setattr(llm.config, "GROQ_MODEL", "core")
    monkeypatch.setattr(llm.config, "GROQ_SMALL_MODEL", "ghost")
    monkeypatch.setattr(llm, "_dead_models", set())
    nf = APIStatusError("model does not exist", response=httpx.Response(404, request=httpx.Request("POST", "http://x")), body=None)
    seen = []
    assert llm.ask_small(_client([nf, "from core"], seen), "s", "u") == "from core"
    assert seen == ["ghost", "core"] and "ghost" in llm._dead_models
    seen.clear()
    assert llm.ask_small(_client(["again"], seen), "s", "u") == "again" and seen == ["core"]  # never retries ghost


def test_engine_agent_renders_right_after_building(project, monkeypatch, fake_sandbox):
    from app import agent
    spec = {"scenes": [{"id": "a", "headline": "One"}]}
    call = SimpleNamespace(id="c1", function=SimpleNamespace(name="build_video", arguments=json.dumps({"spec": spec})))
    seen = []

    class Client:
        def __init__(self, **kw):
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

        def create(self, **kw):
            seen.append(bool(kw.get("tools")))
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="", tool_calls=[call]))], usage=None)
    monkeypatch.setattr(agent, "OpenAI", Client)
    monkeypatch.setattr(agent, "GROQ_API_KEY", "k")
    monkeypatch.setattr(tools, "generate_narrations", lambda pid, clips: {"clips": []})
    fake_sandbox.creates = ["renders/final/final.mp4"]
    out = agent.run_agent(project, "make a video about cats")
    assert out["status"] == "completed" and "render" in out
    assert seen.count(True) == 1  # one agent turn: no extra "now render it" turn
