from pathlib import Path
import os
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

PROJECT_ROOT = Path(os.getenv("PROJECT_ROOT", str(BASE_DIR / "projects"))).resolve()

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")

API_HOST = os.getenv("API_HOST", "127.0.0.1")
API_PORT = int(os.getenv("API_PORT", "8000"))

SANDBOX_IMAGE = os.getenv("SANDBOX_IMAGE", "ai-video-sandbox:latest")
SANDBOX_MEMORY = os.getenv("SANDBOX_MEMORY", "6g")
SANDBOX_CPUS = os.getenv("SANDBOX_CPUS", "4")
SANDBOX_PIDS_LIMIT = int(os.getenv("SANDBOX_PIDS_LIMIT", "256"))
SANDBOX_TIMEOUT_SECONDS = int(os.getenv("SANDBOX_TIMEOUT_SECONDS", "900"))

DEFAULT_FPS = int(os.getenv("DEFAULT_FPS", "30"))
DEFAULT_WIDTH = int(os.getenv("DEFAULT_WIDTH", "1920"))
DEFAULT_HEIGHT = int(os.getenv("DEFAULT_HEIGHT", "1080"))

MAX_PROJECT_BYTES = int(os.getenv("MAX_PROJECT_BYTES", str(2 * 1024**3)))
MAX_FILE_BYTES = int(os.getenv("MAX_FILE_BYTES", str(50 * 1024**2)))

PROJECT_ROOT.mkdir(parents=True, exist_ok=True)
