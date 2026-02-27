#!/usr/bin/env python3
"""
Recall Web Interface — Search conversations and files via browser.
Rewritten 2026-02-21 with auto-search, context expansion, updated agents.
"""

from flask import Flask, render_template_string, request, jsonify
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from recall import unified_search
from search import DB_PATH
import re


def generate_deep_link(content: str) -> str | None:
    """
    Extract platform info and generate a deep link to the original message.
    Returns None if no link can be generated.
    """
    msg_match = re.search(r'\[message_id:\s*(\d+)\]', content)
    if not msg_match:
        return None
    message_id = msg_match.group(1)

    discord_match = re.search(r'\[Discord.*?channel id:(\d+)', content)
    if discord_match:
        channel_id = discord_match.group(1)
        return f"https://discord.com/channels/@me/{channel_id}/{message_id}"

    return None


app = Flask(__name__)

HTML_TEMPLATE = r"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Recall — Memory Search</title>
    <link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>🔍</text></svg>">
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #1a1a2e;
            color: #eee;
            min-height: 100vh;
            padding: 20px;
        }
        .container { max-width: 900px; margin: 0 auto; }
        h1 {
            text-align: center;
            margin-bottom: 20px;
            color: #00d9ff;
            font-size: 2.2em;
        }
        .subtitle {
            text-align: center; margin-bottom: 15px; font-size: 13px;
        }
        .subtitle a { color: #00d9ff; text-decoration: none; }
        .subtitle a:hover { text-decoration: underline; }
        .search-box {
            display: flex;
            gap: 10px;
            margin-bottom: 14px;
            position: relative;
        }
        input[type="text"] {
            flex: 1;
            padding: 13px 18px;
            font-size: 16px;
            border: 2px solid #333;
            border-radius: 10px;
            background: #16213e;
            color: #fff;
            transition: border-color 0.2s;
        }
        input[type="text"]:focus {
            outline: none;
            border-color: #00d9ff;
        }
        .search-btn {
            padding: 13px 28px;
            font-size: 16px;
            background: #00d9ff;
            color: #1a1a2e;
            border: none;
            border-radius: 10px;
            cursor: pointer;
            font-weight: bold;
            transition: background 0.2s;
        }
        .search-btn:hover { background: #00b8d9; }

        /* === Agent Pills === */
        .agent-bar {
            display: flex; gap: 6px; flex-wrap: wrap; align-items: center;
            margin-bottom: 12px;
        }
        .agent-pill {
            display: inline-flex; align-items: center; gap: 4px;
            padding: 5px 12px; border-radius: 16px;
            font-size: 12px; font-weight: 600; cursor: pointer;
            border: 2px solid transparent;
            background: #16213e; color: #888;
            transition: all 0.2s; user-select: none;
        }
        .agent-pill:hover { background: #1a2744; color: #ddd; }
        .agent-pill .pill-count { font-weight: normal; opacity: 0.7; font-size: 0.9em; }
        .agent-pill.active { color: #fff; }
        .agent-pill.active.pill-all     { background: #0f3460; border-color: #00d9ff; color: #00d9ff; }
        .agent-pill.active.pill-kit     { background: rgba(37,99,235,0.25); border-color: #2563eb; color: #5b9aff; }
        .agent-pill.active.pill-cc      { background: rgba(6,182,212,0.25); border-color: #06b6d4; color: #22d3ee; }
        .agent-pill.active.pill-claude  { background: rgba(139,92,246,0.25); border-color: #8b5cf6; color: #a78bfa; }
        .agent-pill.active.pill-cyrus   { background: rgba(245,158,11,0.25); border-color: #f59e0b; color: #fbbf24; }
        .agent-pill.active.pill-damian  { background: rgba(239,68,68,0.25); border-color: #ef4444; color: #f87171; }
        .agent-pill.active.pill-grok    { background: rgba(16,185,129,0.25); border-color: #10b981; color: #34d399; }
        .agent-pill.active.pill-chat    { background: rgba(99,102,241,0.25); border-color: #6366f1; color: #818cf8; }
        .agent-pill.active.pill-arthur  { background: rgba(236,72,153,0.25); border-color: #ec4899; color: #f472b6; }
        .agent-pill.active.pill-hale    { background: rgba(20,184,166,0.25); border-color: #14b8a6; color: #2dd4bf; }
        .agent-pill.active.pill-roman   { background: rgba(249,115,22,0.25); border-color: #f97316; color: #fb923c; }
        .agent-pill.active.pill-sterling { background: rgba(168,85,247,0.25); border-color: #a855f7; color: #c084fc; }
        .date-range-select {
            margin-left: auto; padding: 5px 10px; font-size: 12px;
            border-radius: 16px; background: #16213e; color: #aaa;
            border: 1px solid #333; cursor: pointer;
        }

        .options {
            display: flex; gap: 16px; margin-bottom: 14px; flex-wrap: wrap; align-items: center;
        }
        .options label {
            display: flex; align-items: center; gap: 6px; cursor: pointer; font-size: 13px;
        }
        .options input[type="checkbox"] { width: 16px; height: 16px; accent-color: #00d9ff; }
        select {
            padding: 6px 12px; font-size: 13px; border-radius: 5px;
            background: #16213e; color: #fff; border: 1px solid #333;
        }

        .semantic-hint { color: #f90; font-size: 0.82em; margin-left: 12px; }
        .search-indicator {
            display: none; align-items: center; gap: 8px; color: #888; font-size: 0.9em; margin-bottom: 10px;
        }
        .search-indicator.visible { display: flex; }
        .spinner {
            width: 16px; height: 16px; border: 2px solid #333;
            border-top-color: #00d9ff; border-radius: 50%;
            animation: spin 0.6s linear infinite;
        }
        @keyframes spin { to { transform: rotate(360deg); } }

        /* Results & cards */
        .results { margin-top: 16px; }
        .section-title {
            font-size: 1.2em; color: #00d9ff; margin: 16px 0 8px;
            padding-bottom: 5px; border-bottom: 1px solid #333;
            display: flex; align-items: center; gap: 10px;
        }
        .result-count {
            font-size: 0.6em; background: #0f3460; color: #aaa;
            padding: 3px 10px; border-radius: 12px; font-weight: normal;
        }
        .result-card {
            background: #16213e; border-radius: 10px; padding: 14px;
            margin-bottom: 8px; cursor: pointer;
            transition: background 0.15s, border-color 0.2s;
            border: 1px solid transparent;
        }
        .result-card:hover { background: #1a2744; border-color: #333; }
        .result-card.expanded { border-color: rgba(0,217,255,0.3); }
        .result-header {
            display: flex; gap: 10px; font-size: 0.85em; color: #888;
            margin-bottom: 6px; align-items: center; flex-wrap: wrap;
        }
        .agent-badge {
            padding: 2px 10px; border-radius: 4px; font-weight: bold;
            font-size: 0.82em; text-transform: uppercase; letter-spacing: 0.5px;
        }
        .agent-kit       { background: #2563eb; color: #fff; }
        .agent-cc        { background: #06b6d4; color: #1a1a2e; }
        .agent-claude    { background: #8b5cf6; color: #fff; }
        .agent-cyrus     { background: #f59e0b; color: #1a1a2e; }
        .agent-damian    { background: #ef4444; color: #fff; }
        .agent-grok      { background: #10b981; color: #1a1a2e; }
        .agent-chat      { background: #6366f1; color: #fff; }
        .agent-arthur    { background: #ec4899; color: #fff; }
        .agent-hale      { background: #14b8a6; color: #1a1a2e; }
        .agent-roman     { background: #f97316; color: #1a1a2e; }
        .agent-sterling  { background: #a855f7; color: #fff; }
        .agent-unknown   { background: #555; color: #ccc; }

        .result-date { color: #00d9ff; font-weight: 600; font-size: 0.95em; }
        .result-role { color: #aaa; font-style: italic; }
        .result-preview { line-height: 1.5; word-break: break-word; color: #ccc; font-size: 0.9em; }
        .deep-link {
            text-decoration: none; opacity: 0.6; margin-left: auto; padding: 2px 6px;
            border-radius: 4px; transition: opacity 0.2s, background 0.2s;
        }
        .deep-link:hover { opacity: 1; background: rgba(0,255,255,0.1); }
        mark { background: #00d9ff; color: #1a1a2e; padding: 1px 4px; border-radius: 3px; font-weight: bold; }

        /* Conversation viewer (inline expand) */
        /* convo-viewer styling is below in the expansion CSS section */
        .convo-msg {
            padding: 10px 12px; margin: 4px 0; border-radius: 6px;
            font-size: 0.88em; line-height: 1.6; word-break: break-word;
        }
        .convo-msg-user { background: #0f2a50; border-left: 3px solid #00d9ff; }
        .convo-msg-assistant { background: #111a30; border-left: 3px solid #8b5cf6; }
        .convo-msg-tool { background: #0d1520; border-left: 3px solid #333; }
        .convo-msg .msg-role {
            font-weight: bold; text-transform: uppercase; font-size: 0.75em;
            letter-spacing: 0.5px; margin-bottom: 4px;
        }
        .convo-msg-user .msg-role { color: #00d9ff; }
        .convo-msg-assistant .msg-role { color: #a78bfa; }
        .convo-msg-tool .msg-role { color: #666; }
        .convo-msg .msg-time { font-size: 0.7em; color: #555; margin-left: 8px; font-weight: normal; }
        .convo-msg .msg-body { color: #ddd; white-space: pre-wrap; }
        .convo-msg .msg-body code {
            background: #0f3460; padding: 1px 5px; border-radius: 3px; font-size: 0.92em; color: #7dd3fc;
        }
        .convo-msg .msg-body pre {
            background: #0a0f1e; padding: 10px 14px; border-radius: 6px; overflow-x: auto;
            margin: 8px 0; font-size: 0.88em; color: #7dd3fc; line-height: 1.4; white-space: pre-wrap;
        }
        .convo-msg .msg-body pre code { background: none; padding: 0; color: inherit; }
        .convo-msg .msg-body ul { margin: 6px 0 6px 20px; color: #ccc; }
        .convo-msg .msg-body ul li { margin: 2px 0; }
        .convo-msg .msg-body strong { color: #fff; }
        .convo-msg .msg-body h1, .convo-msg .msg-body h2, .convo-msg .msg-body h3 {
            color: #fff; margin: 8px 0 4px; font-weight: 600;
        }
        .convo-msg .msg-body h1 { font-size: 15px; }
        .convo-msg .msg-body h2 { font-size: 14px; }
        .convo-msg .msg-body h3 { font-size: 13px; }

        .tool-calls-summary {
            display: inline-flex; align-items: center; gap: 6px;
            padding: 4px 12px; background: #0d1520; border: 1px solid #333;
            border-radius: 6px; color: #888; font-size: 0.82em;
            cursor: pointer; margin: 3px 0; transition: background 0.15s;
        }
        .tool-calls-summary:hover { background: #16213e; color: #aaa; }
        .msg-show-more {
            color: #00d9ff; cursor: pointer; font-size: 0.82em; opacity: 0.8;
            display: inline-block; margin-top: 4px;
        }
        .msg-show-more:hover { opacity: 1; text-decoration: underline; }
        .convo-loading { text-align: center; padding: 20px; color: #888; font-size: 0.9em; }
        .convo-collapse {
            text-align: center; padding: 6px; color: #00d9ff; font-size: 0.82em;
            cursor: pointer; margin-top: 4px;
        }
        .convo-collapse:hover { text-decoration: underline; }
        .activity-status { text-align: center; color: #888; padding: 30px 10px; font-size: 0.9em; }

        /* File results */
        .result-path { font-family: monospace; font-size: 0.9em; color: #aaa; }

        .summary { text-align: center; color: #888; margin-top: 20px; padding: 10px; }
        .loading { text-align: center; padding: 40px; color: #888; }
        .empty { text-align: center; padding: 40px; color: #666; }

        .help-section {
            margin-top: 40px; padding-top: 20px; border-top: 1px solid #333;
        }
        .help-section h2 { text-align: center; color: #00d9ff; margin-bottom: 20px; }
        .help-grid {
            display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
            gap: 16px; margin-bottom: 20px;
        }
        .help-card { background: #16213e; border-radius: 10px; padding: 16px; }
        .help-card h3 { color: #00d9ff; margin-bottom: 8px; font-size: 1em; }
        .help-card p { color: #aaa; font-size: 0.85em; margin-bottom: 12px; }
        .examples { display: flex; flex-direction: column; gap: 6px; }
        .examples code {
            background: #0f3460; padding: 6px 10px; border-radius: 5px;
            font-size: 0.85em; color: #fff; cursor: pointer; transition: background 0.15s;
        }
        .examples code:hover { background: #00d9ff; color: #1a1a2e; }

        .convo-load-btn {
            text-align: center; padding: 8px; color: #00d9ff; font-size: 0.82em;
            cursor: pointer; border: 1px dashed #333; border-radius: 6px; margin: 4px 0;
            transition: background 0.15s, border-color 0.15s;
        }
        .convo-load-btn:hover { background: rgba(0,217,255,0.08); border-color: #00d9ff; }
        .load-arrow { font-size: 0.9em; margin-right: 4px; }

        .show-more-btn {
            text-align: center; padding: 10px; color: #00d9ff; font-size: 0.88em;
            cursor: pointer; border: 1px dashed #333; border-radius: 8px; margin: 8px 0;
            transition: background 0.15s, border-color 0.15s;
        }
        .show-more-btn:hover { background: rgba(0,217,255,0.08); border-color: #00d9ff; }

        .convo-viewer { display: none; margin-top: 8px; border-top: 1px solid #333; }
        .convo-thread { padding: 8px 0; max-height: 500px; overflow-y: auto; }
        .convo-msg { padding: 8px 12px; margin: 4px 0; border-radius: 6px; }
        .convo-user { background: rgba(0,217,255,0.06); border-left: 3px solid #00d9ff; }
        .convo-assistant { background: rgba(100,255,100,0.05); border-left: 3px solid #4a7; }
        .convo-system { background: rgba(255,255,255,0.03); border-left: 3px solid #555; font-size: 0.85em; }
        .convo-highlight { box-shadow: 0 0 0 2px #f90 inset; }
        .convo-role-line { display: flex; gap: 10px; align-items: center; margin-bottom: 4px; }
        .convo-role { font-weight: 700; font-size: 0.82em; text-transform: uppercase; color: #aaa; }
        .convo-time { font-size: 0.78em; color: #666; }
        .convo-content { font-size: 0.9em; line-height: 1.5; white-space: pre-wrap; word-break: break-word; }
        .convo-content code { background: rgba(255,255,255,0.08); padding: 1px 5px; border-radius: 3px; font-size: 0.9em; }
        .convo-content pre { background: rgba(0,0,0,0.3); padding: 8px 12px; border-radius: 6px; overflow-x: auto; margin: 6px 0; }
        .convo-content pre code { background: none; padding: 0; }
        .convo-show-more { color: #00d9ff; font-size: 0.82em; cursor: pointer; padding: 2px 0; }
        .convo-show-more:hover { text-decoration: underline; }
        .convo-tools-summary { color: #777; font-size: 0.82em; padding: 4px 12px; font-style: italic; }
        .load-more-btn { text-align: center; padding: 8px; color: #00d9ff; font-size: 0.85em; cursor: pointer; border: 1px dashed #444; border-radius: 6px; margin: 6px 0; }
        .load-more-btn:hover { background: rgba(0,217,255,0.08); border-color: #00d9ff; }
        .result-card.expanded { border-color: #00d9ff; }
    </style>
</head>
<body>
    <div class="container">
        <h1>Recall</h1>
        <div class="subtitle"><a href="https://devtracker.srv912889.hstgr.cloud/" target="_blank">Dev Tracker ↗</a> <span style="color:#444;margin:0 8px;">|</span> <span style="color:#666;">Search conversation archives across all agents</span></div>

        <div class="search-box">
            <input type="text" id="query" placeholder="Search conversations and files..." autofocus>
            <button class="search-btn" onclick="doSearch()">Search</button>
        </div>

        <div class="agent-bar" id="agentBar">
            <select class="date-range-select" id="dateRange" onchange="onDateRangeChange()">
                <option value="1">Today</option>
                <option value="3">3 days</option>
                <option value="7">7 days</option>
                <option value="14" selected>14 days</option>
                <option value="30">30 days</option>
                <option value="0">All time</option>
            </select>
        </div>

        <div class="options">
            <label><input type="checkbox" id="semantic"> Semantic search</label>
            <label>Agent: <select id="agent">
                <option value="">All agents</option>
                <option value="Kit">Kit</option>
                <option value="CC">CC</option>
                <option value="Claude">Claude</option>
                <option value="cyrus">Cyrus</option>
                <option value="damian">Damian</option>
                <option value="grok">Grok</option>
                <option value="chat">Chat</option>
                <option value="arthur">Arthur</option>
                <option value="hale">Hale</option>
                <option value="roman">Roman</option>
                <option value="sterling">Sterling</option>
            </select></label>
            <label><input type="checkbox" id="filesOnly"> Files only</label>
            <label><input type="checkbox" id="convosOnly"> Conversations only</label>
        </div>

        <div class="search-indicator" id="searchIndicator">
            <div class="spinner"></div><span>Searching...</span>
        </div>
        <div id="semanticHint" class="semantic-hint" style="display:none;">Semantic mode: press Enter or click Search</div>

        <div id="results"></div>

        <div class="help-section" id="helpSection">
            <h2>Search Tips</h2>
            <div class="help-grid">
                <div class="help-card">
                    <h3>Keyword Search (Default)</h3>
                    <p>Finds exact word matches. Best for specific terms.</p>
                    <div class="examples">
                        <code>project update</code>
                        <code>meeting notes</code>
                        <code>API integration</code>
                    </div>
                </div>
                <div class="help-card">
                    <h3>Semantic Search</h3>
                    <p>Understands meaning. Enable the checkbox!</p>
                    <div class="examples">
                        <code>what did we decide about the website</code>
                        <code>how to handle customer requests</code>
                    </div>
                </div>
                <div class="help-card">
                    <h3>Agent Pills</h3>
                    <p>Click an agent pill to browse recent activity without searching.</p>
                    <div class="examples">
                        <code>Click Kit to see Kit's recent work</code>
                        <code>Click All to see everything</code>
                    </div>
                </div>
                <div class="help-card">
                    <h3>Click to Expand</h3>
                    <p>Click any result card to see the full conversation.</p>
                    <div class="examples">
                        <code>Click a card → full conversation</code>
                        <code>Click again → collapse</code>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <script>
        // --- State ---
        let debounceTimer = null;
        let abortController = null;
        let searchId = 0;
        const DEBOUNCE_MS = 400;
        const TRUNCATE_LEN = 300;
        const MSG_TRUNCATE = 800;
        let expandedCard = null;
        let loadedConvos = {};

        // --- Agent maps ---
        const AGENT_CLASS_MAP = {
            'Kit': 'agent-kit', 'kit': 'agent-kit', 'main': 'agent-kit',
            'CC': 'agent-cc', 'cc': 'agent-cc',
            'Claude': 'agent-claude', 'claude': 'agent-claude',
            'cyrus': 'agent-cyrus', 'damian': 'agent-damian',
            'grok': 'agent-grok', 'chat': 'agent-chat',
            'arthur': 'agent-arthur', 'hale': 'agent-hale',
            'roman': 'agent-roman', 'sterling': 'agent-sterling',
        };
        const AGENT_DISPLAY = {
            'main': 'Kit', 'kit': 'Kit', 'Kit': 'Kit',
            'cc': 'CC', 'CC': 'CC',
            'claude': 'Claude', 'Claude': 'Claude',
            'cyrus': 'Cyrus', 'damian': 'Damian', 'grok': 'Grok',
            'chat': 'Chat', 'arthur': 'Arthur', 'hale': 'Hale',
            'roman': 'Roman', 'sterling': 'Sterling',
        };
        const PILL_CLASS_MAP = {
            'Kit': 'pill-kit', 'CC': 'pill-cc', 'Claude': 'pill-claude',
            'cyrus': 'pill-cyrus', 'damian': 'pill-damian', 'grok': 'pill-grok',
            'chat': 'pill-chat', 'arthur': 'pill-arthur', 'hale': 'pill-hale',
            'roman': 'pill-roman', 'sterling': 'pill-sterling',
        };
        const PILL_ORDER = ['Kit', 'CC', 'Claude', 'cyrus', 'damian', 'grok', 'chat', 'arthur', 'hale', 'roman', 'sterling'];

        function agentBadgeClass(a) { return AGENT_CLASS_MAP[a] || AGENT_CLASS_MAP[a?.toLowerCase()] || 'agent-unknown'; }
        function agentDisplayName(a) { return AGENT_DISPLAY[a] || AGENT_DISPLAY[a?.toLowerCase()] || a || 'unknown'; }

        // --- Utilities ---
        function escapeHtml(t) { if (!t) return ''; const d = document.createElement('div'); d.textContent = String(t); return d.innerHTML; }
        function escapeAttr(t) { if (!t) return ''; return String(t).replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }

        function cleanNoise(text) {
            if (!text) return '';
            text = text.replace(/\[Telegram\s+[^\]]*\]\s*/g, '');
            text = text.replace(/\[Discord\s+[^\]]*\]\s*/g, '');
            text = text.replace(/\[message_id:\s*\d+\]\s*/g, '');
            return text.trim();
        }

        function highlightTerms(html, query) {
            if (!query) return html;
            const stopwords = new Set(['the','a','an','is','in','on','at','to','of','and','or','for','it','by','as']);
            const terms = query.toLowerCase().split(/\s+/).filter(t => t.length > 1 && !stopwords.has(t));
            let r = html;
            for (const term of terms) {
                const e = term.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
                try { r = r.replace(new RegExp('(' + e + ')', 'gi'), '<mark>$1</mark>'); } catch(x) {}
            }
            return r;
        }

        function renderMarkdown(text) {
            if (!text) return '';
            let s = escapeHtml(text);
            s = s.replace(/```(\w*)\n?([\s\S]*?)```/g, (_, lang, code) => '<pre><code>' + code.trim() + '</code></pre>');
            s = s.replace(/`([^`\n]+)`/g, '<code>$1</code>');
            s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
            s = s.replace(/^### (.+)$/gm, '<h3>$1</h3>');
            s = s.replace(/^## (.+)$/gm, '<h2>$1</h2>');
            s = s.replace(/^# (.+)$/gm, '<h1>$1</h1>');
            s = s.replace(/^[\-\*] (.+)$/gm, '<li>$1</li>');
            s = s.replace(/((?:<li>.*<\/li>\n?)+)/g, '<ul>$1</ul>');
            return s;
        }

        // --- Event listeners ---
        const queryInput = document.getElementById('query');
        queryInput.addEventListener('input', () => {
            clearTimeout(debounceTimer);
            debounceTimer = setTimeout(() => { if (queryInput.value.trim().length >= 2 && !document.getElementById('semantic').checked) doSearch(); }, DEBOUNCE_MS);
        });
        queryInput.addEventListener('keypress', e => { if (e.key === 'Enter') { clearTimeout(debounceTimer); doSearch(); } });
        document.getElementById('agent').addEventListener('change', () => { if (queryInput.value.trim().length >= 2) doSearch(); });
        ['semantic', 'filesOnly', 'convosOnly'].forEach(id => {
            document.getElementById(id).addEventListener('change', () => { if (queryInput.value.trim().length >= 2) doSearch(); });
        });
        // Show hint when semantic is toggled
        document.getElementById('semantic').addEventListener('change', function() {
            document.getElementById('semanticHint').style.display = this.checked ? 'inline' : 'none';
        });

        // --- Search ---
        async function doSearch() {
            const query = queryInput.value.trim();
            if (!query) { document.getElementById('results').innerHTML = ''; return; }
            if (abortController) abortController.abort();
            abortController = new AbortController();
            const mySearchId = ++searchId;
            document.getElementById('searchIndicator').classList.add('visible');
            document.getElementById('helpSection').style.display = 'none';
            // Deactivate pills during search
            document.querySelectorAll('.agent-pill').forEach(p => p.classList.remove('active'));
            try {
                const isSemantic = document.getElementById('semantic').checked;
                // Strip quotes for semantic search — they don't help embedding models
                const searchQuery = isSemantic ? query.replace(/^["']|["']$/g, '') : query;
                const params = new URLSearchParams({
                    q: searchQuery,
                    semantic: String(isSemantic),
                    days: document.getElementById('dateRange').value,
                    agent: document.getElementById('agent').value || '',
                    files_only: String(document.getElementById('filesOnly').checked),
                    convos_only: String(document.getElementById('convosOnly').checked)
                });
                const resp = await fetch('/search?' + params, { signal: abortController.signal });
                if (!resp.ok) throw new Error('HTTP ' + resp.status);
                const data = await resp.json();
                // Only render if this is still the latest search
                if (mySearchId === searchId) {
                    renderResults(data, query);
                }
            } catch (err) {
                if (err.name === 'AbortError') return;
                if (mySearchId === searchId) {
                    document.getElementById('results').innerHTML = '<div class="empty">Error: ' + escapeHtml(err.message) + '</div>';
                }
            } finally {
                // Only hide spinner if this is still the latest search
                if (mySearchId === searchId) {
                    document.getElementById('searchIndicator').classList.remove('visible');
                }
            }
        }

        const INITIAL_SHOW = 5;
        let lastSearchData = null;
        let lastSearchQuery = '';
        let convoShowCount = 5;
        let fileShowCount = 5;

        function renderResultCard(r, idx, query) {
            if (r.error) return '<div class="result-card">' + escapeHtml(r.error) + '</div>';
            const dateStr = r.timestamp ? r.timestamp.substring(0, 10) : '';
            const timeStr = r.timestamp ? r.timestamp.substring(11, 16) : '';
            const display = agentDisplayName(r.agent);
            const badge = agentBadgeClass(r.agent);
            const content = cleanNoise(r.content || '');
            const preview = content.length > TRUNCATE_LEN ? content.substring(0, TRUNCATE_LEN) + '...' : content;
            const sid = escapeAttr(r.session_id || '');
            const cardId = 'sr-' + idx;

            return '<div class="result-card" id="' + cardId + '" data-session="' + sid + '" data-msg-id="' + (r.message_id || '') + '" onclick="toggleSearchCard(\'' + cardId + '\', \'' + sid + '\')">' +
                '<div class="result-header">' +
                '<span class="agent-badge ' + badge + '">' + escapeHtml(display) + '</span>' +
                '<span class="result-date">' + escapeHtml(dateStr) + '</span>' +
                '<span>' + escapeHtml(timeStr) + '</span>' +
                '<span class="result-role">' + escapeHtml(r.role || '') + '</span>' +
                (r.deepLink ? '<a href="' + r.deepLink + '" target="_blank" class="deep-link" onclick="event.stopPropagation()">&#128279;</a>' : '') +
                '</div>' +
                '<div class="result-preview">' + highlightTerms(escapeHtml(preview), query) + '</div>' +
                '<div class="convo-viewer" id="cv-' + cardId + '"></div>' +
                '</div>';
        }

        function renderFileCard(r, query) {
            if (r.error) return '';
            const display = agentDisplayName(r.agent);
            const badge = agentBadgeClass(r.agent);
            return '<div class="result-card" style="cursor:default"><div class="result-header">' +
                '<span class="agent-badge ' + badge + '">' + escapeHtml(display) + '</span>' +
                '<span class="result-path">' + escapeHtml(r.path || '') + ':' + (r.line_num || '') + '</span>' +
                '</div><div class="result-preview">' + highlightTerms(escapeHtml(r.line || ''), query) + '</div></div>';
        }

        function renderResults(data, query) {
            lastSearchData = data;
            lastSearchQuery = query;
            convoShowCount = INITIAL_SHOW;
            fileShowCount = INITIAL_SHOW;
            expandedCard = null;
            cardState = {};
            doRenderResults();
        }

        function doRenderResults() {
            const data = lastSearchData;
            const query = lastSearchQuery;
            if (!data) return;
            let html = '';

            const convos = data.conversations || [];
            if (convos.length) {
                html += '<div class="section-title">Conversations <span class="result-count">' + convos.length + ' result' + (convos.length !== 1 ? 's' : '') + '</span></div>';
                const showing = Math.min(convoShowCount, convos.length);
                for (let i = 0; i < showing; i++) {
                    html += renderResultCard(convos[i], i, query);
                }
                if (showing < convos.length) {
                    html += '<div class="show-more-btn" onclick="convoShowCount += 5; doRenderResults();">Show more conversations (' + (convos.length - showing) + ' remaining)</div>';
                }
            }

            const files = data.files || [];
            if (files.length) {
                html += '<div class="section-title">Files <span class="result-count">' + files.length + ' result' + (files.length !== 1 ? 's' : '') + '</span></div>';
                const showing = Math.min(fileShowCount, files.length);
                for (let i = 0; i < showing; i++) {
                    html += renderFileCard(files[i], query);
                }
                if (showing < files.length) {
                    html += '<div class="show-more-btn" onclick="fileShowCount += 5; doRenderResults();">Show more files (' + (files.length - showing) + ' remaining)</div>';
                }
            }

            if (!html) html = '<div class="empty">No results found</div>';
            html += '<div class="summary">' + escapeHtml(data.summary || '') + '</div>';
            document.getElementById('results').innerHTML = html;
        }

        // --- Conversation expansion ---
        async function toggleSearchCard(cardId, sessionId) {
            const card = document.getElementById(cardId);
            const viewer = document.getElementById('cv-' + cardId);
            if (!card || !viewer) return;

            if (expandedCard === cardId) {
                viewer.innerHTML = '';
                viewer.style.display = 'none';
                card.classList.remove('expanded');
                expandedCard = null;
                return;
            }

            if (expandedCard) {
                const prev = document.getElementById(expandedCard);
                const prevV = document.getElementById('cv-' + expandedCard);
                if (prev) prev.classList.remove('expanded');
                if (prevV) { prevV.innerHTML = ''; prevV.style.display = 'none'; }
            }

            expandedCard = cardId;
            card.classList.add('expanded');
            viewer.style.display = 'block';

            const msgId = card.dataset.msgId || '';

            viewer.innerHTML = '<div style="text-align:center;padding:12px;color:#888;">Loading conversation...</div>';

            try {
                // Use /context endpoint to load messages centered on the matched message
                let url;
                if (msgId) {
                    url = '/context?session_id=' + encodeURIComponent(sessionId) + '&message_id=' + msgId + '&radius=15';
                } else {
                    url = '/session?session_id=' + encodeURIComponent(sessionId) + '&window=30';
                }
                const resp = await fetch(url);
                if (!resp.ok) { viewer.innerHTML = '<div style="color:#f44;">Failed to load</div>'; return; }
                const data = await resp.json();
                // Store for loadMore
                if (!loadedConvos[sessionId]) loadedConvos[sessionId] = {};
                loadedConvos[sessionId] = data;
                renderConvo(viewer, data, sessionId, msgId, lastSearchQuery);
            } catch (e) {
                viewer.innerHTML = '<div style="color:#f44;">Error: ' + escapeHtml(e.message) + '</div>';
            }
        }

        function renderConvo(container, data, sessionId, highlightMsgId, searchQuery) {
            const msgs = data.messages || [];
            if (!msgs.length) {
                container.innerHTML = '<div style="padding:8px;color:#888;">No messages in this session</div>';
                return;
            }

            const thread = document.createElement('div');
            thread.className = 'convo-thread';

            if (data.has_more_before) {
                const btn = document.createElement('div');
                btn.className = 'load-more-btn';
                btn.textContent = 'Load earlier messages';
                btn.addEventListener('click', function(ev) {
                    ev.stopPropagation();
                    loadMore('earlier', sessionId, msgs[0].message_index, container);
                });
                thread.appendChild(btn);
            }

            let toolCount = 0;
            function flushTools() {
                if (!toolCount) return;
                const summary = document.createElement('div');
                summary.className = 'convo-tools-summary';
                summary.textContent = toolCount + ' tool call' + (toolCount > 1 ? 's' : '');
                thread.appendChild(summary);
                toolCount = 0;
            }

            for (const m of msgs) {
                const role = m.role || '';
                if (role === 'tool_use' || role === 'tool_result' || role === 'tool') {
                    toolCount++;
                    continue;
                }
                flushTools();

                if (role === 'system' && !(m.content || '').trim()) continue;

                const isHighlight = highlightMsgId && (String(m.id) === String(highlightMsgId) || m.is_match === true);
                const roleClass = role === 'user' ? 'convo-user' : role === 'assistant' ? 'convo-assistant' : 'convo-system';

                const msgDiv = document.createElement('div');
                msgDiv.className = 'convo-msg ' + roleClass + (isHighlight ? ' convo-highlight' : '');

                const roleLine = document.createElement('div');
                roleLine.className = 'convo-role-line';
                const roleSpan = document.createElement('span');
                roleSpan.className = 'convo-role';
                roleSpan.textContent = role;
                roleLine.appendChild(roleSpan);
                const time = m.timestamp ? m.timestamp.substring(11, 16) : '';
                if (time) {
                    const timeSpan = document.createElement('span');
                    timeSpan.className = 'convo-time';
                    timeSpan.textContent = time;
                    roleLine.appendChild(timeSpan);
                }
                msgDiv.appendChild(roleLine);

                const content = cleanNoise(m.content || '');
                const isTruncated = content.length > MSG_TRUNCATE;
                const displayContent = isTruncated ? content.substring(0, MSG_TRUNCATE) : content;

                const contentDiv = document.createElement('div');
                contentDiv.className = 'convo-content';
                contentDiv.innerHTML = searchQuery ? highlightTerms(renderMarkdown(escapeHtml(displayContent)), searchQuery) : renderMarkdown(escapeHtml(displayContent));
                msgDiv.appendChild(contentDiv);

                if (isTruncated) {
                    const showMore = document.createElement('div');
                    showMore.className = 'convo-show-more';
                    showMore.textContent = 'Show more';
                    showMore.addEventListener('click', (function(fullContent, cd, sm) {
                        return function(ev) {
                            ev.stopPropagation();
                            cd.innerHTML = renderMarkdown(escapeHtml(fullContent));
                            sm.remove();
                        };
                    })(content, contentDiv, showMore));
                    msgDiv.appendChild(showMore);
                }

                thread.appendChild(msgDiv);
            }
            flushTools();

            if (data.has_more_after) {
                const btn = document.createElement('div');
                btn.className = 'load-more-btn';
                btn.textContent = 'Load later messages';
                btn.addEventListener('click', function(ev) {
                    ev.stopPropagation();
                    loadMore('later', sessionId, msgs[msgs.length - 1].message_index, container);
                });
                thread.appendChild(btn);
            }

            container.innerHTML = '';
            container.appendChild(thread);

            if (highlightMsgId) {
                setTimeout(function() {
                    const hl = container.querySelector('.convo-highlight');
                    if (hl) {
                        var thread = hl.closest('.convo-thread');
                        if (thread) {
                            thread.scrollTop = hl.offsetTop - thread.offsetTop - (thread.clientHeight / 2) + (hl.offsetHeight / 2);
                        }
                    }
                }, 100);
            }
        }

        async function loadMore(direction, sessionId, fromIdx, container) {
            if (!container) return;
            const around = direction === 'earlier' ? fromIdx - 15 : fromIdx + 15;
            try {
                const resp = await fetch('/session?session_id=' + encodeURIComponent(sessionId) + '&around=' + around + '&window=30');
                if (!resp.ok) return;
                const data = await resp.json();
                const existing = loadedConvos[sessionId];
                if (existing) {
                    const existingIdxs = new Set(existing.messages.map(function(m) { return m.message_index; }));
                    for (const m of data.messages) {
                        if (!existingIdxs.has(m.message_index)) {
                            existing.messages.push(m);
                        }
                    }
                    existing.messages.sort(function(a, b) { return a.message_index - b.message_index; });
                    existing.has_more_before = data.has_more_before;
                    existing.has_more_after = data.has_more_after;
                    renderConvo(container, existing, sessionId, '', lastSearchQuery);
                } else {
                    loadedConvos[sessionId] = data;
                    renderConvo(container, data, sessionId, '', lastSearchQuery);
                }
            } catch (e) {
                console.error('loadMore error:', e);
            }
        }

                function onDateRangeChange() {
            (async () => {
                const days = document.getElementById('dateRange').value;
                try {
                    const resp = await fetch('/activity?days=' + days + '&limit=0');
                    if (!resp.ok) return;
                    const data = await resp.json();
                    if (typeof pillState !== 'undefined') {
                        pillState.agentCounts = data.agent_counts || {};
                        loadedConvos = {};
                        if (typeof renderPills === 'function') renderPills();
                    }
                } catch (e) {}
                if (typeof pillState !== 'undefined' && typeof loadActivity === 'function') {
                    if (pillState.activeAgent !== null || pillState.sessions.length > 0) {
                        await loadActivity(pillState.activeAgent);
                    }
                }
            })();
        }

        // Make example searches clickable
        document.querySelectorAll('.examples code').forEach(el => {
            el.addEventListener('click', () => {
                if (el.textContent.startsWith('Click')) return;
                queryInput.value = el.textContent;
                doSearch();
            });
        });
    </script>
</body>
</html>
"""


@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)


@app.route('/search')
def search_endpoint():
    query = request.args.get('q', '')
    semantic = request.args.get('semantic', 'false').lower() == 'true'
    agent = request.args.get('agent', '') or None
    files_only = request.args.get('files_only', 'false').lower() == 'true'
    convos_only = request.args.get('convos_only', 'false').lower() == 'true'

    if not query:
        return jsonify({"error": "No query provided"})

    days = int(request.args.get('days', '0'))  # 0 = all time
    results = unified_search(
        query=query,
        agent=agent,
        semantic=semantic,
        files_only=files_only,
        convos_only=convos_only,
        days=days if days > 0 else None,
        limit=20
    )

    # Post-process conversation results: add deep links, session_id, message_id
    for convo in results.get("conversations", []):
        full_content = convo.pop("fullContent", convo.get("content", ""))
        convo["deepLink"] = generate_deep_link(full_content)

        # Resolve session_id and message_id for context expansion
        # The unified_search doesn't include these, so look them up
        if "session_id" not in convo:
            _enrich_convo_with_session(convo)

    return jsonify(results)


def _enrich_convo_with_session(convo: dict):
    """Look up session_id and message id for a conversation result, for context expansion."""
    try:
        conn = sqlite3.connect(str(DB_PATH))
        content_prefix = (convo.get("content") or "")[:200]
        if not content_prefix:
            return

        cursor = conn.execute("""
            SELECT m.id, m.session_id, m.message_index
            FROM messages m
            JOIN sessions s ON m.session_id = s.id
            WHERE m.content LIKE ? AND m.role = ?
            LIMIT 1
        """, (content_prefix + '%', convo.get("role", "")))

        row = cursor.fetchone()
        if row:
            convo["message_id"] = row[0]
            convo["session_id"] = row[1]
        conn.close()
    except Exception:
        pass


@app.route('/context')
def context_endpoint():
    """Return surrounding messages for a given message in a session."""
    session_id = request.args.get('session_id', '')
    message_id = request.args.get('message_id', '')
    radius = int(request.args.get('radius', '5'))

    if not session_id:
        return jsonify({"error": "session_id required"}), 400

    try:
        conn = sqlite3.connect(str(DB_PATH))

        # Find the message_index of the target message
        if message_id:
            cursor = conn.execute(
                "SELECT message_index FROM messages WHERE id = ? AND session_id = ?",
                (int(message_id), session_id)
            )
        else:
            return jsonify({"error": "message_id required"}), 400

        row = cursor.fetchone()
        if not row:
            conn.close()
            return jsonify({"messages": [], "error": "Message not found"})

        target_index = row[0]

        # Get surrounding messages
        cursor = conn.execute("""
            SELECT id, role, content, message_index, timestamp
            FROM messages
            WHERE session_id = ?
              AND message_index >= ?
              AND message_index <= ?
            ORDER BY message_index ASC
        """, (session_id, (target_index or 0) - radius, (target_index or 0) + radius))

        messages = []
        for r in cursor.fetchall():
            messages.append({
                "id": r[0],
                "role": r[1],
                "content": (r[2] or "")[:1000],
                "message_index": r[3],
                "timestamp": r[4],
                "is_match": r[3] == target_index
            })

        # Check if there are more messages before/after
        min_idx = conn.execute(
            "SELECT MIN(message_index) FROM messages WHERE session_id = ?",
            (session_id,)
        ).fetchone()[0] or 0
        max_idx_all = conn.execute(
            "SELECT MAX(message_index) FROM messages WHERE session_id = ?",
            (session_id,)
        ).fetchone()[0] or 0

        loaded_min = messages[0]["message_index"] if messages else 0
        loaded_max = messages[-1]["message_index"] if messages else 0

        conn.close()
        return jsonify({
            "session_id": session_id,
            "messages": messages,
            "target_index": target_index,
            "has_more_before": loaded_min > min_idx,
            "has_more_after": loaded_max < max_idx_all,
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500




@app.route('/activity')
def activity_endpoint():
    """Browse recent agent conversations — no search query needed."""
    agent = request.args.get('agent', '') or None
    days = int(request.args.get('days', '14'))
    limit = min(int(request.args.get('limit', '30')), 100)

    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row

        # Get recent sessions for the specified agent (or all)
        sql = """
            SELECT s.id, s.agent_id, s.started_at, s.message_count,
                   (SELECT content FROM messages m WHERE m.session_id = s.id AND m.role = 'user' ORDER BY m.message_index ASC LIMIT 1) as first_user_msg,
                   (SELECT content FROM messages m WHERE m.session_id = s.id AND m.role = 'assistant' ORDER BY m.message_index DESC LIMIT 1) as last_assistant_msg
            FROM sessions s
            WHERE s.message_count > 2 AND LENGTH(s.agent_id) < 15 AND s.agent_id NOT LIKE 'agent:%' AND s.agent_id NOT GLOB '[0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F]*'
        """
        params = []
        if agent:
            sql += " AND s.agent_id = ?"
            params.append(agent)
        if days > 0:
            sql += " AND s.started_at >= datetime('now', ?)"
            params.append(f"-{days} days")
        sql += " ORDER BY s.started_at DESC LIMIT ?"
        params.append(limit)

        rows = conn.execute(sql, params).fetchall()

        sessions = []
        for r in rows:
            first_msg = (r['first_user_msg'] or '')[:500]
            last_msg = (r['last_assistant_msg'] or '')[:500]
            sessions.append({
                "session_id": r['id'],
                "agent": r['agent_id'],
                "started_at": r['started_at'],
                "message_count": r['message_count'],
                "first_user_message": first_msg,
                "last_assistant_message": last_msg,
            })

        # Also get agent summary counts
        count_sql = """
            SELECT s.agent_id, COUNT(*) as cnt
            FROM sessions s
            WHERE s.message_count > 2 AND LENGTH(s.agent_id) < 15 AND s.agent_id NOT LIKE 'agent:%' AND s.agent_id NOT GLOB '[0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F]*'
        """
        count_params = []
        if days > 0:
            count_sql += " AND s.started_at >= datetime('now', ?)"
            count_params.append(f"-{days} days")
        count_sql += " GROUP BY s.agent_id ORDER BY cnt DESC"
        agent_counts = {r[0]: r[1] for r in conn.execute(count_sql, count_params).fetchall()}

        conn.close()
        return jsonify({
            "sessions": sessions,
            "agent_counts": agent_counts,
            "total": len(sessions),
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500



@app.route('/session')
def session_endpoint():
    """Return messages for a session, with optional windowed loading.

    Params:
        session_id: required
        around: message_index to center on (optional, loads all if omitted but capped)
        window: number of messages to load (default 30, max 60)
    """
    session_id = request.args.get('session_id', '')
    if not session_id:
        return jsonify({"error": "session_id required"}), 400

    around = request.args.get('around', None)
    window = min(int(request.args.get('window', '30')), 60)

    try:
        conn = sqlite3.connect(str(DB_PATH))
        sess = conn.execute(
            "SELECT id, agent_id, started_at, message_count FROM sessions WHERE id = ?",
            (session_id,)
        ).fetchone()
        if not sess:
            conn.close()
            return jsonify({"error": "Session not found", "messages": []})

        total = sess[3] or 0

        if around is not None:
            # Windowed: load `window` messages centered on `around`
            center = int(around)
            half = window // 2
            low = max(0, center - half)
            high = center + half

            rows = conn.execute("""
                SELECT role, content, message_index, timestamp
                FROM messages
                WHERE session_id = ? AND message_index >= ? AND message_index <= ?
                ORDER BY message_index ASC
            """, (session_id, low, high)).fetchall()

            # Check if there are more before/after
            has_before = low > 0
            min_idx = conn.execute(
                "SELECT MIN(message_index) FROM messages WHERE session_id = ?",
                (session_id,)
            ).fetchone()[0] or 0
            max_idx = conn.execute(
                "SELECT MAX(message_index) FROM messages WHERE session_id = ?",
                (session_id,)
            ).fetchone()[0] or 0
            has_before = low > min_idx
            has_after = high < max_idx
        else:
            # No around param: load first 30 messages (capped)
            rows = conn.execute("""
                SELECT role, content, message_index, timestamp
                FROM messages
                WHERE session_id = ?
                ORDER BY message_index ASC
                LIMIT ?
            """, (session_id, window)).fetchall()

            max_idx = conn.execute(
                "SELECT MAX(message_index) FROM messages WHERE session_id = ?",
                (session_id,)
            ).fetchone()[0] or 0
            has_before = False
            last_loaded = rows[-1][2] if rows else 0
            has_after = last_loaded < max_idx

        messages = []
        for r in rows:
            content_text = r[1] or ""
            if r[0] == 'tool_result' and len(content_text) > 500:
                content_text = content_text[:500] + "..."
            messages.append({
                "role": r[0],
                "content": content_text,
                "message_index": r[2],
                "timestamp": r[3],
            })

        conn.close()
        return jsonify({
            "session_id": sess[0],
            "agent": sess[1],
            "started_at": sess[2],
            "message_count": sess[3],
            "messages": messages,
            "has_more_before": has_before if around is not None else False,
            "has_more_after": has_after,
            "total_messages": total,
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', '-p', type=int, default=8765, help='Port to run on')
    parser.add_argument('--host', default='100.82.195.86', help='Host to bind to (Tailscale IP)')
    args = parser.parse_args()

    print(f"Recall Web Interface running at http://localhost:{args.port}")
    app.run(host=args.host, port=args.port, debug=False)
