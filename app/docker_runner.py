from pathlib import Path
import subprocess
import uuid
from .config import (
    SANDBOX_IMAGE,
    SANDBOX_MEMORY,
    SANDBOX_CPUS,
    SANDBOX_PIDS_LIMIT,
    SANDBOX_TIMEOUT_SECONDS,
    HOST_PROJECT_ROOT,
)
from .project import project_dir

class DockerError(RuntimeError):
    pass

def _tail(text: str, limit: int = 4000) -> str:
    """Keep error output short: it is shown to the user and fed back to the LLM."""
    text = text or ""
    return text if len(text) <= limit else "...[truncated]...\n" + text[-limit:]

def _mount_source(project_id: str, root: Path) -> str:
    # When the API runs in a container, `root` only exists inside that container, but the
    # Docker daemon resolves bind-mount paths on the host.
    if HOST_PROJECT_ROOT:
        return HOST_PROJECT_ROOT.rstrip("/\\") + "/" + project_id
    return str(root)

def _hint(stderr: str) -> str:
    daemon_down = (
        "Cannot connect to the Docker daemon",  # older CLIs
        "error during connect",                 # older Docker Desktop on Windows
        "failed to connect to the docker API",  # current CLIs (Linux socket and Windows pipe)
    )
    if any(marker in stderr for marker in daemon_down):
        return "\nHint: the Docker daemon is not reachable. Is Docker Desktop / the Docker service running?"
    if "Unable to find image" in stderr or "pull access denied" in stderr or "No such image" in stderr:
        return f"\nHint: build the sandbox image first: docker build -t {SANDBOX_IMAGE} ./sandbox"
    return ""

def run_in_sandbox(project_id: str, command: list[str], output_name: str | None = None):
    root = project_dir(project_id)
    if not root.exists():
        raise FileNotFoundError(project_id)

    # A name lets us kill the container if the timeout fires.
    name = f"aivideo-{uuid.uuid4().hex[:12]}"

    # The container gets only the project workspace. It does not get the Docker socket.
    docker_cmd = [
        "docker", "run", "--rm",
        "--name", name,
        "--memory", SANDBOX_MEMORY,
        "--cpus", SANDBOX_CPUS,
        "--pids-limit", str(SANDBOX_PIDS_LIMIT),
        "--network", "none",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--read-only",
        "--tmpfs", "/tmp:rw,noexec,nosuid,size=1g",
        # With a read-only root filesystem, HOME must point at the tmpfs or Chromium and
        # fontconfig have nowhere to write their caches.
        "-e", "HOME=/tmp",
        "-v", f"{_mount_source(project_id, root)}:/workspace:rw",
        SANDBOX_IMAGE,
        *command,
    ]

    try:
        completed = subprocess.run(
            docker_cmd,
            capture_output=True,
            text=True,
            timeout=SANDBOX_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        # subprocess only kills the `docker run` client; the container would keep running
        # (and eating CPU/RAM) without this.
        try:
            subprocess.run(["docker", "kill", name], capture_output=True, check=False, timeout=30)
        except (OSError, subprocess.SubprocessError):
            pass
        raise DockerError(f"Sandbox timed out after {SANDBOX_TIMEOUT_SECONDS}s and was killed.") from exc
    except FileNotFoundError as exc:
        raise DockerError("Docker executable was not found on PATH.") from exc

    if completed.returncode != 0:
        raise DockerError(
            f"Sandbox failed with code {completed.returncode}\n"
            f"STDOUT:\n{_tail(completed.stdout)}\nSTDERR:\n{_tail(completed.stderr)}"
            f"{_hint(completed.stderr)}"
        )

    return {
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "output": str(root / output_name) if output_name else None,
    }
