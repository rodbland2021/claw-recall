"""Backward-compatible wrapper — imports from claw_recall.cli."""
from claw_recall.cli import *  # noqa: F401,F403

if __name__ == "__main__":
    from claw_recall.cli import main
    main()
