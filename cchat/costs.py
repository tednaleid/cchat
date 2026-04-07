"""Model-aware pricing and cost computation for conversations."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from cchat import parser


@dataclass
class TokenBreakdown:
    """Per-type token totals for a conversation or period."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_create_5m_tokens: int = 0
    cache_create_1h_tokens: int = 0

    def __iadd__(self, other: "TokenBreakdown") -> "TokenBreakdown":
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cache_read_tokens += other.cache_read_tokens
        self.cache_create_5m_tokens += other.cache_create_5m_tokens
        self.cache_create_1h_tokens += other.cache_create_1h_tokens
        return self

# Pricing rates per million tokens
RATES = {
    "claude-opus-4-6": {
        "input": 5.00,
        "output": 25.00,
        "cache_read": 0.50,
        "cache_create_5m": 6.25,
        "cache_create_1h": 10.00,
    },
    "claude-sonnet-4-6": {
        "input": 3.00,
        "output": 15.00,
        "cache_read": 0.30,
        "cache_create_5m": 3.75,
        "cache_create_1h": 6.00,
    },
    "claude-haiku-4-5-20251001": {
        "input": 1.00,
        "output": 5.00,
        "cache_read": 0.10,
        "cache_create_5m": 1.25,
        "cache_create_1h": 2.00,
    },
    "claude-opus-4-20250514": {
        "input": 15.00,
        "output": 75.00,
        "cache_read": 1.50,
        "cache_create_5m": 18.75,
        "cache_create_1h": 22.50,
    },
    "claude-sonnet-4-5-20250514": {
        "input": 3.00,
        "output": 15.00,
        "cache_read": 0.30,
        "cache_create_5m": 3.75,
        "cache_create_1h": 4.50,
    },
    "claude-3-5-sonnet-20241022": {
        "input": 3.00,
        "output": 15.00,
        "cache_read": 0.30,
        "cache_create_5m": 3.75,
        "cache_create_1h": 4.50,
    },
    "claude-3-5-haiku-20241022": {
        "input": 1.00,
        "output": 5.00,
        "cache_read": 0.10,
        "cache_create_5m": 1.25,
        "cache_create_1h": 1.50,
    },
}

FALLBACK_MODEL = "claude-sonnet-4-6"


def get_rates(model: str | None) -> dict:
    """Get pricing rates for a model.

    Args:
        model: Model ID string, or None for fallback.

    Returns:
        Dict with keys: input, output, cache_read, cache_create_5m, cache_create_1h

    Strategy:
        1. Exact match on model ID
        2. Substring match (model ID is substring of a known key)
        3. Fallback to Sonnet 4.6
    """
    if model is None:
        return RATES[FALLBACK_MODEL]

    # Exact match
    if model in RATES:
        return RATES[model]

    # Substring match
    for known_model, rates in RATES.items():
        if known_model in model or model in known_model:
            return rates

    # Fallback
    return RATES[FALLBACK_MODEL]


def cost_for_usage(usage: dict, rates: dict) -> float:
    """Compute cost for a single turn's usage.

    Args:
        usage: Dict from parser.extract_usage() with keys:
               input_tokens, output_tokens, cache_read_input_tokens,
               cache_creation_input_tokens, and optionally cache_creation dict.
        rates: Dict from get_rates() with pricing per million tokens.

    Returns:
        Cost in USD.
    """
    input_tokens = usage.get("input_tokens", 0)
    output_tokens = usage.get("output_tokens", 0)
    cache_read_tokens = usage.get("cache_read_input_tokens", 0)

    # Handle cache creation with optional tier breakdown
    cache_create_5m = 0
    cache_create_1h = 0
    cache_create_tokens = usage.get("cache_creation_input_tokens", 0)

    cc_obj = usage.get("cache_creation")
    if isinstance(cc_obj, dict):
        cc_5m = cc_obj.get("ephemeral_5m_input_tokens", 0)
        cc_1h = cc_obj.get("ephemeral_1h_input_tokens", 0)
        if cc_5m > 0 or cc_1h > 0:
            cache_create_5m = cc_5m
            cache_create_1h = cc_1h
        else:
            # Fallback: no tier data, charge all at 5m rate
            cache_create_5m = cache_create_tokens
    else:
        # No breakdown dict, charge all at 5m rate
        cache_create_5m = cache_create_tokens

    cost = (
        input_tokens * rates["input"]
        + output_tokens * rates["output"]
        + cache_read_tokens * rates["cache_read"]
        + cache_create_5m * rates["cache_create_5m"]
        + cache_create_1h * rates["cache_create_1h"]
    ) / 1_000_000

    return cost


def compute_file_cost(path: Path) -> tuple[float, int]:
    """Compute total cost and token count for a JSONL conversation file.

    Returns:
        (cost_usd, total_tokens)
    """
    total_cost = 0.0
    total_tokens = 0

    lines = parser.parse_lines(path)
    deduped = parser.deduplicate_assistant_lines(lines)

    for _line_num, data in deduped:
        if data.get("type") != "assistant":
            continue

        model = parser.extract_model(data)
        usage = parser.extract_usage(data)

        if usage is None:
            continue

        rates = get_rates(model)
        turn_cost = cost_for_usage(usage, rates)
        total_cost += turn_cost
        total_tokens += (
            usage.get("input_tokens", 0)
            + usage.get("output_tokens", 0)
            + usage.get("cache_read_input_tokens", 0)
            + usage.get("cache_creation_input_tokens", 0)
        )

    return total_cost, total_tokens


def compute_file_tokens(path: Path) -> TokenBreakdown:
    """Compute per-type token breakdown for a JSONL conversation file.

    Returns:
        TokenBreakdown with input, output, cache_read, cache_create_5m,
        and cache_create_1h token counts.
    """
    bd = TokenBreakdown()
    lines = parser.parse_lines(path)
    deduped = parser.deduplicate_assistant_lines(lines)

    for _line_num, data in deduped:
        if data.get("type") != "assistant":
            continue

        usage = parser.extract_usage(data)
        if usage is None:
            continue

        bd.input_tokens += usage.get("input_tokens", 0)
        bd.output_tokens += usage.get("output_tokens", 0)
        bd.cache_read_tokens += usage.get("cache_read_input_tokens", 0)

        cache_create_tokens = usage.get("cache_creation_input_tokens", 0)
        cc_obj = usage.get("cache_creation")
        if isinstance(cc_obj, dict):
            cc_5m = cc_obj.get("ephemeral_5m_input_tokens", 0)
            cc_1h = cc_obj.get("ephemeral_1h_input_tokens", 0)
            if cc_5m > 0 or cc_1h > 0:
                bd.cache_create_5m_tokens += cc_5m
                bd.cache_create_1h_tokens += cc_1h
            else:
                bd.cache_create_5m_tokens += cache_create_tokens
        else:
            bd.cache_create_5m_tokens += cache_create_tokens

    return bd


def compute_file_model(path: Path) -> str | None:
    """Return the model used most frequently in the file."""
    from collections import Counter
    model_counts: Counter[str] = Counter()
    lines = parser.parse_lines(path)
    deduped = parser.deduplicate_assistant_lines(lines)
    for _line_num, data in deduped:
        if data.get("type") != "assistant":
            continue
        model = parser.extract_model(data)
        if model:
            model_counts[model] += 1
    if not model_counts:
        return None
    return model_counts.most_common(1)[0][0]


class CostCache:
    """Persistent cache for conversation costs."""

    def __init__(self):
        """Initialize cache, loading from disk if it exists."""
        self.cache_path = Path.home() / ".claude" / "cchat-costs.json"
        self.entries: dict = {}
        self.dirty = False
        self._load()

    def _load(self) -> None:
        """Load cache from disk."""
        if not self.cache_path.exists():
            self.entries = {}
            return

        try:
            with open(self.cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.entries = data.get("entries", {})
        except (json.JSONDecodeError, IOError):
            self.entries = {}

    def get(self, uuid: str, mtime: float, size: int) -> tuple[float, int] | None:
        """Get cached cost and tokens if entry exists and mtime+size match.

        Returns:
            (cost_usd, total_tokens) if cached and valid, else None.
        """
        entry = self.entries.get(uuid)
        if entry is None:
            return None

        if entry.get("mtime") != mtime or entry.get("size") != size:
            return None

        cost = entry.get("cost_usd")
        tokens = entry.get("total_tokens")
        if cost is None or tokens is None:
            return None
        return cost, tokens

    def set(self, uuid: str, mtime: float, size: int, cost: float, total_tokens: int = 0) -> None:
        """Set cache entry for a UUID."""
        self.entries[uuid] = {
            "cost_usd": cost,
            "total_tokens": total_tokens,
            "mtime": mtime,
            "size": size,
        }
        self.dirty = True

    def save(self) -> None:
        """Save cache to disk if dirty. Uses atomic write."""
        if not self.dirty:
            return

        # Ensure directory exists
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)

        # Atomic write via .tmp + rename
        tmp_path = self.cache_path.with_suffix(".json.tmp")
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(
                    {"version": 1, "entries": self.entries},
                    f,
                    indent=2,
                )
            tmp_path.replace(self.cache_path)
            self.dirty = False
        except IOError:
            # Silently fail on write errors
            pass
