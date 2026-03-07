"""Backward-compatible wrapper — imports from claw_recall.capture.sources."""
from claw_recall.capture.sources import *  # noqa: F401,F403

if __name__ == "__main__":
    from claw_recall.capture.sources import main
    main()
