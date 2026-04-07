# cchat Web UI Specification

## Overview

A web interface for browsing Claude Code conversations stored as JSONL files on disk. The UI renders conversation threads, subagent/agent team hierarchies, and individual messages with tool calls. **Live conversations stream updates to the browser in realtime** via filesystem watching.

## Architecture

```
Browser (SPA)  <--WebSocket-->  Python backend (FastAPI + uvicorn)
                                    |
                                    +-- cchat.store (conversation discovery)
                                    +-- cchat.parser (JSONL parsing)
                                    +-- cchat.costs (token/cost computation)
                                    +-- watchfiles (filesystem monitoring)
```

**Stack choices:**
- **Backend**: FastAPI (async, WebSocket-native, already in Python ecosystem alongside cchat)
- **Frontend**: Vanilla JS + Preact (via CDN, no build step). Single `index.html` with inline JS/CSS.
- **Realtime**: WebSocket per open conversation. Backend watches JSONL files with `watchfiles` and pushes new/changed lines.
- **No database**: All data comes from JSONL files on disk + the existing cost cache.

**Entrypoint**: `cchat serve [--port 8411] [--host 127.0.0.1]` added as a new CLI command.

---

## Data Model (JSONL -> UI)

### Line Types and How They Map to UI

| JSONL `type` | UI Treatment |
|---|---|
| `user` (string content) | **User message bubble** -- plain text or markdown |
| `user` (array content with `tool_result`) | **Tool result** -- collapsed by default, expandable. Linked to the tool_use that triggered it. |
| `assistant` (text) | **Assistant message bubble** -- markdown rendered |
| `assistant` (thinking) | **Thinking block** -- collapsed by default, muted style, expandable |
| `assistant` (tool_use) | **Tool call card** -- tool name as header, input as syntax-highlighted JSON or specialized rendering |
| `system` (bridge_status) | **Session start marker** -- horizontal rule with session URL |
| `system` (turn_duration) | **Turn timing badge** -- small inline indicator |
| `permission-mode` | **Session metadata** -- shown in sidebar/header, not in message flow |
| `progress` (agent_progress) | **Subagent activity indicator** -- links to subagent conversation |
| `file-history-snapshot` | Hidden from message flow. Available in "Files" tab. |
| `queue-operation` | Hidden from message flow. |
| `attachment` | **Attachment indicator** -- filename/type shown inline |
| `custom-title` | Used as conversation display title |
| `agent-name` | Used as subagent display name |

### Streaming Deduplication

Assistant messages stream as multiple JSONL lines sharing the same `message.id`. The backend must:
1. Track `message.id` -> latest content
2. On each update, send a `message_update` WebSocket event with the full current content
3. When `stop_reason` is set (non-null), send a `message_final` event

The frontend replaces in-place rather than appending, keyed by `message.id`.

---

## Pages / Views

### 1. Conversation List (`/`)

**Layout**: Full-width table/list, sortable columns.

| Column | Source |
|---|---|
| Title | `custom-title` line, or first user message snippet (60 chars) |
| Project | Project key, decoded (e.g., `C--git-l-sc` -> `C:\git\l\sc`) |
| Started | `first_timestamp` |
| Last Active | `last_timestamp` |
| Turns | Count of user message turns |
| Agents | Count of Agent tool_use calls |
| Model | Primary model used |
| Tokens | Total tokens |
| Cost | Estimated cost (from cost cache) |

**Features:**
- Filter by project (dropdown)
- Sort by any column (default: last active, descending)
- Search box (filters by title/snippet)
- **Live indicator**: Conversations with recent activity (file mtime < 60s ago) show a pulsing dot. New conversations appear automatically via filesystem watch on the projects directory.
- Pagination or virtual scroll for large lists
- Click row -> navigate to conversation view

### 2. Conversation View (`/conversation/:uuid`)

**Layout**: Three-panel.

```
+--sidebar(240px)--+--------main-content--------+--detail-panel(320px)--+
|                  |                             |                       |
| Conversation     |   Message Thread            | Context / Inspector   |
| metadata         |   (scrollable)              | (selected item info)  |
|                  |                             |                       |
| Subagent tree    |                             |                       |
|                  |                             |                       |
| Files modified   |                             |                       |
|                  |                             |                       |
| Token/cost       |                             |                       |
| summary          |                             |                       |
+------------------+-----------------------------+-----------------------+
```

#### Sidebar

- **Metadata**: UUID (copyable), slug, project, model, timestamps, total tokens, cost
- **Subagent tree**: Hierarchical list of subagents. Each entry shows:
  - Agent name (from `agent-name` line) or description (from `.meta.json`)
  - Agent type (from `.meta.json`)
  - Prompt snippet
  - Token count / cost
  - Click to view subagent conversation inline or in a new tab
- **Files modified**: List of files touched (from Write/Edit tool calls), with modification count
- **Turn navigator**: Numbered list of user turns for quick jumping

#### Main Content -- Message Thread

The core view. Renders the conversation as a vertical thread:

**User message:**
```
+--[USER]--avatar--timestamp--------------------------------------+
|                                                                   |
|  Message text (markdown rendered)                                 |
|                                                                   |
+-------------------------------------------------------------------+
```

**Assistant message:**
```
+--[ASSISTANT]--model-badge--timestamp--tokens-badge--------------+
|                                                                   |
|  [> Thinking (collapsed)]                   <- click to expand    |
|                                                                   |
|  Response text (markdown rendered, code blocks highlighted)       |
|                                                                   |
|  +--[Tool Call: Bash]--------------------------------------+      |
|  | $ ls -la /tmp                                           |      |
|  | [> Result: 15 lines] <- collapsed, click to expand      |      |
|  +----------------------------------------------------------+     |
|                                                                   |
|  +--[Tool Call: Read]--------------------------------------+      |
|  | /path/to/file.py  lines 1-50                            |      |
|  | [> Result: file contents] <- collapsed                  |      |
|  +----------------------------------------------------------+     |
|                                                                   |
|  +--[Tool Call: Agent]-------------------------------------+      |
|  | "Explore codebase structure"                             |      |
|  | -> agent-a48ead874594006e6 (click to expand/navigate)   |      |
|  | Status: completed | Tokens: 12.4K | Cost: $0.08         |      |
|  +----------------------------------------------------------+     |
|                                                                   |
+-------------------------------------------------------------------+
```

**Tool call cards** -- specialized rendering per tool:

| Tool | Rendering |
|---|---|
| `Bash` | Command in monospace, result in scrollable pre block |
| `Read` | File path + line range as header, content syntax-highlighted |
| `Write` | File path as header, full content syntax-highlighted |
| `Edit` | File path, old_string/new_string as a unified diff view |
| `Grep` | Pattern + path as header, results as file list or content matches |
| `Glob` | Pattern as header, file list as result |
| `Agent` | Description, prompt (collapsed), link to subagent conversation |
| `WebSearch` / `WebFetch` | Query/URL as header, results collapsed |
| Other (MCP tools, etc.) | Generic: tool name + JSON input, text result |

**Tool results** are matched to their tool_use via `tool_use_id` and rendered inline beneath the corresponding tool call card, not as separate messages.

**Streaming behavior** (realtime):
- As new lines arrive via WebSocket, messages append to the bottom
- Streaming assistant messages update in-place (keyed by `message.id`)
- Auto-scroll to bottom when user is near bottom (within 200px); otherwise show a "new messages" indicator
- Thinking blocks show a typing animation while streaming

#### Detail Panel (right sidebar)

Contextual information for the selected/hovered item:
- **For a tool call**: Full input JSON, full result text, execution timing
- **For a message**: Token breakdown (input/output/cache), model, cost for this turn
- **For a subagent**: Full metadata, prompt, token/cost summary, link to open in full view
- **Nothing selected**: Conversation-level token chart (tokens over time)

### 3. Subagent View

Two modes:
1. **Inline expansion**: Within the parent conversation, expand the Agent tool call to show the subagent's full message thread nested and indented.
2. **Full view**: Navigate to `/conversation/:parent-uuid/agent/:agent-id` which renders the subagent conversation as a full conversation view (same layout as the main conversation view).

### 4. Search (`/search`)

- Full-text search across all conversations
- Results show: conversation title, matching line snippet with highlighted match, timestamp
- Click result -> jump to that line in conversation view, with the matched line highlighted and scrolled into view
- Filter by: project, date range, line type (user/assistant/system)

### 5. Spending Dashboard (`/spending`)

- Weekly spending chart (bar chart, same Wed-Wed weeks as CLI)
- Daily breakdown table
- Per-conversation cost breakdown for selected period
- Per-model cost split
- Token usage trends (input vs output vs cache)

---

## Realtime Streaming

### WebSocket Protocol

**Connection**: `ws://host:port/ws/conversation/:uuid`

**Server -> Client messages:**

```jsonc
// New line appended to conversation
{
  "event": "line_append",
  "line_number": 42,
  "data": {
    "type": "assistant",
    "uuid": "...",
    "timestamp": "...",
    "message": { ... },
    // full JSONL line as parsed JSON
  }
}

// Streaming assistant message update (same message.id, new content)
{
  "event": "message_update",
  "message_id": "msg_...",
  "line_number": 42,
  "data": {
    "content": [ ... ],  // full current content array
    "usage": { ... },
    "stop_reason": null
  }
}

// Streaming complete
{
  "event": "message_final",
  "message_id": "msg_...",
  "line_number": 45,
  "data": {
    "content": [ ... ],
    "usage": { ... },
    "stop_reason": "end_turn"
  }
}

// New subagent detected
{
  "event": "subagent_new",
  "agent_id": "a48ead874594006e6",
  "meta": { "agentType": "...", "description": "..." }
}

// Subagent line (if client has subscribed to it)
{
  "event": "subagent_line",
  "agent_id": "a48ead874594006e6",
  "line_number": 5,
  "data": { ... }
}
```

**Client -> Server messages:**

```jsonc
// Subscribe to subagent updates
{ "action": "subscribe_agent", "agent_id": "a48ead874594006e6" }

// Unsubscribe
{ "action": "unsubscribe_agent", "agent_id": "a48ead874594006e6" }
```

**Conversation list WebSocket**: `ws://host:port/ws/conversations`
- Pushes `conversation_updated` events when any JSONL file changes (new lines, new conversations)
- Lightweight: only sends metadata updates, not full line data

### Filesystem Watching

Backend uses `watchfiles` (or `watchdog`) to monitor:
1. `~/.claude/projects/*/` for new/changed `.jsonl` files
2. `~/.claude/projects/*/<uuid>/subagents/` for new subagent files

On file change:
1. Read new bytes from last known file position (seek-based, not re-reading entire file)
2. Parse new lines
3. Broadcast to subscribed WebSocket clients

---

## REST API

All endpoints return JSON. The frontend uses these for initial page loads; WebSocket handles subsequent updates.

### Conversations

```
GET /api/conversations
  ?project=<key>
  ?sort=last_active|created|cost|tokens
  ?order=asc|desc
  ?limit=50&offset=0
  -> { conversations: [...], total: N }

GET /api/conversations/:uuid
  -> { uuid, slug, project_key, model, timestamps, turns, agents, tokens, cost, subagents: [...] }

GET /api/conversations/:uuid/lines
  ?type=user|assistant|system
  ?deduplicate=true  (default: true, collapses streaming assistant lines)
  ?offset=0&limit=100
  -> { lines: [...], total: N }

GET /api/conversations/:uuid/lines/:line_number
  -> { ...full line data... }

GET /api/conversations/:uuid/files
  -> { files: [{ path, modifications, tools }] }

GET /api/conversations/:uuid/agents
  -> { agents: [{ agent_id, description, type, prompt, tokens, cost, timestamps }] }

GET /api/conversations/:uuid/agents/:agent_id/lines
  ?deduplicate=true
  ?offset=0&limit=100
  -> { lines: [...], total: N }
```

### Search

```
GET /api/search
  ?q=<query>
  ?project=<key>
  ?type=user|assistant
  ?sort=newest|oldest
  ?limit=50
  -> { results: [{ conversation_uuid, slug, line_number, type, snippet, timestamp }] }
```

### Spending

```
GET /api/spending
  ?weeks=4
  ?project=<key>
  -> { weeks: [{ start, end, days: [{ date, cost, conversations }], subtotal }], grand_total }
```

---

## Frontend Design

### Visual Language

- **Dark theme** by default (light theme toggle). Dark gray background (`#1a1a2e`), slightly lighter cards (`#222240`).
- **Monospace** for code, tool inputs/outputs. **Sans-serif** (system font stack) for UI text and conversation prose.
- **Color coding**:
  - User messages: subtle blue-gray left border
  - Assistant messages: no border or subtle purple left border
  - Tool calls: color-coded by tool category (file ops = green, shell = amber, search = blue, agent = purple)
  - Thinking blocks: muted/dimmed text, italic
  - Errors/denials: red accent
- **Compact by default**: Tool results collapsed, thinking collapsed, expandable on click.
- **Token/cost badges**: Small pill badges on assistant messages showing token count. Color intensity scales with cost (green -> yellow -> red).

### Keyboard Navigation

| Key | Action |
|---|---|
| `j` / `k` | Next / previous message |
| `Enter` | Expand/collapse selected item |
| `e` | Expand all tool results in current message |
| `t` | Toggle thinking blocks visibility |
| `/` | Focus search |
| `Esc` | Close detail panel / deselect |
| `1`-`9` | Jump to Nth user turn |
| `a` | Toggle subagent tree |

### Responsive

- At narrow widths (<1024px): detail panel hidden (click to show as overlay), sidebar collapsible
- At very narrow (<768px): single-column, full-width message thread

---

## Implementation Plan

### Phase 1: Backend Core
- Add `serve` command to cchat CLI
- FastAPI app with REST API endpoints (conversations, lines, agents, files, search, spending)
- JSONL file watcher with seek-based incremental reading
- WebSocket server for conversation streaming
- Serve static frontend from package data

### Phase 2: Frontend -- Conversation List + View
- Conversation list page with sorting, filtering, live indicators
- Conversation view with message thread rendering
- Tool call cards with specialized rendering per tool type
- Thinking block expansion
- Tool result matching (tool_use_id -> tool_result)
- WebSocket connection for live updates

### Phase 3: Subagent Support
- Subagent tree in sidebar
- Inline expansion of subagent conversations
- Full subagent view
- WebSocket subscription to subagent file changes

### Phase 4: Search + Spending
- Search page with highlighted results and navigation
- Spending dashboard with charts

### Phase 5: Polish
- Keyboard navigation
- Syntax highlighting (highlight.js via CDN for code blocks)
- Markdown rendering (marked.js via CDN)
- Dark/light theme toggle
- Cost/token visualization (sparklines, badges)

---

## Dependencies (new)

```
fastapi
uvicorn[standard]
watchfiles
```

No npm, no build step. Frontend is a single `index.html` with Preact + marked + highlight.js loaded from CDN, or bundled as static assets.
