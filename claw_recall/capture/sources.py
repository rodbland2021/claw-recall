#!/usr/bin/env python3
"""
Claw Recall — External Source Capture

Polls Gmail, Google Drive, and Slack for new content and captures it as thoughts.
Tracks captured items in capture_log to avoid re-processing.

Usage:
    python3 capture_sources.py gmail                    # Poll both accounts
    python3 capture_sources.py gmail --account personal # Poll one account
    python3 capture_sources.py drive                    # Poll Drive
    python3 capture_sources.py drive --account rbs      # Poll one account
    python3 capture_sources.py slack                    # Poll Slack channels
    python3 capture_sources.py all                      # Poll everything
    python3 capture_sources.py status                   # Show capture stats
"""

import sys
import json
import sqlite3
import argparse
import logging
import re
from pathlib import Path
from datetime import datetime
from html import unescape

SCRIPTS_DIR = Path.home() / "clawd" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from claw_recall.capture.thoughts import capture_thought, batch_embed_thoughts
from claw_recall.database import get_db
from claw_recall.config import DB_PATH

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [capture] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
log = logging.getLogger("capture_sources")

# Max items per poll cycle
GMAIL_POLL_LIMIT = 50
DRIVE_POLL_LIMIT = 30
# Max body length to capture (avoid bloating DB)
# 8000 chars ≈ 2000 tokens — enough for meeting transcriptions and strategic docs
MAX_BODY_LENGTH = 8000

# ─── Gmail Noise Filter ─────────────────────────────────────────────────────
# Sender patterns that indicate automated/marketing/noise emails.
# Matched against the From header (case-insensitive substring match).
GMAIL_SENDER_BLOCKLIST = [
    'noreply@', 'no-reply@', 'do-not-reply@', 'donotreply@',
    'notifications@', 'notification@', 'notify@', 'alerts@', 'alert@',
    'marketing@', 'newsletter@', 'news@', 'digest@', 'updates@',
    'mailer-daemon@', 'postmaster@',
    # Social media / platforms
    'facebookmail.com', 'linkedin.com', 'twitter.com', 'x.com',
    'pinterest.com', 'instagram.com', 'tiktok.com', 'reddit.com',
    'quora.com', 'medium.com', 'substack.com',
    # Common marketing platforms
    'mailchimp.com', 'sendgrid.net', 'constantcontact.com',
    'hubspot.com', 'klaviyo.com', 'mailgun.org', 'amazonses.com',
    # Shopping / receipts
    'shipment-tracking@', 'order-update@', 'orders@',
    'receipts@', 'billing@',
    # Services
    'googleplay.com', 'apple.com/itunes', 'noreply@github.com',
    'noreply@google.com', 'calendar-notification@google.com',
    'forwarding-noreply@google.com',
]

# Subject patterns that indicate noise (case-insensitive regex).
GMAIL_SUBJECT_NOISE_PATTERNS = [
    r'^your\s+(order|shipment|receipt|invoice|statement|subscription)',
    r'^(shipping|delivery)\s+(confirmation|update|notification)',
    r'^(weekly|daily|monthly)\s+(digest|summary|report|newsletter|update)',
    r'^(unsubscribe|you\'?re?\s+subscribed)',
    r'password\s+reset',
    r'verify\s+your\s+(email|account)',
    r'(sign|log)\s*-?\s*in\s+(alert|attempt|notification)',
    r'^(welcome\s+to|thanks?\s+for\s+(signing|subscribing|joining|registering))',
    r'(promotional|sale|discount|off\s+your\s+order|limited\s+time|flash\s+sale)',
    r'^(security\s+alert|new\s+sign-?in)',
    r'(has\s+been\s+shipped|out\s+for\s+delivery|package\s+delivered)',
]

import re as _re
_COMPILED_SUBJECT_PATTERNS = [_re.compile(p, _re.IGNORECASE) for p in GMAIL_SUBJECT_NOISE_PATTERNS]


# ─── Drive Noise Filter ──────────────────────────────────────────────────────
# MIME types that are code, binary, or media — never useful for memory recall.
# Uses a blocklist approach: anything NOT in this list gets captured.
DRIVE_MIME_BLOCKLIST = {
    # Code / source files
    'text/javascript', 'application/javascript', 'application/x-javascript',
    'text/x-python', 'application/x-python-code',
    'text/css', 'text/x-scss', 'text/x-less',
    'text/x-java-source', 'text/x-c', 'text/x-c++src', 'text/x-csrc',
    'text/x-go', 'text/x-rust', 'text/x-ruby', 'text/x-perl',
    'text/x-shellscript', 'application/x-shellscript',
    'text/x-typescript', 'text/texmacs',  # texmacs = TypeScript misclassified by Drive
    'application/x-httpd-php',
    # Build artifacts / binaries
    'application/octet-stream', 'application/wasm',
    'application/x-dosexec', 'application/x-executable',
    'application/x-mach-binary', 'application/x-sharedlib',
    # Archives / packages
    'application/zip', 'application/gzip', 'application/x-tar',
    'application/x-7z-compressed', 'application/x-rar-compressed',
    'application/java-archive',
    # Config / data formats (code-adjacent)
    'application/json', 'application/xml', 'text/xml',
    'application/x-yaml', 'text/yaml',
    # Fonts
    'application/x-font-otf', 'application/x-font-ttf',
    'font/otf', 'font/ttf', 'font/woff', 'font/woff2',
    'application/vnd.ms-fontobject',
    # Images (metadata stubs only — no searchable content)
    'image/jpeg', 'image/png', 'image/gif', 'image/webp',
    'image/svg+xml', 'image/bmp', 'image/tiff',
    'image/heif', 'image/heic', 'image/avif',
    # Video (metadata stubs only)
    'video/mp4', 'video/webm', 'video/quicktime', 'video/x-msvideo',
    'video/mp2t', 'video/x-matroska', 'video/mpeg',
    # Audio (metadata stubs only)
    'audio/mpeg', 'audio/wav', 'audio/ogg', 'audio/webm',
    'audio/aac', 'audio/flac', 'audio/x-m4a',
    # Crypto / signatures
    'application/gpg-signature', 'application/pgp-signature',
    # Subtitle files (code-adjacent)
    'application/x-subrip', 'text/x-ssa',
    # Map files (source maps)
    'application/x-sourcemap',
    # Design files (metadata stubs only — no searchable content)
    'application/postscript', 'application/illustrator',  # Adobe Illustrator .ai
    'image/vnd.adobe.photoshop', 'application/x-photoshop', 'image/x-photoshop',  # .psd
    'application/vnd.adobe.illustrator',  # .ai alternate
    'application/x-indesign',  # .indd/.idml
}

# File extensions for design assets (that may have ambiguous MIME types)
DRIVE_DESIGN_EXTENSIONS = {
    '.psd', '.ai', '.indd', '.idml', '.sketch', '.fig', '.xd',
}

# File extensions that indicate code/build artifacts (for text/plain files
# whose MIME type doesn't reveal their nature).
DRIVE_CODE_EXTENSIONS = {
    '.js', '.mjs', '.cjs', '.jsx', '.ts', '.tsx', '.mts', '.cts',
    '.py', '.pyc', '.pyo', '.pyw',
    '.css', '.scss', '.less', '.sass',
    '.java', '.class', '.jar',
    '.c', '.h', '.cpp', '.hpp', '.cc',
    '.go', '.rs', '.rb', '.pl', '.pm',
    '.sh', '.bash', '.zsh', '.fish',
    '.php', '.lua', '.swift', '.kt',
    '.map', '.min.js', '.min.css',
    '.lock', '.sum',  # lockfiles
    '.wasm', '.dll', '.so', '.dylib', '.exe',
    '.svg',  # usually code-generated
    '.cmd', '.bat', '.ps1',  # Windows scripts
}


def _is_drive_noise(mime: str, filename: str) -> bool:
    """
    Check if a Drive file is noise (code, binary, media, design metadata stub).

    Returns True if the file should be filtered out.
    Conservative — unknown MIME types are captured.
    """
    if mime in DRIVE_MIME_BLOCKLIST:
        return True

    # Check extension for text/plain or unknown MIME types
    name_lower = filename.lower()
    for ext in DRIVE_CODE_EXTENSIONS:
        if name_lower.endswith(ext):
            return True
    for ext in DRIVE_DESIGN_EXTENSIONS:
        if name_lower.endswith(ext):
            return True

    return False


def _is_gmail_noise(sender: str, subject: str) -> bool:
    """
    Check if an email is likely noise (newsletter, marketing, automated).

    Returns True if the email should be filtered out.
    Conservative — when in doubt, returns False (capture it).
    """
    sender_lower = sender.lower()
    subject_lower = subject.lower()

    # Check sender blocklist
    for pattern in GMAIL_SENDER_BLOCKLIST:
        if pattern in sender_lower:
            return True

    # Check subject patterns
    for compiled in _COMPILED_SUBJECT_PATTERNS:
        if compiled.search(subject):
            return True

    return False


def _strip_html(html: str) -> str:
    """Strip HTML tags and decode entities to plain text."""
    # Remove style/script blocks entirely
    text = re.sub(r'<(style|script)[^>]*>.*?</\1>', '', html, flags=re.IGNORECASE | re.DOTALL)
    # Convert block elements to newlines
    text = re.sub(r'<(br|p|div|tr|li|h[1-6])[^>]*/?>', '\n', text, flags=re.IGNORECASE)
    # Remove all remaining tags
    text = re.sub(r'<[^>]+>', '', text)
    text = unescape(text)
    # Collapse whitespace
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _is_captured(conn: sqlite3.Connection, source_type: str, source_id: str, account: str) -> bool:
    """Check if an item has already been captured."""
    row = conn.execute(
        "SELECT 1 FROM capture_log WHERE source_type = ? AND source_id = ? AND account = ?",
        (source_type, source_id, account)
    ).fetchone()
    return row is not None


def _log_capture(conn: sqlite3.Connection, source_type: str, source_id: str,
                 account: str, thought_id: int, source_modified: str = None):
    """Record a captured item in the log."""
    conn.execute(
        """INSERT OR REPLACE INTO capture_log
           (source_type, source_id, account, thought_id, source_modified)
           VALUES (?, ?, ?, ?, ?)""",
        (source_type, source_id, account, thought_id, source_modified)
    )


# ─── Gmail Capture ────────────────────────────────────────────────────────────

def poll_gmail(account: str = None, limit: int = GMAIL_POLL_LIMIT,
               full_body: bool = True, filter_noise: bool = True) -> dict:
    """
    Poll Gmail for new emails and capture them as thoughts.

    Args:
        account: 'personal', 'rbs', or None for both
        limit: Max emails to check per account
        full_body: If True, fetch full email body (slower, more API calls)
        filter_noise: If True, skip newsletters/marketing/automated emails

    Returns:
        {captured: int, skipped: int, filtered: int, errors: int, accounts: [...]}
    """
    from email_helper import list_inbox, get_email

    accounts = [account] if account else ['personal', 'rbs']
    stats = {"captured": 0, "skipped": 0, "filtered": 0, "errors": 0, "accounts": accounts}
    new_thought_ids = []

    with get_db() as conn:
        for acct in accounts:
            try:
                # Scan both inbox and sent mail
                inbox_emails = list_inbox(acct, limit=limit, query='in:inbox')
                sent_emails = list_inbox(acct, limit=limit // 2, query='in:sent')
                # Merge, dedup by msg_id (sent replies also appear in inbox)
                seen_ids = {e['id'] for e in inbox_emails}
                emails = list(inbox_emails)
                for e in sent_emails:
                    if e['id'] not in seen_ids:
                        emails.append(e)
                        seen_ids.add(e['id'])
                log.info(f"Gmail [{acct}]: {len(inbox_emails)} inbox + {len(sent_emails)} sent = {len(emails)} unique")

                for email_meta in emails:
                    msg_id = email_meta['id']

                    if _is_captured(conn, 'gmail', msg_id, acct):
                        stats["skipped"] += 1
                        continue

                    # Build content from metadata or full body
                    sender = email_meta.get('from', 'Unknown')
                    subject = email_meta.get('subject', 'No subject')
                    date = email_meta.get('date', '')
                    snippet = email_meta.get('snippet', '')

                    # If full_body, fetch real metadata before filtering
                    # (list_inbox returns from='Unknown'/subject='No subject'
                    # for sent mail — get_email returns correct values)
                    if full_body:
                        try:
                            full = get_email(acct, msg_id)
                            sender = full.get('from', sender)
                            subject = full.get('subject', subject)
                            body = full.get('body', '')
                            if body:
                                body = _strip_html(body)[:MAX_BODY_LENGTH]
                            else:
                                body = snippet
                        except Exception as e:
                            log.warning(f"Failed to get full body for {msg_id}: {e}")
                            body = snippet
                    else:
                        body = snippet

                    # Filter noise (now with correct metadata if full_body was used)
                    if filter_noise and _is_gmail_noise(sender, subject):
                        stats["filtered"] += 1
                        log.debug(f"  Filtered: {sender[:40]} — {subject[:50]}")
                        continue

                    content = f"Email from {sender}: {subject}\n{body}"

                    metadata = {
                        'from': sender,
                        'subject': subject,
                        'date': date,
                        'message_id': msg_id,
                        'thread_id': email_meta.get('threadId'),
                        'account': acct,
                    }

                    result = capture_thought(
                        content=content,
                        source='gmail',
                        agent=None,
                        metadata=metadata,
                        generate_embedding=False,  # Deferred — batch embed below
                        conn=conn,
                    )

                    if 'error' in result:
                        log.error(f"Capture error for {msg_id}: {result['error']}")
                        stats["errors"] += 1
                    elif result.get('duplicate'):
                        stats["skipped"] += 1
                    else:
                        _log_capture(conn, 'gmail', msg_id, acct, result['id'])
                        new_thought_ids.append(result['id'])
                        stats["captured"] += 1
                        log.info(f"  Captured: {subject[:60]}")

                conn.commit()
            except Exception as e:
                log.error(f"Gmail [{acct}] error: {e}")
                stats["errors"] += 1

        # Batch embed all new thoughts in one API call
        if new_thought_ids:
            embed_result = batch_embed_thoughts(new_thought_ids, conn=conn)
            log.info(f"Batch embedded {embed_result.get('embedded', 0)} Gmail thoughts")

    return stats


# ─── Google Drive Capture ─────────────────────────────────────────────────────

def poll_drive(account: str = None, limit: int = DRIVE_POLL_LIMIT,
               filter_noise: bool = True) -> dict:
    """
    Poll Google Drive for recently modified documents and capture them.

    Filters out code, binary, and media files by default.
    Detects updated documents by comparing modifiedTime.

    Args:
        account: 'personal', 'rbs', or None for both
        limit: Max files to check per account
        filter_noise: If True, skip code/binary/media files

    Returns:
        {captured: int, skipped: int, updated: int, filtered: int, errors: int}
    """
    from google_helper import get_service

    accounts = [account] if account else ['personal', 'rbs']
    stats = {"captured": 0, "skipped": 0, "updated": 0, "filtered": 0, "errors": 0, "accounts": accounts}
    new_thought_ids = []

    # MIME types we extract full content from (not just metadata)
    CONTENT_EXTRACTABLE_MIMES = {
        'application/vnd.google-apps.document',     # Google Docs
        'application/vnd.google-apps.spreadsheet',  # Google Sheets (titles only)
        'application/vnd.google-apps.presentation',  # Google Slides (title only)
        'text/plain',
        'text/markdown',
        'text/csv',
        'application/pdf',
        'application/vnd.openxmlformats-officedocument.wordprocessingml.document',  # .docx
        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',  # .xlsx
        'application/vnd.ms-excel.sheet.macroenabled.12',  # .xlsm
    }

    with get_db() as conn:
        for acct in accounts:
            try:
                drive_svc = get_service(acct, 'drive', 'v3')

                # List recently modified files (exclude folders and trashed)
                api_result = drive_svc.files().list(
                    q="trashed=false and mimeType != 'application/vnd.google-apps.folder'",
                    pageSize=limit,
                    fields='files(id,name,mimeType,size,modifiedTime,createdTime)',
                    orderBy='modifiedTime desc',
                    supportsAllDrives=True,
                    includeItemsFromAllDrives=True,
                ).execute()

                files = api_result.get('files', [])
                log.info(f"Drive [{acct}]: {len(files)} recent files")

                for f in files:
                    file_id = f['id']
                    mime = f.get('mimeType', '')
                    modified = f.get('modifiedTime', '')
                    name = f.get('name', 'Untitled')

                    # Filter noise (code, binary, media)
                    if filter_noise and _is_drive_noise(mime, name):
                        stats["filtered"] += 1
                        continue

                    # Check if already captured with same modifiedTime
                    existing = conn.execute(
                        """SELECT source_modified FROM capture_log
                           WHERE source_type = 'drive' AND source_id = ? AND account = ?""",
                        (file_id, acct)
                    ).fetchone()

                    if existing:
                        if existing[0] == modified:
                            stats["skipped"] += 1
                            continue
                        # File was updated — re-capture
                        stats["updated"] += 1

                    # Get content for extractable types
                    body = ""
                    if mime == 'application/vnd.google-apps.document':
                        try:
                            docs_svc = get_service(acct, 'docs', 'v1')
                            doc = docs_svc.documents().get(documentId=file_id).execute()
                            text_parts = []
                            for element in doc.get('body', {}).get('content', []):
                                if 'paragraph' in element:
                                    for pe in element['paragraph'].get('elements', []):
                                        tr = pe.get('textRun')
                                        if tr:
                                            text_parts.append(tr.get('content', ''))
                            body = ''.join(text_parts)[:MAX_BODY_LENGTH]
                        except Exception as e:
                            log.warning(f"Failed to read doc {file_id}: {e}")
                            body = ""
                    elif mime == 'application/vnd.google-apps.spreadsheet':
                        body = f"(Google Spreadsheet — {name})"
                    elif mime == 'application/vnd.google-apps.presentation':
                        body = f"(Google Slides — {name})"
                    elif mime == 'application/pdf':
                        try:
                            import io
                            from googleapiclient.http import MediaIoBaseDownload
                            req = drive_svc.files().get_media(fileId=file_id)
                            buf = io.BytesIO()
                            downloader = MediaIoBaseDownload(buf, req)
                            done = False
                            while not done:
                                _, done = downloader.next_chunk()
                            buf.seek(0)
                            try:
                                from pypdf import PdfReader
                                reader = PdfReader(buf)
                                text_parts = []
                                for page in reader.pages[:20]:  # Max 20 pages
                                    text_parts.append(page.extract_text() or '')
                                body = '\n'.join(text_parts)[:MAX_BODY_LENGTH]
                            except Exception as e:
                                log.warning(f"PDF text extraction failed for {file_id}: {e}")
                                body = f"(PDF file — {name}, extraction failed)"
                        except Exception as e:
                            log.warning(f"Failed to download PDF {file_id}: {e}")
                    elif mime in CONTENT_EXTRACTABLE_MIMES:
                        try:
                            import io
                            from googleapiclient.http import MediaIoBaseDownload
                            req = drive_svc.files().get_media(fileId=file_id)
                            buf = io.BytesIO()
                            downloader = MediaIoBaseDownload(buf, req)
                            done = False
                            while not done:
                                _, done = downloader.next_chunk()
                            body = buf.getvalue().decode('utf-8', errors='replace')[:MAX_BODY_LENGTH]
                        except Exception as e:
                            log.warning(f"Failed to download {file_id}: {e}")
                    else:
                        # Unknown type that passed the noise filter — capture with metadata
                        size = f.get('size', 'unknown')
                        body = f"({mime}, size: {size})"

                    content = f"Drive: {name}\n{body}" if body else f"Drive: {name}"

                    metadata = {
                        'file_id': file_id,
                        'name': name,
                        'mimeType': mime,
                        'modifiedTime': modified,
                        'account': acct,
                    }

                    cap_result = capture_thought(
                        content=content,
                        source='drive',
                        agent=None,
                        metadata=metadata,
                        generate_embedding=False,  # Deferred — batch embed below
                        conn=conn,
                    )

                    if 'error' in cap_result:
                        log.error(f"Capture error for {name}: {cap_result['error']}")
                        stats["errors"] += 1
                    elif cap_result.get('duplicate'):
                        stats["skipped"] += 1
                    else:
                        _log_capture(conn, 'drive', file_id, acct, cap_result['id'], modified)
                        new_thought_ids.append(cap_result['id'])
                        stats["captured"] += 1
                        log.info(f"  Captured: {name[:60]}")

                conn.commit()
            except Exception as e:
                log.error(f"Drive [{acct}] error: {e}")
                stats["errors"] += 1

        # Batch embed all new thoughts in one API call
        if new_thought_ids:
            embed_result = batch_embed_thoughts(new_thought_ids, conn=conn)
            log.info(f"Batch embedded {embed_result.get('embedded', 0)} Drive thoughts")

    return stats


# ─── Slack Capture ─────────────────────────────────────────────────────────────

# Read Slack bot token from OpenClaw config
_SLACK_TOKEN = None

def _get_slack_token() -> str:
    """Get Slack bot token from OpenClaw config."""
    global _SLACK_TOKEN
    if _SLACK_TOKEN:
        return _SLACK_TOKEN
    config_path = Path.home() / ".openclaw" / "openclaw.json"
    if config_path.exists():
        with open(config_path) as f:
            config = json.load(f)
        _SLACK_TOKEN = config.get("channels", {}).get("slack", {}).get("botToken", "")
    return _SLACK_TOKEN or ""


def poll_slack(limit: int = 50) -> dict:
    """
    Poll Slack channels/DMs for recent messages and capture them.

    Uses the Slack Web API via bot token from OpenClaw config.
    Only captures messages the bot has access to (channels it's in + DMs).

    Args:
        limit: Max messages to check per channel

    Returns:
        {captured: int, skipped: int, errors: int, channels: int}
    """
    try:
        from slack_sdk import WebClient
        from slack_sdk.errors import SlackApiError
    except ImportError:
        return {"error": "slack_sdk not installed. Run: pip install slack_sdk"}

    token = _get_slack_token()
    if not token:
        return {"error": "No Slack bot token found in ~/.openclaw/openclaw.json"}

    client = WebClient(token=token)
    stats = {"captured": 0, "skipped": 0, "errors": 0, "channels": 0}
    new_thought_ids = []

    with get_db() as conn:
        # Get list of channels/DMs — query each type separately for reliability
        try:
            channels = []
            for conv_type in ["im", "mpim", "public_channel", "private_channel"]:
                try:
                    result = client.conversations_list(types=conv_type, limit=100)
                    channels.extend(result.get("channels", []))
                except SlackApiError:
                    pass
            log.info(f"Slack: {len(channels)} accessible channels/DMs")
        except SlackApiError as e:
            log.error(f"Slack API error listing channels: {e.response['error']}")
            return {"error": f"Slack API: {e.response['error']}"}

        # Get user info cache for display names
        user_cache = {}

        def get_username(user_id):
            if user_id in user_cache:
                return user_cache[user_id]
            try:
                info = client.users_info(user=user_id)
                name = info["user"].get("real_name") or info["user"].get("name", user_id)
                user_cache[user_id] = name
                return name
            except Exception:
                user_cache[user_id] = user_id
                return user_id

        for channel in channels:
            channel_id = channel["id"]
            channel_name = channel.get("name", channel.get("user", channel_id))
            stats["channels"] += 1

            try:
                # Get recent messages
                history = client.conversations_history(
                    channel=channel_id,
                    limit=limit,
                )
                messages = history.get("messages", [])

                for msg in messages:
                    # Skip bot messages, join/leave, etc.
                    if msg.get("subtype") in ("channel_join", "channel_leave", "bot_message"):
                        continue
                    if not msg.get("text"):
                        continue

                    msg_ts = msg["ts"]
                    source_id = f"{channel_id}:{msg_ts}"

                    if _is_captured(conn, 'slack', source_id, 'default'):
                        stats["skipped"] += 1
                        continue

                    user_id = msg.get("user", "unknown")
                    username = get_username(user_id)
                    text = msg["text"][:MAX_BODY_LENGTH]
                    ts_dt = datetime.fromtimestamp(float(msg_ts))

                    content = f"Slack [{channel_name}] {username}: {text}"

                    metadata = {
                        'channel_id': channel_id,
                        'channel_name': channel_name,
                        'user_id': user_id,
                        'username': username,
                        'ts': msg_ts,
                        'date': ts_dt.isoformat(),
                    }

                    result = capture_thought(
                        content=content,
                        source='slack',
                        agent=None,
                        metadata=metadata,
                        generate_embedding=False,
                        conn=conn,
                    )

                    if 'error' in result:
                        log.error(f"Capture error for slack {source_id}: {result['error']}")
                        stats["errors"] += 1
                    elif result.get('duplicate'):
                        stats["skipped"] += 1
                    else:
                        _log_capture(conn, 'slack', source_id, 'default', result['id'])
                        new_thought_ids.append(result['id'])
                        stats["captured"] += 1

            except SlackApiError as e:
                if e.response['error'] == 'not_in_channel':
                    continue  # Skip channels bot isn't in
                log.error(f"Slack channel {channel_name}: {e.response['error']}")
                stats["errors"] += 1

        conn.commit()

        # Batch embed
        if new_thought_ids:
            embed_result = batch_embed_thoughts(new_thought_ids, conn=conn)
            log.info(f"Batch embedded {embed_result.get('embedded', 0)} Slack thoughts")

    return stats


# ─── Gmail Backfill ───────────────────────────────────────────────────────────

def backfill_gmail(account: str = None, days: int = 90,
                   full_body: bool = False, filter_noise: bool = True) -> dict:
    """
    Backfill Gmail history — paginates through all emails for the given period.

    Unlike poll_gmail which checks the most recent inbox messages,
    this paginates through ALL mail (not just inbox) for the date range.

    Args:
        account: 'personal', 'rbs', or None for both
        days: How many days back to go (default 90)
        full_body: Fetch full email body (slower)
        filter_noise: If True, skip newsletters/marketing/automated emails

    Returns:
        {captured: int, skipped: int, filtered: int, errors: int, pages: int}
    """
    from email_helper import get_service as get_gmail_service, get_email

    accounts = [account] if account else ['personal', 'rbs']
    stats = {"captured": 0, "skipped": 0, "filtered": 0, "errors": 0, "pages": 0, "accounts": accounts}
    new_thought_ids = []

    # Calculate date cutoff
    from datetime import timedelta
    cutoff = datetime.now() - timedelta(days=days)
    after_str = cutoff.strftime('%Y/%m/%d')

    with get_db() as conn:
        for acct in accounts:
            try:
                service, _ = get_gmail_service(acct)
                query = f"after:{after_str}"
                log.info(f"Gmail backfill [{acct}]: searching {query}")

                page_token = None
                page_num = 0

                while True:
                    # List message IDs (500 per page max)
                    kwargs = {
                        'userId': 'me',
                        'maxResults': 500,
                        'q': query,
                    }
                    if page_token:
                        kwargs['pageToken'] = page_token

                    result = service.users().messages().list(**kwargs).execute()
                    messages = result.get('messages', [])
                    page_num += 1
                    stats["pages"] += 1

                    log.info(f"  Page {page_num}: {len(messages)} messages")

                    for msg_meta in messages:
                        msg_id = msg_meta['id']

                        if _is_captured(conn, 'gmail', msg_id, acct):
                            stats["skipped"] += 1
                            continue

                        try:
                            # Fetch metadata for this message
                            m = service.users().messages().get(
                                userId='me', id=msg_id, format='metadata',
                                metadataHeaders=['From', 'Subject', 'Date']
                            ).execute()
                            headers = {h['name']: h['value'] for h in m['payload']['headers']}

                            sender = headers.get('From', 'Unknown')
                            subject = headers.get('Subject', 'No subject')
                            date = headers.get('Date', '')
                            snippet = m.get('snippet', '')

                            # Filter noise
                            if filter_noise and _is_gmail_noise(sender, subject):
                                stats["filtered"] += 1
                                continue

                            if full_body:
                                try:
                                    full = get_email(acct, msg_id)
                                    body = full.get('body', '')
                                    if body:
                                        body = _strip_html(body)[:MAX_BODY_LENGTH]
                                    else:
                                        body = snippet
                                except Exception as e:
                                    log.warning(f"Failed to get body for {msg_id}: {e}")
                                    body = snippet
                            else:
                                body = snippet

                            content = f"Email from {sender}: {subject}\n{body}"

                            metadata = {
                                'from': sender,
                                'subject': subject,
                                'date': date,
                                'message_id': msg_id,
                                'thread_id': msg_meta.get('threadId'),
                                'account': acct,
                            }

                            cap_result = capture_thought(
                                content=content,
                                source='gmail',
                                agent=None,
                                metadata=metadata,
                                generate_embedding=False,
                                conn=conn,
                            )

                            if 'error' in cap_result:
                                stats["errors"] += 1
                            elif cap_result.get('duplicate'):
                                stats["skipped"] += 1
                            else:
                                _log_capture(conn, 'gmail', msg_id, acct, cap_result['id'])
                                new_thought_ids.append(cap_result['id'])
                                stats["captured"] += 1

                        except Exception as e:
                            log.warning(f"Error processing {msg_id}: {e}")
                            stats["errors"] += 1

                    conn.commit()

                    # Batch embed every 500 captures to avoid memory buildup
                    if len(new_thought_ids) >= 500:
                        embed_result = batch_embed_thoughts(new_thought_ids, conn=conn)
                        log.info(f"  Batch embedded {embed_result.get('embedded', 0)} thoughts")
                        new_thought_ids = []

                    # Check for next page
                    page_token = result.get('nextPageToken')
                    if not page_token:
                        break

                log.info(f"Gmail backfill [{acct}] done: {stats['captured']} captured, "
                         f"{stats['skipped']} skipped, {stats['errors']} errors")

            except Exception as e:
                log.error(f"Gmail backfill [{acct}] error: {e}")
                stats["errors"] += 1

        # Final batch embed for remaining
        if new_thought_ids:
            embed_result = batch_embed_thoughts(new_thought_ids, conn=conn)
            log.info(f"Final batch embedded {embed_result.get('embedded', 0)} thoughts")

    return stats


# ─── Drive Backfill ──────────────────────────────────────────────────────────

def backfill_drive(account: str = None, days: int = None,
                   filter_noise: bool = True) -> dict:
    """
    Backfill Google Drive — paginates through all documents.

    Unlike poll_drive which only checks the most recent page,
    this paginates through ALL files, optionally filtered by creation date.
    Filters out code, binary, and media files by default.

    Args:
        account: 'personal', 'rbs', or None for both
        days: Only capture files created in last N days (None = all files)
        filter_noise: If True, skip code/binary/media files

    Returns:
        {captured: int, skipped: int, updated: int, filtered: int, errors: int, pages: int}
    """
    from google_helper import get_service

    accounts = [account] if account else ['personal', 'rbs']
    stats = {"captured": 0, "skipped": 0, "updated": 0, "filtered": 0, "errors": 0, "pages": 0, "accounts": accounts}
    new_thought_ids = []

    CONTENT_EXTRACTABLE_MIMES = {
        'application/vnd.google-apps.document',
        'application/vnd.google-apps.spreadsheet',
        'application/vnd.google-apps.presentation',
        'text/plain',
        'text/markdown',
        'text/csv',
        'application/pdf',
        'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        'application/vnd.ms-excel.sheet.macroenabled.12',
    }

    with get_db() as conn:
        for acct in accounts:
            try:
                drive_svc = get_service(acct, 'drive', 'v3')

                q_parts = ["trashed=false", "mimeType != 'application/vnd.google-apps.folder'"]
                if days:
                    from datetime import timedelta
                    cutoff = datetime.now() - timedelta(days=days)
                    q_parts.append(f"createdTime > '{cutoff.strftime('%Y-%m-%dT%H:%M:%S')}'")
                query = " and ".join(q_parts)

                page_token = None
                page_num = 0

                while True:
                    kwargs = {
                        'q': query,
                        'pageSize': 100,
                        'fields': 'nextPageToken, files(id,name,mimeType,size,modifiedTime,createdTime)',
                        'orderBy': 'modifiedTime desc',
                        'supportsAllDrives': True,
                        'includeItemsFromAllDrives': True,
                    }
                    if page_token:
                        kwargs['pageToken'] = page_token

                    api_result = drive_svc.files().list(**kwargs).execute()
                    files = api_result.get('files', [])
                    page_num += 1
                    stats["pages"] += 1

                    log.info(f"  Drive [{acct}] page {page_num}: {len(files)} files")

                    for f in files:
                        file_id = f['id']
                        mime = f.get('mimeType', '')
                        modified = f.get('modifiedTime', '')
                        name = f.get('name', 'Untitled')

                        # Filter noise (code, binary, media)
                        if filter_noise and _is_drive_noise(mime, name):
                            stats["filtered"] += 1
                            continue

                        # Check if already captured with same modifiedTime
                        existing = conn.execute(
                            """SELECT source_modified FROM capture_log
                               WHERE source_type = 'drive' AND source_id = ? AND account = ?""",
                            (file_id, acct)
                        ).fetchone()

                        if existing:
                            if existing[0] == modified:
                                stats["skipped"] += 1
                                continue
                            stats["updated"] += 1

                        # Get content for extractable types
                        body = ""
                        if mime == 'application/vnd.google-apps.document':
                            try:
                                docs_svc = get_service(acct, 'docs', 'v1')
                                doc = docs_svc.documents().get(documentId=file_id).execute()
                                text_parts = []
                                for element in doc.get('body', {}).get('content', []):
                                    if 'paragraph' in element:
                                        for pe in element['paragraph'].get('elements', []):
                                            tr = pe.get('textRun')
                                            if tr:
                                                text_parts.append(tr.get('content', ''))
                                body = ''.join(text_parts)[:MAX_BODY_LENGTH]
                            except Exception as e:
                                log.warning(f"Failed to read doc {file_id}: {e}")
                                body = ""
                        elif mime == 'application/vnd.google-apps.spreadsheet':
                            body = f"(Google Spreadsheet — {name})"
                        elif mime == 'application/vnd.google-apps.presentation':
                            body = f"(Google Slides — {name})"
                        elif mime == 'application/pdf':
                            try:
                                import io
                                from googleapiclient.http import MediaIoBaseDownload
                                req = drive_svc.files().get_media(fileId=file_id)
                                buf = io.BytesIO()
                                downloader = MediaIoBaseDownload(buf, req)
                                done = False
                                while not done:
                                    _, done = downloader.next_chunk()
                                buf.seek(0)
                                try:
                                    from pypdf import PdfReader
                                    reader = PdfReader(buf)
                                    text_parts = []
                                    for page in reader.pages[:20]:  # Max 20 pages
                                        text_parts.append(page.extract_text() or '')
                                    body = '\n'.join(text_parts)[:MAX_BODY_LENGTH]
                                except Exception as e:
                                    log.warning(f"PDF text extraction failed for {file_id}: {e}")
                                    body = f"(PDF file — {name}, extraction failed)"
                            except Exception as e:
                                log.warning(f"Failed to download PDF {file_id}: {e}")
                        elif mime in CONTENT_EXTRACTABLE_MIMES:
                            try:
                                import io
                                from googleapiclient.http import MediaIoBaseDownload
                                req = drive_svc.files().get_media(fileId=file_id)
                                buf = io.BytesIO()
                                downloader = MediaIoBaseDownload(buf, req)
                                done = False
                                while not done:
                                    _, done = downloader.next_chunk()
                                body = buf.getvalue().decode('utf-8', errors='replace')[:MAX_BODY_LENGTH]
                            except Exception as e:
                                log.warning(f"Failed to download {file_id}: {e}")
                        else:
                            # Unknown type that passed noise filter — capture with metadata
                            size = f.get('size', 'unknown')
                            body = f"({mime}, size: {size})"

                        content = f"Drive: {name}\n{body}" if body else f"Drive: {name}"

                        metadata = {
                            'file_id': file_id,
                            'name': name,
                            'mimeType': mime,
                            'modifiedTime': modified,
                            'account': acct,
                        }

                        cap_result = capture_thought(
                            content=content,
                            source='drive',
                            agent=None,
                            metadata=metadata,
                            generate_embedding=False,
                            conn=conn,
                        )

                        if 'error' in cap_result:
                            stats["errors"] += 1
                        elif cap_result.get('duplicate'):
                            stats["skipped"] += 1
                        else:
                            _log_capture(conn, 'drive', file_id, acct, cap_result['id'], modified)
                            new_thought_ids.append(cap_result['id'])
                            stats["captured"] += 1
                            log.info(f"    Captured: {name[:60]}")

                    conn.commit()

                    # Batch embed every 500 captures
                    if len(new_thought_ids) >= 500:
                        embed_result = batch_embed_thoughts(new_thought_ids, conn=conn)
                        log.info(f"  Batch embedded {embed_result.get('embedded', 0)} thoughts")
                        new_thought_ids = []

                    page_token = api_result.get('nextPageToken')
                    if not page_token:
                        break

                log.info(f"Drive backfill [{acct}] done")

            except Exception as e:
                log.error(f"Drive backfill [{acct}] error: {e}")
                stats["errors"] += 1

        # Final batch embed
        if new_thought_ids:
            embed_result = batch_embed_thoughts(new_thought_ids, conn=conn)
            log.info(f"Final batch embedded {embed_result.get('embedded', 0)} thoughts")

    return stats


# ─── Cleanup ─────────────────────────────────────────────────────────────────

def cleanup_gmail_noise(dry_run: bool = True) -> dict:
    """
    Retroactively remove noisy Gmail thoughts that match the noise filter.

    Deletes thoughts + their embeddings + capture_log entries for emails
    identified as noise by _is_gmail_noise().

    Args:
        dry_run: If True, only count — don't delete. Set False to actually delete.

    Returns:
        {total_gmail: int, noise: int, deleted: int, examples: [...]}
    """
    with get_db() as conn:
        # Find all Gmail thoughts with their metadata
        rows = conn.execute(
            "SELECT id, content, metadata FROM thoughts WHERE source = 'gmail'"
        ).fetchall()

        stats = {"total_gmail": len(rows), "noise": 0, "deleted": 0, "kept": 0, "examples": []}

        noise_ids = []
        for thought_id, content, metadata_json in rows:
            try:
                meta = json.loads(metadata_json) if metadata_json else {}
            except (json.JSONDecodeError, TypeError):
                meta = {}

            sender = meta.get('from', '')
            subject = meta.get('subject', '')

            # Also try to parse from content if metadata is sparse
            if not sender and content.startswith('Email from '):
                # Parse "Email from Sender: Subject\nBody"
                first_line = content.split('\n')[0]
                parts = first_line.replace('Email from ', '', 1).split(': ', 1)
                if len(parts) == 2:
                    sender, subject = parts

            if _is_gmail_noise(sender, subject):
                noise_ids.append(thought_id)
                stats["noise"] += 1
                if len(stats["examples"]) < 10:
                    stats["examples"].append(f"  [{thought_id}] {sender[:40]} — {subject[:50]}")
            else:
                stats["kept"] += 1

        if not dry_run and noise_ids:
            # Delete in batches of 500
            for i in range(0, len(noise_ids), 500):
                batch = noise_ids[i:i + 500]
                placeholders = ','.join(['?'] * len(batch))

                # Delete embeddings
                conn.execute(
                    f"DELETE FROM thought_embeddings WHERE thought_id IN ({placeholders})",
                    batch
                )
                # Delete capture_log entries
                conn.execute(
                    f"""DELETE FROM capture_log WHERE source_type = 'gmail'
                        AND thought_id IN ({placeholders})""",
                    batch
                )
                # Delete thoughts (FTS cleanup handled by thoughts_ad trigger)
                conn.execute(
                    f"DELETE FROM thoughts WHERE id IN ({placeholders})",
                    batch
                )
                stats["deleted"] += len(batch)

            conn.commit()
            log.info(f"Deleted {stats['deleted']} noisy Gmail thoughts")
        elif dry_run:
            log.info(f"Dry run: {stats['noise']} noisy Gmail thoughts would be deleted "
                     f"out of {stats['total_gmail']} total")

        return stats


def cleanup_drive_noise(dry_run: bool = True) -> dict:
    """
    Retroactively remove noisy Drive thoughts (code, binary, media stubs).

    Deletes thoughts + their embeddings + capture_log entries for Drive files
    identified as noise by _is_drive_noise().

    Args:
        dry_run: If True, only count — don't delete. Set False to actually delete.

    Returns:
        {total_drive: int, noise: int, deleted: int, kept: int, examples: [...]}
    """
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, content, metadata FROM thoughts WHERE source = 'drive'"
        ).fetchall()

        stats = {"total_drive": len(rows), "noise": 0, "deleted": 0, "kept": 0, "examples": []}

        noise_ids = []
        for thought_id, content, metadata_json in rows:
            try:
                meta = json.loads(metadata_json) if metadata_json else {}
            except (json.JSONDecodeError, TypeError):
                meta = {}

            mime = meta.get('mimeType', meta.get('mime_type', ''))
            name = meta.get('name', '')

            # Fallback: parse name from content "Drive: filename\n..."
            if not name and content and content.startswith('Drive: '):
                name = content.split('\n')[0].replace('Drive: ', '', 1).strip()

            if _is_drive_noise(mime, name):
                noise_ids.append(thought_id)
                stats["noise"] += 1
                if len(stats["examples"]) < 10:
                    stats["examples"].append(f"  [{thought_id}] {name[:40]} ({mime})")
            else:
                stats["kept"] += 1

        if not dry_run and noise_ids:
            for i in range(0, len(noise_ids), 500):
                batch = noise_ids[i:i + 500]
                placeholders = ','.join(['?'] * len(batch))

                # Delete embeddings first
                conn.execute(
                    f"DELETE FROM thought_embeddings WHERE thought_id IN ({placeholders})",
                    batch
                )
                # Delete capture_log entries
                conn.execute(
                    f"""DELETE FROM capture_log WHERE source_type = 'drive'
                        AND thought_id IN ({placeholders})""",
                    batch
                )
                # Delete thoughts (FTS cleanup handled by thoughts_ad trigger)
                conn.execute(
                    f"DELETE FROM thoughts WHERE id IN ({placeholders})",
                    batch
                )
                stats["deleted"] += len(batch)

            conn.commit()
            log.info(f"Deleted {stats['deleted']} noisy Drive thoughts")
        elif dry_run:
            log.info(f"Dry run: {stats['noise']} noisy Drive thoughts would be deleted "
                     f"out of {stats['total_drive']} total")

        return stats


# ─── Status & Stats ───────────────────────────────────────────────────────────

def capture_status() -> dict:
    """Get capture statistics from the log."""
    with get_db() as conn:
        stats = {}
        rows = conn.execute(
            "SELECT source_type, account, COUNT(*) FROM capture_log GROUP BY source_type, account"
        ).fetchall()
        for source_type, account, count in rows:
            key = f"{source_type}:{account}" if account else source_type
            stats[key] = count

        stats["total"] = conn.execute("SELECT COUNT(*) FROM capture_log").fetchone()[0]

        latest = conn.execute(
            "SELECT source_type, account, MAX(captured_at) FROM capture_log GROUP BY source_type, account"
        ).fetchall()
        stats["latest"] = {
            f"{r[0]}:{r[1]}" if r[1] else r[0]: r[2] for r in latest
        }

        return stats


# ─── CLI ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        prog='capture-sources',
        description='Claw Recall — External Source Capture (Gmail, Drive)',
    )
    parser.add_argument('source', choices=['gmail', 'drive', 'slack', 'all', 'status', 'cleanup'],
                        help='Which source to poll (or "cleanup" to remove Gmail noise)')
    parser.add_argument('--account', '-a', choices=['personal', 'rbs'],
                        help='Specific account (default: both)')
    parser.add_argument('--limit', '-n', type=int, default=50,
                        help='Max items to check per account')
    parser.add_argument('--full-body', action='store_true',
                        help='Gmail: fetch full email body (slower)')
    parser.add_argument('--backfill', action='store_true',
                        help='Backfill mode: paginate through all history')
    parser.add_argument('--days', '-d', type=int, default=90,
                        help='Backfill: how many days back (default: 90)')
    parser.add_argument('--no-filter', action='store_true',
                        help='Gmail: disable noise filter (capture everything)')
    parser.add_argument('--confirm', action='store_true',
                        help='Cleanup: actually delete (default is dry run)')
    parser.add_argument('--quiet', '-q', action='store_true',
                        help='Minimal output')

    args = parser.parse_args()

    if args.quiet:
        logging.getLogger().setLevel(logging.WARNING)

    if args.source == 'status':
        stats = capture_status()
        print("Claw Recall — Capture Status")
        print(f"  Total captured: {stats.get('total', 0)}")
        for key, count in sorted(stats.items()):
            if key not in ('total', 'latest'):
                print(f"  {key}: {count}")
        if stats.get('latest'):
            print("\n  Latest captures:")
            for key, ts in stats['latest'].items():
                print(f"    {key}: {ts}")
        return

    if args.source == 'cleanup':
        # Gmail cleanup
        print("Scanning Gmail thoughts for noise...")
        gmail_stats = cleanup_gmail_noise(dry_run=not args.confirm)
        print(f"\n  Gmail — Total: {gmail_stats['total_gmail']}, "
              f"Noise: {gmail_stats['noise']}, Keep: {gmail_stats['kept']}")
        if gmail_stats['examples']:
            print("  Examples:")
            for ex in gmail_stats['examples'][:5]:
                print(f"    {ex}")

        # Drive cleanup
        print("\nScanning Drive thoughts for noise...")
        drive_stats = cleanup_drive_noise(dry_run=not args.confirm)
        print(f"\n  Drive — Total: {drive_stats['total_drive']}, "
              f"Noise: {drive_stats['noise']}, Keep: {drive_stats['kept']}")
        if drive_stats['examples']:
            print("  Examples:")
            for ex in drive_stats['examples'][:5]:
                print(f"    {ex}")

        total_noise = gmail_stats['noise'] + drive_stats['noise']
        if args.confirm:
            total_deleted = gmail_stats['deleted'] + drive_stats['deleted']
            print(f"\n  Deleted {total_deleted} noisy thoughts + embeddings + capture_log entries")
        elif total_noise > 0:
            print(f"\n  Dry run — {total_noise} total noise records. To actually delete, run:")
            print(f"    python3 capture_sources.py cleanup --confirm")
        return

    results = {}
    if args.source in ('gmail', 'all'):
        if args.backfill:
            filter_on = not args.no_filter
            print(f"Backfilling Gmail ({args.days} days, filter={'on' if filter_on else 'off'})...")
            results['gmail'] = backfill_gmail(
                account=args.account,
                days=args.days,
                full_body=args.full_body,
                filter_noise=filter_on,
            )
            g = results['gmail']
            print(f"  Gmail: {g['captured']} captured, {g['filtered']} filtered, "
                  f"{g['skipped']} skipped, {g['errors']} errors ({g['pages']} pages)")
        else:
            filter_on = not args.no_filter
            print(f"Polling Gmail{' (no filter)' if not filter_on else ''}...")
            results['gmail'] = poll_gmail(
                account=args.account,
                limit=args.limit,
                full_body=args.full_body,
                filter_noise=filter_on,
            )
            g = results['gmail']
            print(f"  Gmail: {g['captured']} captured, {g.get('filtered', 0)} filtered, "
                  f"{g['skipped']} skipped, {g['errors']} errors")

    if args.source in ('drive', 'all'):
        filter_on = not args.no_filter
        if args.backfill:
            print(f"Backfilling Google Drive{f' ({args.days} days)' if args.days != 90 else ''}"
                  f"{' (no filter)' if not filter_on else ''}...")
            results['drive'] = backfill_drive(
                account=args.account,
                days=args.days if args.days != 90 else None,
                filter_noise=filter_on,
            )
            d = results['drive']
            print(f"  Drive: {d['captured']} captured, {d.get('filtered', 0)} filtered, "
                  f"{d['updated']} updated, {d['skipped']} skipped, "
                  f"{d['errors']} errors ({d['pages']} pages)")
        else:
            print(f"Polling Google Drive{' (no filter)' if not filter_on else ''}...")
            results['drive'] = poll_drive(
                account=args.account,
                limit=args.limit,
                filter_noise=filter_on,
            )
            d = results['drive']
            print(f"  Drive: {d['captured']} captured, {d.get('filtered', 0)} filtered, "
                  f"{d['updated']} updated, {d['skipped']} skipped, {d['errors']} errors")

    if args.source in ('slack', 'all'):
        print("Polling Slack...")
        results['slack'] = poll_slack(limit=args.limit)
        s = results['slack']
        if 'error' in s:
            print(f"  Slack: {s['error']}")
        else:
            print(f"  Slack: {s['captured']} captured, {s['skipped']} skipped, "
                  f"{s['errors']} errors ({s['channels']} channels)")

    # Summary
    total_captured = sum(r.get('captured', 0) for r in results.values())
    total_errors = sum(r.get('errors', 0) for r in results.values())
    if total_captured > 0 or total_errors > 0:
        print(f"\nTotal: {total_captured} new captures, {total_errors} errors")


if __name__ == "__main__":
    main()
