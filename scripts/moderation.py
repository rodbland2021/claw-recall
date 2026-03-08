#!/usr/bin/env python3
"""
Claw Recall Discord Moderation Script

Commands:
    timeout <guild_id> <user_id> <duration> <reason> [--moderator <id>]
    ban <guild_id> <user_id> <reason> [--moderator <id>] [--delete-days <0-7>]
    kick <guild_id> <user_id> <reason> [--moderator <id>]
    unban <guild_id> <user_id> [--moderator <id>]
    untimeout <guild_id> <user_id> [--moderator <id>]
    warn <guild_id> <user_id> <reason> [--moderator <id>]
    strikes <guild_id> <user_id>
    purge <channel_id> <count>
    log <guild_id> <action> <user_id> <reason> [--moderator <id>]
    raid-check <guild_id> [--window <seconds>] [--threshold <count>]
    member-info <guild_id> <user_id>
    has-role <guild_id> <user_id> <role_id>

Duration format: 1m, 5m, 1h, 6h, 1d, 7d, 28d (max 28 days for Discord timeout)

Environment:
    DISCORD_BOT_TOKEN - Bot token (or reads from OpenClaw config)
    MOD_DB_PATH - Path to moderation SQLite DB (default: data/moderation.db)
"""

import argparse
import json
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError

SCRIPT_DIR = Path(__file__).parent.resolve()
REPO_DIR = SCRIPT_DIR.parent
DEFAULT_DB = REPO_DIR / "data" / "moderation.db"

DISCORD_API = "https://discord.com/api/v10"


def get_bot_token():
    """Get bot token from env or OpenClaw config."""
    token = os.environ.get("DISCORD_BOT_TOKEN")
    if token:
        return token

    config_path = Path.home() / ".openclaw" / "openclaw.json"
    if config_path.exists():
        with open(config_path) as f:
            config = json.load(f)
        return (
            config.get("channels", {})
            .get("discord", {})
            .get("accounts", {})
            .get("claw-recall", {})
            .get("token", "")
        )
    return ""


def discord_request(method, path, token, data=None):
    """Make a Discord API request."""
    url = f"{DISCORD_API}{path}"
    headers = {
        "Authorization": f"Bot {token}",
        "Content-Type": "application/json",
        "User-Agent": "DiscordBot (https://github.com/rodbland2021/claw-recall, 1.0)",
    }

    body = json.dumps(data).encode() if data else None
    req = Request(url, data=body, headers=headers, method=method)

    try:
        with urlopen(req) as resp:
            if resp.status == 204:
                return {"ok": True}
            return json.loads(resp.read())
    except HTTPError as e:
        error_body = e.read().decode()
        try:
            error_json = json.loads(error_body)
        except json.JSONDecodeError:
            error_json = {"message": error_body}
        return {"error": True, "status": e.code, "detail": error_json}


def parse_duration(s):
    """Parse duration string to seconds and ISO timestamp."""
    match = re.match(r"^(\d+)(m|h|d)$", s.lower())
    if not match:
        raise ValueError(f"Invalid duration: {s}. Use format: 1m, 5m, 1h, 6h, 1d, 7d, 28d")

    value, unit = int(match.group(1)), match.group(2)
    multiplier = {"m": 60, "h": 3600, "d": 86400}
    seconds = value * multiplier[unit]

    max_timeout = 28 * 86400  # 28 days
    if seconds > max_timeout:
        raise ValueError(f"Duration {s} exceeds Discord maximum of 28 days")

    until = datetime.now(timezone.utc) + timedelta(seconds=seconds)
    return seconds, until.isoformat()


def get_db(db_path=None):
    """Get or create the moderation database."""
    path = Path(db_path or os.environ.get("MOD_DB_PATH", DEFAULT_DB))
    path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")

    conn.executescript("""
        CREATE TABLE IF NOT EXISTS mod_actions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            moderator_id TEXT,
            action TEXT NOT NULL,
            reason TEXT,
            duration_seconds INTEGER,
            created_at TEXT DEFAULT (datetime('now')),
            expires_at TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_mod_guild_user
            ON mod_actions(guild_id, user_id);
        CREATE INDEX IF NOT EXISTS idx_mod_created
            ON mod_actions(created_at);
        CREATE INDEX IF NOT EXISTS idx_mod_action
            ON mod_actions(action);

        CREATE TABLE IF NOT EXISTS raid_joins (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            joined_at TEXT DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_raid_guild_time
            ON raid_joins(guild_id, joined_at);
    """)

    return conn


def log_action(conn, guild_id, user_id, action, reason=None, moderator_id=None,
               duration_seconds=None, expires_at=None):
    """Log a moderation action to the database."""
    conn.execute(
        """INSERT INTO mod_actions (guild_id, user_id, moderator_id, action, reason,
           duration_seconds, expires_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (guild_id, user_id, moderator_id, action, reason, duration_seconds, expires_at),
    )
    conn.commit()


def cmd_timeout(args):
    """Timeout a user."""
    token = get_bot_token()
    seconds, until_iso = parse_duration(args.duration)

    result = discord_request(
        "PATCH",
        f"/guilds/{args.guild_id}/members/{args.user_id}",
        token,
        {"communication_disabled_until": until_iso},
    )

    if "error" in result:
        print(json.dumps({"ok": False, "error": result["detail"]}, indent=2))
        return 1

    conn = get_db()
    log_action(conn, args.guild_id, args.user_id, "timeout", args.reason,
               args.moderator, seconds, until_iso)

    print(json.dumps({
        "ok": True,
        "action": "timeout",
        "user_id": args.user_id,
        "duration": args.duration,
        "until": until_iso,
        "reason": args.reason,
    }, indent=2))
    return 0


def cmd_untimeout(args):
    """Remove a timeout from a user."""
    token = get_bot_token()

    result = discord_request(
        "PATCH",
        f"/guilds/{args.guild_id}/members/{args.user_id}",
        token,
        {"communication_disabled_until": None},
    )

    if "error" in result:
        print(json.dumps({"ok": False, "error": result["detail"]}, indent=2))
        return 1

    conn = get_db()
    log_action(conn, args.guild_id, args.user_id, "untimeout",
               moderator_id=args.moderator)

    print(json.dumps({
        "ok": True,
        "action": "untimeout",
        "user_id": args.user_id,
    }, indent=2))
    return 0


def cmd_ban(args):
    """Ban a user."""
    token = get_bot_token()
    data = {"reason": args.reason}
    if args.delete_days:
        data["delete_message_seconds"] = min(args.delete_days, 7) * 86400

    result = discord_request(
        "PUT",
        f"/guilds/{args.guild_id}/bans/{args.user_id}",
        token,
        data,
    )

    if "error" in result:
        print(json.dumps({"ok": False, "error": result["detail"]}, indent=2))
        return 1

    conn = get_db()
    log_action(conn, args.guild_id, args.user_id, "ban", args.reason, args.moderator)

    print(json.dumps({
        "ok": True,
        "action": "ban",
        "user_id": args.user_id,
        "reason": args.reason,
    }, indent=2))
    return 0


def cmd_unban(args):
    """Unban a user."""
    token = get_bot_token()

    result = discord_request(
        "DELETE",
        f"/guilds/{args.guild_id}/bans/{args.user_id}",
        token,
    )

    if "error" in result:
        print(json.dumps({"ok": False, "error": result["detail"]}, indent=2))
        return 1

    conn = get_db()
    log_action(conn, args.guild_id, args.user_id, "unban", moderator_id=args.moderator)

    print(json.dumps({
        "ok": True,
        "action": "unban",
        "user_id": args.user_id,
    }, indent=2))
    return 0


def cmd_kick(args):
    """Kick a user."""
    token = get_bot_token()

    result = discord_request(
        "DELETE",
        f"/guilds/{args.guild_id}/members/{args.user_id}",
        token,
    )

    if "error" in result:
        print(json.dumps({"ok": False, "error": result["detail"]}, indent=2))
        return 1

    conn = get_db()
    log_action(conn, args.guild_id, args.user_id, "kick", args.reason, args.moderator)

    print(json.dumps({
        "ok": True,
        "action": "kick",
        "user_id": args.user_id,
        "reason": args.reason,
    }, indent=2))
    return 0


def cmd_warn(args):
    """Issue a warning (recorded in DB only, no Discord action)."""
    conn = get_db()
    log_action(conn, args.guild_id, args.user_id, "warn", args.reason, args.moderator)

    # Count total strikes for this user
    row = conn.execute(
        """SELECT COUNT(*) as count FROM mod_actions
           WHERE guild_id = ? AND user_id = ? AND action IN ('warn', 'timeout', 'ban')""",
        (args.guild_id, args.user_id),
    ).fetchone()

    strike_count = row["count"]

    print(json.dumps({
        "ok": True,
        "action": "warn",
        "user_id": args.user_id,
        "reason": args.reason,
        "total_strikes": strike_count,
        "auto_action": (
            "timeout recommended" if strike_count == 2
            else "ban recommended" if strike_count >= 3
            else None
        ),
    }, indent=2))
    return 0


def cmd_strikes(args):
    """Check a user's strike history."""
    conn = get_db()

    rows = conn.execute(
        """SELECT action, reason, moderator_id, created_at, duration_seconds, expires_at
           FROM mod_actions
           WHERE guild_id = ? AND user_id = ?
           ORDER BY created_at DESC""",
        (args.guild_id, args.user_id),
    ).fetchall()

    strikes = [dict(r) for r in rows]
    active_strikes = [s for s in strikes if s["action"] in ("warn", "timeout", "ban")]

    print(json.dumps({
        "ok": True,
        "user_id": args.user_id,
        "total_actions": len(strikes),
        "strike_count": len(active_strikes),
        "history": strikes,
    }, indent=2))
    return 0


def cmd_purge(args):
    """Delete the last N messages from a channel."""
    token = get_bot_token()
    count = min(args.count, 100)  # Discord max is 100

    # Get message IDs
    messages = discord_request(
        "GET",
        f"/channels/{args.channel_id}/messages?limit={count}",
        token,
    )

    if "error" in messages:
        print(json.dumps({"ok": False, "error": messages["detail"]}, indent=2))
        return 1

    if not isinstance(messages, list) or len(messages) == 0:
        print(json.dumps({"ok": True, "deleted": 0}, indent=2))
        return 0

    msg_ids = [m["id"] for m in messages]

    if len(msg_ids) == 1:
        result = discord_request(
            "DELETE",
            f"/channels/{args.channel_id}/messages/{msg_ids[0]}",
            token,
        )
    else:
        result = discord_request(
            "POST",
            f"/channels/{args.channel_id}/messages/bulk-delete",
            token,
            {"messages": msg_ids},
        )

    if "error" in result:
        print(json.dumps({"ok": False, "error": result["detail"]}, indent=2))
        return 1

    print(json.dumps({
        "ok": True,
        "action": "purge",
        "channel_id": args.channel_id,
        "deleted": len(msg_ids),
    }, indent=2))
    return 0


def cmd_raid_check(args):
    """Check for raid patterns (rapid joins)."""
    conn = get_db()
    window = args.window or 60
    threshold = args.threshold or 5

    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=window)).isoformat()

    rows = conn.execute(
        """SELECT COUNT(*) as count, GROUP_CONCAT(user_id) as user_ids
           FROM raid_joins
           WHERE guild_id = ? AND joined_at > ?""",
        (args.guild_id, cutoff),
    ).fetchone()

    count = rows["count"]
    is_raid = count >= threshold

    print(json.dumps({
        "ok": True,
        "guild_id": args.guild_id,
        "window_seconds": window,
        "threshold": threshold,
        "recent_joins": count,
        "is_raid": is_raid,
        "user_ids": rows["user_ids"].split(",") if rows["user_ids"] else [],
    }, indent=2))
    return 0


def cmd_log_join(args):
    """Log a member join for raid detection."""
    conn = get_db()
    conn.execute(
        "INSERT INTO raid_joins (guild_id, user_id) VALUES (?, ?)",
        (args.guild_id, args.user_id),
    )
    conn.commit()

    # Clean up old entries (keep 24h)
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    conn.execute("DELETE FROM raid_joins WHERE joined_at < ?", (cutoff,))
    conn.commit()

    print(json.dumps({"ok": True, "logged": True}))
    return 0


def cmd_member_info(args):
    """Get member info from Discord."""
    token = get_bot_token()

    member = discord_request(
        "GET",
        f"/guilds/{args.guild_id}/members/{args.user_id}",
        token,
    )

    if "error" in member:
        print(json.dumps({"ok": False, "error": member["detail"]}, indent=2))
        return 1

    user = member.get("user", {})
    joined = member.get("joined_at", "")
    roles = member.get("roles", [])

    # Check account age
    # Discord snowflake epoch: 2015-01-01T00:00:00Z = 1420070400000
    user_id = int(user.get("id", "0"))
    created_ms = ((user_id >> 22) + 1420070400000) if user_id else 0
    created_at = datetime.fromtimestamp(created_ms / 1000, tz=timezone.utc).isoformat() if created_ms else ""
    account_age_days = (datetime.now(timezone.utc) - datetime.fromtimestamp(created_ms / 1000, tz=timezone.utc)).days if created_ms else 0

    print(json.dumps({
        "ok": True,
        "user_id": user.get("id"),
        "username": user.get("username"),
        "display_name": member.get("nick") or user.get("global_name") or user.get("username"),
        "account_created": created_at,
        "account_age_days": account_age_days,
        "joined_guild": joined,
        "roles": roles,
        "is_bot": user.get("bot", False),
    }, indent=2))
    return 0


def cmd_has_role(args):
    """Check if a user has a specific role."""
    token = get_bot_token()

    member = discord_request(
        "GET",
        f"/guilds/{args.guild_id}/members/{args.user_id}",
        token,
    )

    if "error" in member:
        print(json.dumps({"ok": False, "error": member["detail"]}, indent=2))
        return 1

    has_it = args.role_id in member.get("roles", [])

    # Also check if user is guild owner (implicit admin)
    guild = discord_request("GET", f"/guilds/{args.guild_id}", token)
    is_owner = guild.get("owner_id") == args.user_id

    print(json.dumps({
        "ok": True,
        "user_id": args.user_id,
        "role_id": args.role_id,
        "has_role": has_it,
        "is_owner": is_owner,
        "is_authorized": has_it or is_owner,
    }, indent=2))
    return 0


def cmd_mod_summary(args):
    """Generate a moderation summary for the last N hours."""
    conn = get_db()
    hours = args.hours or 24
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()

    # Mod actions
    actions = conn.execute(
        """SELECT action, COUNT(*) as count
           FROM mod_actions
           WHERE guild_id = ? AND created_at > ?
           GROUP BY action""",
        (args.guild_id, cutoff),
    ).fetchall()

    # Recent joins
    joins = conn.execute(
        """SELECT COUNT(*) as count FROM raid_joins
           WHERE guild_id = ? AND joined_at > ?""",
        (args.guild_id, cutoff),
    ).fetchone()

    # Recent details
    recent = conn.execute(
        """SELECT action, user_id, reason, moderator_id, created_at
           FROM mod_actions
           WHERE guild_id = ? AND created_at > ?
           ORDER BY created_at DESC LIMIT 20""",
        (args.guild_id, cutoff),
    ).fetchall()

    summary = {
        "ok": True,
        "period_hours": hours,
        "action_counts": {r["action"]: r["count"] for r in actions},
        "new_joins": joins["count"],
        "recent_actions": [dict(r) for r in recent],
    }

    print(json.dumps(summary, indent=2))
    return 0


def main():
    parser = argparse.ArgumentParser(description="Claw Recall Discord Moderation")
    subs = parser.add_subparsers(dest="command")

    # timeout
    p = subs.add_parser("timeout")
    p.add_argument("guild_id")
    p.add_argument("user_id")
    p.add_argument("duration")
    p.add_argument("reason")
    p.add_argument("--moderator", default=None)

    # untimeout
    p = subs.add_parser("untimeout")
    p.add_argument("guild_id")
    p.add_argument("user_id")
    p.add_argument("--moderator", default=None)

    # ban
    p = subs.add_parser("ban")
    p.add_argument("guild_id")
    p.add_argument("user_id")
    p.add_argument("reason")
    p.add_argument("--moderator", default=None)
    p.add_argument("--delete-days", type=int, default=0)

    # unban
    p = subs.add_parser("unban")
    p.add_argument("guild_id")
    p.add_argument("user_id")
    p.add_argument("--moderator", default=None)

    # kick
    p = subs.add_parser("kick")
    p.add_argument("guild_id")
    p.add_argument("user_id")
    p.add_argument("reason")
    p.add_argument("--moderator", default=None)

    # warn
    p = subs.add_parser("warn")
    p.add_argument("guild_id")
    p.add_argument("user_id")
    p.add_argument("reason")
    p.add_argument("--moderator", default=None)

    # strikes
    p = subs.add_parser("strikes")
    p.add_argument("guild_id")
    p.add_argument("user_id")

    # purge
    p = subs.add_parser("purge")
    p.add_argument("channel_id")
    p.add_argument("count", type=int)

    # raid-check
    p = subs.add_parser("raid-check")
    p.add_argument("guild_id")
    p.add_argument("--window", type=int, default=60)
    p.add_argument("--threshold", type=int, default=5)

    # log-join
    p = subs.add_parser("log-join")
    p.add_argument("guild_id")
    p.add_argument("user_id")

    # member-info
    p = subs.add_parser("member-info")
    p.add_argument("guild_id")
    p.add_argument("user_id")

    # has-role
    p = subs.add_parser("has-role")
    p.add_argument("guild_id")
    p.add_argument("user_id")
    p.add_argument("role_id")

    # mod-summary
    p = subs.add_parser("mod-summary")
    p.add_argument("guild_id")
    p.add_argument("--hours", type=int, default=24)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    commands = {
        "timeout": cmd_timeout,
        "untimeout": cmd_untimeout,
        "ban": cmd_ban,
        "unban": cmd_unban,
        "kick": cmd_kick,
        "warn": cmd_warn,
        "strikes": cmd_strikes,
        "purge": cmd_purge,
        "raid-check": cmd_raid_check,
        "log-join": cmd_log_join,
        "member-info": cmd_member_info,
        "has-role": cmd_has_role,
        "mod-summary": cmd_mod_summary,
    }

    return commands[args.command](args)


if __name__ == "__main__":
    sys.exit(main() or 0)
