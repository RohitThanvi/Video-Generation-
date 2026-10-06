from openai import OpenAI, RateLimitError
import json
import re
import time
from .config import GROQ_API_KEY, GROQ_MODEL
from .project import project_dir, load_project, list_files
from .tools import write_source, write_storyboard, save_text_asset, generate_narration, ingest_host_asset, validate, render, install_design_kit

MAX_RETRIES = 6

def _retry_after_seconds(exc: RateLimitError) -> float | None:
    """Groq tells us the exact wait in the error body ('Please try again in 5.895s')."""
    m = re.search(r"try again in ([\d.]+)s", str(exc), re.IGNORECASE)
    return float(m.group(1)) if m else None

def _create_completion_with_retry(client, **kwargs):
    """Groq's on-demand tier is TPM-limited; wait out the window and retry instead of dying."""
    for attempt in range(MAX_RETRIES):
        try:
            return client.chat.completions.create(**kwargs)
        except RateLimitError as exc:
            if attempt == MAX_RETRIES - 1:
                raise
            delay = _retry_after_seconds(exc)
            if delay is None:
                delay = min(2 ** attempt * 5, 60)  # 5s, 10s, 20s, ... capped at 60s
            time.sleep(delay + 0.5)

SYSTEM = r"""
You are the director/engineering agent for a deterministic HTML-to-video compiler. You build a
premium, modern explainer video (think Apple keynote / Kurzgesagt-clean, NOT a 1990s web page)
inside the project workspace using ONLY the provided tools.

## Hard rules
- Never invent filesystem locations; use the semantic tools. Source code goes in source/, narration text in narration/.
- The renderer has no network: no CDNs, no remote images/fonts/scripts. Everything is local or inline.
- Canvas is 1920x1080. The page is recorded in real time from page load; video length = sum of storyboard durations.
- Audio inside the page is NOT recorded. Narration is made with generate_narration and attached to a scene in storyboard.json ("narration": "<file>.wav"); the compiler mixes it in.
- fetch() cannot read file:// URLs: inline any data.
- Keep facts accurate and spell text exactly. Never claim success before render_video returns ok. Never run host commands. ingest_host_asset only for paths the user wrote.
- Keep every file SHORT and never repeat yourself: no duplicated CSS rules, no repeated keyframe percentages, no filler. A complete index.html is typically 120-250 lines.

## Workflow (follow this order)
1. install_design_kit  -> creates source/kit.css (design system) and source/kit.js (scene engine). Do NOT rewrite them.
2. Plan scenes (4-8 scenes). For each scene write narration of ~30-45 words (speech is ~2.8 words/second).
3. generate_narration for EVERY scene (filename = <scene id>.wav). Each result returns narration_seconds and min_scene_duration_seconds.
4. write_storyboard: {"scenes":[{"id":"intro","title":"...","duration_seconds":N,"narration":"intro.wav"}, ...]} with duration_seconds >= min_scene_duration_seconds. Scenes without narration: 4-6 s. This is what keeps audio sequential: a clip must fit inside its own scene, so never shorten below the minimum.
5. write_source index.html using the kit (skeleton below). Put scene-specific layout in one small <style> block.
6. validate_project; fix every error; then render_video(1920,1080,30).

## index.html skeleton
<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><title>Video</title>
<link rel="stylesheet" href="kit.css"></head><body>
<div class="bg"><i class="orb o1"></i><i class="orb o2"></i><i class="orb o3"></i></div>
<section class="scene" data-scene="intro" data-transition="zoom">   <!-- data-scene == storyboard id, one section per scene, same order -->
  <div class="eyebrow fade-in-down">Chapter 01</div>
  <h1 class="title blur-in" style="--d:.15s">Big <span class="grad-text">headline</span></h1>
  <p class="subtitle fade-in-up" style="--d:.6s">One supporting sentence.</p>
</section>
<!-- more <section class="scene" ...> -->
<div class="progress"></div>
<script src="kit.js"></script></body></html>

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

Create a polished explainer with real content and real visuals, not placeholders.
"""

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

def _call_tool(project_id, name, args, supplied_text=""):
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
    if name == "validate_project":
        return validate(project_id)
    if name == "render_video":
        return render(project_id, int(args["width"]), int(args["height"]), int(args["fps"]))
    raise ValueError(f"Unknown tool: {name}")

def run_agent(project_id: str, instruction: str | None = None):
    if not GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY is not configured.")

    client = OpenAI(
        api_key=GROQ_API_KEY,
        base_url="https://api.groq.com/openai/v1",
    )

    manifest = load_project(project_id)
    files = list_files(project_id)

    user_message = {
        "project": manifest,
        "existing_files": files,
        "instruction": instruction or "Build the complete video from the project's original prompt. Validate and render it.",
    }

    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": json.dumps(user_message, indent=2)},
    ]
    # Text the user wrote themselves: the only place a host path may come from.
    supplied_text = f"{manifest.get('prompt') or ''}\n{instruction or ''}"

    for _ in range(30):
        response = _create_completion_with_retry(
            client,
            model=GROQ_MODEL,
            messages=messages,
            tools=_tool_schemas(),
            tool_choice="auto",
            temperature=0.2,
        )

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
            return {"status": "completed", "message": msg.content or "Agent completed."}

        for tc in msg.tool_calls:
            try:
                # Tools without parameters (validate_project) are often called with an
                # empty string instead of "{}".
                raw_args = tc.function.arguments
                args = json.loads(raw_args) if raw_args and raw_args.strip() else {}
                if not isinstance(args, dict):
                    raise ValueError("Tool arguments must be a JSON object.")
                result = _call_tool(project_id, tc.function.name, args, supplied_text)
                tool_output = {"ok": True, "result": result}
            except Exception as exc:
                tool_output = {"ok": False, "error": str(exc)}

            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": json.dumps(tool_output, default=str),
            })

    raise RuntimeError("Agent reached the maximum tool-call iterations.")
