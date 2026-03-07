"""Backward-compatible wrapper — imports from claw_recall.database and claw_recall.config."""
from claw_recall.config import DB_PATH, EMBEDDING_MODEL, EMBEDDING_DIM, MIN_CONTENT_LENGTH  # noqa: F401
from claw_recall.database import get_db  # noqa: F401
