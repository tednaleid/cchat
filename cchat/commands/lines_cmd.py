"""Show parsed lines from a conversation."""

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
    p = subparsers.add_parser("lines", help="List lines in a conversation")
    p.add_argument("conv", nargs="?", default=None,
                   help="Conversation identifier (path, UUID, prefix, or slug)")
    p.add_argument("--agent", dest="agent_id", default=None, metavar="AGENT_ID",
                   help="Resolve a subagent JSONL by agent ID (mutually exclusive with conv)")
    p.add_argument("--type", dest="line_type", default=None,
                   help="Only show lines of this type (e.g. user, assistant, system)")
    p.add_argument("--subtype", dest="line_subtype", default=None,
                   help="Only show lines of this subtype (e.g. message, tool_result, tool_use, text)")
    p.add_argument("--head", type=int, nargs="?", const=50, default=None, metavar="N",
                   help="Show first N lines (default N=50; default mode if nothing specified)")
    p.add_argument("--tail", type=int, nargs="?", const=50, default=None, metavar="N",
                   help="Show last N lines (default N=50)")
    p.add_argument("--from", type=int, default=None, dest="from_line", metavar="N",
                   help="Start from line number N (inclusive, overrides --head/--tail)")
    p.add_argument("--to", type=int, default=None, dest="to_line", metavar="N",
                   help="End at line number N (inclusive, overrides --head/--tail)")
    p.add_argument("--full", action="store_true",
                   help="Show full content of each line instead of compact table")
    p.add_argument("--max-chars", type=int, default=None, metavar="N",
                   help="Limit content to N characters per line (implies --full)")
    p.add_argument("--middle-out", action="store_true",
                   help="When truncating with --max-chars, cut the middle and keep start+end")
    p.add_argument("--json", action="store_true", dest="json_output",
                   help="Output as JSON")
    p.add_argument("--no-color", action="store_true",
                   help="Disable colored output")
    p.set_defaults(func=run)


def _format_tokens(count: int | None) -> str:
    """Format a token count for display: ``1234`` -> ``1.2k``, *None* -> ``-``."""
    if count is None:
        return "-"
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}M"
    if count >= 1_000:
        return f"{count / 1_000:.1f}k"
    return str(count)


def _trunc(text: str, max_len: int, full: bool) -> str:
    """Truncate text unless full mode is set."""
    if full:
        return text
    return formatters.truncate(text, max_len)


def _cap(text: str, budget: list[int] | None, middle_out: bool = False) -> str:
    """Return *text* capped to the remaining character budget.

    *budget* is a one-element list ``[remaining]`` mutated in-place so that
    successive calls within the same line share the allowance.  When *budget*
    is ``None`` no truncation is applied.

    If *middle_out* is True the first and last portions are kept with a
    ``[...N chars...]`` marker in the middle.
    """
    if budget is None:
        return text
    if budget[0] <= 0:
        return "..."
    if len(text) <= budget[0]:
        budget[0] -= len(text)
        return text
    limit = budget[0]
    budget[0] = 0
    if not middle_out:
        return text[:limit] + "..."
    # Keep start and end, redact the middle
    redacted = len(text) - limit
    marker = f"\n[...{redacted} chars...]\n"
    half = (limit - len(marker)) // 2
    if half < 20:
        # Not enough room for a meaningful split -- fall back to end-truncation
        return text[:limit] + "..."
    end_len = limit - len(marker) - half
    return text[:half] + marker + text[-end_len:]


def _render_line(line_num: int, data: dict, max_chars: int | None = None,
                 middle_out: bool = False) -> None:
    """Render a single conversation line with full detail (replicates view_cmd pattern)."""
    line_type = data.get("type", "")
    ts = formatters.format_timestamp(parser.extract_timestamp(data))
    subtype = parser.classify_line_subtype(data)
    tokens = parser.extract_token_count(data)
    msg = data.get("message") or {}

    # Budget tracker: mutable single-element list shared across _cap calls
    budget = [max_chars] if max_chars is not None else None
    mo = middle_out

    # Header
    tok_str = f"  {_format_tokens(tokens)} tok" if tokens is not None else ""
    header = formatters.colored(
        f"--- L{line_num}  {line_type}/{subtype}{tok_str}  {ts} ---",
        formatters.BOLD,
    )
    _safe_print(header)

    if line_type == "user":
        content = msg.get("content") if isinstance(msg, dict) else None

        if isinstance(content, str):
            _safe_print(formatters.colored("USER:", formatters.GREEN))
            _safe_print(_cap(content, budget, mo))

        elif isinstance(content, list):
            _safe_print(formatters.colored("TOOL RESULT:", formatters.GREEN))
            for item in content:
                if not isinstance(item, dict):
                    continue
                if item.get("type") != "tool_result":
                    continue
                tool_use_id = item.get("tool_use_id", "")
                _safe_print(f"  tool_use_id: {tool_use_id}")
                if budget is not None and budget[0] <= 0:
                    _safe_print("  ...")
                    break
                sub_content = item.get("content", [])
                if isinstance(sub_content, list):
                    for sub in sub_content:
                        if isinstance(sub, dict) and sub.get("type") == "text":
                            _safe_print(f"  {_cap(sub.get('text', ''), budget, mo)}")
                elif isinstance(sub_content, str):
                    _safe_print(f"  {_cap(sub_content, budget, mo)}")

        else:
            _safe_print(formatters.colored("USER:", formatters.GREEN))
            _safe_print(_cap(str(content), budget, mo))

    elif line_type == "assistant":
        _safe_print(formatters.colored("ASSISTANT:", formatters.BLUE))
        content = msg.get("content") if isinstance(msg, dict) else None
        if isinstance(content, list):
            for item in content:
                if not isinstance(item, dict):
                    continue
                if budget is not None and budget[0] <= 0:
                    _safe_print("...")
                    break
                item_type = item.get("type", "")

                if item_type == "text":
                    _safe_print(_cap(item.get("text", ""), budget, mo))

                elif item_type == "thinking":
                    _safe_print(formatters.colored("[THINKING]", formatters.DIM))
                    _safe_print(_cap(item.get("thinking", ""), budget, mo))

                elif item_type == "tool_use":
                    name = item.get("name", "tool")
                    _safe_print(formatters.colored(f"[TOOL: {name}]", formatters.MAGENTA))
                    inp = item.get("input", {})
                    _safe_print(_cap(json.dumps(inp, indent=2, default=str), budget, mo))

    elif line_type == "system":
        _safe_print(formatters.colored("SYSTEM:", formatters.BOLD))
        subtype_val = data.get("subtype", "")
        if subtype_val:
            _safe_print(f"  subtype: {subtype_val}")
        for key in ("url", "durationMs", "sessionId"):
            val = data.get(key)
            if val is not None:
                _safe_print(f"  {key}: {val}")

    else:
        _safe_print(f"{line_type.upper() or 'UNKNOWN'}:")
        _safe_print(_cap(json.dumps(data, indent=2, default=str), budget, mo))

    _safe_print()  # blank line separator


def run(args: argparse.Namespace) -> None:
    if args.no_color:
        formatters.set_no_color(True)

    if args.agent_id and args.conv:
        print("Error: --agent and conv are mutually exclusive", file=sys.stderr)
        sys.exit(1)
    if not args.agent_id and not args.conv:
        print("Error: either conv or --agent is required", file=sys.stderr)
        sys.exit(1)

    if args.agent_id:
        path = store.resolve_agent(args.agent_id)
    else:
        path = store.resolve_conversation(args.conv)
    lines = parser.deduplicate_assistant_lines(parser.parse_lines(path))

    # Collect into list, applying type filter
    entries: list[tuple[int, dict]] = []
    for line_num, data in lines:
        if args.line_type and data.get("type") != args.line_type:
            continue
        if args.line_subtype and parser.classify_line_subtype(data) != args.line_subtype:
            continue
        entries.append((line_num, data))

    # Validate mutually exclusive --head / --tail
    if args.head is not None and args.tail is not None:
        print("Error: --head and --tail are mutually exclusive", file=sys.stderr)
        sys.exit(1)

    # Apply navigation: --from/--to override --head/--tail
    if args.from_line is not None or args.to_line is not None:
        entries = [
            (ln, d) for ln, d in entries
            if (args.from_line is None or ln >= args.from_line)
            and (args.to_line is None or ln <= args.to_line)
        ]
    elif args.tail is not None:
        entries = entries[-args.tail:]
    else:
        # --head N, or default to first 50 (but --full shows all by default)
        head_n = args.head if args.head is not None else (None if args.full else 50)
        if head_n is not None:
            entries = entries[:head_n]

    if args.json_output:
        records = []
        for line_num, data in entries:
            uuid_val = data.get("uuid", "")
            records.append({
                "line_number": line_num,
                "type": data.get("type", ""),
                "subtype": parser.classify_line_subtype(data),
                "tokens": parser.extract_token_count(data),
                "uuid": uuid_val[:8] if uuid_val else "",
                "timestamp": parser.extract_timestamp(data),
                "snippet": parser.extract_content_summary(data),
            })
        _safe_print(formatters.format_json(records))
        return

    # --max-chars implies --full
    if args.max_chars is not None:
        args.full = True

    if args.full:
        for line_num, data in entries:
            _render_line(line_num, data, max_chars=args.max_chars,
                         middle_out=args.middle_out)
        return

    headers = ["LINE#", "TYPE", "SUBTYPE", "TOKENS", "UUID", "TIMESTAMP", "SNIPPET"]
    rows: list[list[str]] = []
    for line_num, data in entries:
        uuid_val = data.get("uuid", "")
        tokens = parser.extract_token_count(data)
        rows.append([
            str(line_num),
            data.get("type", ""),
            parser.classify_line_subtype(data),
            _format_tokens(tokens),
            uuid_val[:8] if uuid_val else "",
            formatters.format_timestamp(parser.extract_timestamp(data)),
            parser.extract_content_summary(data),
        ])

    _safe_print(formatters.format_table(rows, headers, no_color=args.no_color))
