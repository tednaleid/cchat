"""Streaming JSONL parser for Claude Code conversation transcripts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator


def parse_lines(path: Path) -> Iterator[tuple[int, dict]]:
    """Open a JSONL file and yield (line_number, parsed_dict) for each line.

    Line numbers are 1-indexed.  Malformed lines are silently skipped.
    Never loads the full file into memory.
    """
    with open(path, encoding="utf-8") as fh:
        for line_num, raw in enumerate(fh, start=1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                data = json.loads(raw)
            except (json.JSONDecodeError, ValueError):
                continue
            yield (line_num, data)


def deduplicate_assistant_lines(
    lines: Iterator[tuple[int, dict]],
) -> Iterator[tuple[int, dict]]:
    """Deduplicate streamed assistant lines by ``message.id``.

    Assistant messages are streamed as multiple JSONL lines sharing the same
    ``message.id``.  Only the final line for each id (the one with
    ``stop_reason`` set, or the last one seen) is yielded.  Non-assistant lines
    pass through immediately.
    """
    # message_id -> (line_num, data)
    buffer: dict[str, tuple[int, dict]] = {}
    current_id: str | None = None

    for line_num, data in lines:
        if data.get("type") != "assistant":
            # Flush any buffered assistant entry before yielding a
            # non-assistant line, so ordering is preserved.
            if current_id is not None and current_id in buffer:
                yield buffer.pop(current_id)
                current_id = None
            yield (line_num, data)
            continue

        msg = data.get("message") or {}
        msg_id = msg.get("id")
        if msg_id is None:
            # No message id – pass through as-is.
            yield (line_num, data)
            continue

        # If we see a *new* message id, flush the previous buffered entry.
        if msg_id != current_id and current_id is not None and current_id in buffer:
            yield buffer.pop(current_id)

        current_id = msg_id
        buffer[msg_id] = (line_num, data)

    # Flush remaining buffered entries.
    for entry in buffer.values():
        yield entry


def extract_timestamp(line: dict) -> str | None:
    """Return the ``timestamp`` field as an ISO string, or *None*."""
    ts = line.get("timestamp")
    if ts is None:
        return None
    return str(ts)


def classify_line_subtype(line: dict) -> str:
    """Return a short subtype label for a conversation line.

    Subtypes refine the top-level ``type`` to distinguish e.g. user messages
    from tool results, or assistant text from tool calls.
    """
    line_type = line.get("type", "")

    if line_type == "user":
        msg = line.get("message") or {}
        content = msg.get("content")
        if isinstance(content, list):
            return "tool_result"
        return "message"

    if line_type == "assistant":
        msg = line.get("message") or {}
        content = msg.get("content")
        if not isinstance(content, list):
            return "response"
        has_text = False
        has_tool = False
        has_thinking = False
        for item in content:
            if not isinstance(item, dict):
                continue
            it = item.get("type", "")
            if it == "text":
                has_text = True
            elif it == "tool_use":
                has_tool = True
            elif it == "thinking":
                has_thinking = True
        parts = []
        if has_thinking:
            parts.append("thinking")
        if has_text and has_tool:
            parts.append("text+tool")
        elif has_text:
            parts.append("text")
        elif has_tool:
            parts.append("tool_use")
        return "+".join(parts) if parts else "response"

    if line_type == "system":
        return line.get("subtype") or "system"

    if line_type == "progress":
        return "agent"

    # custom-title, agent-name, file-history-snapshot, etc.
    return line_type or ""


def extract_token_count(line: dict) -> int | None:
    """Return the total token footprint of a line, or *None* if unknown.

    For assistant lines, returns ``input_tokens + output_tokens`` from the
    usage block.  For other lines, estimates from the serialised content
    length (roughly 4 chars per token).
    """
    line_type = line.get("type", "")

    if line_type == "assistant":
        msg = line.get("message") or {}
        usage = msg.get("usage")
        if isinstance(usage, dict):
            inp = usage.get("input_tokens", 0)
            out = usage.get("output_tokens", 0)
            cache_read = usage.get("cache_read_input_tokens", 0)
            cache_create = usage.get("cache_creation_input_tokens", 0)
            total = inp + out + cache_read + cache_create
            if total > 0:
                return total
        return None

    if line_type == "user":
        msg = line.get("message") or {}
        content = msg.get("content")
        if isinstance(content, str):
            return max(1, len(content) // 4)
        if isinstance(content, list):
            # Estimate from serialised JSON length
            import json as _json
            try:
                return max(1, len(_json.dumps(content, default=str)) // 4)
            except (TypeError, ValueError):
                pass
        return None

    return None


def extract_content_summary(line: dict, max_len: int = 80) -> str:
    """Return a short human-readable summary of a conversation line."""
    line_type = line.get("type", "")

    if line_type == "user":
        msg = line.get("message") or {}
        content = msg.get("content")
        if isinstance(content, str):
            text = content.replace("\n", " ").strip()
            if len(text) > max_len:
                return text[:max_len - 1] + "\u2026"
            return text
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("type") == "tool_result":
                    return "[tool_result]"
            return "[tool_result]"
        return "user"

    if line_type == "assistant":
        msg = line.get("message") or {}
        content = msg.get("content")
        if not isinstance(content, list):
            return "assistant"
        parts: list[str] = []
        first_text: str | None = None
        tool_names: list[str] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "text" and first_text is None:
                first_text = item.get("text", "").replace("\n", " ").strip()
            elif item.get("type") == "tool_use":
                name = item.get("name", "tool")
                tool_names.append(name)
        if first_text:
            if len(first_text) > max_len:
                first_text = first_text[:max_len - 1] + "\u2026"
            parts.append(first_text)
        if tool_names:
            parts.append("[" + ", ".join(tool_names) + "]")
        return " ".join(parts) if parts else "assistant"

    if line_type == "system":
        return line.get("subtype") or "system"

    if line_type == "progress":
        return "agent progress"

    return line_type or "unknown"


def extract_model(line: dict) -> str | None:
    """Return the model ID from an assistant message, or None."""
    msg = line.get("message") or {}
    return msg.get("model")


def extract_usage(line: dict) -> dict | None:
    """Extract token usage information from an assistant message.

    Returns a dict with keys ``input_tokens``, ``output_tokens``,
    ``cache_read_input_tokens``, ``cache_creation_input_tokens`` (all
    defaulting to 0), and optionally ``cache_creation`` (a dict with tier
    breakdown), or *None* if usage data is absent.
    """
    msg = line.get("message") or {}
    usage = msg.get("usage")
    if not usage or not isinstance(usage, dict):
        return None
    result = {
        "input_tokens": usage.get("input_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
        "cache_read_input_tokens": usage.get("cache_read_input_tokens", 0),
        "cache_creation_input_tokens": usage.get("cache_creation_input_tokens", 0),
    }
    # Include tier breakdown if present
    cc = usage.get("cache_creation")
    if isinstance(cc, dict):
        result["cache_creation"] = cc
    return result


def extract_file_modifications(line: dict) -> list[dict] | None:
    """Extract file modification info from assistant tool_use calls.

    Looks for ``Write`` and ``Edit`` tool calls and returns a list of
    ``{"tool": name, "file_path": path}`` dicts, or *None* if none found.
    """
    if line.get("type") != "assistant":
        return None
    msg = line.get("message") or {}
    content = msg.get("content")
    if not isinstance(content, list):
        return None

    modifications: list[dict] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        if item.get("type") != "tool_use":
            continue
        name = item.get("name", "")
        if name not in ("Write", "Edit"):
            continue
        inp = item.get("input") or {}
        file_path = inp.get("file_path")
        if file_path:
            modifications.append({"tool": name, "file_path": file_path})

    return modifications if modifications else None
