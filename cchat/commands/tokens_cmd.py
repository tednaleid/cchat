"""Show per-turn token usage and estimated cost for a conversation."""

from __future__ import annotations

import argparse

from cchat import costs, formatters, parser, store


def register(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser("tokens", help="Show token usage and estimated cost")
    p.add_argument("conv", help="Conversation identifier (path, UUID, prefix, or slug)")
    p.add_argument("--json", action="store_true", default=False, help="Output as JSON")
    p.add_argument("--no-color", action="store_true", default=False, help="Disable colored output")
    p.set_defaults(func=run)


def _fmt(n: int) -> str:
    """Format an integer with comma separators."""
    return f"{n:,}"


def run(args: argparse.Namespace) -> None:
    if args.no_color:
        formatters.set_no_color(True)

    conv_path = store.resolve_conversation(args.conv)

    lines = parser.parse_lines(conv_path)
    deduped = parser.deduplicate_assistant_lines(lines)

    turns: list[dict] = []
    turn_num = 0
    total_cost = 0.0

    for _line_num, data in deduped:
        if data.get("type") != "assistant":
            continue
        model = parser.extract_model(data)
        usage = parser.extract_usage(data)
        if usage is None:
            continue

        turn_num += 1
        rates = costs.get_rates(model)
        turn_cost = costs.cost_for_usage(usage, rates)
        total_cost += turn_cost

        # Extract model name (last component for display)
        model_display = "unknown"
        if model:
            parts = model.split("-")
            if len(parts) > 1:
                # e.g., "claude-opus-4-6" -> "opus-4-6"
                model_display = "-".join(parts[1:])
            else:
                model_display = model

        turns.append({
            "turn": turn_num,
            "model": model_display,
            "input_tokens": usage["input_tokens"],
            "output_tokens": usage["output_tokens"],
            "cache_read_input_tokens": usage["cache_read_input_tokens"],
            "cache_creation_input_tokens": usage["cache_creation_input_tokens"],
            "cost_usd": turn_cost,
        })

    if not turns:
        print("No token usage data found.")
        return

    # Compute totals
    total_input = sum(t["input_tokens"] for t in turns)
    total_output = sum(t["output_tokens"] for t in turns)
    total_cache_read = sum(t["cache_read_input_tokens"] for t in turns)
    total_cache_create = sum(t["cache_creation_input_tokens"] for t in turns)

    if args.json:
        data = {
            "turns": turns,
            "totals": {
                "input_tokens": total_input,
                "output_tokens": total_output,
                "cache_read_input_tokens": total_cache_read,
                "cache_creation_input_tokens": total_cache_create,
            },
            "estimated_cost_usd": round(total_cost, 2),
        }
        print(formatters.format_json(data))
        return

    # Table output
    headers = ["TURN", "MODEL", "INPUT", "OUTPUT", "CACHE_READ", "CACHE_CREATE", "COST"]
    rows = [
        [
            str(t["turn"]),
            t["model"],
            _fmt(t["input_tokens"]),
            _fmt(t["output_tokens"]),
            _fmt(t["cache_read_input_tokens"]),
            _fmt(t["cache_creation_input_tokens"]),
            formatters.format_cost(t["cost_usd"]),
        ]
        for t in turns
    ]

    # Totals row
    rows.append([
        "TOTAL",
        "",
        _fmt(total_input),
        _fmt(total_output),
        _fmt(total_cache_read),
        _fmt(total_cache_create),
        formatters.format_cost(total_cost),
    ])

    print(formatters.format_table(rows, headers, no_color=args.no_color))
