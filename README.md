# 🔍 Claw Recall

**Searchable conversation memory for OpenClaw agents.**

Ever had your agent forget something important? Context compaction means your agent loses access to older conversations. Claw Recall fixes that — giving your agent the ability to search through ALL your past conversations, not just what's in the current context window.

## 📑 Contents

- [Using It With Your Bot](#using-it-with-your-bot-telegram-discord-etc) — Ask your bot naturally, get answers from past conversations
- [Web Interface](#web-interface) — Visual search with highlighting
- [CLI Usage](#cli-usage) — Command-line for power users
- [Installation](#installation) — Setup in 5 steps
- [Why You Need This](#why-you-need-this) — The compaction problem explained
- [How It Works](#how-it-works) — Technical overview
- [Multi-Agent Setup](#multi-agent-setup) — Shared database for teams
- [Roadmap](#roadmap--future-enhancements) — What's coming next

---

## Using It With Your Bot (Telegram, Discord, etc.)

This is how most people will use Claw Recall — just ask your bot!

![Telegram Example](docs/telegram-example.png)

### Example Prompts

Just talk to your bot naturally:

- "What did we discuss about the website redesign last month?"
- "Can you find that conversation where we decided on the budget?"
- "Remind me what we talked about with the API integration"
- "Search our history for anything about project X"

Your bot will search through ALL your archived conversations and summarize what it finds.

### Setting Up Your Bot to Use Recall

Add this to your agent's `TOOLS.md` or `AGENTS.md`:

```markdown
## 🦞 Claw Recall — Conversation Memory Search

Search past conversations that are no longer in your context window.

**Location:** `~/tools/recall/` (or wherever you installed it)

**When to use:** When the user asks about past conversations, decisions, or context that might have been compacted away.

**How to search:**
\`\`\`bash
cd ~/tools/recall && ./recall.py "search terms"
cd ~/tools/recall && ./recall.py "conceptual question" --semantic
\`\`\`
```

That's it! Your bot will now use Claw Recall when you ask about past conversations.

---

## Web Interface

For browsing and exploring your conversation history visually:

![Recall Web Interface](docs/screenshot.png)

```bash
python web.py --port 8765
# Open http://localhost:8765
```

Features:
- Search with result highlighting
- Toggle semantic search
- Filter by agent
- Click 🔗 to jump to original Discord messages

---

## CLI Usage

For power users and scripting:

```bash
# Basic keyword search
./recall.py "project budget"

# Semantic search (finds related concepts)
./recall.py "how did we handle that customer issue" --semantic

# Filter by agent
./recall.py "playbook" --agent cyrus

# Search only files (skip conversations)
./recall.py "RUNBOOK" --files-only

# Search only conversations (skip files)
./recall.py "meeting notes" --convos-only
```

---

## Installation

### 1. Clone & Install

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

### 4. Make It Accessible to Your Agent

```bash
# Option A: In your agent's workspace
cp -r claw-recall ~/clawd/tools/recall/

# Option B: Shared location (for multi-agent setups)
mkdir -p ~/shared
cp -r claw-recall ~/shared/convo-memory/
ln -s ~/shared/convo-memory ~/clawd/shared/convo-memory
```

### 5. Keep It Updated

Set up automatic indexing so new conversations are searchable:

```bash
# Add to crontab (crontab -e)
0 * * * * cd ~/tools/recall && python index.py --source ~/.openclaw/agents-archive/
```

---

## Why You Need This

### The Problem: Compaction Erases Memory

OpenClaw agents have a limited context window. When conversations get too long, the system **compacts** them — summarizing older messages to make room for new ones. This means:

- ❌ Specific details from last week's conversation? Gone.
- ❌ That decision you made about project X? Summarized away.
- ❌ The exact steps you worked through together? Lost in compaction.

### What OpenClaw Already Has (and What's Missing)

OpenClaw **does** have a built-in `memory_search` tool that searches:
- ✅ Current session files in `~/.openclaw/agents/*/sessions/`
- ✅ Markdown files in your workspace (MEMORY.md, memory/*.md, etc.)

**But here's the gap:** When sessions are archived to `~/.openclaw/agents-archive/`, the built-in tool **can't search them anymore**. Those archives become invisible to your agent.

In a typical setup:
- Current sessions: ~50-100 MB (recent conversations)
- Archived sessions: **500+ MB** (weeks/months of history) ← *unsearchable without Claw Recall!*

---

## How It Works

1. **Indexing:** Reads archived JSON sessions and extracts messages into SQLite
2. **Keyword Search:** Uses FTS5 full-text search for fast exact matching (~0.5s)
3. **Semantic Search:** OpenAI embeddings find conceptually related content (~2s)
4. **File Search:** Also scans markdown/text files across your workspaces

---

## Multi-Agent Setup

If you have multiple agents, point them all to a shared database:

```bash
# Shared location
mkdir -p ~/shared/convo-memory
cp -r claw-recall/* ~/shared/convo-memory/

# Symlink from each agent workspace
ln -s ~/shared/convo-memory ~/clawd/shared/convo-memory
ln -s ~/shared/convo-memory ~/clawd-cyrus/shared/convo-memory
```

Now all agents search the same database.

---

## Roadmap / Future Enhancements

- [ ] **Deep linking to original messages** — Click search results to jump back to the original Telegram/Discord message (platform-dependent, WhatsApp/Signal don't support this)
- [ ] **Real-time indexing** — Index conversations as they happen, not just from archives
- [ ] **Embeddings caching** — Skip re-embedding unchanged messages
- [ ] **Multi-user support** — Separate databases per user/workspace

PRs welcome! 🦞

---

## Requirements

- Python 3.10+
- SQLite 3.35+ (included with Python)
- OpenAI API key (optional, for semantic search)

## License

MIT — Use freely, modify as needed.

## Credits

Built for the [OpenClaw](https://github.com/openclaw/openclaw) community.

---

**Questions?** Open an issue or find us on [Discord](https://discord.com/invite/clawd).
