import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

# app.config reads these at import time, so they must be set before anything imports `app`.
_TMP_ROOT = tempfile.mkdtemp(prefix="vg-tests-")
os.environ["PROJECT_ROOT"] = _TMP_ROOT
os.environ["GROQ_API_KEY"] = ""
os.environ.pop("HOST_PROJECT_ROOT", None)

sys.path.insert(0, str(REPO))


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(_TMP_ROOT, ignore_errors=True)


@pytest.fixture
def project():
    from app.project import create_project, project_dir

    pid = create_project("test project")["id"]
    yield pid
    shutil.rmtree(project_dir(pid), ignore_errors=True)


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from app.main import app

    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def fake_sandbox(monkeypatch):
    """Replace Docker. Records commands; `creates` lists files (relative to the project) to write."""
    from app import tools
    from app.project import project_dir

    class Fake:
        calls = []
        creates = []

        def __call__(self, project_id, command, output_name=None):
            self.calls.append(command)
            root = project_dir(project_id)
            for rel in self.creates:
                target = root / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"data")
            return {"stdout": "", "stderr": "", "output": output_name}

    fake = Fake()
    fake.calls = []
    fake.creates = []
    monkeypatch.setattr(tools, "run_in_sandbox", fake)
    return fake


def make_renderable(pid):
    """Give a project the minimum files a render needs."""
    import json
    from app.project import project_dir

    root = project_dir(pid)
    (root / "source" / "index.html").write_text("<html></html>", encoding="utf-8")
    (root / "storyboard.json").write_text(
        json.dumps({"scenes": [{"id": "s1", "duration_seconds": 2}]}), encoding="utf-8"
    )
    return root
