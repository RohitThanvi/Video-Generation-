from pathlib import Path
import os
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

# A relative PROJECT_ROOT (e.g. "./projects" from .env.example) is resolved against the
# repository, not the current working directory, so the API, the CLI and scripts run
# from other directories all see the same projects.
_project_root = Path(os.getenv("PROJECT_ROOT") or (BASE_DIR / "projects")).expanduser()
if not _project_root.is_absolute():
    _project_root = BASE_DIR / _project_root
PROJECT_ROOT = _project_root.resolve()

# Only needed when the API itself runs inside a container (docker-compose): the Docker
# daemon lives on the host, so sandbox bind mounts must use the *host* path of PROJECT_ROOT.
HOST_PROJECT_ROOT = os.getenv("HOST_PROJECT_ROOT", "").strip()

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")

API_HOST = os.getenv("API_HOST", "127.0.0.1")
API_PORT = int(os.getenv("API_PORT", "8000"))

SANDBOX_IMAGE = os.getenv("SANDBOX_IMAGE", "ai-video-sandbox:latest")
SANDBOX_MEMORY = os.getenv("SANDBOX_MEMORY", "6g")
SANDBOX_CPUS = os.getenv("SANDBOX_CPUS", "4")
SANDBOX_PIDS_LIMIT = int(os.getenv("SANDBOX_PIDS_LIMIT", "256"))
SANDBOX_TIMEOUT_SECONDS = int(os.getenv("SANDBOX_TIMEOUT_SECONDS", "3600"))

# Lets the agent search free (openly licensed) images and download assets on the API host.
# The render sandbox itself always stays offline.
ALLOW_WEB_ASSETS = os.getenv("ALLOW_WEB_ASSETS", "true").strip().lower() in {"1", "true", "yes", "on"}

DEFAULT_FPS = int(os.getenv("DEFAULT_FPS", "30"))
DEFAULT_WIDTH = int(os.getenv("DEFAULT_WIDTH", "1920"))
DEFAULT_HEIGHT = int(os.getenv("DEFAULT_HEIGHT", "1080"))

MAX_PROJECT_BYTES = int(os.getenv("MAX_PROJECT_BYTES", str(2 * 1024**3)))
MAX_FILE_BYTES = int(os.getenv("MAX_FILE_BYTES", str(50 * 1024**2)))

PROJECT_ROOT.mkdir(parents=True, exist_ok=True)
