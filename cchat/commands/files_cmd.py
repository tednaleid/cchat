"""List files modified in a conversation."""

from __future__ import annotations

import argparse
from collections import defaultdict

from cchat import formatters, parser, store


def register(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser("files", help="List files modified in a conversation")
    p.add_argument("conv", help="Conversation identifier (path, UUID, prefix, or slug)")
    p.add_argument(
        "--no-subagents",
        action="store_true",
        default=False,
        help="Exclude subagent conversations",
    )
    p.add_argument("--json", action="store_true", default=False, help="Output as JSON")
    p.add_argument("--no-color", action="store_true", default=False, help="Disable colored output")
    p.set_defaults(func=run)


def _scan_file(path, file_counts, file_tools):
    """Scan a single JSONL file for file modifications."""
    for _line_num, data in parser.parse_lines(path):
        if data.get("type") != "assistant":
            continue
        mods = parser.extract_file_modifications(data)
        if not mods:
            continue
        for mod in mods:
            fp = mod["file_path"]
            file_counts[fp] += 1
            file_tools[fp].add(mod["tool"])


def run(args: argparse.Namespace) -> None:
    if args.no_color:
        formatters.set_no_color(True)

    conv_path = store.resolve_conversation(args.conv)

    file_counts: dict[str, int] = defaultdict(int)
    file_tools: dict[str, set[str]] = defaultdict(set)

    # Scan main conversation
    _scan_file(conv_path, file_counts, file_tools)

    # Scan subagents unless disabled
    include_subagents = not args.no_subagents
    if include_subagents:
        for sa_path in store.get_subagent_paths(conv_path):
            _scan_file(sa_path, file_counts, file_tools)

    if not file_counts:
        print("No file modifications found.")
        return

    # Sort by modification count descending
    sorted_files = sorted(file_counts.items(), key=lambda x: x[1], reverse=True)

    if args.json:
        data = [
            {
                "file_path": fp,
                "modifications": count,
                "tools": sorted(file_tools[fp]),
            }
            for fp, count in sorted_files
        ]
        print(formatters.format_json(data))
        return

    rows = [
        [fp, str(count), ", ".join(sorted(file_tools[fp]))]
        for fp, count in sorted_files
    ]
    headers = ["FILE", "MODIFICATIONS", "TOOLS"]
    print(formatters.format_table(rows, headers, no_color=args.no_color))
