# 🔍 Claw Recall

**Searchable conversation memory for OpenClaw agents.**

Search across all your OpenClaw conversations and markdown files — find what you discussed with any agent, anytime.

![Recall Web Interface](docs/screenshot.png)

## Features

- 🔤 **Keyword Search** — Fast FTS5 full-text search (~0.5s)
- 🧠 **Semantic Search** — Find by meaning using OpenAI embeddings (~2s)
- 📁 **File Search** — Search markdown files across all agent workspaces
- 🌐 **Web Interface** — Beautiful dark-themed UI with result highlighting
- 🔌 **Python API** — Easy integration for agents
- ⚡ **Fast** — Parallel search, file caching, optimized queries

## Quick Start

```bash
# 1. Clone the repo
git clone https://github.com/rodbland2021/claw-recall.git
cd claw-recall

# 2. Install dependencies
pip install -r requirements.txt

# 3. Set up your OpenAI key (optional, for semantic search)
cp .env.example .env
# Edit .env and add your OPENAI_API_KEY

# 4. Create the database
python setup_db.py

# 5. Index your conversations
python index.py --source ~/.openclaw/agents-archive/

# 6. Search!
./recall.py "what did we discuss about X"
```

## Web Interface

```bash
# Start the web server
python web.py --port 8765

# Open http://localhost:8765
```

The web interface provides:
- Search box with Enter to submit
- Semantic search toggle
- Filter by agent
- Result highlighting
- Example searches

## CLI Usage

```bash
# Keyword search
./recall.py "LYFER campaign"

# Semantic search (understands meaning)
./recall.py "what did we decide about Facebook ads" --semantic

# Filter by agent
./recall.py "playbook" --agent cyrus

# Search only files
./recall.py "RUNBOOK" --files-only

# Search only conversations
./recall.py "meeting" --convos-only
```

## Python API

```python
from recall import unified_search

# Search everything
results = unified_search("playbook", semantic=True)
print(results["summary"])

for r in results["conversations"]:
    print(f"{r['agent']}: {r['content'][:100]}")

for r in results["files"]:
    print(f"{r['path']}:{r['line_num']}")
```

## Configuration

Create a `.env` file:

```bash
# Required for semantic search
OPENAI_API_KEY=sk-...

# Optional: Custom paths
OPENCLAW_ARCHIVE=~/.openclaw/agents-archive
AGENT_WORKSPACES=/home/user/clawd,/home/user/clawd-cyrus
```

## How It Works

### Keyword Search
Uses SQLite FTS5 (Full-Text Search) — extremely fast, finds exact word matches.

### Semantic Search
1. Your query is converted to a vector (1,536 numbers) using OpenAI's embedding model
2. All messages have pre-computed embeddings stored in the database
3. We find messages with the closest vectors (cosine similarity)
4. This finds related concepts even without exact word matches

### File Search
Scans markdown/text files across all configured agent workspaces with caching for speed.

## Database Schema

- **sessions** — Conversation session metadata
- **messages** — Individual messages with timestamps
- **messages_fts** — FTS5 virtual table for fast keyword search
- **embeddings** — Vector embeddings for semantic search

## Auto-Indexing

Set up a cron job to keep the database current:

```bash
# Index new sessions every hour
0 * * * * cd /path/to/claw-recall && python index.py --source ~/.openclaw/agents-archive/
```

Or use the provided hook script:
```bash
./hooks/quick-index.sh
```

## Requirements

- Python 3.10+
- SQLite 3.35+ (for FTS5)
- OpenAI API key (optional, for semantic search)

## License

MIT — Use freely, modify as needed. Contributions welcome!

## Credits

Built for the [OpenClaw](https://github.com/openclaw/openclaw) community.
