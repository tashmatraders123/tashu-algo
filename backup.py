"""
backup.py
Free, zero-extra-infrastructure backup for the data Render's ephemeral
filesystem loses on every redeploy/restart/spin-down: users.json,
support_messages.json, and each user's trade_history.json.

Uses GitHub's Contents API to read/write a file in a repo you control.
Requires two environment variables (set in Render's dashboard):

  GITHUB_BACKUP_TOKEN  -- a GitHub Personal Access Token with permission
                          to write to the target repo (a fine-grained
                          token scoped to just that repo is safest).
  GITHUB_BACKUP_REPO   -- "owner/repo", e.g. "tashmatraders123/tashu-algo"
                          (can be the same repo as your code, or a
                          separate private repo just for backups).

Optional:
  GITHUB_BACKUP_BRANCH -- defaults to "main"

If these aren't set, every function here is a harmless no-op (backup()
returns False, restore() returns None) -- the app runs fine without
backup configured, it just won't survive a filesystem wipe.
"""
import base64
import json
import logging
import os
import threading

import requests

log = logging.getLogger("backup")

GITHUB_TOKEN = os.environ.get("GITHUB_BACKUP_TOKEN", "")
GITHUB_REPO = os.environ.get("GITHUB_BACKUP_REPO", "")
GITHUB_BRANCH = os.environ.get("GITHUB_BACKUP_BRANCH", "main")

ENABLED = bool(GITHUB_TOKEN and GITHUB_REPO)

_lock = threading.Lock()
_warned_disabled = False


def _headers():
    return {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
    }


def _warn_disabled_once():
    global _warned_disabled
    if not _warned_disabled:
        log.warning(
            "Backup is not configured (GITHUB_BACKUP_TOKEN / GITHUB_BACKUP_REPO "
            "not set) -- data will NOT survive a Render redeploy or restart."
        )
        _warned_disabled = True


def backup_file(path_in_repo, content_str, commit_message):
    """
    Creates or updates path_in_repo in the configured GitHub repo with
    content_str. Best-effort: never raises -- a backup failure should
    never interrupt trading or break a web request. Returns True on
    success, False otherwise.
    """
    if not ENABLED:
        _warn_disabled_once()
        return False

    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{path_in_repo}"
    with _lock:
        try:
            sha = None
            get_resp = requests.get(
                url, headers=_headers(), params={"ref": GITHUB_BRANCH}, timeout=10
            )
            if get_resp.status_code == 200:
                sha = get_resp.json().get("sha")

            body = {
                "message": commit_message,
                "content": base64.b64encode(content_str.encode("utf-8")).decode("ascii"),
                "branch": GITHUB_BRANCH,
            }
            if sha:
                body["sha"] = sha

            put_resp = requests.put(url, headers=_headers(), json=body, timeout=15)
            if put_resp.status_code not in (200, 201):
                log.warning("Backup PUT failed for %s: %s %s", path_in_repo, put_resp.status_code, put_resp.text[:300])
                return False
            return True
        except requests.RequestException as e:
            log.warning("Backup failed for %s: %s", path_in_repo, e)
            return False


def restore_file(path_in_repo):
    """
    Fetches path_in_repo's current content from the configured GitHub
    repo. Returns the content as a string, or None if not configured,
    not found, or the request fails. Best-effort, never raises.
    """
    if not ENABLED:
        return None

    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{path_in_repo}"
    try:
        resp = requests.get(url, headers=_headers(), params={"ref": GITHUB_BRANCH}, timeout=15)
        if resp.status_code != 200:
            return None
        data = resp.json()
        content_b64 = data.get("content", "")
        return base64.b64decode(content_b64).decode("utf-8")
    except (requests.RequestException, ValueError) as e:
        log.warning("Restore failed for %s: %s", path_in_repo, e)
        return None


def backup_json_file(path_in_repo, local_path, commit_message):
    """Convenience: reads local_path and backs it up if it exists."""
    if not os.path.exists(local_path):
        return False
    try:
        with open(local_path, "r", encoding="utf-8") as f:
            content = f.read()
    except OSError:
        return False
    return backup_file(path_in_repo, content, commit_message)


def restore_json_file(path_in_repo, local_path):
    """
    Convenience: if local_path doesn't already exist (or is empty),
    restores it from the GitHub backup. Returns True if a restore
    actually happened.
    """
    if os.path.exists(local_path) and os.path.getsize(local_path) > 0:
        return False
    content = restore_file(path_in_repo)
    if content is None:
        return False
    try:
        os.makedirs(os.path.dirname(local_path) or ".", exist_ok=True)
        with open(local_path, "w", encoding="utf-8") as f:
            f.write(content)
        log.info("Restored %s from GitHub backup.", local_path)
        return True
    except OSError as e:
        log.warning("Could not write restored file %s: %s", local_path, e)
        return False
