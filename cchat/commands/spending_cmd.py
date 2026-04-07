"""Show spending by day and week."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta
from collections import defaultdict

from cchat import costs, formatters, store
from cchat.costs import TokenBreakdown

# Week reset is Wednesday at 5:00 PM
WEEK_RESET_HOUR = 17  # 5:00 PM
WEEK_RESET_WEEKDAY = 2  # Wednesday (Monday=0)


def _fmt_tokens(n: int) -> str:
    """Format a token count with human-friendly suffix (K, M, B)."""
    if n >= 1_000_000_000:
        v = n / 1_000_000_000
        return f"{v:.3g}B"
    if n >= 1_000_000:
        v = n / 1_000_000
        return f"{v:.3g}M"
    if n >= 1_000:
        v = n / 1_000
        return f"{v:.3g}K"
    return str(n)


def _token_summary_line(bd: TokenBreakdown) -> str:
    """Build the token summary line from a breakdown."""
    cache_create_total = bd.cache_create_5m_tokens + bd.cache_create_1h_tokens
    parts = [
        f"{_fmt_tokens(bd.input_tokens)} input",
        f"{_fmt_tokens(bd.output_tokens)} output",
    ]
    # cache_create: show tier breakdown only if both tiers are present
    if bd.cache_create_5m_tokens > 0 or bd.cache_create_1h_tokens > 0:
        if bd.cache_create_5m_tokens > 0 and bd.cache_create_1h_tokens > 0:
            tier_detail = (
                f"{_fmt_tokens(bd.cache_create_5m_tokens)} 5m"
                f" + {_fmt_tokens(bd.cache_create_1h_tokens)} 1h"
            )
            parts.append(f"{_fmt_tokens(cache_create_total)} cache_create ({tier_detail})")
        else:
            parts.append(f"{_fmt_tokens(cache_create_total)} cache_create")
    if bd.cache_read_tokens > 0:
        parts.append(f"{_fmt_tokens(bd.cache_read_tokens)} cache_read")
    return "Tokens: " + ", ".join(parts)


def register(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser("spending", help="Show spending by day and week")
    p.add_argument(
        "--weeks",
        type=int,
        default=4,
        help="Number of weeks to show (default: 4)",
    )
    p.add_argument(
        "--project",
        metavar="KEY",
        default=None,
        help="Only show conversations for this project key",
    )
    p.add_argument(
        "--projects",
        action="store_true",
        default=False,
        help="Break down spending by project per day",
    )
    p.add_argument("--json", action="store_true", default=False, help="Output as JSON")
    p.add_argument(
        "--no-color", action="store_true", default=False, help="Disable colored output"
    )
    p.set_defaults(func=run)


def get_week_start(dt: datetime) -> datetime:
    """Find the most recent Wednesday 5:00 PM before or equal to dt.

    Args:
        dt: A datetime to check.

    Returns:
        The start of the week (Wednesday 5:00 PM, 00:00:00 seconds/microseconds).
    """
    # Normalize to the reset hour
    candidate = dt.replace(hour=WEEK_RESET_HOUR, minute=0, second=0, microsecond=0)

    # If dt is on Wednesday and at or after 5 PM, the week started this Wednesday
    if dt.weekday() == WEEK_RESET_WEEKDAY and dt >= candidate:
        return candidate

    # Walk back to the previous Wednesday 5 PM
    days_back = (dt.weekday() - WEEK_RESET_WEEKDAY) % 7
    if days_back == 0:
        # dt is Wednesday but before 5 PM, so walk back a full week
        days_back = 7

    return (dt - timedelta(days=days_back)).replace(
        hour=WEEK_RESET_HOUR, minute=0, second=0, microsecond=0
    )


def run(args: argparse.Namespace) -> None:
    if args.no_color:
        formatters.set_no_color(True)

    # Discover conversations
    conversations = store.discover_conversations(project_key=args.project)

    if not conversations:
        if args.json:
            print(
                formatters.format_json(
                    {
                        "weeks": [],
                        "total_cost_usd": 0.0,
                        "total_conversations": 0,
                    }
                )
            )
        else:
            print("No conversations found.")
        return

    # Compute costs
    cost_cache = costs.CostCache()
    for c in conversations:
        c.estimated_cost_usd = store.get_conversation_cost(c, cost_cache)
    cost_cache.save()

    if args.projects:
        _run_projects_mode(conversations, args)
        return

    # Group conversations by week and day
    # Week -> Day (date string) -> list of (cost, conv_count)
    week_data: dict[str, dict[str, tuple[float, int]]] = defaultdict(
        lambda: defaultdict(lambda: (0.0, 0))
    )

    # Accumulate token breakdown for all displayed conversations
    total_breakdown = TokenBreakdown()

    for conv in conversations:
        if not conv.first_timestamp:
            continue

        try:
            dt = datetime.fromisoformat(conv.first_timestamp.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            continue

        week_start = get_week_start(dt)
        week_key = week_start.isoformat()
        day_key = dt.strftime("%Y-%m-%d")

        current_cost, current_count = week_data[week_key][day_key]
        current_cost += conv.estimated_cost_usd or 0.0
        current_count += 1
        week_data[week_key][day_key] = (current_cost, current_count)

    # Compute token breakdowns only for conversations in the displayed weeks
    sorted_week_keys = sorted(week_data.keys(), reverse=True)[:args.weeks]
    displayed_week_keys = set(sorted_week_keys)
    for conv in conversations:
        if not conv.first_timestamp:
            continue
        try:
            dt = datetime.fromisoformat(conv.first_timestamp.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            continue
        week_start = get_week_start(dt)
        week_key = week_start.isoformat()
        if week_key in displayed_week_keys:
            total_breakdown += store.get_conversation_tokens(conv)

    if args.json:
        _output_json(week_data, args.weeks, total_breakdown)
    else:
        _output_table(week_data, args.weeks, args.no_color, total_breakdown)


def _output_json(
    week_data: dict[str, dict[str, tuple[float, int]]],
    num_weeks: int,
    token_breakdown: TokenBreakdown | None = None,
) -> None:
    """Output spending data as JSON."""
    # Sort weeks in descending order (most recent first)
    sorted_weeks = sorted(week_data.keys(), reverse=True)[:num_weeks]

    weeks_list = []
    total_cost = 0.0
    total_conversations = 0

    for week_key in sorted_weeks:
        try:
            week_start_dt = datetime.fromisoformat(week_key)
        except ValueError:
            continue

        days_dict = week_data[week_key]

        # Sort days in descending order
        sorted_days = sorted(days_dict.keys(), reverse=True)

        days_list = []
        week_cost = 0.0
        week_conversations = 0

        for day_key in sorted_days:
            cost, count = days_dict[day_key]
            days_list.append(
                {
                    "date": day_key,
                    "cost_usd": round(cost, 2),
                    "conversations": count,
                }
            )
            week_cost += cost
            week_conversations += count

        total_cost += week_cost
        total_conversations += week_conversations

        # Determine if this is the current week
        # Get current time in the same timezone context as week_start_dt
        now = datetime.now()
        # Convert both to naive UTC for comparison if needed
        if week_start_dt.tzinfo is not None:
            # Remove timezone info for comparison (assume UTC)
            week_start_utc = week_start_dt.replace(tzinfo=None)
        else:
            week_start_utc = week_start_dt
        current_week = get_week_start(now).isoformat() == week_start_utc.isoformat()

        weeks_list.append(
            {
                "week_start": week_key,
                "current": current_week,
                "days": days_list,
                "subtotal_cost_usd": round(week_cost, 2),
                "subtotal_conversations": week_conversations,
            }
        )

    data: dict = {
        "weeks": weeks_list,
        "total_cost_usd": round(total_cost, 2),
        "total_conversations": total_conversations,
    }
    if token_breakdown is not None:
        data["token_totals"] = {
            "input_tokens": token_breakdown.input_tokens,
            "output_tokens": token_breakdown.output_tokens,
            "cache_read_tokens": token_breakdown.cache_read_tokens,
            "cache_create_5m_tokens": token_breakdown.cache_create_5m_tokens,
            "cache_create_1h_tokens": token_breakdown.cache_create_1h_tokens,
        }
    print(formatters.format_json(data))


def _output_table(
    week_data: dict[str, dict[str, tuple[float, int]]],
    num_weeks: int,
    no_color: bool,
    token_breakdown: TokenBreakdown | None = None,
) -> None:
    """Output spending data as a formatted table."""
    # Sort weeks in descending order (most recent first)
    sorted_weeks = sorted(week_data.keys(), reverse=True)[:num_weeks]

    # Determine current week
    now = datetime.now()
    current_week_key = get_week_start(now).isoformat()

    output_lines = []
    grand_total_cost = 0.0
    grand_total_conversations = 0

    for week_idx, week_key in enumerate(sorted_weeks):
        try:
            week_start_dt = datetime.fromisoformat(week_key)
        except ValueError:
            continue

        is_current = week_key == current_week_key

        # Format week header
        week_date_str = week_start_dt.strftime("%Y-%m-%d")
        if is_current:
            week_header = f"Week of {week_date_str} (current)"
            week_header = formatters.colored(week_header, formatters.GREEN)
        else:
            week_header = f"Week of {week_date_str}"

        output_lines.append(week_header)

        days_dict = week_data[week_key]

        # Sort days in descending order
        sorted_days = sorted(days_dict.keys(), reverse=True)

        week_cost = 0.0
        week_conversations = 0

        for day_key in sorted_days:
            cost, count = days_dict[day_key]
            week_cost += cost
            week_conversations += count

            # Parse day key to get day of week
            try:
                day_dt = datetime.fromisoformat(day_key)
                dow = day_dt.strftime("%a")  # Mon, Tue, etc.
            except ValueError:
                dow = "???"

            cost_str = formatters.format_cost(cost)
            conv_str = f"{count} conversations"

            # Check if this is the Wednesday at the week start
            day_note = ""
            if day_dt.weekday() == WEEK_RESET_WEEKDAY:
                # This Wednesday should be the week start (earliest day in the week)
                # Add a note indicating conversations on this day are from 5:00 PM onward
                day_note = " (from 5:00 PM)"

            line = f"  {dow} {day_key}  {cost_str:>8}  {conv_str}{day_note}"
            output_lines.append(line)

        grand_total_cost += week_cost
        grand_total_conversations += week_conversations

        # Subtotal line for this week
        subtotal_cost_str = formatters.format_cost(week_cost)
        subtotal_line = (
            f"  Subtotal      {subtotal_cost_str:>8}  {week_conversations} conversations"
        )
        subtotal_line = formatters.colored(subtotal_line, formatters.BOLD)
        output_lines.append(subtotal_line)

        # Blank line between weeks (except after the last one)
        if week_idx < len(sorted_weeks) - 1:
            output_lines.append("")

    # Grand total
    grand_total_cost_str = formatters.format_cost(grand_total_cost)
    grand_total_line = f"Grand total    {grand_total_cost_str:>8}  {grand_total_conversations} conversations"
    grand_total_line = formatters.colored(grand_total_line, formatters.BOLD)
    output_lines.append("")
    output_lines.append(grand_total_line)

    if token_breakdown is not None:
        output_lines.append(_token_summary_line(token_breakdown))

    print("\n".join(output_lines))


# ---------------------------------------------------------------------------
# --projects mode: breakdown by project per day
# ---------------------------------------------------------------------------

# Type alias: week_key -> day_key -> project_key -> (cost, count)
ProjectWeekData = dict[str, dict[str, dict[str, tuple[float, int]]]]


def _friendly_project(key: str) -> str:
    """Shorten a project key for display.

    Claude project keys encode paths by replacing ``/`` and ``\\`` with ``-``,
    so ``C:\\git\\l\\sc`` becomes ``C--git-l-sc`` (the ``:\\`` produces ``--``).
    The encoding is lossy -- a literal hyphen in a directory name (e.g.
    ``video-game``) is indistinguishable from a path separator.  We decode
    aggressively (all ``-`` -> ``/``) as a best-guess display name; the raw
    key is always shown alongside for copy-paste into ``--project``.
    """
    # "C--git-l-sc" -> "/git/l/sc"
    if len(key) >= 2 and key[1] == "-":
        key = key[2:]  # drop drive letter + dash
    key = key.replace("-", "/")
    if not key.startswith("/"):
        key = "/" + key
    return key


def _run_projects_mode(
    conversations: list[store.ConversationInfo], args: argparse.Namespace
) -> None:
    """Group spending by project, day, and week."""
    week_data: ProjectWeekData = defaultdict(
        lambda: defaultdict(lambda: defaultdict(lambda: (0.0, 0)))
    )

    for conv in conversations:
        if not conv.first_timestamp:
            continue
        try:
            dt = datetime.fromisoformat(conv.first_timestamp.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            continue

        week_key = get_week_start(dt).isoformat()
        day_key = dt.strftime("%Y-%m-%d")
        proj = conv.project_key or "(unknown)"

        cur_cost, cur_count = week_data[week_key][day_key][proj]
        week_data[week_key][day_key][proj] = (
            cur_cost + (conv.estimated_cost_usd or 0.0),
            cur_count + 1,
        )

    if args.json:
        _output_projects_json(week_data, args.weeks)
    else:
        _output_projects_table(week_data, args.weeks)


def _output_projects_json(week_data: ProjectWeekData, num_weeks: int) -> None:
    sorted_weeks = sorted(week_data.keys(), reverse=True)[:num_weeks]

    weeks_list = []
    total_cost = 0.0
    total_conversations = 0

    for week_key in sorted_weeks:
        try:
            week_start_dt = datetime.fromisoformat(week_key)
        except ValueError:
            continue

        days_dict = week_data[week_key]
        sorted_days = sorted(days_dict.keys(), reverse=True)

        days_list = []
        week_cost = 0.0
        week_conversations = 0

        for day_key in sorted_days:
            projects = days_dict[day_key]
            proj_list = []
            day_cost = 0.0
            day_count = 0
            for proj in sorted(projects):
                cost, count = projects[proj]
                proj_list.append(
                    {"project": proj, "cost_usd": round(cost, 2), "conversations": count}
                )
                day_cost += cost
                day_count += count

            days_list.append(
                {
                    "date": day_key,
                    "cost_usd": round(day_cost, 2),
                    "conversations": day_count,
                    "projects": proj_list,
                }
            )
            week_cost += day_cost
            week_conversations += day_count

        total_cost += week_cost
        total_conversations += week_conversations

        now = datetime.now()
        current_week = get_week_start(now).isoformat() == week_key

        weeks_list.append(
            {
                "week_start": week_key,
                "current": current_week,
                "days": days_list,
                "subtotal_cost_usd": round(week_cost, 2),
                "subtotal_conversations": week_conversations,
            }
        )

    print(
        formatters.format_json(
            {
                "weeks": weeks_list,
                "total_cost_usd": round(total_cost, 2),
                "total_conversations": total_conversations,
            }
        )
    )


def _output_projects_table(week_data: ProjectWeekData, num_weeks: int) -> None:
    sorted_weeks = sorted(week_data.keys(), reverse=True)[:num_weeks]

    now = datetime.now()
    current_week_key = get_week_start(now).isoformat()

    lines: list[str] = []
    grand_cost = 0.0
    grand_count = 0

    for week_idx, week_key in enumerate(sorted_weeks):
        try:
            week_start_dt = datetime.fromisoformat(week_key)
        except ValueError:
            continue

        is_current = week_key == current_week_key
        week_date_str = week_start_dt.strftime("%Y-%m-%d")
        header = f"Week of {week_date_str}" + (" (current)" if is_current else "")
        if is_current:
            header = formatters.colored(header, formatters.GREEN)
        lines.append(header)

        days_dict = week_data[week_key]
        sorted_days = sorted(days_dict.keys(), reverse=True)

        week_cost = 0.0
        week_count = 0

        for day_key in sorted_days:
            try:
                day_dt = datetime.fromisoformat(day_key)
                dow = day_dt.strftime("%a")
            except ValueError:
                dow = "???"

            projects = days_dict[day_key]
            day_cost = sum(c for c, _ in projects.values())
            day_count = sum(n for _, n in projects.values())
            week_cost += day_cost
            week_count += day_count

            # Day header line
            cost_str = formatters.format_cost(day_cost)
            lines.append(f"  {dow} {day_key}  {cost_str:>8}  {day_count} conversations")

            # Per-project breakdown under the day
            for proj in sorted(projects, key=lambda p: projects[p][0], reverse=True):
                pcost, pcount = projects[proj]
                pcost_str = formatters.format_cost(pcost)
                pname = _friendly_project(proj)
                raw_key = formatters.colored(f"({proj})", formatters.DIM)
                lines.append(
                    f"      {pname:<30} {pcost_str:>8}  {pcount} conv  {raw_key}"
                )

        grand_cost += week_cost
        grand_count += week_count

        subtotal_str = formatters.format_cost(week_cost)
        sub_line = f"  Subtotal      {subtotal_str:>8}  {week_count} conversations"
        lines.append(formatters.colored(sub_line, formatters.BOLD))

        if week_idx < len(sorted_weeks) - 1:
            lines.append("")

    grand_str = formatters.format_cost(grand_cost)
    grand_line = f"Grand total    {grand_str:>8}  {grand_count} conversations"
    lines.append("")
    lines.append(formatters.colored(grand_line, formatters.BOLD))

    print("\n".join(lines))
