"""List subagents for a conversation."""

from __future__ import annotations

import argparse

from cchat import costs, formatters, store


def register(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser("agents", help="List subagents for a conversation")
    p.add_argument("conv", help="Conversation identifier (path, UUID, prefix, or slug)")
    p.add_argument("--project", metavar="KEY", default=None,
                   help="Project key to narrow conversation resolution")
    p.add_argument("--json", action="store_true", dest="json_output",
                   help="Output as JSON")
    p.add_argument("--no-color", action="store_true",
                   help="Disable colored output")
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> None:
    if args.no_color:
        formatters.set_no_color(True)

    conv_path = store.resolve_conversation(args.conv, project_key=args.project)
    subagents = store.list_subagents(conv_path)

    if not subagents:
        print("No subagents found for this conversation.")
        return

    # Compute cost/tokens for each subagent
    cost_cache = costs.CostCache()
    for sa in subagents:
        store.get_subagent_stats(sa, cost_cache)
    cost_cache.save()

    if args.json_output:
        records = [
            {
                "agent_id": sa.agent_id,
                "prompt_snippet": sa.prompt_snippet or "",
                "first_timestamp": sa.first_timestamp,
                "last_timestamp": sa.last_timestamp,
                "model": sa.model,
                "total_tokens": sa.total_tokens,
                "estimated_cost_usd": sa.estimated_cost_usd,
                "line_count": sa.line_count,
                "turn_count": sa.turn_count,
                "size": sa.size,
            }
            for sa in subagents
        ]
        print(formatters.format_json(records))
        return

    headers = ["AGENT_ID", "PROMPT", "FIRST", "LAST", "MODEL", "TOKENS", "COST"]
    rows: list[list[str]] = []
    for sa in subagents:
        rows.append([
            sa.agent_id,
            formatters.truncate(sa.prompt_snippet or "(no prompt)", 60),
            formatters.format_timestamp(sa.first_timestamp),
            formatters.format_timestamp(sa.last_timestamp),
            formatters.format_model(sa.model),
            formatters.format_tokens(sa.total_tokens),
            formatters.format_cost(sa.estimated_cost_usd),
        ])

    print(formatters.format_table(rows, headers, no_color=args.no_color))
