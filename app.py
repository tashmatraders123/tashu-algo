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
    static_folder=os.path.join(BASE_DIR, "static"),
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

# Tiny shared cache for the public live-price ticker tape, so ten browser
# tabs polling every few seconds don't turn into ten upstream requests.
_ticker_cache = {"at": 0.0, "data": []}
_ticker_lock = threading.Lock()
TICKER_CACHE_SECONDS = 3
TICKER_SYMBOLS = COMMON_SYMBOLS


def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "username" not in session:
            if request.path.startswith("/api/"):
                return jsonify({"ok": False, "error": "Not logged in"}), 401
            return redirect(url_for("login_page"))
        if users.is_suspended(session["username"]):
            # Access was revoked after this session started -- log them
            # out now rather than letting an already-open tab keep working.
            session.pop("username", None)
            if request.path.startswith("/api/"):
                return jsonify({"ok": False, "error": "Your access has been suspended"}), 403
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
        return render_template("register.html", error=None, form={})
    username = request.form.get("username", "").strip()
    email = request.form.get("email", "").strip()
    password = request.form.get("password", "")
    confirm_password = request.form.get("confirm_password", "")
    form = {"username": username, "email": email}

    if password != confirm_password:
        return render_template("register.html", error="Passwords do not match", form=form)

    ok, error = users.create_user(username, password, email=email)
    if not ok:
        return render_template("register.html", error=error, form=form)
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
    identifier = request.form.get("identifier", request.form.get("username", "")).strip()
    password = request.form.get("password", "")

    # Let people log in with either their username or their email.
    username = users.get_username_by_email(identifier) if "@" in identifier else identifier
    username = username or identifier

    ok, reason = users.verify_login(username, password)
    if ok:
        session["username"] = username.strip().lower()
        return redirect(url_for("dashboard"))
    if reason == "pending":
        return render_template(
            "login.html", error="Your account is waiting for admin approval. Check back soon."
        )
    if reason == "suspended":
        return render_template(
            "login.html", error="Your access has been suspended. Contact the admin for details."
        )
    return render_template("login.html", error="Incorrect username/email or password")


@app.route("/logout")
def logout():
    session.pop("username", None)
    return redirect(url_for("login_page"))


@app.route("/")
@login_required
def dashboard():
    username = session["username"]
    settings = users.get_settings(username)
    return render_template(
        "index.html",
        username=username,
        email=users.get_email(username),
        common_symbols=COMMON_SYMBOLS,
        is_admin=users.is_admin(username),
        has_api_keys=bool(settings["api_key"] and settings["api_secret"]),
        api_key_masked=users.mask_secret(settings["api_key"]),
    )


@app.route("/contact")
def contact_page():
    return render_template("contact.html", logged_in=("username" in session))


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


@app.route("/admin/suspend", methods=["POST"])
@admin_required
def admin_suspend():
    target = request.form.get("username", "").strip().lower()
    users.suspend_user(target)
    # If their bot is currently running, stop it immediately rather than
    # waiting for them to notice they've lost access.
    with _processes_lock:
        entry = _bot_processes.get(target)
        if entry is not None and entry["process"].poll() is None:
            entry["stop_requested_at"] = time.time()
    paths = user_paths(target)
    try:
        with open(paths["stop_flag"], "w") as f:
            f.write("stop")
    except OSError:
        pass
    return redirect(url_for("admin_page"))


@app.route("/admin/unsuspend", methods=["POST"])
@admin_required
def admin_unsuspend():
    target = request.form.get("username", "")
    users.unsuspend_user(target)
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


@app.route("/api/algo")
@login_required
def api_algo():
    """Read-only summary of the strategy/risk config, for the dashboard's
    'Algorithm' card. This is the same config every user's bot process
    reads from .env / config.py -- it's shared, not per-user."""
    return jsonify({
        "ok": True,
        "mode": config.STRATEGY_MODE,
        "resolution": config.RESOLUTION,
        "ema_fast": config.EMA_FAST,
        "ema_slow": config.EMA_SLOW,
        "rsi_period": config.RSI_PERIOD,
        "rsi_long_range": [config.RSI_LONG_MIN, config.RSI_LONG_MAX],
        "rsi_short_range": [config.RSI_SHORT_MIN, config.RSI_SHORT_MAX],
        "atr_period": config.ATR_PERIOD,
        "sl_atr_mult": config.SL_ATR_MULT,
        "tp_rr_mult": getattr(config, "TP_RR_MULT", 2.0),
        "risk_per_trade_pct": config.RISK_PER_TRADE_PCT,
        "leverage": config.LEVERAGE,
        "max_daily_loss_pct": config.MAX_DAILY_LOSS_PCT,
        "cooldown_seconds": config.COOLDOWN_SECONDS,
    })


@app.route("/api/ticker")
@login_required
def api_ticker():
    """Public, no-API-key-required live prices for the scrolling ticker
    tape. Cached for a few seconds and shared across everyone logged in,
    so it stays live without hammering the exchange."""
    now = time.time()
    with _ticker_lock:
        if now - _ticker_cache["at"] < TICKER_CACHE_SECONDS and _ticker_cache["data"]:
            return jsonify({"ok": True, "tickers": _ticker_cache["data"]})

    try:
        client = DeltaClient(base_url=config.MARKET_DATA_BASE_URL, api_key="", api_secret="")
        raw = client.get_tickers_bulk(TICKER_SYMBOLS)
        tickers = []
        for symbol in TICKER_SYMBOLS:
            t = raw.get(symbol)
            if not t:
                continue
            try:
                close = float(t.get("close", 0) or 0)
                change_pct = t.get("mark_change_24h")
                change_pct = float(change_pct) if change_pct not in (None, "") else None
            except (TypeError, ValueError):
                close, change_pct = 0.0, None
            tickers.append({"symbol": symbol, "price": close, "change_pct": change_pct})
        with _ticker_lock:
            _ticker_cache["at"] = now
            _ticker_cache["data"] = tickers
        return jsonify({"ok": True, "tickers": tickers})
    except DeltaAPIError as e:
        # Serve the last known prices rather than a blank ticker tape.
        if _ticker_cache["data"]:
            return jsonify({"ok": True, "tickers": _ticker_cache["data"], "stale": True})
        return jsonify({"ok": False, "error": str(e), "tickers": []})
    except Exception as e:
        if _ticker_cache["data"]:
            return jsonify({"ok": True, "tickers": _ticker_cache["data"], "stale": True})
        return jsonify({"ok": False, "error": f"Unexpected error: {e}", "tickers": []})


@app.route("/api/account")
@login_required
def api_account():
    """Live 'Position & Trade Details' data: balance, the open position
    (if any) with its real entry/mark/SL/TP/PnL, and whether the bot is
    currently running -- everything the dashboard needs to show a clean,
    single, structured trade panel instead of a raw log."""
    username = session["username"]
    settings = users.get_settings(username)
    with _processes_lock:
        entry = _bot_processes.get(username)
        running = entry is not None and entry["process"].poll() is None

    if not settings["api_key"] or not settings["api_secret"]:
        return jsonify({"ok": False, "error": "Add your Delta API key/secret in Settings first", "running": running})

    try:
        base_url = (
            "https://cdn-ind.testnet.deltaex.org" if settings["use_testnet"]
            else "https://api.india.delta.exchange"
        )
        client = DeltaClient(base_url=base_url, api_key=settings["api_key"], api_secret=settings["api_secret"])
        balance = client.get_available_balance()
        product = client.get_product(settings["symbol"])
        product_id = product["id"]

        positions = client.get_positions(product_id)
        if isinstance(positions, dict):
            positions = [positions]

        open_position = None
        for p in positions:
            size = float(p.get("size", 0) or 0)
            if size != 0:
                direction = "long" if size > 0 else "short"
                entry_price = float(p.get("entry_price", 0) or 0)
                unrealized_pnl = float(p.get("unrealized_pnl", 0) or 0)

                # Current mark/last price, for a live PnL% and mark line.
                try:
                    mark_price = float(client.get_ticker(settings["symbol"]).get("close", entry_price))
                except DeltaAPIError:
                    mark_price = entry_price

                # Pull the real attached SL/TP off the open orders, rather
                # than re-deriving them -- this is what's actually
                # protecting the position on the exchange right now.
                stop_loss, take_profit = None, None
                try:
                    for o in client.get_open_orders(product_id):
                        stop_type = o.get("stop_order_type")
                        price = o.get("stop_price")
                        if stop_type == "stop_loss_order" and price is not None:
                            stop_loss = float(price)
                        elif stop_type == "take_profit_order" and price is not None:
                            take_profit = float(price)
                except DeltaAPIError:
                    pass  # SL/TP just won't be shown this cycle -- not fatal

                notional = abs(size) * entry_price
                pnl_pct = (unrealized_pnl / notional * 100) if notional else 0.0

                open_position = {
                    "symbol": settings["symbol"],
                    "direction": direction,
                    "size": abs(size),
                    "entry_price": entry_price,
                    "mark_price": mark_price,
                    "stop_loss": stop_loss,
                    "take_profit": take_profit,
                    "unrealized_pnl": unrealized_pnl,
                    "unrealized_pnl_pct": pnl_pct,
                    "leverage": p.get("leverage") or config.LEVERAGE,
                }
                break

        return jsonify({
            "ok": True,
            "running": running,
            "balance": balance,
            "symbol": settings["symbol"],
            "dry_run": settings["dry_run"],
            "position": open_position,
        })
    except DeltaAPIError as e:
        return jsonify({"ok": False, "error": str(e), "running": running})
    except Exception as e:
        return jsonify({"ok": False, "error": f"Unexpected error: {e}", "running": running})


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
            "email": users.get_email(username),
            "api_key_masked": users.mask_secret(settings["api_key"]),
            "has_api_keys": bool(settings["api_key"] and settings["api_secret"]),
        })

    payload = request.get_json(force=True, silent=True) or {}
    updates = {}
    new_key = str(payload.get("api_key") or "").strip()
    new_secret = str(payload.get("api_secret") or "").strip()
    if new_key:
        updates["api_key"] = new_key
    if new_secret:
        updates["api_secret"] = new_secret
    if "use_testnet" in payload:
        updates["use_testnet"] = bool(payload["use_testnet"])

    if not updates:
        return jsonify({"ok": False, "error": "Nothing to update"}), 400

    users.update_settings(username, updates)
    settings = users.get_settings(username)
    return jsonify({
        "ok": True,
        "api_key_masked": users.mask_secret(settings["api_key"]),
        "has_api_keys": bool(settings["api_key"] and settings["api_secret"]),
    })


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
