"""
users.py
Simple file-based multi-user store. Each friend gets their own account:
their own password (hashed, never stored in plain text), their own Delta
API key/secret, and their own trade settings -- fully isolated from
everyone else's.

SECURITY NOTE: API keys/secrets are stored in users.json on this server's
disk, encrypted only in the sense that the file isn't served publicly by
Flask. This is appropriate for a small private tool shared among people
you trust who are running it on a machine/server you control. It is NOT
a substitute for a proper secrets vault if you ever expose this beyond a
small trusted group.
"""
import json
import os
import threading

from werkzeug.security import generate_password_hash, check_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
USERS_PATH = os.path.join(BASE_DIR, "users.json")
USER_DATA_DIR = os.path.join(BASE_DIR, "user_data")

_lock = threading.Lock()

DEFAULT_SETTINGS = {
    "api_key": "",
    "api_secret": "",
    "use_testnet": True,
    "symbol": "BTCUSD",
    "fixed_size": 0,
    "dry_run": True,
}


def _load():
    if not os.path.exists(USERS_PATH):
        return {}
    with open(USERS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _save(users):
    with open(USERS_PATH, "w", encoding="utf-8") as f:
        json.dump(users, f, indent=2)


def user_exists(username):
    return username in _load()


def create_user(username, password):
    username = username.strip().lower()
    if not username or not password:
        return False, "Username and password are required"
    if len(password) < 6:
        return False, "Password must be at least 6 characters"
    with _lock:
        users = _load()
        if username in users:
            return False, "That username is already taken"
        is_first_user = len(users) == 0
        users[username] = {
            "password_hash": generate_password_hash(password),
            "settings": dict(DEFAULT_SETTINGS),
            # The very first account ever created becomes the admin and is
            # auto-approved. Everyone after that needs the admin to approve
            # them before they can log in.
            "is_admin": is_first_user,
            "approved": is_first_user,
            # Separate from "approved": an approved account can later be
            # suspended (access revoked) and unsuspended (access restored)
            # without deleting it or losing its settings/API keys.
            "suspended": False,
        }
        _save(users)
    os.makedirs(os.path.join(USER_DATA_DIR, username), exist_ok=True)
    return True, None


def verify_login(username, password):
    """Returns (ok, reason). reason is None on success, or a short string
    explaining why login was refused ('bad_credentials' / 'pending' /
    'suspended')."""
    users = _load()
    username = username.strip().lower()
    user = users.get(username)
    if not user or not check_password_hash(user["password_hash"], password):
        return False, "bad_credentials"
    if not user.get("approved", False):
        return False, "pending"
    if user.get("suspended", False):
        return False, "suspended"
    return True, None


def is_admin(username):
    users = _load()
    user = users.get(username.strip().lower())
    return bool(user and user.get("is_admin", False))


def list_pending_users():
    users = _load()
    return sorted([u for u, data in users.items() if not data.get("approved", False)])


def list_all_users():
    users = _load()
    return sorted(users.items())


def approve_user(username):
    username = username.strip().lower()
    with _lock:
        users = _load()
        if username not in users:
            return False
        users[username]["approved"] = True
        _save(users)
    return True


def is_suspended(username):
    users = _load()
    user = users.get(username.strip().lower())
    return bool(user and user.get("suspended", False))


def suspend_user(username):
    """
    Revokes an already-approved user's access without deleting their
    account, settings, or API keys -- they can be unsuspended later.
    Refuses to suspend an admin account.
    """
    username = username.strip().lower()
    with _lock:
        users = _load()
        if username not in users:
            return False
        if users[username].get("is_admin", False):
            return False
        users[username]["suspended"] = True
        _save(users)
    return True


def unsuspend_user(username):
    username = username.strip().lower()
    with _lock:
        users = _load()
        if username not in users:
            return False
        users[username]["suspended"] = False
        _save(users)
    return True


def reject_user(username):
    """Rejects (deletes) a pending account. Does not touch approved users."""
    username = username.strip().lower()
    with _lock:
        users = _load()
        if username not in users or users[username].get("approved", False):
            return False
        del users[username]
        _save(users)
    return True


def get_settings(username):
    users = _load()
    user = users.get(username)
    if not user:
        return dict(DEFAULT_SETTINGS)
    settings = dict(DEFAULT_SETTINGS)
    settings.update(user.get("settings", {}))
    return settings


def update_settings(username, updates):
    with _lock:
        users = _load()
        if username not in users:
            return False
        settings = dict(DEFAULT_SETTINGS)
        settings.update(users[username].get("settings", {}))
        settings.update(updates)
        users[username]["settings"] = settings
        _save(users)
    return True


def user_dir(username):
    d = os.path.join(USER_DATA_DIR, username)
    os.makedirs(d, exist_ok=True)
    return d


def mask_secret(value):
    if not value or len(value) < 8:
        return "not set" if not value else "****"
    return value[:4] + "..." + value[-4:]
