"""Backward-compatible wrapper — imports from claw_recall.database."""
from claw_recall.database import SCHEMA, setup_database, get_db_stats
from claw_recall.config import DB_PATH

if __name__ == "__main__":
    conn = setup_database()
    conn.close()
    stats = get_db_stats()
    print(f"\nDatabase stats:")
    print(f"   Sessions: {stats.get('sessions', 0)}")
    print(f"   Messages: {stats.get('messages', 0)}")
    print(f"   Size: {stats.get('file_size_mb', 0)} MB")
