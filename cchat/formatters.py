"""Output formatting utilities for the cchat CLI tool."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime

# ---------------------------------------------------------------------------
# Global state
# ---------------------------------------------------------------------------

_no_color: bool = False


def set_no_color(val: bool) -> None:
    """Globally force color off (or back on)."""
    global _no_color
    _no_color = val


def supports_color() -> bool:
    """Return *True* if the terminal likely supports ANSI color codes."""
    if _no_color:
        return False
    if os.environ.get("NO_COLOR") is not None:
        return False
    if not hasattr(sys.stdout, "isatty") or not sys.stdout.isatty():
        return False
    return True


# ---------------------------------------------------------------------------
# Common ANSI codes
# ---------------------------------------------------------------------------

RED = 31
GREEN = 32
YELLOW = 33
BLUE = 34
MAGENTA = 35
CYAN = 36
BOLD = 1
DIM = 2


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------


def colored(text: str, color_code: int) -> str:
    """Wrap *text* in ANSI escape sequences if color is supported."""
    if not supports_color():
        return text
    return f"\033[{color_code}m{text}\033[0m"


def truncate_middle(text: str, max_len: int) -> str:
    """Truncate *text* by replacing the middle with an ellipsis."""
    if len(text) <= max_len:
        return text
    if max_len < 3:
        return text[:max_len]
    half = (max_len - 1) // 2
    end_len = max_len - 1 - half
    return text[:half] + "\u2026" + text[-end_len:]


def truncate(text: str, max_len: int) -> str:
    """Simple end-truncation with an ellipsis."""
    if len(text) <= max_len:
        return text
    if max_len < 2:
        return text[:max_len]
    return text[: max_len - 1] + "\u2026"


# ---------------------------------------------------------------------------
# Table formatting
# ---------------------------------------------------------------------------


def format_table(
    rows: list[list[str]],
    headers: list[str],
    no_color: bool = False,
) -> str:
    """Format *rows* as a simple text table with *headers*.

    Columns are left-aligned and separated by two spaces.
    """
    num_cols = len(headers)

    # Ensure every row has the right number of columns.
    normalised: list[list[str]] = []
    for row in rows:
        padded = list(row) + [""] * (num_cols - len(row))
        normalised.append(padded[:num_cols])

    # Column widths.
    widths = [len(h) for h in headers]
    for row in normalised:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))

    sep = "  "

    # Header row.
    use_color = supports_color() and not no_color
    header_cells = [h.ljust(widths[i]) for i, h in enumerate(headers)]
    header_line = sep.join(header_cells)
    if use_color:
        header_line = f"\033[{BOLD}m{header_line}\033[0m"

    # Separator line.
    dash_line = sep.join("-" * w for w in widths)

    # Data rows.
    data_lines: list[str] = []
    for row in normalised:
        cells = [cell.ljust(widths[i]) for i, cell in enumerate(row)]
        data_lines.append(sep.join(cells))

    return "\n".join([header_line, dash_line] + data_lines)


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


def format_size(size_bytes: int) -> str:
    """Return a human-readable file size string."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    if size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"


def format_timestamp(ts: str | None) -> str:
    """Parse an ISO timestamp and return ``MM-DD HH:MM``, or ``\u2014``."""
    if not ts:
        return "\u2014"
    try:
        dt = datetime.fromisoformat(str(ts))
        return dt.strftime("%m-%d %H:%M")
    except (ValueError, TypeError):
        return "\u2014"


def format_cost(cost: float | None) -> str:
    """Return a formatted cost string, or '-' if None."""
    if cost is None:
        return "-"
    if cost < 0.01:
        return f"${cost:.3f}"
    return f"${cost:.2f}"


def format_tokens(count: int | None) -> str:
    """Return a human-readable token count string."""
    if count is None or count == 0:
        return "-"
    if count < 1000:
        return str(count)
    if count < 1_000_000:
        return f"{count / 1000:.1f}K"
    return f"{count / 1_000_000:.1f}M"


def format_model(model: str | None) -> str:
    """Return a short display name for a model ID."""
    if not model:
        return "-"
    if "opus-4-6" in model:
        return "opus4.6"
    if "opus-4" in model:
        return "opus4"
    if "sonnet-4-6" in model:
        return "sonnet4.6"
    if "sonnet-4-5" in model:
        return "sonnet4.5"
    if "sonnet-4" in model:
        return "sonnet4"
    if "haiku-4-5" in model:
        return "haiku4.5"
    if "haiku-4" in model:
        return "haiku4"
    if "3-5-sonnet" in model:
        return "sonnet3.5"
    if "3-5-haiku" in model:
        return "haiku3.5"
    # Fallback: last segment
    return model.split("-")[-1] if "-" in model else model


def format_json(data: object) -> str:
    """Pretty-print *data* as indented JSON."""
    return json.dumps(data, indent=2, default=str)
