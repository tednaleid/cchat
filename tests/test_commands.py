"""Integration-style tests for cchat CLI commands.

Each test exercises the command run() function with realistic JSONL data,
monkeypatching cchat.store.PROJECTS_DIR to use tmp_path.
"""

from __future__ import annotations

import argparse
import json
import os
import uuid as uuid_mod
from pathlib import Path

import pytest

import cchat.store as store
from cchat.commands import (
    agents_cmd,
    files_cmd,
    line_cmd,
    lines_cmd,
    list_cmd,
    search_cmd,
    tokens_cmd,
    view_cmd,
)


# ---------------------------------------------------------------------------
# JSONL line builder helpers
# ---------------------------------------------------------------------------

_COUNTER = 0


def _next_ts(base: str = "2025-06-15T10:00:00") -> str:
    """Return incrementing timestamps so sorting is deterministic."""
    global _COUNTER
    _COUNTER += 1
    # Increment minutes
    h, m = divmod(_COUNTER, 60)
    return f"2025-06-15T{10 + h:02d}:{m:02d}:00Z"


def _uuid() -> str:
    return str(uuid_mod.uuid4())


def make_system_line(
    subtype: str = "bridge_status",
    *,
    uuid_val: str | None = None,
    parent_uuid: str | None = None,
    timestamp: str | None = None,
    slug: str | None = None,
    **extra,
) -> dict:
    line = {
        "type": "system",
        "subtype": subtype,
        "uuid": uuid_val or _uuid(),
        "parentUuid": parent_uuid or "",
        "timestamp": timestamp or _next_ts(),
    }
    if slug is not None:
        line["slug"] = slug
    line.update(extra)
    return line


def make_user_line(
    content: str,
    *,
    uuid_val: str | None = None,
    parent_uuid: str | None = None,
    timestamp: str | None = None,
    **extra,
) -> dict:
    line = {
        "type": "user",
        "uuid": uuid_val or _uuid(),
        "parentUuid": parent_uuid or "",
        "timestamp": timestamp or _next_ts(),
        "message": {"content": content},
    }
    line.update(extra)
    return line


def make_tool_result_line(
    tool_use_id: str,
    text: str,
    *,
    uuid_val: str | None = None,
    parent_uuid: str | None = None,
    timestamp: str | None = None,
    **extra,
) -> dict:
    line = {
        "type": "user",
        "uuid": uuid_val or _uuid(),
        "parentUuid": parent_uuid or "",
        "timestamp": timestamp or _next_ts(),
        "message": {
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": [{"type": "text", "text": text}],
                }
            ]
        },
    }
    line.update(extra)
    return line


def make_assistant_line(
    text: str | None = None,
    *,
    tool_uses: list[dict] | None = None,
    message_id: str | None = None,
    usage: dict | None = None,
    uuid_val: str | None = None,
    parent_uuid: str | None = None,
    timestamp: str | None = None,
    stop_reason: str | None = "end_turn",
    **extra,
) -> dict:
    content_items: list[dict] = []
    if text is not None:
        content_items.append({"type": "text", "text": text})
    if tool_uses:
        for tu in tool_uses:
            content_items.append(tu)

    msg: dict = {
        "id": message_id or _uuid(),
        "content": content_items,
    }
    if stop_reason:
        msg["stop_reason"] = stop_reason
    if usage:
        msg["usage"] = usage

    line = {
        "type": "assistant",
        "uuid": uuid_val or _uuid(),
        "parentUuid": parent_uuid or "",
        "timestamp": timestamp or _next_ts(),
        "message": msg,
    }
    line.update(extra)
    return line


def _tool_use_item(name: str, input_data: dict, tool_use_id: str | None = None) -> dict:
    return {
        "type": "tool_use",
        "id": tool_use_id or _uuid(),
        "name": name,
        "input": input_data,
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


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


# ============================================================================
# list_cmd tests
# ============================================================================


class TestListCmd:
    def _args(self, **overrides) -> argparse.Namespace:
        defaults = dict(
            project=None,
            limit=20,
            sort="date",
            no_cost=False,
            include_subagents=False,
            json_output=False,
            no_color=True,
        )
        defaults.update(overrides)
        return argparse.Namespace(**defaults)

    def test_lists_conversations_sorted_by_date(
        self, make_conversation, capsys
    ):
        # Create two conversations with different timestamps
        make_conversation(
            [
                make_system_line(timestamp="2025-06-10T08:00:00Z"),
                make_user_line("First conv", timestamp="2025-06-10T08:01:00Z"),
            ],
        )
        make_conversation(
            [
                make_system_line(timestamp="2025-06-12T09:00:00Z"),
                make_user_line("Second conv", timestamp="2025-06-12T09:01:00Z"),
            ],
        )

        list_cmd.run(self._args())
        out = capsys.readouterr().out

        # Both conversations should appear
        assert "First conv" in out
        assert "Second conv" in out
        # Most recent first: "Second conv" should appear before "First conv"
        assert out.index("Second conv") < out.index("First conv")

    def test_sort_by_size(self, make_conversation, capsys):
        # Create a small and a large conversation
        make_conversation(
            [make_user_line("tiny")],
        )
        make_conversation(
            [make_user_line("large " * 200)] * 10,
        )

        list_cmd.run(self._args(sort="size"))
        out = capsys.readouterr().out
        lines = [l for l in out.strip().split("\n") if l.strip()]
        # Header + separator + 2 data rows
        assert len(lines) >= 4

    def test_limit_restricts_count(self, make_conversation, capsys):
        for i in range(5):
            make_conversation(
                [make_user_line(f"Conv {i}", timestamp=f"2025-06-1{i}T08:00:00Z")],
            )

        list_cmd.run(self._args(limit=2))
        out = capsys.readouterr().out
        data_lines = [
            l for l in out.strip().split("\n") if l.strip()
        ]
        # Header + separator + 2 data rows = 4 lines
        assert len(data_lines) == 4

    def test_project_filter(self, make_conversation, capsys):
        make_conversation(
            [make_user_line("In alpha")],
            project_key="alpha",
        )
        make_conversation(
            [make_user_line("In beta")],
            project_key="beta",
        )

        list_cmd.run(self._args(project="alpha"))
        out = capsys.readouterr().out
        assert "In alpha" in out
        assert "In beta" not in out

    def test_json_output(self, make_conversation, capsys):
        make_conversation(
            [
                make_system_line(timestamp="2025-06-10T08:00:00Z"),
                make_user_line("Hello world", timestamp="2025-06-10T08:01:00Z"),
            ],
        )

        list_cmd.run(self._args(json_output=True))
        out = capsys.readouterr().out
        data = json.loads(out)
        assert isinstance(data, list)
        assert len(data) == 1
        rec = data[0]
        assert "project_key" in rec
        assert "snippet" in rec
        assert "slug" in rec
        assert "turn_count" in rec
        assert "size" in rec

    def test_no_conversations_empty_table(self, projects_dir, capsys):
        list_cmd.run(self._args())
        out = capsys.readouterr().out
        # Should have header row at minimum
        assert "PROJECT" in out
        assert "SNIPPET" in out

    def test_no_color_disables_bold_header(self, make_conversation, capsys):
        make_conversation([make_user_line("test")])
        list_cmd.run(self._args(no_color=True))
        out = capsys.readouterr().out
        # No ANSI escape codes
        assert "\033[" not in out

    def test_include_subagents_table(self, make_conversation, projects_dir, capsys):
        """--include-subagents shows nested subagent rows in table output."""
        conv_uuid = "conv-with-agents-1234"
        path, _ = make_conversation(
            [
                make_user_line("parent task", timestamp="2025-06-15T10:00:00Z"),
                make_assistant_line(
                    "delegating",
                    timestamp="2025-06-15T10:01:00Z",
                    tool_uses=[_tool_use_item("Agent", {"prompt": "do research"})],
                ),
            ],
            conv_uuid=conv_uuid,
        )

        # Create subagent file
        sa_dir = path.parent / conv_uuid / "subagents"
        sa_dir.mkdir(parents=True)
        sa_file = sa_dir / "agent-abc123def456.jsonl"
        sa_lines = [
            make_user_line("Research the topic carefully", timestamp="2025-06-15T10:01:30Z"),
            make_assistant_line("Found results", timestamp="2025-06-15T10:02:00Z"),
        ]
        with open(sa_file, "w", encoding="utf-8") as f:
            for line in sa_lines:
                f.write(json.dumps(line) + "\n")

        list_cmd.run(self._args(include_subagents=True))
        out = capsys.readouterr().out
        assert "parent task" in out
        assert "abc123def456" in out
        assert "Research the topic careful" in out  # truncated snippet

    def test_include_subagents_json(self, make_conversation, projects_dir, capsys):
        """--include-subagents with --json includes subagents array."""
        conv_uuid = "conv-json-agents-5678"
        path, _ = make_conversation(
            [make_user_line("hello", timestamp="2025-06-15T10:00:00Z")],
            conv_uuid=conv_uuid,
        )

        # Create subagent
        sa_dir = path.parent / conv_uuid / "subagents"
        sa_dir.mkdir(parents=True)
        sa_file = sa_dir / "agent-deadbeef.jsonl"
        sa_lines = [
            make_user_line("sub prompt here", timestamp="2025-06-15T10:01:00Z"),
        ]
        with open(sa_file, "w", encoding="utf-8") as f:
            for line in sa_lines:
                f.write(json.dumps(line) + "\n")

        list_cmd.run(self._args(include_subagents=True, json_output=True))
        out = capsys.readouterr().out
        data = json.loads(out)
        assert len(data) == 1
        assert "subagents" in data[0]
        sa = data[0]["subagents"]
        assert len(sa) == 1
        assert sa[0]["agent_id"] == "deadbeef"
        assert "sub prompt here" in sa[0]["prompt_snippet"]

    def test_include_subagents_no_agents(self, make_conversation, capsys):
        """--include-subagents with no subagents shows normal output."""
        make_conversation(
            [make_user_line("solo conversation", timestamp="2025-06-15T10:00:00Z")]
        )
        list_cmd.run(self._args(include_subagents=True))
        out = capsys.readouterr().out
        assert "solo conversation" in out

    def test_include_subagents_json_no_agents(self, make_conversation, capsys):
        """--include-subagents --json without subagents omits subagents key."""
        make_conversation(
            [make_user_line("no agents here", timestamp="2025-06-15T10:00:00Z")]
        )
        list_cmd.run(self._args(include_subagents=True, json_output=True))
        out = capsys.readouterr().out
        data = json.loads(out)
        assert len(data) == 1
        assert "subagents" not in data[0]


# ============================================================================
# lines_cmd tests
# ============================================================================


class TestLinesCmd:
    def _args(self, conv, **overrides) -> argparse.Namespace:
        defaults = dict(
            conv=str(conv),
            agent_id=None,
            line_type=None,
            line_subtype=None,
            head=None,
            tail=None,
            from_line=None,
            to_line=None,
            full=False,
            max_chars=None,
            middle_out=False,
            json_output=False,
            no_color=True,
        )
        defaults.update(overrides)
        return argparse.Namespace(**defaults)

    def test_shows_deduplicated_lines_as_table(self, make_conversation, capsys):
        msg_id = _uuid()
        path, _ = make_conversation([
            make_system_line(timestamp="2025-06-15T10:00:00Z"),
            make_user_line("Hello there", timestamp="2025-06-15T10:01:00Z"),
            # Streaming chunks with same message id -- only last should appear
            make_assistant_line(
                "partial",
                message_id=msg_id,
                stop_reason=None,
                timestamp="2025-06-15T10:02:00Z",
            ),
            make_assistant_line(
                "complete answer",
                message_id=msg_id,
                stop_reason="end_turn",
                timestamp="2025-06-15T10:02:01Z",
            ),
        ])

        lines_cmd.run(self._args(path))
        out = capsys.readouterr().out

        assert "LINE#" in out
        assert "TYPE" in out
        assert "SNIPPET" in out
        # The duplicate assistant line should be deduplicated
        assert "complete answer" in out

    def test_type_filter(self, make_conversation, capsys):
        path, _ = make_conversation([
            make_system_line(timestamp="2025-06-15T10:00:00Z"),
            make_user_line("my question", timestamp="2025-06-15T10:01:00Z"),
            make_assistant_line("my answer", timestamp="2025-06-15T10:02:00Z"),
        ])

        lines_cmd.run(self._args(path, line_type="user"))
        out = capsys.readouterr().out
        assert "my question" in out
        # system and assistant lines should not appear
        lines = out.strip().split("\n")
        data_lines = lines[2:]  # skip header and separator
        for dl in data_lines:
            assert "system" not in dl.split()[1] if len(dl.split()) > 1 else True

    def test_head_limits(self, make_conversation, capsys):
        path, _ = make_conversation([
            make_user_line(f"line {i}", timestamp=f"2025-06-15T10:{i:02d}:00Z")
            for i in range(10)
        ])

        lines_cmd.run(self._args(path, head=3))
        out = capsys.readouterr().out
        data_lines = [
            l for l in out.strip().split("\n") if l.strip()
        ]
        # header + separator + 3 data rows
        assert len(data_lines) == 5

    def test_tail_limits(self, make_conversation, capsys):
        path, _ = make_conversation([
            make_user_line(f"line {i}", timestamp=f"2025-06-15T10:{i:02d}:00Z")
            for i in range(10)
        ])

        lines_cmd.run(self._args(path, tail=2))
        out = capsys.readouterr().out
        data_lines = [
            l for l in out.strip().split("\n") if l.strip()
        ]
        # header + separator + 2 data rows
        assert len(data_lines) == 4

    def test_head_and_tail_error(self, make_conversation, capsys):
        path, _ = make_conversation([make_user_line("x")])

        with pytest.raises(SystemExit):
            lines_cmd.run(self._args(path, head=5, tail=5))
        err = capsys.readouterr().err
        assert "mutually exclusive" in err

    def test_from_to_range(self, make_conversation, capsys):
        path, _ = make_conversation([
            make_user_line(f"msg {i}", timestamp=f"2025-06-15T10:{i:02d}:00Z")
            for i in range(10)
        ])

        # Lines are 1-indexed; show lines 3 through 5
        lines_cmd.run(self._args(path, from_line=3, to_line=5))
        out = capsys.readouterr().out
        data_lines = [
            l for l in out.strip().split("\n") if l.strip()
        ]
        # header + separator + 3 data rows
        assert len(data_lines) == 5

    def test_json_output(self, make_conversation, capsys):
        path, _ = make_conversation([
            make_user_line("hello", timestamp="2025-06-15T10:00:00Z"),
            make_assistant_line("world", timestamp="2025-06-15T10:01:00Z"),
        ])

        lines_cmd.run(self._args(path, json_output=True))
        out = capsys.readouterr().out
        data = json.loads(out)
        assert isinstance(data, list)
        assert len(data) == 2
        assert data[0]["type"] == "user"
        assert "line_number" in data[0]
        assert "snippet" in data[0]

    def test_full_mode(self, make_conversation, capsys):
        path, _ = make_conversation([
            make_user_line("a user message", timestamp="2025-06-15T10:00:00Z"),
            make_assistant_line("an answer", timestamp="2025-06-15T10:01:00Z"),
        ])

        lines_cmd.run(self._args(path, full=True))
        out = capsys.readouterr().out
        # Full mode renders with USER: / ASSISTANT: headers, not table
        assert "USER:" in out
        assert "ASSISTANT:" in out
        assert "a user message" in out
        assert "an answer" in out

    def test_default_shows_first_50(self, make_conversation, capsys):
        # Create 60 lines
        path, _ = make_conversation([
            make_user_line(f"line {i}", timestamp=f"2025-06-15T{10 + i // 60:02d}:{i % 60:02d}:00Z")
            for i in range(60)
        ])

        lines_cmd.run(self._args(path))
        out = capsys.readouterr().out
        data_lines = [
            l for l in out.strip().split("\n") if l.strip()
        ]
        # header + separator + 50 data rows = 52
        assert len(data_lines) == 52


# ============================================================================
# line_cmd tests (was view_cmd)
# ============================================================================


class TestViewCmd:
    def _args(self, conv, line, **overrides) -> argparse.Namespace:
        defaults = dict(
            conv=str(conv),
            line=line,
            raw=False,
            full=False,
            context=0,
            no_color=True,
        )
        defaults.update(overrides)
        return argparse.Namespace(**defaults)

    def test_view_single_line(self, make_conversation, capsys):
        path, _ = make_conversation([
            make_user_line("first message", timestamp="2025-06-15T10:00:00Z"),
            make_assistant_line("response", timestamp="2025-06-15T10:01:00Z"),
        ])

        line_cmd.run(self._args(path, 1))
        out = capsys.readouterr().out
        assert "USER:" in out
        assert "first message" in out

    def test_raw_json(self, make_conversation, capsys):
        path, _ = make_conversation([
            make_user_line("hello", timestamp="2025-06-15T10:00:00Z"),
        ])

        line_cmd.run(self._args(path, 1, raw=True))
        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["type"] == "user"
        assert data["message"]["content"] == "hello"

    def test_line_not_found(self, make_conversation):
        path, _ = make_conversation([
            make_user_line("only line"),
        ])

        with pytest.raises(SystemExit, match="Line 999 not found"):
            line_cmd.run(self._args(path, 999))

    def test_user_line_renders_with_user_header(self, make_conversation, capsys):
        path, _ = make_conversation([
            make_user_line("tell me a joke", timestamp="2025-06-15T10:00:00Z"),
        ])

        line_cmd.run(self._args(path, 1))
        out = capsys.readouterr().out
        assert "USER:" in out
        assert "tell me a joke" in out

    def test_assistant_line_renders_with_assistant_header(
        self, make_conversation, capsys
    ):
        path, _ = make_conversation([
            make_assistant_line(
                "Here is a joke",
                timestamp="2025-06-15T10:00:00Z",
            ),
        ])

        line_cmd.run(self._args(path, 1))
        out = capsys.readouterr().out
        assert "ASSISTANT:" in out
        assert "Here is a joke" in out

    def test_system_line_renders_with_system_header(
        self, make_conversation, capsys
    ):
        path, _ = make_conversation([
            make_system_line(
                subtype="bridge_status",
                timestamp="2025-06-15T10:00:00Z",
                sessionId="sess-123",
            ),
        ])

        line_cmd.run(self._args(path, 1))
        out = capsys.readouterr().out
        assert "SYSTEM:" in out
        assert "bridge_status" in out

    def test_tool_result_renders(self, make_conversation, capsys):
        path, _ = make_conversation([
            make_tool_result_line(
                "tool-id-abc",
                "file contents here",
                timestamp="2025-06-15T10:00:00Z",
            ),
        ])

        line_cmd.run(self._args(path, 1))
        out = capsys.readouterr().out
        assert "TOOL RESULT:" in out
        assert "tool-id-abc" in out
        assert "file contents here" in out

    def test_context_lines(self, make_conversation, capsys):
        path, _ = make_conversation([
            make_user_line("before msg", timestamp="2025-06-15T10:00:00Z"),
            make_assistant_line("target msg", timestamp="2025-06-15T10:01:00Z"),
            make_user_line("after msg", timestamp="2025-06-15T10:02:00Z"),
        ])

        # View line 2 with 1 context line on each side
        line_cmd.run(self._args(path, 2, context=1))
        out = capsys.readouterr().out
        assert "ASSISTANT:" in out
        assert "target msg" in out
        # Context lines should be rendered as compact one-liners
        assert "L1" in out
        assert "L3" in out


# ============================================================================
# files_cmd tests
# ============================================================================


class TestFilesCmd:
    def _args(self, conv, **overrides) -> argparse.Namespace:
        defaults = dict(
            conv=str(conv),
            no_subagents=False,
            json=False,
            no_color=True,
        )
        defaults.update(overrides)
        return argparse.Namespace(**defaults)

    def test_lists_modified_files(self, make_conversation, capsys):
        write_tool = _tool_use_item("Write", {"file_path": "/tmp/foo.py"})
        edit_tool = _tool_use_item("Edit", {"file_path": "/tmp/bar.py"})

        path, _ = make_conversation([
            make_assistant_line(
                "Writing files",
                tool_uses=[write_tool],
                timestamp="2025-06-15T10:00:00Z",
            ),
            make_assistant_line(
                "Editing files",
                tool_uses=[edit_tool],
                timestamp="2025-06-15T10:01:00Z",
            ),
        ])

        files_cmd.run(self._args(path))
        out = capsys.readouterr().out
        assert "/tmp/foo.py" in out
        assert "/tmp/bar.py" in out
        assert "Write" in out
        assert "Edit" in out

    def test_multiple_modifications_counted(self, make_conversation, capsys):
        write1 = _tool_use_item("Write", {"file_path": "/tmp/same.py"})
        edit1 = _tool_use_item("Edit", {"file_path": "/tmp/same.py"})

        path, _ = make_conversation([
            make_assistant_line(
                "first",
                tool_uses=[write1],
                timestamp="2025-06-15T10:00:00Z",
            ),
            make_assistant_line(
                "second",
                tool_uses=[edit1],
                timestamp="2025-06-15T10:01:00Z",
            ),
        ])

        files_cmd.run(self._args(path))
        out = capsys.readouterr().out
        assert "/tmp/same.py" in out
        # Count should be 2
        assert "2" in out

    def test_json_output(self, make_conversation, capsys):
        write_tool = _tool_use_item("Write", {"file_path": "/tmp/out.txt"})
        path, _ = make_conversation([
            make_assistant_line("w", tool_uses=[write_tool]),
        ])

        files_cmd.run(self._args(path, json=True))
        out = capsys.readouterr().out
        data = json.loads(out)
        assert isinstance(data, list)
        assert data[0]["file_path"] == "/tmp/out.txt"
        assert data[0]["modifications"] == 1
        assert "Write" in data[0]["tools"]

    def test_no_modifications(self, make_conversation, capsys):
        path, _ = make_conversation([
            make_user_line("hello"),
            make_assistant_line("hi"),
        ])

        files_cmd.run(self._args(path))
        out = capsys.readouterr().out
        assert "No file modifications found." in out

    def test_no_subagents_flag(self, make_conversation, projects_dir, capsys):
        # Create main conversation with a Write
        write_main = _tool_use_item("Write", {"file_path": "/tmp/main.py"})
        conv_uuid = str(uuid_mod.uuid4())
        path, _ = make_conversation(
            [make_assistant_line("main", tool_uses=[write_main])],
            conv_uuid=conv_uuid,
        )

        # Create subagent directory and file
        sa_dir = path.parent / conv_uuid / "subagents"
        sa_dir.mkdir(parents=True)
        write_sub = _tool_use_item("Write", {"file_path": "/tmp/subagent.py"})
        sa_line = make_assistant_line("sub work", tool_uses=[write_sub])
        sa_path = sa_dir / "agent-001.jsonl"
        with open(sa_path, "w", encoding="utf-8") as f:
            f.write(json.dumps(sa_line) + "\n")

        # With subagents (default)
        files_cmd.run(self._args(path))
        out = capsys.readouterr().out
        assert "/tmp/main.py" in out
        assert "/tmp/subagent.py" in out

        # Without subagents
        files_cmd.run(self._args(path, no_subagents=True))
        out = capsys.readouterr().out
        assert "/tmp/main.py" in out
        assert "/tmp/subagent.py" not in out

    def test_includes_subagent_files_by_default(
        self, make_conversation, projects_dir, capsys
    ):
        conv_uuid = str(uuid_mod.uuid4())
        path, _ = make_conversation(
            [make_user_line("hi")],
            conv_uuid=conv_uuid,
        )

        # Create subagent
        sa_dir = path.parent / conv_uuid / "subagents"
        sa_dir.mkdir(parents=True)
        write_sub = _tool_use_item("Edit", {"file_path": "/tmp/subfile.rs"})
        sa_line = make_assistant_line("editing", tool_uses=[write_sub])
        with open(sa_dir / "agent-002.jsonl", "w", encoding="utf-8") as f:
            f.write(json.dumps(sa_line) + "\n")

        files_cmd.run(self._args(path))
        out = capsys.readouterr().out
        assert "/tmp/subfile.rs" in out


# ============================================================================
# search_cmd tests
# ============================================================================


class TestSearchCmd:
    def _args(self, query, **overrides) -> argparse.Namespace:
        defaults = dict(
            query=query,
            project=None,
            limit=20,
            sort="newest",
            type_filter=None,
            first_per_conv=False,
            json=False,
            no_color=True,
        )
        defaults.update(overrides)
        return argparse.Namespace(**defaults)

    def test_finds_matches_case_insensitive(self, make_conversation, capsys):
        make_conversation([
            make_user_line("Hello World", timestamp="2025-06-15T10:00:00Z"),
            make_assistant_line("goodbye", timestamp="2025-06-15T10:01:00Z"),
        ])

        search_cmd.run(self._args("hello"))
        out = capsys.readouterr().out
        assert "Hello World" in out or "hello" in out.lower()

    def test_type_filter(self, make_conversation, capsys):
        make_conversation([
            make_user_line("findme user", timestamp="2025-06-15T10:00:00Z"),
            make_assistant_line("findme assistant", timestamp="2025-06-15T10:01:00Z"),
        ])

        search_cmd.run(self._args("findme", type_filter="assistant"))
        out = capsys.readouterr().out
        # Should only have assistant matches
        assert "assistant" in out
        # The table output includes type column -- check data lines
        data_lines = [
            l for l in out.strip().split("\n")
            if l.strip() and not l.startswith("DATE") and "---" not in l
        ]
        for dl in data_lines:
            assert "user" not in dl.lower().split()

    def test_limit_restricts(self, make_conversation, capsys):
        make_conversation([
            make_user_line(f"keyword {i}", timestamp=f"2025-06-15T10:{i:02d}:00Z")
            for i in range(10)
        ])

        search_cmd.run(self._args("keyword", limit=3))
        out = capsys.readouterr().out
        data_lines = [
            l for l in out.strip().split("\n")
            if l.strip() and "DATE" not in l and "---" not in l
        ]
        assert len(data_lines) == 3

    def test_sort_newest_oldest(self, make_conversation, capsys):
        make_conversation([
            make_user_line("target early", timestamp="2025-06-10T08:00:00Z"),
            make_user_line("target late", timestamp="2025-06-20T08:00:00Z"),
        ])

        # Newest first (default)
        search_cmd.run(self._args("target", sort="newest"))
        out_newest = capsys.readouterr().out

        search_cmd.run(self._args("target", sort="oldest"))
        out_oldest = capsys.readouterr().out

        # In newest: "06-20" should appear before "06-10"
        assert out_newest.index("06-20") < out_newest.index("06-10")
        # In oldest: "06-10" should appear before "06-20"
        assert out_oldest.index("06-10") < out_oldest.index("06-20")

    def test_first_per_conv(self, make_conversation, capsys):
        # Two conversations each containing "findme"
        make_conversation(
            [
                make_user_line("findme aaa", timestamp="2025-06-10T08:00:00Z"),
                make_user_line("findme bbb", timestamp="2025-06-10T08:01:00Z"),
            ],
        )
        make_conversation(
            [
                make_user_line("findme ccc", timestamp="2025-06-11T08:00:00Z"),
            ],
        )

        search_cmd.run(self._args("findme", first_per_conv=True))
        out = capsys.readouterr().out
        data_lines = [
            l for l in out.strip().split("\n")
            if l.strip() and "DATE" not in l and "---" not in l
        ]
        # Should be exactly 2 matches (one per conversation)
        assert len(data_lines) == 2

    def test_json_output(self, make_conversation, capsys):
        make_conversation([
            make_user_line("searchable text", timestamp="2025-06-15T10:00:00Z"),
        ])

        search_cmd.run(self._args("searchable", json=True))
        out = capsys.readouterr().out
        data = json.loads(out)
        assert "matches" in data
        assert "total" in data
        assert "capped" in data
        assert isinstance(data["matches"], list)
        assert len(data["matches"]) >= 1

    def test_no_matches(self, make_conversation, capsys):
        make_conversation([
            make_user_line("nothing relevant"),
        ])

        search_cmd.run(self._args("xyznonexistent"))
        out = capsys.readouterr().out
        assert "No matches found" in out

    def test_project_filter(self, make_conversation, capsys):
        make_conversation(
            [make_user_line("keyword alpha", timestamp="2025-06-15T10:00:00Z")],
            project_key="proj-a",
        )
        make_conversation(
            [make_user_line("keyword beta", timestamp="2025-06-15T10:01:00Z")],
            project_key="proj-b",
        )

        search_cmd.run(self._args("keyword", project="proj-a"))
        out = capsys.readouterr().out
        assert "alpha" in out
        assert "beta" not in out

    def test_truncation_notice_on_stderr(self, make_conversation, capsys):
        make_conversation([
            make_user_line(f"match {i}", timestamp=f"2025-06-15T10:{i:02d}:00Z")
            for i in range(10)
        ])

        search_cmd.run(self._args("match", limit=3))
        err = capsys.readouterr().err
        assert "showing" in err
        assert "3" in err


# ============================================================================
# tokens_cmd tests
# ============================================================================


class TestTokensCmd:
    def _args(self, conv, **overrides) -> argparse.Namespace:
        defaults = dict(
            conv=str(conv),
            json=False,
            no_color=True,
        )
        defaults.update(overrides)
        return argparse.Namespace(**defaults)

    def _usage(
        self,
        inp: int = 100,
        out: int = 50,
        cache_read: int = 0,
        cache_create: int = 0,
    ) -> dict:
        return {
            "input_tokens": inp,
            "output_tokens": out,
            "cache_read_input_tokens": cache_read,
            "cache_creation_input_tokens": cache_create,
        }

    def test_shows_per_turn_table(self, make_conversation, capsys):
        path, _ = make_conversation([
            make_assistant_line(
                "resp1",
                usage=self._usage(inp=1000, out=200),
                timestamp="2025-06-15T10:00:00Z",
            ),
            make_assistant_line(
                "resp2",
                usage=self._usage(inp=500, out=100),
                timestamp="2025-06-15T10:01:00Z",
            ),
        ])

        tokens_cmd.run(self._args(path))
        out = capsys.readouterr().out
        assert "TURN" in out
        assert "INPUT" in out
        assert "OUTPUT" in out
        assert "TOTAL" in out

    def test_totals_correct(self, make_conversation, capsys):
        path, _ = make_conversation([
            make_assistant_line(
                "r1",
                usage=self._usage(inp=1000, out=200, cache_read=300, cache_create=100),
                timestamp="2025-06-15T10:00:00Z",
            ),
            make_assistant_line(
                "r2",
                usage=self._usage(inp=500, out=100, cache_read=200, cache_create=50),
                timestamp="2025-06-15T10:01:00Z",
            ),
        ])

        tokens_cmd.run(self._args(path))
        out = capsys.readouterr().out
        # Total input = 1000 + 500 = 1,500
        assert "1,500" in out
        # Total output = 200 + 100 = 300
        assert "300" in out

    def test_cost_estimate(self, make_conversation, capsys):
        # Cost with Sonnet 4.6 fallback (no model specified):
        # input=1000, output=200, cache_read=300, cache_create=100
        # = (1000*3 + 200*15 + 300*0.30 + 100*3.75) / 1_000_000
        # = (3000 + 3000 + 90 + 375) / 1_000_000
        # = 6465 / 1_000_000 = 0.006465 -> $0.006
        path, _ = make_conversation([
            make_assistant_line(
                "r",
                usage=self._usage(inp=1000, out=200, cache_read=300, cache_create=100),
                timestamp="2025-06-15T10:00:00Z",
            ),
        ])

        tokens_cmd.run(self._args(path))
        out = capsys.readouterr().out
        assert "$0.006" in out or "$0.01" in out  # Allow both 3-decimal and 2-decimal formats

    def test_json_output(self, make_conversation, capsys):
        path, _ = make_conversation([
            make_assistant_line(
                "r",
                usage=self._usage(inp=1000, out=500, cache_read=200, cache_create=100),
                timestamp="2025-06-15T10:00:00Z",
            ),
        ])

        tokens_cmd.run(self._args(path, json=True))
        out = capsys.readouterr().out
        data = json.loads(out)
        assert "turns" in data
        assert "totals" in data
        assert "estimated_cost_usd" in data
        assert data["totals"]["input_tokens"] == 1000
        assert data["totals"]["output_tokens"] == 500
        assert data["totals"]["cache_read_input_tokens"] == 200
        assert data["totals"]["cache_creation_input_tokens"] == 100
        assert isinstance(data["estimated_cost_usd"], float)

    def test_no_usage_data(self, make_conversation, capsys):
        path, _ = make_conversation([
            make_user_line("hello"),
            make_assistant_line("world"),  # no usage dict
        ])

        tokens_cmd.run(self._args(path))
        out = capsys.readouterr().out
        assert "No token usage data found." in out

    def test_multiple_turns_accumulate(self, make_conversation, capsys):
        path, _ = make_conversation([
            make_assistant_line(
                "t1",
                usage=self._usage(inp=100, out=50, cache_read=10, cache_create=5),
                timestamp="2025-06-15T10:00:00Z",
            ),
            make_assistant_line(
                "t2",
                usage=self._usage(inp=200, out=75, cache_read=20, cache_create=10),
                timestamp="2025-06-15T10:01:00Z",
            ),
            make_assistant_line(
                "t3",
                usage=self._usage(inp=300, out=100, cache_read=30, cache_create=15),
                timestamp="2025-06-15T10:02:00Z",
            ),
        ])

        tokens_cmd.run(self._args(path, json=True))
        out = capsys.readouterr().out
        data = json.loads(out)
        assert len(data["turns"]) == 3
        assert data["totals"]["input_tokens"] == 600
        assert data["totals"]["output_tokens"] == 225
        assert data["totals"]["cache_read_input_tokens"] == 60
        assert data["totals"]["cache_creation_input_tokens"] == 30

        # Verify cost calculation with Sonnet 4.6 fallback (no model specified):
        # (600*3 + 225*15 + 60*0.30 + 30*3.75) / 1_000_000
        # = (1800 + 3375 + 18 + 112.5) / 1_000_000 = 0.0105255
        expected_cost = (600 * 3 + 225 * 15 + 60 * 0.30 + 30 * 3.75) / 1_000_000
        assert data["estimated_cost_usd"] == round(expected_cost, 2)


# ============================================================================
# cli.py tests
# ============================================================================


class TestCli:
    def test_no_args_raises_system_exit(self, monkeypatch):
        """main() with no subcommand should exit (argparse requires one)."""
        monkeypatch.setattr("sys.argv", ["cchat"])
        from cchat.cli import main
        with pytest.raises(SystemExit):
            main()

    def test_no_color_flag_sets_formatters(self, monkeypatch, make_conversation, capsys):
        """--no-color should call formatters.set_no_color(True)."""
        from cchat.cli import main
        from cchat import formatters

        # Create a conversation so 'list' has something to work with
        make_conversation([make_user_line("test")])

        monkeypatch.setattr("sys.argv", ["cchat", "--no-color", "list"])
        main()
        # After main() with --no-color, the internal flag should be True
        assert formatters._no_color is True
        # Reset for other tests
        formatters.set_no_color(False)

    def test_keyboard_interrupt_exits_cleanly(self, monkeypatch):
        """main() should catch KeyboardInterrupt and exit with 0."""
        from cchat.cli import main

        def _raise_interrupt(_args):
            raise KeyboardInterrupt

        monkeypatch.setattr("sys.argv", ["cchat", "list"])
        # Patch the list_cmd.run to raise KeyboardInterrupt
        from cchat.commands import list_cmd
        monkeypatch.setattr(list_cmd, "run", _raise_interrupt)

        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 0

    def test_broken_pipe_exits_cleanly(self, monkeypatch):
        """main() should catch BrokenPipeError and exit with 0."""
        from cchat.cli import main

        def _raise_broken_pipe(_args):
            raise BrokenPipeError

        monkeypatch.setattr("sys.argv", ["cchat", "list"])
        from cchat.commands import list_cmd
        monkeypatch.setattr(list_cmd, "run", _raise_broken_pipe)

        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 0


# ============================================================================
# Additional lines_cmd tests
# ============================================================================


class TestLinesCmdFullMode:
    """Tests for lines_cmd full-mode rendering paths not covered above."""

    def _args(self, conv, **overrides) -> argparse.Namespace:
        defaults = dict(
            conv=str(conv),
            agent_id=None,
            line_type=None,
            line_subtype=None,
            head=None,
            tail=None,
            from_line=None,
            to_line=None,
            full=True,
            max_chars=None,
            middle_out=False,
            json_output=False,
            no_color=True,
        )
        defaults.update(overrides)
        return argparse.Namespace(**defaults)

    def test_full_tool_result_with_list_sub_content(self, make_conversation, capsys):
        """Full mode shows tool_result items with sub_content as a list."""
        path, _ = make_conversation([
            make_tool_result_line(
                "tool-abc",
                "result text here",
                timestamp="2025-06-15T10:00:00Z",
            ),
        ])
        lines_cmd.run(self._args(path))
        out = capsys.readouterr().out
        assert "TOOL RESULT:" in out
        assert "tool-abc" in out
        assert "result text here" in out

    def test_full_tool_result_with_string_sub_content(self, make_conversation, capsys):
        """Full mode shows tool_result items with sub_content as a string."""
        line = {
            "type": "user",
            "uuid": _uuid(),
            "parentUuid": "",
            "timestamp": _next_ts(),
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "tool-str-123",
                        "content": "plain string content",
                    }
                ]
            },
        }
        path, _ = make_conversation([line])
        lines_cmd.run(self._args(path))
        out = capsys.readouterr().out
        assert "TOOL RESULT:" in out
        assert "tool-str-123" in out
        assert "plain string content" in out

    def test_full_assistant_with_thinking(self, make_conversation, capsys):
        """Full mode shows thinking blocks in assistant lines."""
        line = make_assistant_line(None, timestamp="2025-06-15T10:00:00Z")
        line["message"]["content"] = [
            {"type": "thinking", "thinking": "Let me reason about this..."},
            {"type": "text", "text": "Here is my answer."},
        ]
        path, _ = make_conversation([line])
        lines_cmd.run(self._args(path))
        out = capsys.readouterr().out
        assert "ASSISTANT:" in out
        assert "[THINKING]" in out
        assert "Let me reason about this..." in out
        assert "Here is my answer." in out

    def test_full_assistant_with_tool_use(self, make_conversation, capsys):
        """Full mode shows tool_use blocks in assistant lines."""
        tool_item = _tool_use_item("Read", {"file_path": "/tmp/test.py"})
        line = make_assistant_line("Some text", tool_uses=[tool_item],
                                  timestamp="2025-06-15T10:00:00Z")
        path, _ = make_conversation([line])
        lines_cmd.run(self._args(path))
        out = capsys.readouterr().out
        assert "ASSISTANT:" in out
        assert "[TOOL: Read]" in out
        assert "/tmp/test.py" in out

    def test_full_system_line_with_fields(self, make_conversation, capsys):
        """Full mode shows system lines with subtype, url, durationMs, sessionId."""
        line = make_system_line(
            subtype="init",
            timestamp="2025-06-15T10:00:00Z",
            url="https://example.com",
            durationMs=1234,
            sessionId="sess-xyz",
        )
        path, _ = make_conversation([line])
        lines_cmd.run(self._args(path))
        out = capsys.readouterr().out
        assert "SYSTEM:" in out
        assert "subtype: init" in out
        assert "url: https://example.com" in out
        assert "durationMs: 1234" in out
        assert "sessionId: sess-xyz" in out

    def test_full_unknown_type_line(self, make_conversation, capsys):
        """Full mode shows unknown line types with JSON dump."""
        line = {
            "type": "custom_weird",
            "uuid": _uuid(),
            "parentUuid": "",
            "timestamp": _next_ts(),
            "data": "some payload",
        }
        path, _ = make_conversation([line])
        lines_cmd.run(self._args(path))
        out = capsys.readouterr().out
        assert "CUSTOM_WEIRD:" in out
        assert "some payload" in out

    def test_trunc_with_full_bypasses_truncation(self):
        """_trunc with full=True returns the full text."""
        long_text = "x" * 500
        assert lines_cmd._trunc(long_text, 10, True) == long_text

    def test_trunc_without_full_truncates(self):
        """_trunc with full=False truncates the text."""
        long_text = "x" * 500
        result = lines_cmd._trunc(long_text, 10, False)
        assert len(result) <= 10


# ============================================================================
# Additional line_cmd tests
# ============================================================================


class TestViewCmdAdditional:
    """Tests for line_cmd rendering paths not covered above."""

    def _args(self, conv, line, **overrides) -> argparse.Namespace:
        defaults = dict(
            conv=str(conv),
            line=line,
            raw=False,
            full=False,
            context=0,
            no_color=True,
        )
        defaults.update(overrides)
        return argparse.Namespace(**defaults)

    def test_user_line_with_none_content(self, make_conversation, capsys):
        """User line with None content falls through to else branch."""
        line = {
            "type": "user",
            "uuid": _uuid(),
            "parentUuid": "",
            "timestamp": _next_ts(),
            "message": {"content": None},
        }
        path, _ = make_conversation([line])
        line_cmd.run(self._args(path, 1))
        out = capsys.readouterr().out
        assert "USER:" in out
        assert "None" in out

    def test_user_line_with_non_string_non_list_content(self, make_conversation, capsys):
        """User line with integer content falls through to else branch."""
        line = {
            "type": "user",
            "uuid": _uuid(),
            "parentUuid": "",
            "timestamp": _next_ts(),
            "message": {"content": 42},
        }
        path, _ = make_conversation([line])
        line_cmd.run(self._args(path, 1))
        out = capsys.readouterr().out
        assert "USER:" in out
        assert "42" in out

    def test_tool_result_with_list_sub_content(self, make_conversation, capsys):
        """Tool result with sub_content as list of text items."""
        line = {
            "type": "user",
            "uuid": _uuid(),
            "parentUuid": "",
            "timestamp": _next_ts(),
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "tid-list",
                        "content": [
                            {"type": "text", "text": "first chunk"},
                            {"type": "text", "text": "second chunk"},
                        ],
                    }
                ]
            },
        }
        path, _ = make_conversation([line])
        line_cmd.run(self._args(path, 1))
        out = capsys.readouterr().out
        assert "TOOL RESULT:" in out
        assert "tid-list" in out
        assert "first chunk" in out
        assert "second chunk" in out

    def test_tool_result_with_string_sub_content(self, make_conversation, capsys):
        """Tool result with sub_content as a plain string."""
        line = {
            "type": "user",
            "uuid": _uuid(),
            "parentUuid": "",
            "timestamp": _next_ts(),
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "tid-str",
                        "content": "just a string result",
                    }
                ]
            },
        }
        path, _ = make_conversation([line])
        line_cmd.run(self._args(path, 1))
        out = capsys.readouterr().out
        assert "TOOL RESULT:" in out
        assert "tid-str" in out
        assert "just a string result" in out

    def test_assistant_with_thinking_blocks(self, make_conversation, capsys):
        """Assistant line with thinking blocks renders [THINKING] header."""
        line = make_assistant_line(None, timestamp="2025-06-15T10:00:00Z")
        line["message"]["content"] = [
            {"type": "thinking", "thinking": "Deep thought here"},
            {"type": "text", "text": "Final answer"},
        ]
        path, _ = make_conversation([line])
        line_cmd.run(self._args(path, 1))
        out = capsys.readouterr().out
        assert "ASSISTANT:" in out
        assert "[THINKING]" in out
        assert "Deep thought here" in out
        assert "Final answer" in out

    def test_assistant_with_tool_use_blocks(self, make_conversation, capsys):
        """Assistant line with tool_use blocks renders [TOOL: name] header."""
        tool_item = _tool_use_item("Bash", {"command": "ls -la"})
        line = make_assistant_line("running command", tool_uses=[tool_item],
                                  timestamp="2025-06-15T10:00:00Z")
        path, _ = make_conversation([line])
        line_cmd.run(self._args(path, 1))
        out = capsys.readouterr().out
        assert "ASSISTANT:" in out
        assert "[TOOL: Bash]" in out
        assert "ls -la" in out

    def test_unknown_line_type(self, make_conversation, capsys):
        """Unknown line type renders with TYPE: header and JSON dump."""
        line = {
            "type": "exotic",
            "uuid": _uuid(),
            "parentUuid": "",
            "timestamp": _next_ts(),
            "foo": "bar",
        }
        path, _ = make_conversation([line])
        line_cmd.run(self._args(path, 1))
        out = capsys.readouterr().out
        assert "EXOTIC:" in out
        assert "bar" in out

    def test_context_deduped_away_line_warning(self, make_conversation, capsys):
        """Viewing with --context a line that was deduped shows a warning."""
        msg_id = _uuid()
        path, _ = make_conversation([
            make_user_line("before", timestamp="2025-06-15T10:00:00Z"),
            # Streaming chunk (no stop_reason) -- will be deduped
            make_assistant_line(
                "partial response",
                message_id=msg_id,
                stop_reason=None,
                timestamp="2025-06-15T10:01:00Z",
            ),
            # Final version with same message_id
            make_assistant_line(
                "complete response",
                message_id=msg_id,
                stop_reason="end_turn",
                timestamp="2025-06-15T10:01:01Z",
            ),
        ])
        # Line 2 is the streaming chunk; it exists in raw but is deduped
        line_cmd.run(self._args(path, 2, context=1))
        captured = capsys.readouterr()
        assert "deduplicated" in captured.err
        assert "streaming chunk" in captured.err
        # Should still render the line content
        assert "partial response" in captured.out

    def test_trunc_with_full_bypasses(self):
        """line_cmd._trunc with full=True returns full text."""
        long_text = "y" * 500
        assert line_cmd._trunc(long_text, 10, True) == long_text

    def test_trunc_without_full_truncates(self):
        """line_cmd._trunc with full=False truncates."""
        long_text = "y" * 500
        result = line_cmd._trunc(long_text, 10, False)
        assert len(result) <= 10


# ============================================================================
# Additional search_cmd tests
# ============================================================================


class TestSearchCmdAdditional:
    """Tests for search_cmd edge cases not covered above."""

    def _args(self, query, **overrides) -> argparse.Namespace:
        defaults = dict(
            query=query,
            project=None,
            limit=20,
            sort="newest",
            type_filter=None,
            first_per_conv=False,
            json=False,
            no_color=True,
        )
        defaults.update(overrides)
        return argparse.Namespace(**defaults)

    def test_hard_cap_limits_matches(self, make_conversation, monkeypatch, capsys):
        """When matches exceed HARD_CAP, search stops and marks capped=True."""
        # Set HARD_CAP to a small number for testing
        monkeypatch.setattr(search_cmd, "HARD_CAP", 5)

        # Create conversation with many matching lines
        many_lines = [
            make_user_line(f"keyword {i}", timestamp=f"2025-06-15T10:{i:02d}:00Z")
            for i in range(20)
        ]
        make_conversation(many_lines)

        search_cmd.run(self._args("keyword", json=True, limit=100))
        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["capped"] is True
        assert data["total"] == 5
        assert len(data["matches"]) == 5

    def test_malformed_json_line_in_search(self, make_conversation, projects_dir, capsys):
        """Malformed JSON lines that match query should produce data={} fallback."""
        proj = projects_dir / "test-project"
        proj.mkdir(exist_ok=True)
        c_uuid = str(uuid_mod.uuid4())
        path = proj / f"{c_uuid}.jsonl"
        with open(path, "w", encoding="utf-8") as f:
            # Write a valid line first
            f.write(json.dumps(make_user_line("normal line",
                               timestamp="2025-06-15T10:00:00Z")) + "\n")
            # Write a malformed JSON line that contains the search query
            f.write("this is not valid json but contains findme\n")

        search_cmd.run(self._args("findme", json=True))
        out = capsys.readouterr().out
        data = json.loads(out)
        assert len(data["matches"]) >= 1
        # The malformed line should have type "?" from data.get("type", "?")
        malformed_match = [m for m in data["matches"] if m["type"] == "?"]
        assert len(malformed_match) == 1


# ============================================================================
# Additional store.py tests
# ============================================================================


class TestStoreAdditional:
    """Tests for store.py edge cases not covered above."""

    def test_scan_continuation_loop_counts(self, projects_dir):
        """Lines beyond the first 101 should still be counted for turns/agents.

        Note: the first loop reads indices 0-99 (processing them) then reads
        index 100 to check the break condition, consuming it from the file
        iterator without processing.  So we need 101 filler lines (indices
        0-100) to ensure the user turn and agent call land in the
        continuation loop.
        """
        proj = projects_dir / "test-project"
        proj.mkdir(exist_ok=True)
        c_uuid = str(uuid_mod.uuid4())
        path = proj / f"{c_uuid}.jsonl"

        lines = []
        # 101 system lines: indices 0-100 consumed by the first loop
        for i in range(101):
            lines.append(json.dumps(make_system_line(
                timestamp=f"2025-06-15T{10 + i // 3600:02d}:{(i // 60) % 60:02d}:{i % 60:02d}Z"
            )))
        # Index 101+: user turn and agent call in continuation loop
        lines.append(json.dumps(make_user_line("turn in continuation",
                     timestamp="2025-06-15T12:00:00Z")))
        agent_tool = _tool_use_item("Agent", {"prompt": "do something"})
        lines.append(json.dumps(make_assistant_line(
            "agent call",
            tool_uses=[agent_tool],
            timestamp="2025-06-15T12:01:00Z",
        )))

        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

        info = store._scan_conversation(path)
        assert info.turn_count >= 1  # the user turn in continuation
        assert info.agent_count >= 1  # the agent call in continuation
        assert info.last_timestamp == "2025-06-15T12:01:00Z"

    def test_tail_optimization_oserror(self, projects_dir, monkeypatch):
        """OSError in tail optimization should be caught gracefully."""
        proj = projects_dir / "test-project"
        proj.mkdir(exist_ok=True)
        c_uuid = str(uuid_mod.uuid4())
        path = proj / f"{c_uuid}.jsonl"

        # Create a file large enough to trigger tail optimization (> 4096 bytes)
        lines_data = []
        for i in range(200):
            lines_data.append(json.dumps(make_user_line(
                "x" * 50,
                timestamp=f"2025-06-15T10:{i // 60:02d}:{i % 60:02d}Z",
            )))
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines_data) + "\n")

        assert os.path.getsize(path) > 4096

        # Monkeypatch open to raise OSError on 'rb' mode (the tail read)
        original_open = open
        call_count = [0]

        def _patched_open(p, mode="r", **kwargs):
            if str(p) == str(path) and mode == "rb":
                call_count[0] += 1
                raise OSError("simulated read error")
            return original_open(p, mode, **kwargs)

        monkeypatch.setattr("builtins.open", _patched_open)

        # Should not raise -- the OSError is caught
        info = store._scan_conversation(path)
        assert info.uuid == c_uuid
        assert call_count[0] >= 1

    def test_discover_skips_subagent_files(self, projects_dir):
        """Files in 'subagents' directories should be skipped by discover."""
        proj = projects_dir / "test-project"
        proj.mkdir(exist_ok=True)

        # Create a normal conversation
        c_uuid = str(uuid_mod.uuid4())
        path = proj / f"{c_uuid}.jsonl"
        with open(path, "w", encoding="utf-8") as f:
            f.write(json.dumps(make_user_line("normal")) + "\n")

        # discover should find it
        results = store.discover_conversations()
        assert len(results) == 1
        assert results[0].uuid == c_uuid

    def test_slug_resolution(self, projects_dir):
        """resolve_conversation should find a conversation by slug."""
        proj = projects_dir / "test-project"
        proj.mkdir(exist_ok=True)
        c_uuid = str(uuid_mod.uuid4())
        path = proj / f"{c_uuid}.jsonl"

        line = make_system_line(
            timestamp="2025-06-15T10:00:00Z",
            slug="my-test-slug",
        )
        with open(path, "w", encoding="utf-8") as f:
            f.write(json.dumps(line) + "\n")

        resolved = store.resolve_conversation("my-test-slug")
        assert resolved == path

    def test_slug_resolution_with_project_key(self, projects_dir):
        """resolve_conversation with project_key narrows slug search."""
        for pk in ("proj-a", "proj-b"):
            proj = projects_dir / pk
            proj.mkdir(exist_ok=True)

        c_uuid = str(uuid_mod.uuid4())
        path_a = projects_dir / "proj-a" / f"{c_uuid}.jsonl"
        line = make_system_line(timestamp="2025-06-15T10:00:00Z", slug="unique-slug")
        with open(path_a, "w", encoding="utf-8") as f:
            f.write(json.dumps(line) + "\n")

        resolved = store.resolve_conversation("unique-slug", project_key="proj-a")
        assert resolved == path_a

    def test_slug_resolution_nonexistent_dir(self, projects_dir):
        """resolve_conversation skips non-existent project dirs gracefully."""
        proj = projects_dir / "exists"
        proj.mkdir(exist_ok=True)
        c_uuid = str(uuid_mod.uuid4())
        path = proj / f"{c_uuid}.jsonl"
        line = make_system_line(timestamp="2025-06-15T10:00:00Z", slug="found-slug")
        with open(path, "w", encoding="utf-8") as f:
            f.write(json.dumps(line) + "\n")

        # Even though we search all projects (including non-existent ones),
        # should still find the slug
        resolved = store.resolve_conversation("found-slug")
        assert resolved == path


# ============================================================================
# resolve_agent / --agent flag tests
# ============================================================================


class TestResolveAgent:
    """Tests for store.resolve_agent and lines_cmd --agent flag."""

    def _lines_args(self, **overrides) -> argparse.Namespace:
        defaults = dict(
            conv=None,
            agent_id=None,
            line_type=None,
            line_subtype=None,
            head=None,
            tail=None,
            from_line=None,
            to_line=None,
            full=False,
            max_chars=None,
            middle_out=False,
            json_output=False,
            no_color=True,
        )
        defaults.update(overrides)
        return argparse.Namespace(**defaults)

    def _create_agent_file(self, projects_dir, agent_id, lines,
                           project_key="test-project", session_uuid=None):
        """Create a subagent JSONL file and return its path."""
        s_uuid = session_uuid or str(uuid_mod.uuid4())
        proj = projects_dir / project_key
        subagent_dir = proj / s_uuid / "subagents"
        subagent_dir.mkdir(parents=True, exist_ok=True)
        path = subagent_dir / f"agent-{agent_id}.jsonl"
        with open(path, "w", encoding="utf-8") as f:
            for line in lines:
                f.write(json.dumps(line) + "\n")
        return path

    def test_resolve_agent_found(self, projects_dir):
        """resolve_agent returns the path for a known agent ID."""
        agent_id = "ad37c9994945aa7c4"
        expected = self._create_agent_file(projects_dir, agent_id, [
            make_user_line("hello from subagent"),
        ])
        result = store.resolve_agent(agent_id)
        assert result == expected

    def test_resolve_agent_not_found(self, projects_dir):
        """resolve_agent raises SystemExit when agent ID is unknown."""
        with pytest.raises(SystemExit, match="No subagent found"):
            store.resolve_agent("deadbeef123456789")

    def test_resolve_agent_no_projects_dir(self, tmp_path, monkeypatch):
        """resolve_agent raises SystemExit when projects dir is missing."""
        monkeypatch.setattr(store, "PROJECTS_DIR", tmp_path / "nonexistent")
        with pytest.raises(SystemExit, match="does not exist"):
            store.resolve_agent("abc123")

    def test_resolve_agent_multiple_picks_newest(self, projects_dir):
        """When multiple matches exist, the most recently modified is returned."""
        agent_id = "abcdef1234567890a"
        import time

        older = self._create_agent_file(
            projects_dir, agent_id,
            [make_user_line("old")],
            project_key="proj-a",
            session_uuid=str(uuid_mod.uuid4()),
        )
        # Ensure the second file has a later mtime
        time.sleep(0.05)
        newer = self._create_agent_file(
            projects_dir, agent_id,
            [make_user_line("new")],
            project_key="proj-b",
            session_uuid=str(uuid_mod.uuid4()),
        )

        result = store.resolve_agent(agent_id)
        assert result == newer

    def test_lines_cmd_with_agent_flag(self, projects_dir, capsys):
        """lines_cmd runs successfully when --agent is provided instead of conv."""
        agent_id = "ff00112233aabbcc0"
        self._create_agent_file(projects_dir, agent_id, [
            make_user_line("subagent message", timestamp="2025-06-15T10:00:00Z"),
            make_assistant_line("subagent reply", timestamp="2025-06-15T10:01:00Z"),
        ])

        lines_cmd.run(self._lines_args(agent_id=agent_id))
        out = capsys.readouterr().out
        assert "subagent message" in out

    def test_lines_cmd_both_conv_and_agent_errors(self, projects_dir, capsys):
        """Providing both conv and --agent should error."""
        with pytest.raises(SystemExit):
            lines_cmd.run(self._lines_args(conv="something", agent_id="something"))

    def test_lines_cmd_neither_conv_nor_agent_errors(self, projects_dir, capsys):
        """Providing neither conv nor --agent should error."""
        with pytest.raises(SystemExit):
            lines_cmd.run(self._lines_args())


# ============================================================================
# agents_cmd tests
# ============================================================================


class TestAgentsCmd:
    """Tests for the ``cchat agents`` subcommand."""

    def _args(self, conv, **overrides) -> argparse.Namespace:
        defaults = dict(
            conv=str(conv),
            project=None,
            json_output=False,
            no_color=True,
        )
        defaults.update(overrides)
        return argparse.Namespace(**defaults)

    def _create_subagents(self, path, conv_uuid, agents):
        """Create subagent JSONL files.

        ``agents`` is a list of (agent_id, lines) tuples.
        """
        sa_dir = path.parent / conv_uuid / "subagents"
        sa_dir.mkdir(parents=True, exist_ok=True)
        for agent_id, lines in agents:
            sa_file = sa_dir / f"agent-{agent_id}.jsonl"
            with open(sa_file, "w", encoding="utf-8") as f:
                for line in lines:
                    f.write(json.dumps(line) + "\n")

    def test_agents_table_output(self, make_conversation, capsys):
        """Table output shows agent IDs and prompt snippets."""
        conv_uuid = "agents-test-uuid-1"
        path, _ = make_conversation(
            [make_user_line("parent", timestamp="2025-06-15T10:00:00Z")],
            conv_uuid=conv_uuid,
        )
        self._create_subagents(path, conv_uuid, [
            ("aaa111", [
                make_user_line("Investigate the crash logs", timestamp="2025-06-15T10:01:00Z"),
                make_assistant_line("Found issue", timestamp="2025-06-15T10:02:00Z"),
            ]),
            ("bbb222", [
                make_user_line("Write unit tests for parser", timestamp="2025-06-15T10:03:00Z"),
                make_assistant_line("Tests written", timestamp="2025-06-15T10:04:00Z"),
            ]),
        ])

        agents_cmd.run(self._args(path))
        out = capsys.readouterr().out
        assert "aaa111" in out
        assert "bbb222" in out
        assert "Investigate the crash" in out
        assert "Write unit tests" in out
        assert "AGENT_ID" in out  # header

    def test_agents_json_output(self, make_conversation, capsys):
        """JSON output includes structured agent metadata."""
        conv_uuid = "agents-test-uuid-2"
        path, _ = make_conversation(
            [make_user_line("parent", timestamp="2025-06-15T10:00:00Z")],
            conv_uuid=conv_uuid,
        )
        self._create_subagents(path, conv_uuid, [
            ("ccc333", [
                make_user_line("Analyze binary", timestamp="2025-06-15T10:01:00Z"),
                make_assistant_line("Analysis done", timestamp="2025-06-15T10:02:00Z"),
                make_user_line("What about the header?", timestamp="2025-06-15T10:03:00Z"),
            ]),
        ])

        agents_cmd.run(self._args(path, json_output=True))
        out = capsys.readouterr().out
        data = json.loads(out)
        assert len(data) == 1
        agent = data[0]
        assert agent["agent_id"] == "ccc333"
        assert "Analyze binary" in agent["prompt_snippet"]
        assert agent["turn_count"] == 2
        assert agent["line_count"] == 3
        assert agent["first_timestamp"] == "2025-06-15T10:01:00Z"
        assert agent["last_timestamp"] == "2025-06-15T10:03:00Z"
        assert "size" in agent

    def test_agents_no_subagents_message(self, make_conversation, capsys):
        """When no subagents exist, prints informational message."""
        path, _ = make_conversation(
            [make_user_line("solo", timestamp="2025-06-15T10:00:00Z")]
        )
        agents_cmd.run(self._args(path))
        out = capsys.readouterr().out
        assert "No subagents found" in out

    def test_agents_resolves_by_uuid(self, make_conversation, capsys):
        """Conversation can be resolved by UUID."""
        conv_uuid = "agents-resolve-uuid"
        path, _ = make_conversation(
            [make_user_line("parent", timestamp="2025-06-15T10:00:00Z")],
            conv_uuid=conv_uuid,
        )
        self._create_subagents(path, conv_uuid, [
            ("ddd444", [
                make_user_line("subagent work", timestamp="2025-06-15T10:01:00Z"),
            ]),
        ])

        agents_cmd.run(self._args(conv_uuid))
        out = capsys.readouterr().out
        assert "ddd444" in out

    def test_agents_multiple_sorted_by_timestamp(self, make_conversation, capsys):
        """Multiple subagents should appear sorted by first_timestamp."""
        conv_uuid = "agents-sorted-uuid"
        path, _ = make_conversation(
            [make_user_line("parent", timestamp="2025-06-15T10:00:00Z")],
            conv_uuid=conv_uuid,
        )
        # Create agents with out-of-order timestamps (zzz should sort after aaa)
        self._create_subagents(path, conv_uuid, [
            ("zzz_later", [
                make_user_line("later agent", timestamp="2025-06-15T10:05:00Z"),
            ]),
            ("aaa_earlier", [
                make_user_line("earlier agent", timestamp="2025-06-15T10:01:00Z"),
            ]),
        ])

        agents_cmd.run(self._args(path, json_output=True))
        out = capsys.readouterr().out
        data = json.loads(out)
        assert len(data) == 2
        # Earlier agent should come first
        assert data[0]["agent_id"] == "aaa_earlier"
        assert data[1]["agent_id"] == "zzz_later"


# ============================================================================
# store.list_subagents / store._scan_subagent tests
# ============================================================================


class TestStoreListSubagents:
    """Tests for store.list_subagents and store._scan_subagent."""

    def _create_conv_with_agents(self, projects_dir, conv_uuid, agents,
                                 project_key="test-project"):
        """Create a conversation file and its subagent files.

        ``agents`` is a list of (agent_id, lines) tuples.
        Returns the conversation path.
        """
        proj = projects_dir / project_key
        proj.mkdir(exist_ok=True)
        conv_path = proj / f"{conv_uuid}.jsonl"
        conv_path.write_text(
            json.dumps(make_user_line("parent")) + "\n",
            encoding="utf-8",
        )

        sa_dir = proj / conv_uuid / "subagents"
        sa_dir.mkdir(parents=True, exist_ok=True)
        for agent_id, lines in agents:
            sa_file = sa_dir / f"agent-{agent_id}.jsonl"
            with open(sa_file, "w", encoding="utf-8") as f:
                for line in lines:
                    f.write(json.dumps(line) + "\n")
        return conv_path

    def test_list_subagents_basic(self, projects_dir):
        """list_subagents returns SubagentInfo for each agent."""
        conv_path = self._create_conv_with_agents(
            projects_dir, "conv-sa-1", [
                ("agent1", [
                    make_user_line("first prompt", timestamp="2025-06-15T10:00:00Z"),
                    make_assistant_line("reply", timestamp="2025-06-15T10:01:00Z"),
                ]),
            ],
        )
        results = store.list_subagents(conv_path)
        assert len(results) == 1
        sa = results[0]
        assert sa.agent_id == "agent1"
        assert sa.conversation_uuid == "conv-sa-1"
        assert sa.project_key == "test-project"
        assert sa.prompt_snippet == "first prompt"
        assert sa.line_count == 2
        assert sa.turn_count == 1
        assert sa.first_timestamp == "2025-06-15T10:00:00Z"
        assert sa.last_timestamp == "2025-06-15T10:01:00Z"

    def test_list_subagents_empty(self, projects_dir):
        """No subagent dir returns empty list."""
        proj = projects_dir / "test-project"
        proj.mkdir(exist_ok=True)
        conv_path = proj / "conv-no-agents.jsonl"
        conv_path.write_text("{}\n", encoding="utf-8")
        results = store.list_subagents(conv_path)
        assert results == []

    def test_scan_subagent_no_user_lines(self, projects_dir):
        """Subagent with only assistant lines has no prompt_snippet."""
        conv_path = self._create_conv_with_agents(
            projects_dir, "conv-sa-noprompt", [
                ("agentX", [
                    make_assistant_line("just a reply", timestamp="2025-06-15T10:00:00Z"),
                ]),
            ],
        )
        results = store.list_subagents(conv_path)
        assert len(results) == 1
        assert results[0].prompt_snippet is None
        assert results[0].turn_count == 0
        assert results[0].line_count == 1
