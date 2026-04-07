"""Extensive tests for cchat.parser module."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import pytest

from cchat.parser import (
    deduplicate_assistant_lines,
    extract_content_summary,
    extract_file_modifications,
    extract_timestamp,
    extract_usage,
    parse_lines,
)

ELLIPSIS = "\u2026"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def write_jsonl(tmp_path: Path, lines, filename: str = "test.jsonl") -> Path:
    """Write a list of items to a JSONL file. Items can be dicts (serialized
    as JSON) or raw strings (written verbatim, useful for malformed lines)."""
    p = tmp_path / filename
    with open(p, "w", encoding="utf-8") as fh:
        for item in lines:
            if isinstance(item, str):
                fh.write(item + "\n")
            else:
                fh.write(json.dumps(item) + "\n")
    return p


def make_assistant(msg_id: Optional[str] = None,
                   stop_reason: Optional[str] = None,
                   content: Optional[list] = None,
                   usage: Optional[dict] = None,
                   extra_msg: Optional[dict] = None) -> dict:
    """Build a typical assistant line dict."""
    msg = {}
    if msg_id is not None:
        msg["id"] = msg_id
    if stop_reason is not None:
        msg["stop_reason"] = stop_reason
    if content is not None:
        msg["content"] = content
    if usage is not None:
        msg["usage"] = usage
    if extra_msg:
        msg.update(extra_msg)
    return {"type": "assistant", "message": msg}


def make_user(content=None) -> dict:
    """Build a typical user line dict."""
    d = {"type": "user"}
    if content is not None:
        d["message"] = {"content": content}
    return d


# ===========================================================================
# parse_lines
# ===========================================================================

class TestParseLines:
    def test_normal_multiline(self, tmp_path):
        data = [{"a": 1}, {"b": 2}, {"c": 3}]
        p = write_jsonl(tmp_path, data)
        result = list(parse_lines(p))
        assert result == [(1, {"a": 1}), (2, {"b": 2}), (3, {"c": 3})]

    def test_line_numbers_are_1_indexed(self, tmp_path):
        p = write_jsonl(tmp_path, [{"x": 10}])
        result = list(parse_lines(p))
        assert result[0][0] == 1

    def test_empty_lines_skipped_but_numbers_increment(self, tmp_path):
        """Blank lines between valid JSON still count for line numbering."""
        p = tmp_path / "test.jsonl"
        with open(p, "w", encoding="utf-8") as fh:
            fh.write('{"a":1}\n')
            fh.write("\n")
            fh.write('{"b":2}\n')
        result = list(parse_lines(p))
        assert result == [(1, {"a": 1}), (3, {"b": 2})]

    def test_malformed_json_skipped(self, tmp_path):
        lines = ['{"valid": true}', "not json at all", '{"also": "valid"}']
        p = write_jsonl(tmp_path, lines)
        result = list(parse_lines(p))
        assert len(result) == 2
        assert result[0] == (1, {"valid": True})
        assert result[1] == (3, {"also": "valid"})

    def test_empty_file(self, tmp_path):
        p = tmp_path / "empty.jsonl"
        p.write_text("", encoding="utf-8")
        assert list(parse_lines(p)) == []

    def test_only_blank_lines(self, tmp_path):
        p = tmp_path / "blanks.jsonl"
        p.write_text("\n\n\n\n", encoding="utf-8")
        assert list(parse_lines(p)) == []

    def test_mixed_valid_invalid_blank(self, tmp_path):
        raw_lines = [
            '{"first": 1}',   # line 1 - valid
            "",                # line 2 - blank
            "{bad json}",     # line 3 - malformed
            '{"fourth": 4}',  # line 4 - valid
            "",                # line 5 - blank
            '{"sixth": 6}',   # line 6 - valid
        ]
        p = tmp_path / "mixed.jsonl"
        p.write_text("\n".join(raw_lines) + "\n", encoding="utf-8")
        result = list(parse_lines(p))
        assert result == [
            (1, {"first": 1}),
            (4, {"fourth": 4}),
            (6, {"sixth": 6}),
        ]

    def test_whitespace_only_lines_skipped(self, tmp_path):
        p = tmp_path / "ws.jsonl"
        p.write_text('  \n\t\n{"ok":1}\n', encoding="utf-8")
        result = list(parse_lines(p))
        assert result == [(3, {"ok": 1})]

    def test_returns_iterator_not_list(self, tmp_path):
        p = write_jsonl(tmp_path, [{"a": 1}])
        result = parse_lines(p)
        # Should be an iterator/generator, not a list
        assert hasattr(result, "__next__")


# ===========================================================================
# deduplicate_assistant_lines
# ===========================================================================

class TestDeduplicateAssistantLines:
    def test_non_assistant_pass_through(self):
        lines = [(1, {"type": "user"}), (2, {"type": "system"})]
        result = list(deduplicate_assistant_lines(iter(lines)))
        assert result == lines

    def test_single_assistant_no_dups(self):
        a = make_assistant(msg_id="a1", stop_reason="end_turn")
        lines = [(1, a)]
        result = list(deduplicate_assistant_lines(iter(lines)))
        assert result == [(1, a)]

    def test_multiple_same_id_yields_last(self):
        a1 = make_assistant(msg_id="a1", content=[{"type": "text", "text": "chunk1"}])
        a2 = make_assistant(msg_id="a1", content=[{"type": "text", "text": "chunk2"}])
        a3 = make_assistant(msg_id="a1", stop_reason="end_turn",
                            content=[{"type": "text", "text": "full"}])
        lines = [(1, a1), (2, a2), (3, a3)]
        result = list(deduplicate_assistant_lines(iter(lines)))
        assert len(result) == 1
        assert result[0] == (3, a3)

    def test_last_with_stop_reason_yielded(self):
        a1 = make_assistant(msg_id="x")
        a2 = make_assistant(msg_id="x", stop_reason="end_turn")
        lines = [(1, a1), (2, a2)]
        result = list(deduplicate_assistant_lines(iter(lines)))
        assert result == [(2, a2)]

    def test_interleaved_user_assistant_ordering(self):
        """user -> assistant(A, chunk1) -> assistant(A, chunk2) -> user -> assistant(B)"""
        u1 = {"type": "user", "message": {"content": "hello"}}
        a_chunk1 = make_assistant(msg_id="A", content=[{"type": "text", "text": "p1"}])
        a_chunk2 = make_assistant(msg_id="A", stop_reason="end_turn",
                                  content=[{"type": "text", "text": "p1p2"}])
        u2 = {"type": "user", "message": {"content": "next"}}
        a_b = make_assistant(msg_id="B", stop_reason="end_turn",
                             content=[{"type": "text", "text": "resp"}])

        lines = [(1, u1), (2, a_chunk1), (3, a_chunk2), (4, u2), (5, a_b)]
        result = list(deduplicate_assistant_lines(iter(lines)))

        assert len(result) == 4
        assert result[0] == (1, u1)
        assert result[1] == (3, a_chunk2)  # flushed A (last chunk) before u2
        assert result[2] == (4, u2)
        assert result[3] == (5, a_b)       # flushed B at end

    def test_assistant_no_message_id_passes_through(self):
        a = make_assistant(msg_id=None)
        lines = [(1, a)]
        result = list(deduplicate_assistant_lines(iter(lines)))
        assert result == [(1, a)]

    def test_assistant_no_message_at_all_passes_through(self):
        a = {"type": "assistant"}  # no message key
        lines = [(1, a)]
        result = list(deduplicate_assistant_lines(iter(lines)))
        assert result == [(1, a)]

    def test_buffer_flushes_at_end(self):
        a1 = make_assistant(msg_id="z", content=[{"type": "text", "text": "c1"}])
        a2 = make_assistant(msg_id="z", content=[{"type": "text", "text": "c2"}])
        lines = [(1, a1), (2, a2)]
        result = list(deduplicate_assistant_lines(iter(lines)))
        assert result == [(2, a2)]

    def test_empty_input(self):
        assert list(deduplicate_assistant_lines(iter([]))) == []

    def test_non_assistant_then_dup_assistant_then_non_assistant(self):
        u1 = {"type": "user", "message": {"content": "q"}}
        a1 = make_assistant(msg_id="m1", content=[{"type": "text", "text": "part"}])
        a2 = make_assistant(msg_id="m1", stop_reason="end_turn",
                            content=[{"type": "text", "text": "full"}])
        s = {"type": "system", "subtype": "init"}

        lines = [(1, u1), (2, a1), (3, a2), (4, s)]
        result = list(deduplicate_assistant_lines(iter(lines)))
        assert result == [(1, u1), (3, a2), (4, s)]

    def test_multiple_different_ids_sequential(self):
        """Two different assistant messages back to back (no user between)."""
        a1 = make_assistant(msg_id="id1", stop_reason="end_turn")
        a2 = make_assistant(msg_id="id2", stop_reason="end_turn")
        lines = [(1, a1), (2, a2)]
        result = list(deduplicate_assistant_lines(iter(lines)))
        assert len(result) == 2
        assert result[0] == (1, a1)
        assert result[1] == (2, a2)

    def test_two_ids_interleaved_chunks(self):
        """id1 chunk, id1 chunk, id2 chunk, id2 chunk -> yields last of each."""
        a1a = make_assistant(msg_id="id1", content=[{"type": "text", "text": "1a"}])
        a1b = make_assistant(msg_id="id1", content=[{"type": "text", "text": "1b"}])
        a2a = make_assistant(msg_id="id2", content=[{"type": "text", "text": "2a"}])
        a2b = make_assistant(msg_id="id2", content=[{"type": "text", "text": "2b"}])
        lines = [(1, a1a), (2, a1b), (3, a2a), (4, a2b)]
        result = list(deduplicate_assistant_lines(iter(lines)))
        assert result == [(2, a1b), (4, a2b)]


# ===========================================================================
# extract_timestamp
# ===========================================================================

class TestExtractTimestamp:
    def test_string_timestamp(self):
        assert extract_timestamp({"timestamp": "2024-01-15T10:30:00Z"}) == "2024-01-15T10:30:00Z"

    def test_int_timestamp(self):
        assert extract_timestamp({"timestamp": 1700000000}) == "1700000000"

    def test_no_timestamp(self):
        assert extract_timestamp({"type": "user"}) is None

    def test_timestamp_none(self):
        assert extract_timestamp({"timestamp": None}) is None

    def test_empty_dict(self):
        assert extract_timestamp({}) is None

    def test_float_timestamp(self):
        assert extract_timestamp({"timestamp": 1700000000.123}) == "1700000000.123"

    def test_empty_string_timestamp(self):
        # Empty string is falsy but not None
        assert extract_timestamp({"timestamp": ""}) == ""


# ===========================================================================
# extract_content_summary
# ===========================================================================

class TestExtractContentSummary:
    # -- user type --

    def test_user_string_content_short(self):
        line = make_user("Hello world")
        assert extract_content_summary(line) == "Hello world"

    def test_user_string_content_with_newlines(self):
        line = make_user("line1\nline2\nline3")
        assert extract_content_summary(line) == "line1 line2 line3"

    def test_user_string_content_truncated(self):
        long_text = "a" * 100
        line = make_user(long_text)
        result = extract_content_summary(line)
        assert len(result) == 80
        assert result.endswith(ELLIPSIS)
        assert result == "a" * 79 + ELLIPSIS

    def test_user_string_content_exact_max_len(self):
        text = "x" * 80
        line = make_user(text)
        assert extract_content_summary(line) == text

    def test_user_string_content_one_over_max_len(self):
        text = "x" * 81
        line = make_user(text)
        result = extract_content_summary(line)
        assert result == "x" * 79 + ELLIPSIS

    def test_user_list_content_tool_result(self):
        line = make_user([{"type": "tool_result", "content": "data"}])
        assert extract_content_summary(line) == "[tool_result]"

    def test_user_empty_list_content(self):
        line = make_user([])
        assert extract_content_summary(line) == "[tool_result]"

    def test_user_list_without_tool_result(self):
        line = make_user([{"type": "text", "text": "hi"}])
        # list content without tool_result items still returns [tool_result]
        assert extract_content_summary(line) == "[tool_result]"

    def test_user_no_message(self):
        line = {"type": "user"}
        assert extract_content_summary(line) == "user"

    def test_user_message_no_content(self):
        line = {"type": "user", "message": {}}
        assert extract_content_summary(line) == "user"

    def test_user_message_none(self):
        line = {"type": "user", "message": None}
        assert extract_content_summary(line) == "user"

    def test_user_custom_max_len(self):
        line = make_user("abcdefghij")
        assert extract_content_summary(line, max_len=5) == "abcd" + ELLIPSIS

    def test_user_custom_max_len_no_truncation(self):
        line = make_user("abc")
        assert extract_content_summary(line, max_len=5) == "abc"

    # -- assistant type --

    def test_assistant_text_and_tool_use(self):
        line = make_assistant(content=[
            {"type": "text", "text": "Let me check"},
            {"type": "tool_use", "name": "Read"},
        ])
        result = extract_content_summary(line)
        assert result == "Let me check [Read]"

    def test_assistant_text_only(self):
        line = make_assistant(content=[
            {"type": "text", "text": "Just text"},
        ])
        assert extract_content_summary(line) == "Just text"

    def test_assistant_tool_use_only(self):
        line = make_assistant(content=[
            {"type": "tool_use", "name": "Bash"},
        ])
        assert extract_content_summary(line) == "[Bash]"

    def test_assistant_multiple_tool_use(self):
        line = make_assistant(content=[
            {"type": "tool_use", "name": "Read"},
            {"type": "tool_use", "name": "Edit"},
        ])
        assert extract_content_summary(line) == "[Read, Edit]"

    def test_assistant_text_and_multiple_tools(self):
        line = make_assistant(content=[
            {"type": "text", "text": "Working on it"},
            {"type": "tool_use", "name": "Grep"},
            {"type": "tool_use", "name": "Write"},
        ])
        assert extract_content_summary(line) == "Working on it [Grep, Write]"

    def test_assistant_no_content_list(self):
        line = make_assistant()
        assert extract_content_summary(line) == "assistant"

    def test_assistant_content_not_list(self):
        line = {"type": "assistant", "message": {"content": "string instead"}}
        assert extract_content_summary(line) == "assistant"

    def test_assistant_empty_content_list(self):
        line = make_assistant(content=[])
        assert extract_content_summary(line) == "assistant"

    def test_assistant_content_with_non_dict_items(self):
        line = make_assistant(content=["not a dict", 42])
        assert extract_content_summary(line) == "assistant"

    def test_assistant_text_with_newlines(self):
        line = make_assistant(content=[
            {"type": "text", "text": "line1\nline2"},
        ])
        assert extract_content_summary(line) == "line1 line2"

    def test_assistant_long_text_truncated(self):
        long_text = "b" * 100
        line = make_assistant(content=[
            {"type": "text", "text": long_text},
        ])
        result = extract_content_summary(line)
        assert result == "b" * 79 + ELLIPSIS

    def test_assistant_only_second_text_ignored(self):
        """Only the first text block is used."""
        line = make_assistant(content=[
            {"type": "text", "text": "first"},
            {"type": "text", "text": "second"},
        ])
        assert extract_content_summary(line) == "first"

    def test_assistant_tool_use_missing_name(self):
        line = make_assistant(content=[
            {"type": "tool_use"},
        ])
        assert extract_content_summary(line) == "[tool]"

    # -- system type --

    def test_system_with_subtype(self):
        line = {"type": "system", "subtype": "init"}
        assert extract_content_summary(line) == "init"

    def test_system_without_subtype(self):
        line = {"type": "system"}
        assert extract_content_summary(line) == "system"

    def test_system_subtype_none(self):
        line = {"type": "system", "subtype": None}
        assert extract_content_summary(line) == "system"

    def test_system_subtype_empty_string(self):
        line = {"type": "system", "subtype": ""}
        assert extract_content_summary(line) == "system"

    # -- progress type --

    def test_progress(self):
        assert extract_content_summary({"type": "progress"}) == "agent progress"

    # -- unknown / other types --

    def test_unknown_type(self):
        assert extract_content_summary({"type": "some_custom_type"}) == "some_custom_type"

    def test_empty_dict(self):
        assert extract_content_summary({}) == "unknown"

    def test_no_type_key(self):
        assert extract_content_summary({"message": "stuff"}) == "unknown"

    def test_type_empty_string(self):
        assert extract_content_summary({"type": ""}) == "unknown"

    def test_custom_max_len_on_assistant(self):
        line = make_assistant(content=[
            {"type": "text", "text": "abcdefghij"},
        ])
        result = extract_content_summary(line, max_len=5)
        assert result == "abcd" + ELLIPSIS


# ===========================================================================
# extract_usage
# ===========================================================================

class TestExtractUsage:
    def test_full_usage(self):
        line = make_assistant(usage={
            "input_tokens": 100,
            "output_tokens": 200,
            "cache_read_input_tokens": 50,
            "cache_creation_input_tokens": 10,
        })
        result = extract_usage(line)
        assert result == {
            "input_tokens": 100,
            "output_tokens": 200,
            "cache_read_input_tokens": 50,
            "cache_creation_input_tokens": 10,
        }

    def test_missing_optional_fields_default_zero(self):
        line = make_assistant(usage={"input_tokens": 100})
        result = extract_usage(line)
        assert result is not None
        assert result["input_tokens"] == 100
        assert result["output_tokens"] == 0
        assert result["cache_read_input_tokens"] == 0
        assert result["cache_creation_input_tokens"] == 0

    def test_empty_usage_dict(self):
        # Empty dict is falsy, so returns None
        line = make_assistant(usage={})
        assert extract_usage(line) is None

    def test_no_message(self):
        assert extract_usage({}) is None

    def test_message_but_no_usage(self):
        line = make_assistant()
        assert extract_usage(line) is None

    def test_usage_none(self):
        line = {"type": "assistant", "message": {"usage": None}}
        assert extract_usage(line) is None

    def test_usage_non_dict(self):
        line = {"type": "assistant", "message": {"usage": "not a dict"}}
        assert extract_usage(line) is None

    def test_usage_as_list(self):
        line = {"type": "assistant", "message": {"usage": [1, 2, 3]}}
        assert extract_usage(line) is None

    def test_non_assistant_with_usage(self):
        """extract_usage doesn't check type -- it checks message.usage."""
        line = {"type": "user", "message": {"usage": {"input_tokens": 5}}}
        result = extract_usage(line)
        assert result is not None
        assert result["input_tokens"] == 5

    def test_message_none(self):
        line = {"type": "assistant", "message": None}
        assert extract_usage(line) is None

    def test_usage_zero_values(self):
        line = make_assistant(usage={
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
        })
        # All zeros but dict is truthy (has keys)
        result = extract_usage(line)
        assert result is not None
        assert all(v == 0 for v in result.values())


# ===========================================================================
# extract_file_modifications
# ===========================================================================

class TestExtractFileModifications:
    def test_non_assistant_returns_none(self):
        line = {"type": "user"}
        assert extract_file_modifications(line) is None

    def test_write_tool(self):
        line = make_assistant(content=[
            {"type": "tool_use", "name": "Write",
             "input": {"file_path": "/tmp/foo.py"}},
        ])
        result = extract_file_modifications(line)
        assert result == [{"tool": "Write", "file_path": "/tmp/foo.py"}]

    def test_edit_tool(self):
        line = make_assistant(content=[
            {"type": "tool_use", "name": "Edit",
             "input": {"file_path": "/tmp/bar.py", "old_string": "a", "new_string": "b"}},
        ])
        result = extract_file_modifications(line)
        assert result == [{"tool": "Edit", "file_path": "/tmp/bar.py"}]

    def test_both_write_and_edit(self):
        line = make_assistant(content=[
            {"type": "tool_use", "name": "Write",
             "input": {"file_path": "/a.py"}},
            {"type": "tool_use", "name": "Edit",
             "input": {"file_path": "/b.py"}},
        ])
        result = extract_file_modifications(line)
        assert result == [
            {"tool": "Write", "file_path": "/a.py"},
            {"tool": "Edit", "file_path": "/b.py"},
        ]

    def test_non_write_edit_tools_ignored(self):
        line = make_assistant(content=[
            {"type": "tool_use", "name": "Read",
             "input": {"file_path": "/c.py"}},
            {"type": "tool_use", "name": "Bash",
             "input": {"command": "ls"}},
        ])
        assert extract_file_modifications(line) is None

    def test_tool_use_missing_file_path(self):
        line = make_assistant(content=[
            {"type": "tool_use", "name": "Write", "input": {}},
        ])
        assert extract_file_modifications(line) is None

    def test_tool_use_file_path_none(self):
        line = make_assistant(content=[
            {"type": "tool_use", "name": "Write",
             "input": {"file_path": None}},
        ])
        assert extract_file_modifications(line) is None

    def test_tool_use_file_path_empty_string(self):
        line = make_assistant(content=[
            {"type": "tool_use", "name": "Write",
             "input": {"file_path": ""}},
        ])
        assert extract_file_modifications(line) is None

    def test_no_content(self):
        line = make_assistant()
        assert extract_file_modifications(line) is None

    def test_content_not_list(self):
        line = {"type": "assistant", "message": {"content": "string"}}
        assert extract_file_modifications(line) is None

    def test_multiple_writes_different_files(self):
        line = make_assistant(content=[
            {"type": "tool_use", "name": "Write",
             "input": {"file_path": "/x.py"}},
            {"type": "tool_use", "name": "Write",
             "input": {"file_path": "/y.py"}},
            {"type": "tool_use", "name": "Write",
             "input": {"file_path": "/z.py"}},
        ])
        result = extract_file_modifications(line)
        assert len(result) == 3
        paths = [r["file_path"] for r in result]
        assert paths == ["/x.py", "/y.py", "/z.py"]

    def test_mixed_tool_types(self):
        """Write + Read + Edit + Bash -> only Write and Edit returned."""
        line = make_assistant(content=[
            {"type": "tool_use", "name": "Read",
             "input": {"file_path": "/r.py"}},
            {"type": "tool_use", "name": "Write",
             "input": {"file_path": "/w.py"}},
            {"type": "tool_use", "name": "Bash",
             "input": {"command": "echo hi"}},
            {"type": "tool_use", "name": "Edit",
             "input": {"file_path": "/e.py"}},
        ])
        result = extract_file_modifications(line)
        assert result == [
            {"tool": "Write", "file_path": "/w.py"},
            {"tool": "Edit", "file_path": "/e.py"},
        ]

    def test_no_message(self):
        line = {"type": "assistant"}
        assert extract_file_modifications(line) is None

    def test_message_none(self):
        line = {"type": "assistant", "message": None}
        assert extract_file_modifications(line) is None

    def test_content_with_non_dict_items(self):
        line = make_assistant(content=["string_item", 42,
                                       {"type": "tool_use", "name": "Write",
                                        "input": {"file_path": "/ok.py"}}])
        result = extract_file_modifications(line)
        assert result == [{"tool": "Write", "file_path": "/ok.py"}]

    def test_tool_use_input_none(self):
        line = make_assistant(content=[
            {"type": "tool_use", "name": "Write", "input": None},
        ])
        assert extract_file_modifications(line) is None

    def test_tool_use_no_input_key(self):
        line = make_assistant(content=[
            {"type": "tool_use", "name": "Edit"},
        ])
        assert extract_file_modifications(line) is None

    def test_no_type_key(self):
        """Line without type key is not assistant."""
        assert extract_file_modifications({}) is None
        assert extract_file_modifications({"message": {}}) is None
