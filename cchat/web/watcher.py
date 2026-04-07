"""Filesystem watcher and WebSocket broadcast for live conversation updates."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from cchat import store


class FileTracker:
    """Track file positions and line counts for seek-based incremental reading."""

    def __init__(self):
        # key -> (byte_position, line_count)
        self._state: dict[str, tuple[int, int]] = {}

    def read_new_lines(self, path: Path) -> list[tuple[int, dict]]:
        """Read new lines from a file since last read position.

        Returns list of (line_number, parsed_data) tuples.
        """
        key = str(path)
        last_pos, last_line = self._state.get(key, (0, 0))

        try:
            size = path.stat().st_size
        except OSError:
            return []

        if size <= last_pos:
            return []

        new_lines = []
        line_num = last_line
        try:
            with open(path, "r", encoding="utf-8") as f:
                f.seek(last_pos)
                for raw in f:
                    line_num += 1
                    stripped = raw.strip()
                    if not stripped:
                        continue
                    try:
                        data = json.loads(stripped)
                        new_lines.append((line_num, data))
                    except (json.JSONDecodeError, ValueError):
                        pass

                self._state[key] = (f.tell(), line_num)
        except OSError:
            pass

        return new_lines

    def reset(self, path: Path) -> None:
        """Reset tracking for a file (re-read from beginning)."""
        self._state.pop(str(path), None)

    def mark_current(self, path: Path) -> None:
        """Mark the current end of file as the read position (skip existing content).

        Counts lines so subsequent reads get correct line numbers.
        """
        key = str(path)
        try:
            line_count = 0
            with open(path, "r", encoding="utf-8") as f:
                for _ in f:
                    line_count += 1
            self._state[key] = (path.stat().st_size, line_count)
        except OSError:
            pass


class ConnectionManager:
    """Manage WebSocket connections for conversation streaming."""

    def __init__(self):
        # uuid -> set of websocket connections
        self.conversation_subs: dict[str, set] = {}
        # Connections watching the conversation list
        self.list_subs: set = set()
        # (parent_uuid, agent_id) -> set of websocket connections
        self.agent_subs: dict[tuple[str, str], set] = {}

    def subscribe_conversation(self, uuid: str, ws: Any) -> None:
        if uuid not in self.conversation_subs:
            self.conversation_subs[uuid] = set()
        self.conversation_subs[uuid].add(ws)

    def unsubscribe_conversation(self, uuid: str, ws: Any) -> None:
        if uuid in self.conversation_subs:
            self.conversation_subs[uuid].discard(ws)
            if not self.conversation_subs[uuid]:
                del self.conversation_subs[uuid]

    def subscribe_list(self, ws: Any) -> None:
        self.list_subs.add(ws)

    def unsubscribe_list(self, ws: Any) -> None:
        self.list_subs.discard(ws)

    def subscribe_agent(self, parent_uuid: str, agent_id: str, ws: Any) -> None:
        key = (parent_uuid, agent_id)
        if key not in self.agent_subs:
            self.agent_subs[key] = set()
        self.agent_subs[key].add(ws)

    def unsubscribe_agent(self, parent_uuid: str, agent_id: str, ws: Any) -> None:
        key = (parent_uuid, agent_id)
        if key in self.agent_subs:
            self.agent_subs[key].discard(ws)
            if not self.agent_subs[key]:
                del self.agent_subs[key]

    async def broadcast_to_conversation(self, uuid: str, message: dict) -> None:
        subs = self.conversation_subs.get(uuid, set())
        dead = []
        for ws in subs:
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            subs.discard(ws)

    async def broadcast_to_list(self, message: dict) -> None:
        dead = []
        for ws in self.list_subs:
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.list_subs.discard(ws)

    async def broadcast_to_agent(
        self, parent_uuid: str, agent_id: str, message: dict
    ) -> None:
        key = (parent_uuid, agent_id)
        subs = self.agent_subs.get(key, set())
        dead = []
        for ws in subs:
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            subs.discard(ws)


# Module-level singletons
manager = ConnectionManager()
tracker = FileTracker()

# Track message.id -> latest line for streaming dedup
_message_tracker: dict[str, int] = {}


def _classify_line_event(line_num: int, data: dict) -> list[dict]:
    """Classify a new line into WebSocket events.

    Returns a list of events to broadcast (usually one, but streaming
    assistant messages produce message_update/message_final events).
    """
    events = []
    line_type = data.get("type")

    if line_type == "assistant":
        msg = data.get("message") or {}
        msg_id = msg.get("id")
        stop_reason = msg.get("stop_reason")

        if msg_id:
            if stop_reason:
                events.append({
                    "event": "message_final",
                    "message_id": msg_id,
                    "line_number": line_num,
                    "data": {
                        "content": msg.get("content", []),
                        "usage": msg.get("usage"),
                        "stop_reason": stop_reason,
                    },
                })
                _message_tracker.pop(msg_id, None)
            else:
                events.append({
                    "event": "message_update",
                    "message_id": msg_id,
                    "line_number": line_num,
                    "data": {
                        "content": msg.get("content", []),
                        "usage": msg.get("usage"),
                        "stop_reason": None,
                    },
                })
                _message_tracker[msg_id] = line_num
            return events

    # Default: line_append for all other line types
    events.append({
        "event": "line_append",
        "line_number": line_num,
        "data": data,
    })
    return events


def _detect_new_subagents(
    conv_path: Path, known_agents: set[str]
) -> list[tuple[str, dict]]:
    """Check for new subagent files and return (agent_id, meta) pairs."""
    new_agents = []
    for sa_path in store.get_subagent_paths(conv_path):
        aid = sa_path.stem
        if aid.startswith("agent-"):
            aid = aid[len("agent-"):]
        if aid not in known_agents:
            known_agents.add(aid)
            # Try to read meta
            meta_path = sa_path.with_suffix(".meta.json")
            meta = {}
            if meta_path.exists():
                try:
                    with open(meta_path, "r", encoding="utf-8") as f:
                        meta = json.load(f)
                except (json.JSONDecodeError, OSError):
                    pass
            new_agents.append((aid, meta))
    return new_agents


async def watch_filesystem(stop_event: asyncio.Event | None = None) -> None:
    """Watch ~/.claude/projects/ for file changes and broadcast via WebSocket.

    Uses polling with asyncio.sleep rather than watchfiles to keep
    dependencies lighter. Checks every 0.5 seconds.
    """
    projects_dir = store.PROJECTS_DIR
    if not projects_dir.exists():
        return

    # Known subagents per conversation uuid
    known_agents: dict[str, set[str]] = {}
    # Track which files we know about for list change detection
    known_files: set[str] = set()

    while True:
        if stop_event and stop_event.is_set():
            break

        try:
            await _poll_changes(projects_dir, known_agents, known_files)
        except Exception:
            pass  # Don't crash the watcher on transient errors

        await asyncio.sleep(0.5)


async def _poll_changes(
    projects_dir: Path,
    known_agents: dict[str, set[str]],
    known_files: set[str],
) -> None:
    """Single poll cycle: check for new/changed files and broadcast."""
    list_changed = False
    _changed_uuids: dict[str, Path] = {}

    # Scan all conversation files
    for proj_dir in projects_dir.iterdir():
        if not proj_dir.is_dir():
            continue
        for jsonl_file in proj_dir.glob("*.jsonl"):
            if "subagents" in jsonl_file.parts:
                continue

            file_key = str(jsonl_file)
            uuid = jsonl_file.stem

            # Detect new conversations
            if file_key not in known_files:
                known_files.add(file_key)
                list_changed = True
                _changed_uuids[uuid] = jsonl_file
                tracker.mark_current(jsonl_file)

            # Check for new lines in conversations with active subscribers
            new_lines = []
            if uuid in manager.conversation_subs:
                new_lines = tracker.read_new_lines(jsonl_file)
                for line_num, data in new_lines:
                    events = _classify_line_event(line_num, data)
                    for event in events:
                        await manager.broadcast_to_conversation(uuid, event)

                # Check for new subagents
                if uuid not in known_agents:
                    known_agents[uuid] = set()
                new_sa = _detect_new_subagents(jsonl_file, known_agents[uuid])
                for agent_id, meta in new_sa:
                    await manager.broadcast_to_conversation(uuid, {
                        "event": "subagent_new",
                        "agent_id": agent_id,
                        "meta": meta,
                    })

                # Check subscribed agent files for new lines
                conv_path = jsonl_file
                for sa_path in store.get_subagent_paths(conv_path):
                    aid = sa_path.stem
                    if aid.startswith("agent-"):
                        aid = aid[len("agent-"):]
                    key = (uuid, aid)
                    if key in manager.agent_subs:
                        sa_lines = tracker.read_new_lines(sa_path)
                        for ln, data in sa_lines:
                            await manager.broadcast_to_agent(uuid, aid, {
                                "event": "subagent_line",
                                "agent_id": aid,
                                "line_number": ln,
                                "data": data,
                            })

            # Track conversations with new lines for list notification
            if new_lines:
                list_changed = True
                _changed_uuids[uuid] = jsonl_file

    if list_changed:
        import time as _time
        for changed_uuid, changed_path in _changed_uuids.items():
            is_active = False
            try:
                is_active = (_time.time() - changed_path.stat().st_mtime) < 60
            except OSError:
                pass
            await manager.broadcast_to_list({
                "event": "conversation_updated",
                "uuid": changed_uuid,
                "is_active": is_active,
            })
