"""Tests for cchat.formatters module."""

from __future__ import annotations

import json
import sys

import pytest

import cchat.formatters as fmt
from cchat.formatters import (
    BLUE,
    BOLD,
    CYAN,
    DIM,
    GREEN,
    MAGENTA,
    RED,
    YELLOW,
    colored,
    format_json,
    format_size,
    format_table,
    format_timestamp,
    set_no_color,
    supports_color,
    truncate,
    truncate_middle,
)

ELLIPSIS = chr(8230)  # Unicode horizontal ellipsis U+2026
EM_DASH = chr(8212)  # Unicode em-dash U+2014


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeTTY:
    """A fake stdout that reports isatty() = True."""

    def isatty(self):
        return True

    def write(self, *a, **kw):
        pass

    def flush(self):
        pass


class _FakeNonTTY:
    """A fake stdout that reports isatty() = False."""

    def isatty(self):
        return False


class _FakeNoIsatty:
    """A fake stdout with no isatty attribute."""

    pass


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_no_color():
    """Reset the module-level _no_color flag before and after every test."""
    set_no_color(False)
    yield
    set_no_color(False)


@pytest.fixture()
def force_color_on(monkeypatch):
    """Patch supports_color to always return True.

    Use this for tests that need color output but are NOT testing
    supports_color itself (e.g. colored(), format_table bold header).
    """
    monkeypatch.setattr(fmt, "supports_color", lambda: True)


@pytest.fixture()
def force_color_off():
    """Force color off via set_no_color."""
    set_no_color(True)


@pytest.fixture()
def tty_env(monkeypatch):
    """Set up the environment so the real supports_color() returns True.

    Replaces sys.stdout within the formatters module's namespace with a
    fake TTY object that returns True for isatty().  We patch via a
    wrapper property on the module to survive pytest's capture system.
    """
    set_no_color(False)
    monkeypatch.delenv("NO_COLOR", raising=False)
    fake = _FakeTTY()
    # Patch the function's lookup path: fmt uses ``sys.stdout``, and
    # ``sys`` is the global ``sys`` module.  Pytest capture replaces
    # ``sys.stdout`` *after* fixture setup, so instead we make the
    # formatters module use our own ``sys``-like namespace.
    import types

    fake_sys = types.SimpleNamespace(stdout=fake)
    monkeypatch.setattr(fmt, "sys", fake_sys)


# ===========================================================================
# set_no_color / supports_color
# ===========================================================================


class TestSetNoColor:
    def test_set_true_disables_color(self):
        set_no_color(True)
        assert supports_color() is False

    def test_set_false_with_tty(self, tty_env):
        set_no_color(False)
        assert supports_color() is True

    def test_toggle(self, tty_env):
        set_no_color(True)
        assert supports_color() is False
        set_no_color(False)
        assert supports_color() is True


class TestSupportsColor:
    def test_no_color_env_disables(self, tty_env, monkeypatch):
        monkeypatch.setenv("NO_COLOR", "1")
        assert supports_color() is False

    def test_no_color_env_empty_string(self, tty_env, monkeypatch):
        # The spec says NO_COLOR set to *any* value disables color.
        monkeypatch.setenv("NO_COLOR", "")
        assert supports_color() is False

    def test_stdout_not_tty(self, monkeypatch):
        import types

        set_no_color(False)
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.setattr(fmt, "sys", types.SimpleNamespace(stdout=_FakeNonTTY()))
        assert supports_color() is False

    def test_stdout_no_isatty_attr(self, monkeypatch):
        import types

        set_no_color(False)
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.setattr(fmt, "sys", types.SimpleNamespace(stdout=_FakeNoIsatty()))
        assert supports_color() is False

    def test_all_good_returns_true(self, tty_env):
        assert supports_color() is True

    def test_no_color_flag_takes_priority_over_tty(self, tty_env):
        set_no_color(True)
        assert supports_color() is False


# ===========================================================================
# colored
# ===========================================================================


class TestColored:
    def test_with_color_supported(self, force_color_on):
        result = colored("hello", RED)
        assert result == "\033[31mhello\033[0m"

    def test_without_color(self, force_color_off):
        result = colored("hello", RED)
        assert result == "hello"

    @pytest.mark.parametrize(
        "code,expected_num",
        [
            (RED, 31),
            (GREEN, 32),
            (YELLOW, 33),
            (BLUE, 34),
            (MAGENTA, 35),
            (CYAN, 36),
            (BOLD, 1),
            (DIM, 2),
        ],
    )
    def test_all_codes(self, force_color_on, code, expected_num):
        result = colored("x", code)
        assert result == f"\033[{expected_num}mx\033[0m"

    def test_empty_string(self, force_color_on):
        result = colored("", RED)
        assert result == "\033[31m\033[0m"

    def test_plain_passthrough_when_no_color(self, force_color_off):
        for code in (RED, GREEN, YELLOW, BLUE, MAGENTA, CYAN, BOLD, DIM):
            assert colored("text", code) == "text"


# ===========================================================================
# truncate_middle
# ===========================================================================


class TestTruncateMiddle:
    def test_short_text_unchanged(self):
        assert truncate_middle("abc", 10) == "abc"

    def test_equal_length_unchanged(self):
        assert truncate_middle("abcde", 5) == "abcde"

    def test_longer_text_truncated(self):
        result = truncate_middle("abcdefghij", 5)
        # max_len=5, half=(5-1)//2=2, end_len=5-1-2=2
        assert result == "ab" + ELLIPSIS + "ij"

    def test_max_len_0(self):
        assert truncate_middle("hello", 0) == ""

    def test_max_len_1(self):
        assert truncate_middle("hello", 1) == "h"

    def test_max_len_2(self):
        assert truncate_middle("hello", 2) == "he"

    def test_max_len_3(self):
        # half=(3-1)//2=1, end_len=3-1-1=1
        result = truncate_middle("hello", 3)
        assert result == "h" + ELLIPSIS + "o"

    def test_empty_string(self):
        assert truncate_middle("", 5) == ""

    def test_empty_string_zero_max(self):
        assert truncate_middle("", 0) == ""

    def test_odd_max_len(self):
        result = truncate_middle("abcdefg", 5)
        assert result == "ab" + ELLIPSIS + "fg"
        assert len(result) == 5

    def test_even_max_len(self):
        result = truncate_middle("abcdefgh", 6)
        assert result == "ab" + ELLIPSIS + "fgh"
        assert len(result) == 6

    def test_long_text(self):
        text = "a" * 100
        result = truncate_middle(text, 11)
        assert result == "aaaaa" + ELLIPSIS + "aaaaa"
        assert len(result) == 11

    def test_max_len_4(self):
        # half=(4-1)//2=1, end_len=4-1-1=2
        result = truncate_middle("abcdef", 4)
        assert result == "a" + ELLIPSIS + "ef"
        assert len(result) == 4

    def test_one_char_over(self):
        # Text is exactly 1 char longer than max_len
        result = truncate_middle("abcdef", 5)
        assert result == "ab" + ELLIPSIS + "ef"
        assert len(result) == 5


# ===========================================================================
# truncate
# ===========================================================================


class TestTruncate:
    def test_short_text_unchanged(self):
        assert truncate("abc", 10) == "abc"

    def test_equal_length_unchanged(self):
        assert truncate("abcde", 5) == "abcde"

    def test_longer_text_truncated(self):
        result = truncate("abcdefghij", 5)
        assert result == "abcd" + ELLIPSIS

    def test_max_len_0(self):
        assert truncate("hello", 0) == ""

    def test_max_len_1(self):
        # max_len < 2 -> just slice
        assert truncate("hello", 1) == "h"

    def test_max_len_2(self):
        result = truncate("hello", 2)
        assert result == "h" + ELLIPSIS

    def test_empty_string(self):
        assert truncate("", 5) == ""

    def test_empty_string_zero_max(self):
        assert truncate("", 0) == ""

    def test_long_text(self):
        text = "a" * 100
        result = truncate(text, 10)
        assert result == "a" * 9 + ELLIPSIS
        assert len(result) == 10

    def test_max_len_3(self):
        result = truncate("abcdef", 3)
        assert result == "ab" + ELLIPSIS
        assert len(result) == 3

    def test_one_char_over(self):
        result = truncate("abcdef", 5)
        assert result == "abcd" + ELLIPSIS
        assert len(result) == 5


# ===========================================================================
# format_table
# ===========================================================================


class TestFormatTable:
    def test_simple_table(self, force_color_off):
        rows = [["alice", "30"], ["bob", "25"]]
        headers = ["Name", "Age"]
        result = format_table(rows, headers, no_color=True)
        lines = result.split("\n")
        assert len(lines) == 4  # header + separator + 2 data rows
        assert "Name" in lines[0]
        assert "Age" in lines[0]
        # Separator line should contain only dashes and spaces
        assert set(lines[1].strip()) <= {"-", " "}

    def test_rows_shorter_than_headers(self, force_color_off):
        rows = [["alice"]]
        headers = ["Name", "Age"]
        result = format_table(rows, headers, no_color=True)
        lines = result.split("\n")
        assert len(lines) == 3

    def test_rows_longer_than_headers(self, force_color_off):
        rows = [["alice", "30", "extra"]]
        headers = ["Name", "Age"]
        result = format_table(rows, headers, no_color=True)
        lines = result.split("\n")
        assert "extra" not in lines[2]

    def test_empty_rows(self, force_color_off):
        rows = []
        headers = ["Name", "Age"]
        result = format_table(rows, headers, no_color=True)
        lines = result.split("\n")
        assert len(lines) == 2  # header + separator only

    def test_single_row(self, force_color_off):
        rows = [["alice", "30"]]
        headers = ["Name", "Age"]
        result = format_table(rows, headers, no_color=True)
        lines = result.split("\n")
        assert len(lines) == 3

    def test_column_widths_determined_by_longest_cell(self, force_color_off):
        rows = [["a very long name", "1"]]
        headers = ["Name", "Age"]
        result = format_table(rows, headers, no_color=True)
        lines = result.split("\n")
        # First column width should be len("a very long name") = 16
        # Header "Name" should be padded to 16 chars
        assert lines[0].startswith("Name" + " " * 12)
        # Separator should start with 16 dashes
        assert lines[1].startswith("-" * 16)

    def test_separator_matches_column_widths(self, force_color_off):
        headers = ["A", "BB", "CCC"]
        rows = [["x", "yy", "zzz"]]
        result = format_table(rows, headers, no_color=True)
        lines = result.split("\n")
        # Widths: A->1 but "x" is also 1, BB->2, CCC->3
        # separator: "-  --  ---"
        assert lines[1] == "-  --  ---"

    def test_bold_header_when_color_on(self, force_color_on):
        rows = [["a", "b"]]
        headers = ["H1", "H2"]
        result = format_table(rows, headers)
        assert "\033[1m" in result
        assert "\033[0m" in result

    def test_no_color_param_disables_bold(self, force_color_on):
        rows = [["a", "b"]]
        headers = ["H1", "H2"]
        result = format_table(rows, headers, no_color=True)
        assert "\033[1m" not in result

    def test_global_no_color_disables_bold(self, force_color_off):
        rows = [["a", "b"]]
        headers = ["H1", "H2"]
        result = format_table(rows, headers)
        assert "\033[1m" not in result

    def test_left_alignment(self, force_color_off):
        rows = [["a", "b"]]
        headers = ["Name", "City"]
        result = format_table(rows, headers, no_color=True)
        lines = result.split("\n")
        # Data row should be: "a   " + "  " + "b   "
        # But trailing spaces on the last column are fine
        assert lines[2].startswith("a   ")
        assert "b" in lines[2]

    def test_multiple_columns(self, force_color_off):
        headers = ["A", "B", "C", "D"]
        rows = [["1", "2", "3", "4"], ["w", "x", "y", "z"]]
        result = format_table(rows, headers, no_color=True)
        lines = result.split("\n")
        assert len(lines) == 4

    def test_data_cell_wider_than_header(self, force_color_off):
        headers = ["N"]
        rows = [["longvalue"]]
        result = format_table(rows, headers, no_color=True)
        lines = result.split("\n")
        # Header should be padded to width of "longvalue" (9 chars)
        assert lines[0] == "N" + " " * 8  # ljust(9)
        # Separator should be 9 dashes
        assert lines[1] == "-" * 9

    def test_single_column(self, force_color_off):
        headers = ["Item"]
        rows = [["apple"], ["banana"]]
        result = format_table(rows, headers, no_color=True)
        lines = result.split("\n")
        assert len(lines) == 4
        # Width determined by "banana" (6 chars)
        assert lines[1].strip() == "-" * 6


# ===========================================================================
# format_size
# ===========================================================================


class TestFormatSize:
    def test_zero_bytes(self):
        assert format_size(0) == "0 B"

    def test_small_bytes(self):
        assert format_size(500) == "500 B"

    def test_one_byte(self):
        assert format_size(1) == "1 B"

    def test_1023_bytes(self):
        assert format_size(1023) == "1023 B"

    def test_exactly_1_kb(self):
        assert format_size(1024) == "1.0 KB"

    def test_1_5_kb(self):
        assert format_size(1536) == "1.5 KB"

    def test_just_under_1_mb(self):
        result = format_size(1024 * 1024 - 1)
        assert "KB" in result

    def test_exactly_1_mb(self):
        assert format_size(1048576) == "1.0 MB"

    def test_just_under_1_gb(self):
        result = format_size(1024 * 1024 * 1024 - 1)
        assert "MB" in result

    def test_exactly_1_gb(self):
        assert format_size(1073741824) == "1.0 GB"

    def test_large_gb(self):
        assert format_size(5 * 1024 * 1024 * 1024) == "5.0 GB"

    def test_fractional_kb(self):
        assert format_size(2560) == "2.5 KB"

    def test_fractional_mb(self):
        assert format_size(1572864) == "1.5 MB"


# ===========================================================================
# format_timestamp
# ===========================================================================


class TestFormatTimestamp:
    def test_valid_iso(self):
        assert format_timestamp("2024-01-15T10:30:00") == "01-15 10:30"

    def test_valid_iso_with_seconds(self):
        assert format_timestamp("2024-06-01T23:59:59") == "06-01 23:59"

    def test_valid_iso_date_only(self):
        assert format_timestamp("2024-01-15") == "01-15 00:00"

    def test_none_returns_em_dash(self):
        assert format_timestamp(None) == EM_DASH

    def test_empty_string_returns_em_dash(self):
        assert format_timestamp("") == EM_DASH

    def test_invalid_string_returns_em_dash(self):
        assert format_timestamp("not-a-date") == EM_DASH

    def test_garbage_returns_em_dash(self):
        assert format_timestamp("xyz123") == EM_DASH

    def test_valid_iso_with_tz_offset(self):
        assert format_timestamp("2024-01-15T10:30:00+05:00") == "01-15 10:30"

    def test_valid_iso_with_utc_offset(self):
        assert format_timestamp("2024-01-15T10:30:00+00:00") == "01-15 10:30"

    def test_midnight(self):
        assert format_timestamp("2024-12-25T00:00:00") == "12-25 00:00"

    def test_non_string_with_str_fallback(self):
        # format_timestamp calls str(ts) before fromisoformat, so a non-string
        # that str() converts to a valid ISO date would work; otherwise em-dash.
        assert format_timestamp(12345) == EM_DASH


# ===========================================================================
# format_json
# ===========================================================================


class TestFormatJson:
    def test_dict(self):
        data = {"key": "value", "num": 42}
        result = format_json(data)
        parsed = json.loads(result)
        assert parsed == data

    def test_indentation(self):
        data = {"a": 1}
        result = format_json(data)
        # Should use 2-space indent
        assert '\n  "a": 1' in result

    def test_list(self):
        data = [1, 2, 3]
        result = format_json(data)
        parsed = json.loads(result)
        assert parsed == data

    def test_nested(self):
        data = {"outer": {"inner": [1, 2, {"deep": True}]}}
        result = format_json(data)
        parsed = json.loads(result)
        assert parsed == data

    def test_non_serializable_uses_str(self):
        from datetime import datetime

        dt = datetime(2024, 1, 15, 10, 30)
        data = {"ts": dt}
        result = format_json(data)
        parsed = json.loads(result)
        assert parsed["ts"] == str(dt)

    def test_empty_dict(self):
        assert format_json({}) == "{}"

    def test_empty_list(self):
        assert format_json([]) == "[]"

    def test_string_value(self):
        result = format_json("hello")
        assert json.loads(result) == "hello"

    def test_none_value(self):
        assert format_json(None) == "null"

    def test_boolean(self):
        assert format_json(True) == "true"

    def test_nested_list_of_dicts(self):
        data = [{"a": 1}, {"b": 2}]
        result = format_json(data)
        parsed = json.loads(result)
        assert parsed == data

    def test_set_uses_str_fallback(self):
        data = {"items": {1, 2, 3}}
        # Sets are not JSON serializable; default=str should handle it
        result = format_json(data)
        assert "items" in result
