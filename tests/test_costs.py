"""Comprehensive tests for cchat.costs module."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import pytest

from cchat.costs import CostCache, compute_file_cost, cost_for_usage, get_rates


def write_jsonl(tmp_path: Path, lines, filename: str = "test.jsonl") -> Path:
    """Write a list of items to a JSONL file."""
    p = tmp_path / filename
    with open(p, "w", encoding="utf-8") as fh:
        for item in lines:
            if isinstance(item, str):
                fh.write(item + "\n")
            else:
                fh.write(json.dumps(item) + "\n")
    return p


def make_assistant(
    msg_id: Optional[str] = None,
    stop_reason: Optional[str] = None,
    content: Optional[list] = None,
    usage: Optional[dict] = None,
    model: Optional[str] = None,
    extra_msg: Optional[dict] = None,
) -> dict:
    """Build a typical assistant line dict."""
    msg = {}
    if msg_id is not None:
        msg["id"] = msg_id
    if stop_reason is not None:
        msg["stop_reason"] = stop_reason
    if content is not None:
        msg["content"] = content
    if usage is not None:
        msg["usage"] = usage
    if model is not None:
        msg["model"] = model
    if extra_msg:
        msg.update(extra_msg)
    return {"type": "assistant", "message": msg}


def make_user(content=None) -> dict:
    """Build a typical user line dict."""
    d: dict[str, object] = {"type": "user"}
    if content is not None:
        d["message"] = {"content": content}
    return d


# ===========================================================================
# get_rates
# ===========================================================================


class TestGetRates:
    def test_exact_match_opus(self):
        rates = get_rates("claude-opus-4-6")
        assert rates["input"] == 5.00
        assert rates["output"] == 25.00
        assert rates["cache_read"] == 0.50
        assert rates["cache_create_5m"] == 6.25
        assert rates["cache_create_1h"] == 10.00

    def test_exact_match_sonnet(self):
        rates = get_rates("claude-sonnet-4-6")
        assert rates["input"] == 3.00
        assert rates["output"] == 15.00

    def test_exact_match_haiku(self):
        rates = get_rates("claude-haiku-4-5-20251001")
        assert rates["input"] == 1.00
        assert rates["output"] == 5.00

    def test_exact_match_old_opus(self):
        rates = get_rates("claude-opus-4-20250514")
        assert rates["input"] == 15.00
        assert rates["output"] == 75.00

    def test_exact_match_old_sonnet(self):
        rates = get_rates("claude-sonnet-4-5-20250514")
        assert rates["input"] == 3.00
        assert rates["output"] == 15.00

    def test_exact_match_claude_3_5_sonnet(self):
        rates = get_rates("claude-3-5-sonnet-20241022")
        assert rates["input"] == 3.00
        assert rates["output"] == 15.00

    def test_exact_match_claude_3_5_haiku(self):
        rates = get_rates("claude-3-5-haiku-20241022")
        assert rates["input"] == 1.00
        assert rates["output"] == 5.00

    def test_substring_match(self):
        # "sonnet-4-6" should match "claude-sonnet-4-6"
        rates = get_rates("sonnet-4-6")
        assert rates["input"] == 3.00
        assert rates["output"] == 15.00

    def test_none_fallback(self):
        rates = get_rates(None)
        assert rates["input"] == 3.00  # Sonnet 4.6 fallback
        assert rates["output"] == 15.00

    def test_unknown_model_fallback(self):
        rates = get_rates("claude-unknown-xyz-12345")
        assert rates["input"] == 3.00  # Sonnet 4.6 fallback
        assert rates["output"] == 15.00


# ===========================================================================
# cost_for_usage
# ===========================================================================


class TestCostForUsage:
    def test_simple_usage_no_cache(self):
        usage = {
            "input_tokens": 1_000_000,
            "output_tokens": 1_000_000,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
        }
        rates = get_rates("claude-sonnet-4-6")
        cost = cost_for_usage(usage, rates)
        # (1M * 3 + 1M * 15) / 1M = 3 + 15 = 18
        assert cost == pytest.approx(18.0, abs=0.01)

    def test_usage_with_cache_read(self):
        usage = {
            "input_tokens": 1_000_000,
            "output_tokens": 0,
            "cache_read_input_tokens": 1_000_000,
            "cache_creation_input_tokens": 0,
        }
        rates = get_rates("claude-sonnet-4-6")
        cost = cost_for_usage(usage, rates)
        # (1M * 3 + 1M * 0.30) / 1M = 3 + 0.30 = 3.30
        assert cost == pytest.approx(3.30, abs=0.01)

    def test_usage_with_cache_creation_simple(self):
        usage = {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 1_000_000,
        }
        rates = get_rates("claude-sonnet-4-6")
        cost = cost_for_usage(usage, rates)
        # (1M * 3.75) / 1M = 3.75 (falls back to 5m rate)
        assert cost == pytest.approx(3.75, abs=0.01)

    def test_usage_with_cache_creation_breakdown(self):
        usage = {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 2_000_000,  # ignored if breakdown exists
            "cache_creation": {
                "ephemeral_5m_input_tokens": 1_000_000,
                "ephemeral_1h_input_tokens": 1_000_000,
            },
        }
        rates = get_rates("claude-sonnet-4-6")
        cost = cost_for_usage(usage, rates)
        # (1M * 3.75 + 1M * 6.00) / 1M = 3.75 + 6.00 = 9.75
        assert cost == pytest.approx(9.75, abs=0.01)

    def test_usage_with_cache_creation_only_5m(self):
        usage = {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
            "cache_creation": {
                "ephemeral_5m_input_tokens": 1_000_000,
                "ephemeral_1h_input_tokens": 0,
            },
        }
        rates = get_rates("claude-sonnet-4-6")
        cost = cost_for_usage(usage, rates)
        # (1M * 3.75) / 1M = 3.75
        assert cost == pytest.approx(3.75, abs=0.01)

    def test_usage_with_cache_creation_only_1h(self):
        usage = {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
            "cache_creation": {
                "ephemeral_5m_input_tokens": 0,
                "ephemeral_1h_input_tokens": 1_000_000,
            },
        }
        rates = get_rates("claude-sonnet-4-6")
        cost = cost_for_usage(usage, rates)
        # (1M * 6.00) / 1M = 6.00
        assert cost == pytest.approx(6.00, abs=0.01)

    def test_zero_usage(self):
        usage = {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
        }
        rates = get_rates("claude-sonnet-4-6")
        cost = cost_for_usage(usage, rates)
        assert cost == 0.0

    def test_complex_usage(self):
        usage = {
            "input_tokens": 500_000,
            "output_tokens": 200_000,
            "cache_read_input_tokens": 300_000,
            "cache_creation_input_tokens": 0,
        }
        rates = get_rates("claude-sonnet-4-6")
        cost = cost_for_usage(usage, rates)
        # (500k * 3 + 200k * 15 + 300k * 0.30) / 1M
        # = (1.5 + 3.0 + 0.09) / 1 = 4.59
        assert cost == pytest.approx(4.59, abs=0.01)


# ===========================================================================
# compute_file_cost
# ===========================================================================


class TestComputeFileCost:
    def test_no_usage_data(self, tmp_path):
        # File with no assistant messages with usage
        lines = [
            make_user("hello"),
            make_assistant(msg_id="a1", stop_reason="end_turn"),  # no usage
        ]
        p = write_jsonl(tmp_path, lines)
        cost, tokens = compute_file_cost(p)
        assert cost == 0.0
        assert tokens == 0

    def test_single_turn_cost(self, tmp_path):
        lines = [
            make_user("hello"),
            make_assistant(
                msg_id="a1",
                stop_reason="end_turn",
                model="claude-sonnet-4-6",
                usage={
                    "input_tokens": 1_000_000,
                    "output_tokens": 1_000_000,
                    "cache_read_input_tokens": 0,
                    "cache_creation_input_tokens": 0,
                },
            ),
        ]
        p = write_jsonl(tmp_path, lines)
        cost, tokens = compute_file_cost(p)
        # Sonnet: (1M * 3 + 1M * 15) / 1M = 18
        assert cost == pytest.approx(18.0, abs=0.01)
        assert tokens == 2_000_000

    def test_multiple_turns_sum(self, tmp_path):
        lines = [
            make_user("hello"),
            make_assistant(
                msg_id="a1",
                stop_reason="end_turn",
                model="claude-sonnet-4-6",
                usage={
                    "input_tokens": 1_000_000,
                    "output_tokens": 0,
                    "cache_read_input_tokens": 0,
                    "cache_creation_input_tokens": 0,
                },
            ),
            make_user("continue"),
            make_assistant(
                msg_id="a2",
                stop_reason="end_turn",
                model="claude-sonnet-4-6",
                usage={
                    "input_tokens": 0,
                    "output_tokens": 1_000_000,
                    "cache_read_input_tokens": 0,
                    "cache_creation_input_tokens": 0,
                },
            ),
        ]
        p = write_jsonl(tmp_path, lines)
        cost, tokens = compute_file_cost(p)
        # Turn 1: 1M * 3 / 1M = 3.00
        # Turn 2: 1M * 15 / 1M = 15.00
        # Total: 18.00
        assert cost == pytest.approx(18.0, abs=0.01)
        assert tokens == 2_000_000

    def test_different_models(self, tmp_path):
        lines = [
            make_user("hello"),
            make_assistant(
                msg_id="a1",
                stop_reason="end_turn",
                model="claude-opus-4-6",
                usage={
                    "input_tokens": 1_000_000,
                    "output_tokens": 0,
                    "cache_read_input_tokens": 0,
                    "cache_creation_input_tokens": 0,
                },
            ),
            make_user("continue"),
            make_assistant(
                msg_id="a2",
                stop_reason="end_turn",
                model="claude-haiku-4-5-20251001",
                usage={
                    "input_tokens": 0,
                    "output_tokens": 1_000_000,
                    "cache_read_input_tokens": 0,
                    "cache_creation_input_tokens": 0,
                },
            ),
        ]
        p = write_jsonl(tmp_path, lines)
        cost, tokens = compute_file_cost(p)
        # Turn 1 (Opus): 1M * 5 / 1M = 5.00
        # Turn 2 (Haiku): 1M * 5 / 1M = 5.00
        # Total: 10.00
        assert cost == pytest.approx(10.0, abs=0.01)
        assert tokens == 2_000_000

    def test_deduplication(self, tmp_path):
        # Stream the same message multiple times (like in real JSONL)
        lines = [
            make_user("hello"),
            make_assistant(
                msg_id="a1",
                model="claude-sonnet-4-6",
                usage={
                    "input_tokens": 500_000,
                    "output_tokens": 0,
                    "cache_read_input_tokens": 0,
                    "cache_creation_input_tokens": 0,
                },
            ),
            make_assistant(
                msg_id="a1",  # Same ID
                model="claude-sonnet-4-6",
                usage={
                    "input_tokens": 500_000,
                    "output_tokens": 0,
                    "cache_read_input_tokens": 0,
                    "cache_creation_input_tokens": 0,
                },
            ),
            make_assistant(
                msg_id="a1",
                stop_reason="end_turn",
                model="claude-sonnet-4-6",
                usage={
                    "input_tokens": 500_000,
                    "output_tokens": 500_000,
                    "cache_read_input_tokens": 0,
                    "cache_creation_input_tokens": 0,
                },
            ),
        ]
        p = write_jsonl(tmp_path, lines)
        cost, tokens = compute_file_cost(p)
        # Only the last occurrence is counted (deduped)
        # (500k * 3 + 500k * 15) / 1M = (1.5 + 7.5) / 1 = 9.0
        assert cost == pytest.approx(9.0, abs=0.01)
        assert tokens == 1_000_000


# ===========================================================================
# CostCache
# ===========================================================================


class TestCostCache:
    def test_set_and_get(self, tmp_path, monkeypatch):
        # Use tmp_path for cache
        cache_dir = tmp_path / ".claude"
        cache_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv("HOME", str(tmp_path))

        cache = CostCache()
        cache.cache_path = cache_dir / "cchat-costs.json"

        uuid = "test-uuid-1234"
        mtime = 1234567890.0
        size = 5000
        cost = 25.50

        cache.set(uuid, mtime, size, cost, 50000)
        assert cache.get(uuid, mtime, size) == (cost, 50000)

    def test_get_with_stale_mtime(self, tmp_path, monkeypatch):
        cache_dir = tmp_path / ".claude"
        cache_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv("HOME", str(tmp_path))

        cache = CostCache()
        cache.cache_path = cache_dir / "cchat-costs.json"

        uuid = "test-uuid-1234"
        mtime = 1234567890.0
        size = 5000
        cost = 25.50

        cache.set(uuid, mtime, size, cost)
        # Get with different mtime
        result = cache.get(uuid, 9999999.0, size)
        assert result is None

    def test_get_with_stale_size(self, tmp_path, monkeypatch):
        cache_dir = tmp_path / ".claude"
        cache_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv("HOME", str(tmp_path))

        cache = CostCache()
        cache.cache_path = cache_dir / "cchat-costs.json"

        uuid = "test-uuid-1234"
        mtime = 1234567890.0
        size = 5000
        cost = 25.50

        cache.set(uuid, mtime, size, cost)
        # Get with different size
        result = cache.get(uuid, mtime, 9999)
        assert result is None

    def test_get_nonexistent(self, tmp_path, monkeypatch):
        cache_dir = tmp_path / ".claude"
        cache_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv("HOME", str(tmp_path))

        cache = CostCache()
        cache.cache_path = cache_dir / "cchat-costs.json"

        result = cache.get("nonexistent", 1234567890.0, 5000)
        assert result is None

    def test_save_and_load_roundtrip(self, tmp_path, monkeypatch):
        cache_dir = tmp_path / ".claude"
        cache_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv("HOME", str(tmp_path))

        # Create and save
        cache1 = CostCache()
        cache1.cache_path = cache_dir / "cchat-costs.json"
        cache1.set("uuid-1", 1000.0, 5000, 10.50, 100000)
        cache1.set("uuid-2", 2000.0, 6000, 20.75, 200000)
        cache1.save()

        # Load and verify - create new instance which loads from disk
        cache2 = CostCache()
        cache2.cache_path = cache_dir / "cchat-costs.json"
        cache2._load()  # Explicitly load from the file we just saved
        assert cache2.get("uuid-1", 1000.0, 5000) == (10.50, 100000)
        assert cache2.get("uuid-2", 2000.0, 6000) == (20.75, 200000)

    def test_cache_file_format(self, tmp_path, monkeypatch):
        cache_dir = tmp_path / ".claude"
        cache_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv("HOME", str(tmp_path))

        cache = CostCache()
        cache.cache_path = cache_dir / "cchat-costs.json"
        cache.set("uuid-1", 1000.0, 5000, 10.50)
        cache.save()

        # Verify the file format
        with open(cache.cache_path, "r") as f:
            data = json.load(f)
        assert data["version"] == 1
        assert "uuid-1" in data["entries"]
        assert data["entries"]["uuid-1"]["cost_usd"] == 10.50
        assert data["entries"]["uuid-1"]["mtime"] == 1000.0
        assert data["entries"]["uuid-1"]["size"] == 5000

    def test_replace_entry(self, tmp_path, monkeypatch):
        cache_dir = tmp_path / ".claude"
        cache_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv("HOME", str(tmp_path))

        cache = CostCache()
        cache.cache_path = cache_dir / "cchat-costs.json"

        uuid = "uuid-1"
        # Set with first values
        cache.set(uuid, 1000.0, 5000, 10.50, 100000)
        assert cache.get(uuid, 1000.0, 5000) == (10.50, 100000)

        # Replace with new values
        cache.set(uuid, 2000.0, 6000, 20.75, 200000)
        assert cache.get(uuid, 2000.0, 6000) == (20.75, 200000)
        assert cache.get(uuid, 1000.0, 5000) is None

    def test_dirty_flag(self, tmp_path, monkeypatch):
        cache_dir = tmp_path / ".claude"
        cache_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv("HOME", str(tmp_path))

        cache = CostCache()
        cache.cache_path = cache_dir / "cchat-costs.json"

        assert cache.dirty is False
        cache.set("uuid-1", 1000.0, 5000, 10.50)
        assert cache.dirty is True
        cache.save()
        assert cache.dirty is False
