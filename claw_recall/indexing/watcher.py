#!/usr/bin/env python3
"""
Claw Recall — Real-Time File Watcher

Watches OpenClaw session directories for new/modified .jsonl files
and indexes them automatically using watchdog + inotify.

Indexing is serialized through a single worker thread with a persistent
DB connection and busy timeout, eliminating "database is locked" errors.

Usage:
    python3 -m claw_recall.indexing.watcher           # Run in foreground
    systemctl start claw-recall-watcher               # Run as service
"""

import sys
import time
import sqlite3
import threading
import logging
import queue
from pathlib import Path

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from claw_recall.indexing.indexer import index_session_file, is_excluded
from claw_recall.config import DB_PATH, WATCH_DIRS

DEBOUNCE_SECONDS = 5
EMBEDDING_ON_WATCH = False
MAX_RETRIES = 3
RETRY_DELAY = 10  # seconds between retries
BUSY_TIMEOUT_MS = 30000  # 30s SQLite busy timeout

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [watcher] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
log = logging.getLogger("watcher")


class IndexWorker(threading.Thread):
    """Single worker thread that processes index jobs serially with a persistent DB connection."""

    def __init__(self):
        super().__init__(daemon=True)
        self._queue = queue.Queue()
        self._conn = None
        self.stats = {"indexed": 0, "skipped": 0, "errors": 0, "retries": 0}

    def close(self):
        """Close the persistent DB connection (call from main thread on shutdown)."""
        if self._conn:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

    def _get_conn(self):
        if self._conn is None:
            self._conn = sqlite3.connect(str(DB_PATH), timeout=BUSY_TIMEOUT_MS / 1000)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        return self._conn

    def submit(self, path: str):
        self._queue.put(path)

    def run(self):
        while True:
            path = self._queue.get()
            self._process(path, attempt=1)

    def _process(self, path: str, attempt: int):
        filepath = Path(path)
        if not filepath.exists():
            return

        try:
            conn = self._get_conn()
            result = index_session_file(filepath, conn, generate_embeds=EMBEDDING_ON_WATCH)

            if result['status'] == 'indexed':
                self.stats["indexed"] += 1
                if result.get('incremental'):
                    log.info(f"Indexed: {filepath.name} (+{result.get('messages', 0)} new, "
                             f"total={result.get('total_messages', '?')}, agent={result.get('agent', '?')})")
                else:
                    log.info(f"Indexed: {filepath.name} ({result['messages']} msgs, "
                             f"agent={result.get('agent', '?')})")
            else:
                self.stats["skipped"] += 1
        except sqlite3.OperationalError as e:
            if "locked" in str(e) and attempt < MAX_RETRIES:
                self.stats["retries"] += 1
                log.warning(f"DB locked indexing {filepath.name}, retry {attempt}/{MAX_RETRIES} in {RETRY_DELAY}s")
                self._conn = None  # drop stale connection
                time.sleep(RETRY_DELAY)
                self._process(path, attempt + 1)
            else:
                self.stats["errors"] += 1
                log.error(f"Error indexing {filepath.name} (attempt {attempt}): {e}")
                self._conn = None
        except Exception as e:
            self.stats["errors"] += 1
            log.error(f"Error indexing {filepath.name}: {e}")
            self._conn = None


class SessionFileHandler(FileSystemEventHandler):
    """Handles .jsonl file changes with debounced indexing."""

    def __init__(self, worker: IndexWorker):
        super().__init__()
        self._pending = {}  # path -> timer
        self._lock = threading.Lock()
        self._worker = worker

    def _should_handle(self, path: str) -> bool:
        return (path.endswith('.jsonl')
                and '/subagents/' not in path
                and '.deleted.' not in path
                and not is_excluded(Path(path)))

    def on_created(self, event):
        if not event.is_directory and self._should_handle(event.src_path):
            self._schedule_index(event.src_path)

    def on_modified(self, event):
        if not event.is_directory and self._should_handle(event.src_path):
            self._schedule_index(event.src_path)

    def _schedule_index(self, path: str):
        with self._lock:
            if path in self._pending:
                self._pending[path].cancel()
            timer = threading.Timer(DEBOUNCE_SECONDS, self._submit, args=[path])
            timer.daemon = True
            timer.start()
            self._pending[path] = timer

    def _submit(self, path: str):
        with self._lock:
            self._pending.pop(path, None)
        self._worker.submit(path)


def main():
    worker = IndexWorker()
    worker.start()

    handler = SessionFileHandler(worker)
    observer = Observer()

    watched = 0
    for watch_dir in WATCH_DIRS:
        if watch_dir.exists():
            observer.schedule(handler, str(watch_dir), recursive=True)
            watched += 1
            log.info(f"Watching: {watch_dir}")
        else:
            log.warning(f"Directory not found, skipping: {watch_dir}")

    if watched == 0:
        log.error("No directories to watch!")
        sys.exit(1)

    observer.start()
    log.info(f"Claw Recall watcher started -- monitoring {watched} directories")

    try:
        while True:
            time.sleep(300)
            s = worker.stats
            log.info(f"Stats: indexed={s['indexed']} skipped={s['skipped']} retries={s['retries']} errors={s['errors']}")
    except KeyboardInterrupt:
        observer.stop()
        worker.close()
        log.info(f"Watcher stopped. Stats: {worker.stats}")

    observer.join()


if __name__ == "__main__":
    main()
