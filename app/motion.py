"""Motion layouts: animated diagrams, orbits, timelines and kinetic typography.

The slide layouts (list, steps, compare...) put text on cards and fade them in. These layouts are
real motion graphics instead: the model supplies the content (nodes, events, words) and this
module turns it into SVG + HTML with drawn-on connectors, travelling data packets, orbiting
bodies, a comet racing along a timeline and big moving type over animated shapes.

  flow      nodes joined by connectors that draw themselves; packets flow along them
            (shape: line = a pipeline, cycle = a loop, hub = one source feeding many)
  orbit     a glowing core with items travelling around it on elliptical orbits
  timeline  a path that draws itself, markers that pop, a comet that keeps travelling
  kinetic   one huge headline that flies in word by word, punch lines and animated shapes

Everything is plain CSS animation scoped to `.scene.is-active`, so it restarts with its scene and
the renderer's virtual clock can seek it frame by frame. No JavaScript, no libraries.
Reveals use the same `--d` delays as the other layouts, so they land as the narration says them.
"""
import html as _html
import math

from .stage import Rng

esc = _html.escape

LAYOUTS = ("flow", "orbit", "timeline", "kinetic")
FLOW_SHAPES = ("line", "cycle", "hub")
LIMITS = {"flow": (2, 7), "orbit": (2, 8), "timeline": (2, 6), "kinetic": (0, 4)}
COLORS = ("var(--accent)", "var(--accent-2)", "var(--accent-3)", "var(--warn)", "var(--good)", "var(--bad)")
W, H = 1920, 1080


# ---------------------------------------------------------------------------------- normalizing

def _text(value, limit) -> str:
    return " ".join(str(value or "").split())[:limit]


def _pick(value, allowed, default=None):
    value = str(value or "").strip().lower()
    return value if value in allowed else default


def _items(raw, layout, icons):
    lo, hi = LIMITS[layout]
    out = []
    for i, it in enumerate((raw if isinstance(raw, list) else [])[:hi]):
        if isinstance(it, str):
            it = {"title": it}
        if not isinstance(it, dict) or not (it.get("title") or it.get("text") or it.get("label")):
            continue
        out.append({
            "title": _text(it.get("title"), 40), "text": _text(it.get("text"), 90), "label": _text(it.get("label") or it.get("year"), 14),
            "icon": _pick(it.get("icon"), icons) or icons[(i * 5 + 2) % len(icons)],
        })
    if len(out) < lo:
        raise ValueError(f"layout '{layout}' needs at least {lo} items (each {{title, text?, icon?}}"
                         + (", label? for the date" if layout == "timeline" else "") + f"), got {len(out)}.")
    return out


def normalize_motion(layout: str, raw: dict, icons) -> dict:
    """Validate the motion fields of a scene. Raises ValueError with a fixable message."""
    m = {"items": _items(raw.get("items") or raw.get("nodes") or raw.get("events"), layout, icons)}
    n = len(m["items"])
    if layout == "flow":
        shape = _pick(raw.get("shape"), FLOW_SHAPES, "line")
        edges = []
        for e in raw.get("edges") if isinstance(raw.get("edges"), list) else []:
            try:
                a, b = int(e[0]), int(e[1])
            except (TypeError, ValueError, IndexError, KeyError):
                continue
            if 0 <= a < n and 0 <= b < n and a != b and [a, b] not in edges:
                edges.append([a, b])
        if not edges:
            edges = [[0, i] for i in range(1, n)] if shape == "hub" else [[i, i + 1] for i in range(n - 1)]
            if shape == "cycle" and n > 2:
                edges.append([n - 1, 0])
        m.update(shape=shape, edges=edges[:12])
    elif layout == "orbit":
        c = raw.get("center") if isinstance(raw.get("center"), dict) else {}
        m["center"] = {"title": _text(c.get("title") or raw.get("headline"), 24), "icon": _pick(c.get("icon"), icons) or "star"}
    elif layout == "kinetic":
        lines = raw.get("lines")
        lines = [_text(x if isinstance(x, str) else (x or {}).get("title") if isinstance(x, dict) else "", 48) for x in (lines if isinstance(lines, list) else [])]
        m["lines"] = [x for x in lines if x][:4] or [it["title"] for it in m["items"] if it["title"]][:4]
        m["items"] = []
    return m


# ---------------------------------------------------------------------------------------- geometry

def _f(n) -> str:
    return f"{n:.1f}".rstrip("0").rstrip(".")


def _unit(dx, dy):
    d = math.hypot(dx, dy) or 1.0
    return dx / d, dy / d


def _curve(a, b, radius, bulge_from=None, bulge=0.0):
    """A path between two node centres, trimmed to the node circles. Returns (d, end_point, end_angle_deg)."""
    ux, uy = _unit(b[0] - a[0], b[1] - a[1])
    s = (a[0] + ux * radius, a[1] + uy * radius)
    e = (b[0] - ux * (radius + 8), b[1] - uy * (radius + 8))
    mx, my = (s[0] + e[0]) / 2, (s[1] + e[1]) / 2
    if bulge_from is not None:
        ox, oy = _unit(mx - bulge_from[0], my - bulge_from[1])
    else:
        ox, oy = -uy, ux
    c = (mx + ox * bulge, my + oy * bulge)
    ang = math.degrees(math.atan2(e[1] - c[1], e[0] - c[0]))
    return f"M{_f(s[0])} {_f(s[1])} Q{_f(c[0])} {_f(c[1])} {_f(e[0])} {_f(e[1])}", e, ang


def flow_positions(shape, n):
    if shape == "cycle" and n > 2:
        return [(960 + 560 * math.sin(2 * math.pi * i / n), 640 - 215 * math.cos(2 * math.pi * i / n)) for i in range(n)]
    if shape == "hub" and n > 2:
        pos = [(960.0, 640.0)]
        for i in range(n - 1):
            a = math.pi * (0.5 + 2 * i / (n - 1)) + math.pi / (n - 1)  # start below-left, go round
            pos.append((960 + 640 * math.cos(a), 640 + 250 * math.sin(a)))
        return pos
    xs = [960.0] if n == 1 else [200 + i * (1520 / (n - 1)) for i in range(n)]
    return [(x, 640 + (-52 if i % 2 else 52)) for i, x in enumerate(xs)]


# --------------------------------------------------------------------------------------- rendering

def _icon(name):
    return f'<i data-icon="{name}"></i>'


def _flow(m, clock):
    items, shape, edges = m["items"], m["shape"], m["edges"]
    n = len(items)
    pos = flow_positions(shape, n)
    R = 82
    tags = {}
    order = []  # reveal order: a node, then the connectors that arrive at it
    seen = set()
    for a, b in edges:
        for k in (a, b):
            if k not in seen:
                seen.add(k)
                order.append(("n", k))
        order.append(("e", (a, b)))
    for k in range(n):
        if k not in seen:
            order.append(("n", k))
    svg, packets, nodes = [], [], []
    for kind, ref in order:
        tags[(kind, ref)] = clock.tag()
    for ei, (a, b) in enumerate(edges):
        color = COLORS[(a) % 3]
        if shape == "cycle":
            d, end, ang = _curve(pos[a], pos[b], R, bulge_from=(960, 640), bulge=46)
        elif shape == "hub":
            d, end, ang = _curve(pos[a], pos[b], R, bulge=34 * (1 if ei % 2 else -1))
        else:
            d, end, ang = _curve(pos[a], pos[b], R, bulge=30 * (1 if ei % 2 else -1))
        t = tags[("e", (a, b))]
        svg.append(f'<path d="{d}" pathLength="1" fill="none" stroke="{color}" stroke-opacity=".28" stroke-width="16" stroke-linecap="round" class="draw" style="--d:{t}"/>'
                   f'<path d="{d}" pathLength="1" fill="none" stroke="{color}" stroke-width="5" stroke-linecap="round" class="draw" style="--d:{t}"/>'
                   f'<g transform="translate({_f(end[0])},{_f(end[1])}) rotate({_f(ang)})"><path d="M2 0 L-26 -14 L-26 14 Z" fill="{color}" class="pop" style="--d:calc({t} + 1.1s)"/></g>')
        packets.append(f'<div class="mo-pkt" style="offset-path:path(\'{d}\');--c:{color};--pd:calc({t} + 1.5s + {ei * .55:.2f}s);--tt:{2.2 + (ei % 3) * .35:.2f}s"></div>')
    for k, it in enumerate(items):
        x, y = pos[k]
        c = COLORS[k % 3]
        big = shape == "hub" and k == 0 and n > 2
        sz = 190 if big else 150
        nodes.append(
            f'<div class="mo-node pop" style="left:{_f(x - 150)}px;top:{_f(y - sz / 2)}px;--d:{tags[("n", k)]};--c:{c};--sz:{sz}px">'
            f'<div class="mo-float" style="animation-delay:-{k * .9:.1f}s"><div class="mo-orb">{_icon(it["icon"])}<u class="mo-ring"></u><u class="mo-ring r2"></u></div>'
            f'<div class="mo-nt">{esc(it["title"])}</div>' + (f'<div class="mo-nx">{esc(it["text"])}</div>' if it["text"] else "") + '</div></div>')
    return (f'<svg class="mo-svg" viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg">{"".join(svg)}</svg>' + "".join(packets) + "".join(nodes))


def _orbit(m, clock):
    items, center = m["items"], m["center"]
    n = len(items)
    cx, cy = 960, 610
    rings = [(520, 215)] if n <= 4 else [(380, 160), (700, 255)]
    per = [n] if len(rings) == 1 else [math.ceil(n / 2), n - math.ceil(n / 2)]
    out = [f'<svg class="mo-svg" viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg">']
    for rx, ry in rings:
        out.append(f'<ellipse cx="{cx}" cy="{cy}" rx="{rx}" ry="{ry}" fill="none" stroke="var(--accent)" stroke-opacity=".35" stroke-width="3" stroke-dasharray="4 16" stroke-linecap="round" class="fade-in" style="--d:{clock.tag()}"/>')
    out.append("</svg>")
    core_tag = clock.tag()
    out.append(f'<div class="mo-core pop" style="left:{cx - 130}px;top:{cy - 130}px;--d:{core_tag}"><div class="mo-core-in">{_icon(center["icon"])}<u class="mo-ring"></u><u class="mo-ring r2"></u><u class="mo-ring r3"></u></div>'
               + (f'<div class="mo-core-t">{esc(center["title"])}</div>' if center["title"] else "") + '</div>')
    k = 0
    for ri, ((rx, ry), count) in enumerate(zip(rings, per)):
        path = f"M{cx - rx} {cy} A{rx} {ry} 0 1 1 {cx + rx} {cy} A{rx} {ry} 0 1 1 {cx - rx} {cy}"
        period = 34 + ri * 16
        for j in range(count):
            it = items[k]
            phase = -(j / count) * period - ri * 7
            out.append(
                f'<div class="mo-orbiter" style="offset-path:path(\'{path}\');animation-duration:{period}s,{period}s;animation-delay:{phase:.1f}s,{phase:.1f}s;--c:{COLORS[k % 3]}">'
                f'<div class="mo-sat pop" style="--d:{clock.tag()}"><div class="mo-orb sm">{_icon(it["icon"])}</div><div class="mo-nt sm">{esc(it["title"])}</div>'
                + (f'<div class="mo-nx sm">{esc(it["text"])}</div>' if it["text"] else "") + '</div></div>')
            k += 1
    return "".join(out)


def _timeline(m, clock):
    items = m["items"]
    n = len(items)
    xs = [200 + i * (1520 / (n - 1)) for i in range(n)]
    ys = [650 + (-80 if i % 2 == 0 else 80) for i in range(n)]
    pts = [(80.0, ys[0])] + list(zip(xs, ys)) + [(1840.0, ys[-1])]

    def seg(p, q):
        mx = (p[0] + q[0]) / 2
        return f"M{_f(p[0])} {_f(p[1])} C{_f(mx)} {_f(p[1])} {_f(mx)} {_f(q[1])} {_f(q[0])} {_f(q[1])}"

    full = f"M{_f(pts[0][0])} {_f(pts[0][1])}" + "".join(
        f" C{_f((p[0] + q[0]) / 2)} {_f(p[1])} {_f((p[0] + q[0]) / 2)} {_f(q[1])} {_f(q[0])} {_f(q[1])}" for p, q in zip(pts, pts[1:]))
    svg, marks = [], []
    first = clock.tag()
    svg.append(f'<path d="{seg(pts[0], pts[1])}" pathLength="1" fill="none" stroke="var(--accent)" stroke-opacity=".3" stroke-width="18" stroke-linecap="round" class="draw" style="--d:{first}"/>'
               f'<path d="{seg(pts[0], pts[1])}" pathLength="1" fill="none" stroke="var(--accent)" stroke-width="6" stroke-linecap="round" class="draw" style="--d:{first}"/>')
    for i, it in enumerate(items):
        t = clock.tag()
        c = COLORS[i % 3]
        up = i % 2 == 0
        marks.append(
            f'<div class="mo-mark pop" style="left:{_f(xs[i] - 190)}px;top:{_f(ys[i] - (265 if up else -52))}px;--d:{t};--c:{c}">'
            + ('<div class="mo-tl up">' if up else '') + (f'<div class="mo-tl-label">{esc(it["label"])}</div>' if it["label"] else "")
            + f'<div class="mo-nt">{esc(it["title"])}</div>' + (f'<div class="mo-nx">{esc(it["text"])}</div>' if it["text"] else "") + ('</div>' if up else '') + '</div>')
        svg.append(f'<g transform="translate({_f(xs[i])},{_f(ys[i])})"><circle r="30" fill="{c}" fill-opacity=".25" class="pop" style="--d:{t}"/><circle r="17" fill="{c}" class="pop" style="--d:{t}"/>'
                   f'<circle r="17" fill="none" stroke="{c}" stroke-width="3" class="mo-pulse" style="--d:calc({t} + .6s)"/></g>')
        if i < n - 1:
            t2 = clock.tag()
            a, b = pts[i + 1], pts[i + 2]
            svg.append(f'<path d="{seg(a, b)}" pathLength="1" fill="none" stroke="var(--accent)" stroke-opacity=".3" stroke-width="18" stroke-linecap="round" class="draw" style="--d:{t2}"/>'
                       f'<path d="{seg(a, b)}" pathLength="1" fill="none" stroke="var(--accent)" stroke-width="6" stroke-linecap="round" class="draw" style="--d:{t2}"/>')
    tail = clock.tag()
    svg.append(f'<path d="{seg(pts[-2], pts[-1])}" pathLength="1" fill="none" stroke="var(--accent)" stroke-opacity=".3" stroke-width="18" stroke-linecap="round" class="draw" style="--d:{tail}"/>'
               f'<path d="{seg(pts[-2], pts[-1])}" pathLength="1" fill="none" stroke="var(--accent)" stroke-width="6" stroke-linecap="round" class="draw" style="--d:{tail}"/>')
    comet = "".join(f'<div class="mo-comet c{k}" style="offset-path:path(\'{full}\');--pd:calc({tail} + {k * .09:.2f}s)"></div>' for k in (3, 2, 1, 0))
    return f'<svg class="mo-svg" viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg">{"".join(svg)}</svg>' + comet + "".join(marks)


def _kinetic(m, clock, sc, kin):
    rng = Rng(sc["id"])
    shapes = []
    for i in range(3):  # shockwave rings
        shapes.append(f'<circle cx="960" cy="540" r="330" fill="none" stroke="{COLORS[i]}" stroke-width="5" class="mo-wave" style="animation-delay:{-i * 1.6:.1f}s"/>')
    polys = ("0,-70 61,35 -61,35", "-56,-56 56,-56 56,56 -56,56", "0,-70 61,-35 61,35 0,70 -61,35 -61,-35", "0,-80 20,-25 76,-25 30,10 48,66 0,32 -48,66 -30,10 -76,-25 -20,-25")
    spots = [(180, 190), (1700, 170), (230, 880), (1660, 860), (880, 120), (1010, 960), (110, 520), (1810, 560)]
    for i, (x, y) in enumerate(spots):
        k = rng.between(.8, 1.7)
        shapes.append(f'<g transform="translate({x},{y}) scale({k:.2f})"><polygon points="{polys[i % 4]}" fill="none" stroke="{COLORS[i % 3]}" stroke-width="{4 / k:.1f}" stroke-opacity=".7" '
                      f'class="mo-spin{" rev" if i % 2 else ""}" style="animation-duration:{rng.between(14, 30):.0f}s"/></g>')
    for i in range(26):
        shapes.append(f'<circle cx="{rng.between(0, W):.0f}" cy="{rng.between(0, H):.0f}" r="{rng.between(2, 7):.1f}" fill="{COLORS[i % 3]}" opacity=".6" class="mo-dot" '
                      f'style="animation-delay:{-rng.between(0, 6):.1f}s;animation-duration:{rng.between(4, 9):.1f}s"/>')
    sweeps = "".join(f'<div class="mo-sweep" style="animation-delay:{-i * 2.4:.1f}s;top:{-200 + i * 40}px"></div>' for i in range(2))
    lines = []
    for i, text in enumerate(m["lines"]):
        side = "slide-in-left" if i % 2 == 0 else "slide-in-right"
        lines.append(f'<div class="mo-line {side}" style="--d:{clock.tag()};--c:{COLORS[i % 3]}"><span>{esc(text)}</span><b class="grow-x" style="--d:calc({clock.tag()} + .3s)"></b></div>')
    return (f'<svg class="mo-svg" viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg">{"".join(shapes)}</svg>{sweeps}'
            f'<div class="mo-kin-wrap">{{HEAD}}<div class="mo-lines">{"".join(lines)}</div></div>')


def render_motion(sc: dict, clock, head_html: str, kin: str) -> str:
    """The inner markup of a motion scene. `head_html` is the kit's kicker/headline/body block."""
    layout, m = sc["layout"], sc["motion"]
    if layout == "kinetic":
        return '<div class="mo">' + _kinetic(m, clock, sc, kin).replace("{HEAD}", head_html) + "</div>"
    body = {"flow": lambda: _flow(m, clock), "orbit": lambda: _orbit(m, clock), "timeline": lambda: _timeline(m, clock)}[layout]()
    return f'<div class="mo"><div class="mo-head">{head_html}</div>{body}</div>'


# ---------------------------------------------------------------------------------------------- CSS

def motion_css() -> str:
    S = ".scene.is-active "
    return f"""
.scene.motion{{padding:0}}
.scene.motion .cam{{position:absolute;inset:0;display:block;width:auto;padding:0;gap:0;align-items:initial;justify-content:initial}}
.mo{{position:absolute;inset:0}}
.mo-svg{{position:absolute;inset:0;width:1920px;height:1080px;overflow:visible}}
.mo-head{{position:absolute;left:140px;top:84px;width:1300px;display:flex;flex-direction:column;gap:14px;z-index:3}}
.mo-node{{position:absolute;width:300px;text-align:center;z-index:2}}
.mo-orb{{position:relative;width:var(--sz,150px);height:var(--sz,150px);margin:0 auto 16px;border-radius:50%;display:grid;place-items:center;color:#fff;
  background:radial-gradient(circle at 32% 28%,color-mix(in srgb,var(--c) 70%,#fff),var(--c) 55%,color-mix(in srgb,var(--c) 55%,#000));
  box-shadow:0 0 0 6px rgba(255,255,255,.07),0 18px 44px -10px color-mix(in srgb,var(--c) 70%,transparent),inset 0 2px 0 rgba(255,255,255,.45)}}
.mo-orb svg{{width:calc(var(--sz,150px) * .46);height:calc(var(--sz,150px) * .46)}}
.mo-orb.sm{{--sz:116px;margin-bottom:10px}}
.mo-ring{{position:absolute;inset:0;border-radius:50%;border:3px solid var(--c);opacity:0;pointer-events:none}}
.mo-nt{{font-size:36px;font-weight:750;letter-spacing:-.015em;line-height:1.1;text-shadow:0 2px 14px rgba(0,0,0,.5)}}
.mo-nt.sm{{font-size:30px}}
.mo-nx{{font-size:25px;line-height:1.3;color:var(--ink-dim);margin-top:6px;text-shadow:0 2px 12px rgba(0,0,0,.5)}}
.mo-nx.sm{{font-size:22px}}
.mo-pkt{{position:absolute;left:0;top:0;width:38px;height:38px;border-radius:50%;opacity:0;offset-rotate:0deg;z-index:1;
  background:radial-gradient(circle,#fff 0 22%,var(--c) 48%,transparent 72%)}}
.mo-core{{position:absolute;width:260px;text-align:center;z-index:2}}
.mo-core-in{{position:relative;width:260px;height:260px;border-radius:50%;display:grid;place-items:center;color:#fff;--c:var(--accent-2);
  background:radial-gradient(circle at 34% 28%,#fff6,var(--accent-2) 42%,var(--accent) 100%);box-shadow:0 0 120px 20px color-mix(in srgb,var(--accent-2) 55%,transparent)}}
.mo-core-in svg{{width:112px;height:112px}}
.mo-core-t{{font-size:40px;font-weight:800;margin-top:20px;text-shadow:0 2px 14px rgba(0,0,0,.6)}}
.mo-orbiter{{position:absolute;left:0;top:0;width:300px;offset-rotate:0deg;offset-anchor:50% 40%;text-align:center;z-index:2}}
.mo-sat{{width:300px;margin-left:0}}
.mo-mark{{position:absolute;width:380px;text-align:center;z-index:2}}
.mo-tl.up{{display:flex;flex-direction:column;justify-content:flex-end;height:230px}}
.mo-tl-label{{font-size:46px;font-weight:850;letter-spacing:-.03em;color:var(--c);margin-bottom:4px;text-shadow:0 2px 14px rgba(0,0,0,.45)}}
.mo-comet{{position:absolute;left:0;top:0;border-radius:50%;opacity:0;offset-rotate:0deg;z-index:2;background:radial-gradient(circle,#fff 0 30%,var(--accent) 55%,transparent 75%)}}
.mo-comet.c0{{width:54px;height:54px}}.mo-comet.c1{{width:40px;height:40px}}.mo-comet.c2{{width:28px;height:28px}}.mo-comet.c3{{width:18px;height:18px}}
.mo-kin-wrap{{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;gap:34px;padding:0 120px;z-index:2}}
.mo-kin-wrap .title{{font-size:150px;line-height:1.0;max-width:1680px;text-shadow:0 8px 60px rgba(0,0,0,.45)}}
.mo-kin-wrap .eyebrow{{font-size:30px}}
.mo-lines{{display:flex;flex-direction:column;gap:12px;align-items:center}}
.mo-line{{position:relative;font-size:52px;font-weight:700;letter-spacing:-.02em;color:var(--ink-dim)}}
.mo-line b{{position:absolute;left:0;right:0;bottom:-6px;height:6px;border-radius:3px;background:var(--c)}}
.mo-sweep{{position:absolute;left:0;width:340px;height:1500px;transform:rotate(18deg) translateX(-900px);background:linear-gradient(90deg,transparent,rgba(255,255,255,.07),transparent);z-index:1}}
{S}.mo-float{{animation:mo-float 4.8s ease-in-out infinite alternate}}
{S}.mo-ring{{animation:mo-ring 3.4s ease-out infinite;animation-delay:calc(var(--d,0s) + .9s)}}
{S}.mo-ring.r2{{animation-delay:calc(var(--d,0s) + 2.0s)}}
{S}.mo-ring.r3{{animation-delay:calc(var(--d,0s) + 2.9s)}}
{S}.mo-pkt{{animation:mo-travel var(--tt,2.6s) linear var(--pd,3s) infinite}}
{S}.mo-orbiter{{animation-name:mo-orbit,mo-depth;animation-timing-function:linear,linear;animation-iteration-count:infinite,infinite}}
{S}.mo-core-in{{animation:mo-breathe 3.2s ease-in-out infinite}}
{S}.mo-comet{{animation:mo-travel 5.5s linear var(--pd,3s) infinite}}
{S}.mo-pulse{{animation:mo-ring 2.8s ease-out infinite;animation-delay:var(--d,0s)}}
{S}.mo-wave{{transform-origin:960px 540px;animation:mo-wave 4.8s ease-out infinite}}
{S}.mo-spin{{transform-box:fill-box;transform-origin:center;animation:mo-spin 20s linear infinite}}
{S}.mo-spin.rev{{animation-direction:reverse}}
{S}.mo-dot{{animation:mo-dot 6s ease-in-out infinite alternate}}
{S}.mo-sweep{{animation:mo-sweep 7s linear infinite}}
@keyframes mo-float{{from{{transform:translateY(-9px)}}to{{transform:translateY(9px)}}}}
@keyframes mo-ring{{0%{{opacity:.75;transform:scale(1)}}100%{{opacity:0;transform:scale(1.9)}}}}
@keyframes mo-travel{{0%{{offset-distance:0%;opacity:0}}12%{{opacity:1}}88%{{opacity:1}}100%{{offset-distance:100%;opacity:0}}}}
@keyframes mo-orbit{{from{{offset-distance:0%}}to{{offset-distance:100%}}}}
@keyframes mo-depth{{0%{{scale:1}}25%{{scale:.78}}50%{{scale:1}}75%{{scale:1.2}}100%{{scale:1}}}}
@keyframes mo-breathe{{0%,100%{{transform:scale(1)}}50%{{transform:scale(1.06)}}}}
@keyframes mo-wave{{0%{{transform:scale(.3);opacity:.7}}100%{{transform:scale(2.6);opacity:0}}}}
@keyframes mo-spin{{to{{transform:rotate(360deg)}}}}
@keyframes mo-dot{{from{{transform:translate(0,0);opacity:.2}}to{{transform:translate(30px,-60px);opacity:.8}}}}
@keyframes mo-sweep{{from{{transform:rotate(18deg) translateX(-900px)}}to{{transform:rotate(18deg) translateX(2800px)}}}}
"""
