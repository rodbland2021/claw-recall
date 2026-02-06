# 🔍 Claw Recall

**Searchable conversation memory for OpenClaw agents.**

Ever had your agent forget something important? Context compaction means your agent loses access to older conversations. Claw Recall fixes that — giving your agent the ability to search through ALL your past conversations, not just what's in the current context window.

![Recall Web Interface](docs/screenshot.png)

## Why You Need This

### The Problem: Compaction Erases Memory

OpenClaw agents have a limited context window. When conversations get too long, the system **compacts** them — summarizing older messages to make room for new ones. This means:

- ❌ Specific details from last week's conversation? Gone.
- ❌ That decision you made about project X? Summarized away.
- ❌ The exact steps you worked through together? Lost in compaction.

Your agent literally **cannot remember** what happened before the last compaction. It's not being forgetful — that information simply isn't available to it anymore.

### What OpenClaw Already Has (and What It's Missing)

OpenClaw **does** have a built-in `memory_search` tool that searches:
- ✅ Current session files in `~/.openclaw/agents/*/sessions/`
- ✅ Markdown files in your workspace (MEMORY.md, memory/*.md, etc.)

**But here's the gap:** When sessions are archived to `~/.openclaw/agents-archive/`, the built-in tool **can't search them anymore**. Those archives are just JSON files sitting on disk — valuable conversation history that becomes invisible to your agent.

In a typical setup:
- Current sessions: ~50-100 MB (recent conversations)
- Archived sessions: **500+ MB** (weeks/months of history) ← *unsearchable!*

**Claw Recall bridges this gap.** It indexes your archives into a searchable database, giving your agent access to ALL your conversation history, not just recent sessions.

## What You Get

- 🔤 **Keyword Search** — Fast FTS5 full-text search (~0.5s)
- 🧠 **Semantic Search** — Find by meaning, not just words (~2s)
- 📁 **File Search** — Also searches your markdown files (memory/, docs, etc.)
- 🌐 **Web Interface** — Browse and search visually
- 🔌 **Agent Integration** — Your agent can search programmatically

## Quick Start

### 1. Install

```bash
git clone https://github.com/rodbland2021/claw-recall.git
cd claw-recall
pip install -r requirements.txt
```

### 2. Configure (Optional — for Semantic Search)

```bash
cp .env.example .env
# Edit .env and add your OPENAI_API_KEY
```

Semantic search uses OpenAI embeddings (~$0.02 for 30K messages). Without it, you still get fast keyword search.

### 3. Create Database & Index

```bash
# Create the database
python setup_db.py

# Index your archived conversations
python index.py --source ~/.openclaw/agents-archive/

# Optional: Generate embeddings for semantic search
python index.py --source ~/.openclaw/agents-archive/ --embeddings
```

### 4. Test It

```bash
# CLI search
./recall.py "what did we discuss about X"

# Or start the web interface
python web.py --port 8765
# Open http://localhost:8765
```

## Integrating With Your OpenClaw Agent

### Step 1: Make the Tool Accessible

Put claw-recall somewhere your agent can access it. Common options:

```bash
# Option A: In your agent's workspace
cp -r claw-recall ~/clawd/tools/recall/

# Option B: Shared location (for multi-agent setups)
cp -r claw-recall ~/shared/convo-memory/
ln -s ~/shared/convo-memory ~/clawd/shared/convo-memory
```

### Step 2: Tell Your Agent About It

Add this to your agent's `TOOLS.md` or `AGENTS.md`:

```markdown
## 🦞 Claw Recall — Conversation Memory Search

Search past conversations that are no longer in your context window.

**Location:** `~/tools/recall/` (or wherever you put it)

**Quick search:**
```bash
cd ~/tools/recall
./recall.py "what did we discuss about playbooks"  # Keyword search
./recall.py "how to edit long form videos" --semantic  # Semantic search
```

**When to use:**
- User asks "what did we talk about last week?"
- You need context from a previous conversation
- Looking for a decision or detail that's been compacted away

**Python API:**
```python
from recall import unified_search
results = unified_search("Facebook ads", semantic=True)
```
```

### Step 3: Teach the Agent When to Use It

Add this to your `AGENTS.md` or system prompt:

```markdown
## Memory Recall

Before answering questions about past conversations, decisions, or context that might have been compacted, search your conversation memory:

```bash
cd ~/tools/recall && ./recall.py "relevant search terms"
```

Use `--semantic` when searching for concepts rather than exact words.
```

### Step 4: Keep It Updated

Set up automatic indexing so new conversations are searchable:

```bash
# Add to crontab (crontab -e)
0 * * * * cd ~/tools/recall && python index.py --source ~/.openclaw/agents-archive/
```

Or use OpenClaw's built-in cron:
```
Schedule: Every hour
Command: cd ~/tools/recall && python index.py --source ~/.openclaw/agents-archive/
```

## Example Agent Interaction

Here's what it looks like when you ask your bot about past conversations:

![Telegram Example](docs/telegram-example.png)

**What's happening:**
1. You ask: "What did we discuss about the website redesign last month?"
2. The bot searches archived conversations using Claw Recall
3. It finds relevant results with dates and context
4. It summarizes the findings in a helpful response

**Example prompts that trigger recall:**
- "What did we decide about X last week?"
- "Can you find that conversation where we discussed Y?"
- "What was the budget we agreed on for the project?"
- "Remind me what we talked about with [person/topic]"

## Web Interface

The web interface is great for manual browsing:

```bash
python web.py --port 8765 --host 0.0.0.0
```

Features:
- Search with highlighting
- Toggle semantic search
- Filter by agent
- See conversation context

**Tip:** Bind to your Tailscale IP for secure remote access without exposing to the internet.

## CLI Reference

```bash
# Basic keyword search
./recall.py "LYFER campaign"

# Semantic search (finds related concepts)
./recall.py "how to handle customer complaints" --semantic

# Filter by agent
./recall.py "playbook" --agent cyrus

# Search only files (skip conversations)
./recall.py "RUNBOOK" --files-only

# Search only conversations (skip files)
./recall.py "meeting notes" --convos-only

# Limit results
./recall.py "budget" --limit 5
```

## How It Works

1. **Indexing:** Reads archived JSON sessions and extracts messages
2. **FTS5:** SQLite full-text search for fast keyword matching
3. **Embeddings:** OpenAI converts text to vectors for semantic similarity
4. **File Search:** Scans markdown/text files across workspaces

## Requirements

- Python 3.10+
- SQLite 3.35+ (included with Python)
- OpenAI API key (optional, for semantic search)

## Multi-Agent Setup

If you have multiple agents, point them all to a shared database:

```bash
# Shared location
mkdir -p ~/shared/convo-memory
cp -r claw-recall/* ~/shared/convo-memory/

# Symlink from each agent workspace
ln -s ~/shared/convo-memory ~/clawd/shared/convo-memory
ln -s ~/shared/convo-memory ~/clawd-cyrus/shared/convo-memory
ln -s ~/shared/convo-memory ~/clawd-darius/shared/convo-memory
```

Now all agents search the same database.

## Roadmap / Future Enhancements

- [ ] **Deep linking to original messages** — Click search results to jump back to the original Telegram/Discord message (platform-dependent, WhatsApp/Signal don't support this)
- [ ] **Real-time indexing** — Index conversations as they happen, not just from archives
- [ ] **Embeddings caching** — Skip re-embedding unchanged messages
- [ ] **Multi-user support** — Separate databases per user/workspace
- [ ] **Export/backup** — Export search results to markdown

PRs welcome! 🦞

## License

MIT — Use freely, modify as needed.

## Credits

Built for the [OpenClaw](https://github.com/openclaw/openclaw) community.

---

**Questions?** Open an issue or find us on [Discord](https://discord.com/invite/clawd).
