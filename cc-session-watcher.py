#!/usr/bin/env python3
"""
Claw Recall — WSL Session Watcher

Runs on Rod's WSL desktop. Monitors Claude Code and Claude (OpenClaw)
session files for changes and pushes them to the VPS for indexing via
an SSH-tunneled HTTP endpoint.

Usage:
    python3 cc-session-watcher.py             # Run in foreground
    python3 cc-session-watcher.py --dry-run   # Show what would be pushed
    python3 cc-session-watcher.py --catch-up  # One-time catch-up scan, then exit
"""

import json
import os
import subprocess
import sys
import time
import threading
import logging
import argparse
from pathlib import Path
from datetime import datetime, timezone

try:
    import requests
except ImportError:
    print("ERROR: 'requests' not installed. Run: pip3 install requests")
    sys.exit(1)

try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler
except ImportError:
    print("ERROR: 'watchdog' not installed. Run: pip3 install watchdog")
    sys.exit(1)

# --- Configuration ---

WATCH_DIRS = [
    Path.home() / ".claude" / "projects",                      # Claude Code sessions
    Path.home() / ".openclaw" / "agents" / "main" / "sessions",  # Claude (OpenClaw) active
    Path.home() / ".openclaw" / "agents-archive",              # Claude (OpenClaw) archived
]

VPS_ENDPOINT = "http://localhost:18765/index-session"
VPS_INDEX_LOCAL_ENDPOINT = "http://localhost:18765/index-local"
VPS_REMOTE_STAGING = "/tmp/claw-recall-remote"
SSH_LOCAL_PORT = 18765
SSH_REMOTE_HOST = "172.17.0.1"
SSH_REMOTE_PORT = 8765
SSH_HOST = "vps"

DEBOUNCE_SECONDS = 10
MAX_FILE_SIZE_MB = 200  # HTTP upload limit; larger files use rsync
RSYNC_MAX_FILE_SIZE_MB = 600  # Absolute max for rsync fallback
MAX_RETRIES = 3
RETRY_BASE_DELAY = 10  # seconds, multiplied by attempt number

STATE_FILE = Path.home() / ".claw-recall-watcher.json"
LOG_FILE = Path.home() / "logs" / "cc-session-watcher.log"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [cc-watcher] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(str(LOG_FILE), mode='a') if LOG_FILE.parent.exists() else logging.StreamHandler(),
    ],
)
log = logging.getLogger("cc-watcher")


# --- SSH Tunnel ---

class SSHTunnel:
    """Manages an SSH local port forward to the VPS."""

    def __init__(self):
        self._proc = None

    def _kill_stale_tunnel(self):
        """Kill any stale SSH tunnel on our port from a previous run."""
        try:
            result = subprocess.run(
                ["lsof", "-ti", f":{SSH_LOCAL_PORT}"],
                capture_output=True, text=True, timeout=5,
            )
            if result.stdout.strip():
                for pid in result.stdout.strip().split('\n'):
                    try:
                        os.kill(int(pid), 9)
                        log.info(f"Killed stale tunnel process (PID {pid})")
                    except (ValueError, ProcessLookupError):
                        pass
                time.sleep(1)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

    def ensure_running(self) -> bool:
        if self._proc is not None and self._proc.poll() is None:
            return True
        self._kill_stale_tunnel()
        # Start new tunnel
        cmd = [
            "ssh", "-N",
            "-L", f"{SSH_LOCAL_PORT}:{SSH_REMOTE_HOST}:{SSH_REMOTE_PORT}",
            "-o", "ServerAliveInterval=30",
            "-o", "ServerAliveCountMax=3",
            "-o", "ExitOnForwardFailure=yes",
            "-o", "StrictHostKeyChecking=no",
            SSH_HOST,
        ]
        try:
            self._proc = subprocess.Popen(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            )
            time.sleep(2)  # Give tunnel time to establish
            if self._proc.poll() is not None:
                stderr = self._proc.stderr.read().decode()
                log.error(f"SSH tunnel failed to start: {stderr.strip()}")
                self._proc = None
                return False
            log.info("SSH tunnel established")
            return True
        except Exception as e:
            log.error(f"SSH tunnel error: {e}")
            self._proc = None
            return False

    def stop(self):
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
            log.info("SSH tunnel stopped")


# --- State Management ---

def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception as e:
            log.warning(f"Could not load state file: {e}")
    return {"indexed": {}}


def save_state(state: dict):
    try:
        tmp = STATE_FILE.with_suffix('.tmp')
        tmp.write_text(json.dumps(state, indent=2, default=str))
        tmp.replace(STATE_FILE)  # Atomic rename
    except Exception as e:
        log.warning(f"Could not save state: {e}")


def needs_indexing(filepath: Path, state: dict) -> bool:
    """Check if a file needs to be pushed (new or changed since last push)."""
    path_str = str(filepath)
    try:
        stat = filepath.stat()
    except OSError:
        return False
    entry = state.get("indexed", {}).get(path_str)
    if entry is None:
        return True
    return entry.get("size") != stat.st_size or entry.get("mtime") != stat.st_mtime


def update_state(state: dict, filepath: Path):
    """Mark a file as indexed in state."""
    try:
        stat = filepath.stat()
        state.setdefault("indexed", {})[str(filepath)] = {
            "size": stat.st_size,
            "mtime": stat.st_mtime,
            "indexed_at": datetime.now(timezone.utc).isoformat(),
        }
    except OSError:
        pass


# --- File Push ---

def _rsync_push(filepath: Path, dry_run: bool = False) -> dict:
    """Push an oversized file via rsync (delta transfer) + trigger local indexing."""
    try:
        file_size = filepath.stat().st_size
    except OSError:
        return {"status": "error", "reason": "file_not_found"}

    source_path = str(filepath)
    # Build VPS staging path preserving directory structure for agent detection
    for marker in ['.claude/projects', '.openclaw/agents', '.openclaw/agents-archive']:
        idx = source_path.find(marker)
        if idx >= 0:
            path_suffix = source_path[idx:]
            break
    else:
        path_suffix = filepath.name

    remote_path = f"{VPS_REMOTE_STAGING}/{os.path.dirname(path_suffix)}/"

    if dry_run:
        log.info(f"[DRY RUN] Would rsync: {filepath.name} ({file_size // (1024*1024)}MB) -> {remote_path}")
        return {"status": "dry_run"}

    # Ensure remote directory exists and rsync the file
    try:
        mkdir_result = subprocess.run(
            ["ssh", SSH_HOST, f"mkdir -p {remote_path}"],
            capture_output=True, text=True, timeout=10,
        )
        if mkdir_result.returncode != 0:
            log.error(f"rsync mkdir failed: {mkdir_result.stderr.strip()}")
            return {"status": "error", "reason": "mkdir_failed"}

        rsync_result = subprocess.run(
            ["rsync", "-az", "--inplace", str(filepath), f"{SSH_HOST}:{remote_path}"],
            capture_output=True, text=True, timeout=600,
        )
        if rsync_result.returncode != 0:
            log.error(f"rsync failed: {rsync_result.stderr.strip()}")
            return {"status": "error", "reason": "rsync_failed"}
    except subprocess.TimeoutExpired:
        log.error(f"rsync timed out for {filepath.name}")
        return {"status": "error", "reason": "rsync_timeout"}
    except Exception as e:
        log.error(f"rsync error: {e}")
        return {"status": "error", "reason": str(e)}

    # Trigger local indexing on VPS via the SSH tunnel
    remote_file = f"{VPS_REMOTE_STAGING}/{path_suffix}"
    try:
        response = requests.post(
            VPS_INDEX_LOCAL_ENDPOINT,
            json={"filepath": remote_file, "source_path": source_path},
            timeout=600,
        )
        if response.status_code == 200:
            return response.json()
        else:
            log.warning(f"index-local failed ({response.status_code}): {response.text[:200]}")
            return {"status": "error", "reason": f"index_local_{response.status_code}"}
    except Exception as e:
        log.error(f"index-local request error: {e}")
        return {"status": "error", "reason": str(e)}


def push_file(filepath: Path, dry_run: bool = False) -> dict:
    """Push a session file to the VPS for indexing."""
    try:
        file_size = filepath.stat().st_size
    except OSError:
        return {"status": "error", "reason": "file_not_found"}

    max_http = MAX_FILE_SIZE_MB * 1024 * 1024
    max_rsync = RSYNC_MAX_FILE_SIZE_MB * 1024 * 1024

    if file_size > max_rsync:
        log.warning(f"Skipping {filepath.name} ({file_size // (1024*1024)}MB > {RSYNC_MAX_FILE_SIZE_MB}MB absolute limit)")
        return {"status": "skipped", "reason": "too_large"}

    if file_size > max_http:
        log.info(f"Large file {filepath.name} ({file_size // (1024*1024)}MB) — using rsync")
        return _rsync_push(filepath, dry_run)

    if file_size == 0:
        return {"status": "skipped", "reason": "empty_file"}

    if dry_run:
        log.info(f"[DRY RUN] Would push: {filepath} ({file_size:,} bytes)")
        return {"status": "dry_run"}

    session = requests.Session()
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with open(filepath, 'rb') as f:
                response = session.post(
                    VPS_ENDPOINT,
                    files={'file': (filepath.name, f, 'application/x-jsonlines')},
                    data={'source_path': str(filepath)},
                    timeout=300,
                )

            if response.status_code == 200:
                result = response.json()
                return result
            else:
                log.warning(f"Push failed ({response.status_code}): {response.text[:200]}")

        except requests.ConnectionError:
            log.warning(f"VPS unreachable (attempt {attempt}/{MAX_RETRIES})")
        except requests.Timeout:
            log.warning(f"Push timed out (attempt {attempt}/{MAX_RETRIES})")
        except Exception as e:
            log.error(f"Push error: {e}")

        if attempt < MAX_RETRIES:
            delay = RETRY_BASE_DELAY * attempt
            log.info(f"Retrying in {delay}s...")
            time.sleep(delay)

    return {"status": "error", "reason": "max_retries_exceeded"}


# --- File Watcher ---

def _should_handle(path: str) -> bool:
    return (path.endswith('.jsonl')
            and '/subagents/' not in path
            and '.deleted.' not in path)


class SessionFileHandler(FileSystemEventHandler):
    """Handles .jsonl file changes with debounced pushing."""

    def __init__(self, callback):
        super().__init__()
        self._pending = {}  # path -> timer
        self._lock = threading.Lock()
        self._callback = callback

    def on_created(self, event):
        if not event.is_directory and _should_handle(event.src_path):
            self._schedule(event.src_path)

    def on_modified(self, event):
        if not event.is_directory and _should_handle(event.src_path):
            self._schedule(event.src_path)

    def _schedule(self, path: str):
        with self._lock:
            if path in self._pending:
                self._pending[path].cancel()
            # Longer debounce for large files (rsync is heavier than HTTP upload)
            try:
                size = os.path.getsize(path)
                delay = 120 if size > MAX_FILE_SIZE_MB * 1024 * 1024 else DEBOUNCE_SECONDS
            except OSError:
                delay = DEBOUNCE_SECONDS
            timer = threading.Timer(delay, self._fire, args=[path])
            timer.daemon = True
            timer.start()
            self._pending[path] = timer

    def _fire(self, path: str):
        with self._lock:
            self._pending.pop(path, None)
        self._callback(path)


# --- Main ---

def main():
    parser = argparse.ArgumentParser(description='Claw Recall WSL Session Watcher')
    parser.add_argument('--dry-run', action='store_true', help='Show what would be pushed')
    parser.add_argument('--catch-up', action='store_true', help='One-time catch-up scan, then exit')
    args = parser.parse_args()

    # Ensure log directory exists
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

    log.info("Starting Claw Recall WSL session watcher...")

    # Start SSH tunnel (unless dry-run)
    tunnel = SSHTunnel()
    if not args.dry_run:
        if not tunnel.ensure_running():
            log.error("Could not establish SSH tunnel. Exiting.")
            sys.exit(1)

    # Load state
    state = load_state()
    stats = {"pushed": 0, "skipped": 0, "errors": 0}

    def handle_change(path: str):
        """Called when a session file changes (after debounce)."""
        filepath = Path(path)
        if not filepath.exists():
            return

        if not args.dry_run:
            tunnel.ensure_running()

        result = push_file(filepath, dry_run=args.dry_run)
        status = result.get("status", "error")

        if status == "indexed":
            stats["pushed"] += 1
            update_state(state, filepath)
            save_state(state)
            if result.get('incremental'):
                log.info(f"Indexed: {filepath.name} (+{result.get('messages', 0)} new, "
                         f"total={result.get('total_messages', '?')}, agent={result.get('agent', '?')})")
            else:
                log.info(f"Indexed: {filepath.name} ({result.get('messages', 0)} msgs, "
                         f"agent={result.get('agent', '?')})")
        elif status == "skipped":
            stats["skipped"] += 1
            update_state(state, filepath)  # Mark as seen even if skipped
        elif status == "dry_run":
            stats["pushed"] += 1
        else:
            stats["errors"] += 1
            log.error(f"Failed to push {filepath.name}: {result.get('reason', 'unknown')}")

    # Catch-up scan — recent files first for fastest value
    log.info("Running catch-up scan...")
    catch_up = 0
    pending_files = []
    for watch_dir in WATCH_DIRS:
        if not watch_dir.exists():
            log.warning(f"Directory not found: {watch_dir}")
            continue
        for filepath in watch_dir.rglob("*.jsonl"):
            if not _should_handle(str(filepath)):
                continue
            if needs_indexing(filepath, state):
                try:
                    pending_files.append((filepath.stat().st_mtime, filepath))
                except OSError:
                    pass
    pending_files.sort(reverse=True)  # Most recent first
    log.info(f"Found {len(pending_files)} files to process")
    for _, filepath in pending_files:
        handle_change(str(filepath))
        catch_up += 1

    # Prune state entries for deleted files
    pruned = 0
    for path_str in list(state.get("indexed", {}).keys()):
        if not Path(path_str).exists():
            del state["indexed"][path_str]
            pruned += 1

    log.info(f"Catch-up complete: {catch_up} files processed "
             f"(pushed={stats['pushed']} skipped={stats['skipped']} errors={stats['errors']}"
             f"{f', pruned={pruned} stale entries' if pruned else ''})")
    save_state(state)

    if args.catch_up:
        tunnel.stop()
        log.info("Catch-up mode complete. Exiting.")
        return

    # Start watchdog observer
    handler = SessionFileHandler(callback=handle_change)
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
        tunnel.stop()
        sys.exit(1)

    observer.start()
    log.info(f"Watcher started — monitoring {watched} directories")

    try:
        while True:
            time.sleep(300)
            # Periodic stats + tunnel health check
            tunnel.ensure_running()
            log.info(f"Stats: pushed={stats['pushed']} skipped={stats['skipped']} errors={stats['errors']}")
            save_state(state)
    except KeyboardInterrupt:
        observer.stop()
        tunnel.stop()
        log.info(f"Watcher stopped. Final stats: {stats}")

    observer.join()


if __name__ == "__main__":
    main()
