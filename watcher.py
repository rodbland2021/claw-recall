"""Backward-compatible wrapper — imports from claw_recall.indexing.watcher."""
from claw_recall.indexing.watcher import *  # noqa: F401,F403

if __name__ == "__main__":
    from claw_recall.indexing.watcher import main
    main()
