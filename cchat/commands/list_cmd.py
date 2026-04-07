"""List conversations discovered under ~/.claude/projects/."""

from __future__ import annotations

import argparse

from cchat import costs, formatters, store


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
            rec = {
                "project_key": c.project_key,
                "snippet": c.snippet or "",
                "first_timestamp": c.first_timestamp,
                "last_timestamp": c.last_timestamp,
                "turn_count": c.turn_count,
                "agent_count": c.agent_count,
                "model": c.model,
                "total_tokens": c.total_tokens,
                "size": c.size,
                "slug": c.slug,
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

    if args.no_cost:
        headers = ["PROJECT", "SNIPPET", "FIRST", "LAST", "TURNS", "AGENTS", "MODEL", "TOKENS", "SLUG"]
    else:
        headers = ["PROJECT", "SNIPPET", "FIRST", "LAST", "TURNS", "AGENTS", "MODEL", "TOKENS", "COST", "SLUG"]

    rows: list[list[str]] = []
    for c in conversations:
        row = [
            c.project_key,
            formatters.truncate_middle(c.snippet or "", 60),
            formatters.format_timestamp(c.first_timestamp),
            formatters.format_timestamp(c.last_timestamp),
            str(c.turn_count),
            str(c.agent_count),
            formatters.format_model(c.model),
            formatters.format_tokens(c.total_tokens),
        ]
        if not args.no_cost:
            row.append(formatters.format_cost(c.estimated_cost_usd))
        row.append(c.slug or "")
        rows.append(row)

        if args.include_subagents and c.uuid in subagents_by_conv:
            for sa in subagents_by_conv[c.uuid]:
                sa_row = [
                    "",
                    "  -> " + formatters.truncate(sa.prompt_snippet or "(no prompt)", 55),
                    formatters.format_timestamp(sa.first_timestamp),
                    formatters.format_timestamp(sa.last_timestamp),
                    "",
                    "",
                    formatters.format_model(sa.model),
                    formatters.format_tokens(sa.total_tokens) if not args.no_cost else "-",
                ]
                if not args.no_cost:
                    sa_row.append(formatters.format_cost(sa.estimated_cost_usd))
                sa_row.append(sa.agent_id)
                rows.append(sa_row)

    print(formatters.format_table(rows, headers, no_color=args.no_color))
