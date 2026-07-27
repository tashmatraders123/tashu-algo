"""
support.py
Simple file-based contact-support inbox. Any logged-in user can submit a
message; only the admin can read the full inbox (mirrors the pattern in
users.py -- flat JSON file, one small lock, good enough for a private
tool used by a small trusted group).
"""
import json
import os
import threading
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SUPPORT_PATH = os.path.join(BASE_DIR, "support_messages.json")

_lock = threading.Lock()


def _load():
    if not os.path.exists(SUPPORT_PATH):
        return []
    with open(SUPPORT_PATH, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return []


def _save(messages):
    with open(SUPPORT_PATH, "w", encoding="utf-8") as f:
        json.dump(messages, f, indent=2)


def submit_message(username, message):
    message = (message or "").strip()
    if not message:
        return False, "Message can't be empty"
    with _lock:
        messages = _load()
        next_id = (max((m["id"] for m in messages), default=0)) + 1
        messages.append({
            "id": next_id,
            "username": username.strip().lower(),
            "message": message,
            "submitted_at": int(time.time()),
            "resolved": False,
        })
        _save(messages)
    return True, None


def list_messages_for_user(username):
    username = username.strip().lower()
    return sorted(
        (m for m in _load() if m["username"] == username),
        key=lambda m: m["submitted_at"], reverse=True,
    )


def list_all_messages():
    """Admin-only: every message from every user, newest first."""
    return sorted(_load(), key=lambda m: m["submitted_at"], reverse=True)


def set_resolved(message_id, resolved):
    with _lock:
        messages = _load()
        for m in messages:
            if m["id"] == message_id:
                m["resolved"] = bool(resolved)
                _save(messages)
                return True
    return False
