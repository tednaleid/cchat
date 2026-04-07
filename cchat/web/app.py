"""FastAPI application for cchat web UI."""

from __future__ import annotations

import asyncio
import json
import time
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import re

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

from cchat import costs, parser, store
from cchat.commands.spending_cmd import get_week_start
from cchat.web.watcher import manager, tracker, watch_filesystem

# Shared cost cache instance
_cost_cache = costs.CostCache()
_cost_cache_last_save: float = 0.0
_COST_SAVE_INTERVAL = 5.0  # seconds

STATIC_DIR = Path(__file__).parent / "static"


def _maybe_save_cost_cache() -> None:
    """Save cost cache at most once per _COST_SAVE_INTERVAL seconds."""
    global _cost_cache_last_save
    now = time.time()
    if now - _cost_cache_last_save >= _COST_SAVE_INTERVAL:
        _cost_cache.save()
        _cost_cache_last_save = now


def decode_project_key(key: str) -> str:
    """Decode a project key to a human-readable path.

    ``C--git-l-sc`` -> ``C:\\git\\l\\sc``

    The first segment before ``-`` is the drive letter.  Double dashes
    (``--``) encode backslashes, single dashes encode path separators.
    """
    if not key:
        return key
    # Replace -- with a placeholder, then - with \, then restore --
    # Actually: the encoding is simpler. The key is the path with
    # os.sep replaced by '-' and ':' dropped. Drive letter is first char.
    # C:\git\l\sc -> C--git-l-sc  (: dropped, \ becomes -)
    # But -- means \, single - is just a separator? Let's look at the spec:
    # "Decode project keys: C--git-l-sc -> C:\git\l\sc (replace -- with \, handle drive letter)"
    # So: -- is backslash, first part before first -- is drive letter
    parts = key.split("--")
    if len(parts) < 2:
        return key
    drive = parts[0]
    rest = "\\".join(parts[1:])
    # Replace single dashes with backslashes in each segment
    rest = rest.replace("-", "\\")
    return f"{drive}:\\{rest}"


def _extract_title(path: Path) -> str | None:
    """Scan first 100 lines for a custom-title line."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            for i, raw in enumerate(f):
                if i >= 100:
                    break
                if "custom-title" not in raw:
                    continue
                try:
                    data = json.loads(raw)
                except (json.JSONDecodeError, ValueError):
                    continue
                if data.get("type") == "custom-title":
                    # Title stored in title field or message content
                    return (
                        data.get("title")
                        or data.get("customTitle")
                        or (data.get("message", {}) or {}).get("content")
                    )
    except OSError:
        pass
    return None


def _conv_info_to_dict(info: store.ConversationInfo, title: str | None = None) -> dict:
    """Convert a ConversationInfo to a JSON-serializable dict."""
    return {
        "uuid": info.uuid,
        "title": title or info.snippet,
        "project_key": info.project_key,
        "project_path": decode_project_key(info.project_key),
        "slug": info.slug,
        "snippet": info.snippet,
        "first_timestamp": info.first_timestamp,
        "last_timestamp": info.last_timestamp,
        "turn_count": info.turn_count,
        "agent_count": info.agent_count,
        "total_tokens": info.total_tokens,
        "model": info.model,
        "estimated_cost_usd": info.estimated_cost_usd,
        "size": info.size,
        "is_active": _is_active(info.path),
    }


def _is_active(path: Path) -> bool:
    """Check if a conversation file was modified in the last 60 seconds."""
    try:
        return (time.time() - path.stat().st_mtime) < 60
    except OSError:
        return False


def _subagent_info_to_dict(sa: store.SubagentInfo) -> dict:
    """Convert a SubagentInfo to a JSON-serializable dict."""
    meta = _read_subagent_meta(sa.path)
    return {
        "agent_id": sa.agent_id,
        "conversation_uuid": sa.conversation_uuid,
        "prompt_snippet": sa.prompt_snippet,
        "first_timestamp": sa.first_timestamp,
        "last_timestamp": sa.last_timestamp,
        "line_count": sa.line_count,
        "turn_count": sa.turn_count,
        "total_tokens": sa.total_tokens,
        "model": sa.model,
        "estimated_cost_usd": sa.estimated_cost_usd,
        "description": meta.get("description"),
        "agent_type": meta.get("agentType"),
    }


def _read_subagent_meta(sa_path: Path) -> dict:
    """Try to read .meta.json next to a subagent JSONL file."""
    meta_path = sa_path.with_suffix(".meta.json")
    if not meta_path.exists():
        # Also check without 'agent-' prefix
        meta_path = sa_path.parent / f"{sa_path.stem}.meta.json"
        if not meta_path.exists():
            return {}
    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _line_to_dict(line_num: int, data: dict) -> dict:
    """Convert a parsed JSONL line to a JSON-serializable dict for the API."""
    return {
        "line_number": line_num,
        "type": data.get("type"),
        "subtype": parser.classify_line_subtype(data),
        "timestamp": parser.extract_timestamp(data),
        "summary": parser.extract_content_summary(data),
        "tokens": parser.extract_token_count(data),
        "model": parser.extract_model(data),
        "usage": parser.extract_usage(data),
        "data": data,
    }


_UUID_RE = re.compile(r"^[0-9a-fA-F-]{4,64}$")
_AGENT_ID_RE = re.compile(r"^[0-9a-fA-F]{4,64}$")


def _validate_uuid(uuid: str) -> None:
    """Reject UUIDs with path traversal characters or invalid format."""
    if not _UUID_RE.match(uuid):
        raise HTTPException(status_code=400, detail="Invalid conversation ID format")


def _validate_agent_id(agent_id: str) -> None:
    """Reject agent IDs that aren't hex strings."""
    if not _AGENT_ID_RE.match(agent_id):
        raise HTTPException(status_code=400, detail="Invalid agent ID format")


def _resolve_conv_path(uuid: str) -> Path:
    """Resolve a conversation UUID to its file path, raising 404 on failure."""
    _validate_uuid(uuid)
    try:
        return store.resolve_conversation(uuid)
    except SystemExit:
        raise HTTPException(status_code=404, detail=f"Conversation not found: {uuid}")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    from contextlib import asynccontextmanager

    _watcher_stop = asyncio.Event()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        asyncio.create_task(watch_filesystem(_watcher_stop))
        yield
        _watcher_stop.set()
        _cost_cache.save()  # Flush any remaining dirty cache

    app = FastAPI(title="cchat", docs_url="/api/docs", lifespan=lifespan)

    # No CORS middleware -- UI is served from the same origin.

    # ---- REST endpoints ----

    @app.get("/api/projects")
    def list_projects():
        keys = store.list_projects()
        return {
            "projects": [
                {"key": k, "path": decode_project_key(k)} for k in keys
            ]
        }

    @app.get("/api/conversations")
    def list_conversations(
        project: str | None = Query(None),
        sort: str = Query("last_active"),
        order: str = Query("desc"),
        limit: int = Query(50, ge=1, le=500),
        offset: int = Query(0, ge=0),
    ):
        convs = store.discover_conversations(project_key=project)

        # Compute costs
        for c in convs:
            c.estimated_cost_usd = store.get_conversation_cost(c, _cost_cache)
        _maybe_save_cost_cache()

        # Sort
        sort_keys = {
            "last_active": lambda c: c.last_timestamp or "",
            "created": lambda c: c.first_timestamp or "",
            "cost": lambda c: c.estimated_cost_usd or 0.0,
            "tokens": lambda c: c.total_tokens or 0,
        }
        key_fn = sort_keys.get(sort, sort_keys["last_active"])
        reverse = order == "desc"
        convs.sort(key=key_fn, reverse=reverse)

        total = len(convs)
        page = convs[offset : offset + limit]
        # Extract titles only for the page (avoid scanning all files)
        titles = {c.uuid: _extract_title(c.path) for c in page}
        return {
            "conversations": [
                _conv_info_to_dict(c, title=titles.get(c.uuid)) for c in page
            ],
            "total": total,
        }

    @app.get("/api/conversations/{uuid}")
    def get_conversation(uuid: str):
        conv_path = _resolve_conv_path(uuid)
        info = store._scan_conversation(conv_path)
        info.estimated_cost_usd = store.get_conversation_cost(info, _cost_cache)

        # Get subagents
        subagents = store.list_subagents(conv_path)
        for sa in subagents:
            store.get_subagent_stats(sa, _cost_cache)
        _maybe_save_cost_cache()

        title = _extract_title(conv_path)
        result = _conv_info_to_dict(info, title=title)
        result["subagents"] = [_subagent_info_to_dict(sa) for sa in subagents]
        return result

    @app.get("/api/conversations/{uuid}/lines")
    def get_conversation_lines(
        uuid: str,
        type: str | None = Query(None),
        deduplicate: bool = Query(True),
        limit: int = Query(100, ge=1, le=1000),
        offset: int = Query(0, ge=0),
    ):
        conv_path = _resolve_conv_path(uuid)
        lines_iter = parser.parse_lines(conv_path)

        if deduplicate:
            lines_iter = parser.deduplicate_assistant_lines(lines_iter)

        all_lines = []
        for line_num, data in lines_iter:
            if type and data.get("type") != type:
                continue
            all_lines.append(_line_to_dict(line_num, data))

        total = len(all_lines)
        page = all_lines[offset : offset + limit]
        return {"lines": page, "total": total}

    @app.get("/api/conversations/{uuid}/lines/{line_number}")
    def get_conversation_line(uuid: str, line_number: int):
        conv_path = _resolve_conv_path(uuid)
        for line_num, data in parser.parse_lines(conv_path):
            if line_num == line_number:
                return _line_to_dict(line_num, data)
        raise HTTPException(status_code=404, detail=f"Line {line_number} not found")

    @app.get("/api/conversations/{uuid}/files")
    def get_conversation_files(uuid: str):
        conv_path = _resolve_conv_path(uuid)
        file_counts: dict[str, int] = defaultdict(int)
        file_tools: dict[str, set] = defaultdict(set)

        # Scan main conversation + subagents
        for path in [conv_path] + store.get_subagent_paths(conv_path):
            for _ln, data in parser.parse_lines(path):
                mods = parser.extract_file_modifications(data)
                if mods:
                    for mod in mods:
                        fp = mod["file_path"]
                        file_counts[fp] += 1
                        file_tools[fp].add(mod["tool"])

        sorted_files = sorted(file_counts.items(), key=lambda x: x[1], reverse=True)
        return {
            "files": [
                {
                    "path": fp,
                    "modifications": count,
                    "tools": sorted(file_tools[fp]),
                }
                for fp, count in sorted_files
            ]
        }

    @app.get("/api/conversations/{uuid}/agents")
    def get_conversation_agents(uuid: str):
        conv_path = _resolve_conv_path(uuid)
        subagents = store.list_subagents(conv_path)
        for sa in subagents:
            store.get_subagent_stats(sa, _cost_cache)
        _maybe_save_cost_cache()
        return {
            "agents": [_subagent_info_to_dict(sa) for sa in subagents]
        }

    @app.get("/api/conversations/{uuid}/agents/{agent_id}/lines")
    def get_agent_lines(
        uuid: str,
        agent_id: str,
        deduplicate: bool = Query(True),
        limit: int = Query(100, ge=1, le=1000),
        offset: int = Query(0, ge=0),
    ):
        conv_path = _resolve_conv_path(uuid)
        _validate_agent_id(agent_id)
        # Find the subagent file
        sa_paths = store.get_subagent_paths(conv_path)
        sa_path = None
        for p in sa_paths:
            aid = p.stem
            if aid.startswith("agent-"):
                aid = aid[len("agent-"):]
            if aid == agent_id:
                sa_path = p
                break

        if sa_path is None:
            raise HTTPException(
                status_code=404, detail=f"Agent {agent_id} not found"
            )

        lines_iter = parser.parse_lines(sa_path)
        if deduplicate:
            lines_iter = parser.deduplicate_assistant_lines(lines_iter)

        all_lines = [_line_to_dict(ln, data) for ln, data in lines_iter]
        total = len(all_lines)
        page = all_lines[offset : offset + limit]
        return {"lines": page, "total": total}

    @app.get("/api/search")
    def search_conversations(
        q: str = Query(..., min_length=1),
        project: str | None = Query(None),
        type: str | None = Query(None),
        sort: str = Query("newest"),
        limit: int = Query(50, ge=1, le=200),
    ):
        query_lower = q.lower()
        convs = store.discover_conversations(project_key=project)
        conv_meta = {c.uuid: c for c in convs}

        results = []
        hard_cap = 1000

        for conv in convs:
            try:
                with open(conv.path, "r", encoding="utf-8") as fh:
                    for line_num, raw_line in enumerate(fh, start=1):
                        if query_lower not in raw_line.lower():
                            continue
                        try:
                            data = json.loads(raw_line)
                        except (json.JSONDecodeError, ValueError):
                            continue

                        line_type = data.get("type", "")
                        if type and line_type != type:
                            continue

                        timestamp = parser.extract_timestamp(data)
                        snippet = parser.extract_content_summary(data)
                        info = conv_meta.get(conv.uuid)

                        results.append({
                            "conversation_uuid": conv.uuid,
                            "slug": info.slug if info else None,
                            "line_number": line_num,
                            "type": line_type,
                            "snippet": snippet,
                            "timestamp": timestamp,
                        })

                        if len(results) >= hard_cap:
                            break
            except OSError:
                continue
            if len(results) >= hard_cap:
                break

        # Sort
        reverse = sort == "newest"
        results.sort(key=lambda r: r["timestamp"] or "", reverse=reverse)
        results = results[:limit]

        return {"results": results}

    @app.get("/api/spending")
    def get_spending(
        weeks: int = Query(4, ge=1, le=52),
        project: str | None = Query(None),
    ):
        convs = store.discover_conversations(project_key=project)
        for c in convs:
            c.estimated_cost_usd = store.get_conversation_cost(c, _cost_cache)
        _maybe_save_cost_cache()

        # Group by week and day (same logic as spending_cmd)
        week_data: dict[str, dict[str, dict]] = defaultdict(
            lambda: defaultdict(lambda: {"cost": 0.0, "conversations": 0})
        )

        for conv in convs:
            if not conv.first_timestamp:
                continue
            try:
                dt = datetime.fromisoformat(
                    conv.first_timestamp.replace("Z", "+00:00")
                )
            except (ValueError, AttributeError):
                continue

            week_start = get_week_start(dt)
            week_key = week_start.isoformat()
            day_key = dt.strftime("%Y-%m-%d")

            entry = week_data[week_key][day_key]
            entry["cost"] += conv.estimated_cost_usd or 0.0
            entry["conversations"] += 1

        # Build response
        sorted_weeks = sorted(week_data.keys(), reverse=True)[:weeks]
        grand_total = 0.0
        weeks_list = []

        for week_key in sorted_weeks:
            days_dict = week_data[week_key]
            sorted_days = sorted(days_dict.keys(), reverse=True)
            subtotal = 0.0

            days_list = []
            for day_key in sorted_days:
                entry = days_dict[day_key]
                days_list.append({
                    "date": day_key,
                    "cost": round(entry["cost"], 4),
                    "conversations": entry["conversations"],
                })
                subtotal += entry["cost"]

            try:
                ws_dt = datetime.fromisoformat(week_key)
                week_end = ws_dt + timedelta(days=7)
            except ValueError:
                week_end = None

            grand_total += subtotal
            weeks_list.append({
                "start": week_key,
                "end": week_end.isoformat() if week_end else None,
                "days": days_list,
                "subtotal": round(subtotal, 4),
            })

        return {
            "weeks": weeks_list,
            "grand_total": round(grand_total, 4),
        }

    # ---- WebSocket endpoints ----

    @app.websocket("/ws/conversation/{uuid}")
    async def ws_conversation(websocket: WebSocket, uuid: str):
        await websocket.accept()
        # Resolve path to validate UUID exists
        try:
            conv_path = _resolve_conv_path(uuid)
        except HTTPException:
            await websocket.close(code=4004, reason="Conversation not found")
            return

        # Mark current file position so we only stream new lines
        tracker.mark_current(conv_path)
        manager.subscribe_conversation(uuid, websocket)

        try:
            while True:
                raw = await websocket.receive_text()
                try:
                    msg = json.loads(raw)
                except (json.JSONDecodeError, ValueError):
                    continue

                action = msg.get("action")
                agent_id = msg.get("agent_id")

                if action == "subscribe_agent" and agent_id and _AGENT_ID_RE.match(agent_id):
                    manager.subscribe_agent(uuid, agent_id, websocket)
                    # Mark the agent file position
                    for sa_path in store.get_subagent_paths(conv_path):
                        aid = sa_path.stem
                        if aid.startswith("agent-"):
                            aid = aid[len("agent-"):]
                        if aid == agent_id:
                            tracker.mark_current(sa_path)
                            break
                elif action == "unsubscribe_agent" and agent_id and _AGENT_ID_RE.match(agent_id):
                    manager.unsubscribe_agent(uuid, agent_id, websocket)

        except WebSocketDisconnect:
            pass
        finally:
            manager.unsubscribe_conversation(uuid, websocket)

    @app.websocket("/ws/conversations")
    async def ws_conversations(websocket: WebSocket):
        await websocket.accept()
        manager.subscribe_list(websocket)
        try:
            while True:
                await websocket.receive_text()  # Keep alive
        except WebSocketDisconnect:
            pass
        finally:
            manager.unsubscribe_list(websocket)

    # ---- Static files (SPA fallback) ----
    if STATIC_DIR.exists():
        app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")

    return app
