#!/bin/bash
# doc-audit.sh — Check documentation for references to changed/deleted files
#
# Usage:
#   bash scripts/doc-audit.sh                    # Compare HEAD vs HEAD~1
#   bash scripts/doc-audit.sh HEAD~5             # Compare HEAD vs 5 commits ago
#   bash scripts/doc-audit.sh abc1234 def5678    # Compare two specific commits
#
# Scans README.md, docs/, CONTRIBUTING.md for references to deleted/renamed
# source files (*.py, *.sh). Matches code-like references (backtick-wrapped
# or used as command arguments), not project names or URLs.
#
# Exit code 0 = clean, 1 = stale references found.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$SCRIPT_DIR"

# Determine comparison range
if [ $# -eq 0 ]; then
    BASE="HEAD~1"
    HEAD="HEAD"
elif [ $# -eq 1 ]; then
    BASE="$1"
    HEAD="HEAD"
else
    BASE="$1"
    HEAD="$2"
fi

# Get deleted and renamed files — only source files (.py, .sh)
TARGETS=""
while IFS=$'\t' read -r status filepath; do
    filename=$(basename "$filepath")
    case "$filename" in
        *.py|*.sh) TARGETS="$TARGETS $filename" ;;
    esac
done < <(git diff --name-status "$BASE" "$HEAD" 2>/dev/null | grep -E '^[DR]' | awk -F'\t' '{print $1"\t"$2}' || true)

TARGETS=$(echo "$TARGETS" | xargs)

if [ -z "$TARGETS" ]; then
    echo "No deleted/renamed source files (.py/.sh) between $BASE and $HEAD"
    exit 0
fi

# Doc files to scan (skip CHANGELOG — historical references are fine)
DOC_FILES="README.md CONTRIBUTING.md"
DOC_DIRS="docs/"

FOUND=0

echo "Checking docs for references to deleted/renamed source files..."
echo "Range: $BASE..$HEAD"
echo "Files: $TARGETS"
echo ""

for filename in $TARGETS; do
    # Build a pattern that matches code-like usage of the exact filename:
    # - python3 filename.py (not test_filename.py)
    # - `filename.py` (exact, not as substring)
    # - path/filename.py (not scripts/filename.py if it still exists)
    # Exclude matches where the file exists at the referenced path
    escaped=$(echo "$filename" | sed 's/\./\\./g')
    pattern="(python3?\s+${escaped}(\s|$)|\`${escaped}\`|[^a-zA-Z_]${escaped}[^a-zA-Z_])"

    scan_doc() {
        local doc="$1"
        local raw_matches
        raw_matches=$(grep -nE "$pattern" "$doc" 2>/dev/null || true)
        [ -z "$raw_matches" ] && return

        # Filter out lines where filename appears as part of a path that still exists
        # e.g., "claw_recall/api/web.py" or "scripts/cleanup_excluded.py"
        local filtered=""
        while IFS= read -r line; do
            local content
            content=$(echo "$line" | cut -d: -f2-)
            # Skip lines where filename appears as part of a longer path (new location)
            local path_ref
            path_ref=$(echo "$content" | grep -oP '\S*'"$filename" | head -1)
            if [ -n "$path_ref" ] && [ "$path_ref" != "$filename" ]; then
                if [ -e "$path_ref" ] || echo "$path_ref" | grep -qE 'claw_recall/|scripts/|hooks/|tests/'; then
                    continue
                fi
            fi
            # Skip indented tree entries (project structure diagrams)
            if echo "$content" | grep -qE '^\s{2,}'"$filename"'\s'; then
                continue
            fi
            filtered="$filtered$line"$'\n'
        done <<< "$raw_matches"

        filtered=$(echo "$filtered" | sed '/^$/d')
        if [ -n "$filtered" ]; then
            echo "STALE: $doc references deleted '$filename':"
            echo "$filtered" | sed 's/^/  /'
            echo ""
            FOUND=1
        fi
    }

    for doc in $DOC_FILES; do
        [ -f "$doc" ] && scan_doc "$doc"
    done

    if [ -d "$DOC_DIRS" ]; then
        while IFS= read -r doc; do
            scan_doc "$doc"
        done < <(find "$DOC_DIRS" -name '*.md' -type f 2>/dev/null)
    fi
done

if [ "$FOUND" -eq 0 ]; then
    echo "Clean — no stale doc references found."
    exit 0
else
    echo "---"
    echo "Found stale references. Update the docs above to fix."
    exit 1
fi
