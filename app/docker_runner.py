from pathlib import Path
import os
import subprocess
import tempfile
from .config import SANDBOX_IMAGE, SANDBOX_MEMORY, SANDBOX_CPUS, SANDBOX_PIDS_LIMIT, SANDBOX_TIMEOUT_SECONDS
from .project import project_dir

class DockerError(RuntimeError):
    pass

def run_in_sandbox(project_id: str, command: list[str], output_name: str | None = None):
    root = project_dir(project_id)
    if not root.exists():
        raise FileNotFoundError(project_id)

    # The container gets only the project workspace. It does not get the Docker socket.
    docker_cmd = [
        "docker", "run", "--rm",
        "--memory", SANDBOX_MEMORY,
        "--cpus", SANDBOX_CPUS,
        "--pids-limit", str(SANDBOX_PIDS_LIMIT),
        "--network", "none",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--read-only",
        "--tmpfs", "/tmp:rw,noexec,nosuid,size=1g",
        "-v", f"{root}:/workspace:rw",
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
        raise DockerError("Sandbox timed out.") from exc
    except FileNotFoundError as exc:
        raise DockerError("Docker executable was not found on PATH.") from exc

    if completed.returncode != 0:
        raise DockerError(
            f"Sandbox failed with code {completed.returncode}\n"
            f"STDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}"
        )

    return {
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "output": str(project_dir(project_id) / output_name) if output_name else None,
    }
