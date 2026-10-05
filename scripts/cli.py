import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.project import create_project, load_project
from app.agent import run_agent
from app.tools import validate, render, ingest_host_asset

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
