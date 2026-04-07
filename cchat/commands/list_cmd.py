"""List conversations discovered under ~/.claude/projects/."""

from __future__ import annotations

import argparse
import shutil
import sys

from cchat import costs, formatters, store

# ---------------------------------------------------------------------------
# Field definitions
# ---------------------------------------------------------------------------

ALL_FIELDS = [
    "workspace", "project", "snippet", "first", "last", "turns", "agents",
    "model", "tokens", "cost", "name", "slug",
]

DEFAULT_FIELDS = list(ALL_FIELDS)

FIELD_HEADERS = {
    "workspace": "WORKSPACE",
    "project": "PROJECT",
    "snippet": "SNIPPET",
    "first": "FIRST",
    "last": "LAST",
    "turns": "TURNS",
    "agents": "AGENTS",
    "model": "MODEL",
    "tokens": "TOKENS",
    "cost": "COST",
    "name": "NAME",
    "slug": "SLUG",
}


def _build_cell(field: str, c: store.ConversationInfo, snippet_width: int) -> str:
    """Return the formatted cell value for a given field and conversation."""
    if field == "workspace":
        return formatters.format_workspace(c.cwd)
    if field == "project":
        return c.project_key
    if field == "snippet":
        return formatters.truncate_middle(c.snippet or "", snippet_width)
    if field == "first":
        return formatters.format_timestamp(c.first_timestamp)
    if field == "last":
        return formatters.format_timestamp(c.last_timestamp)
    if field == "turns":
        return str(c.turn_count)
    if field == "agents":
        return str(c.agent_count)
    if field == "model":
        return formatters.format_model(c.model)
    if field == "tokens":
        return formatters.format_tokens(c.total_tokens)
    if field == "cost":
        return formatters.format_cost(c.estimated_cost_usd)
    if field == "name":
        return c.name or ""
    if field == "slug":
        return c.slug or ""
    return ""


def _build_subagent_cell(field: str, sa: store.SubagentInfo, snippet_width: int, no_cost: bool) -> str:
    """Return the formatted cell value for a subagent row."""
    if field == "snippet":
        return "  -> " + formatters.truncate(sa.prompt_snippet or "(no prompt)", max(snippet_width - 5, 10))
    if field == "first":
        return formatters.format_timestamp(sa.first_timestamp)
    if field == "last":
        return formatters.format_timestamp(sa.last_timestamp)
    if field == "model":
        return formatters.format_model(sa.model)
    if field == "tokens":
        return formatters.format_tokens(sa.total_tokens) if not no_cost else "-"
    if field == "cost":
        return formatters.format_cost(sa.estimated_cost_usd)
    if field == "slug":
        return sa.agent_id
    return ""


def _compute_snippet_width(fields: list[str], conversations: list[store.ConversationInfo]) -> int:
    """Compute snippet column width to fit the terminal.

    Estimates the width of all non-snippet columns, then gives snippet
    whatever space remains. Returns a minimum of 20.
    """
    if "snippet" not in fields:
        return 0

    if not sys.stdout.isatty():
        return 10000

    try:
        term_width = shutil.get_terminal_size().columns
    except (ValueError, OSError):
        return 60

    # Estimate width of each non-snippet column from headers + a sample of data
    sep_width = 2  # two-space column separator
    other_width = 0
    for f in fields:
        if f == "snippet":
            continue
        col_width = len(FIELD_HEADERS[f])
        for c in conversations[:20]:
            cell = _build_cell(f, c, 0)
            col_width = max(col_width, len(cell))
        other_width += col_width + sep_width

    available = term_width - other_width - sep_width
    return max(available, 20)


def _parse_fields(fields_str: str | None, no_cost: bool) -> list[str]:
    """Parse the --fields argument into a validated field list.

    Fields prefixed with ``-`` remove from the defaults (e.g. ``-project``).
    Positive fields select explicitly.  If the list contains only removals,
    they are applied against the defaults.  If it contains any positive field,
    removals are ignored.
    """
    if fields_str is not None:
        raw = [f.strip() for f in fields_str.split(",") if f.strip()]
        adds = [f for f in raw if not f.startswith("-")]
        removes = {f[1:] for f in raw if f.startswith("-")}

        unknown = [f for f in adds if f not in ALL_FIELDS]
        unknown += [f for f in removes if f not in ALL_FIELDS]
        if unknown:
            raise SystemExit(f"Unknown fields: {', '.join(unknown)}. Available: {', '.join(ALL_FIELDS)}")

        if adds:
            fields = adds
        else:
            fields = [f for f in DEFAULT_FIELDS if f not in removes]
    else:
        fields = list(DEFAULT_FIELDS)

    if no_cost and "cost" in fields:
        fields.remove("cost")

    return fields


def register(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser("list", help="List conversations")
    p.add_argument("--project", metavar="KEY", default=None,
                   help="Only show conversations for this project key")
    p.add_argument("--limit", type=int, default=20,
                   help="Max conversations to show (default: 20)")
    p.add_argument("--sort", choices=["date", "size", "cost"], default="date",
                   help="Sort order (default: date)")
    p.add_argument("--no-cost", action="store_true", default=False,
                   help="Skip cost computation for faster listing")
    p.add_argument("--include-subagents", action="store_true", default=False,
                   help="Show subagent entries nested under their parent conversation")
    p.add_argument("-f", "--fields", metavar="FIELDS", default=None,
                   help=f"Comma-separated columns to display; prefix with - to remove from defaults, use = for negatives e.g. -f=-project,-tokens (available: {','.join(ALL_FIELDS)})")
    p.add_argument("--json", action="store_true", dest="json_output",
                   help="Output as JSON")
    p.add_argument("--no-color", action="store_true",
                   help="Disable colored output")
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> None:
    if args.no_color:
        formatters.set_no_color(True)

    conversations = store.discover_conversations(project_key=args.project)

    # Compute costs and tokens if requested
    cost_cache = None
    if not args.no_cost:
        cost_cache = costs.CostCache()
        for c in conversations:
            c.estimated_cost_usd = store.get_conversation_cost(c, cost_cache)
        cost_cache.save()

    # Sort
    if args.sort == "cost":
        conversations.sort(
            key=lambda c: c.estimated_cost_usd or 0,
            reverse=True,
        )
    elif args.sort == "size":
        conversations.sort(key=lambda c: c.size, reverse=True)
    else:
        # Sort by last_timestamp, most recent first; None sorts last
        conversations.sort(
            key=lambda c: c.last_timestamp or "",
            reverse=True,
        )

    # Limit
    conversations = conversations[: args.limit]

    # Optionally gather subagent info
    subagents_by_conv: dict[str, list[store.SubagentInfo]] = {}
    if args.include_subagents:
        for c in conversations:
            subs = store.list_subagents(c.path)
            if subs:
                if not args.no_cost and cost_cache is not None:
                    for sa in subs:
                        store.get_subagent_stats(sa, cost_cache)
                subagents_by_conv[c.uuid] = subs
        if cost_cache is not None:
            cost_cache.save()

    if args.json_output:
        records = []
        for c in conversations:
            rec: dict[str, object] = {
                "project_key": c.project_key,
                "workspace": formatters.format_workspace(c.cwd),
                "snippet": c.snippet or "",
                "first_timestamp": c.first_timestamp,
                "last_timestamp": c.last_timestamp,
                "turn_count": c.turn_count,
                "agent_count": c.agent_count,
                "model": c.model,
                "total_tokens": c.total_tokens,
                "size": c.size,
                "slug": c.slug,
                "name": c.name,
                "cwd": c.cwd,
            }
            if not args.no_cost:
                rec["estimated_cost_usd"] = c.estimated_cost_usd
            if args.include_subagents and c.uuid in subagents_by_conv:
                rec["subagents"] = [
                    {
                        "agent_id": sa.agent_id,
                        "prompt_snippet": sa.prompt_snippet or "",
                        "first_timestamp": sa.first_timestamp,
                        "last_timestamp": sa.last_timestamp,
                        "model": sa.model,
                        "total_tokens": sa.total_tokens,
                        "line_count": sa.line_count,
                        "turn_count": sa.turn_count,
                        "estimated_cost_usd": sa.estimated_cost_usd,
                    }
                    for sa in subagents_by_conv[c.uuid]
                ]
            records.append(rec)
        print(formatters.format_json(records))
        return

    fields = _parse_fields(args.fields, args.no_cost)
    snippet_width = _compute_snippet_width(fields, conversations)
    headers = [FIELD_HEADERS[f] for f in fields]

    rows: list[list[str]] = []
    for c in conversations:
        row = [_build_cell(f, c, snippet_width) for f in fields]
        rows.append(row)

        if args.include_subagents and c.uuid in subagents_by_conv:
            for sa in subagents_by_conv[c.uuid]:
                sa_row = [_build_subagent_cell(f, sa, snippet_width, args.no_cost) for f in fields]
                rows.append(sa_row)

    print(formatters.format_table(rows, headers, no_color=args.no_color))
