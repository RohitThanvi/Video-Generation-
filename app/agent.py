from openai import OpenAI
import json
from .config import GROQ_API_KEY, GROQ_MODEL
from .project import project_dir, load_project, list_files
from .tools import write_source, write_storyboard, save_text_asset, generate_narration, ingest_host_asset, validate, render

SYSTEM = r"""
You are the director/engineering agent for a deterministic HTML video compiler.

Your job is to create a complete video project inside the project's workspace using the provided tools.

Rules:
1. Never invent filesystem locations. Use the semantic tools.
2. Put narration text in narration/ and source code in source/.
3. Image/video/audio/font/3D/data assets must use the corresponding asset tool.
4. Create a storyboard before writing scenes. Every scene must have a positive duration_seconds.
5. source/index.html is the entry point and must render the whole video at 1920x1080 unless the project settings say otherwise.
6. The final HTML must be self-contained except for local project assets. Do not rely on an internet CDN because the renderer has no network.
7. Prefer SVG/CSS/Canvas for diagrams and Three.js only when the required library is already available locally. Do not import remote scripts.
8. Do not claim that a render succeeded until the render tool returns success.
9. Before final rendering, call validate.
10. If validation fails, fix the project and validate again.
11. Keep all generated content technically accurate and spell text exactly.
12. If narration is required, write the narration text and call generate_narration; do not merely save a text file.
13. Supplied local assets can be ingested only through ingest_host_asset; never copy arbitrary host files through generated code.
14. Do not assume remote internet assets exist because the renderer has no network.
15. The project is intended for deterministic browser rendering, not interactive user presentation.
16. Do not execute arbitrary host commands. Only use the provided tools.

Create polished explainer-style videos. Use actual HTML/CSS/JS rather than placeholder text.
"""

def _tool_schemas():
    return [
        {
            "type": "function",
            "function": {
                "name": "write_storyboard",
                "description": "Create the project's storyboard and scene timings.",
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
                "description": "Generate a local WAV narration file using pyttsx3 inside the sandbox.",
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

def _call_tool(project_id, name, args):
    if name == "write_storyboard":
        return write_storyboard(project_id, args["storyboard"])
    if name == "write_source":
        return write_source(project_id, args["relative_path"], args["content"])
    if name == "save_text_asset":
        return save_text_asset(project_id, args["asset_type"], args["filename"], args["content"])
    if name == "generate_narration":
        return generate_narration(
            project_id,
            args["filename"],
            args["text"],
            int(args.get("rate", 170)),
            float(args.get("volume", 1.0)),
        )
    if name == "ingest_host_asset":
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
    raise ValueError(name)

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

    for _ in range(30):
        response = client.chat.completions.create(
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
                args = json.loads(tc.function.arguments)
                result = _call_tool(project_id, tc.function.name, args)
                tool_output = {"ok": True, "result": result}
            except Exception as exc:
                tool_output = {"ok": False, "error": str(exc)}

            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": json.dumps(tool_output, default=str),
            })

    raise RuntimeError("Agent reached the maximum tool-call iterations.")
