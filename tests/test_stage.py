import json
import re

import pytest

from app import agent, engine, scenes, stage, tools
from app.project import project_dir

POND = {"layout": "stage", "id": "pond", "headline": "A man dancing in a pond", "duration": 8,
        "stage": {"env": "pond", "time": "sunset", "actors": [{"kind": "person", "action": "dance", "style": "disco",
                                                                 "look": {"shirt": "red", "hat": "party"}}]}}


def test_normalize_stage_clamps_and_defaults():
    st = stage.normalize_stage({"env": "mars", "time": "dawn", "actors": [
        {"kind": "alien", "action": "moonwalk", "x": 500, "scale": 9, "tempo": 0, "look": {"shirt": "chartreuse", "skin": "dark", "hat": "tophat"}},
        "junk", {"kind": "robot", "action": "dance"}, {}, {}], "props": ["notes", "ufo", "notes"]})
    assert st["env"] == "meadow" and st["time"] == "day"
    assert len(st["actors"]) == stage.MAX_ACTORS  # junk entry skipped, rest capped
    a = st["actors"][0]
    assert a["kind"] == "person" and a["action"] == "idle" and a["from"] == 95 and a["scale"] == 1.8 and a["tempo"] == .5
    assert a["look"]["hat"] == "none" and a["look"]["skin"] == stage.SKINS["dark"] and a["look"]["shirt"].startswith("#")
    assert st["actors"][1]["style"] == "robot" and "color" in st["actors"][1]["look"]
    assert st["props"] == ["notes"]
    assert stage.normalize_stage(None)["actors"][0]["action"] == "idle"  # never empty, never raises


def test_walkers_always_travel():
    a = stage.normalize_stage({"actors": [{"action": "walk", "x": 50}]})["actors"][0]
    assert a["from"] < a["to"]


def test_color_input_cannot_inject_markup():
    st = stage.normalize_stage({"actors": [{"look": {"shirt": '"/><script>alert(1)</script>', "hair": "red\" onload=\"x"}}]})
    svg = stage.render_stage(st, 5, "x")
    assert "<script" not in svg and "onload" not in svg


@pytest.mark.parametrize("env", stage.ENVS)
@pytest.mark.parametrize("time", stage.TIMES)
def test_every_environment_and_time_renders_valid_svg(env, time):
    import xml.dom.minidom
    st = stage.normalize_stage({"env": env, "time": time, "actors": [{"action": "dance"}]})
    svg = stage.render_stage(st, 6, f"{env}-{time}")
    xml.dom.minidom.parseString(svg)  # well-formed
    assert svg.startswith("<svg") and "p-torso" in svg
    assert svg == stage.render_stage(st, 6, f"{env}-{time}")  # deterministic


def test_every_action_loops_and_has_css():
    css = stage.stage_css()
    for key, d in stage.ACTION_DEFS.items():
        frames = d["frames"]
        assert frames[0][0] == 0 and frames[-1][0] == 100 and frames[0][1] == frames[-1][1], key  # seamless loop
        assert [p for p, _ in frames] == sorted(p for p, _ in frames), key
        assert f"@keyframes st-{key}-" in css
    # every animation is scoped so it restarts with its scene
    for line in css.splitlines():
        if "animation:" in line and not line.startswith("@keyframes"):
            assert line.startswith(stage.SCOPE.strip()), line[:80]
    assert "dance-disco" in css and "dance-robot" in css


def test_right_side_is_mirrored_and_pose_is_baked():
    assert stage._rot("armR", {"armR": 150}) == -150 and stage._rot("armL", {"armL": 150}) == 150
    assert "rotate(150" in stage.character_svg({"kind": "person", "look": stage.normalize_stage({})["actors"][0]["look"]}, {"armL": 150})


def test_stage_scene_compiles_and_is_scoped():
    spec = scenes.normalize_spec({"scenes": [POND]})
    sc = spec["scenes"][0]
    assert sc["layout"] == "stage" and sc["stage"]["env"] == "pond"
    page = scenes.compile_html(spec, durations={"pond": 8})
    assert 'class="scene stage"' in page and 'class="st-cap' in page and "A man dancing in a pond" in page
    assert page.count("<style>") == 1 and "a-dance-disco" in page and "--dur:8s" in page
    assert "st-ripple" in page  # wading ripples at the feet
    assert 'data-scene="pond" data-transition' in page and 'data-cam="none"' in page.split('data-scene="pond"')[1].split(">")[0]
    assert ".scene.stage .cam{position:absolute;inset:0" in page  # fx.js wraps content in .cam; it must fill the scene
    plain = scenes.compile_html(scenes.normalize_spec({"scenes": [{"headline": "Hi"}]}))
    assert "<style>" not in plain  # no stage css when unused


def test_stage_needs_no_headline_and_accepts_flat_fields():
    spec = scenes.normalize_spec({"scenes": [{"layout": "stage", "env": "space", "actors": [{"kind": "robot", "action": "wave"}]}]})
    assert spec["scenes"][0]["stage"]["env"] == "space" and spec["scenes"][0]["stage"]["actors"][0]["kind"] == "robot"
    page = scenes.compile_html(spec)
    assert 'class="st-cap' not in page


def test_caption_can_be_turned_off():
    sc = dict(POND, stage=dict(POND["stage"], caption=False))
    assert 'class="st-cap' not in scenes.compile_html(scenes.normalize_spec({"scenes": [sc]}))


def test_stage_scenes_are_silent_unless_asked(project, monkeypatch):
    seen = []
    monkeypatch.setattr(tools, "generate_narrations", lambda pid, clips: seen.extend(c["filename"] for c in clips) or {"clips": [
        {"filename": c["filename"], "ok": True, "narration_seconds": 4.0} for c in clips]})
    asked = []
    out = engine.build_video(project, {"scenes": [
        POND, dict(POND, id="talk", say="He dances because the water is warm"), {"id": "end", "headline": "The end"}]},
        write_narration=lambda items: asked.append([i["id"] for i in items]) or {i["id"]: "Words." for i in items})
    assert asked == [["talk", "end"]] and sorted(seen) == ["end.wav", "talk.wav"]
    board = {b["id"]: b for b in json.loads((project_dir(project) / "storyboard.json").read_text(encoding="utf-8"))["scenes"]}
    assert "narration" not in board["pond"] and board["pond"]["duration_seconds"] == 8
    page = (project_dir(project) / "source" / "index.html").read_text(encoding="utf-8")
    assert "--dur:8s" in page and out["layouts"][0] == "stage"


def test_patch_scene_merges_stage_tweaks(project, monkeypatch):
    monkeypatch.setattr(tools, "generate_narrations", lambda pid, clips: {"clips": []})
    engine.build_video(project, {"scenes": [POND]})
    engine.patch_scene(project, "pond", {"stage": {"time": "night", "props": ["fireflies"]}})
    st = engine.load_spec(project)["scenes"][0]["stage"]
    assert st["time"] == "night" and st["props"] == ["fireflies"]
    assert st["env"] == "pond" and st["actors"][0]["action"] == "dance"  # untouched fields survive


def test_character_requests_route_to_the_engine_with_stage_docs():
    for text in ("animation video of a man dancing in a pond", "a cartoon mascot waving", "character animation of a robot",
                 "an animated explainer about bees"):
        assert agent.is_engine_mode(text), text
        sys = agent.build_system(text)
        assert 'layout "stage"' in sys and "pond" in sys and "dance" in sys and "build_video" in sys
    assert not agent.is_engine_mode("a calculus lesson") and not agent.is_engine_mode("a three.js scene")
    assert len(agent.build_system("a video about cats")) < 9000  # the prompt stays small
