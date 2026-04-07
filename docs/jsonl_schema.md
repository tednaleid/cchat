# Claude Code Conversation JSONL Schema

## Storage Location

```
~/.claude/projects/<project-key>/<uuid>.jsonl
```

- Each project directory is named by its path with path separators replaced by `--` (e.g. `C--git-l-sc/` for `C:\git\l\sc`)
- Each conversation is a UUID-named `.jsonl` file (one JSON object per line)
- Sub-agent conversations are nested under `<uuid>/subagents/agent-*.jsonl`
- `~/.claude/history.jsonl` contains a global index with timestamps, project paths, and session IDs

## Line Format

Each line is a JSON object. Lines chain via `parentUuid` → `uuid` (first line has `parentUuid: null`).

**Common fields** on most lines: `type`, `uuid`, `parentUuid`, `timestamp`, `sessionId`, `cwd`, `gitBranch`, `version`, `slug`, `isSidechain`.

## Line Types (`type` field)

- **`system`** — metadata. `subtype` is `"bridge_status"` (session start, has `url`) or `"turn_duration"` (turn end, has `durationMs`).
- **`user`** — human message or tool result.
  - Human message: `message.content` is a string. Has `promptId`, `permissionMode`.
  - Tool result: `message.content` is array of `{type: "tool_result", tool_use_id, content: [{type: "text", text}]}`. Has `sourceToolAssistantUUID` (points to assistant line that made the call) and `toolUseResult` object.
- **`assistant`** — LLM response. `message` is an Anthropic API message object.
  - `message.content` is array of content items: `{type: "text", text}`, `{type: "thinking", thinking, signature}`, or `{type: "tool_use", id, name, input}`.
  - **Streaming**: multiple lines share the same `message.id`; intermediate lines have `stop_reason: null`, final line has `"end_turn"` or `"tool_use"`.
  - `message.usage` has `input_tokens`, `output_tokens`, `cache_read_input_tokens`, etc.
- **`progress`** — subagent execution updates. `data.type` is `"agent_progress"`, `toolUseID` links to spawning tool_use.
- **`file-history-snapshot`** — file state for undo. Has `snapshot.trackedFileBackups` mapping paths to backup data.

## Conversation Flow

`system/bridge_status` → `user` (human) → `assistant` (streaming chunks) → `user` (tool results) → `assistant` → ... → `system/turn_duration`
