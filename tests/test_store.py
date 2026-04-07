"""Tests for cchat.store module."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cchat.store import (
    _extract_snippet,
    _is_agent_call,
    _is_user_turn,
    _parse_line,
    _scan_conversation,
    discover_conversations,
    get_subagent_paths,
    list_projects,
    resolve_conversation,
)


# ---------------------------------------------------------------------------
# JSONL line helpers
# ---------------------------------------------------------------------------

def _user_line(content: str, timestamp: str | None = None, slug: str | None = None) -> str:
    """Generate a realistic user turn JSONL line."""
    obj: dict = {
        "type": "user",
        "message": {"role": "user", "content": content},
    }
    if timestamp:
        obj["timestamp"] = timestamp
    if slug:
        obj["slug"] = slug
    return json.dumps(obj)


def _assistant_line(
    content_text: str = "Sure.",
    timestamp: str | None = None,
    tool_uses: list[dict] | None = None,
) -> str:
    """Generate a realistic assistant turn JSONL line."""
    content: list[dict] = [{"type": "text", "text": content_text}]
    if tool_uses:
        content.extend(tool_uses)
    obj: dict = {
        "type": "assistant",
        "message": {"role": "assistant", "content": content},
    }
    if timestamp:
        obj["timestamp"] = timestamp
    return json.dumps(obj)


def _agent_tool_use() -> dict:
    """Return an Agent tool_use content block."""
    return {"type": "tool_use", "name": "Agent", "id": "tu_1", "input": {}}


def _other_tool_use(name: str = "Read") -> dict:
    """Return a non-Agent tool_use content block."""
    return {"type": "tool_use", "name": name, "id": "tu_2", "input": {}}


def _system_line(timestamp: str | None = None) -> str:
    """Generate a system/summary JSONL line."""
    obj: dict = {"type": "system", "message": {"role": "system", "content": "init"}}
    if timestamp:
        obj["timestamp"] = timestamp
    return json.dumps(obj)


def _tool_result_user_line(timestamp: str | None = None) -> str:
    """Generate a user line with list content (tool result), not a real user turn."""
    obj: dict = {
        "type": "user",
        "message": {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "tu_1", "content": "ok"}],
        },
    }
    if timestamp:
        obj["timestamp"] = timestamp
    return json.dumps(obj)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def projects_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create and monkeypatch PROJECTS_DIR to a temp directory."""
    p = tmp_path / "projects"
    p.mkdir()
    monkeypatch.setattr("cchat.store.PROJECTS_DIR", p)
    return p


@pytest.fixture()
def make_conversation(projects_dir: Path):
    """Factory fixture: create a JSONL conversation file under projects_dir.

    Usage:
        path = make_conversation("my-project", "abc12345", [line1, line2, ...])
    Returns the Path to the created .jsonl file.
    """

    def _factory(
        project: str,
        uuid: str,
        lines: list[str],
    ) -> Path:
        proj = projects_dir / project
        proj.mkdir(exist_ok=True)
        fpath = proj / f"{uuid}.jsonl"
        fpath.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return fpath

    return _factory


# ===================================================================
# _parse_line
# ===================================================================

class TestParseLine:
    def test_valid_json(self):
        result = _parse_line('{"type": "user"}')
        assert result == {"type": "user"}

    def test_invalid_json(self):
        assert _parse_line("{not json}") is None

    def test_empty_string(self):
        assert _parse_line("") is None

    def test_non_json_string(self):
        assert _parse_line("hello world") is None

    def test_valid_nested_json(self):
        line = json.dumps({"a": {"b": [1, 2, 3]}})
        result = _parse_line(line)
        assert result == {"a": {"b": [1, 2, 3]}}


# ===================================================================
# _is_user_turn
# ===================================================================

class TestIsUserTurn:
    def test_user_with_string_content(self):
        obj = {"type": "user", "message": {"content": "hello"}}
        assert _is_user_turn(obj) is True

    def test_user_with_list_content_tool_result(self):
        obj = {
            "type": "user",
            "message": {"content": [{"type": "tool_result"}]},
        }
        assert _is_user_turn(obj) is False

    def test_user_with_no_message(self):
        obj = {"type": "user"}
        assert _is_user_turn(obj) is False

    def test_non_user_type(self):
        obj = {"type": "assistant", "message": {"content": "hello"}}
        assert _is_user_turn(obj) is False

    def test_user_with_message_as_non_dict(self):
        obj = {"type": "user", "message": "a string"}
        assert _is_user_turn(obj) is False

    def test_user_with_content_none(self):
        obj = {"type": "user", "message": {"content": None}}
        assert _is_user_turn(obj) is False

    def test_user_with_content_int(self):
        obj = {"type": "user", "message": {"content": 42}}
        assert _is_user_turn(obj) is False


# ===================================================================
# _is_agent_call
# ===================================================================

class TestIsAgentCall:
    def test_assistant_with_agent_tool_use(self):
        obj = {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "text", "text": "Delegating..."},
                    {"type": "tool_use", "name": "Agent", "id": "tu_1"},
                ],
            },
        }
        assert _is_agent_call(obj) is True

    def test_assistant_with_non_agent_tool_use(self):
        obj = {
            "type": "assistant",
            "message": {
                "content": [{"type": "tool_use", "name": "Read", "id": "tu_2"}],
            },
        }
        assert _is_agent_call(obj) is False

    def test_assistant_with_no_tool_use(self):
        obj = {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "Just text."}]},
        }
        assert _is_agent_call(obj) is False

    def test_non_assistant_type(self):
        obj = {
            "type": "user",
            "message": {
                "content": [{"type": "tool_use", "name": "Agent", "id": "tu_1"}],
            },
        }
        assert _is_agent_call(obj) is False

    def test_assistant_with_message_as_non_dict(self):
        obj = {"type": "assistant", "message": "string message"}
        assert _is_agent_call(obj) is False

    def test_assistant_with_content_as_non_list(self):
        obj = {
            "type": "assistant",
            "message": {"content": "string content"},
        }
        assert _is_agent_call(obj) is False

    def test_assistant_with_content_containing_non_dict_items(self):
        obj = {
            "type": "assistant",
            "message": {"content": ["string item", 42, None]},
        }
        assert _is_agent_call(obj) is False

    def test_assistant_with_no_message_key(self):
        obj = {"type": "assistant"}
        assert _is_agent_call(obj) is False


# ===================================================================
# _extract_snippet
# ===================================================================

class TestExtractSnippet:
    def test_short_content(self):
        obj = {"message": {"content": "Hello world"}}
        assert _extract_snippet(obj) == "Hello world"

    def test_content_over_60_chars(self):
        long_text = "A" * 80
        obj = {"message": {"content": long_text}}
        result = _extract_snippet(obj)
        assert result == "A" * 60
        assert len(result) == 60

    def test_content_exactly_60_chars(self):
        text = "B" * 60
        obj = {"message": {"content": text}}
        assert _extract_snippet(obj) == text

    def test_content_with_newlines(self):
        obj = {"message": {"content": "line1\nline2\nline3"}}
        assert _extract_snippet(obj) == "line1 line2 line3"

    def test_no_message(self):
        obj = {"type": "user"}
        assert _extract_snippet(obj) is None

    def test_message_not_dict(self):
        obj = {"message": "just a string"}
        assert _extract_snippet(obj) is None

    def test_content_not_string(self):
        obj = {"message": {"content": [{"type": "tool_result"}]}}
        assert _extract_snippet(obj) is None

    def test_content_with_leading_whitespace(self):
        obj = {"message": {"content": "  hello  "}}
        assert _extract_snippet(obj) == "hello"


# ===================================================================
# _scan_conversation
# ===================================================================

class TestScanConversation:
    def test_timestamps_extracted(self, make_conversation):
        lines = [
            _system_line("2024-01-01T00:00:00Z"),
            _user_line("hi", timestamp="2024-01-01T00:01:00Z"),
            _assistant_line("hello", timestamp="2024-01-01T00:02:00Z"),
        ]
        path = make_conversation("proj", "conv-uuid-1", lines)
        info = _scan_conversation(path)
        assert info.first_timestamp == "2024-01-01T00:00:00Z"
        assert info.last_timestamp == "2024-01-01T00:02:00Z"

    def test_slug_extracted(self, make_conversation):
        lines = [
            _user_line("hi", timestamp="2024-01-01T00:00:00Z"),
            json.dumps({"type": "system", "slug": "my-slug", "timestamp": "2024-01-01T00:00:01Z"}),
        ]
        path = make_conversation("proj", "conv-uuid-2", lines)
        info = _scan_conversation(path)
        assert info.slug == "my-slug"

    def test_user_turn_count(self, make_conversation):
        lines = [
            _user_line("q1", timestamp="2024-01-01T00:00:00Z"),
            _assistant_line("a1", timestamp="2024-01-01T00:00:01Z"),
            _user_line("q2", timestamp="2024-01-01T00:00:02Z"),
            _assistant_line("a2", timestamp="2024-01-01T00:00:03Z"),
            _tool_result_user_line(timestamp="2024-01-01T00:00:04Z"),
        ]
        path = make_conversation("proj", "conv-uuid-3", lines)
        info = _scan_conversation(path)
        assert info.turn_count == 2  # tool result user lines excluded

    def test_agent_count(self, make_conversation):
        lines = [
            _user_line("do it", timestamp="2024-01-01T00:00:00Z"),
            _assistant_line("delegating", timestamp="2024-01-01T00:00:01Z",
                            tool_uses=[_agent_tool_use()]),
            _assistant_line("reading", timestamp="2024-01-01T00:00:02Z",
                            tool_uses=[_other_tool_use()]),
            _assistant_line("delegating again", timestamp="2024-01-01T00:00:03Z",
                            tool_uses=[_agent_tool_use()]),
        ]
        path = make_conversation("proj", "conv-uuid-4", lines)
        info = _scan_conversation(path)
        assert info.agent_count == 2

    def test_snippet_from_first_user_message(self, make_conversation):
        lines = [
            _system_line("2024-01-01T00:00:00Z"),
            _user_line("First question here", timestamp="2024-01-01T00:00:01Z"),
            _user_line("Second question", timestamp="2024-01-01T00:00:02Z"),
        ]
        path = make_conversation("proj", "conv-uuid-5", lines)
        info = _scan_conversation(path)
        assert info.snippet == "First question here"

    def test_more_than_100_lines(self, make_conversation):
        """Metadata from first 100 lines; counts from all lines.

        Note: the implementation reads line i=100 via enumerate but breaks
        before processing it, so that line is consumed from the iterator
        but not counted. The second loop picks up from line 101 onward.
        """
        lines = []
        # First 100 lines (indices 0-99): 50 user + 50 assistant
        for i in range(50):
            lines.append(_user_line(f"q{i}", timestamp=f"2024-01-01T00:{i:02d}:00Z"))
            lines.append(_assistant_line(f"a{i}", timestamp=f"2024-01-01T00:{i:02d}:01Z"))
        # Lines 100+: 11 more user turns (index 100 is consumed but not
        # processed by the first loop, so only 10 of these 11 are counted)
        for i in range(50, 61):
            lines.append(_user_line(f"q{i}", timestamp=f"2024-01-01T01:{i:02d}:00Z"))

        path = make_conversation("proj", "conv-uuid-6", lines)
        info = _scan_conversation(path)
        # 50 from first loop + 10 from second loop (line 100 is dropped)
        assert info.turn_count == 60
        assert info.first_timestamp == "2024-01-01T00:00:00Z"
        # last_timestamp should be from the final lines
        assert info.last_timestamp is not None
        assert "01:" in info.last_timestamp  # from the second batch

    def test_large_file_tail_optimization(self, make_conversation):
        """Files > 4096 bytes use tail read for last_timestamp."""
        lines = []
        # Generate enough data to exceed 4096 bytes
        for i in range(80):
            lines.append(_user_line(
                f"Question number {i} with some padding text to make lines longer xxxxxxxxxxxxxxxxxxxx",
                timestamp=f"2024-01-{(i % 28) + 1:02d}T00:00:00Z",
            ))
        final_ts = "2024-12-31T23:59:59Z"
        lines.append(_user_line("final question", timestamp=final_ts))

        path = make_conversation("proj", "conv-uuid-7", lines)
        # Verify file is large enough
        assert path.stat().st_size > 4096
        info = _scan_conversation(path)
        assert info.last_timestamp == final_ts

    def test_malformed_lines_skipped(self, make_conversation):
        lines = [
            _user_line("hello", timestamp="2024-01-01T00:00:00Z"),
            "{bad json",
            "",
            _user_line("world", timestamp="2024-01-01T00:01:00Z"),
        ]
        path = make_conversation("proj", "conv-uuid-8", lines)
        info = _scan_conversation(path)
        assert info.turn_count == 2
        assert info.first_timestamp == "2024-01-01T00:00:00Z"

    def test_empty_file(self, make_conversation):
        path = make_conversation("proj", "conv-uuid-9", [])
        # The factory writes a trailing newline, so file has 1 byte.
        # Rewrite as truly empty.
        path.write_text("", encoding="utf-8")
        info = _scan_conversation(path)
        assert info.turn_count == 0
        assert info.agent_count == 0
        assert info.first_timestamp is None
        assert info.last_timestamp is None
        assert info.snippet is None
        assert info.slug is None

    def test_uuid_and_project_key(self, make_conversation):
        path = make_conversation("my-proj", "abcd-1234", [_system_line("2024-01-01T00:00:00Z")])
        info = _scan_conversation(path)
        assert info.uuid == "abcd-1234"
        assert info.project_key == "my-proj"
        assert info.path == path


# ===================================================================
# discover_conversations
# ===================================================================

class TestDiscoverConversations:
    def test_empty_projects_dir(self, projects_dir):
        assert discover_conversations() == []

    def test_projects_dir_does_not_exist(self, tmp_path, monkeypatch):
        monkeypatch.setattr("cchat.store.PROJECTS_DIR", tmp_path / "nonexistent")
        assert discover_conversations() == []

    def test_single_project_one_conversation(self, make_conversation):
        make_conversation("proj1", "uuid-aaa", [
            _user_line("hi", timestamp="2024-01-01T00:00:00Z"),
        ])
        results = discover_conversations()
        assert len(results) == 1
        assert results[0].uuid == "uuid-aaa"
        assert results[0].project_key == "proj1"

    def test_multiple_projects_multiple_conversations(self, make_conversation):
        make_conversation("proj1", "uuid-aaa", [_user_line("hi")])
        make_conversation("proj1", "uuid-bbb", [_user_line("hey")])
        make_conversation("proj2", "uuid-ccc", [_user_line("hello")])
        results = discover_conversations()
        uuids = {r.uuid for r in results}
        assert uuids == {"uuid-aaa", "uuid-bbb", "uuid-ccc"}

    def test_subagent_files_excluded(self, projects_dir):
        proj = projects_dir / "proj1"
        proj.mkdir()
        # Main conversation
        main = proj / "uuid-main.jsonl"
        main.write_text(_user_line("hi") + "\n", encoding="utf-8")
        # Subagent file inside uuid-main/subagents/
        sub_dir = proj / "uuid-main" / "subagents"
        sub_dir.mkdir(parents=True)
        sub_file = sub_dir / "agent-001.jsonl"
        sub_file.write_text(_user_line("sub") + "\n", encoding="utf-8")

        results = discover_conversations()
        assert len(results) == 1
        assert results[0].uuid == "uuid-main"

    def test_project_key_filter(self, make_conversation):
        make_conversation("proj1", "uuid-aaa", [_user_line("hi")])
        make_conversation("proj2", "uuid-bbb", [_user_line("hey")])
        results = discover_conversations(project_key="proj1")
        assert len(results) == 1
        assert results[0].uuid == "uuid-aaa"

    def test_project_key_nonexistent(self, projects_dir):
        results = discover_conversations(project_key="no-such-project")
        assert results == []


# ===================================================================
# resolve_conversation
# ===================================================================

class TestResolveConversation:
    def test_direct_file_path(self, make_conversation):
        path = make_conversation("proj1", "uuid-aaa", [_user_line("hi")])
        result = resolve_conversation(str(path))
        assert result == path

    def test_exact_uuid_match(self, make_conversation):
        path = make_conversation("proj1", "uuid-exact-match", [_user_line("hi")])
        result = resolve_conversation("uuid-exact-match")
        assert result == path

    def test_uuid_prefix_4_chars(self, make_conversation):
        path = make_conversation("proj1", "abcdef-1234-5678", [_user_line("hi")])
        result = resolve_conversation("abcd")
        assert result == path

    def test_slug_match(self, make_conversation):
        lines = [
            json.dumps({"type": "system", "slug": "my-cool-slug", "timestamp": "2024-01-01T00:00:00Z"}),
            _user_line("hi", timestamp="2024-01-01T00:01:00Z"),
        ]
        path = make_conversation("proj1", "uuid-for-slug", lines)
        result = resolve_conversation("my-cool-slug")
        assert result == path

    def test_nonexistent_identifier_raises(self, projects_dir):
        with pytest.raises(SystemExit, match="Could not resolve"):
            resolve_conversation("nonexistent")

    def test_ambiguous_uuid_prefix_raises(self, make_conversation):
        make_conversation("proj1", "abcd-1111", [_user_line("hi")])
        make_conversation("proj1", "abcd-2222", [_user_line("hi")])
        with pytest.raises(SystemExit, match="matches multiple"):
            resolve_conversation("abcd")

    def test_uuid_in_multiple_projects_no_key_raises(self, make_conversation):
        make_conversation("proj1", "shared-uuid", [_user_line("hi")])
        make_conversation("proj2", "shared-uuid", [_user_line("hi")])
        with pytest.raises(SystemExit, match="multiple projects"):
            resolve_conversation("shared-uuid")

    def test_uuid_in_multiple_projects_with_key(self, make_conversation):
        make_conversation("proj1", "shared-uuid", [_user_line("hi")])
        path2 = make_conversation("proj2", "shared-uuid", [_user_line("hi")])
        result = resolve_conversation("shared-uuid", project_key="proj2")
        assert result == path2

    def test_short_prefix_falls_through_to_slug(self, make_conversation):
        """A 3-char identifier that doesn't match exact UUID should try slug."""
        lines = [
            json.dumps({"type": "system", "slug": "abc", "timestamp": "2024-01-01T00:00:00Z"}),
        ]
        path = make_conversation("proj1", "xyz-full-uuid", lines)
        result = resolve_conversation("abc")
        assert result == path

    def test_projects_dir_does_not_exist_raises(self, tmp_path, monkeypatch):
        monkeypatch.setattr("cchat.store.PROJECTS_DIR", tmp_path / "nonexistent")
        with pytest.raises(SystemExit, match="does not exist"):
            resolve_conversation("anything")


# ===================================================================
# list_projects
# ===================================================================

class TestListProjects:
    def test_empty_projects_dir(self, projects_dir):
        assert list_projects() == []

    def test_projects_dir_does_not_exist(self, tmp_path, monkeypatch):
        monkeypatch.setattr("cchat.store.PROJECTS_DIR", tmp_path / "nonexistent")
        assert list_projects() == []

    def test_multiple_project_dirs_sorted(self, projects_dir):
        (projects_dir / "zebra").mkdir()
        (projects_dir / "alpha").mkdir()
        (projects_dir / "middle").mkdir()
        assert list_projects() == ["alpha", "middle", "zebra"]

    def test_files_in_projects_dir_excluded(self, projects_dir):
        (projects_dir / "real-project").mkdir()
        (projects_dir / "not-a-dir.txt").write_text("nope", encoding="utf-8")
        result = list_projects()
        assert result == ["real-project"]


# ===================================================================
# get_subagent_paths
# ===================================================================

class TestGetSubagentPaths:
    def test_no_subagent_dir(self, tmp_path):
        conv = tmp_path / "my-uuid.jsonl"
        conv.write_text("", encoding="utf-8")
        assert get_subagent_paths(conv) == []

    def test_subagent_dir_with_matching_files(self, tmp_path):
        conv = tmp_path / "my-uuid.jsonl"
        conv.write_text("", encoding="utf-8")
        sub_dir = tmp_path / "my-uuid" / "subagents"
        sub_dir.mkdir(parents=True)
        (sub_dir / "agent-001.jsonl").write_text("{}\n", encoding="utf-8")
        (sub_dir / "agent-002.jsonl").write_text("{}\n", encoding="utf-8")

        result = get_subagent_paths(conv)
        assert len(result) == 2
        assert result[0].name == "agent-001.jsonl"
        assert result[1].name == "agent-002.jsonl"

    def test_subagent_dir_non_matching_files_excluded(self, tmp_path):
        conv = tmp_path / "my-uuid.jsonl"
        conv.write_text("", encoding="utf-8")
        sub_dir = tmp_path / "my-uuid" / "subagents"
        sub_dir.mkdir(parents=True)
        (sub_dir / "agent-001.jsonl").write_text("{}\n", encoding="utf-8")
        (sub_dir / "notes.txt").write_text("not an agent", encoding="utf-8")
        (sub_dir / "summary.jsonl").write_text("{}\n", encoding="utf-8")

        result = get_subagent_paths(conv)
        assert len(result) == 1
        assert result[0].name == "agent-001.jsonl"

    def test_subagent_paths_are_sorted(self, tmp_path):
        conv = tmp_path / "my-uuid.jsonl"
        conv.write_text("", encoding="utf-8")
        sub_dir = tmp_path / "my-uuid" / "subagents"
        sub_dir.mkdir(parents=True)
        (sub_dir / "agent-003.jsonl").write_text("{}\n", encoding="utf-8")
        (sub_dir / "agent-001.jsonl").write_text("{}\n", encoding="utf-8")
        (sub_dir / "agent-002.jsonl").write_text("{}\n", encoding="utf-8")

        result = get_subagent_paths(conv)
        names = [p.name for p in result]
        assert names == ["agent-001.jsonl", "agent-002.jsonl", "agent-003.jsonl"]
