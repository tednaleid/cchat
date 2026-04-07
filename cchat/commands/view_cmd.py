"""View conversation details and subagents."""

from __future__ import annotations

import argparse

from cchat import costs, formatters, store


def register(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser("view", help="View conversation details")
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
    info = store._scan_conversation(conv_path)

    # Compute cost and tokens
    cost_cache = costs.CostCache()
    info.estimated_cost_usd = store.get_conversation_cost(info, cost_cache)

    # Get model
    if not info.model:
        info.model = costs.compute_file_model(info.path)

    # Get subagents
    subagents = store.list_subagents(conv_path)
    for sa in subagents:
        store.get_subagent_stats(sa, cost_cache)
    cost_cache.save()

    if args.json_output:
        rec = {
            "uuid": info.uuid,
            "project_key": info.project_key,
            "slug": info.slug,
            "snippet": info.snippet,
            "model": info.model,
            "first_timestamp": info.first_timestamp,
            "last_timestamp": info.last_timestamp,
            "turn_count": info.turn_count,
            "agent_count": info.agent_count,
            "total_tokens": info.total_tokens,
            "size_bytes": info.size,
            "estimated_cost_usd": info.estimated_cost_usd,
            "subagents": [
                {
                    "agent_id": sa.agent_id,
                    "prompt_snippet": sa.prompt_snippet or "",
                    "model": sa.model,
                    "first_timestamp": sa.first_timestamp,
                    "last_timestamp": sa.last_timestamp,
                    "total_tokens": sa.total_tokens,
                    "estimated_cost_usd": sa.estimated_cost_usd,
                }
                for sa in subagents
            ],
        }
        print(formatters.format_json(rec))
        return

    # Pretty print conversation details
    label_color = formatters.CYAN
    print(formatters.colored("Conversation", formatters.BOLD))
    print(f"  {formatters.colored('UUID:', label_color)}      {info.uuid}")
    print(f"  {formatters.colored('Project:', label_color)}   {info.project_key}")
    if info.slug:
        print(f"  {formatters.colored('Slug:', label_color)}      {info.slug}")
    if info.snippet:
        print(f"  {formatters.colored('Snippet:', label_color)}   {info.snippet}")
    if info.model:
        print(f"  {formatters.colored('Model:', label_color)}     {formatters.format_model(info.model)}")
    print(f"  {formatters.colored('First:', label_color)}     {formatters.format_timestamp(info.first_timestamp)}")
    print(f"  {formatters.colored('Last:', label_color)}      {formatters.format_timestamp(info.last_timestamp)}")
    print(f"  {formatters.colored('Turns:', label_color)}     {info.turn_count}")
    print(f"  {formatters.colored('Agents:', label_color)}    {info.agent_count}")
    print(f"  {formatters.colored('Tokens:', label_color)}    {formatters.format_tokens(info.total_tokens)}")
    print(f"  {formatters.colored('Size:', label_color)}      {formatters.format_size(info.size)}")
    print(f"  {formatters.colored('Cost:', label_color)}      {formatters.format_cost(info.estimated_cost_usd)}")

    if subagents:
        print()
        print(formatters.colored("Subagents", formatters.BOLD))
        headers = ["AGENT_ID", "PROMPT", "FIRST", "LAST", "MODEL", "TOKENS", "COST"]
        rows = []
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
