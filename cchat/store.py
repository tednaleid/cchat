"""Conversation discovery and resolution module."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cchat.costs import CostCache, TokenBreakdown

CLAUDE_DIR = Path.home() / ".claude"
PROJECTS_DIR = CLAUDE_DIR / "projects"
SESSIONS_DIR = CLAUDE_DIR / "sessions"
HISTORY_FILE = CLAUDE_DIR / "history.jsonl"


def _safe_subdirs(parent: Path) -> list[Path]:
    """List subdirectories, skipping symlinks and inaccessible entries."""
    dirs: list[Path] = []
    for entry in parent.iterdir():
        if entry.is_symlink():
            continue
        try:
            if entry.is_dir():
                dirs.append(entry)
        except OSError:
            continue
    return dirs


@dataclass
class ConversationInfo:
    path: Path
    uuid: str
    project_key: str
    size: int
    first_timestamp: str | None = None
    last_timestamp: str | None = None
    slug: str | None = None
    snippet: str | None = None
    turn_count: int = 0
    agent_count: int = 0
    total_tokens: int = 0
    model: str | None = None
    estimated_cost_usd: float | None = None
    session_id: str | None = None
    name: str | None = None
    cwd: str | None = None


@dataclass
class SubagentInfo:
    """Metadata for a single subagent JSONL file."""
    path: Path
    agent_id: str
    conversation_uuid: str
    project_key: str
    size: int
    prompt_snippet: str | None = None
    first_timestamp: str | None = None
    last_timestamp: str | None = None
    line_count: int = 0
    turn_count: int = 0
    total_tokens: int = 0
    model: str | None = None
    estimated_cost_usd: float | None = None


def _parse_line(raw: str) -> dict | None:
    """Parse a single JSONL line, returning None on failure."""
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None


def _is_user_turn(obj: dict) -> bool:
    """Check if this line is a human user message (string content, not tool result)."""
    if obj.get("type") != "user":
        return False
    msg = obj.get("message", {})
    content = msg.get("content") if isinstance(msg, dict) else None
    return isinstance(content, str)


def _is_agent_call(obj: dict) -> bool:
    """Check if this assistant line contains an Agent tool_use call."""
    if obj.get("type") != "assistant":
        return False
    msg = obj.get("message", {})
    if not isinstance(msg, dict):
        return False
    content = msg.get("content", [])
    if not isinstance(content, list):
        return False
    for item in content:
        if isinstance(item, dict) and item.get("type") == "tool_use" and item.get("name") == "Agent":
            return True
    return False


def _extract_snippet(obj: dict) -> str | None:
    """Extract first 60 chars of user message content."""
    msg = obj.get("message", {})
    if not isinstance(msg, dict):
        return None
    content = msg.get("content")
    if isinstance(content, str):
        text = content.strip().replace("\n", " ")
        return text[:60] if len(text) > 60 else text
    return None


def _scan_conversation(path: Path) -> ConversationInfo:
    """Scan a conversation JSONL file for metadata."""
    uuid = path.stem
    project_key = path.parent.name
    size = os.path.getsize(path)

    first_timestamp = None
    last_timestamp = None
    slug = None
    snippet = None
    session_id = None
    cwd = None
    turn_count = 0
    agent_count = 0
    model = None

    # Scan first 100 lines for metadata + start counting
    with open(path, "r", encoding="utf-8") as f:
        for i, raw_line in enumerate(f):
            if i >= 100:
                break
            obj = _parse_line(raw_line)
            if obj is None:
                continue

            ts = obj.get("timestamp")
            if ts:
                if first_timestamp is None:
                    first_timestamp = ts
                last_timestamp = ts

            if slug is None and obj.get("slug"):
                slug = obj["slug"]

            if session_id is None and obj.get("sessionId"):
                session_id = obj["sessionId"]

            if cwd is None and obj.get("cwd"):
                cwd = obj["cwd"]

            if snippet is None and _is_user_turn(obj):
                snippet = _extract_snippet(obj)

            if model is None and obj.get("type") == "assistant":
                msg = obj.get("message") or {}
                if isinstance(msg, dict) and msg.get("model"):
                    model = msg["model"]

            if _is_user_turn(obj):
                turn_count += 1
            if _is_agent_call(obj):
                agent_count += 1

        # Continue scanning remaining lines for counts and last_timestamp
        for raw_line in f:
            obj = _parse_line(raw_line)
            if obj is None:
                continue

            ts = obj.get("timestamp")
            if ts:
                last_timestamp = ts

            if _is_user_turn(obj):
                turn_count += 1
            if _is_agent_call(obj):
                agent_count += 1

    # Optimize last_timestamp: read last 4KB for final timestamp
    if size > 4096:
        try:
            with open(path, "rb") as f:
                f.seek(max(0, size - 4096))
                tail = f.read().decode("utf-8", errors="replace")
            for raw_line in reversed(tail.splitlines()):
                obj = _parse_line(raw_line)
                if obj and obj.get("timestamp"):
                    last_timestamp = obj["timestamp"]
                    break
        except OSError:
            pass

    return ConversationInfo(
        path=path,
        uuid=uuid,
        project_key=project_key,
        size=size,
        first_timestamp=first_timestamp,
        last_timestamp=last_timestamp,
        slug=slug,
        snippet=snippet,
        turn_count=turn_count,
        agent_count=agent_count,
        model=model,
        session_id=session_id,
        cwd=cwd,
    )


def _load_session_names() -> dict[str, str]:
    """Load session names from ~/.claude/sessions/*.json.

    Returns a mapping of sessionId -> name for sessions that have been renamed.
    """
    names: dict[str, str] = {}
    if not SESSIONS_DIR.exists():
        return names
    for path in SESSIONS_DIR.glob("*.json"):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            sid = data.get("sessionId")
            name = data.get("name")
            if sid and name:
                names[sid] = name
        except (json.JSONDecodeError, OSError):
            continue
    return names


def _resolve_project_key(project_key: str) -> str | None:
    """Resolve a project key, trying exact match then substring match.

    Returns the matched directory name, or None if no match found.
    """
    exact = PROJECTS_DIR / project_key
    if exact.exists() and exact.is_dir():
        return project_key

    # Substring match against known project directory names
    needle = project_key.lower()
    candidates = [d.name for d in _safe_subdirs(PROJECTS_DIR)]
    matches = [c for c in candidates if needle in c.lower()]

    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        import sys

        print(
            f"Ambiguous project key '{project_key}' matches:\n"
            + "\n".join(f"  {m}" for m in sorted(matches)),
            file=sys.stderr,
        )
        return None
    return None


def discover_conversations(project_key: str | None = None) -> list[ConversationInfo]:
    """Discover all conversations under ~/.claude/projects/.

    Args:
        project_key: If given, only scan that project subdirectory.
            Supports exact directory names and substring matching.

    Returns:
        List of ConversationInfo for each conversation found.
    """
    if not PROJECTS_DIR.exists():
        return []

    results: list[ConversationInfo] = []

    if project_key:
        resolved = _resolve_project_key(project_key)
        if resolved is None:
            return []
        search_dirs = [PROJECTS_DIR / resolved]
    else:
        search_dirs = _safe_subdirs(PROJECTS_DIR)

    for proj_dir in search_dirs:
        if not proj_dir.exists():
            continue
        for jsonl_file in proj_dir.glob("*.jsonl"):
            # Skip files in subagents/ subdirectories
            if "subagents" in jsonl_file.parts:
                continue
            results.append(_scan_conversation(jsonl_file))

    # Populate session names from ~/.claude/sessions/*.json
    session_names = _load_session_names()
    for conv in results:
        if conv.session_id and conv.session_id in session_names:
            conv.name = session_names[conv.session_id]

    return results


def resolve_conversation(identifier: str, project_key: str | None = None) -> Path:
    """Resolve a conversation identifier to its JSONL file path.

    Resolution order:
    1. Valid file path that exists
    2. Exact UUID match
    3. UUID prefix (4+ chars)
    4. Slug match
    5. Raise SystemExit

    Args:
        identifier: File path, UUID, UUID prefix, or slug.
        project_key: Optional project key to narrow search.

    Returns:
        Path to the conversation JSONL file.
    """
    # 1. Direct file path
    candidate = Path(identifier)
    if candidate.exists() and candidate.is_file():
        return candidate

    if not PROJECTS_DIR.exists():
        raise SystemExit(f"No conversations found: {PROJECTS_DIR} does not exist")

    if project_key:
        resolved = _resolve_project_key(project_key)
        search_pattern = resolved if resolved else project_key
    else:
        search_pattern = "*"

    # 2. Exact UUID match
    matches = list(PROJECTS_DIR.glob(f"{search_pattern}/{identifier}.jsonl"))
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        projects = [m.parent.name for m in matches]
        raise SystemExit(
            f"UUID {identifier} found in multiple projects: {', '.join(projects)}. "
            "Use --project to disambiguate."
        )

    # 3. UUID prefix (4+ chars)
    if len(identifier) >= 4:
        matches = list(PROJECTS_DIR.glob(f"{search_pattern}/{identifier}*.jsonl"))
        # Filter out subagent files
        matches = [m for m in matches if "subagents" not in m.parts]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            uuids = [m.stem for m in matches]
            raise SystemExit(
                f"Prefix '{identifier}' matches multiple conversations:\n"
                + "\n".join(f"  {u}" for u in uuids)
            )

    # 4. Slug match
    if project_key:
        search_dirs = [PROJECTS_DIR / project_key]
    else:
        search_dirs = _safe_subdirs(PROJECTS_DIR)

    for proj_dir in search_dirs:
        if not proj_dir.exists():
            continue
        for jsonl_file in proj_dir.glob("*.jsonl"):
            if "subagents" in jsonl_file.parts:
                continue
            try:
                with open(jsonl_file, "r", encoding="utf-8") as f:
                    for i, raw_line in enumerate(f):
                        if i >= 50:
                            break
                        obj = _parse_line(raw_line)
                        if obj and obj.get("slug") == identifier:
                            return jsonl_file
            except OSError:
                continue

    raise SystemExit(f"Could not resolve conversation: '{identifier}'")


def resolve_agent(agent_id: str) -> Path:
    """Resolve a subagent ID to its JSONL file path.

    Globs for ``~/.claude/projects/**/agent-<agent_id>.jsonl``.

    Args:
        agent_id: Hex agent identifier (e.g. ``ad37c9994945aa7c4``).

    Returns:
        Path to the agent JSONL file.
    """
    if not PROJECTS_DIR.exists():
        raise SystemExit(f"No conversations found: {PROJECTS_DIR} does not exist")

    matches = sorted(
        PROJECTS_DIR.glob(f"**/agent-{agent_id}.jsonl"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )

    if not matches:
        raise SystemExit(f"No subagent found with ID '{agent_id}'")

    return matches[0]


def list_projects() -> list[str]:
    """Return directory names under the projects dir."""
    if not PROJECTS_DIR.exists():
        return []
    return sorted(d.name for d in _safe_subdirs(PROJECTS_DIR))


def get_subagent_paths(conv_path: Path) -> list[Path]:
    """Find subagent JSONL files for a conversation.

    Looks for <uuid>/subagents/agent-*.jsonl relative to the conversation file.
    """
    uuid = conv_path.stem
    subagent_dir = conv_path.parent / uuid / "subagents"
    if not subagent_dir.exists():
        return []
    return sorted(subagent_dir.glob("agent-*.jsonl"))


def _scan_subagent(path: Path, conv_uuid: str, project_key: str) -> SubagentInfo:
    """Scan a subagent JSONL file for metadata."""
    # Extract agent ID from filename: agent-<id>.jsonl
    agent_id = path.stem
    if agent_id.startswith("agent-"):
        agent_id = agent_id[len("agent-"):]

    size = os.path.getsize(path)
    first_timestamp = None
    last_timestamp = None
    prompt_snippet = None
    line_count = 0
    turn_count = 0
    model = None

    with open(path, "r", encoding="utf-8") as f:
        for raw_line in f:
            obj = _parse_line(raw_line)
            if obj is None:
                continue
            line_count += 1

            ts = obj.get("timestamp")
            if ts:
                if first_timestamp is None:
                    first_timestamp = ts
                last_timestamp = ts

            # First user message with string content is the prompt
            if prompt_snippet is None and _is_user_turn(obj):
                prompt_snippet = _extract_snippet(obj)

            if model is None and obj.get("type") == "assistant":
                msg = obj.get("message") or {}
                if isinstance(msg, dict) and msg.get("model"):
                    model = msg["model"]

            if _is_user_turn(obj):
                turn_count += 1

    return SubagentInfo(
        path=path,
        agent_id=agent_id,
        conversation_uuid=conv_uuid,
        project_key=project_key,
        size=size,
        prompt_snippet=prompt_snippet,
        first_timestamp=first_timestamp,
        last_timestamp=last_timestamp,
        line_count=line_count,
        turn_count=turn_count,
        model=model,
    )


def list_subagents(conv_path: Path) -> list[SubagentInfo]:
    """List all subagents for a conversation with metadata.

    Args:
        conv_path: Path to the conversation JSONL file.

    Returns:
        List of SubagentInfo sorted by first_timestamp (earliest first).
    """
    conv_uuid = conv_path.stem
    project_key = conv_path.parent.name
    sa_paths = get_subagent_paths(conv_path)

    results = []
    for sa_path in sa_paths:
        results.append(_scan_subagent(sa_path, conv_uuid, project_key))

    # Sort by timestamp (None sorts last)
    results.sort(key=lambda s: s.first_timestamp or "")
    return results


def get_conversation_cost(info: ConversationInfo, cache: "CostCache") -> float:
    """Return cost for conversation + all subagents. Per-file costs cached independently.

    Also populates ``info.total_tokens`` as a side effect.

    Returns:
        Total cost in USD (main + all subagents).
    """
    from cchat.costs import compute_file_cost

    # Main conversation
    mtime = info.path.stat().st_mtime
    cached = cache.get(info.uuid, mtime, info.size)
    if cached is not None:
        total_cost, total_tokens = cached
    else:
        total_cost, total_tokens = compute_file_cost(info.path)
        cache.set(info.uuid, mtime, info.size, total_cost, total_tokens)

    info.total_tokens = total_tokens

    # Subagents
    for sa_path in get_subagent_paths(info.path):
        sa_id = sa_path.stem
        if sa_id.startswith("agent-"):
            sa_id = sa_id[len("agent-") :]
        sa_size = sa_path.stat().st_size
        sa_mtime = sa_path.stat().st_mtime
        sa_cached = cache.get(sa_id, sa_mtime, sa_size)
        if sa_cached is not None:
            sa_cost, sa_tokens = sa_cached
        else:
            sa_cost, sa_tokens = compute_file_cost(sa_path)
            cache.set(sa_id, sa_mtime, sa_size, sa_cost, sa_tokens)
        total_cost += sa_cost
        info.total_tokens += sa_tokens

    return total_cost


def get_conversation_tokens(
    info: ConversationInfo, *, include_subagents: bool = False
) -> "TokenBreakdown":
    """Return a TokenBreakdown for a conversation.

    Args:
        info: Conversation metadata.
        include_subagents: If True, also sum tokens from subagent JSONL files.
            Default False to match cost calculation scope (which only counts
            the parent file).
    """
    from cchat.costs import compute_file_tokens

    bd = compute_file_tokens(info.path)
    if include_subagents:
        for sa_path in get_subagent_paths(info.path):
            bd += compute_file_tokens(sa_path)
    return bd


def get_subagent_stats(subagent: SubagentInfo, cache: "CostCache") -> None:
    """Populate cost and tokens on a SubagentInfo (mutates in place)."""
    from cchat.costs import compute_file_cost

    sa_mtime = subagent.path.stat().st_mtime
    cached = cache.get(subagent.agent_id, sa_mtime, subagent.size)
    if cached is not None:
        subagent.estimated_cost_usd, subagent.total_tokens = cached
    else:
        cost, tokens = compute_file_cost(subagent.path)
        cache.set(subagent.agent_id, sa_mtime, subagent.size, cost, tokens)
        subagent.estimated_cost_usd = cost
        subagent.total_tokens = tokens
