"""
app.py
Multi-user web dashboard backend. Each person who signs in gets their own
isolated bot process, their own Delta API keys (never visible to anyone
else), and their own settings.

IMPORTANT: this app has no email verification and is meant for a small
group of people you personally trust with the link to this site --
anyone who can reach it can create an account. Do not publicize the URL
widely without adding stronger access control first.
"""
import os
import secrets
import subprocess
import sys
import threading
import time
from functools import wraps

from flask import Flask, jsonify, request, session, redirect, url_for, render_template

import config
import users
from delta_api import DeltaClient, DeltaAPIError

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BOT_SCRIPT = os.path.join(BASE_DIR, "bot.py")
SECRET_KEY_PATH = os.path.join(BASE_DIR, "secret.key")

COMMON_SYMBOLS = [
    "BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD", "DOGEUSD",
    "ADAUSD", "MATICUSD", "LTCUSD", "BNBUSD", "AVAXUSD",
]

app = Flask(
    __name__,
    static_folder=os.path.join(BASE_DIR, "web"),
    static_url_path="/static",
    template_folder=os.path.join(BASE_DIR, "templates"),
)

# Persistent session secret (generated once, reused across restarts so
# people don't get logged out every time the server restarts)
if os.path.exists(SECRET_KEY_PATH):
    with open(SECRET_KEY_PATH, "r") as f:
        app.secret_key = f.read().strip()
else:
    app.secret_key = secrets.token_hex(32)
    with open(SECRET_KEY_PATH, "w") as f:
        f.write(app.secret_key)

# username -> {"process": Popen, "stop_requested_at": float|None}
_bot_processes = {}
_processes_lock = threading.Lock()


def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "username" not in session:
            if request.path.startswith("/api/"):
                return jsonify({"ok": False, "error": "Not logged in"}), 401
            return redirect(url_for("login_page"))
        return f(*args, **kwargs)
    return wrapper


def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "username" not in session:
            return redirect(url_for("login_page"))
        if not users.is_admin(session["username"]):
            return "Admins only.", 403
        return f(*args, **kwargs)
    return wrapper


def user_paths(username):
    d = users.user_dir(username)
    return {
        "log": os.path.join(d, "bot.log"),
        "stop_flag": os.path.join(d, "stop.flag"),
        "log_rel": os.path.relpath(os.path.join(d, "bot.log"), BASE_DIR),
        "stop_flag_rel": os.path.relpath(os.path.join(d, "stop.flag"), BASE_DIR),
    }


# ----------------------------------------------------------------------
# Auth pages
# ----------------------------------------------------------------------
@app.route("/register", methods=["GET", "POST"])
def register_page():
    if request.method == "GET":
        return render_template("register.html", error=None)
    username = request.form.get("username", "")
    password = request.form.get("password", "")
    ok, error = users.create_user(username, password)
    if not ok:
        return render_template("register.html", error=error)
    if users.is_admin(username.strip().lower()):
        # first-ever user, auto-approved as admin
        session["username"] = username.strip().lower()
        return redirect(url_for("dashboard"))
    return render_template(
        "login.html",
        error=None,
        info="Account created! An admin needs to approve it before you can log in.",
    )


@app.route("/login", methods=["GET", "POST"])
def login_page():
    if request.method == "GET":
        return render_template("login.html", error=None)
    username = request.form.get("username", "")
    password = request.form.get("password", "")
    ok, reason = users.verify_login(username, password)
    if ok:
        session["username"] = username.strip().lower()
        return redirect(url_for("dashboard"))
    if reason == "pending":
        return render_template(
            "login.html", error="Your account is waiting for admin approval. Check back soon."
        )
    return render_template("login.html", error="Incorrect username or password")


@app.route("/logout")
def logout():
    session.pop("username", None)
    return redirect(url_for("login_page"))


@app.route("/")
@login_required
def dashboard():
    return render_template(
        "index.html",
        username=session["username"],
        common_symbols=COMMON_SYMBOLS,
        is_admin=users.is_admin(session["username"]),
    )


@app.route("/admin")
@admin_required
def admin_page():
    return render_template(
        "admin.html",
        username=session["username"],
        pending=users.list_pending_users(),
        all_users=users.list_all_users(),
    )


@app.route("/admin/approve", methods=["POST"])
@admin_required
def admin_approve():
    target = request.form.get("username", "")
    users.approve_user(target)
    return redirect(url_for("admin_page"))


@app.route("/admin/reject", methods=["POST"])
@admin_required
def admin_reject():
    target = request.form.get("username", "")
    users.reject_user(target)
    return redirect(url_for("admin_page"))


# ----------------------------------------------------------------------
# API: status
# ----------------------------------------------------------------------
@app.route("/api/status")
@login_required
def api_status():
    username = session["username"]
    with _processes_lock:
        entry = _bot_processes.get(username)
        running = entry is not None and entry["process"].poll() is None
        if entry is not None and not running:
            del _bot_processes[username]

    settings = users.get_settings(username)
    return jsonify({
        "running": running,
        "username": username,
        "symbol": settings["symbol"],
        "fixed_size": settings["fixed_size"],
        "dry_run": settings["dry_run"],
        "use_testnet": settings["use_testnet"],
        "api_key_masked": users.mask_secret(settings["api_key"]),
        "has_api_keys": bool(settings["api_key"] and settings["api_secret"]),
        "common_symbols": COMMON_SYMBOLS,
    })


@app.route("/api/account")
@login_required
def api_account():
    username = session["username"]
    settings = users.get_settings(username)
    if not settings["api_key"] or not settings["api_secret"]:
        return jsonify({"ok": False, "error": "Add your Delta API key/secret in Settings first"})
    try:
        base_url = (
            "https://cdn-ind.testnet.deltaex.org" if settings["use_testnet"]
            else "https://api.india.delta.exchange"
        )
        client = DeltaClient(base_url=base_url, api_key=settings["api_key"], api_secret=settings["api_secret"])
        balance = client.get_available_balance()
        product = client.get_product(settings["symbol"])
        positions = client.get_positions(product["id"])
        if isinstance(positions, dict):
            positions = [positions]
        open_position = None
        for p in positions:
            if float(p.get("size", 0) or 0) != 0:
                open_position = {
                    "size": p.get("size"),
                    "entry_price": p.get("entry_price"),
                    "unrealized_pnl": p.get("unrealized_pnl"),
                }
                break
        return jsonify({"ok": True, "balance": balance, "position": open_position})
    except DeltaAPIError as e:
        return jsonify({"ok": False, "error": str(e)})
    except Exception as e:
        return jsonify({"ok": False, "error": f"Unexpected error: {e}"})


# ----------------------------------------------------------------------
# API: logs (per-user, polling-based tail)
# ----------------------------------------------------------------------
@app.route("/api/logs")
@login_required
def api_logs():
    username = session["username"]
    log_path = user_paths(username)["log"]
    since = request.args.get("since", default=0, type=int)
    if not os.path.exists(log_path):
        return jsonify({"lines": [], "offset": 0})
    size = os.path.getsize(log_path)
    if since > size:
        since = 0
    with open(log_path, "r", encoding="utf-8", errors="replace") as f:
        f.seek(since)
        data = f.read()
        new_offset = f.tell()
    lines = data.splitlines() if data else []
    return jsonify({"lines": lines, "offset": new_offset})


# ----------------------------------------------------------------------
# API: start / stop (per-user isolated subprocess)
# ----------------------------------------------------------------------
@app.route("/api/start", methods=["POST"])
@login_required
def api_start():
    username = session["username"]
    with _processes_lock:
        entry = _bot_processes.get(username)
        if entry is not None and entry["process"].poll() is None:
            return jsonify({"ok": False, "error": "Your bot is already running"}), 400

    payload = request.get_json(force=True, silent=True) or {}
    symbol = str(payload.get("symbol", "BTCUSD")).strip().upper()
    live_trading = bool(payload.get("live_trading", False))
    try:
        fixed_size = int(payload.get("fixed_size", 0))
        if fixed_size < 0:
            raise ValueError
    except (ValueError, TypeError):
        return jsonify({"ok": False, "error": "Lot size must be a whole number, 0 or more"}), 400

    settings = users.get_settings(username)
    if not settings["api_key"] or not settings["api_secret"]:
        return jsonify({"ok": False, "error": "Add your Delta API key/secret in Settings first"}), 400

    users.update_settings(username, {
        "symbol": symbol, "fixed_size": fixed_size, "dry_run": not live_trading,
    })

    paths = user_paths(username)
    if os.path.exists(paths["stop_flag"]):
        os.remove(paths["stop_flag"])

    child_env = os.environ.copy()
    child_env.update({
        "DELTA_API_KEY": settings["api_key"],
        "DELTA_API_SECRET": settings["api_secret"],
        "USE_TESTNET": "true" if settings["use_testnet"] else "false",
        "SYMBOL": symbol,
        "FIXED_SIZE": str(fixed_size),
        "DRY_RUN": "false" if live_trading else "true",
        "LOG_FILE": paths["log_rel"],
        "STOP_FLAG_FILE": paths["stop_flag_rel"],
    })

    creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        process = subprocess.Popen(
            [sys.executable, BOT_SCRIPT], cwd=BASE_DIR, env=child_env, creationflags=creationflags,
        )
    except Exception as e:
        return jsonify({"ok": False, "error": f"Could not start bot: {e}"}), 500

    with _processes_lock:
        _bot_processes[username] = {"process": process, "stop_requested_at": None}

    return jsonify({"ok": True})


@app.route("/api/stop", methods=["POST"])
@login_required
def api_stop():
    username = session["username"]
    with _processes_lock:
        entry = _bot_processes.get(username)
        if entry is None or entry["process"].poll() is not None:
            _bot_processes.pop(username, None)
            return jsonify({"ok": True, "already_stopped": True})
        entry["stop_requested_at"] = time.time()

    paths = user_paths(username)
    with open(paths["stop_flag"], "w") as f:
        f.write("stop")
    return jsonify({"ok": True})


# ----------------------------------------------------------------------
# API: per-user settings (API keys + strategy account prefs)
# ----------------------------------------------------------------------
@app.route("/api/settings", methods=["GET", "POST"])
@login_required
def api_settings():
    username = session["username"]
    if request.method == "GET":
        settings = users.get_settings(username)
        return jsonify({
            "symbol": settings["symbol"],
            "fixed_size": settings["fixed_size"],
            "use_testnet": settings["use_testnet"],
            "api_key_masked": users.mask_secret(settings["api_key"]),
            "has_api_keys": bool(settings["api_key"] and settings["api_secret"]),
        })

    payload = request.get_json(force=True, silent=True) or {}
    updates = {}
    if "api_key" in payload and payload["api_key"].strip():
        updates["api_key"] = payload["api_key"].strip()
    if "api_secret" in payload and payload["api_secret"].strip():
        updates["api_secret"] = payload["api_secret"].strip()
    if "use_testnet" in payload:
        updates["use_testnet"] = bool(payload["use_testnet"])
    users.update_settings(username, updates)
    return jsonify({"ok": True})


def _background_watchdog():
    while True:
        with _processes_lock:
            for username, entry in list(_bot_processes.items()):
                if (entry["stop_requested_at"] is not None
                        and entry["process"].poll() is None
                        and (time.time() - entry["stop_requested_at"]) > 15):
                    entry["process"].terminate()
        time.sleep(2)


if __name__ == "__main__":
    with open(os.path.join(BASE_DIR, "server.pid"), "w") as f:
        f.write(str(os.getpid()))
    threading.Thread(target=_background_watchdog, daemon=True).start()
    app.run(host="0.0.0.0", port=5000, debug=False)
