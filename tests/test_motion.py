import re

import pytest

from app import agent, engine, motion, scenes
from app.project import project_dir

FLOW = {"id": "f", "layout": "flow", "headline": "How it works", "shape": "cycle", "duration": 7,
        "items": [{"title": "Build"}, {"title": "Measure"}, {"title": "Learn"}]}
ORBIT = {"id": "o", "layout": "orbit", "headline": "Ecosystem", "center": {"title": "Web"},
         "items": [{"title": t} for t in ("A", "B", "C", "D", "E")]}
TIMELINE = {"id": "t", "layout": "timeline", "headline": "History",
            "items": [{"label": "1969", "title": "ARPANET"}, {"label": "1991", "title": "The Web"}]}
KINETIC = {"id": "k", "layout": "kinetic", "headline": "Stop reading slides", "lines": ["Move.", "Show.", "Tell."]}


def page(*scs, **kw):
    return scenes.compile_html(scenes.normalize_spec({"scenes": list(scs), **kw}))


def test_limits_and_errors():
    with pytest.raises(ValueError, match="at least 2 items"):
        scenes.normalize_spec({"scenes": [dict(FLOW, items=[{"title": "only"}])]})
    big = scenes.normalize_spec({"scenes": [dict(FLOW, items=[{"title": str(i)} for i in range(20)])]})
    assert len(big["scenes"][0]["motion"]["items"]) == motion.LIMITS["flow"][1]
    k = scenes.normalize_spec({"scenes": [dict(KINETIC, lines=["a"] * 9)]})["scenes"][0]
    assert len(k["motion"]["lines"]) == 4


def test_flow_edges_validated_and_default():
    m = scenes.normalize_spec({"scenes": [dict(FLOW, edges=[[0, 9], [1, 1], "x", [0, 1]])]})["scenes"][0]["motion"]
    assert m["edges"] == [[0, 1]] or m["edges"] == [(0, 1)]
    cyc = scenes.normalize_spec({"scenes": [FLOW]})["scenes"][0]["motion"]
    assert len(cyc["edges"]) == 3  # a cycle closes


def test_all_layouts_render_resolved_and_deterministic():
    for sc in (FLOW, ORBIT, TIMELINE, KINETIC):
        a, b = page(sc), page(sc)
        assert a == b
        assert "@@D" not in a
        assert 'class="scene motion' in a


def test_escaping():
    evil = '<img src=x onerror=alert(1)>'
    h = page(dict(FLOW, items=[{"title": evil}, {"title": evil + "2"}]), dict(TIMELINE, id="t2", items=[{"label": evil, "title": evil}, {"title": "b"}]))
    assert "<img src=x" not in h and "&lt;img" in h


def test_css_only_when_used_and_scoped():
    plain = page({"id": "a", "layout": "title", "headline": "Hi"})
    assert "mo-orbit" not in plain and "mo-travel" not in plain
    assert "mo-orbit" in page(ORBIT)
    css = motion.motion_css()
    for m in re.finditer(r"animation:\s*mo-", css):
        rule = css[css.rfind("}", 0, m.start()) + 1:m.start()]
        assert ".scene.is-active" in rule, rule[-80:]


def test_prompt_documents_motion_layouts():
    prompt = agent._ENGINE
    for name in ("flow", "orbit", "timeline", "kinetic"):
        assert f"- {name}:" in prompt
    assert "at most once per video" in prompt


def test_engine_builds_motion_project(project):
    engine.build_video(project, {"scenes": [KINETIC, FLOW, ORBIT, TIMELINE]}, narrate=False)
    html = (project_dir(project) / "source" / "index.html").read_text(encoding="utf-8")
    assert html.count('class="scene motion') == 4
