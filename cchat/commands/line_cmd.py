"""Show a single line from a conversation."""

from __future__ import annotations

import argparse
import json
import sys

from cchat import formatters, parser, store


def _safe_print(text=""):
    """Print with encoding-safe fallback for Windows consoles."""
    encoding = sys.stdout.encoding or 'utf-8'
    print(text.encode(encoding, errors='replace').decode(encoding))


def register(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser("line", help="Show a single conversation line")
    p.add_argument("conv", help="Conversation identifier (path, UUID, prefix, or slug)")
    p.add_argument("line", type=int, help="Line number (1-indexed)")
    p.add_argument("--raw", action="store_true",
                   help="Print raw JSON")
    p.add_argument("--full", action="store_true",
                   help="Show full content without truncation")
    p.add_argument("-C", "--context", type=int, default=0, metavar="N",
                   help="Show N context lines before and after (from deduplicated view)")
    p.add_argument("--no-color", action="store_true",
                   help="Disable colored output")
    p.set_defaults(func=run)


def _trunc(text: str, max_len: int, full: bool) -> str:
    """Truncate text unless --full is set."""
    if full:
        return text
    return formatters.truncate(text, max_len)


def _render_line(data: dict, *, full: bool = False) -> None:
    """Render a single conversation line with full detail."""
    line_type = data.get("type", "")
    msg = data.get("message") or {}

    if line_type == "user":
        content = msg.get("content") if isinstance(msg, dict) else None

        if isinstance(content, str):
            _safe_print(formatters.colored("USER:", formatters.GREEN))
            _safe_print(content)

        elif isinstance(content, list):
            _safe_print(formatters.colored("TOOL RESULT:", formatters.GREEN))
            for item in content:
                if not isinstance(item, dict):
                    continue
                if item.get("type") != "tool_result":
                    continue
                tool_use_id = item.get("tool_use_id", "")
                _safe_print(f"  tool_use_id: {tool_use_id}")
                sub_content = item.get("content", [])
                if isinstance(sub_content, list):
                    for sub in sub_content:
                        if isinstance(sub, dict) and sub.get("type") == "text":
                            text = sub.get("text", "")
                            _safe_print(f"  {_trunc(text, 200, full)}")
                elif isinstance(sub_content, str):
                    _safe_print(f"  {_trunc(sub_content, 200, full)}")

        else:
            _safe_print(formatters.colored("USER:", formatters.GREEN))
            _safe_print(str(content))

    elif line_type == "assistant":
        _safe_print(formatters.colored("ASSISTANT:", formatters.BLUE))
        content = msg.get("content") if isinstance(msg, dict) else None
        if isinstance(content, list):
            for item in content:
                if not isinstance(item, dict):
                    continue
                item_type = item.get("type", "")

                if item_type == "text":
                    text = item.get("text", "")
                    _safe_print(_trunc(text, 500, full))

                elif item_type == "thinking":
                    _safe_print(formatters.colored("[THINKING]", formatters.DIM))
                    thinking = item.get("thinking", "")
                    _safe_print(_trunc(thinking, 100, full))

                elif item_type == "tool_use":
                    name = item.get("name", "tool")
                    _safe_print(formatters.colored(f"[TOOL: {name}]", formatters.MAGENTA))
                    inp = item.get("input", {})
                    inp_str = json.dumps(inp, default=str)
                    _safe_print(_trunc(inp_str, 200, full))

    elif line_type == "system":
        _safe_print(formatters.colored("SYSTEM:", formatters.BOLD))
        subtype = data.get("subtype", "")
        if subtype:
            _safe_print(f"  subtype: {subtype}")
        # Print other relevant fields
        for key in ("url", "durationMs", "sessionId"):
            val = data.get(key)
            if val is not None:
                _safe_print(f"  {key}: {val}")

    else:
        _safe_print(f"{line_type.upper() or 'UNKNOWN'}:")
        _safe_print(json.dumps(data, indent=2, default=str))


def _render_context_line(line_num: int, data: dict) -> None:
    """Render a context line as a compact one-liner."""
    line_type = data.get("type", "")
    ts = formatters.format_timestamp(parser.extract_timestamp(data))
    summary = parser.extract_content_summary(data)
    label = formatters.colored(f"L{line_num}", formatters.DIM)
    type_str = formatters.colored(line_type, formatters.CYAN)
    _safe_print(f"  {label}  {ts}  {type_str}  {summary}")


def run(args: argparse.Namespace) -> None:
    if args.no_color:
        formatters.set_no_color(True)

    path = store.resolve_conversation(args.conv)

    # Find the line at the given line number (raw, NOT deduplicated)
    target_data = None
    for line_num, data in parser.parse_lines(path):
        if line_num == args.line:
            target_data = data
            break

    if target_data is None:
        raise SystemExit(f"Line {args.line} not found in conversation")

    if args.raw:
        print(json.dumps(target_data, indent=2))
        return

    if args.context > 0:
        # Load all deduplicated lines to find context
        deduped = list(
            parser.deduplicate_assistant_lines(parser.parse_lines(path))
        )

        # Find the target line in the deduplicated list
        target_idx = None
        for i, (ln, _data) in enumerate(deduped):
            if ln == args.line:
                target_idx = i
                break

        if target_idx is None:
            # Target line was deduped away (e.g. streaming chunk)
            print(
                formatters.colored(
                    f"Warning: line {args.line} was deduplicated "
                    f"(likely a streaming chunk); showing without context",
                    formatters.YELLOW,
                ),
                file=sys.stderr,
            )
            _render_line(target_data, full=args.full)
            return

        # Slice context window
        start = max(0, target_idx - args.context)
        end = min(len(deduped), target_idx + args.context + 1)

        before = deduped[start:target_idx]
        after = deduped[target_idx + 1 : end]

        # Render before-context lines
        for ln, d in before:
            _render_context_line(ln, d)

        if before:
            _safe_print("---")

        # Render the target line fully
        _render_line(target_data, full=args.full)

        if after:
            _safe_print("---")

        # Render after-context lines
        for ln, d in after:
            _render_context_line(ln, d)
    else:
        _render_line(target_data, full=args.full)
