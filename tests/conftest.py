"""Shared fixtures for tests."""
import json
import sys
import uuid as uuid_mod
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import cchat.store as store

# Global counter for timestamps
_COUNTER = 0


@pytest.fixture(autouse=True)
def _reset_counter():
    """Reset timestamp counter between tests."""
    global _COUNTER
    _COUNTER = 0
    yield
    _COUNTER = 0


@pytest.fixture()
def projects_dir(tmp_path, monkeypatch):
    """Patch cchat.store.PROJECTS_DIR to a temp directory."""
    pd = tmp_path / "projects"
    pd.mkdir()
    monkeypatch.setattr(store, "PROJECTS_DIR", pd)
    return pd


@pytest.fixture()
def make_conversation(projects_dir):
    """Factory fixture that creates a conversation JSONL file.

    Returns (path, uuid) so the caller can reference it.
    """

    def _make(
        lines: list[dict],
        project_key: str = "test-project",
        conv_uuid: str | None = None,
    ) -> tuple[Path, str]:
        c_uuid = conv_uuid or str(uuid_mod.uuid4())
        proj = projects_dir / project_key
        proj.mkdir(exist_ok=True)
        path = proj / f"{c_uuid}.jsonl"
        with open(path, "w", encoding="utf-8") as f:
            for line in lines:
                f.write(json.dumps(line) + "\n")
        return path, c_uuid

    return _make
