#!/usr/bin/env python3
"""
Community Role Auto-Assignment Script
Claw Recall Discord — run via cron by Kit

Criteria:
  - Joined 7+ days ago
  - Sent 5+ messages across any channel (excluding bots)

State tracked in: data/community_role_state.json
"""

import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from urllib.request import Request, urlopen
from urllib.error import HTTPError

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPT_DIR)
STATE_FILE = os.path.join(REPO_ROOT, "data", "community_role_state.json")

# Discord config — read from the discord bot's config.json
BOT_CONFIG_PATH = os.path.join(
    os.path.dirname(REPO_ROOT),
    "claw-recall-discord-bot",
    "config.json"
)

DISCORD_API = "https://discord.com/api/v10"
COMMUNITY_ROLE_ID = "1479413213803319400"
QUARANTINE_ROLE_ID = "1480086575906951244"
DAYS_THRESHOLD = 7
MSG_THRESHOLD = 5

# Staff/excluded channels (categories + text channels not worth scanning for member activity)
STAFF_CHANNEL_IDS = {
    "1479413224439939112",  # 🔒 Staff (category)
    "1479414757009391723",  # moderator-only
    "1479321861539238053",  # mod-log
    "1479598145594982410",  # dev-feed
}


def load_config():
    with open(BOT_CONFIG_PATH) as f:
        return json.load(f)


def discord_request(method, path, token, data=None, retries=3):
    url = f"{DISCORD_API}{path}"
    headers = {
        "Authorization": f"Bot {token}",
        "Content-Type": "application/json",
        "User-Agent": "ClawRecallBot-Admin/1.0",
    }
    body = json.dumps(data).encode() if data else None
    for attempt in range(retries):
        req = Request(url, data=body, headers=headers, method=method)
        try:
            with urlopen(req) as resp:
                if resp.status == 204:
                    return {"ok": True}
                return json.loads(resp.read())
        except HTTPError as e:
            if e.code == 429:
                # Rate limited — back off
                retry_after = float(e.headers.get("Retry-After", "1"))
                print(f"  [rate limit] waiting {retry_after}s...")
                time.sleep(retry_after + 0.5)
                continue
            error_body = e.read().decode()
            return {"error": True, "status": e.code, "detail": error_body}
    return {"error": True, "status": 0, "detail": "Max retries exceeded"}


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"assigned": {}, "last_run": None}


def save_state(state):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def get_guild_members(guild_id, token):
    members = []
    after = None
    while True:
        path = f"/guilds/{guild_id}/members?limit=1000"
        if after:
            path += f"&after={after}"
        result = discord_request("GET", path, token)
        if not isinstance(result, list):
            print(f"  [error] failed to fetch members: {result}")
            break
        if not result:
            break
        members.extend(result)
        if len(result) < 1000:
            break
        after = result[-1]["user"]["id"]
    return members


def get_channel_messages(channel_id, token, limit=100):
    """Fetch recent messages from a channel."""
    path = f"/channels/{channel_id}/messages?limit={limit}"
    result = discord_request("GET", path, token)
    if isinstance(result, list):
        return result
    return []


def count_user_messages(guild_id, token, channels):
    """Count messages per user across all eligible channels."""
    counts = {}
    for ch in channels:
        ch_id = ch["id"]
        if ch_id in STAFF_CHANNEL_IDS:
            continue
        if ch.get("type") not in (0, 5, 11, 12):  # text, news, thread, news_thread
            continue

        messages = get_channel_messages(ch_id, token, limit=100)
        time.sleep(0.2)  # be gentle with rate limits

        for msg in messages:
            author = msg.get("author", {})
            if author.get("bot"):
                continue
            uid = author.get("id")
            if uid:
                counts[uid] = counts.get(uid, 0) + 1

    return counts


def main():
    print("=" * 60)
    print("Community Role Auto-Assignment")
    print(f"Run time: {datetime.now(timezone.utc).isoformat()}")
    print("Criteria: joined 7+ days ago AND 5+ messages")
    print("=" * 60)

    cfg = load_config()
    token = cfg["discord_token"]
    guild_id = cfg["guild_id"]
    state = load_state()

    now = datetime.now(timezone.utc)
    join_cutoff = now - timedelta(days=DAYS_THRESHOLD)

    # Fetch members
    print("\n[1] Fetching guild members...")
    members = get_guild_members(guild_id, token)
    human_members = [m for m in members if not m.get("user", {}).get("bot")]
    print(f"  Total members: {len(members)} | Human members: {len(human_members)}")

    # Fetch channels
    print("\n[2] Fetching channels...")
    channels = discord_request("GET", f"/guilds/{guild_id}/channels", token)
    if not isinstance(channels, list):
        print(f"  [error] could not fetch channels: {channels}")
        sys.exit(1)
    text_channels = [c for c in channels if c.get("type") in (0, 5) and c["id"] not in STAFF_CHANNEL_IDS]
    print(f"  Eligible text channels: {len(text_channels)}")

    # Count messages per user
    print("\n[3] Counting messages per user across channels...")
    msg_counts = count_user_messages(guild_id, token, text_channels)
    print(f"  Users with messages: {len(msg_counts)}")

    # Evaluate each member
    print("\n[4] Evaluating members...")
    newly_assigned = []
    already_has_role = []
    not_qualified = []

    for m in human_members:
        user = m["user"]
        uid = user["id"]
        username = user.get("username", "unknown")
        roles = m.get("roles", [])
        joined_at = m.get("joined_at", "")

        # Skip quarantined members
        if QUARANTINE_ROLE_ID in roles:
            print(f"  SKIP (quarantined): {username}")
            continue

        # Already has Community role?
        if COMMUNITY_ROLE_ID in roles:
            already_has_role.append(username)
            print(f"  SKIP (already Community): {username}")
            continue

        # Check join date
        joined_dt = datetime.fromisoformat(joined_at.replace("Z", "+00:00")) if joined_at else None
        days_in_server = (now - joined_dt).days if joined_dt else 0
        meets_join = joined_dt is not None and joined_dt <= join_cutoff

        # Check message count
        user_msgs = msg_counts.get(uid, 0)
        meets_msgs = user_msgs >= MSG_THRESHOLD

        status_parts = [
            f"joined={joined_at[:10] if joined_at else 'unknown'} ({days_in_server}d)",
            f"msgs={user_msgs}",
            f"join_ok={meets_join}",
            f"msg_ok={meets_msgs}",
        ]
        print(f"  {username}: {' | '.join(status_parts)}")

        if meets_join and meets_msgs:
            # Assign Community role
            print(f"  --> Assigning Community role to {username}...")
            result = discord_request(
                "PUT",
                f"/guilds/{guild_id}/members/{uid}/roles/{COMMUNITY_ROLE_ID}",
                token
            )
            if result.get("ok") or result.get("error") is None:
                newly_assigned.append(username)
                state["assigned"][uid] = {
                    "username": username,
                    "assigned_at": now.isoformat(),
                    "days_in_server": days_in_server,
                    "message_count": user_msgs,
                }
                print(f"  --> SUCCESS: {username} now has Community role")
            else:
                print(f"  --> FAILED to assign role: {result}")
        else:
            not_qualified.append({
                "username": username,
                "days": days_in_server,
                "msgs": user_msgs,
                "join_ok": meets_join,
                "msg_ok": meets_msgs,
            })

    # Update state
    state["last_run"] = now.isoformat()
    save_state(state)

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"Members evaluated:      {len(human_members)}")
    print(f"Already had Community:  {len(already_has_role)}")
    print(f"Newly assigned:         {len(newly_assigned)}")
    print(f"Not yet qualified:      {len(not_qualified)}")

    if newly_assigned:
        print("\n✅ Newly assigned Community role:")
        for name in newly_assigned:
            print(f"   - {name}")
    else:
        print("\nNo new Community role assignments this run.")

    if not_qualified:
        print("\nNot yet qualified:")
        for nq in not_qualified:
            reasons = []
            if not nq["join_ok"]:
                reasons.append(f"only {nq['days']}d in server (need 7)")
            if not nq["msg_ok"]:
                reasons.append(f"only {nq['msgs']} messages (need 5)")
            print(f"   - {nq['username']}: {'; '.join(reasons)}")

    print(f"\nState saved to: {STATE_FILE}")
    print("Done.")


if __name__ == "__main__":
    main()
