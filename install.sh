#!/bin/bash
# Claw Recall - Installation Script
# Adds the tool documentation to your agent's TOOLS.md

set -e

TOOLS_FILE="${1:-$HOME/clawd/TOOLS.md}"
RECALL_PATH="$(cd "$(dirname "$0")" && pwd)"

# Check if TOOLS.md exists
if [ ! -f "$TOOLS_FILE" ]; then
    echo "Creating $TOOLS_FILE..."
    echo "# TOOLS.md - Local Notes" > "$TOOLS_FILE"
    echo "" >> "$TOOLS_FILE"
fi

# Check if already installed
if grep -q "Claw Recall" "$TOOLS_FILE" 2>/dev/null; then
    echo "✅ Claw Recall already configured in $TOOLS_FILE"
    exit 0
fi

# Add Claw Recall documentation
cat >> "$TOOLS_FILE" << EOF

## 🦞 Claw Recall — Conversation Memory Search

Search past conversations that have been compacted or archived.
Your context window only holds recent messages — this tool searches ALL your history.

**Location:** \`$RECALL_PATH\`

**When to use:**
- User asks about past conversations ("what did we discuss about X?")
- User asks about decisions or context that might be compacted
- User wants to find a specific conversation from days/weeks ago

**How to search:**
\`\`\`bash
cd $RECALL_PATH
./recall.py "search terms"                    # Keyword search (fast)
./recall.py "what did we decide about X" --semantic  # Meaning-based search
\`\`\`

**Always include dates** in your response so the user knows when the conversation happened.

Example response format:
> I searched our conversation history. On **February 2nd**, we discussed [topic]...
EOF

echo "✅ Claw Recall added to $TOOLS_FILE"
echo ""
echo "Your bot will now know how to use Claw Recall!"
echo "Restart your gateway for changes to take effect."
