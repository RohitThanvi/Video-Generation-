"""Scene engine: compile a compact scene spec into a finished kit + fx page.

The model should not re-invent layout, timing or markup for every video. It makes the creative
decisions (look, story, which visual, what moves) as a small JSON spec; this module turns that
into the page deterministically using tested components, and times every reveal to the narration.

Spec (all fields optional except scenes[].headline unless noted):
{
  "theme": "blue|sunset|emerald|crimson|gold|mono", "bg": "particles|network|stars|aurora|grid|waves|rain|none",
  "post": "vignette grain",
  "scenes": [{
     "id": "intro", "layout": "title|split|stat|chart|list|steps|quote|photo|compare|flow|orbit|timeline|kinetic|stage|custom",
     "headline": "...", "kicker": "...", "body": "...", "say": "talking points for the narration",
     "narration": "exact spoken text (else written from `say`)", "duration": 6,
     "visual": {"type": "hero3d|bars|donut|line|photo|blob|icon|number|none", ...type fields},
     "items": [{"title","text","icon"}],            # list / steps
     "stat": {"value": 87, "prefix": "", "suffix": "%", "label": "..."},   # stat
     "quote": "...", "by": "...",                   # quote
     "left": {"title","items":[...]}, "right": {...},  # compare
     "stage": {...},                                # stage: animated environment + character (see app/stage.py)
     "items": [...], "edges": [[0,1]], "shape": "line|cycle|hub", "center": {...}, "lines": [...]   # flow / orbit / timeline / kinetic (app/motion.py)
     "html": "<raw inner html>", "css": "...",      # custom (escape hatch, full creative freedom)
     "transition": "zoom|slide|wipe|curtain|slice|flip|blur|spin|rise|glitch", "kin": "rise|drop|blur|flip|wave|zoom|spin|glitch",
     "cam": "push|pull|left|right|drift|tilt|none"}]
}
"""
import html as _html
import re

from . import motion as motion_mod
from . import stage as stage_mod

THEMES = ("blue", "sunset", "emerald", "crimson", "gold", "mono")
BGS = ("particles", "network", "stars", "aurora", "grid", "waves", "rain", "none")
LAYOUTS = ("title", "split", "stat", "chart", "list", "steps", "quote", "photo", "compare", "flow", "orbit", "timeline", "kinetic", "stage", "custom")
SHAPES = ("torusknot", "icosa", "globe", "crystal", "rings")
VISUALS = ("hero3d", "bars", "donut", "line", "photo", "blob", "icon", "number", "none")
TRANSITIONS = ("zoom", "slide", "wipe", "curtain", "slice", "flip", "blur", "spin", "rise", "glitch")
KINS = ("rise", "drop", "blur", "flip", "wave", "zoom", "spin", "glitch")
CAMS = ("push", "pull", "left", "right", "drift", "tilt", "none")
ICONS = ("rocket", "globe", "moon", "sun", "star", "zap", "clock", "check", "arrow", "chart", "users",
         "lightbulb", "target", "layers", "shield", "flag", "play", "satellite")
ENTRANCES = ("fade-in-up", "scale-up", "slide-in-left", "slide-in-right", "pop", "blur-in")
MAX_SCENES = 12
MAX_HEROES = 3  # real WebGL is the most expensive thing to render in software

_ID = re.compile(r"[^a-z0-9_-]+")
esc = _html.escape


def _text(value, limit=240) -> str:
    return " ".join(str(value or "").split())[:limit]


def _num(value, default=0.0):
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return default


def _numbers(values, limit=8):
    if isinstance(values, str):
        values = re.split(r"[,\s]+", values.strip())
    out = [_num(v, None) for v in (values or [])][:limit]
    return [v for v in out if v is not None]


def _pick(value, allowed, default=None):
    value = str(value or "").strip().lower()
    return value if value in allowed else default


def _fmt(n: float) -> str:
    return str(int(n)) if float(n).is_integer() else f"{n:g}"


def _items(raw, limit=6):
    out = []
    for it in (raw or [])[:limit]:
        if isinstance(it, str):
            it = {"title": it}
        if isinstance(it, dict) and (it.get("title") or it.get("text")):
            out.append({"title": _text(it.get("title"), 60), "text": _text(it.get("text"), 120),
                        "icon": _pick(it.get("icon"), ICONS)})
    return out


def normalize_spec(spec) -> dict:
    """Validate and complete a spec. Never trusts the model: unknown values fall back to defaults."""
    if isinstance(spec, str):
        import json
        spec = json.loads(spec)
    if not isinstance(spec, dict):
        raise ValueError("spec must be a JSON object with a 'scenes' list.")
    raw_scenes = spec.get("scenes")
    if not isinstance(raw_scenes, list) or not raw_scenes:
        raise ValueError("spec.scenes must be a non-empty list.")
    scenes, seen, heroes = [], set(), 0
    for i, raw in enumerate(raw_scenes[:MAX_SCENES]):
        if not isinstance(raw, dict):
            raise ValueError(f"scenes[{i}] must be an object.")
        sid = _ID.sub("-", str(raw.get("id") or f"scene-{i + 1}").lower()).strip("-") or f"scene-{i + 1}"
        while sid in seen:
            sid += "-x"
        seen.add(sid)
        layout = _pick(raw.get("layout"), LAYOUTS, "split" if i else "title")
        sc = {
            "id": sid, "layout": layout,
            "headline": _text(raw.get("headline") or raw.get("title"), 90),
            "kicker": _text(raw.get("kicker"), 50), "body": _text(raw.get("body"), 260),
            "say": _text(raw.get("say"), 400), "narration": _text(raw.get("narration"), 900),
            "duration": min(40.0, max(3.0, _num(raw.get("duration"), 6.0))),
            "transition": _pick(raw.get("transition"), TRANSITIONS),
            "kin": _pick(raw.get("kin"), KINS), "cam": _pick(raw.get("cam"), CAMS),
        }
        if layout not in ("custom", "quote", "stage") and not sc["headline"]:
            raise ValueError(f"scenes[{i}] ({sid}) needs a headline.")
        vis = raw.get("visual") if isinstance(raw.get("visual"), dict) else {}
        vtype = _pick(vis.get("type"), VISUALS)
        if vtype is None:  # a scene without a hero visual is a slide: pick one by layout
            vtype = {"photo": "photo", "stat": "none", "list": "none", "steps": "none", "compare": "none",
                     "quote": "blob", "chart": "bars", "custom": "none", "stage": "none",
                     "flow": "none", "orbit": "none", "timeline": "none", "kinetic": "none"}.get(layout, "hero3d" if i % 2 == 0 else "blob")
        if vtype == "hero3d":
            if heroes >= MAX_HEROES:
                vtype = "blob"
            else:
                heroes += 1
        v = {"type": vtype}
        if vtype == "hero3d":
            v["shape"] = _pick(vis.get("shape"), SHAPES, SHAPES[heroes % len(SHAPES)])
        elif vtype in ("bars", "line"):
            v["values"] = _numbers(vis.get("values")) or [3, 5, 4, 8]
            v["labels"] = [_text(x, 14) for x in (vis.get("labels") or [])][: len(v["values"])]
            v["suffix"] = _text(vis.get("suffix"), 6)
        elif vtype == "donut":
            v.update(value=min(100.0, max(0.0, _num(vis.get("value"), 70))), suffix=_text(vis.get("suffix") or "%", 6),
                     label=_text(vis.get("label"), 30))
        elif vtype == "photo":
            v.update(query=_text(vis.get("query") or sc["headline"], 80), file=_text(vis.get("file"), 120))
        elif vtype == "icon":
            v["name"] = _pick(vis.get("name"), ICONS, "star")
        elif vtype == "number":
            v.update(value=_num(vis.get("value"), 0), prefix=_text(vis.get("prefix"), 4),
                     suffix=_text(vis.get("suffix"), 8), label=_text(vis.get("label"), 40))
        sc["visual"] = v
        sc["items"] = _items(raw.get("items"))
        st = raw.get("stat") if isinstance(raw.get("stat"), dict) else {}
        if layout == "stat":
            sc["stat"] = {"value": _num(st.get("value"), 0), "prefix": _text(st.get("prefix"), 4),
                          "suffix": _text(st.get("suffix"), 8), "label": _text(st.get("label"), 60)}
        if layout == "quote":
            sc["quote"] = _text(raw.get("quote") or sc["headline"], 200)
            sc["by"] = _text(raw.get("by"), 60)
        if layout == "compare":
            for side in ("left", "right"):
                d = raw.get(side) if isinstance(raw.get(side), dict) else {}
                sc[side] = {"title": _text(d.get("title"), 40), "items": _items(d.get("items"), 4)}
        if layout in motion_mod.LAYOUTS:
            sc["motion"] = motion_mod.normalize_motion(layout, dict(raw, headline=sc["headline"]), ICONS)
        if layout == "stage":
            st = raw.get("stage")
            if not isinstance(st, dict):  # tolerate a model that put env/actors straight on the scene
                st = {k: raw[k] for k in ("env", "environment", "time", "actors", "props", "caption") if k in raw}
            sc["stage"] = stage_mod.normalize_stage(st)
        if layout == "custom":
            sc["html"] = str(raw.get("html") or "")[:20000]
            sc["css"] = str(raw.get("css") or "")[:6000]
            if not sc["html"].strip():
                raise ValueError(f"scenes[{i}] ({sid}) is layout 'custom' but has no html.")
        scenes.append(sc)
    seed = sum(ord(c) for c in str(spec.get("title") or scenes[0]["headline"]))
    return {
        "title": _text(spec.get("title") or scenes[0]["headline"], 80) or "Video",
        "theme": _pick(spec.get("theme"), THEMES, THEMES[seed % 5]),
        "bg": _pick(spec.get("bg"), BGS, BGS[(seed // 5) % 5]),
        "post": " ".join(p for p in str(spec.get("post") or "vignette").split() if p in ("vignette", "grain", "leak", "scan")) or "vignette",
        "scenes": scenes,
    }


class _Clock:
    """Hands out reveal delays that spread across the narration, so items appear as they are said."""

    def __init__(self):
        self.n = 0

    def tag(self) -> str:
        self.n += 1
        return f"@@D{self.n - 1}@@"

    def resolve(self, markup: str, narration_seconds: float | None) -> str:
        start = 0.3
        end = max(2.0, (narration_seconds + 0.6) * 0.78) if narration_seconds else 2.6 + 0.25 * self.n
        step = (end - start) / max(1, self.n - 1)
        return re.sub(r"@@D(\d+)@@", lambda m: f"{start + int(m.group(1)) * step:.2f}s", markup)


def _visual(v: dict, clock: _Clock, size: int, image_for=None) -> str:
    d = clock.tag()
    t = v["type"]
    if t == "hero3d":
        return f'<div class="hero3d pop" data-shape="{v["shape"]}" style="--size:{size}px;--d:{d}"></div>'
    if t == "bars":
        labels = ",".join(v["labels"]) if v["labels"] else ",".join(f"#{i + 1}" for i in range(len(v["values"])))
        return (f'<div class="fx-bars" data-values="{",".join(_fmt(x) for x in v["values"])}" data-labels="{esc(labels)}" '
                f'data-suffix="{esc(v["suffix"])}" style="--h:{int(size * .8)}px;width:{int(size * 1.2)}px;--d:{d}"></div>')
    if t == "line":
        return (f'<div class="fx-line" data-values="{",".join(_fmt(x) for x in v["values"])}" '
                f'style="--w:{int(size * 1.2)}px;--h:{int(size * .7)}px;width:{int(size * 1.2)}px;height:{int(size * .7)}px;--d:{d}"></div>')
    if t == "donut":
        return (f'<div class="fx-donut" data-value="{_fmt(v["value"])}" data-suffix="{esc(v["suffix"])}" '
                f'data-label="{esc(v["label"])}" style="--size:{int(size * .75)}px;--d:{d}"></div>')
    if t == "photo":
        img = image_for(v) if image_for else None
        if img:
            return (f'<div class="photo scale-up" style="--d:{d};width:{int(size * 1.35)}px;height:{int(size * 1.0)}px;flex:none">'
                    f'<img class="kb" src="../assets/images/{esc(img)}" alt=""></div>')
        return f'<div class="blob float" style="width:{int(size * .8)}px;height:{int(size * .8)}px;--d:{d}"></div>'
    if t == "icon":
        return f'<div class="icon-badge pop breathe" style="--d:{d};width:{int(size * .5)}px;height:{int(size * .5)}px;font-size:{int(size * .28)}px"><i data-icon="{v["name"]}"></i></div>'
    if t == "number":
        suffix = f' data-suffix="{esc(v["suffix"])}"' if v["suffix"] else ""
        prefix = f' data-prefix="{esc(v["prefix"])}"' if v["prefix"] else ""
        label = f'<div class="label fade-in-up" style="--d:{clock.tag()}">{esc(v["label"])}</div>' if v["label"] else ""
        return (f'<div class="col" style="align-items:center;gap:8px"><span class="big-number pop" data-count="{_fmt(v["value"])}" data-group="0"'
                f'{prefix}{suffix} style="--d:{d};font-size:{int(size * .5)}px"></span>{label}</div>')
    if t == "blob":
        return f'<div class="blob float" style="width:{int(size * .8)}px;height:{int(size * .8)}px;--d:{d}"></div>'
    return ""


def _text_block(sc: dict, clock: _Clock, big: bool, first: bool = False, bullets: bool = True) -> str:
    kin = sc["kin"] or KINS[sum(map(ord, sc["id"])) % len(KINS)]
    parts = []
    if sc["kicker"]:
        parts.append(f'<div class="eyebrow fade-in-down" style="--d:{clock.tag()}">{esc(sc["kicker"])}</div>')
    if sc["headline"]:
        tag, cls = ("h1", "title") if big else ("h2", "h2")
        mode = "chars" if big and len(sc["headline"]) <= 14 else "words"
        parts.append(f'<{tag} class="{cls}" data-split="{mode}" data-kin="{kin}" style="--d:{clock.tag()};--step:.1s">{esc(sc["headline"])}</{tag}>')
    if sc["body"]:
        parts.append(f'<p class="{"subtitle" if big else "body"} fade-in-up" style="--d:{clock.tag()}">{esc(sc["body"])}</p>')
    return "".join(parts)


def _cards(items, clock, numbered=False) -> str:
    cols = "grid-3" if len(items) in (3, 5, 6) else "grid-2" if len(items) in (2, 4) else "grid-3"
    cards = []
    for i, it in enumerate(items):
        ent = ENTRANCES[(i + 1) % len(ENTRANCES)]
        badge = (f'<div class="icon-badge{" teal" if i % 2 else ""}" style="font-size:44px;font-weight:800">{i + 1}</div>' if numbered
                 else f'<div class="icon-badge{" teal" if i % 3 == 1 else " amber" if i % 3 == 2 else ""}"><i data-icon="{it["icon"] or ICONS[(i * 5) % len(ICONS)]}"></i></div>')
        cards.append(f'<div class="glass {ent}" style="--d:{clock.tag()}">{badge}<h3>{esc(it["title"])}</h3>'
                     + (("<p>" + esc(it["text"]) + "</p>") if it["text"] else "") + "</div>")
    return f'<div class="{cols}">{"".join(cards)}</div>'


def _scene_html(sc: dict, index: int, total: int, image_for, transition: str, clock: _Clock, duration: float | None = None) -> str:
    layout, v = sc["layout"], sc["visual"]
    side_text = lambda big=False: f'<div class="col grow">{_text_block(sc, clock, big)}</div>'
    if layout == "stage":
        st = sc["stage"]
        inner = stage_mod.render_stage(st, duration or sc["duration"], sc["id"])
        if sc["headline"] and st["caption"]:
            inner += f'<div class="st-cap fade-in-up" style="--d:.5s">{esc(sc["headline"])}</div>'
        cls = "scene stage"
    elif layout in motion_mod.LAYOUTS:
        kin = sc["kin"] or KINS[sum(map(ord, sc["id"])) % len(KINS)]
        inner = motion_mod.render_motion(sc, clock, _text_block(sc, clock, layout == "kinetic"), kin)
        cls = "scene motion"
    elif layout == "custom":
        inner = sc["html"]
        if sc["css"]:
            inner = f"<style>{sc['css']}</style>" + inner
        cls = "scene"
    elif layout == "title":
        vis = _visual(v, clock, 380, image_for)
        inner = f'<div class="col" style="align-items:center;gap:16px">{vis}{_text_block(sc, clock, True)}</div>'
        cls = "scene center"
    elif layout == "stat":
        st = sc["stat"]
        pre = f' data-prefix="{esc(st["prefix"])}"' if st["prefix"] else ""
        suf = f' data-suffix="{esc(st["suffix"])}"' if st["suffix"] else ""
        dec = f' data-decimals="{len(_fmt(st["value"]).split(".")[1])}"' if "." in _fmt(st["value"]) else ""
        head = _text_block(sc, clock, False)
        num = (f'<span class="big-number pop" data-count="{_fmt(st["value"])}" data-group="0"{pre}{suf}{dec} '
               f'style="--d:{clock.tag()};font-size:300px;line-height:1"></span>')
        label = f'<div class="label fade-in-up" style="--d:{clock.tag()};font-size:40px">{esc(st["label"])}</div>' if st["label"] else ""
        inner = f'<div class="col" style="align-items:center;gap:10px">{head}{num}{label}</div>'
        cls = "scene center"
    elif layout == "quote":
        kin = sc["kin"] or "blur"
        by = f'<div class="label fade-in-up" style="--d:{clock.tag()}">{esc(sc["by"])}</div>' if sc["by"] else ""
        inner = (f'<div class="col" style="align-items:center;gap:30px;max-width:1500px"><div class="h2 fx-gradient" data-split="words" data-kin="{kin}" '
                 f'style="--d:{clock.tag()};--step:.08s;text-align:center">“{esc(sc["quote"])}”</div>{by}</div>')
        cls = "scene center"
    elif layout in ("list", "steps"):
        inner = f'{_text_block(sc, clock, False)}{_cards(sc["items"], clock, numbered=layout == "steps")}'
        cls = "scene"
    elif layout == "compare":
        sides = []
        for k, ent in (("left", "slide-in-left"), ("right", "slide-in-right")):
            side = sc[k]
            lines = "".join(f'<p style="margin-top:14px">• {esc(it["title"] or it["text"])}</p>' for it in side["items"])
            sides.append(f'<div class="glass {ent}" style="--d:{clock.tag()}"><h3>{esc(side["title"])}</h3>{lines}</div>')
        inner = f'{_text_block(sc, clock, False)}<div class="grid-2">{"".join(sides)}</div>'
        cls = "scene"
    elif layout == "chart":
        inner = f'<div class="row"><div class="col" style="width:640px;flex:none">{_text_block(sc, clock, False)}</div>{_visual(v, clock, 560, image_for)}</div>'
        cls = "scene"
    else:  # split / photo
        size = 560 if layout == "split" else 560
        vis = _visual(v, clock, size, image_for)
        parts = [side_text(), vis] if index % 2 == 0 else [vis, side_text()]
        inner = f'<div class="row">{"".join(p for p in parts if p)}</div>'
        cls = "scene"
    cam = "none" if layout == "stage" else sc["cam"]  # the stage has its own slow push-in
    attrs = f' data-transition="{transition}"' + (f' data-cam="{cam}"' if cam else "")
    return f'<section class="{cls}" data-scene="{esc(sc["id"])}"{attrs}>{inner}</section>'


def compile_html(spec: dict, narration_seconds: dict | None = None, image_for=None, durations: dict | None = None) -> str:
    """spec must come from normalize_spec. narration_seconds: {scene id: seconds} to time the reveals.
    durations: {scene id: final seconds}, so stage scenes can spread their movement over the whole scene."""
    narration_seconds = narration_seconds or {}
    sections, last = [], None
    for i, sc in enumerate(spec["scenes"]):
        trans = sc["transition"] or next(t for t in (TRANSITIONS[(i * 3 + 1) % len(TRANSITIONS)], "zoom", "blur") if t != last)
        last = trans
        clock = _Clock()
        markup = _scene_html(sc, i, len(spec["scenes"]), image_for, trans, clock, (durations or {}).get(sc["id"]))
        sections.append(clock.resolve(markup, narration_seconds.get(sc["id"])))
    used = {sc["layout"] for sc in spec["scenes"]}
    stage_style = (f"<style>{stage_mod.stage_css()}</style>" if "stage" in used else "") + \
                  (f"<style>{motion_mod.motion_css()}</style>" if used & set(motion_mod.LAYOUTS) else "")
    return (
        '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
        f'<title>{esc(spec["title"])}</title>'
        f'<link rel="stylesheet" href="kit.css"><link rel="stylesheet" href="fx.css">{stage_style}</head>\n'
        f'<body data-theme="{spec["theme"]}" data-bg="{spec["bg"]}" data-post="{spec["post"]}">\n'
        '<div class="bg"><i class="orb o1"></i><i class="orb o2"></i><i class="orb o3"></i></div>\n'
        + "\n".join(sections)
        + '\n<div class="progress"></div>\n<script src="kit.js"></script><script src="fx.js"></script></body></html>\n'
    )
