"""Backward-compatible wrapper — imports from claw_recall.indexing.indexer."""
from claw_recall.indexing.indexer import *  # noqa: F401,F403
from claw_recall.indexing.indexer import main

if __name__ == "__main__":
    main()
