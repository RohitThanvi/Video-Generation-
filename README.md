# AI Video Compiler — Local V1

A local, agent-driven HTML-to-video compiler.

## What it does

The system gives an LLM a structured set of tools to create a video project. The project is stored outside the execution sandbox. Rendering happens inside a Docker container containing:

- Python
- Node.js
- Chromium
- Playwright
- FFmpeg
- pyttsx3

The agent can create scenes, write HTML/CSS/JS, generate local narration with pyttsx3, validate the project, and render an MP4.

## Architecture

```text
User
  |
  v
FastAPI
  |
  +--> Groq API (OpenAI-compatible chat API)
  |
  +--> Project workspace on host
  |
  +--> Docker sandbox
          |
          +--> Chromium / Playwright
          +--> FFmpeg
          +--> pyttsx3
```

The LLM is outside the sandbox. Generated/rendered code executes inside the sandbox.

## Requirements

- Docker Desktop (Windows/macOS) or Docker Engine (Linux)
- Python 3.11+
- A Groq API key
- Internet access for the Groq API
- FFmpeg is inside the Docker image; it is not required on the host.
- pyttsx3 is installed in the sandbox and uses the guest OS speech engine.

For Windows, Docker Desktop with Linux containers is recommended.

## 1. Install Python dependencies

```bash
python -m venv .venv

# Windows
.venv\Scripts\activate

# Linux/macOS
source .venv/bin/activate

pip install -r requirements.txt
```

## 2. Configure environment

Copy `.env.example` to `.env` and set your Groq key:

```bash
GROQ_API_KEY=your_key_here
```

The default model is configurable through `GROQ_MODEL`. Set it to the exact model ID currently available in your Groq account.

Other settings are also configurable in `.env`.

## 3. Build the sandbox image

```bash
docker build -t ai-video-sandbox:latest ./sandbox
```

## 4. Start the API

```bash
uvicorn app.main:app --reload
```

API documentation:

http://127.0.0.1:8000/docs

## 5. Create a project

```bash
python scripts/cli.py create "A 30 second explainer about the water cycle"
```

The CLI returns a project ID.

## 6. Ask the agent to build it

```bash
python scripts/cli.py generate PROJECT_ID
```

The agent will iteratively use its tools to create the project.

## 7. Render manually

```bash
python scripts/cli.py render PROJECT_ID
```

The MP4 will be placed under:

```text
projects/PROJECT_ID/renders/final/final.mp4
```

If a scene in `storyboard.json` has `"narration": "<file>.wav"`, that narration clip is mixed into the MP4 starting at the scene's start time. If no scene references narration, any `narration/*.wav` files are played back to back from the start. The page's own `<audio>` elements are not recorded.

## 8. Validate

```bash
python scripts/cli.py validate PROJECT_ID
```

## Project structure

```text
projects/
  <project_id>/
    project.json
    storyboard.json
    source/
      index.html
      styles.css
      app.js
    assets/
      images/
      video/
      audio/
      fonts/
      models3d/
      data/
    narration/
    renders/
      preview/
      final/
```

The agent is not allowed to arbitrarily choose asset directories through the public tools. Asset type determines its destination.

## Important security note

This is a local V1. The FastAPI process needs permission to start Docker containers. Do NOT expose this API to the public internet as-is.

For a multi-user SaaS, replace the local Docker execution layer with stronger isolation, such as Firecracker microVMs, add authentication/authorization, per-user quotas, a job queue, object storage, and a dedicated worker fleet.

## Supported video authoring primitives

The agent can create arbitrary HTML/CSS/JS, so the renderer supports:

- HTML text
- CSS animations
- SVG
- Canvas
- browser-rendered charts
- screenshots/images
- audio
- Three.js/WebGL if loaded by the generated project
- local video assets
- subtitles/captions
- narration timing

The renderer is browser-based, so there is no artificial 8-second generative-video limit.

## TTS

`pyttsx3` is used locally. Its actual voices depend on the operating system and speech engines available inside the sandbox. The Docker image uses Linux speech components. For better voices later, replace the TTS service with another provider without changing the project model.

## API endpoints

- `POST /projects`
- `GET /projects/{project_id}`
- `POST /projects/{project_id}/agent`
- `POST /projects/{project_id}/validate`
- `POST /projects/{project_id}/render`
- `POST /projects/{project_id}/assets`

Example:

```bash
curl -X POST http://127.0.0.1:8000/projects \
  -H "Content-Type: application/json" \
  -d '{"prompt":"Create a 20 second scientific explainer about photosynthesis."}'
```

Then use the returned project ID with the agent endpoint.

## Design principle

The LLM is the director. The engine is responsible for filesystem layout, validation, rendering, resource limits, and media correctness.

## Browser UI

The project now includes a lightweight ChatGPT-style web interface served directly by FastAPI. No Node.js frontend build is required.

Start the API:

```bash
uvicorn app.main:app --reload
```

Open `http://127.0.0.1:8000/` in a browser. The UI supports:

- New video projects and project sidebar
- Natural-language chat with the agent
- Image/video/audio/font/3D/data attachments
- Project validation
- MP4 rendering and in-browser preview/download
- The same backend APIs remain available for CLI use

### Terminal asset upload

```bash
python scripts/cli.py add-asset PROJECT_ID image "C:\\Users\\Rohit\\Desktop\\dashboard.png"
```

The browser and CLI both use the same project workspace and asset routing.

## Running with docker-compose

The API can also run in a container. Because the Docker daemon runs on the host, the sandbox must bind-mount the *host* path of `projects/`. Set it in `.env` as an absolute path:

```bash
HOST_PROJECT_ROOT=/absolute/path/to/Video-Generation-/projects
```

Then build the sandbox image (`make build-sandbox`) and run `docker compose up --build`. The API is published on `127.0.0.1:8000` only, because the container can start containers on the host.

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

The tests do not need Docker or a Groq key. The renderer's ffmpeg/audio tests are skipped if `ffmpeg` is not installed.

## After updating

Rebuild the sandbox image whenever `sandbox/` changes (`make build-sandbox`); the host API calls scripts inside that image.

## Visual design kit and audio sync

The agent no longer hand-writes CSS. `install_design_kit` copies `app/kit/kit.css` (design system:
gradient backgrounds, glassmorphism cards, Inter/Roboto typography, SVG icon set, `fade-in-up` /
`slide-in-*` / `scale-up` / `blur-in` / `draw` entrance animations with `cubic-bezier` easing) and
`app/kit/kit.js` (scene engine: animated cross-fade/slide/zoom/wipe transitions, count-up numbers,
progress bar) into `source/`. See `examples/isro-journey/` for a complete project.

Audio is sequential by construction:

1. `generate_narration` returns `narration_seconds` and `min_scene_duration_seconds`.
2. The storyboard scene that plays a clip must be at least that long (`validate_project` enforces it).
3. The renderer starts each clip 0.6 s after its scene starts and refuses to render if clips would
   overlap or run past the end (`plan_audio`).
4. `render.py` injects the storyboard's scene ids/durations into the page (`window.__VIDEO_SCENES__`),
   so the HTML timeline cannot disagree with the storyboard.

Rebuild the sandbox image after pulling (`docker build -t ai-video-sandbox sandbox`): it now installs
`fonts-inter` and `fonts-roboto`.

## Rendering engine, 3D, math and cartoon styles

Videos are rendered **frame by frame**, not recorded in real time. The page runs against a
virtual clock (`sandbox/renderer/runtime.js`): `performance.now`, `Date`, `requestAnimationFrame`,
timers, CSS animations/transitions and Web Animations all advance exactly 1/fps per frame, so
output is deterministic and never drops frames, however heavy the scene. Each frame is a PNG
screenshot piped to ffmpeg (H.264, BT.709, narration mixed in).

Optional page hooks: `window.__renderFrame(tMs, frameIndex)` (draw/seek yourself, may be async;
do not return a GSAP timeline from it) and `window.__VIDEO_READY` (a Promise the renderer awaits
before frame 0). Globals: `__VIDEO_FPS__`, `__VIDEO_TOTAL__`, `__VIDEO_WIDTH__`, `__VIDEO_HEIGHT__`,
`__VIDEO_SCENES__`.

Libraries available offline (served under `/vendor/<name>/`, import map injected automatically):
`three`, `gsap`, `d3`, `katex`, and `mathkit` (a small manim-style toolkit for 3Blue1Brown-style
math animation: `Scene`, `create`, `write`, `morph`, `shift`, `camera`, axes/plots, LaTeX via KaTeX).
The agent picks the style from the request (3D, math, cartoon, or the default design kit); users
never need to mention HTML/CSS.

Try the examples: `python scripts/cli.py example math-3b1b` (also `three-3d`, `cartoon-explainer`),
then `python scripts/cli.py render <project_id>`.

Notes:
- Frame-by-frame rendering is slower than real time (minutes per video; WebGL is software-rendered).
  Use a lower fps/resolution for drafts. `SANDBOX_TIMEOUT_SECONDS` now defaults to 3600.
- Rebuild the sandbox image once (`docker build -t ai-video-sandbox:latest sandbox`); the apt/pip
  layers are cached, only the JS-library layer is new.
- The design kit pages assume a 1920x1080 canvas.

## Free web images and asset downloads

The agent can search openly licensed images (Openverse, no API key) with `search_free_images` and
fetch public files with `download_asset`. Downloads run on the API host, are saved into the
project's `assets/` folders, and the render sandbox stays offline (it only sees local files).
Guards: http(s) only, public IPs only (redirects re-checked), size limit `MAX_FILE_BYTES`, file
type must match the asset category. Disable with `ALLOW_WEB_ASSETS=false`.
