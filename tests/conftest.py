import sys
from pathlib import Path

import pytest

# Ensure repo root is on sys.path for imports
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture
def tmp_workspace(tmp_path):
    """Provide a temporary workspace directory."""
    ws = tmp_path / "workspace" / "projects"
    ws.mkdir(parents=True)
    return tmp_path / "workspace"


@pytest.fixture
def tmp_runtime(tmp_path):
    """Provide a temporary runtime root."""
    rt = tmp_path / "runtime" / "state"
    rt.mkdir(parents=True)
    return tmp_path / "runtime"


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch, tmp_path):
    """Prevent tests from touching real ~/.teamos."""
    monkeypatch.setenv("TEAMOS_HOME", str(tmp_path / "teamos_home"))
    monkeypatch.setenv("TEAMOS_RUNTIME_ROOT", str(tmp_path / "runtime"))
    monkeypatch.setenv("TEAMOS_WORKSPACE_ROOT", str(tmp_path / "workspace"))
