import json
import threading
import time
from types import SimpleNamespace

import httpx
import pytest
from openai import RateLimitError

from app import agent, jobs, llm, tools
from app.project import project_dir


# ------------------------------------------------------------------ limiter

class Clock:
    def __init__(self):
        self.t = 1000.0
        self.sleeps = []

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s


def test_limiter_waits_for_the_window_instead_of_failing():
    c = Clock()
    lim = llm.TokenLimiter(tpm=1000, clock=c, sleep=c.sleep)
    lim.acquire(600)
    lim.acquire(300)
    assert c.sleeps == []
    lim.acquire(400)  # 1300 > 1000: must wait for the first entry to age out
    assert c.sleeps and 59 < sum(c.sleeps) < 62


def test_limiter_lets_oversized_request_through_when_window_empty_and_respects_rpm():
    c = Clock()
    lim = llm.TokenLimiter(tpm=100, rpm=2, clock=c, sleep=c.sleep)
    lim.acquire(5000)  # bigger than tpm, but the window is empty
    assert c.sleeps == []
    lim.acquire(1)  # window now holds 5000 -> waits
    assert c.sleeps
    c2 = Clock()
    lim2 = llm.TokenLimiter(rpm=2, clock=c2, sleep=c2.sleep)
    lim2.acquire(1)
    lim2.acquire(1)
    lim2.acquire(1)
    assert c2.sleeps


def test_limiter_settle_uses_real_usage():
    c = Clock()
    lim = llm.TokenLimiter(tpm=1000, clock=c, sleep=c.sleep)
    e = lim.acquire(900)
    lim.settle(e, 100)  # the estimate was far too high
    lim.acquire(800)
    assert c.sleeps == []


@pytest.mark.parametrize("text,expected", [
    ("Please try again in 5.895s.", 5.895),
    ("try again in 1m5.2s", 65.2),
    ("try again in 350ms", 0.35),
    ("rate limited", None),
])
def test_retry_after_parsing(text, expected):
    got = llm.retry_after_seconds(Exception(text))
    assert got == pytest.approx(expected) if expected is not None else got is None


# ------------------------------------------------------------------ complete()

def _rate_limit(msg="Please try again in 2s"):
    req = httpx.Request("POST", "https://api.groq.com/x")
    return RateLimitError(msg, response=httpx.Response(429, request=req), body=None)


def _client(outcomes, seen_models):
    outcomes = list(outcomes)

    def create(**kw):
        seen_models.append(kw["model"])
        o = outcomes.pop(0)
        if isinstance(o, Exception):
            raise o
        return o

    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


def _ok(total=50):
    return SimpleNamespace(
        choices=[], usage=SimpleNamespace(total_tokens=total, prompt_tokens=total - 10, completion_tokens=10)
    )


@pytest.fixture(autouse=True)
def fresh_limiter(monkeypatch):
    monkeypatch.setattr(llm, "_limiter", llm.TokenLimiter())


def test_complete_retries_with_server_hint_and_counts_usage():
    slept, models = [], []
    usage = llm.Usage()
    resp = llm.complete(_client([_rate_limit(), _ok()], models), usage=usage, sleep=slept.append,
                        model="m1", messages=[{"role": "user", "content": "hi"}])
    assert models == ["m1", "m1"]
    assert slept == [2.5]
    d = usage.as_dict()
    assert d["llm_calls"] == 1 and d["rate_limit_retries"] == 1 and d["prompt_tokens"] == 40


def test_complete_falls_back_to_second_model(monkeypatch):
    monkeypatch.setattr(llm.config, "GROQ_FALLBACK_MODEL", "small")
    monkeypatch.setattr(llm, "MAX_RETRIES", 2)
    models = []
    llm.complete(_client([_rate_limit(), _rate_limit(), _ok()], models), sleep=lambda s: None,
                 model="big", messages=[])
    assert models == ["big", "big", "small"]


def test_complete_raises_when_everything_is_exhausted(monkeypatch):
    monkeypatch.setattr(llm, "MAX_RETRIES", 2)
    with pytest.raises(RateLimitError):
        llm.complete(_client([_rate_limit(), _rate_limit()], []), sleep=lambda s: None, model="m", messages=[])


# ------------------------------------------------------------------ agent: compaction + new tools

def _tc(i, name, args):
    return SimpleNamespace(id=i, function=SimpleNamespace(name=name, arguments=json.dumps(args)))


def _resp(content="", calls=None):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=calls))])


def _fake_llm(monkeypatch, script):
    seen, remaining = [], list(script)

    class Client:
        def __init__(self, **kw):
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

        def create(self, **kw):
            seen.append(json.loads(json.dumps(kw["messages"], default=str)))
            return remaining.pop(0)

    monkeypatch.setattr(agent, "OpenAI", Client)
    monkeypatch.setattr(agent, "GROQ_API_KEY", "k")
    return seen


def test_saved_files_are_stubbed_in_later_history(project, monkeypatch):
    big = "<html>" + "x" * 5000 + "</html>"
    seen = _fake_llm(monkeypatch, [
        _resp(calls=[_tc("c1", "write_source", {"relative_path": "index.html", "content": big})]),
        _resp("done"),
    ])
    agent.run_agent(project, "go")
    second_call_history = json.dumps(seen[1])
    assert "xxxxx" not in second_call_history and "[saved" in second_call_history
    assert (project_dir(project) / "source" / "index.html").read_text() == big  # the file itself is intact


def test_failed_writes_keep_full_arguments_so_the_model_can_retry(project, monkeypatch):
    seen = _fake_llm(monkeypatch, [
        _resp(calls=[_tc("c1", "write_source", {"relative_path": "../evil.js", "content": "KEEPME"})]),
        _resp("done"),
    ])
    agent.run_agent(project, "go")
    assert "KEEPME" in json.dumps(seen[1])


def test_progress_events_are_emitted(project, monkeypatch):
    _fake_llm(monkeypatch, [_resp(calls=[_tc("c1", "validate_project", {})]), _resp("fin")])
    events = []
    agent.run_agent(project, "go", on_event=events.append)
    assert any("validate_project" in e for e in events) and any("Thinking" in e for e in events)


def test_read_and_patch_source(project):
    tools.write_source(project, "a.js", "let a = 1;\nlet b = 1;\n")
    assert tools.read_source(project, "a.js")["content"].startswith("let a")
    with pytest.raises(ValueError, match="2 places"):
        tools.patch_source(project, "a.js", "= 1", "= 2")
    tools.patch_source(project, "a.js", "let a = 1", "let a = 9")
    assert "let a = 9" in tools.read_source(project, "a.js")["content"]
    assert tools.patch_source(project, "a.js", "= 1", "= 2", replace_all=True)["replacements"] == 1
    with pytest.raises(ValueError, match="not found"):
        tools.patch_source(project, "a.js", "zzz", "y")
    with pytest.raises(ValueError):
        tools.read_source(project, "../x.js")
    with pytest.raises(FileNotFoundError):
        tools.read_source(project, "nope.js")


def test_generate_narrations_runs_clips_and_reports_each(project, monkeypatch):
    def fake(pid, filename, text, rate=170, volume=1.0):
        if filename == "bad.wav":
            raise RuntimeError("tts exploded")
        return {"path": f"narration/{filename}", "stdout": "x", "narration_seconds": 1.0}

    monkeypatch.setattr(tools, "generate_narration", fake)
    out = tools.generate_narrations(project, [
        {"filename": "a.wav", "text": "hi"}, {"filename": "bad.wav", "text": "x"}, "junk",
    ])["clips"]
    assert [c["ok"] for c in out] == [True, False, False]
    assert "stdout" not in out[0] and "tts exploded" in out[1]["error"]
    with pytest.raises(ValueError):
        tools.generate_narrations(project, [])


# ------------------------------------------------------------------ jobs

def _wait(client, job_id, timeout=5):
    end = time.time() + timeout
    while time.time() < end:
        snap = client.get(f"/jobs/{job_id}").json()
        if snap["status"] in {"done", "error"}:
            return snap
        time.sleep(0.02)
    raise AssertionError("job did not finish")


def test_agent_job_runs_in_background_and_reports_progress(client, project, monkeypatch):
    from app import main

    def fake(pid, instruction, on_event=None):
        on_event("step one")
        return {"status": "completed", "message": f"did {instruction}"}

    monkeypatch.setattr(main, "run_agent", fake)
    r = client.post(f"/projects/{project}/jobs", json={"kind": "agent", "instruction": "x"})
    assert r.status_code == 202
    snap = _wait(client, r.json()["id"])
    assert snap["status"] == "done" and snap["result"]["message"] == "did x"
    assert [e["message"] for e in snap["events"]] == ["step one"]
    # `since` only returns new events
    assert client.get(f"/jobs/{snap['id']}?since={snap['next']}").json()["events"] == []


def test_job_errors_are_captured_not_raised(client, project, monkeypatch):
    from app import main

    def boom(pid, instruction, on_event=None):
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(main, "run_agent", boom)
    jid = client.post(f"/projects/{project}/jobs", json={"kind": "agent"}).json()["id"]
    snap = _wait(client, jid)
    assert snap["status"] == "error" and "model unavailable" in snap["error"]


def test_one_active_job_per_project_and_reattach(client, project, monkeypatch):
    from app import main

    gate = threading.Event()
    monkeypatch.setattr(main, "run_agent", lambda pid, i, on_event=None: gate.wait(5) and {"status": "completed"})
    first = client.post(f"/projects/{project}/jobs", json={"kind": "agent"})
    assert first.status_code == 202
    assert client.post(f"/projects/{project}/jobs", json={"kind": "agent"}).status_code == 409
    assert client.get(f"/projects/{project}/job").json()["id"] == first.json()["id"]
    gate.set()
    _wait(client, first.json()["id"])
    assert client.get(f"/projects/{project}/job").json() == {"status": "idle"}
    assert client.post(f"/projects/{project}/jobs", json={"kind": "agent"}).status_code == 202  # free again


def test_job_endpoint_validation(client, project):
    assert client.post("/projects/" + "0" * 32 + "/jobs", json={"kind": "agent"}).status_code == 404
    assert client.post(f"/projects/{project}/jobs", json={"kind": "other"}).status_code == 422
    assert client.post(f"/projects/{project}/jobs", json={"kind": "render", "width": 101}).status_code == 422
    assert client.get("/jobs/nope").status_code == 404


def test_render_job_goes_through_render(client, project, monkeypatch):
    from app import main

    monkeypatch.setattr(main, "render", lambda pid, w, h, f: {"size_bytes": 1, "w": w, "fps": f})
    jid = client.post(f"/projects/{project}/jobs", json={"kind": "render", "width": 640, "height": 360, "fps": 12}).json()["id"]
    snap = _wait(client, jid)
    assert snap["status"] == "done" and snap["result"] == {"size_bytes": 1, "w": 640, "fps": 12}
