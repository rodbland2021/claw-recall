#!/usr/bin/env python3
"""
Recall Web Interface — Search conversations and files via browser.
"""

from flask import Flask, render_template_string, request, jsonify
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from recall import unified_search

app = Flask(__name__)

HTML_TEMPLATE = """
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
            margin-bottom: 30px;
            color: #00d9ff;
            font-size: 2.5em;
        }
        .search-box {
            display: flex;
            gap: 10px;
            margin-bottom: 20px;
        }
        input[type="text"] {
            flex: 1;
            padding: 15px 20px;
            font-size: 18px;
            border: 2px solid #333;
            border-radius: 10px;
            background: #16213e;
            color: #fff;
        }
        input[type="text"]:focus {
            outline: none;
            border-color: #00d9ff;
        }
        button {
            padding: 15px 30px;
            font-size: 18px;
            background: #00d9ff;
            color: #1a1a2e;
            border: none;
            border-radius: 10px;
            cursor: pointer;
            font-weight: bold;
        }
        button:hover { background: #00b8d9; }
        .options {
            display: flex;
            gap: 20px;
            margin-bottom: 20px;
            flex-wrap: wrap;
        }
        .options label {
            display: flex;
            align-items: center;
            gap: 8px;
            cursor: pointer;
        }
        .options input[type="checkbox"] {
            width: 18px;
            height: 18px;
        }
        select {
            padding: 8px 15px;
            font-size: 14px;
            border-radius: 5px;
            background: #16213e;
            color: #fff;
            border: 1px solid #333;
        }
        .results { margin-top: 30px; }
        .section-title {
            font-size: 1.3em;
            color: #00d9ff;
            margin: 20px 0 10px;
            padding-bottom: 5px;
            border-bottom: 1px solid #333;
        }
        .result {
            background: #16213e;
            border-radius: 10px;
            padding: 15px;
            margin-bottom: 10px;
        }
        .result-header {
            display: flex;
            gap: 15px;
            font-size: 0.85em;
            color: #888;
            margin-bottom: 8px;
        }
        .result-header .agent {
            background: #00d9ff;
            color: #1a1a2e;
            padding: 2px 8px;
            border-radius: 4px;
            font-weight: bold;
        }
        .result-content {
            line-height: 1.5;
            word-break: break-word;
        }
        .result-path {
            font-family: monospace;
            font-size: 0.9em;
            color: #aaa;
        }
        .summary {
            text-align: center;
            color: #888;
            margin-top: 20px;
        }
        .loading {
            text-align: center;
            padding: 40px;
            color: #888;
        }
        .empty {
            text-align: center;
            padding: 40px;
            color: #666;
        }
        .help-section {
            margin-top: 50px;
            padding-top: 30px;
            border-top: 1px solid #333;
        }
        .help-section h2 {
            text-align: center;
            color: #00d9ff;
            margin-bottom: 25px;
        }
        .help-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
            gap: 20px;
            margin-bottom: 30px;
        }
        .help-card {
            background: #16213e;
            border-radius: 10px;
            padding: 20px;
        }
        .help-card h3 {
            color: #00d9ff;
            margin-bottom: 10px;
            font-size: 1.1em;
        }
        .help-card p {
            color: #aaa;
            font-size: 0.9em;
            margin-bottom: 15px;
        }
        .examples {
            display: flex;
            flex-direction: column;
            gap: 8px;
        }
        .examples code {
            background: #0f3460;
            padding: 8px 12px;
            border-radius: 5px;
            font-size: 0.85em;
            color: #fff;
            cursor: pointer;
        }
        .examples code:hover {
            background: #00d9ff;
            color: #1a1a2e;
        }
        .how-it-works {
            background: #16213e;
            border-radius: 10px;
            padding: 25px;
        }
        .how-it-works h3 {
            color: #00d9ff;
            margin-bottom: 15px;
        }
        .how-it-works p {
            color: #ccc;
            margin-bottom: 12px;
            line-height: 1.6;
        }
        mark {
            background: #00d9ff;
            color: #1a1a2e;
            padding: 1px 4px;
            border-radius: 3px;
            font-weight: bold;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>🔍 Recall</h1>
        
        <div class="search-box">
            <input type="text" id="query" placeholder="Search conversations and files..." autofocus>
            <button onclick="search()">Search</button>
        </div>
        
        <div class="options">
            <label>
                <input type="checkbox" id="semantic"> Semantic search
            </label>
            <label>
                Agent:
                <select id="agent">
                    <option value="">All agents</option>
                    <option value="main">Kit (main)</option>
                    <option value="cyrus">Cyrus</option>
                    <option value="arthur">Arthur</option>
                    <option value="damian">Damian</option>
                    <option value="hale">Dr. Hale</option>
                    <option value="roman">Roman</option>
                    <option value="conrad">Conrad</option>
                    <option value="elara">Elara</option>
                    <option value="sterling">Sterling</option>
                    <option value="shared">Shared</option>
                </select>
            </label>
            <label>
                <input type="checkbox" id="filesOnly"> Files only
            </label>
            <label>
                <input type="checkbox" id="convosOnly"> Conversations only
            </label>
        </div>
        
        <div id="results"></div>
        
        <div class="help-section">
            <h2>💡 Search Tips</h2>
            
            <div class="help-grid">
                <div class="help-card">
                    <h3>🔤 Keyword Search (Default)</h3>
                    <p>Finds exact word matches. Best for specific terms.</p>
                    <div class="examples">
                        <code>project update</code>
                        <code>meeting notes</code>
                        <code>API integration</code>
                    </div>
                </div>
                
                <div class="help-card">
                    <h3>🧠 Semantic Search</h3>
                    <p>Understands meaning, finds related concepts. Enable the checkbox!</p>
                    <div class="examples">
                        <code>what did we decide about the website</code>
                        <code>how to handle customer requests</code>
                        <code>conversation about project timeline</code>
                    </div>
                </div>
                
                <div class="help-card">
                    <h3>📁 File Search</h3>
                    <p>Searches playbooks, memory files, and docs across all agents.</p>
                    <div class="examples">
                        <code>setup guide</code>
                        <code>deployment workflow</code>
                        <code>configuration docs</code>
                    </div>
                </div>
                
                <div class="help-card">
                    <h3>🎯 Filter by Agent</h3>
                    <p>Narrow results to a specific agent's conversations/files.</p>
                    <div class="examples">
                        <code>main → primary assistant</code>
                        <code>research → research tasks</code>
                        <code>dev → coding projects</code>
                    </div>
                </div>
            </div>
            
            <div class="how-it-works">
                <h3>⚙️ How It Works</h3>
                <p><strong>Keyword search:</strong> Uses SQLite FTS5 full-text search — fast, finds exact matches.</p>
                <p><strong>Semantic search:</strong> Uses OpenAI embeddings to understand meaning. Your query and all messages are converted to vectors (numbers representing meaning), then we find the closest matches. This is why "what did we discuss about ads" finds conversations about "Facebook campaign structure" even without those exact words.</p>
                <p><strong>Database:</strong> 32,078 messages indexed from 116 archived sessions across all agents.</p>
            </div>
        </div>
    </div>
    
    <script>
        document.getElementById('query').addEventListener('keypress', (e) => {
            if (e.key === 'Enter') search();
        });
        
        async function search() {
            const query = document.getElementById('query').value;
            if (!query.trim()) {
                document.getElementById('results').innerHTML = '<div class="empty">Please enter a search query</div>';
                return;
            }
            
            const semantic = document.getElementById('semantic').checked;
            const agent = document.getElementById('agent').value;
            const filesOnly = document.getElementById('filesOnly').checked;
            const convosOnly = document.getElementById('convosOnly').checked;
            
            document.getElementById('results').innerHTML = '<div class="loading">Searching...</div>';
            
            try {
                const params = new URLSearchParams({
                    q: query,
                    semantic: String(semantic),
                    agent: agent || '',
                    files_only: String(filesOnly),
                    convos_only: String(convosOnly)
                });
                
                console.log('Searching:', params.toString());
                
                const response = await fetch('/search?' + params);
                
                if (!response.ok) {
                    throw new Error('Server error: ' + response.status);
                }
                
                const data = await response.json();
                console.log('Results:', data.summary);
                
                renderResults(data, query);
            } catch (err) {
                console.error('Search error:', err);
                document.getElementById('results').innerHTML = '<div class="empty">Error: ' + err.message + '</div>';
            }
        }
        
        function renderResults(data, query) {
            let html = '';
            
            // Conversations
            if (data.conversations && data.conversations.length > 0) {
                html += '<div class="section-title">📝 Conversations</div>';
                for (const r of data.conversations) {
                    if (r.error) {
                        html += '<div class="result"><div class="result-content">Error: ' + r.error + '</div></div>';
                        continue;
                    }
                    const ts = r.timestamp ? r.timestamp.substring(0, 16).replace('T', ' ') : 'unknown';
                    html += `
                        <div class="result">
                            <div class="result-header">
                                <span class="agent">${r.agent}</span>
                                <span>${r.channel}</span>
                                <span>${ts}</span>
                                <span>[${r.role}]</span>
                            </div>
                            <div class="result-content">${highlightTerms(r.content, query)}</div>
                        </div>
                    `;
                }
            }
            
            // Files
            if (data.files && data.files.length > 0) {
                html += '<div class="section-title">📁 Files</div>';
                for (const r of data.files) {
                    if (r.error) {
                        html += '<div class="result"><div class="result-content">Error: ' + r.error + '</div></div>';
                        continue;
                    }
                    html += `
                        <div class="result">
                            <div class="result-header">
                                <span class="agent">${r.agent}</span>
                                <span class="result-path">${r.path}:${r.line_num}</span>
                            </div>
                            <div class="result-content">${highlightTerms(r.line, query)}</div>
                        </div>
                    `;
                }
            }
            
            if (!html) {
                html = '<div class="empty">No results found</div>';
            }
            
            html += '<div class="summary">' + data.summary + '</div>';
            
            document.getElementById('results').innerHTML = html;
        }
        
        function escapeHtml(text) {
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        }
        
        function highlightTerms(text, query) {
            // Escape HTML first
            let escaped = escapeHtml(text);
            
            // Get search terms (split by space, filter short words)
            const terms = query.toLowerCase().split(' ').filter(t => t.length > 2);
            
            // Highlight each term
            for (const term of terms) {
                // Escape special regex characters
                const escapedTerm = term.replace(/[.*+?^${}()|[\\]\\\\]/g, '\\\\$&');
                try {
                    const regex = new RegExp('(' + escapedTerm + ')', 'gi');
                    escaped = escaped.replace(regex, '<mark>$1</mark>');
                } catch (e) {
                    console.error('Regex error for term:', term);
                }
            }
            
            return escaped;
        }
        
        // Make example searches clickable
        document.querySelectorAll('.examples code').forEach(el => {
            el.addEventListener('click', () => {
                document.getElementById('query').value = el.textContent;
                search();
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
def search():
    query = request.args.get('q', '')
    semantic = request.args.get('semantic', 'false').lower() == 'true'
    agent = request.args.get('agent', '') or None
    files_only = request.args.get('files_only', 'false').lower() == 'true'
    convos_only = request.args.get('convos_only', 'false').lower() == 'true'
    
    if not query:
        return jsonify({"error": "No query provided"})
    
    results = unified_search(
        query=query,
        agent=agent,
        semantic=semantic,
        files_only=files_only,
        convos_only=convos_only,
        limit=20
    )
    
    return jsonify(results)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', '-p', type=int, default=8765, help='Port to run on')
    parser.add_argument('--host', default='0.0.0.0', help='Host to bind to')
    args = parser.parse_args()
    
    print(f"🔍 Recall Web Interface running at http://localhost:{args.port}")
    app.run(host=args.host, port=args.port, debug=False)
