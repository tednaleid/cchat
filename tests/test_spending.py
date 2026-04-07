"""Tests for the spending command."""

from __future__ import annotations

import argparse
import json
import uuid as uuid_mod
from datetime import datetime, timedelta


from cchat.commands import spending_cmd


# Helper functions (copied from test_commands.py)
_COUNTER = 0


def _next_ts(base: str = "2025-06-15T10:00:00") -> str:
    """Return incrementing timestamps so sorting is deterministic."""
    global _COUNTER
    _COUNTER += 1
    # Increment minutes
    h, m = divmod(_COUNTER, 60)
    return f"2025-06-15T{10 + h:02d}:{m:02d}:00Z"


def _uuid() -> str:
    return str(uuid_mod.uuid4())


def make_system_line(
    subtype: str = "bridge_status",
    *,
    uuid_val: str | None = None,
    parent_uuid: str | None = None,
    timestamp: str | None = None,
    slug: str | None = None,
    **extra,
) -> dict:
    line = {
        "type": "system",
        "subtype": subtype,
        "uuid": uuid_val or _uuid(),
        "parentUuid": parent_uuid or "",
        "timestamp": timestamp or _next_ts(),
    }
    if slug is not None:
        line["slug"] = slug
    line.update(extra)
    return line


def make_user_line(
    content: str,
    *,
    uuid_val: str | None = None,
    parent_uuid: str | None = None,
    timestamp: str | None = None,
    **extra,
) -> dict:
    line = {
        "type": "user",
        "uuid": uuid_val or _uuid(),
        "parentUuid": parent_uuid or "",
        "timestamp": timestamp or _next_ts(),
        "message": {"content": content},
    }
    line.update(extra)
    return line


def make_assistant_line(
    text: str | None = None,
    *,
    tool_uses: list[dict] | None = None,
    message_id: str | None = None,
    usage: dict | None = None,
    uuid_val: str | None = None,
    parent_uuid: str | None = None,
    timestamp: str | None = None,
    stop_reason: str | None = "end_turn",
    **extra,
) -> dict:
    content_items: list[dict] = []
    if text is not None:
        content_items.append({"type": "text", "text": text})
    if tool_uses:
        for tu in tool_uses:
            content_items.append(tu)

    msg: dict = {
        "id": message_id or _uuid(),
        "content": content_items,
    }
    if stop_reason:
        msg["stop_reason"] = stop_reason
    if usage:
        msg["usage"] = usage

    line = {
        "type": "assistant",
        "uuid": uuid_val or _uuid(),
        "parentUuid": parent_uuid or "",
        "timestamp": timestamp or _next_ts(),
        "message": msg,
    }
    line.update(extra)
    return line


class TestGetWeekStart:
    """Tests for the get_week_start() function."""

    def test_wednesday_at_5pm_returns_self(self):
        """Wednesday at 5:00 PM should return itself."""
        dt = datetime(2026, 3, 25, 17, 0, 0)  # Wed 5:00 PM
        result = spending_cmd.get_week_start(dt)
        assert result == dt

    def test_wednesday_after_5pm_returns_self(self):
        """Wednesday after 5:00 PM should return the same day at 5 PM."""
        dt = datetime(2026, 3, 25, 18, 30, 0)  # Wed 6:30 PM
        result = spending_cmd.get_week_start(dt)
        assert result == datetime(2026, 3, 25, 17, 0, 0)

    def test_wednesday_before_5pm_returns_previous_week(self):
        """Wednesday before 5:00 PM should return the previous Wednesday at 5 PM."""
        dt = datetime(2026, 3, 25, 16, 0, 0)  # Wed 4:00 PM
        result = spending_cmd.get_week_start(dt)
        # Should go back 7 days to previous Wednesday 5 PM
        expected = datetime(2026, 3, 18, 17, 0, 0)
        assert result == expected

    def test_thursday_returns_wednesday_5pm(self):
        """Thursday should return that week's Wednesday at 5 PM."""
        dt = datetime(2026, 3, 26, 10, 0, 0)  # Thu 10:00 AM
        result = spending_cmd.get_week_start(dt)
        expected = datetime(2026, 3, 25, 17, 0, 0)
        assert result == expected

    def test_monday_returns_wednesday_5pm(self):
        """Monday should return the previous Wednesday at 5 PM."""
        dt = datetime(2026, 3, 23, 10, 0, 0)  # Mon 10:00 AM
        result = spending_cmd.get_week_start(dt)
        expected = datetime(2026, 3, 18, 17, 0, 0)
        assert result == expected

    def test_tuesday_returns_previous_wednesday_5pm(self):
        """Tuesday should return the previous Wednesday at 5 PM."""
        dt = datetime(2026, 3, 24, 10, 0, 0)  # Tue 10:00 AM
        result = spending_cmd.get_week_start(dt)
        expected = datetime(2026, 3, 18, 17, 0, 0)
        assert result == expected

    def test_sunday_returns_previous_wednesday_5pm(self):
        """Sunday should return the previous Wednesday at 5 PM."""
        dt = datetime(2026, 3, 29, 10, 0, 0)  # Sun 10:00 AM
        result = spending_cmd.get_week_start(dt)
        expected = datetime(2026, 3, 25, 17, 0, 0)
        assert result == expected


class TestSpendingCmd:
    """Tests for the spending command."""

    def _args(self, **overrides) -> argparse.Namespace:
        defaults = dict(
            weeks=4,
            project=None,
            projects=False,
            json=False,
            no_color=True,
        )
        defaults.update(overrides)
        return argparse.Namespace(**defaults)

    def test_no_conversations(self, projects_dir, capsys):
        """Test with no conversations."""
        spending_cmd.run(self._args())
        captured = capsys.readouterr()
        assert "No conversations found" in captured.out

    def test_no_conversations_json(self, projects_dir, capsys):
        """Test JSON output with no conversations."""
        spending_cmd.run(self._args(json=True))
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data == {
            "weeks": [],
            "total_cost_usd": 0.0,
            "total_conversations": 0,
        }

    def test_single_conversation_single_week(self, projects_dir, make_conversation, capsys):
        """Test single conversation in a single week."""
        # Create a conversation on Monday
        make_conversation(
            [
                make_system_line(timestamp="2026-03-30T10:00:00Z"),  # Mon
                make_user_line("Test", timestamp="2026-03-30T10:01:00Z"),
                make_assistant_line(
                    "Response",
                    timestamp="2026-03-30T10:02:00Z",
                    usage={
                        "input_tokens": 1000,
                        "output_tokens": 500,
                        "cache_read_input_tokens": 0,
                        "cache_creation_input_tokens": 0,
                    },
                ),
            ],
        )

        spending_cmd.run(self._args(no_color=True))
        captured = capsys.readouterr()

        # Should contain week header and data
        # 2026-03-30 is Monday, so the week starts on Wed 2026-03-25
        assert "Week of 2026-03-25" in captured.out
        assert "2026-03-30" in captured.out
        assert "Mon" in captured.out

    def test_multiple_conversations_same_day(
        self, projects_dir, make_conversation, capsys
    ):
        """Test multiple conversations on the same day."""
        # Create two conversations on the same day
        for i in range(2):
            make_conversation(
                [
                    make_system_line(timestamp="2026-03-30T10:00:00Z"),  # Mon
                    make_user_line(f"Test {i}", timestamp="2026-03-30T10:01:00Z"),
                    make_assistant_line(
                        f"Response {i}",
                        timestamp="2026-03-30T10:02:00Z",
                        usage={
                            "input_tokens": 1000,
                            "output_tokens": 500,
                            "cache_read_input_tokens": 0,
                            "cache_creation_input_tokens": 0,
                        },
                    ),
                ],
            )

        spending_cmd.run(self._args(no_color=True))
        captured = capsys.readouterr()

        # Should show 2 conversations for that day
        assert "2 conversations" in captured.out

    def test_conversations_across_days(self, projects_dir, make_conversation, capsys):
        """Test conversations across different days in a week."""
        # Create conversations on different days
        days = [
            "2026-03-30",  # Mon
            "2026-03-31",  # Tue
        ]

        for day in days:
            make_conversation(
                [
                    make_system_line(timestamp=f"{day}T10:00:00Z"),
                    make_user_line("Test", timestamp=f"{day}T10:01:00Z"),
                    make_assistant_line(
                        "Response",
                        timestamp=f"{day}T10:02:00Z",
                        usage={
                            "input_tokens": 1000,
                            "output_tokens": 500,
                            "cache_read_input_tokens": 0,
                            "cache_creation_input_tokens": 0,
                        },
                    ),
                ],
            )

        spending_cmd.run(self._args(no_color=True))
        captured = capsys.readouterr()

        # Should contain both days
        assert "2026-03-30" in captured.out
        assert "2026-03-31" in captured.out

    def test_week_boundary_wednesday_before_5pm(
        self, projects_dir, make_conversation, capsys
    ):
        """Test that conversations on Wed before 5 PM go to previous week."""
        # Create conversation on Wed at 4:59 PM -> should be in previous week
        make_conversation(
            [
                make_system_line(timestamp="2026-03-25T16:59:00Z"),
                make_user_line("Test", timestamp="2026-03-25T17:00:00Z"),
                make_assistant_line(
                    "Response",
                    timestamp="2026-03-25T17:01:00Z",
                    usage={
                        "input_tokens": 1000,
                        "output_tokens": 500,
                        "cache_read_input_tokens": 0,
                        "cache_creation_input_tokens": 0,
                    },
                ),
            ],
        )

        spending_cmd.run(self._args(no_color=True))
        captured = capsys.readouterr()

        # Should be in week of 2026-03-18, not 2026-03-25
        assert "Week of 2026-03-18" in captured.out

    def test_week_boundary_wednesday_after_5pm(
        self, projects_dir, make_conversation, capsys
    ):
        """Test that conversations on Wed at/after 5 PM go to current week."""
        # Create conversation on Wed at 5:01 PM -> should be in current week
        make_conversation(
            [
                make_system_line(timestamp="2026-03-25T17:01:00Z"),
                make_user_line("Test", timestamp="2026-03-25T17:02:00Z"),
                make_assistant_line(
                    "Response",
                    timestamp="2026-03-25T17:03:00Z",
                    usage={
                        "input_tokens": 1000,
                        "output_tokens": 500,
                        "cache_read_input_tokens": 0,
                        "cache_creation_input_tokens": 0,
                    },
                ),
            ],
        )

        spending_cmd.run(self._args(no_color=True))
        captured = capsys.readouterr()

        # Should be in week of 2026-03-25
        assert "Week of 2026-03-25" in captured.out

    def test_json_output_structure(self, projects_dir, make_conversation, capsys):
        """Test JSON output structure."""
        # Create a conversation
        make_conversation(
            [
                make_system_line(timestamp="2026-03-30T10:00:00Z"),
                make_user_line("Test", timestamp="2026-03-30T10:01:00Z"),
                make_assistant_line(
                    "Response",
                    timestamp="2026-03-30T10:02:00Z",
                    usage={
                        "input_tokens": 1000,
                        "output_tokens": 500,
                        "cache_read_input_tokens": 0,
                        "cache_creation_input_tokens": 0,
                    },
                ),
            ],
        )

        spending_cmd.run(self._args(json=True))
        captured = capsys.readouterr()
        data = json.loads(captured.out)

        # Verify structure
        assert "weeks" in data
        assert "total_cost_usd" in data
        assert "total_conversations" in data
        assert isinstance(data["weeks"], list)

        # Check week structure
        if data["weeks"]:
            week = data["weeks"][0]
            assert "week_start" in week
            assert "current" in week
            assert "days" in week
            assert "subtotal_cost_usd" in week
            assert "subtotal_conversations" in week

            # Check day structure
            if week["days"]:
                day = week["days"][0]
                assert "date" in day
                assert "cost_usd" in day
                assert "conversations" in day

    def test_weeks_limit(self, projects_dir, make_conversation, capsys):
        """Test that --weeks limit is respected."""
        # Create conversations in different weeks
        base_date = datetime(2026, 3, 26)  # Wed 5 PM start of a week
        for week_offset in range(8):
            day = base_date + timedelta(days=week_offset * 7 + 1)  # Thursday of each week
            timestamp = day.isoformat() + "Z"
            make_conversation(
                [
                    make_system_line(timestamp=timestamp),
                    make_user_line("Test", timestamp=timestamp),
                    make_assistant_line(
                        "Response",
                        timestamp=timestamp,
                        usage={
                            "input_tokens": 1000,
                            "output_tokens": 500,
                            "cache_read_input_tokens": 0,
                            "cache_creation_input_tokens": 0,
                        },
                    ),
                ],
            )

        spending_cmd.run(self._args(weeks=2, json=True))
        captured = capsys.readouterr()
        data = json.loads(captured.out)

        # Should only have 2 weeks
        assert len(data["weeks"]) <= 2

    def test_project_filter(self, projects_dir, make_conversation, capsys):
        """Test that --project filter works."""
        # Create conversations in different projects
        make_conversation(
            [
                make_system_line(timestamp="2026-03-30T10:00:00Z"),
                make_user_line("Test", timestamp="2026-03-30T10:01:00Z"),
                make_assistant_line(
                    "Response",
                    timestamp="2026-03-30T10:02:00Z",
                    usage={
                        "input_tokens": 1000,
                        "output_tokens": 500,
                        "cache_read_input_tokens": 0,
                        "cache_creation_input_tokens": 0,
                    },
                ),
            ],
            project_key="project-a",
        )
        make_conversation(
            [
                make_system_line(timestamp="2026-03-30T10:00:00Z"),
                make_user_line("Test", timestamp="2026-03-30T10:01:00Z"),
                make_assistant_line(
                    "Response",
                    timestamp="2026-03-30T10:02:00Z",
                    usage={
                        "input_tokens": 1000,
                        "output_tokens": 500,
                        "cache_read_input_tokens": 0,
                        "cache_creation_input_tokens": 0,
                    },
                ),
            ],
            project_key="project-b",
        )

        spending_cmd.run(self._args(project="project-a", json=True))
        captured = capsys.readouterr()
        data = json.loads(captured.out)

        # Should only have 1 conversation total
        assert data["total_conversations"] == 1

    def test_conversation_without_timestamp(self, projects_dir, make_conversation, capsys):
        """Test that conversations without timestamps are skipped gracefully."""
        # Create a conversation without a timestamp in the first message
        make_conversation(
            [
                {"type": "system", "subtype": "bridge_status"},  # No timestamp
                make_user_line("Test", timestamp="2026-03-30T10:01:00Z"),
            ],
        )

        # Should not crash
        spending_cmd.run(self._args(json=True))
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "weeks" in data

    def test_subtotal_bold_in_table(self, projects_dir, make_conversation, capsys):
        """Test that subtotal rows are marked as bold in table output."""
        make_conversation(
            [
                make_system_line(timestamp="2026-03-30T10:00:00Z"),
                make_user_line("Test", timestamp="2026-03-30T10:01:00Z"),
                make_assistant_line(
                    "Response",
                    timestamp="2026-03-30T10:02:00Z",
                    usage={
                        "input_tokens": 1000,
                        "output_tokens": 500,
                        "cache_read_input_tokens": 0,
                        "cache_creation_input_tokens": 0,
                    },
                ),
            ],
        )

        spending_cmd.run(self._args(no_color=True))
        captured = capsys.readouterr()

        # Should contain "Subtotal"
        assert "Subtotal" in captured.out

    def test_grand_total_shown(self, projects_dir, make_conversation, capsys):
        """Test that grand total is shown."""
        make_conversation(
            [
                make_system_line(timestamp="2026-03-30T10:00:00Z"),
                make_user_line("Test", timestamp="2026-03-30T10:01:00Z"),
                make_assistant_line(
                    "Response",
                    timestamp="2026-03-30T10:02:00Z",
                    usage={
                        "input_tokens": 1000,
                        "output_tokens": 500,
                        "cache_read_input_tokens": 0,
                        "cache_creation_input_tokens": 0,
                    },
                ),
            ],
        )

        spending_cmd.run(self._args(no_color=True))
        captured = capsys.readouterr()

        # Should contain "Grand total"
        assert "Grand total" in captured.out

    def test_invalid_timestamp_skipped(self, projects_dir, make_conversation, capsys):
        """Test that conversations with invalid timestamps are skipped gracefully."""
        # Create conversation with invalid ISO format
        make_conversation(
            [
                make_system_line(timestamp="not-a-timestamp"),
                make_user_line("Test", timestamp="2026-03-30T10:01:00Z"),
            ],
        )

        spending_cmd.run(self._args(json=True))
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        # Should handle gracefully without crashing
        assert "weeks" in data
