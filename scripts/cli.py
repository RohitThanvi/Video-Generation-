import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.project import create_project, load_project
from app.agent import run_agent
from app.tools import validate, render, ingest_host_asset, generate_narration, write_source, write_storyboard
from app.project import require_project_dir

EXAMPLES_DIR = Path(__file__).resolve().parents[1] / "examples"

parser = argparse.ArgumentParser(description="AI Video Compiler CLI")
sub = parser.add_subparsers(dest="cmd", required=True)

p = sub.add_parser("create")
p.add_argument("prompt")

g = sub.add_parser("generate")
g.add_argument("project_id")
g.add_argument("--instruction", default=None)

v = sub.add_parser("validate")
v.add_argument("project_id")

r = sub.add_parser("render")
r.add_argument("project_id")
r.add_argument("--width", type=int, default=1920)
r.add_argument("--height", type=int, default=1080)
r.add_argument("--fps", type=int, default=30)

s = sub.add_parser("show")
s.add_argument("project_id")

a = sub.add_parser("add-asset")
a.add_argument("project_id")
a.add_argument("asset_type", choices=["image", "video", "audio", "font", "model3d", "data"])
a.add_argument("path")
a.add_argument("--name", default=None)

e = sub.add_parser("example", help="create a project from examples/<name> (e.g. math-3b1b, three-3d, cartoon-explainer)")
e.add_argument("name", nargs="?", default=None)

d = sub.add_parser("stage-demo", help="build a showcase project of the stage layout (no model, no voice); then render it")

args = parser.parse_args()

if args.cmd == "create":
    print(json.dumps(create_project(args.prompt), indent=2))
elif args.cmd == "generate":
    print(json.dumps(run_agent(args.project_id, args.instruction), indent=2, default=str))
elif args.cmd == "validate":
    print(json.dumps(validate(args.project_id), indent=2, default=str))
elif args.cmd == "render":
    print(json.dumps(render(args.project_id, args.width, args.height, args.fps), indent=2, default=str))
elif args.cmd == "show":
    print(json.dumps(load_project(args.project_id), indent=2))
elif args.cmd == "add-asset":
    print(json.dumps(ingest_host_asset(args.project_id, args.asset_type, args.path, args.name), indent=2))
elif args.cmd == "stage-demo":
    from app import engine
    look = {"shirt": "red", "pants": "navy", "hat": "party"}
    spec = {"title": "Stage demo", "scenes": [
        {"id": "pond", "layout": "stage", "headline": "A man dancing in a pond", "duration": 7,
         "stage": {"env": "pond", "time": "sunset", "actors": [{"action": "dance", "style": "disco", "look": look}]}},
        {"id": "walk", "layout": "stage", "headline": "Walking to the beach", "duration": 6,
         "stage": {"env": "beach", "time": "day", "actors": [{"action": "walk", "from": 10, "to": 90, "look": {"shirt": "teal", "hair_style": "long", "glasses": True}}]}},
        {"id": "robot", "layout": "stage", "headline": "Robot at the disco", "duration": 6,
         "stage": {"env": "disco", "time": "night", "actors": [{"kind": "robot", "action": "dance", "style": "robot", "x": 35}, {"action": "dance", "style": "hiphop", "x": 68, "look": {"hair_style": "bun", "shirt": "purple"}}]}},
        {"id": "city", "layout": "stage", "headline": "", "duration": 5,
         "stage": {"env": "city", "time": "night", "actors": [{"action": "march", "from": 15, "to": 85}]}},
        {"id": "space", "layout": "stage", "headline": "Jump!", "duration": 5,
         "stage": {"env": "space", "time": "night", "actors": [{"kind": "robot", "action": "jump", "look": {"color": "gold", "accent": "lime"}}]}},
        {"id": "forest", "layout": "stage", "headline": "Cheers", "duration": 5,
         "stage": {"env": "forest", "time": "sunset", "actors": [{"action": "cheer", "look": {"hair_style": "long", "hair": "ginger"}}, {"action": "clap", "x": 70}]}},
    ]}
    pid = create_project("Stage demo")["id"]
    print(json.dumps({"project_id": pid, **engine.build_video(pid, spec, narrate=False), "next": f"python scripts/cli.py render {pid}"}, indent=2))
elif args.cmd == "example":
    available = sorted(d.name for d in EXAMPLES_DIR.iterdir() if (d / "storyboard.json").is_file()) if EXAMPLES_DIR.is_dir() else []
    if not args.name or args.name not in available:
        sys.exit("Available examples: " + ", ".join(available))
    src = EXAMPLES_DIR / args.name
    project = create_project(f"Example: {args.name}")
    pid = project["id"]
    root = require_project_dir(pid)
    for f in sorted((src / "source").rglob("*")):
        if f.is_file():
            write_source(pid, f.relative_to(src / "source").as_posix(), f.read_text(encoding="utf-8"))
    write_storyboard(pid, json.loads((src / "storyboard.json").read_text(encoding="utf-8")))
    # Narration scripts (narration/*.txt) are turned into audio in the sandbox (needs Docker).
    for txt in sorted((src / "narration").glob("*.txt")) if (src / "narration").is_dir() else []:
        generate_narration(pid, txt.stem + ".wav", txt.read_text(encoding="utf-8"))
    print(json.dumps({"project_id": pid, "next": f"python scripts/cli.py render {pid}"}, indent=2))
