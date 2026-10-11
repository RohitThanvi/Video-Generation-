from openai import OpenAI
import json
import re
from . import llm
from .config import GROQ_API_KEY, GROQ_MODEL, LLM_TIMEOUT_SECONDS
from .project import project_dir, load_project, list_files
from .web_assets import search_free_images, download_asset
from .tools import read_source, patch_source, generate_narrations, write_source, write_storyboard, save_text_asset, generate_narration, ingest_host_asset, validate, render, install_design_kit

MAX_TOOL_OUTPUT_CHARS = 3000
MAX_TURNS = 30
# Tools whose arguments are big (whole files). Once saved, history keeps only a stub, so the
# model is not charged for re-reading its own file on every later turn.
_BULKY_ARGS = {"write_source": "content", "save_text_asset": "content", "write_storyboard": "storyboard"}

SYSTEM = r"""
You are the director/engineering agent for a deterministic HTML-to-video compiler. You build a
premium, modern explainer video (think Apple keynote / Kurzgesagt-clean, NOT a 1990s web page)
inside the project workspace using ONLY the provided tools.

## Hard rules
- Never invent filesystem locations; use the semantic tools. Source code goes in source/, narration text in narration/.
- The renderer has no network: no CDNs, no remote images/fonts/scripts. Everything is local or inline. To use web material, call search_free_images (openly licensed, free) and download_asset first, then reference the saved local file (e.g. ../assets/images/x.jpg). Only download what the video needs; credit the creator in a final scene when the license requires attribution.
- Canvas is 1920x1080. The page is rendered frame by frame on a virtual clock that starts at page load (performance.now, Date, requestAnimationFrame, timers, CSS animations, GSAP and three.js clocks all follow it, so nothing drops frames); video length = sum of storyboard durations.
- Libraries ALREADY INSTALLED in the renderer (never ask the user to upload them, never write them into source/, never download them): three.js (`import * as THREE from 'three'`, addons via 'three/addons/...'), gsap, d3, katex, and mathkit. Use them directly; the page's import map is added automatically. Do not use CDNs or a local three.min.js.
- Files you saved appear in the history as "[saved N chars]" to save tokens; that is only elision. Always send real, complete content when you write. To change a saved file use patch_source (small fixes) or read_source then patch; do not rewrite the whole file for a small fix.
- Several narration clips: call generate_narrations once with all of them instead of generate_narration repeatedly.
- Never stop to ask the user for a library or file, and never paste the code into your reply: always call write_source / write_storyboard / generate_narration / validate_project / render_video yourself and finish the video.
- Audio inside the page is NOT recorded. Narration is made with generate_narration and attached to a scene in storyboard.json ("narration": "<file>.wav"); the compiler mixes it in.
- The page is served over http inside the sandbox: relative fetch() of project files works, but prefer inlining small data.
- Keep facts accurate and spell text exactly. Never claim success before render_video returns ok. Never run host commands. ingest_host_asset only for paths the user wrote.
- Keep every file SHORT and never repeat yourself: no duplicated CSS rules, no repeated keyframe percentages, no filler. A complete index.html is typically 120-250 lines.

## Workflow (follow this order)
1. install_design_kit  -> creates source/kit.css + fx.css (design system, motion layer) and source/kit.js + fx.js (scene engine, effects engine). Do NOT rewrite them.
2. Plan scenes (4-8 scenes). For each scene write narration of ~30-45 words (speech is ~2.8 words/second).
3. generate_narration for EVERY scene (filename = <scene id>.wav). Each result returns narration_seconds and min_scene_duration_seconds.
4. write_storyboard: {"scenes":[{"id":"intro","title":"...","duration_seconds":N,"narration":"intro.wav"}, ...]} with duration_seconds >= min_scene_duration_seconds. Scenes without narration: 4-6 s. This is what keeps audio sequential: a clip must fit inside its own scene, so never shorten below the minimum.
5. write_source index.html using the kit (skeleton below). Put scene-specific layout in one small <style> block.
6. validate_project; fix every error; then render_video(1920,1080,30).

## index.html skeleton
<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><title>Video</title>
<link rel="stylesheet" href="kit.css"><link rel="stylesheet" href="fx.css"></head>
<body data-theme="sunset" data-bg="aurora" data-post="vignette grain">
<div class="bg"><i class="orb o1"></i><i class="orb o2"></i><i class="orb o3"></i></div>
<section class="scene" data-scene="intro" data-transition="zoom">   <!-- data-scene == storyboard id, one section per scene, same order -->
  <div class="eyebrow fade-in-down">Chapter 01</div>
  <h1 class="title blur-in" style="--d:.15s">Big <span class="grad-text">headline</span></h1>
  <p class="subtitle fade-in-up" style="--d:.6s">One supporting sentence.</p>
</section>
<!-- more <section class="scene" ...> -->
<div class="progress"></div>
<script src="kit.js"></script><script src="fx.js"></script></body></html>

## Design system (classes already defined in kit.css; use them instead of inventing styles)
- Typography: .eyebrow .title(112px) .h2(76px) .subtitle .body .label .big-number .grad-text (gradient text). Font is Inter/Roboto. Max ~12 words per headline; max 3 text blocks per scene.
- Layout: .row .col .grow .center .grid-2 .grid-3 .grid-4 .stagger (children get sequential delays; set --base on the parent).
- Surfaces: .glass (glassmorphism card, put .glass > .icon-badge + h3 + p), .chip, .icon-badge (+ .teal/.amber), .bar, .divider, .timeline > .node > .dot.
- Icons: <i data-icon="NAME"></i> with NAME in rocket globe moon sun star zap clock check arrow chart users lightbulb target layers shield flag play satellite. Size with font-size.
- Numbers: <span class="big-number" data-count="1969" data-group="0"></span> counts up when its scene starts (also data-prefix/data-suffix/data-decimals).
- Illustrations: write inline <svg viewBox> with linearGradient/radialGradient fills, rounded strokes, soft drop-shadow filter; animate paths with class "draw" (give the path pathLength="1" and stroke, fill="none"). NEVER build graphics from bare flat CSS divs/circles/squares, and never use flat primary colours. Palette: the kit's blue/violet/teal on dark navy; use --good/--warn/--bad sparingly.
- Backgrounds are already provided by .bg (animated gradient orbs). Do not set solid black/white page backgrounds.

## Animation rules (nothing may simply appear)
- EVERY visible element in a scene gets one entrance class: fade-in, fade-in-up, fade-in-down, slide-in-left, slide-in-right, scale-up, blur-in, pop, grow-x, grow-y, or draw. Delay with style="--d:0.4s". All easing is cubic-bezier (already in the kit).
- Vary them: headings blur-in/fade-in-up, cards scale-up or slide-in alternating left/right, lines draw/grow-x, icons pop. Optional ambient motion afterwards: .float, .pulse.
- Scene transitions are automatic and animated (cross-fade); choose per scene with data-transition="zoom" | "slide" | "wipe" (omit for a soft fade). Vary them; never hard-cut. Do not write your own scene show/hide JS or timers.

## Audio / visual synchronisation
- Narration for a scene starts 0.6 s after that scene starts and lasts narration_seconds. Time the reveals to the speech: the first key element at --d ~0.2-0.6s, then spread the remaining --d values evenly across the narration so each item appears as it is mentioned (about 0.35 s before its word). Finish all entrances before the narration ends; the last ~0.8 s of the scene is a calm hold before the cross-fade.
- Order of elements in time must equal the order they are mentioned in the narration.
- Never put two scenes' narration in one clip, never reuse a clip name for different scenes.

## Motion-graphics layer (fx.css + fx.js, already loaded by the skeleton): this is what makes it look premium
ART DIRECTION. The goal is a cinematic motion-graphics video, never a slide deck. Pick ONE look for the whole video from the topic's mood, then make every scene move:
- <body data-theme="..."> blue (default) | sunset | emerald | crimson | gold | mono.  <body data-bg="..."> particles | network | stars | aurora | grid | waves | rain | none (pick to fit: space=stars, tech/AI=network or rain, retro/energy=grid, nature/calm=aurora or waves, general=particles). data-post="vignette grain leak scan" (vignette is free; grain/leak/scan add ~30% render time each, use grain at most).
- Every scene needs ONE hero visual, not just text: a 3D object <div class="hero3d" data-shape="torusknot|icosa|globe|crystal|rings" style="--size:640px"></div> (real three.js, put it in a .row next to the text; keep at most one per scene, and no more than 2-3 scenes with one), or a chart, or an inline-SVG illustration, or a giant number. Never a scene with only a headline and a paragraph.
- Kinetic typography: <h1 class="title" data-split="chars|words" data-kin="rise|drop|blur|flip|wave|zoom|spin|glitch" style="--d:.3s;--step:.06s">. Use it for the main headline of EVERY scene (chars for short titles, words for longer). Variants: class="neon", "fx-gradient" (flowing gradient), "stroke-text", "glitch" (+ data-text="same text"), "underline-draw", .mask-reveal > h2 (slides up from a mask).
- Camera: every scene already gets a slow push/pull/pan (data-cam="push|pull|left|right|drift|tilt|none" to choose). Transitions: data-transition="zoom|slide|wipe|curtain|slice|flip|blur|spin|rise|glitch" (vary them, never repeat the same one twice in a row).
- Infographics (they animate in by themselves, give them real numbers from the topic): <div class="fx-bars" data-values="24,46,71" data-labels="A,B,C" data-suffix="k" style="--h:480px;width:760px"></div>, <div class="fx-donut" data-value="87" data-suffix="%" data-label="Label" style="--size:380px"></div>, <div class="fx-line" data-values="2,3,5,4,9" style="--w:700px;--h:360px;width:700px;height:360px"></div>.
- Ambient motion on shapes/icons/cards: .bob .breathe .spin-slow .spin-rev .tilt3d .glow .shine .ring-burst .blob (morphing gradient blob), .orbit (children orbit; set --size), .marquee (giant scrolling outline text as a backdrop), .lower-third (name tag), photos: <div class="photo"><img class="kb" src="../assets/images/x.jpg"></div> (Ken Burns pan/zoom).
- Rhythm: alternate big/quiet scenes; open with the most striking visual in the first 2 seconds; end on a bold closing card. Use at least 3 different entrance classes, 3 different transitions and 2 different hero visuals per video. Time reveals to the narration as described below.
- Performance: each frame is rendered by software; keep to <= 1 hero3d per scene, avoid huge blurs/box-shadows and more than ~25 animated elements per scene.

## Other styles: choose from the user's request (the kit above is the default)
- 3D (3D, WebGL, three.js, spinning object, planet, camera orbit): do NOT use the kit. One <script type="module"> with `import * as THREE from 'three'`. Create `new THREE.WebGLRenderer({antialias:true, preserveDrawingBuffer:true})`, setPixelRatio(1), setSize(innerWidth, innerHeight); build geometry procedurally (no remote models). Drive everything from time only: `window.__renderFrame = (tMs) => { update(tMs/1000); renderer.render(scene, camera); }; window.__renderFrame(0);`. Titles: HTML overlay (position:fixed) with CSS animations.
- MATH / 3Blue1Brown style (graphs, equations, vectors, calculus, proofs): do NOT use the kit. Use mathkit (black background, KaTeX): `import { Scene, COLORS, create, write, fadeIn, fadeOut, swap, morph, shift, moveTo, colorTo, custom, along } from 'mathkit'`.
  Frame is x -8..8, y -4.5..4.5 (y up). `const s = new Scene()`. Builders (options {color, fill, width, dashed}): s.axes({xRange:[-4,4,1], yRange:[-3,3,1], xLength, yLength, center, grid, labels, xLabel:'x'}) returns a group with .plot(fn,{color,xRange}) .area(fn,[a,b]) .point(x,y) .vector([x,y]) .c2p(x,y); s.line(a,b) s.arrow(a,b) s.dot(p) s.circle(c,r) s.rect(c,w,h) s.polygon(pts) s.parametric(t=>[x,y],{tRange}) s.text(str,{at,size,color}) s.tex(latex,{at,size,color}) s.group(...els). LaTeX needs doubled backslashes in JS strings.
  Elements are INVISIBLE until introduced: s.add(el) or an animation. Play in order: `s.play(create(axes), write(title))` (items in one play() run together), `s.wait(1)`, `s.camera({center:[x,y], zoom:2})`; morph(plot, newFn) turns one curve into another; all animations take {duration}. End with `s.padTo(window.__VIDEO_TOTAL__); s.start();` and make the storyboard durations equal the animation length.
- CARTOON / animated explainer (characters, motion graphics, squash and stretch): do NOT use the kit. Inline SVG + GSAP: `<script src="/vendor/gsap/dist/gsap.min.js"></script>`. Build `const tl = gsap.timeline({paused:true})`, then `window.__renderFrame = (tMs) => tl.time(Math.min(tMs/1000, tl.duration()), false)`. Draw strokes by tweening attr (`{attr:{'stroke-dashoffset':0}}`), never the CSS property (GSAP rounds it to whole pixels).
- Non-kit styles still get narration through generate_narration + storyboard "narration"; skip install_design_kit and the data-scene markup, and still call validate_project before render_video.

Create a polished explainer with real content and real visuals, not placeholders.
"""

_FX_MARK = "\n## Motion-graphics layer"
_OTHER_MARK = "\n## Other styles"
_CORE, _rest = SYSTEM.split(_FX_MARK, 1)
_FX_FULL, _OTHER = _rest.split(_OTHER_MARK, 1)
_FX_FULL = _FX_MARK + _FX_FULL
_OTHER = _OTHER_MARK + _OTHER
_FX_SLIM = """
## Motion graphics (fx layer, loaded by the skeleton)
Make it cinematic, never a slide deck. <body data-theme="blue|sunset|emerald|crimson|gold|mono" data-bg="particles|network|stars|aurora|grid|waves|rain" data-post="vignette grain">. Every scene gets a hero visual: <div class="hero3d" data-shape="torusknot|icosa|globe|crystal|rings" style="--size:640px"></div> (max 1 per scene), or <div class="fx-bars" data-values="1,2,3" data-labels="a,b,c">, <div class="fx-donut" data-value="80" data-suffix="%">, <div class="fx-line" data-values="1,3,2,5">, an inline SVG, or a big number. Headlines: data-split="chars|words" data-kin="rise|drop|blur|flip|wave|zoom|spin|glitch" (also .neon .fx-gradient). Camera moves are automatic; vary data-transition (zoom slide wipe curtain slice flip blur spin rise glitch). Ambient: .bob .breathe .spin-slow .glow .shine .blob .orbit.
"""
_OTHER_KEYWORDS = re.compile(
    r"3d|three\.?js|webgl|math|equation|calculus|theorem|proof|3blue|manim|vector|derivative|integral|"
    r"cartoon|character|squash|mascot|animated explainer|svg animation", re.IGNORECASE)

def build_system(user_text: str = "", slim: bool = False) -> str:
    """Core rules always; the long style sections only when they help (they cost ~1.5k tokens)."""
    parts = [_CORE, _FX_SLIM if slim else _FX_FULL]
    if not slim and _OTHER_KEYWORDS.search(user_text or ""):
        parts.append(_OTHER)
    return "".join(parts)

def _tool_schemas():
    return [
        {
            "type": "function",
            "function": {
                "name": "install_design_kit",
                "description": "Install the design system (source/kit.css) and scene engine (source/kit.js). Call first, once.",
                "parameters": {"type": "object", "properties": {}}
            }
        },
        {
            "type": "function",
            "function": {
                "name": "write_storyboard",
                "description": "Create the storyboard: {\"scenes\":[{\"id\",\"title\",\"duration_seconds\",\"narration\":\"<id>.wav\"}]}. duration_seconds must be >= min_scene_duration_seconds returned by generate_narration for that scene's clip.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "storyboard": {"type": "object"}
                    },
                    "required": ["storyboard"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "write_source",
                "description": "Write source code under source/. Paths cannot escape source/.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "relative_path": {"type": "string"},
                        "content": {"type": "string"}
                    },
                    "required": ["relative_path", "content"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "save_text_asset",
                "description": "Save a text-based asset in a type-controlled directory.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "asset_type": {
                            "type": "string",
                            "enum": ["image", "video", "audio", "font", "model3d", "data", "narration"]
                        },
                        "filename": {"type": "string"},
                        "content": {"type": "string"}
                    },
                    "required": ["asset_type", "filename", "content"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "generate_narration",
                "description": "Generate a local WAV narration clip (espeak-ng) inside the sandbox. Returns narration_seconds and min_scene_duration_seconds.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "filename": {"type": "string"},
                        "text": {"type": "string"},
                        "rate": {"type": "integer"},
                        "volume": {"type": "number"}
                    },
                    "required": ["filename", "text"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "ingest_host_asset",
                "description": "Copy a user-supplied local asset into the correct typed project directory. Only use a path the user has intentionally supplied/configured.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "asset_type": {
                            "type": "string",
                            "enum": ["image", "video", "audio", "font", "model3d", "data"]
                        },
                        "source_path": {"type": "string"},
                        "filename": {"type": "string"}
                    },
                    "required": ["asset_type", "source_path"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "read_source",
                "description": "Read a file under source/ (your history only keeps a stub of files you saved).",
                "parameters": {"type": "object", "properties": {"relative_path": {"type": "string"}}, "required": ["relative_path"]}
            }
        },
        {
            "type": "function",
            "function": {
                "name": "patch_source",
                "description": "Replace exact text in a file under source/. Cheap way to fix a bug or tweak a value. `old` must match exactly once unless replace_all is true.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "relative_path": {"type": "string"},
                        "old": {"type": "string"},
                        "new": {"type": "string"},
                        "replace_all": {"type": "boolean"}
                    },
                    "required": ["relative_path", "old", "new"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "generate_narrations",
                "description": "Generate several narration clips at once (parallel). Same fields as generate_narration per clip; returns narration_seconds and min_scene_duration_seconds for each.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "clips": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "filename": {"type": "string"},
                                    "text": {"type": "string"},
                                    "rate": {"type": "integer"},
                                    "volume": {"type": "number"}
                                },
                                "required": ["filename", "text"]
                            }
                        }
                    },
                    "required": ["clips"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "search_free_images",
                "description": "Search openly licensed images on the web (Openverse, free). Returns direct image URLs with license and creator. Download one with download_asset, then reference it as a local file.",
                "parameters": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}},
                    "required": ["query"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "download_asset",
                "description": "Download a public http(s) file (image, font, audio, data...) into the project's asset folder on the host. The sandbox itself stays offline, so always download first and use the local path.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "asset_type": {"type": "string", "enum": ["image", "video", "audio", "font", "model3d", "data"]},
                        "url": {"type": "string"},
                        "filename": {"type": "string"}
                    },
                    "required": ["asset_type", "url"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "validate_project",
                "description": "Validate file placement, extensions, size limits, and storyboard structure.",
                "parameters": {"type": "object", "properties": {}}
            }
        },
        {
            "type": "function",
            "function": {
                "name": "render_video",
                "description": "Render the validated project into an MP4 using Chromium and FFmpeg inside the sandbox.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "width": {"type": "integer"},
                        "height": {"type": "integer"},
                        "fps": {"type": "integer"}
                    },
                    "required": ["width", "height", "fps"]
                }
            }
        }
    ]

def _path_was_supplied_by_user(source_path, supplied_text: str) -> bool:
    """True if the user's own text (project prompt / instruction) contains this exact path."""
    p = str(source_path or "").strip()
    if not p:
        return False
    return p in supplied_text or p.replace("\\", "/") in supplied_text.replace("\\", "/")

def _call_tool(project_id, name, args, supplied_text="", emit=None):
    if name == "install_design_kit":
        return install_design_kit(project_id)
    if name == "write_storyboard":
        return write_storyboard(project_id, args["storyboard"])
    if name == "write_source":
        return write_source(project_id, args["relative_path"], args["content"])
    if name == "save_text_asset":
        return save_text_asset(project_id, args["asset_type"], args["filename"], args["content"])
    if name == "generate_narration":
        rate = args.get("rate")
        volume = args.get("volume")
        return generate_narration(
            project_id,
            args["filename"],
            args["text"],
            170 if rate is None else int(rate),
            1.0 if volume is None else float(volume),
        )
    if name == "ingest_host_asset":
        # The model must not be able to pull arbitrary files off the host (e.g. after a
        # prompt injection). Only a path the user typed themselves is allowed; uploads
        # made through the UI/CLI are already in the project.
        if not _path_was_supplied_by_user(args.get("source_path"), supplied_text):
            raise PermissionError(
                "source_path was not supplied by the user. Only use a path exactly as the user "
                "wrote it in their request, or ask them to attach the file in the UI / "
                "`cli.py add-asset`."
            )
        return ingest_host_asset(
            project_id,
            args["asset_type"],
            args["source_path"],
            args.get("filename"),
        )
    if name == "read_source":
        return read_source(project_id, args["relative_path"])
    if name == "patch_source":
        return patch_source(project_id, args["relative_path"], args["old"], args["new"], bool(args.get("replace_all")))
    if name == "generate_narrations":
        return generate_narrations(project_id, args["clips"])
    if name == "search_free_images":
        return search_free_images(args["query"], int(args.get("limit") or 6))
    if name == "download_asset":
        return download_asset(project_id, args["asset_type"], args["url"], args.get("filename"))
    if name == "validate_project":
        return validate(project_id)
    if name == "render_video":
        return render(project_id, int(args["width"]), int(args["height"]), int(args["fps"]), on_progress=emit)
    raise ValueError(f"Unknown tool: {name}")

def _summarize_call(name: str, args: dict) -> str:
    """Short human-readable line for the live progress feed."""
    for key in ("relative_path", "filename", "query", "url"):
        if isinstance(args.get(key), str):
            return f"{name} {args[key][:80]}"
    if name == "generate_narrations":
        return f"{name} ({len(args.get('clips') or [])} clips)"
    if name == "render_video":
        return f"{name} {args.get('width')}x{args.get('height')}@{args.get('fps')}"
    return name

def _compact_call(call: dict, args: dict, result_ok: bool) -> None:
    """Replace a saved file's bulky argument with a stub, in place, in the history."""
    key = _BULKY_ARGS.get(call["function"]["name"])
    if not (key and result_ok and key in args):
        return
    value = args[key]
    size = len(value) if isinstance(value, str) else len(json.dumps(value, default=str))
    stub = dict(args)
    stub[key] = f"[saved {size} chars]"
    call["function"]["arguments"] = json.dumps(stub)

def _clip(text: str, limit: int = MAX_TOOL_OUTPUT_CHARS) -> str:
    return text if len(text) <= limit else text[:limit] + f"... [truncated {len(text) - limit} chars]"

def _shrink_history(messages: list, level: int) -> None:
    """Make the conversation cheaper in place, never dropping a message (tool_call pairs must stay intact)."""
    keep_tail = 6
    for i, m in enumerate(messages):
        if m["role"] == "tool":
            old = i < len(messages) - keep_tail
            if level >= 2 and old:
                ok = '"ok": false' not in m["content"][:30]
                m["content"] = '{"ok": true, "result": "[omitted]"}' if ok else _clip(m["content"], 300)
            else:
                m["content"] = _clip(m["content"], 600 if level >= 1 else MAX_TOOL_OUTPUT_CHARS)
        elif m["role"] == "assistant" and level >= 1 and len(m.get("content") or "") > 400:
            m["content"] = m["content"][:400] + "..."

def _request_estimate(messages, tools) -> int:
    return llm.estimate_tokens(messages) + llm.estimate_tokens(tools)

def run_agent(project_id: str, instruction: str | None = None, on_event=None):
    """Drive the model until the video is built. `on_event(str)` receives live progress lines."""
    if not GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY is not configured.")
    emit = on_event or (lambda message: None)

    client = OpenAI(
        api_key=GROQ_API_KEY,
        base_url="https://api.groq.com/openai/v1",
        timeout=LLM_TIMEOUT_SECONDS,
        max_retries=0,  # llm.complete owns retry/backoff so the shared budget stays accurate
    )

    manifest = load_project(project_id)
    files = list_files(project_id)

    user_message = {
        "project": manifest,
        "existing_files": files,
        "instruction": instruction or "Build the complete video from the project's original prompt. Validate and render it.",
    }

    user_text = f"{manifest.get('prompt') or ''}\n{instruction or ''}"
    slim = llm.learned_limit is not None  # a previous run hit a request-size limit: start small
    messages = [
        {"role": "system", "content": build_system(user_text, slim)},
        {"role": "user", "content": json.dumps(user_message, indent=2)},
    ]
    # Text the user wrote themselves: the only place a host path may come from.
    supplied_text = f"{manifest.get('prompt') or ''}\n{instruction or ''}"
    usage = llm.Usage()
    tools = _tool_schemas()

    shrink_level = 0
    for turn in range(1, MAX_TURNS + 1):
        emit(f"Thinking (step {turn})…")
        while True:
            # Stay under a request-size limit we already know about (413 from an earlier call).
            if llm.learned_limit and _request_estimate(messages, tools) > llm.learned_limit * 0.85 and shrink_level < 3:
                shrink_level += 1
                _shrink_history(messages, shrink_level)
                if shrink_level == 3 and not slim:
                    slim = True
                    messages[0]["content"] = build_system(user_text, True)
                continue
            try:
                response = llm.complete(
                    client,
                    usage=usage,
                    model=GROQ_MODEL,
                    messages=messages,
                    tools=tools,
                    tool_choice="auto",
                    temperature=0.2,
                )
                break
            except llm.RequestTooLarge as exc:
                if shrink_level >= 3 and slim:
                    raise RuntimeError(
                        "The model rejected the request as too large even after shrinking it "
                        f"({exc.requested or '?'} tokens, limit {exc.limit or '?'}). Your Groq plan/model allows too few "
                        "tokens per request for this agent: use a model with a larger limit (GROQ_MODEL / "
                        "GROQ_FALLBACK_MODEL) or upgrade the plan. Details: " + str(exc)
                    ) from exc
                emit(f"Request too large for the model (limit {exc.limit or 'unknown'}); shrinking context and retrying…")
                shrink_level = min(3, shrink_level + 1)
                _shrink_history(messages, shrink_level)
                if not slim:
                    slim = True
                    messages[0]["content"] = build_system(user_text, True)

        msg = response.choices[0].message
        assistant = {"role": "assistant", "content": msg.content or ""}
        if msg.tool_calls:
            assistant["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in msg.tool_calls
            ]
        messages.append(assistant)

        if not msg.tool_calls:
            return {"status": "completed", "message": msg.content or "Agent completed.", "usage": usage.as_dict()}

        for tc, call in zip(msg.tool_calls, assistant["tool_calls"]):
            args = {}
            try:
                # Tools without parameters (validate_project) are often called with an
                # empty string instead of "{}".
                raw_args = tc.function.arguments
                args = json.loads(raw_args) if raw_args and raw_args.strip() else {}
                if not isinstance(args, dict):
                    raise ValueError("Tool arguments must be a JSON object.")
                emit(_summarize_call(tc.function.name, args))
                result = _call_tool(project_id, tc.function.name, args, supplied_text, emit)
                tool_output = {"ok": True, "result": result}
            except Exception as exc:
                tool_output = {"ok": False, "error": str(exc)}
                emit(f"{tc.function.name} failed: {str(exc)[:160]}")
            _compact_call(call, args, tool_output["ok"])

            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": _clip(json.dumps(tool_output, default=str)),
            })

    raise RuntimeError("Agent reached the maximum tool-call iterations.")
