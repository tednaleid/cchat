import { h, render } from 'preact';
import { useState, useEffect, useRef, useCallback, useMemo } from 'preact/hooks';
import htm from 'htm';
import { marked } from 'marked';
import hljs from 'highlight.js';
import DOMPurify from 'dompurify';

const html = htm.bind(h);

// ============================================================================
// Utilities
// ============================================================================

function formatTokens(n) {
  if (n == null) return '--';
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + 'M';
  if (n >= 1_000) return (n / 1_000).toFixed(1) + 'K';
  return String(n);
}

function formatCost(n) {
  if (n == null) return '--';
  return '$' + Number(n).toFixed(2);
}

function formatTime(ts) {
  if (!ts) return '--';
  const d = new Date(ts);
  const now = Date.now();
  const diff = (now - d.getTime()) / 1000;
  if (diff < 60) return 'just now';
  if (diff < 3600) return Math.floor(diff / 60) + 'm ago';
  if (diff < 86400) return Math.floor(diff / 3600) + 'h ago';
  if (diff < 604800) return Math.floor(diff / 86400) + 'd ago';
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}

function formatTimeAbsolute(ts) {
  if (!ts) return '--';
  const d = new Date(ts);
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) + ', ' +
    d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });
}

function decodeProject(key, path) {
  // Backend provides project_path already decoded; use it if available
  if (path) return path;
  if (!key) return '';
  const placeholder = '\x00';
  return key.replace(/--/g, placeholder).replace(/-/g, '/').replace(new RegExp(placeholder, 'g'), '-');
}

function costClass(cost) {
  if (cost == null) return 'badge-cost-low';
  if (cost < 0.5) return 'badge-cost-low';
  if (cost < 2.0) return 'badge-cost-med';
  return 'badge-cost-high';
}

function getCssSafeToolName(name) {
  if (!name) return 'unknown';
  return name.toLowerCase().replace(/[^a-z0-9]/g, '');
}

function truncate(s, n) {
  if (!s) return '';
  return s.length > n ? s.slice(0, n) + '...' : s;
}

// ============================================================================
// Line normalization: bridge REST _line_to_dict and raw WS JSONL into a
// uniform shape that components can consume.
//
// REST shape:  { line_number, type, subtype, timestamp, summary, tokens, model, usage, data: { type, message, ... } }
// WS shape:    raw JSONL: { type, message, uuid, timestamp, ... }
//
// Normalized:  { line_number, type, timestamp, message, data (raw), summary, tokens, model, usage }
// ============================================================================

function normalizeLine(line, lineNumber) {
  if (!line) return line;
  // Already normalized (has .message at top level without .data wrapper)?
  if (line.message && !line.data) return line;
  // REST shape: real content lives under .data
  if (line.data) {
    return {
      line_number: line.line_number != null ? line.line_number : lineNumber,
      type: line.type || (line.data && line.data.type),
      subtype: line.subtype,
      timestamp: line.timestamp || (line.data && line.data.timestamp),
      message: line.data.message || null,
      summary: line.summary,
      tokens: line.tokens,
      model: line.model || (line.data.message && line.data.message.model),
      usage: line.usage || (line.data.message && line.data.message.usage),
      data: line.data,
    };
  }
  // Raw WS JSONL (no .data wrapper, type at top level)
  return {
    line_number: line.line_number != null ? line.line_number : lineNumber,
    type: line.type,
    timestamp: line.timestamp,
    message: line.message || null,
    data: line,
  };
}

// ============================================================================
// Markdown setup
// ============================================================================

marked.setOptions({
  breaks: true,
  gfm: true,
  highlight: function(code, lang) {
    if (lang && hljs.getLanguage(lang)) {
      try { return hljs.highlight(code, { language: lang }).value; } catch (_) {}
    }
    try { return hljs.highlightAuto(code).value; } catch (_) {}
    return code;
  }
});

function renderMarkdown(text) {
  if (!text) return '';
  return DOMPurify.sanitize(marked.parse(text));
}

// ============================================================================
// API helpers
// ============================================================================

async function api(path, params) {
  const url = new URL(path, location.origin);
  if (params) {
    for (const [k, v] of Object.entries(params)) {
      if (v != null && v !== '') url.searchParams.set(k, v);
    }
  }
  const resp = await fetch(url);
  if (!resp.ok) throw new Error(`API ${resp.status}: ${resp.statusText}`);
  return resp.json();
}

// ============================================================================
// Router
// ============================================================================

function parseHash(hash) {
  const path = hash.slice(1) || '/';
  if (path === '/') return { page: 'list' };
  const convMatch = path.match(/^\/conversation\/(.+?)(?:\/agent\/(.+))?$/);
  if (convMatch) return { page: 'conversation', uuid: convMatch[1], agentId: convMatch[2] };
  if (path === '/search') return { page: 'search' };
  return { page: 'list' };
}

function useRouter() {
  const [route, setRoute] = useState(parseHash(location.hash));
  useEffect(() => {
    const handler = () => setRoute(parseHash(location.hash));
    window.addEventListener('hashchange', handler);
    return () => window.removeEventListener('hashchange', handler);
  }, []);
  return route;
}

function navigate(path) {
  location.hash = path;
}

// ============================================================================
// useWebSocket Hook
// ============================================================================

function useWebSocket(url, onMessage) {
  const wsRef = useRef(null);
  const retriesRef = useRef(0);
  const onMessageRef = useRef(onMessage);
  onMessageRef.current = onMessage;

  const send = useCallback((data) => {
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(data));
    }
  }, []);

  useEffect(() => {
    if (!url) return;

    let closed = false;
    let timer = null;

    function connect() {
      if (closed) return;
      const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
      const ws = new WebSocket(proto + '//' + location.host + url);
      wsRef.current = ws;

      ws.onopen = () => { retriesRef.current = 0; };
      ws.onmessage = (ev) => {
        try {
          const data = JSON.parse(ev.data);
          onMessageRef.current(data);
        } catch (_) {}
      };
      ws.onclose = () => {
        if (closed) return;
        const delay = Math.min(1000 * Math.pow(2, retriesRef.current), 30000);
        retriesRef.current++;
        timer = setTimeout(connect, delay);
      };
      ws.onerror = () => { ws.close(); };
    }

    connect();
    return () => {
      closed = true;
      if (timer) clearTimeout(timer);
      if (wsRef.current) wsRef.current.close();
    };
  }, [url]);

  return { send };
}

// ============================================================================
// Components: Shared
// ============================================================================

function Markdown({ text }) {
  const rendered = useMemo(() => renderMarkdown(text), [text]);
  return html`<div class="message-content" dangerouslySetInnerHTML=${{ __html: rendered }}></div>`;
}

function LoadingSpinner() {
  return html`<div class="loading"><div class="loading-spinner"></div> Loading...</div>`;
}

function EmptyState({ message }) {
  return html`<div class="empty-state">${message || 'Nothing here yet.'}</div>`;
}

function TokenBadge({ tokens, cost }) {
  return html`
    <span class="badge badge-tokens">${formatTokens(tokens)}</span>
    ${cost != null && html`<span class="badge ${costClass(cost)}">${formatCost(cost)}</span>`}
  `;
}

// ============================================================================
// ThinkingBlock
// ============================================================================

function ThinkingBlock({ text, forceExpanded }) {
  const [expanded, setExpanded] = useState(false);
  const isExpanded = forceExpanded || expanded;
  const charCount = text ? text.length : 0;

  return html`
    <div class="thinking-block">
      <button class="thinking-toggle ${isExpanded ? 'expanded' : ''}"
              onClick=${() => setExpanded(!expanded)}>
        <span class="caret">&#9654;</span>
        Thinking... (${formatTokens(charCount)} chars)
      </button>
      ${isExpanded && html`
        <div class="thinking-content">${text}</div>
      `}
    </div>
  `;
}

// ============================================================================
// ToolCallCard
// ============================================================================

function getToolSummary(name, input) {
  const n = (name || '').toLowerCase();
  if (n === 'bash' && input) return truncate((input.command || '').split('\n')[0], 80);
  if (n === 'read' && input) return input.file_path || '';
  if (n === 'write' && input) return input.file_path || '';
  if (n === 'edit' && input) return input.file_path || '';
  if (n === 'grep' && input) return input.pattern || '';
  if (n === 'glob' && input) return input.pattern || '';
  if ((n === 'agent' || n === 'dispatch' || n === 'skill') && input) return truncate(input.description || input.prompt || input.skill || '', 60);
  if (n === 'websearch' && input) return input.query || '';
  if (n === 'webfetch' && input) return input.url || '';
  return name || '';
}

function renderToolInput(name, input) {
  const n = (name || '').toLowerCase();

  if (n === 'bash' && input) {
    return html`
      <div class="tool-card-input">
        <div class="tool-card-label">Command</div>
        <pre class="tool-card-pre">${input.command || ''}</pre>
      </div>
    `;
  }

  if (n === 'read' && input) {
    let desc = input.file_path || '';
    if (input.offset) desc += ` (from line ${input.offset})`;
    if (input.limit) desc += ` (${input.limit} lines)`;
    return html`
      <div class="tool-card-input">
        <div class="tool-card-label">File</div>
        <pre class="tool-card-pre">${desc}</pre>
      </div>
    `;
  }

  if (n === 'write' && input) {
    return html`
      <div class="tool-card-input">
        <div class="tool-card-label">File</div>
        <pre class="tool-card-pre">${input.file_path || ''}</pre>
      </div>
    `;
  }

  if (n === 'edit' && input) {
    const oldLines = (input.old_string || '').split('\n');
    const newLines = (input.new_string || '').split('\n');
    return html`
      <div class="tool-card-input">
        <div class="tool-card-label">${input.file_path || 'Edit'}</div>
        <div class="diff-view">
          ${oldLines.map(l => html`<div class="diff-line diff-line-del">- ${l}</div>`)}
          ${newLines.map(l => html`<div class="diff-line diff-line-add">+ ${l}</div>`)}
        </div>
      </div>
    `;
  }

  if (n === 'grep' && input) {
    return html`
      <div class="tool-card-input">
        <div class="tool-card-label">Search</div>
        <pre class="tool-card-pre">${input.pattern || ''}${input.path ? ' in ' + input.path : ''}</pre>
      </div>
    `;
  }

  if (n === 'glob' && input) {
    return html`
      <div class="tool-card-input">
        <div class="tool-card-label">Pattern</div>
        <pre class="tool-card-pre">${input.pattern || ''}</pre>
      </div>
    `;
  }

  if ((n === 'agent' || n === 'dispatch' || n === 'skill') && input) {
    return html`
      <div class="tool-card-input">
        <div class="tool-card-label">Prompt</div>
        <pre class="tool-card-pre" style="max-height:150px">${input.prompt || input.description || JSON.stringify(input, null, 2)}</pre>
      </div>
    `;
  }

  if (n === 'websearch' && input) {
    return html`
      <div class="tool-card-input">
        <div class="tool-card-label">Query</div>
        <pre class="tool-card-pre">${input.query || ''}</pre>
      </div>
    `;
  }

  if (n === 'webfetch' && input) {
    return html`
      <div class="tool-card-input">
        <div class="tool-card-label">URL</div>
        <pre class="tool-card-pre">${input.url || ''}</pre>
      </div>
    `;
  }

  // Default
  return html`
    <div class="tool-card-input">
      <div class="tool-card-label">Input</div>
      <pre class="tool-card-pre">${JSON.stringify(input, null, 2)}</pre>
    </div>
  `;
}

function renderToolResult(name, result) {
  if (result == null) return null;
  const n = (name || '').toLowerCase();

  // Extract text from result
  let text = '';
  if (typeof result === 'string') {
    text = result;
  } else if (Array.isArray(result)) {
    text = result.map(b => (b && b.text) || '').join('\n');
  } else if (result.text) {
    text = result.text;
  } else {
    text = JSON.stringify(result, null, 2);
  }

  if (n === 'write') text = text || '(file written)';
  if (n === 'edit') text = text || '(edit applied)';

  // Agent/subagent: parse the result and show as a compact status card
  if (n === 'agent') {
    // Result text is typically multi-line with agent metadata
    const lines = text.split('\n').filter(l => l.trim());
    const statusLine = lines.find(l => /completed|failed|error/i.test(l)) || lines[0] || '';
    const isCompleted = /completed/i.test(statusLine);
    const isFailed = /failed|error/i.test(statusLine);
    const statusColor = isFailed ? '#e06c75' : isCompleted ? '#98c379' : '#e5c07b';
    // Try to extract a summary from remaining lines (skip metadata-looking lines)
    const summaryLines = lines.filter(l => !/^(agent_id|name|team_name|Spawned|The agent)/i.test(l.trim()));
    const summary = summaryLines.slice(0, 5).join('\n');

    return html`
      <div class="tool-card-result" style="border-left: 3px solid ${statusColor}; padding-left: 8px;">
        <div class="tool-card-label" style="color: ${statusColor}">
          ${isCompleted ? 'Completed' : isFailed ? 'Failed' : 'Result'}
        </div>
        ${summary && html`<pre class="tool-card-pre" style="max-height:200px">${summary}</pre>`}
      </div>
    `;
  }

  return html`
    <div class="tool-card-result">
      <div class="tool-card-label">Result</div>
      <pre class="tool-card-pre">${text}</pre>
    </div>
  `;
}

function ToolCallCard({ block, result, defaultExpanded }) {
  const [expanded, setExpanded] = useState(defaultExpanded || false);
  const toolName = block.name || 'unknown';
  const cssName = getCssSafeToolName(toolName);
  const summary = getToolSummary(toolName, block.input);

  return html`
    <div class="tool-card tool-${cssName}">
      <div class="tool-card-header ${expanded ? 'expanded' : ''}"
           onClick=${() => setExpanded(!expanded)}>
        <span class="caret">&#9654;</span>
        <span class="tool-card-name">${toolName}</span>
        <span class="tool-card-summary">${summary}</span>
      </div>
      ${expanded && html`
        <div class="tool-card-body">
          ${renderToolInput(toolName, block.input)}
          ${renderToolResult(toolName, result)}
        </div>
      `}
    </div>
  `;
}

// ============================================================================
// Message Components
// ============================================================================

function UserMessage({ line, selected, onClick }) {
  const content = line.message && line.message.content;
  let textParts = [];

  if (typeof content === 'string') {
    textParts.push(content);
  } else if (Array.isArray(content)) {
    for (const block of content) {
      if (block.type === 'text') textParts.push(block.text);
      // skip tool_result blocks -- handled by ToolCallCard
    }
  }

  const text = textParts.join('\n');
  if (!text) return null;

  return html`
    <div class="message message-user ${selected ? 'selected' : ''}" onClick=${onClick}>
      <div class="message-header">
        <span class="message-role">User</span>
        <span class="message-timestamp">${formatTimeAbsolute(line.timestamp)}</span>
      </div>
      <${Markdown} text=${text} />
    </div>
  `;
}

function AssistantMessage({ line, toolResults, selected, onClick, thinkingExpanded }) {
  const msg = line.message || {};
  const content = msg.content || [];
  const usage = line.usage || msg.usage;
  const model = line.model || msg.model;

  const totalTokens = usage ? (usage.input_tokens || 0) + (usage.output_tokens || 0) : (line.tokens || null);
  const cost = null; // cost is conversation-level, not per-message

  return html`
    <div class="message message-assistant ${selected ? 'selected' : ''}" onClick=${onClick}>
      <div class="message-header">
        <span class="message-role">Assistant</span>
        ${model && html`<span class="badge" style="background:rgba(124,92,191,0.2);color:#b8a0d8">${model}</span>`}
        <span class="message-timestamp">${formatTimeAbsolute(line.timestamp)}</span>
        <div class="message-badges">
          ${totalTokens != null && html`<${TokenBadge} tokens=${totalTokens} cost=${cost} />`}
        </div>
      </div>
      ${Array.isArray(content) && content.map(block => {
        if (block.type === 'thinking') {
          return html`<${ThinkingBlock} text=${block.thinking} forceExpanded=${thinkingExpanded} />`;
        }
        if (block.type === 'text') {
          return html`<${Markdown} text=${block.text} />`;
        }
        if (block.type === 'tool_use') {
          const result = toolResults.get(block.id);
          return html`<${ToolCallCard} block=${block} result=${result} />`;
        }
        return null;
      })}
    </div>
  `;
}

function SystemMessage({ line }) {
  return html`
    <div class="message message-system">
      <hr />
      <span>${line.message && line.message.content || 'System event'}</span>
    </div>
  `;
}

// ============================================================================
// MessageThread
// ============================================================================

function MessageThread({ lines, selectedIndex, onSelectMessage, thinkingExpanded }) {
  // Build tool_use_id -> result map
  const toolResults = useMemo(() => {
    const map = new Map();
    for (const line of lines) {
      if (line.type !== 'user') continue;
      const content = line.message && line.message.content;
      if (!Array.isArray(content)) continue;
      for (const block of content) {
        if (block.type === 'tool_result' && block.tool_use_id) {
          let text = block.content;
          map.set(block.tool_use_id, text);
        }
      }
    }
    return map;
  }, [lines]);

  // Group lines into messages: merge consecutive assistant lines with same message.id
  const messages = useMemo(() => {
    const result = [];
    for (const line of lines) {
      result.push(line);
    }
    return result;
  }, [lines]);

  return html`
    <div class="message-thread">
      ${messages.map((line, i) => {
        const selected = i === selectedIndex;
        const onClick = () => onSelectMessage(i);

        // Skip non-display line types
        const t = line.type;
        if (t === 'progress' || t === 'file-history-snapshot' || t === 'queue-operation'
            || t === 'permission-mode' || t === 'custom-title' || t === 'agent-name'
            || t === 'attachment') return null;

        if (t === 'user') {
          return html`<${UserMessage} key=${line.line_number || i} line=${line} selected=${selected} onClick=${onClick} />`;
        }
        if (t === 'assistant') {
          return html`<${AssistantMessage} key=${line.line_number || i} line=${line} toolResults=${toolResults}
                        selected=${selected} onClick=${onClick} thinkingExpanded=${thinkingExpanded} />`;
        }
        if (t === 'system') {
          return html`<${SystemMessage} key=${line.line_number || i} line=${line} />`;
        }
        return null;
      })}
    </div>
  `;
}

// ============================================================================
// SubagentTree
// ============================================================================

function SubagentTree({ agents, convUuid }) {
  if (!agents || agents.length === 0) return null;

  return html`
    <div class="sidebar-section">
      <div class="sidebar-section-title">Subagents (${agents.length})</div>
      <ul class="subagent-tree">
        ${agents.map(a => html`
          <li class="subagent-item" key=${a.agent_id}
              onClick=${() => navigate('/conversation/' + convUuid + '/agent/' + a.agent_id)}>
            <div class="agent-name">${truncate(a.description || a.agent_id, 40)}</div>
            <div class="agent-type">${a.agent_type || 'agent'}</div>
            <div class="agent-cost">
              ${formatTokens(a.total_tokens)} tokens
              ${a.estimated_cost_usd != null ? ' / ' + formatCost(a.estimated_cost_usd) : ''}
            </div>
          </li>
        `)}
      </ul>
    </div>
  `;
}

// ============================================================================
// TurnNavigator
// ============================================================================

function TurnNavigator({ lines, onJumpTo }) {
  // Show user messages as navigation points
  const turns = useMemo(() => {
    const result = [];
    let turnNum = 0;
    for (let i = 0; i < lines.length; i++) {
      if (lines[i].type === 'user') {
        turnNum++;
        const content = lines[i].message && lines[i].message.content;
        let preview = '';
        if (typeof content === 'string') {
          preview = content;
        } else if (Array.isArray(content)) {
          const textBlock = content.find(b => b.type === 'text');
          preview = textBlock ? textBlock.text : '';
        }
        result.push({ index: i, turnNum, preview: truncate(preview, 50) });
      }
    }
    return result;
  }, [lines]);

  if (turns.length === 0) return null;

  return html`
    <div class="sidebar-section">
      <div class="sidebar-section-title">Turns (${turns.length})</div>
      <ul class="turn-nav">
        ${turns.map(t => html`
          <li key=${t.index} onClick=${() => onJumpTo(t.index)}>
            <strong>${t.turnNum}.</strong> ${t.preview}
          </li>
        `)}
      </ul>
    </div>
  `;
}

// ============================================================================
// ConversationView
// ============================================================================

function ConversationView({ uuid, agentId }) {
  const [conv, setConv] = useState(null);
  const [lines, setLines] = useState([]);
  const [files, setFiles] = useState([]);
  const [agents, setAgents] = useState([]);
  const [loading, setLoading] = useState(true);
  const [selectedIndex, setSelectedIndex] = useState(-1);
  const [detailVisible, setDetailVisible] = useState(false);
  const [thinkingExpanded, setThinkingExpanded] = useState(false);
  const [showNewIndicator, setShowNewIndicator] = useState(false);

  const mainRef = useRef(null);
  const atBottomRef = useRef(true);

  // Fetch data
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setLines([]);
    setConv(null);
    setFiles([]);
    setAgents([]);
    setSelectedIndex(-1);

    const linesUrl = agentId
      ? `/api/conversations/${uuid}/agents/${agentId}/lines`
      : `/api/conversations/${uuid}/lines`;

    Promise.all([
      api(`/api/conversations/${uuid}`),
      api(linesUrl, { deduplicate: true, limit: 500 }),
      api(`/api/conversations/${uuid}/files`).catch(() => ({ files: [] })),
      api(`/api/conversations/${uuid}/agents`).catch(() => ({ agents: [] })),
    ]).then(([convData, linesData, filesData, agentsData]) => {
      if (cancelled) return;
      setConv(convData);
      setLines((linesData.lines || []).map((l, i) => normalizeLine(l, i)));
      setFiles(filesData.files || []);
      setAgents(agentsData.agents || []);
      setLoading(false);
    }).catch(err => {
      if (cancelled) return;
      console.error('Failed to load conversation:', err);
      setLoading(false);
    });

    return () => { cancelled = true; };
  }, [uuid, agentId]);

  // Auto-scroll tracking
  useEffect(() => {
    const el = mainRef.current;
    if (!el) return;
    const onScroll = () => {
      atBottomRef.current = (el.scrollHeight - el.scrollTop - el.clientHeight) < 200;
      if (atBottomRef.current) setShowNewIndicator(false);
    };
    el.addEventListener('scroll', onScroll, { passive: true });
    return () => el.removeEventListener('scroll', onScroll);
  }, []);

  // Auto-scroll on new lines
  useEffect(() => {
    const el = mainRef.current;
    if (!el) return;
    if (atBottomRef.current) {
      el.scrollTop = el.scrollHeight;
    } else if (lines.length > 0) {
      setShowNewIndicator(true);
    }
  }, [lines.length]);

  // WebSocket for live updates
  const wsUrl = agentId ? null : `/ws/conversation/${uuid}`;
  useWebSocket(wsUrl, useCallback((msg) => {
    if (msg.event === 'line_append' && msg.data) {
      // WS sends raw JSONL in data; normalize to match REST shape
      const normalized = normalizeLine(msg.data, msg.line_number);
      normalized.line_number = msg.line_number;
      setLines(prev => [...prev, normalized]);
    } else if ((msg.event === 'message_update' || msg.event === 'message_final') && msg.data) {
      // Find by message_id first, fall back to line_number
      setLines(prev => {
        let idx = -1;
        if (msg.message_id) {
          idx = prev.findIndex(l => l.message && l.message.id === msg.message_id);
        }
        if (idx < 0 && msg.line_number != null) {
          idx = prev.findIndex(l => l.line_number === msg.line_number);
        }
        if (idx >= 0) {
          const updated = [...prev];
          const old = updated[idx];
          updated[idx] = { ...old, message: { ...old.message, content: msg.data.content, usage: msg.data.usage, stop_reason: msg.data.stop_reason } };
          return updated;
        }
        return prev;
      });
    } else if (msg.event === 'subagent_new' && msg.meta) {
      setAgents(prev => [...prev, { agent_id: msg.agent_id, ...msg.meta }]);
    }
  }, []));

  // Keyboard navigation
  useEffect(() => {
    const handler = (e) => {
      const tag = e.target.tagName;
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;

      if (e.key === 'j') {
        setSelectedIndex(prev => Math.min(prev + 1, lines.length - 1));
      } else if (e.key === 'k') {
        setSelectedIndex(prev => Math.max(prev - 1, 0));
      } else if (e.key === 't') {
        setThinkingExpanded(prev => !prev);
      } else if (e.key === 'Escape') {
        setDetailVisible(false);
        setSelectedIndex(-1);
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [lines.length]);

  const scrollToBottom = useCallback(() => {
    const el = mainRef.current;
    if (el) el.scrollTop = el.scrollHeight;
    setShowNewIndicator(false);
  }, []);

  const jumpToMessage = useCallback((index) => {
    setSelectedIndex(index);
    const el = mainRef.current;
    if (!el) return;
    const messages = el.querySelectorAll('.message');
    if (messages[index]) {
      messages[index].scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
  }, []);

  if (loading) return html`<${LoadingSpinner} />`;
  if (!conv) return html`<${EmptyState} message="Conversation not found." />`;

  return html`
    <div class="conv-view">
      <div class="conv-sidebar">
        <div class="sidebar-section">
          <div class="sidebar-section-title">Conversation</div>
          <div class="sidebar-meta">
            <div class="sidebar-meta-row">
              <span class="sidebar-meta-label">Slug</span>
              <span class="sidebar-meta-value copyable" title=${conv.slug || ''}>${conv.slug || '--'}</span>
            </div>
            <div class="sidebar-meta-row">
              <span class="sidebar-meta-label">Project</span>
              <span class="sidebar-meta-value" title=${conv.project_key || ''}>${decodeProject(conv.project_key, conv.project_path)}</span>
            </div>
            <div class="sidebar-meta-row">
              <span class="sidebar-meta-label">Model</span>
              <span class="sidebar-meta-value">${conv.model || '--'}</span>
            </div>
            <div class="sidebar-meta-row">
              <span class="sidebar-meta-label">Started</span>
              <span class="sidebar-meta-value">${formatTimeAbsolute(conv.first_timestamp)}</span>
            </div>
            <div class="sidebar-meta-row">
              <span class="sidebar-meta-label">Last Active</span>
              <span class="sidebar-meta-value">${formatTime(conv.last_timestamp)}</span>
            </div>
            <div class="sidebar-meta-row">
              <span class="sidebar-meta-label">Turns</span>
              <span class="sidebar-meta-value">${conv.turn_count || '--'}</span>
            </div>
            <div class="sidebar-meta-row">
              <span class="sidebar-meta-label">Tokens</span>
              <span class="sidebar-meta-value">${formatTokens(conv.total_tokens)}</span>
            </div>
            <div class="sidebar-meta-row">
              <span class="sidebar-meta-label">Cost</span>
              <span class="sidebar-meta-value">${formatCost(conv.estimated_cost_usd)}</span>
            </div>
          </div>
        </div>

        ${agentId && html`
          <div class="sidebar-section">
            <div class="sidebar-section-title">Viewing Agent</div>
            <div class="sidebar-meta">
              <div class="sidebar-meta-row">
                <span class="sidebar-meta-label">ID</span>
                <span class="sidebar-meta-value">${truncate(agentId, 20)}</span>
              </div>
            </div>
            <div style="margin-top:8px">
              <a href="#/conversation/${uuid}">Back to main conversation</a>
            </div>
          </div>
        `}

        <${SubagentTree} agents=${agents} convUuid=${uuid} />

        ${files.length > 0 && html`
          <div class="sidebar-section">
            <div class="sidebar-section-title">Files (${files.length})</div>
            <ul class="files-list">
              ${files.slice(0, 30).map(f => html`
                <li key=${f.path} title=${f.path}>
                  ${f.path.split('/').pop() || f.path}
                  <span class="file-count">${f.modifications || ''}</span>
                </li>
              `)}
              ${files.length > 30 && html`<li style="color:var(--text-dim)">...and ${files.length - 30} more</li>`}
            </ul>
          </div>
        `}

        <${TurnNavigator} lines=${lines} onJumpTo=${jumpToMessage} />
      </div>

      <div class="conv-main" ref=${mainRef}>
        <${MessageThread} lines=${lines} selectedIndex=${selectedIndex}
            onSelectMessage=${setSelectedIndex} thinkingExpanded=${thinkingExpanded} />
        ${showNewIndicator && html`
          <div class="new-messages-indicator">
            <button onClick=${scrollToBottom}>New messages below</button>
          </div>
        `}
      </div>

      <div class="conv-detail ${detailVisible ? 'visible' : ''}">
        ${selectedIndex >= 0 && selectedIndex < lines.length ? html`
          <div class="detail-section">
            <div class="detail-section-title">Line Details</div>
            <pre class="detail-json">${JSON.stringify(lines[selectedIndex], null, 2)}</pre>
          </div>
        ` : html`
          <div class="empty-state">Select a message to view details</div>
        `}
      </div>
    </div>
  `;
}

// ============================================================================
// ConversationList
// ============================================================================

function ConversationList() {
  const [conversations, setConversations] = useState([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [sort, setSort] = useState({ field: 'last_active', order: 'desc' });
  const [projectFilter, setProjectFilter] = useState('');
  const [searchText, setSearchText] = useState('');
  const [offset, setOffset] = useState(0);
  const [projects, setProjects] = useState([]);
  const limit = 50;

  // Fetch projects list for filter dropdown
  useEffect(() => {
    api('/api/projects').then(data => {
      setProjects((data.projects || []).map(p => p));
    }).catch(() => {});
  }, []);

  const fetchConversations = useCallback(() => {
    setLoading(true);
    api('/api/conversations', {
      sort: sort.field,
      order: sort.order,
      project: projectFilter,
      limit,
      offset,
    }).then(data => {
      setConversations(data.conversations || []);
      setTotal(data.total || 0);
      setLoading(false);
    }).catch(err => {
      console.error('Failed to fetch conversations:', err);
      setLoading(false);
    });
  }, [sort, projectFilter, offset]);

  useEffect(() => { fetchConversations(); }, [fetchConversations]);

  // WebSocket for live updates
  useWebSocket('/ws/conversations', useCallback((msg) => {
    if (msg.event === 'conversation_updated' && msg.uuid) {
      // Backend sends { event, uuid, is_active } at top level
      // Mark the conversation as active/inactive; refetch to get full data for new conversations
      setConversations(prev => {
        const idx = prev.findIndex(c => c.uuid === msg.uuid);
        if (idx >= 0) {
          const updated = [...prev];
          updated[idx] = { ...updated[idx], is_active: msg.is_active, last_timestamp: new Date().toISOString() };
          return updated;
        }
        return prev;
      });
      // Refetch to pick up new conversations or updated metadata
      fetchConversations();
    }
  }, [fetchConversations]));

  const toggleSort = (field) => {
    setSort(prev => ({
      field,
      order: prev.field === field && prev.order === 'desc' ? 'asc' : 'desc',
    }));
    setOffset(0);
  };

  const sortArrow = (field) => {
    if (sort.field !== field) return '';
    return sort.order === 'desc' ? ' v' : ' ^';
  };

  // Client-side text filter
  const filtered = useMemo(() => {
    if (!searchText) return conversations;
    const q = searchText.toLowerCase();
    return conversations.filter(c =>
      (c.slug && c.slug.toLowerCase().includes(q)) ||
      (c.snippet && c.snippet.toLowerCase().includes(q)) ||
      (c.uuid && c.uuid.toLowerCase().includes(q))
    );
  }, [conversations, searchText]);

  const totalPages = Math.ceil(total / limit);
  const currentPage = Math.floor(offset / limit) + 1;

  return html`
    <div class="conv-list-page">
      <div class="conv-list-controls">
        <select value=${projectFilter} onChange=${e => { setProjectFilter(e.target.value); setOffset(0); }}>
          <option value="">All projects</option>
          ${projects.map(p => html`<option key=${p.key} value=${p.key}>${p.path || decodeProject(p.key)}</option>`)}
        </select>
        <input type="text" placeholder="Filter by title/slug..."
               value=${searchText} onInput=${e => setSearchText(e.target.value)} />
      </div>

      ${loading ? html`<${LoadingSpinner} />` : filtered.length === 0 ? html`<${EmptyState} message="No conversations found." />` : html`
        <table class="conv-table">
          <thead>
            <tr>
              <th>Title</th>
              <th>Project</th>
              <th onClick=${() => toggleSort('created')}>Started${sortArrow('created')}</th>
              <th onClick=${() => toggleSort('last_active')}>Last Active${sortArrow('last_active')}</th>
              <th class="col-num">Turns</th>
              <th class="col-num">Agents</th>
              <th>Model</th>
              <th class="col-num" onClick=${() => toggleSort('tokens')}>Tokens${sortArrow('tokens')}</th>
              <th class="col-num" onClick=${() => toggleSort('cost')}>Cost${sortArrow('cost')}</th>
            </tr>
          </thead>
          <tbody>
            ${filtered.map(c => html`
                <tr key=${c.uuid} onClick=${() => navigate('/conversation/' + c.uuid)}>
                  <td class="col-title">
                    ${c.is_active && html`<span class="pulse-dot"></span>`}
                    ${c.slug || c.snippet || truncate(c.uuid, 12)}
                  </td>
                  <td class="col-project">${decodeProject(c.project_key, c.project_path)}</td>
                  <td>${formatTime(c.first_timestamp)}</td>
                  <td>${formatTime(c.last_timestamp)}</td>
                  <td class="col-num">${c.turn_count || '--'}</td>
                  <td class="col-num">${c.agent_count || 0}</td>
                  <td>${c.model || '--'}</td>
                  <td class="col-num">${formatTokens(c.total_tokens)}</td>
                  <td class="col-num">
                    <span class="badge ${costClass(c.estimated_cost_usd)}">${formatCost(c.estimated_cost_usd)}</span>
                  </td>
                </tr>
            `)}
          </tbody>
        </table>

        ${totalPages > 1 && html`
          <div class="pagination">
            <button disabled=${offset === 0} onClick=${() => setOffset(Math.max(0, offset - limit))}>Prev</button>
            <span class="page-info">Page ${currentPage} of ${totalPages}</span>
            <button disabled=${offset + limit >= total} onClick=${() => setOffset(offset + limit)}>Next</button>
          </div>
        `}
      `}
    </div>
  `;
}

// ============================================================================
// SearchPage
// ============================================================================

function SearchPage() {
  const [query, setQuery] = useState('');
  const [results, setResults] = useState([]);
  const [loading, setLoading] = useState(false);
  const [searched, setSearched] = useState(false);
  const timerRef = useRef(null);

  const doSearch = useCallback((q) => {
    if (!q || q.length < 2) { setResults([]); setSearched(false); return; }
    setLoading(true);
    setSearched(true);
    api('/api/search', { q, sort: 'newest', limit: 50 }).then(data => {
      setResults(data.results || []);
      setLoading(false);
    }).catch(() => {
      setResults([]);
      setLoading(false);
    });
  }, []);

  const onInput = useCallback((e) => {
    const val = e.target.value;
    setQuery(val);
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => doSearch(val), 400);
  }, [doSearch]);

  return html`
    <div class="search-page">
      <div class="search-input-wrap">
        <input type="text" placeholder="Search conversations..."
               value=${query} onInput=${onInput} autofocus />
      </div>
      ${loading ? html`<${LoadingSpinner} />` :
        results.length === 0 && searched ? html`<${EmptyState} message="No results found." />` :
        results.map(r => html`
          <div class="search-result" key=${r.conversation_uuid + '-' + r.line_number}
               onClick=${() => navigate('/conversation/' + r.conversation_uuid)}>
            <div class="search-result-title">${r.slug || truncate(r.conversation_uuid, 20)}</div>
            <div class="search-result-snippet">${r.snippet || ''}</div>
            <div class="search-result-meta">
              ${r.type || ''} -- ${formatTime(r.timestamp)}
            </div>
          </div>
        `)
      }
    </div>
  `;
}

// ============================================================================
// App Root
// ============================================================================

function App() {
  const route = useRouter();
  const searchInputRef = useRef(null);

  // Global keyboard shortcut for /
  useEffect(() => {
    const handler = (e) => {
      const tag = e.target.tagName;
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
      if (e.key === '/') {
        e.preventDefault();
        if (searchInputRef.current) searchInputRef.current.focus();
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, []);

  const onHeaderSearch = useCallback((e) => {
    if (e.key === 'Enter' && e.target.value) {
      navigate('/search');
    }
  }, []);

  let content;
  if (route.page === 'conversation') {
    content = html`<${ConversationView} uuid=${route.uuid} agentId=${route.agentId} />`;
  } else if (route.page === 'search') {
    content = html`<${SearchPage} />`;
  } else {
    content = html`<${ConversationList} />`;
  }

  return html`
    <div>
      <div class="header">
        <a class="header-logo" href="#/">cchat</a>
        <nav class="header-nav">
          <a href="#/" class=${route.page === 'list' ? 'active' : ''}>Conversations</a>
          <a href="#/search" class=${route.page === 'search' ? 'active' : ''}>Search</a>
        </nav>
        <div class="header-spacer"></div>
        <input class="header-search" type="text" placeholder="Search... (press /)"
               ref=${searchInputRef} onKeyDown=${onHeaderSearch} />
      </div>
      ${content}
    </div>
  `;
}

// ============================================================================
// Mount
// ============================================================================

render(html`<${App} />`, document.getElementById('app'));
