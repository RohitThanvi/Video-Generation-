"""Stage layout: a full-bleed animated SVG scene with a rigged vector character.

The model never draws anything. It names an environment, a time of day, one to three actors, an
action and some props; this module turns that into inline SVG plus CSS keyframes. Everything is
deterministic (seeded by the scene id) and every animation is scoped to `.scene.is-active` /
`.scene.is-leaving`, so it restarts whenever the scene starts. The renderer's virtual clock drives
CSS animations, so nothing here needs JavaScript.

Stage spec (all optional):
{"env": "pond|meadow|beach|city|space|disco|forest", "time": "day|sunset|night",
 "actors": [{"kind": "person|robot", "action": "idle|wave|cheer|jump|walk|march|clap|dance",
             "style": "disco|hiphop|robot|groove",   # dance only
             "x": 50, "from": 20, "to": 80,           # % of the width; from/to make it travel
             "scale": 1.0, "tempo": 1.0,
             "look": {"shirt": "red", "pants": "#223", "skin": "tan", "hair": "#432", "hair_style":
                      "short|long|bun|none", "hat": "none|cap|party|crown", "glasses": false,
                      "color": "silver", "accent": "cyan"}}],     # color/accent: robot
 "props": ["notes", "sparkles", "fish", "butterflies", "confetti", "fireflies"], "caption": true}
"""
import math
import re

ENVS = ("pond", "meadow", "beach", "city", "space", "disco", "forest")
TIMES = ("day", "sunset", "night")
KINDS = ("person", "robot")
ACTIONS = ("idle", "wave", "cheer", "jump", "walk", "march", "clap", "dance")
DANCE_STYLES = ("disco", "hiphop", "robot", "groove")
PROPS = ("notes", "sparkles", "fish", "butterflies", "confetti", "fireflies")
HAIR_STYLES = ("short", "long", "bun", "none")
HATS = ("none", "cap", "party", "crown")
MAX_ACTORS = 3
W, H = 1920, 1080
GROUND = {"pond": 880, "meadow": 930, "beach": 930, "city": 950, "space": 930, "disco": 940, "forest": 950}

COLORS = {
    "red": "#e5484d", "blue": "#3b82f6", "green": "#22a06b", "yellow": "#f5c518", "orange": "#f97316",
    "purple": "#8b5cf6", "pink": "#ec6ab0", "black": "#1f2430", "white": "#f4f4f5", "teal": "#14b8a6",
    "gray": "#8a93a3", "grey": "#8a93a3", "brown": "#7a4a2b", "navy": "#1e3a8a", "silver": "#b8c2d0",
    "gold": "#e0b43c", "cyan": "#22d3ee", "lime": "#84cc16", "blonde": "#e8c468", "ginger": "#c2571a",
}
SKINS = {"light": "#f6d3b8", "tan": "#e0a982", "brown": "#a8693f", "dark": "#6b4226"}
_HEX = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


# ---------------------------------------------------------------------------------- normalizing

def _pick(value, allowed, default):
    value = str(value or "").strip().lower()
    return value if value in allowed else default


def _num(value, default, lo, hi):
    try:
        v = float(str(value).replace("%", "").strip())
    except (TypeError, ValueError):
        return default
    return min(hi, max(lo, v))


def _color(value, default, table=COLORS):
    v = str(value or "").strip()
    if _HEX.match(v):
        return v
    return table.get(v.lower(), default)


def _look(raw, kind):
    raw = raw if isinstance(raw, dict) else {}
    out = {
        "shirt": _color(raw.get("shirt"), "#3b82f6"), "pants": _color(raw.get("pants"), "#334155"),
        "skin": SKINS.get(str(raw.get("skin") or "").strip().lower()) or _color(raw.get("skin"), SKINS["tan"]),
        "hair": _color(raw.get("hair"), "#3a2a20"), "hair_style": _pick(raw.get("hair_style"), HAIR_STYLES, "short"),
        "hat": _pick(raw.get("hat"), HATS, "none"), "glasses": bool(raw.get("glasses")),
        "shoes": _color(raw.get("shoes"), "#f4f4f5"),
    }
    if kind == "robot":
        out["color"] = _color(raw.get("color"), COLORS["silver"])
        out["accent"] = _color(raw.get("accent"), COLORS["cyan"])
    return out


def normalize_stage(raw) -> dict:
    """Validate a stage spec; unknown values fall back to defaults. Never raises on model mistakes."""
    raw = raw if isinstance(raw, dict) else {}
    actors = []
    given = [a for a in (raw.get("actors") if isinstance(raw.get("actors"), list) else []) if isinstance(a, dict)]
    for a in given[:MAX_ACTORS]:
        kind = _pick(a.get("kind"), KINDS, "person")
        action = _pick(a.get("action"), ACTIONS, "idle")
        x = _num(a.get("x"), 50, 5, 95)
        frm, to = _num(a.get("from"), x, -15, 115), _num(a.get("to"), x, -15, 115)
        actors.append({
            "kind": kind, "action": action,
            "style": _pick(a.get("style"), DANCE_STYLES, "robot" if kind == "robot" else "disco"),
            "from": frm, "to": to, "scale": _num(a.get("scale"), 1.0, .5, 1.8), "tempo": _num(a.get("tempo"), 1.0, .5, 2.0),
            "look": _look(a.get("look"), kind),
        })
    if not actors:
        actors = [{"kind": "person", "action": "idle", "style": "disco", "from": 50.0, "to": 50.0, "scale": 1.0,
                   "tempo": 1.0, "look": _look({}, "person")}]
    for a in actors:  # a walker with nowhere to go is just marching on the spot: that is fine, but never silent
        if a["action"] in ("walk", "march") and a["from"] == a["to"]:
            a["from"], a["to"] = max(-10, a["from"] - 30), min(110, a["to"] + 30)
    env = _pick(raw.get("env") or raw.get("environment"), ENVS, "meadow")
    time = _pick(raw.get("time"), TIMES, "day")
    props = raw.get("props")
    if isinstance(props, list):
        props = [p for p in dict.fromkeys(_pick(p, PROPS, None) for p in props) if p]
    else:
        props = None  # not specified: choose sensible ones
    return {"env": env, "time": time, "actors": actors, "props": props, "caption": raw.get("caption") is not False}


def default_props(stage: dict) -> list:
    env, time = stage["env"], stage["time"]
    base = {"pond": ["fish", "sparkles"], "meadow": ["butterflies"], "forest": ["fireflies"] if time == "night" else ["butterflies"],
            "disco": ["sparkles", "confetti"], "space": ["sparkles"], "beach": [], "city": []}[env]
    if any(a["action"] == "dance" for a in stage["actors"]):
        base = base + ["notes"]
    return base


# ------------------------------------------------------------------------------------ randomness

class Rng:
    def __init__(self, seed: str):
        self.s = (sum((i + 1) * ord(c) for i, c in enumerate(seed)) * 2654435761 + 12345) % 2**32

    def r(self) -> float:
        self.s = (1103515245 * self.s + 12345) % 2**31
        return self.s / 2**31

    def between(self, a, b) -> float:
        return a + (b - a) * self.r()

    def choice(self, seq):
        return seq[int(self.r() * len(seq)) % len(seq)]


def _f(n) -> str:
    return f"{n:.1f}".rstrip("0").rstrip(".")


# ------------------------------------------------------------------------------------ palettes

PAL = {
    "day": dict(sky=("#4fb6ff", "#a9e0ff", "#e4f6ff"), far="#8ec9a6", mid="#5fb07a", near="#3c9a5a", sand="#f3e0a9",
                water="#59bdf0", water2="#2f94d2", bld="#8ea4c2", bld2="#a9bbd3", cloud="#ffffff", ground="#58b062",
                ink="#173042", glow="#fff6c9", win="#cfe8ff", fog=0.0),
    "sunset": dict(sky=("#5b3a8e", "#ff7a5c", "#ffc783"), far="#a8527a", mid="#793a73", near="#4f2a5e", sand="#e9b27f",
                   water="#f2897a", water2="#a9487f", bld="#5d3a73", bld2="#7a4a80", cloud="#ffc3a1", ground="#5c3d6b",
                   ink="#2a1736", glow="#ffd27a", win="#ffd48a", fog=0.0),
    "night": dict(sky=("#050824", "#141b53", "#2c3a8a"), far="#1b2a58", mid="#13204a", near="#0c1636", sand="#59608a",
                  water="#1d3c80", water2="#0e2354", bld="#161d44", bld2="#222b5c", cloud="#3d4a8f", ground="#101c3d",
                  ink="#e8ecff", glow="#f4f1e1", win="#ffd98a", fog=0.0),
}


def _hills(color, base, amp, seed, f1=330, f2=170, opacity=1.0):
    p1, p2 = seed * 1.7, seed * 0.9
    pts = [f"{x},{_f(base - amp * (.6 * math.sin(x / f1 + p1) + .4 * math.sin(x / f2 + p2)) - amp)}" for x in range(-40, W + 80, 60)]
    return f'<polygon points="{" ".join(pts)} {W + 40},{H} -40,{H}" fill="{color}" opacity="{opacity}"/>'


def _sky(P, t, u, horizon, rng, stars=True):
    s = [f'<defs><linearGradient id="{u}sky" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{P["sky"][0]}"/>'
         f'<stop offset=".55" stop-color="{P["sky"][1]}"/><stop offset="1" stop-color="{P["sky"][2]}"/></linearGradient></defs>',
         f'<rect width="{W}" height="{H}" fill="url(#{u}sky)"/>']
    if t == "day":
        s.append(f'<circle cx="1500" cy="170" r="150" fill="{P["glow"]}" opacity=".28"/><circle cx="1500" cy="170" r="76" fill="#fff4b0"/>')
    elif t == "sunset":
        s.append(f'<circle cx="1380" cy="{horizon - 40}" r="230" fill="{P["glow"]}" opacity=".3"/>'
                 f'<circle cx="1380" cy="{horizon - 40}" r="118" fill="#ffd98a"/>')
    else:
        if stars:
            for _ in range(80):
                s.append(f'<circle class="st-star" cx="{_f(rng.between(0, W))}" cy="{_f(rng.between(0, horizon - 80))}" '
                         f'r="{_f(rng.between(1.2, 3.2))}" fill="#fff" style="animation-delay:{_f(-rng.between(0, 4))}s"/>')
        s.append('<circle cx="1480" cy="180" r="62" fill="#f6f2dc"/><circle cx="1462" cy="165" r="12" fill="#d9d4b8" opacity=".7"/>'
                 '<circle cx="1500" cy="200" r="9" fill="#d9d4b8" opacity=".7"/><circle cx="1494" cy="150" r="6" fill="#d9d4b8" opacity=".6"/>')
    if True:
        for i in range(3):
            x, y, k = rng.between(-100, 900) + i * 560, rng.between(90, 330), rng.between(.7, 1.3)
            s.append(f'<g transform="translate({_f(x)},{_f(y)}) scale({_f(k)})"><g class="st-cloud" style="animation-delay:{_f(-i * 9)}s;'
                     f'animation-duration:{_f(48 + i * 11)}s"><g fill="{P["cloud"]}" opacity=".88"><ellipse cx="0" cy="0" rx="120" ry="38"/>'
                     f'<ellipse cx="-60" cy="-22" rx="66" ry="44"/><ellipse cx="38" cy="-34" rx="80" ry="52"/></g></g></g>')
    return "".join(s)


def _pine(x, y, k, c1, c2):
    tiers = "".join(f'<polygon points="{_f(x - (120 - i * 24) * k)},{_f(y - 70 * k - i * 78 * k)} {_f(x + (120 - i * 24) * k)},'
                    f'{_f(y - 70 * k - i * 78 * k)} {x},{_f(y - 190 * k - i * 78 * k)}" fill="{c1 if i % 2 else c2}"/>' for i in range(4))
    return f'<rect x="{_f(x - 16 * k)}" y="{_f(y - 80 * k)}" width="{_f(32 * k)}" height="{_f(90 * k)}" fill="#4a3426"/>{tiers}'


def _round_tree(x, y, k, c1, c2):
    return (f'<rect x="{_f(x - 18 * k)}" y="{_f(y - 170 * k)}" width="{_f(36 * k)}" height="{_f(180 * k)}" rx="10" fill="#6b4a32"/>'
            f'<circle cx="{_f(x)}" cy="{_f(y - 250 * k)}" r="{_f(120 * k)}" fill="{c2}"/><circle cx="{_f(x - 80 * k)}" cy="{_f(y - 200 * k)}" r="{_f(80 * k)}" fill="{c1}"/>'
            f'<circle cx="{_f(x + 86 * k)}" cy="{_f(y - 210 * k)}" r="{_f(86 * k)}" fill="{c1}"/>')


def _palm(x, y, k, P):
    leaves = "".join(f'<ellipse cx="0" cy="-60" rx="26" ry="120" fill="{P["near"] if i % 2 else P["mid"]}" transform="rotate({a})"/>'
                     for i, a in enumerate((-110, -70, -30, 20, 65, 105)))
    return (f'<g transform="translate({_f(x)},{_f(y)}) scale({k})"><path d="M0,0 Q40,-200 10,-420" stroke="#8a5a36" stroke-width="30" fill="none" stroke-linecap="round"/>'
            f'<g transform="translate(10,-420)" class="st-sway">{leaves}<circle cx="0" cy="8" r="16" fill="#6b4226"/></g></g>')


# --------------------------------------------------------------------------------- environments

def _env_pond(P, t, u, rng):
    gy = GROUND["pond"]
    s = [_sky(P, t, u, 430, rng), _hills(P["far"], 520, 70, 1.1), _hills(P["mid"], 560, 55, 2.3)]
    for x in (120, 360, 1560, 1790):
        s.append(_round_tree(x, 600, rng.between(.8, 1.15), P["near"], P["mid"]))
    s.append(f'<defs><linearGradient id="{u}wat" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{P["water"]}"/>'
             f'<stop offset="1" stop-color="{P["water2"]}"/></linearGradient></defs>'
             f'<path d="M0,610 Q300,585 700,600 T1400,596 T1920,606 V{H} H0 Z" fill="{P["near"]}"/>'
             f'<path d="M-20,650 Q260,610 620,640 T1300,636 T1940,650 V{H} H-20 Z" fill="url(#{u}wat)"/>')
    for i in range(14):  # shimmering highlights
        x, y = rng.between(60, W - 200), rng.between(690, 1040)
        s.append(f'<rect class="st-shim" x="{_f(x)}" y="{_f(y)}" width="{_f(rng.between(60, 190))}" height="5" rx="3" fill="#fff" '
                 f'style="animation-delay:{_f(-rng.between(0, 3))}s"/>')
    for x, y in ((140, 960), (1740, 1000), (1500, 760), (330, 745)):  # lily pads + flowers + reeds
        s.append(f'<g transform="translate({x},{y})"><ellipse rx="64" ry="20" fill="#3fae6c"/><path d="M0,0 L50,-12 L52,4 Z" fill="{P["water2"]}" opacity=".55"/>'
                 f'<circle cx="-14" cy="-14" r="13" fill="#ff9cc8"/><circle cx="-14" cy="-14" r="5" fill="#ffe27a"/></g>')
    for x in (60, 100, 1830, 1870, 1790):
        s.append(f'<g class="st-sway" style="transform-origin:{x}px 660px"><line x1="{x}" y1="680" x2="{x - 10}" y2="520" stroke="#4d8a4a" stroke-width="7" stroke-linecap="round"/>'
                 f'<rect x="{x - 19}" y="508" width="18" height="52" rx="9" fill="#6b4226"/></g>')
    return "".join(s)


def _env_meadow(P, t, u, rng):
    s = [_sky(P, t, u, 560, rng), _hills(P["far"], 640, 90, 3.1), _hills(P["mid"], 720, 70, 4.4)]
    s.append(_round_tree(260, 760, 1.0, P["near"], P["mid"]) + _round_tree(1640, 780, 1.2, P["near"], P["mid"]))
    s.append(f'<defs><linearGradient id="{u}gr" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{P["ground"]}"/>'
             f'<stop offset="1" stop-color="{P["near"]}"/></linearGradient></defs>'
             f'<path d="M0,780 Q400,740 900,770 T1920,760 V{H} H0 Z" fill="url(#{u}gr)"/>')
    petals = ("#ff6b8b", "#ffd447", "#ffffff", "#b58cff", "#ff9f43")
    for _ in range(70):
        x, y = rng.between(0, W), rng.between(800, 1060)
        k = .6 + (y - 780) / 480
        s.append(f'<g transform="translate({_f(x)},{_f(y)}) scale({_f(k)})"><line x1="0" y1="0" x2="0" y2="22" stroke="{P["mid"]}" stroke-width="3"/>'
                 f'<circle cy="-3" r="8" fill="{rng.choice(petals)}"/><circle cy="-3" r="3" fill="#fff6c9"/></g>')
    return "".join(s)


def _env_beach(P, t, u, rng):
    s = [_sky(P, t, u, 520, rng)]
    s.append(f'<defs><linearGradient id="{u}sea" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{P["water2"]}"/><stop offset="1" stop-color="{P["water"]}"/></linearGradient></defs>'
             f'<rect y="500" width="{W}" height="230" fill="url(#{u}sea)"/>')
    for i in range(9):
        y = 530 + i * 24
        s.append(f'<path class="st-wave" d="M-200,{y} q50,-14 100,0 t100,0 t100,0 t100,0 t100,0 t100,0 t100,0 t100,0 t100,0 t100,0 t100,0 t100,0 t100,0 t100,0 t100,0 t100,0 t100,0 t100,0 t100,0 t100,0 t100,0" '
                 f'stroke="#fff" stroke-opacity="{_f(.18 + i * .03)}" stroke-width="3" fill="none" style="animation-duration:{_f(5 + i * .6)}s"/>')
    s.append(f'<path d="M0,720 Q500,690 1000,716 T1920,704 V{H} H0 Z" fill="{P["sand"]}"/><path d="M0,720 Q500,690 1000,716 T1920,704" stroke="#fff" stroke-opacity=".7" stroke-width="10" fill="none"/>')
    s.append(_palm(180, 800, 1.0, P) + _palm(1760, 830, 1.15, P))
    s.append(f'<g transform="translate(1450,820)"><line x1="0" y1="0" x2="0" y2="-210" stroke="#7a4a2b" stroke-width="8"/><path d="M-130,-200 Q0,-330 130,-200 Z" fill="#ff5d6c"/>'
             f'<path d="M-45,-250 Q0,-330 45,-250 L0,-200 Z" fill="#fff" opacity=".85"/></g>')
    return "".join(s)


def _env_city(P, t, u, rng):
    s = [_sky(P, t, u, 600, rng)]
    for layer, (col, top, lo, hi) in enumerate(((P["bld"], 480, 160, 330), (P["bld2"], 600, 190, 380))):
        x = -30
        while x < W:
            w, h = rng.between(110, 210), rng.between(lo, hi) + (0 if layer else 70)
            y = top + 260 - h + (layer * 20)
            s.append(f'<rect x="{_f(x)}" y="{_f(y)}" width="{_f(w)}" height="{_f(H - y)}" fill="{col}"/>')
            for wy in range(int(y) + 24, 800, 42):
                for wx in range(int(x) + 16, int(x + w) - 22, 34):
                    if rng.r() < (.55 if t != "day" else .35):
                        s.append(f'<rect x="{wx}" y="{wy}" width="16" height="22" rx="2" fill="{P["win"]}" opacity="{.95 if t == "night" else .5}"/>')
            x += w + rng.between(6, 30)
    s.append(f'<rect y="900" width="{W}" height="{H - 900}" fill="{P["near"]}"/><rect y="893" width="{W}" height="14" fill="{P["mid"]}"/>')
    for x in range(40, W, 240):
        s.append(f'<rect x="{x}" y="1000" width="130" height="10" rx="4" fill="#fff" opacity=".55"/>')
    return "".join(s)


def _env_space(P, t, u, rng):
    top, mid = {"day": ("#0b2a5e", "#2f6fd1"), "sunset": ("#2a0f4f", "#a23a8f"), "night": ("#02030d", "#0b1030")}[t]
    s = [f'<defs><radialGradient id="{u}nb1" cx=".3" cy=".4" r=".6"><stop offset="0" stop-color="{mid}" stop-opacity=".9"/><stop offset="1" stop-color="{mid}" stop-opacity="0"/></radialGradient>'
         f'<radialGradient id="{u}nb2" cx=".75" cy=".3" r=".5"><stop offset="0" stop-color="#7a4bd1" stop-opacity=".55"/><stop offset="1" stop-color="#7a4bd1" stop-opacity="0"/></radialGradient>'
         f'<radialGradient id="{u}pl" cx=".35" cy=".3" r=".8"><stop offset="0" stop-color="#ffb36b"/><stop offset="1" stop-color="#b8442a"/></radialGradient></defs>'
         f'<rect width="{W}" height="{H}" fill="{top}"/><rect width="{W}" height="{H}" fill="url(#{u}nb1)"/><rect width="{W}" height="{H}" fill="url(#{u}nb2)"/>']
    for _ in range(170):
        s.append(f'<circle class="st-star" cx="{_f(rng.between(0, W))}" cy="{_f(rng.between(0, 860))}" r="{_f(rng.between(.8, 3))}" fill="#fff" style="animation-delay:{_f(-rng.between(0, 4))}s"/>')
    s.append(f'<g transform="translate(1440,300)"><ellipse rx="300" ry="62" fill="none" stroke="#e9c8a0" stroke-width="16" opacity=".7" transform="rotate(-16)"/>'
             f'<circle r="160" fill="url(#{u}pl)"/><ellipse rx="300" ry="62" fill="none" stroke="#f3d9b6" stroke-width="16" transform="rotate(-16)" clip-path="inset(50% 0 0 0)"/></g>'
             f'<circle cx="360" cy="260" r="64" fill="#cfd5e6"/><circle cx="340" cy="244" r="14" fill="#aab2c8"/><circle cx="384" cy="282" r="10" fill="#aab2c8"/>')
    s.append(f'<ellipse cx="960" cy="1200" rx="1300" ry="330" fill="#8f96ab"/><ellipse cx="960" cy="1200" rx="1300" ry="330" fill="none" stroke="#c9cfe0" stroke-width="6"/>'
             f'<ellipse cx="420" cy="1000" rx="90" ry="22" fill="#767d94"/><ellipse cx="1500" cy="1030" rx="120" ry="28" fill="#767d94"/><ellipse cx="1000" cy="1050" rx="60" ry="14" fill="#767d94"/>')
    return "".join(s)


def _env_disco(P, t, u, rng):
    top, bot = {"day": ("#2a1658", "#6a2a8a"), "sunset": ("#3a1060", "#8a2a7a"), "night": ("#070516", "#1d0f3a")}[t]
    s = [f'<defs><linearGradient id="{u}bk" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{top}"/><stop offset="1" stop-color="{bot}"/></linearGradient></defs>'
         f'<rect width="{W}" height="{H}" fill="url(#{u}bk)"/>']
    cols = ("#ff3d81", "#37d6ff", "#ffd23d", "#7cff6b", "#b46bff")
    for i, x in enumerate((340, 960, 1580)):  # swaying spotlight beams
        c = cols[(i * 2) % 5]
        s.append(f'<g style="transform-origin:{x}px -20px" class="st-beam" data-i="{i}"><polygon points="{x - 18},-20 {x + 18},-20 {x + 230},900 {x - 230},900" fill="{c}" opacity=".16"/></g>')
    s.append(f'<polygon points="0,780 {W},780 {W + 380},{H} -380,{H}" fill="#12091f"/>')
    n = 8
    for r_ in range(4):  # perspective dance floor
        y0, y1 = 780 + r_ * 75, 780 + (r_ + 1) * 75
        k0, k1 = r_ / 4, (r_ + 1) / 4
        for c_ in range(n):
            xa0, xa1 = -380 * k0 + (W + 760 * k0) * c_ / n, -380 * k0 + (W + 760 * k0) * (c_ + 1) / n
            xb0, xb1 = -380 * k1 + (W + 760 * k1) * c_ / n, -380 * k1 + (W + 760 * k1) * (c_ + 1) / n
            s.append(f'<polygon class="st-tile" points="{_f(xa0)},{y0} {_f(xa1)},{y0} {_f(xb1)},{y1} {_f(xb0)},{y1}" fill="{cols[(r_ + c_) % 5]}" opacity=".5" '
                     f'style="animation-delay:{_f(-((r_ + c_) % 5) * .35)}s" stroke="#12091f" stroke-width="3"/>')
    facets = "".join(f'<ellipse cx="0" cy="0" rx="{rx}" ry="64" fill="none" stroke="#cdd6ea" stroke-width="2" opacity=".6"/>' for rx in (16, 34, 50)) + \
             "".join(f'<line x1="-64" y1="{y}" x2="64" y2="{y}" stroke="#cdd6ea" stroke-width="2" opacity=".5"/>' for y in (-36, -12, 12, 36))
    s.append(f'<g transform="translate(1240,250)"><line x1="0" y1="-270" x2="0" y2="-64" stroke="#9aa3b8" stroke-width="5"/><circle r="66" fill="#aab4cc"/>{facets}'
             f'<circle cx="-22" cy="-24" r="10" fill="#fff" opacity=".9"/><circle class="st-spark" cx="30" cy="20" r="7" fill="#fff"/></g>')
    return "".join(s)


def _env_forest(P, t, u, rng):
    s = [_sky(P, t, u, 520, rng), _hills(P["far"], 600, 60, 5.2)]
    for x in range(-20, W + 100, 150):
        s.append(_pine(x + rng.between(-30, 30), rng.between(640, 700), rng.between(.8, 1.1), P["far"], P["mid"]))
    for x in range(40, W + 100, 240):
        s.append(_pine(x + rng.between(-40, 40), rng.between(780, 840), rng.between(1.2, 1.55), P["mid"], P["near"]))
    s.append(f'<defs><linearGradient id="{u}fl" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{P["ground"]}"/><stop offset="1" stop-color="{P["near"]}"/></linearGradient></defs>'
             f'<path d="M0,850 Q500,820 1000,846 T1920,836 V{H} H0 Z" fill="url(#{u}fl)"/>')
    if t != "night":
        for x in (420, 900, 1320):
            s.append(f'<polygon points="{x},0 {x + 80},0 {x + 360},900 {x + 200},900" fill="#fff6c9" opacity=".11"/>')
    for x in (60, 1860):  # foreground trunks
        s.append(f'<rect x="{x - 50}" y="-10" width="100" height="{H}" fill="#2c1d14"/><rect x="{x - 50}" y="-10" width="26" height="{H}" fill="#47301f"/>')
    return "".join(s)


_ENVIRONMENTS = {"pond": _env_pond, "meadow": _env_meadow, "beach": _env_beach, "city": _env_city, "space": _env_space,
                 "disco": _env_disco, "forest": _env_forest}


# ----------------------------------------------------------------------------------------- rig
# Rotating parts. Arms/forearms/legs/knees are given as "outward-positive" degrees: the right-hand
# side is mirrored automatically, so a frame like {"armL": 150, "armR": 150} raises both arms.
MIRROR = {"armR", "foreR", "legR", "kneeR"}
ROT_PARTS = ("legL", "legR", "kneeL", "kneeR", "torso", "head", "armL", "armR", "foreL", "foreR")
# joint positions (parent-relative); feet stand at y=0 and the figure is ~550 units tall
JOINTS = {"legL": (-26, -250), "legR": (26, -250), "kneeL": (0, 120), "kneeR": (0, 120), "torso": (0, -250), "head": (0, -175),
          "armL": (-62, -150), "armR": (62, -150), "foreL": (0, 88), "foreR": (0, 88)}


def _bob(f):
    return f"translateY({_f(f.get('y', 0))}px) scale({_f(f.get('sx', 1))},{_f(f.get('sy', 1))})"


def _rot(part, f):
    v = f.get(part, 0)
    return -v if part in MIRROR else v


# Actions: dur = beats per loop, beat = seconds per beat (scaled by tempo), steps = hold each pose then snap.
# frames: percent -> {part: degrees, "y"/"sx"/"sy": body bounce}. First and last frame must match.
def _loop(*frames):
    return list(frames)


ACTION_DEFS = {
    "idle": dict(beat=.6, dur=4, frames=_loop(
        (0, dict(armL=4, armR=4, foreL=2, foreR=2, head=-3)),
        (50, dict(armL=9, armR=9, foreL=5, foreR=5, head=3, y=-6)),
        (100, dict(armL=4, armR=4, foreL=2, foreR=2, head=-3)))),
    "wave": dict(beat=.4, dur=4, frames=_loop(
        (0, dict(armL=6, armR=150, foreR=28, head=4)),
        (25, dict(armL=6, armR=156, foreR=-24, head=6, y=-3)),
        (50, dict(armL=6, armR=150, foreR=28, head=4)),
        (75, dict(armL=6, armR=156, foreR=-24, head=6, y=-3)),
        (100, dict(armL=6, armR=150, foreR=28, head=4)))),
    "cheer": dict(beat=.5, dur=2, frames=_loop(
        (0, dict(armL=125, armR=125, foreL=25, foreR=25, kneeL=10, kneeR=10, y=0, sx=1.06, sy=.92, head=-4)),
        (50, dict(armL=168, armR=168, foreL=-8, foreR=-8, kneeL=30, kneeR=30, y=-95, sx=.96, sy=1.05, head=4)),
        (100, dict(armL=125, armR=125, foreL=25, foreR=25, kneeL=10, kneeR=10, y=0, sx=1.06, sy=.92, head=-4)))),
    "jump": dict(beat=.5, dur=3, frames=_loop(
        (0, dict(armL=20, armR=20, kneeL=16, kneeR=16, y=0, sx=1.05, sy=.93)),
        (20, dict(armL=-12, armR=-12, kneeL=34, kneeR=34, y=4, sx=1.1, sy=.86)),
        (55, dict(armL=155, armR=155, foreL=10, foreR=10, kneeL=42, kneeR=42, legL=10, legR=10, y=-200, sx=.94, sy=1.07)),
        (82, dict(armL=40, armR=40, kneeL=22, kneeR=22, y=0, sx=1.1, sy=.88)),
        (100, dict(armL=20, armR=20, kneeL=16, kneeR=16, y=0, sx=1.05, sy=.93)))),
    "walk": dict(beat=.55, dur=2, frames=_loop(
        (0, dict(legL=22, legR=-22, armL=16, armR=-16, y=0, torso=-2)),
        (25, dict(legL=0, legR=0, kneeR=30, armL=0, armR=0, y=-12)),
        (50, dict(legL=-22, legR=22, armL=-16, armR=16, y=0, torso=2)),
        (75, dict(legL=0, legR=0, kneeL=30, armL=0, armR=0, y=-12)),
        (100, dict(legL=22, legR=-22, armL=16, armR=-16, y=0, torso=-2)))),
    "march": dict(beat=.4, dur=2, frames=_loop(
        (0, dict(legL=38, legR=-38, kneeR=60, armL=40, armR=-40, y=-4, torso=-3)),
        (25, dict(legL=0, legR=0, y=-16)),
        (50, dict(legL=-38, legR=38, kneeL=60, armL=-40, armR=40, y=-4, torso=3)),
        (75, dict(legL=0, legR=0, y=-16)),
        (100, dict(legL=38, legR=-38, kneeR=60, armL=40, armR=-40, y=-4, torso=-3)))),
    "clap": dict(beat=.35, dur=2, frames=_loop(
        (0, dict(armL=12, armR=12, foreL=-132, foreR=-132, y=0)),
        (50, dict(armL=22, armR=22, foreL=-178, foreR=-178, y=-8, head=4)),
        (100, dict(armL=12, armR=12, foreL=-132, foreR=-132, y=0)))),
    # dance styles ---------------------------------------------------------------------------------
    "dance-disco": dict(beat=.5, dur=4, frames=_loop(
        (0, dict(armL=150, foreL=0, armR=26, foreR=-62, torso=-8, head=-6, legL=12, legR=12, kneeL=0, kneeR=20, y=-4)),
        (25, dict(armL=135, foreL=8, armR=30, foreR=-62, torso=-4, head=-2, legL=10, legR=10, kneeL=8, kneeR=8, y=-14)),
        (50, dict(armR=150, foreR=0, armL=26, foreL=-62, torso=8, head=6, legL=12, legR=12, kneeL=20, kneeR=0, y=-4)),
        (75, dict(armR=135, foreR=8, armL=30, foreL=-62, torso=4, head=2, legL=10, legR=10, kneeL=8, kneeR=8, y=-14)),
        (100, dict(armL=150, foreL=0, armR=26, foreR=-62, torso=-8, head=-6, legL=12, legR=12, kneeL=0, kneeR=20, y=-4)))),
    "dance-hiphop": dict(beat=.4, dur=4, frames=_loop(
        (0, dict(armL=48, foreL=-72, armR=16, foreR=-24, legL=20, legR=20, kneeL=22, kneeR=22, torso=-5, head=5, y=0, sx=1.04, sy=.95)),
        (25, dict(armL=62, foreL=-60, armR=30, foreR=-30, legL=20, legR=20, kneeL=8, kneeR=8, torso=-7, head=8, y=-20)),
        (50, dict(armL=16, foreL=-24, armR=48, foreR=-72, legL=20, legR=20, kneeL=22, kneeR=22, torso=5, head=-5, y=0, sx=1.04, sy=.95)),
        (75, dict(armL=30, foreL=-30, armR=62, foreR=-60, legL=20, legR=20, kneeL=8, kneeR=8, torso=7, head=-8, y=-20)),
        (100, dict(armL=48, foreL=-72, armR=16, foreR=-24, legL=20, legR=20, kneeL=22, kneeR=22, torso=-5, head=5, y=0, sx=1.04, sy=.95)))),
    "dance-robot": dict(beat=.5, dur=4, steps=True, frames=_loop(
        (0, dict(armL=90, foreL=90, armR=0, foreR=0, head=0, torso=0, legL=8, legR=8, y=0)),
        (25, dict(armL=0, foreL=0, armR=90, foreR=90, head=12, torso=4, legL=8, legR=8, y=-6)),
        (50, dict(armL=150, foreL=0, armR=150, foreR=0, head=-12, torso=-4, legL=14, legR=14, y=0)),
        (75, dict(armL=90, foreL=-90, armR=90, foreR=-90, head=0, torso=0, legL=8, legR=8, y=-6)),
        (100, dict(armL=90, foreL=90, armR=0, foreR=0, head=0, torso=0, legL=8, legR=8, y=0)))),
    "dance-groove": dict(beat=.8, dur=4, frames=_loop(
        (0, dict(armL=140, foreL=-10, armR=110, foreR=-20, torso=-6, head=-5, legL=8, legR=8, kneeL=0, kneeR=14, y=0)),
        (25, dict(armL=125, foreL=-20, armR=125, foreR=-10, torso=0, head=0, legL=8, legR=8, kneeL=10, kneeR=10, y=-10)),
        (50, dict(armL=110, foreL=-20, armR=140, foreR=-10, torso=6, head=5, legL=8, legR=8, kneeL=14, kneeR=0, y=0)),
        (75, dict(armL=125, foreL=-10, armR=125, foreR=-20, torso=0, head=0, legL=8, legR=8, kneeL=10, kneeR=10, y=-10)),
        (100, dict(armL=140, foreL=-10, armR=110, foreR=-20, torso=-6, head=-5, legL=8, legR=8, kneeL=0, kneeR=14, y=0)))),
}


def action_key(actor: dict) -> str:
    return f"dance-{actor['style']}" if actor["action"] == "dance" else actor["action"]


def _pose_at(key, pct):
    """The frame dict for a named action at a given percent (used by tests and the pose preview)."""
    frames = ACTION_DEFS[key]["frames"]
    return dict(next(f for p, f in frames if p == pct))


def _joint(part, inner, pose):
    jx, jy = JOINTS[part]
    if pose is None:
        return f'<g transform="translate({jx},{jy})"><g class="p-{part}">{inner}</g></g>'
    return f'<g transform="translate({jx},{jy}) rotate({_f(_rot(part, pose))})">{inner}</g>'


def _limb(x2, y2, color, w):
    return f'<line x1="0" y1="0" x2="{x2}" y2="{y2}" stroke="{color}" stroke-width="{w}" stroke-linecap="round"/>'


def _person(look, pose):
    L, skin = look, look["skin"]
    sk = lambda side: _joint("kneeL" if side == "L" else "kneeR", _limb(0, 124, L["pants"], 34) +
                             f'<ellipse cx="0" cy="130" rx="32" ry="15" fill="{L["shoes"]}"/><ellipse cx="0" cy="134" rx="32" ry="8" fill="#000" opacity=".12"/>', pose)
    legs = "".join(_joint(f"leg{s}", _limb(0, 120, L["pants"], 36) + sk(s), pose) for s in "LR")
    forearm = lambda s: _joint(f"fore{s}", _limb(0, 76, skin, 28) + f'<circle cx="0" cy="82" r="19" fill="{skin}"/>', pose)
    arms = "".join(_joint(f"arm{s}", _limb(0, 84, L["shirt"], 34) + forearm(s), pose) for s in "LR")
    face = (f'<circle cx="-21" cy="-66" r="7" fill="#222"/><circle cx="21" cy="-66" r="7" fill="#222"/>'
            f'<circle cx="-19" cy="-68" r="2.4" fill="#fff"/><circle cx="23" cy="-68" r="2.4" fill="#fff"/>'
            f'<path d="M-20,-38 Q0,-20 20,-38" stroke="#7a2e2e" stroke-width="5" fill="none" stroke-linecap="round"/>'
            f'<circle cx="-36" cy="-48" r="9" fill="#ff8a8a" opacity=".35"/><circle cx="36" cy="-48" r="9" fill="#ff8a8a" opacity=".35"/>')
    if L["glasses"]:
        face += '<g fill="none" stroke="#222" stroke-width="4"><circle cx="-21" cy="-66" r="15"/><circle cx="21" cy="-66" r="15"/><line x1="-6" y1="-66" x2="6" y2="-66"/></g>'
    hair = {"short": f'<path d="M-58,-72 Q-60,-132 0,-132 Q60,-132 58,-72 Q40,-100 0,-98 Q-40,-100 -58,-72 Z" fill="{L["hair"]}"/>',
            "long": f'<path d="M-62,-62 Q-70,-136 0,-134 Q70,-136 62,-62 L64,-8 Q50,-6 46,-22 L46,-80 Q0,-100 -46,-80 L-46,-22 Q-50,-6 -64,-8 Z" fill="{L["hair"]}"/>',
            "bun": f'<circle cx="0" cy="-138" r="24" fill="{L["hair"]}"/><path d="M-58,-72 Q-60,-132 0,-132 Q60,-132 58,-72 Q40,-100 0,-98 Q-40,-100 -58,-72 Z" fill="{L["hair"]}"/>',
            "none": ""}[L["hair_style"]]
    hat = {"none": "", "cap": f'<path d="M-60,-100 Q-58,-146 0,-146 Q58,-146 60,-100 Z" fill="{L["shirt"]}"/><rect x="-6" y="-106" width="84" height="14" rx="7" fill="{L["shirt"]}"/>',
           "party": '<polygon points="-30,-112 30,-112 0,-196" fill="#ff5fa2"/><circle cx="0" cy="-198" r="9" fill="#ffd447"/><line x1="-18" y1="-130" x2="14" y2="-154" stroke="#fff" stroke-width="5"/>',
           "crown": '<polygon points="-44,-112 -44,-160 -22,-136 0,-170 22,-136 44,-160 44,-112" fill="#f5c518" stroke="#c99a0a" stroke-width="3"/>'}[L["hat"]]
    head = _joint("head", f'<rect x="-14" y="-12" width="28" height="22" fill="{skin}"/><circle cx="0" cy="-62" r="58" fill="{skin}"/>{hair}{face}{hat}', pose)
    torso_body = (f'<rect x="-60" y="-178" width="120" height="190" rx="34" fill="{L["shirt"]}"/><rect x="-60" y="-24" width="120" height="36" rx="14" fill="{L["pants"]}"/>'
                  f'<rect x="-14" y="-24" width="28" height="9" rx="4" fill="#fff" opacity=".35"/>')
    return legs, _joint("torso", torso_body + arms + head, pose)


def _robot(look, pose):
    c, a = look["color"], look["accent"]
    dark = "#4b5568"
    kneeS = lambda s: _joint(f"knee{s}", _limb(0, 124, dark, 30) + f'<rect x="-34" y="116" width="68" height="28" rx="12" fill="{c}"/>', pose)
    legs = "".join(_joint(f"leg{s}", _limb(0, 120, dark, 32) + f'<circle r="20" fill="{c}"/>' + kneeS(s), pose) for s in "LR")
    fore = lambda s: _joint(f"fore{s}", _limb(0, 74, dark, 24) + f'<circle cy="84" r="20" fill="{c}"/><rect x="-7" y="82" width="14" height="22" rx="5" fill="{dark}"/>', pose)
    arms = "".join(_joint(f"arm{s}", _limb(0, 84, dark, 28) + f'<circle r="22" fill="{c}"/>' + fore(s), pose) for s in "LR")
    head = _joint("head", f'<rect x="-12" y="-14" width="24" height="22" fill="{dark}"/><rect x="-62" y="-126" width="124" height="108" rx="26" fill="{c}"/>'
                          f'<rect x="-46" y="-104" width="92" height="52" rx="16" fill="#0e1424"/>'
                          f'<circle class="st-blink" cx="-22" cy="-78" r="13" fill="{a}"/><circle class="st-blink" cx="22" cy="-78" r="13" fill="{a}"/>'
                          f'<rect x="-22" y="-44" width="44" height="8" rx="4" fill="{dark}"/>'
                          f'<line x1="0" y1="-126" x2="0" y2="-160" stroke="{dark}" stroke-width="6"/><circle cx="0" cy="-166" r="11" fill="{a}"/>', pose)
    torso_body = (f'<rect x="-64" y="-180" width="128" height="194" rx="30" fill="{c}"/><rect x="-40" y="-150" width="80" height="70" rx="14" fill="#0e1424"/>'
                  f'<circle class="st-blink" cx="0" cy="-114" r="16" fill="{a}"/><rect x="-62" y="-30" width="124" height="40" rx="14" fill="{dark}"/>')
    return legs, _joint("torso", torso_body + arms + head, pose)


def character_svg(actor: dict, pose=None) -> str:
    """The rigged figure at its feet origin. `pose` (a frame dict) bakes a static pose; None = animatable."""
    legs, upper = (_robot if actor["kind"] == "robot" else _person)(actor["look"], pose)
    if pose is None:
        body = f'<g class="p-bob">{legs}{upper}</g>'
    else:
        body = f'<g transform="translate(0,{_f(pose.get("y", 0))}) scale({_f(pose.get("sx", 1))},{_f(pose.get("sy", 1))})">{legs}{upper}</g>'
    return body


# ------------------------------------------------------------------------------------- props

def _note(c):
    return (f'<g fill="{c}" stroke="{c}"><ellipse cx="0" cy="0" rx="13" ry="9" transform="rotate(-22)" stroke="none"/>'
            f'<path d="M11,-4 L11,-50 Q28,-42 30,-26" fill="none" stroke-width="6" stroke-linecap="round"/></g>')


def _star4(r, c):
    return f'<path d="M0,{-r} Q{r * .15},{-r * .15} {r},0 Q{r * .15},{r * .15} 0,{r} Q{-r * .15},{r * .15} {-r},0 Q{-r * .15},{-r * .15} 0,{-r} Z" fill="{c}"/>'


def _props(names, stage, gy, rng):
    out = []
    ax = stage["actors"][0]["from"] * W / 100 if stage["actors"] else W / 2
    top = gy - 560 * stage["actors"][0]["scale"]
    water = stage["env"] in ("pond", "beach")
    if "notes" in names:
        cols = ("#ffd447", "#ff6b8b", "#37d6ff", "#b58cff", "#7cff6b")
        for i in range(8):
            sign = 1 if i % 2 else -1
            x = ax + sign * rng.between(190, 430) * stage["actors"][0]["scale"]
            out.append(f'<g transform="translate({_f(x)},{_f(top + rng.between(60, 240))})"><g class="st-note" style="--dx:{_f(rng.between(-90, 90))}px;'
                       f'animation-delay:{_f(-i * .55)}s">{_note(cols[i % 5])}</g></g>')
    if "sparkles" in names:
        for i in range(16):
            out.append(f'<g transform="translate({_f(rng.between(80, W - 80))},{_f(rng.between(120, gy - 100))})"><g class="st-spark" '
                       f'style="animation-delay:{_f(-rng.between(0, 2.4))}s;animation-duration:{_f(rng.between(1.6, 2.8))}s">{_star4(rng.between(14, 28), "#fff6c9")}</g></g>')
    if "fish" in names and water:
        for i in range(3):
            x, y = rng.between(300, W - 500), rng.between(740, 960) if stage["env"] == "pond" else rng.between(560, 680)
            out.append(f'<g transform="translate({_f(x)},{_f(y)})"><g class="st-fish" style="animation-delay:{_f(-i * 1.7)}s;animation-duration:{_f(4.6 + i * .7)}s">'
                       f'<ellipse rx="34" ry="15" fill="#ff8a3d"/><polygon points="-30,0 -56,-16 -56,16" fill="#ff6a1f"/><circle cx="18" cy="-4" r="4" fill="#222"/></g></g>')
    if "butterflies" in names:
        cs = ("#ff8a3d", "#ffd447", "#b58cff", "#ff6b8b")
        for i in range(4):
            c = cs[i % 4]
            out.append(f'<g transform="translate({_f(rng.between(200, W - 300))},{_f(rng.between(520, gy - 200))})"><g class="st-flutter" '
                       f'style="animation-delay:{_f(-i * 1.9)}s;animation-duration:{_f(6 + i)}s"><g class="st-wing"><ellipse cx="-14" cy="-4" rx="16" ry="11" fill="{c}"/>'
                       f'<ellipse cx="14" cy="-4" rx="16" ry="11" fill="{c}"/></g><rect x="-2" y="-12" width="4" height="22" rx="2" fill="#2a1f1a"/></g></g>')
    if "confetti" in names:
        cs = ("#ff3d81", "#37d6ff", "#ffd23d", "#7cff6b", "#b46bff")
        for i in range(44):
            out.append(f'<g transform="translate({_f(rng.between(0, W))},-30)"><g class="st-conf" style="animation-delay:{_f(-rng.between(0, 6))}s;animation-duration:{_f(rng.between(3.5, 6.5))}s;'
                       f'--sw:{_f(rng.between(-90, 90))}px"><rect width="14" height="8" fill="{cs[i % 5]}"/></g></g>')
    if "fireflies" in names:
        for i in range(18):
            out.append(f'<g transform="translate({_f(rng.between(80, W - 80))},{_f(rng.between(420, gy))})"><g class="st-firefly" style="animation-delay:{_f(-rng.between(0, 5))}s;'
                       f'animation-duration:{_f(rng.between(4, 8))}s"><circle r="14" fill="#ffe88a" opacity=".25"/><circle r="5" fill="#fff3b0"/></g></g>')
    return "".join(out)


# ------------------------------------------------------------------------------------- CSS

SCOPE = ":is(.scene.is-active,.scene.is-leaving) "


def _kf(name, steps):
    return f"@keyframes {name}{{" + "".join(f"{p}%{{{body}}}" for p, body in steps) + "}"


def stage_css() -> str:
    """Static stylesheet shared by every stage scene (animations only run while the scene is active)."""
    css = [".scene.stage{padding:0;display:block}",
           # fx.js wraps scene content in a flex .cam box: make it a full-bleed positioning context for the stage
           ".scene.stage .cam{position:absolute;inset:0;display:block;width:auto;padding:0;gap:0;perspective:none;will-change:auto}",
           ".st-svg{position:absolute;inset:0;width:100%;height:100%;overflow:hidden}",
           ".st-cap{position:absolute;left:0;right:0;top:56px;text-align:center;font-size:60px;font-weight:800;letter-spacing:-.02em;color:#fff;"
           "text-shadow:0 4px 24px rgba(0,0,0,.55),0 1px 3px rgba(0,0,0,.6);padding:0 160px}",]
    for key, d in ACTION_DEFS.items():
        frames = d["frames"]
        parts = [k for k in ROT_PARTS if any(k in f for _, f in frames)]
        if any({"y", "sx", "sy"} & f.keys() for _, f in frames):
            parts.append("bob")
        for part in parts:
            steps = [(p, "transform:" + (_bob(f) if part == "bob" else f"rotate({_f(_rot(part, f))}deg)")) for p, f in frames]
            css.append(_kf(f"st-{key}-{part}", steps))
            timing = "steps(1,end)" if d.get("steps") else "ease-in-out"
            css.append(f"{SCOPE}.st-a.a-{key} .p-{part}{{animation:st-{key}-{part} calc(var(--beat) * {d['dur']}) {timing} infinite}}")
    css.append(_kf("st-move", [(0, "transform:translateX(var(--mx0))"), (100, "transform:translateX(var(--mx1))")]))
    css.append(f"{SCOPE}.st-mv.go{{animation:st-move var(--dur) linear both}}")
    css.append(_kf("st-push", [(0, "transform:scale(1)"), (100, "transform:scale(1.07)")]))
    css.append(f"{SCOPE}.st-env{{transform-origin:960px 560px;animation:st-push var(--dur) ease-in-out both}}")
    css.append(_kf("st-twinkle", [(0, "opacity:.25"), (50, "opacity:1"), (100, "opacity:.25")]))
    css.append(f"{SCOPE}.st-star{{animation:st-twinkle 2.6s ease-in-out infinite}}")
    css.append(_kf("st-drift", [(0, "transform:translateX(-600px)"), (100, "transform:translateX(2700px)")]))
    css.append(f"{SCOPE}.st-cloud{{animation:st-drift 60s linear infinite}}")
    css.append(_kf("st-shimmer", [(0, "opacity:.05"), (50, "opacity:.55"), (100, "opacity:.05")]))
    css.append(f"{SCOPE}.st-shim{{animation:st-shimmer 3s ease-in-out infinite}}")
    css.append(_kf("st-wavex", [(0, "transform:translateX(0)"), (100, "transform:translateX(-200px)")]))
    css.append(f"{SCOPE}.st-wave{{animation:st-wavex 6s linear infinite}}")
    css.append(_kf("st-swayk", [(0, "transform:rotate(-3deg)"), (50, "transform:rotate(3deg)"), (100, "transform:rotate(-3deg)")]))
    css.append(f"{SCOPE}.st-sway{{animation:st-swayk 3.4s ease-in-out infinite}}")
    css.append(_kf("st-tilecolor", [(0, "opacity:.25"), (50, "opacity:.85"), (100, "opacity:.25")]))
    css.append(f"{SCOPE}.st-tile{{animation:st-tilecolor 1.75s ease-in-out infinite}}")
    css.append(_kf("st-beamk", [(0, "transform:rotate(-14deg)"), (50, "transform:rotate(14deg)"), (100, "transform:rotate(-14deg)")]))
    css.append(f"{SCOPE}.st-beam{{animation:st-beamk 4.2s ease-in-out infinite}}{SCOPE}.st-beam[data-i='1']{{animation-delay:-2s}}{SCOPE}.st-beam[data-i='2']{{animation-delay:-1s;animation-direction:reverse}}")
    css.append(_kf("st-blinkk", [(0, "opacity:1"), (46, "opacity:1"), (50, "opacity:.15"), (54, "opacity:1"), (100, "opacity:1")]))
    css.append(f"{SCOPE}.st-blink{{animation:st-blinkk 3.2s linear infinite}}")
    css.append(_kf("st-ripplek", [(0, "transform:scale(.25);opacity:.9"), (100, "transform:scale(1.5);opacity:0")]))
    css.append(f"{SCOPE}.st-ripple{{animation:st-ripplek 1.6s ease-out infinite}}{SCOPE}.st-ripple.r2{{animation-delay:-.55s}}{SCOPE}.st-ripple.r3{{animation-delay:-1.1s}}")
    css.append(_kf("st-notek", [(0, "transform:translate(0,0) rotate(-8deg) scale(.6);opacity:0"), (15, "opacity:1"),
                                (100, "transform:translate(var(--dx),-320px) rotate(12deg) scale(1.1);opacity:0")]))
    css.append(f"{SCOPE}.st-note{{animation:st-notek 4.4s ease-out infinite;opacity:0}}")
    css.append(_kf("st-sparkk", [(0, "transform:scale(0) rotate(0deg);opacity:0"), (50, "transform:scale(1) rotate(90deg);opacity:1"), (100, "transform:scale(0) rotate(180deg);opacity:0")]))
    css.append(f"{SCOPE}.st-spark{{animation:st-sparkk 2.2s ease-in-out infinite;opacity:0}}")
    css.append(_kf("st-fishk", [(0, "opacity:0;transform:translate(0,30px) rotate(-62deg)"), (3, "opacity:1"),
                                (13, "transform:translate(70px,-130px) rotate(-8deg)"), (24, "transform:translate(140px,24px) rotate(62deg);opacity:1"),
                                (27, "opacity:0;transform:translate(150px,40px) rotate(70deg)"), (100, "opacity:0;transform:translate(150px,40px)")]))
    css.append(f"{SCOPE}.st-fish{{animation:st-fishk 5s ease-in-out infinite;opacity:0}}")
    css.append(_kf("st-flutterk", [(0, "transform:translate(0,0)"), (25, "transform:translate(130px,-60px)"), (50, "transform:translate(40px,-140px)"),
                                   (75, "transform:translate(-120px,-50px)"), (100, "transform:translate(0,0)")]))
    css.append(f"{SCOPE}.st-flutter{{animation:st-flutterk 7s ease-in-out infinite}}")
    css.append(_kf("st-wingk", [(0, "transform:scaleX(1)"), (100, "transform:scaleX(.25)")]))
    css.append(f"{SCOPE}.st-wing{{animation:st-wingk .22s ease-in-out infinite alternate}}")
    css.append(_kf("st-confk", [(0, "transform:translate(0,0) rotate(0deg)"), (100, "transform:translate(var(--sw),1180px) rotate(900deg)")]))
    css.append(f"{SCOPE}.st-conf{{animation:st-confk 5s linear infinite}}")
    css.append(_kf("st-fireflyk", [(0, "transform:translate(0,0);opacity:.1"), (25, "transform:translate(60px,-40px);opacity:1"), (50, "transform:translate(10px,-90px);opacity:.2"),
                                   (75, "transform:translate(-60px,-30px);opacity:1"), (100, "transform:translate(0,0);opacity:.1")]))
    css.append(f"{SCOPE}.st-firefly{{animation:st-fireflyk 6s ease-in-out infinite}}")
    css.append(f".st-a.t-night{{filter:brightness(.78) saturate(.9)}}.st-a.t-sunset{{filter:sepia(.18) saturate(1.1) brightness(.95)}}")
    return "\n".join(css)


# ------------------------------------------------------------------------------------- compile

def _ripples(gy):
    return "".join(f'<g transform="translate({x},0)"><ellipse class="st-ripple {c}" rx="90" ry="20" fill="none" stroke="#fff" stroke-width="5"/></g>'
                   for x in (-26, 26) for c in ("", "r2", "r3"))


def render_stage(stage: dict, duration: float, sid: str):
    """Returns (svg, caption_anchor_ok). `stage` must come from normalize_stage."""
    env, time = stage["env"], stage["time"]
    rng = Rng(f"{sid}:{env}:{time}")
    P = PAL[time]
    u = re.sub(r"[^a-z0-9]", "", sid.lower())[:12] + "-"
    gy = GROUND[env]
    dur = max(1.0, duration)
    parts = [f'<svg class="st-svg" viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" preserveAspectRatio="xMidYMid slice" style="--dur:{_f(dur)}s">',
             f'<g class="st-env">{_ENVIRONMENTS[env](P, time, u, rng)}</g>']

    def actor_wrap(actor, inner, shadow=False):
        x0, x1 = actor["from"] * W / 100, actor["to"] * W / 100
        travel = abs(x1 - x0) > 1
        flip = " scale(-1,1)" if x1 < x0 else ""
        beat = ACTION_DEFS[action_key(actor)]["beat"] / actor["tempo"]
        cls = f"st-a a-{action_key(actor)} t-{time}"
        mv = f'class="st-mv{" go" if travel else ""}" style="--mx0:0px;--mx1:{_f(x1 - x0)}px"'
        sh = '<ellipse cx="0" cy="6" rx="86" ry="16" fill="#000" opacity=".25"/>' if shadow and env != "pond" else ""
        return (f'<g transform="translate({_f(x0)},{gy}) scale({_f(actor["scale"])})"><g {mv}>{sh}'
                f'<g class="{cls}" style="--beat:{_f(beat)}s"><g transform="scale({1 if not flip else -1},1)">{inner}</g></g></g></g>')

    names = stage["props"] if stage["props"] is not None else default_props(stage)
    parts.append(_props(names, stage, gy, rng))  # behind the actors, so nothing floats across a face
    for actor in stage["actors"]:
        parts.append(actor_wrap(actor, character_svg(actor), shadow=True))
    if env == "pond":  # water in front of the legs: wading
        parts.append(f'<defs><linearGradient id="{u}fw" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{P["water"]}" stop-opacity=".62"/>'
                     f'<stop offset="1" stop-color="{P["water2"]}" stop-opacity=".92"/></linearGradient></defs>'
                     f'<rect x="-20" y="{gy - 62}" width="{W + 40}" height="{H - gy + 82}" fill="url(#{u}fw)"/>'
                     f'<path class="st-wave" d="M-200,{gy - 62} q50,-14 100,0 t100,0 t100,0 t100,0 t100,0 t100,0 t100,0 t100,0 t100,0 t100,0 t100,0 t100,0 t100,0 t100,0 t100,0 t100,0 t100,0 t100,0 t100,0 t100,0 t100,0" '
                     f'stroke="#fff" stroke-opacity=".6" stroke-width="5" fill="none"/>')
        for actor in stage["actors"]:
            parts.append(actor_wrap(actor, f'<g transform="translate(0,-4)">{_ripples(gy)}</g>'))
    parts.append("</svg>")
    return "".join(parts)


def preview_svg(stage: dict, sid="preview") -> str:
    """Static (no CSS animation) SVG for quick visual checks outside the renderer."""
    return render_stage(stage, 6, sid)


def pose_sheet(actor: dict, keys=None) -> str:
    """A contact sheet of every key pose of an actor's action, drawn with baked transforms."""
    key = keys or action_key(actor)
    frames = ACTION_DEFS[key]["frames"]
    n = len(frames)
    cell = 420
    body = "".join(f'<g transform="translate({cell * i + cell // 2},640) scale(.95)">{character_svg(actor, dict(f))}</g>' for i, (p, f) in enumerate(frames))
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {cell * n} 700"><rect width="{cell * n}" height="700" fill="#e9eef7"/>{body}</svg>')
