"""
config.py
Central configuration. Everything is loaded from environment variables
(or a local .env file) so you NEVER hardcode API keys in source code.
"""
import os
from dotenv import load_dotenv

load_dotenv()  # reads a .env file in the same folder, if present


def _get_bool(name, default=False):
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "y")


def _get_float(name, default):
    val = os.getenv(name)
    return float(val) if val not in (None, "") else default


def _get_int(name, default):
    val = os.getenv(name)
    return int(val) if val not in (None, "") else default


# ---------------------------------------------------------------------
# Exchange / account
# ---------------------------------------------------------------------
API_KEY = os.getenv("DELTA_API_KEY", "")
API_SECRET = os.getenv("DELTA_API_SECRET", "")

# Flip this to False only once you have tested thoroughly on testnet.
USE_TESTNET = _get_bool("USE_TESTNET", True)

BASE_URL = (
    "https://cdn-ind.testnet.deltaex.org"
    if USE_TESTNET
    else "https://api.india.delta.exchange"
)
# If you are trading on Delta Exchange Global (not India) swap BASE_URL to
# https://api.delta.exchange (live) or the global testnet host.

# Where CANDLES/PRICES are read from. By default this is always the REAL
# exchange, regardless of USE_TESTNET -- so the strategy reacts to real
# market movement instead of testnet's often-thin/stale price feed. Orders
# are still placed against BASE_URL (your testnet/demo account) above.
MARKET_DATA_BASE_URL = os.getenv("MARKET_DATA_BASE_URL", "https://api.india.delta.exchange")

# ---------------------------------------------------------------------
# Instrument / strategy
# ---------------------------------------------------------------------
SYMBOL = os.getenv("SYMBOL", "BTCUSD")        # product symbol on Delta
RESOLUTION = os.getenv("RESOLUTION", "1")     # "1" = 1 minute candles
CANDLE_LOOKBACK = _get_int("CANDLE_LOOKBACK", 200)  # candles pulled each cycle

EMA_FAST = _get_int("EMA_FAST", 5)
EMA_SLOW = _get_int("EMA_SLOW", 13)
RSI_PERIOD = _get_int("RSI_PERIOD", 14)
ATR_PERIOD = _get_int("ATR_PERIOD", 14)

# "trend" = trade continuously while price/EMAs stay aligned with the trend
#           (more frequent signals, follows momentum)
# "crossover" = only trade on a fresh EMA cross (fewer, more selective signals)
STRATEGY_MODE = os.getenv("STRATEGY_MODE", "trend")

RSI_LONG_MAX = _get_float("RSI_LONG_MAX", 80)   # don't long if RSI above this (too extended)
RSI_LONG_MIN = _get_float("RSI_LONG_MIN", 50)   # need at least neutral-or-better momentum
RSI_SHORT_MIN = _get_float("RSI_SHORT_MIN", 20)
RSI_SHORT_MAX = _get_float("RSI_SHORT_MAX", 50)

SL_ATR_MULT = _get_float("SL_ATR_MULT", 1.0)    # stop-loss distance = ATR * this
TP_ATR_MULT = _get_float("TP_ATR_MULT", 1.5)    # take-profit distance = ATR * this

# ---------------------------------------------------------------------
# Risk management
# ---------------------------------------------------------------------
RISK_PER_TRADE_PCT = _get_float("RISK_PER_TRADE_PCT", 1.0)  # % of balance risked per trade
LEVERAGE = _get_int("LEVERAGE", 5)
MAX_DAILY_LOSS_PCT = _get_float("MAX_DAILY_LOSS_PCT", 5.0)  # kill switch
COOLDOWN_SECONDS = _get_int("COOLDOWN_SECONDS", 10)           # min gap between trades

# If set to a number > 0, every trade uses exactly this many contracts
# instead of the risk-based calculation above. Set by the control panel's
# "Lot size" field, or manually in .env. 0 = use risk-based auto sizing.
FIXED_SIZE = _get_int("FIXED_SIZE", 0)

# ---------------------------------------------------------------------
# Runtime
# ---------------------------------------------------------------------
POLL_SECONDS = _get_int("POLL_SECONDS", 2)    # how often to check for a new closed candle
LOG_FILE = os.getenv("LOG_FILE", "bot.log")
STOP_FLAG_FILE = os.getenv("STOP_FLAG_FILE", "stop.flag")  # per-user in multi-user mode
DRY_RUN = _get_bool("DRY_RUN", True)          # True = simulate orders, don't send real ones


def validate():
    missing = []
    if not DRY_RUN:
        if not API_KEY:
            missing.append("DELTA_API_KEY")
        if not API_SECRET:
            missing.append("DELTA_API_SECRET")
    if missing:
        raise SystemExit(
            f"Missing required environment variables: {', '.join(missing)}. "
            f"Set them in a .env file or your shell environment."
        )
