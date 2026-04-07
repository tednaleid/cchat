"""Search conversation transcripts for a query string."""

from __future__ import annotations

import argparse
import json
import sys

from cchat import formatters, parser, store

HARD_CAP = 1000


def register(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser("search", help="Search conversation transcripts")
    p.add_argument("query", help="Substring to search for (case-insensitive)")
    p.add_argument("--project", default=None, help="Restrict search to a project key")
    p.add_argument("--limit", type=int, default=20, help="Maximum number of matches (default: 20)")
    p.add_argument("--sort", choices=["newest", "oldest"], default="newest", help="Sort order (default: newest)")
    p.add_argument("--type", dest="type_filter", default=None, help="Only show lines of this type (e.g. user, assistant, system)")
    p.add_argument("--first-per-conv", action="store_true", default=False, help="Keep only the first match per conversation")
    p.add_argument("--json", action="store_true", default=False, help="Output as JSON")
    p.add_argument("--no-color", action="store_true", default=False, help="Disable colored output")
    p.set_defaults(func=run)


def _snippet(line: str, query_lower: str, context: int = 30) -> str:
    """Extract a snippet around the first occurrence of query in line."""
    line_lower = line.lower()
    idx = line_lower.find(query_lower)
    if idx == -1:
        return ""
    start = max(0, idx - context)
    end = min(len(line), idx + len(query_lower) + context)
    snippet = line[start:end].replace("\n", " ").replace("\r", "")
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(line) else ""
    result = prefix + snippet + suffix
    # Replace characters that can't be encoded in the console's encoding (e.g.
    # cp1252 on Windows) to avoid UnicodeEncodeError when printing.
    enc = sys.stdout.encoding or "utf-8"
    return result.encode(enc, errors="replace").decode(enc)


def run(args: argparse.Namespace) -> None:
    if args.no_color:
        formatters.set_no_color(True)

    query_lower = args.query.lower()

    conversations = store.discover_conversations(project_key=args.project)
    if not conversations:
        print("No conversations found.")
        return

    # Build lookup for slug and project_key by UUID.
    conv_meta: dict[str, store.ConversationInfo] = {c.uuid: c for c in conversations}

    matches: list[dict] = []
    capped = False

    for conv in conversations:
        conv_uuid = conv.uuid
        try:
            with open(conv.path, "r", encoding="utf-8") as fh:
                for line_num, raw_line in enumerate(fh, start=1):
                    if query_lower not in raw_line.lower():
                        continue
                    try:
                        data = json.loads(raw_line)
                    except (json.JSONDecodeError, ValueError):
                        data = {}

                    line_type = data.get("type", "?")

                    # --type filter: skip non-matching lines early.
                    if args.type_filter and line_type != args.type_filter:
                        continue

                    snip = _snippet(raw_line.strip(), query_lower)
                    timestamp = parser.extract_timestamp(data)
                    info = conv_meta.get(conv_uuid)

                    matches.append({
                        "conversation": conv_uuid,
                        "line": line_num,
                        "type": line_type,
                        "snippet": snip,
                        "timestamp": timestamp,
                        "slug": info.slug if info else None,
                        "project_key": info.project_key if info else None,
                    })

                    if len(matches) >= HARD_CAP:
                        capped = True
                        break
        except OSError:
            continue

        if capped:
            break

    if not matches:
        print(f"No matches found for '{args.query}'.")
        return

    # Sort all matches.
    reverse = args.sort == "newest"
    matches.sort(key=lambda m: m["timestamp"] or "", reverse=reverse)

    total_found = len(matches)

    # --first-per-conv: keep only the first match per conversation.
    if args.first_per_conv:
        seen: set[str] = set()
        deduped: list[dict] = []
        for m in matches:
            if m["conversation"] not in seen:
                seen.add(m["conversation"])
                deduped.append(m)
        matches = deduped

    # Apply --limit after sorting and dedup.
    matches = matches[: args.limit]

    if args.json:
        envelope = {
            "matches": matches,
            "total": total_found,
            "capped": capped,
        }
        print(formatters.format_json(envelope))
        return

    # Truncation notice to stderr.
    if len(matches) < total_found or capped:
        displayed = len(matches)
        total_label = f"{total_found}+" if capped else str(total_found)
        print(f"(showing {displayed} of {total_label} matches)", file=sys.stderr)

    rows = [
        [
            formatters.format_timestamp(m["timestamp"]),
            m["slug"] or m["conversation"][:8],
            m["conversation"][:8],
            str(m["line"]),
            m["type"],
            m["snippet"],
        ]
        for m in matches
    ]
    headers = ["DATE", "SLUG", "CONVERSATION", "LINE#", "TYPE", "MATCH"]
    print(formatters.format_table(rows, headers, no_color=args.no_color))
