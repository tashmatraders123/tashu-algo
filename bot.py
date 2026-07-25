"""
bot.py
Main entry point. Run with:  python bot.py

Loop:
  1. Every POLL_SECONDS, check whether a new 1-min candle has closed.
  2. When it has, pull the last CANDLE_LOOKBACK candles, compute indicators.
  3. If flat and not in cooldown and kill-switch not triggered: look for a
     fresh EMA cross + RSI filter signal, size the position, fire a
     bracket order (entry + stop-loss + take-profit in one call).
  4. If in a position, just monitor (the exchange manages the SL/TP once
     the bracket order is placed).

Set DRY_RUN=true in your .env to simulate everything without sending real
orders -- highly recommended before going live.
"""
import json
import logging
import os
import signal
import sys
import time

import config
import strategy
from delta_api import DeltaClient, DeltaAPIError
from risk_manager import RiskManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(config.LOG_FILE),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("bot")

_running = True

# The control panel / web dashboard creates this file (in the bot's
# working directory) to request a graceful stop, and removes it again
# once the bot has exited. Using the working directory (not the script's
# own location) means each user's bot instance -- run with its own
# per-user folder as the working directory -- gets its own isolated flag.
STOP_FLAG_PATH = os.path.join(os.getcwd(), config.STOP_FLAG_FILE)

# Written alongside the log file, in the same per-user directory, so the
# web dashboard (a separate process) can read what the bot is currently
# tracking for the open trade -- entry, stop/trailing level, stage, etc.
# Derived from LOG_FILE's directory rather than a new config value, so no
# config.py or app.py env changes are required for this to be per-user.
POSITION_STATE_PATH = os.path.join(
    os.getcwd(), os.path.dirname(config.LOG_FILE) or ".", "position_state.json"
)


def _stop_flag_set():
    return os.path.exists(STOP_FLAG_PATH)


def _clear_stop_flag():
    try:
        os.remove(STOP_FLAG_PATH)
    except FileNotFoundError:
        pass


def _handle_stop(signum, frame):
    global _running
    log.info("Shutdown signal received, finishing current cycle then exiting.")
    _running = False


signal.signal(signal.SIGINT, _handle_stop)
signal.signal(signal.SIGTERM, _handle_stop)


class ScalpingBot:
    def __init__(self):
        config.validate()
        # Reads candles/prices from the REAL exchange (accurate market view)
        self.data_client = DeltaClient(base_url=config.MARKET_DATA_BASE_URL, api_key="", api_secret="")
        # Places orders against your configured account (demo/testnet by default)
        self.trade_client = DeltaClient()
        self.risk = RiskManager()
        self.product = None
        self.product_id = None
        self.contract_value = 1.0
        self.last_candle_time = None
        # Tracks the currently open trade so we can manage its exit across
        # cycles (1:1 checkpoint, volatility check, ATR trailing). None
        # when flat. Reset to None whenever the position is confirmed closed.
        self.open_trade = None

    def setup(self):
        log.info(
            "Starting bot | symbol=%s resolution=%s dry_run=%s testnet=%s",
            config.SYMBOL, config.RESOLUTION, config.DRY_RUN, config.USE_TESTNET,
        )
        log.info("Market data source : %s (real market)", config.MARKET_DATA_BASE_URL)
        log.info("Order execution on : %s (your account)", config.BASE_URL)

        # Product info must come from the TRADE account -- product IDs can
        # differ between the real exchange and testnet even for the same symbol.
        self.product = self.trade_client.get_product(config.SYMBOL)
        self.product_id = self.product["id"]
        self.contract_value = float(self.product.get("contract_value", 1) or 1)
        log.info("Product resolved: id=%s contract_value=%s", self.product_id, self.contract_value)

        if not config.DRY_RUN:
            try:
                self.trade_client.set_leverage(self.product_id, config.LEVERAGE)
                log.info("Leverage set to %sx", config.LEVERAGE)
            except DeltaAPIError as e:
                log.warning("Could not set leverage (continuing anyway): %s", e)

    def get_balance(self):
        if config.DRY_RUN:
            return 1000.0  # pretend balance for simulation/logging purposes
        return self.trade_client.get_available_balance()

    def has_open_position(self):
        if config.DRY_RUN:
            # No real exchange position to check, but we still simulate
            # holding the trade we last "entered" so the 1:1 / trailing
            # management logic can be exercised safely before going live.
            return self.open_trade is not None
        positions = self.trade_client.get_positions(self.product_id)
        if isinstance(positions, dict):
            positions = [positions]
        for p in positions:
            if float(p.get("size", 0)) != 0:
                return True
        return False

    def fetch_df(self):
        end = int(time.time())
        start = end - (config.CANDLE_LOOKBACK * 60)
        candles = self.data_client.get_candles(config.SYMBOL, config.RESOLUTION, start, end)
        return strategy.candles_to_df(candles)

    def _persist_position_state(self):
        """
        Writes the currently tracked trade (or null if flat) to disk so
        the web dashboard -- a separate process -- can display live
        position details (entry, current stop, stage, R-tracking data).
        Best-effort: a write failure here should never interrupt trading.
        """
        try:
            os.makedirs(os.path.dirname(POSITION_STATE_PATH) or ".", exist_ok=True)
            with open(POSITION_STATE_PATH, "w", encoding="utf-8") as f:
                json.dump(self.open_trade, f)
        except OSError as e:
            log.warning("Could not write position state file: %s", e)

    def get_exec_price(self, real_market_price):
        """
        real_market_price decided the DIRECTION and the ATR (volatility)
        used for SL/TP distance. But the actual order/position lives on
        your trade account (demo/testnet), which can sit at a different
        price than the real market -- so anywhere we need the trade
        account's own current price (new entries, or managing an open
        trade), we anchor to it instead of the real market price.
        """
        if config.DRY_RUN:
            return real_market_price
        try:
            trade_ticker = self.trade_client.get_ticker(config.SYMBOL)
            return float(trade_ticker["close"])
        except DeltaAPIError as e:
            log.warning("Could not fetch trade-account price, using real market price instead: %s", e)
            return real_market_price

    def manage_position(self, last_candle):
        """
        Called every cycle while a tracked trade is open. Implements:
          - Close the FULL position once price reaches 1:1 (1R profit),
            UNLESS the market has gotten meaningfully more volatile since
            entry (ATR expanded past VOLATILITY_ATR_EXPANSION_PCT).
          - If volatile at the 1:1 checkpoint, cancel the fixed 1:2 TP and
            switch to an ATR-multiple trailing stop instead, letting the
            trade ride further.
          - While trailing, tighten the stop each cycle as price makes new
            favorable extremes -- never loosen it.
        """
        trade = self.open_trade
        if trade is None:
            log.info("Already in a position, but no tracked state for it "
                      "(likely opened outside this session). Skipping management.")
            return

        real_market_price = float(last_candle["close"])
        current_price = self.get_exec_price(real_market_price)
        current_atr = float(last_candle["atr"])

        # Track the best (most favorable) price seen so far in this trade.
        if trade["direction"] == "long":
            trade["extreme_price"] = max(trade["extreme_price"], current_price)
        else:
            trade["extreme_price"] = min(trade["extreme_price"], current_price)

        r = strategy.r_multiple(
            trade["direction"], trade["entry_price"], current_price, trade["sl_distance"]
        )

        if trade["stage"] == "initial":
            if r >= 1.0:
                expansion_pct = getattr(config, "VOLATILITY_ATR_EXPANSION_PCT", 20.0)
                volatile = strategy.atr_expanded(trade["entry_atr"], current_atr, expansion_pct)

                if not volatile:
                    log.info(
                        "POSITION_CLOSED_BOT: 1:1 reached (R=%.2f), volatility unchanged (entry_atr=%.2f "
                        "current_atr=%.2f) -- closing full position at market.",
                        r, trade["entry_atr"], current_atr,
                    )
                    self._close_full_position(trade, current_price, "bot_1_1")
                    return
                else:
                    trail_mult = getattr(config, "TRAIL_ATR_MULT", 1.5)
                    new_stop = strategy.compute_trailing_stop(
                        trade["direction"], trade["extreme_price"], current_atr, trail_mult
                    )
                    log.info(
                        "1:1 reached (R=%.2f) but ATR expanded %.1f%% since entry -- "
                        "cancelling fixed TP, switching to ATR trailing stop (initial trail stop=%.2f).",
                        r, ((current_atr - trade["entry_atr"]) / trade["entry_atr"]) * 100.0, new_stop,
                    )
                    trade["stage"] = "trailing"
                    trade["current_stop"] = new_stop
                    if not config.DRY_RUN:
                        far_tp = self._far_take_profit(trade)
                        try:
                            self.trade_client.replace_stop_loss(
                                self.product_id, config.SYMBOL, trade["side"], trade["size"],
                                new_stop, far_tp, trade["direction"],
                            )
                        except DeltaAPIError as e:
                            log.error("Failed to switch to trailing stop: %s", e)
                    return
            else:
                log.info("In position, R=%.2f (below 1:1 checkpoint), holding.", r)
                return

        elif trade["stage"] == "trailing":
            trail_mult = getattr(config, "TRAIL_ATR_MULT", 1.5)
            candidate_stop = strategy.compute_trailing_stop(
                trade["direction"], trade["extreme_price"], current_atr, trail_mult
            )
            # Only ever tighten the stop in the favorable direction.
            if trade["direction"] == "long":
                improved = candidate_stop > trade["current_stop"]
            else:
                improved = candidate_stop < trade["current_stop"]

            if improved:
                log.info(
                    "Trailing stop moving %.2f -> %.2f (R=%.2f, atr=%.2f).",
                    trade["current_stop"], candidate_stop, r, current_atr,
                )
                trade["current_stop"] = candidate_stop
                if not config.DRY_RUN:
                    far_tp = self._far_take_profit(trade)
                    try:
                        self.trade_client.replace_stop_loss(
                            self.product_id, config.SYMBOL, trade["side"], trade["size"],
                            candidate_stop, far_tp, trade["direction"],
                        )
                    except DeltaAPIError as e:
                        log.error("Failed to update trailing stop: %s", e)
            else:
                log.info("Trailing (R=%.2f, atr=%.2f), stop holds at %.2f.", r, current_atr, trade["current_stop"])

    def _far_take_profit(self, trade):
        # A deliberately distant TP so it doesn't interfere while the
        # trailing stop is doing the real exit management.
        far_dist = trade["sl_distance"] * 20
        if trade["direction"] == "long":
            return trade["entry_price"] + far_dist
        return trade["entry_price"] - far_dist

    def _record_trade_history(self, trade, exit_price, close_reason):
        """
        Appends a closed trade to trade_history.json (same per-user
        directory as position_state.json) for the dashboard's Recent
        Trades panel and Excel export. Best-effort: never lets a write
        failure interrupt trading. realized_pnl_estimate is exactly that
        -- an estimate from entry/exit price and contract_value, not a
        substitute for Delta's own settled P&L.
        """
        try:
            realized_pnl = None
            if exit_price is not None:
                direction_sign = 1 if trade["direction"] == "long" else -1
                price_diff = (exit_price - trade["entry_price"]) * direction_sign
                realized_pnl = price_diff * abs(trade["size"]) * self.contract_value

            record = {
                "symbol": config.SYMBOL,
                "direction": trade["direction"],
                "entry_price": trade["entry_price"],
                "exit_price": exit_price,
                "size": trade["size"],
                "stage_at_close": trade.get("stage"),
                "close_reason": close_reason,
                "opened_at": trade.get("opened_at"),
                "closed_at": int(time.time()),
                "realized_pnl_estimate": realized_pnl,
            }

            history_dir = os.path.dirname(POSITION_STATE_PATH) or "."
            history_path = os.path.join(history_dir, "trade_history.json")
            history = []
            if os.path.exists(history_path):
                try:
                    with open(history_path, "r", encoding="utf-8") as f:
                        history = json.load(f)
                except (json.JSONDecodeError, OSError):
                    history = []
            history.append(record)
            history = history[-500:]  # cap file size, keep most recent
            with open(history_path, "w", encoding="utf-8") as f:
                json.dump(history, f)
        except OSError as e:
            log.warning("Could not write trade history: %s", e)

    def _close_full_position(self, trade, exit_price, close_reason):
        if not config.DRY_RUN:
            try:
                self.trade_client.cancel_all_orders(self.product_id)
                self.trade_client.close_position(
                    self.product_id, config.SYMBOL, trade["side"], trade["size"]
                )
            except DeltaAPIError as e:
                log.error("Failed to close position at 1:1: %s", e)
                return
        self._record_trade_history(trade, exit_price, close_reason)
        self.open_trade = None
        self._persist_position_state()

    def run_cycle(self):
        df = self.fetch_df()
        if df.empty or len(df) < 5:
            log.warning("Not enough candle data yet, skipping cycle.")
            return

        latest_time = df.index[-1]
        if self.last_candle_time is not None and latest_time == self.last_candle_time:
            return  # no new closed candle yet
        self.last_candle_time = latest_time

        df = strategy.add_indicators(df)
        last = df.iloc[-1]
        log.info(
            "Candle %s | close=%.2f ema_fast=%.2f ema_slow=%.2f rsi=%.1f atr=%.2f",
            latest_time, last["close"], last["ema_fast"], last["ema_slow"],
            last["rsi"], last["atr"],
        )

        balance = self.get_balance()
        if self.risk.daily_kill_switch_triggered(balance):
            log.warning("Kill switch active, no new entries today.")
            return

        # If we were tracking an open trade but the exchange no longer
        # shows one open (SL/TP got hit on its own, or it was closed
        # manually), clear our state so a fresh signal can be taken.
        if self.open_trade is not None and not config.DRY_RUN:
            positions = self.trade_client.get_positions(self.product_id)
            if isinstance(positions, dict):
                positions = [positions]
            still_open = any(float(p.get("size", 0) or 0) != 0 for p in positions)
            if not still_open:
                log.info("POSITION_CLOSED_EXTERNAL: no longer open on exchange (closed via SL/TP fill, or closed manually on Delta). Clearing tracked state.")
                try:
                    exit_price = float(self.trade_client.get_ticker(config.SYMBOL)["close"])
                except DeltaAPIError:
                    exit_price = None
                self._record_trade_history(self.open_trade, exit_price, "external")
                self.open_trade = None
                self._persist_position_state()

        if self.has_open_position():
            self.manage_position(last)
            self._persist_position_state()
            return

        now_ts = int(time.time())
        if not self.risk.cooldown_ok(now_ts):
            log.info("In cooldown, skipping signal check.")
            return

        direction, atr_value = strategy.generate_signal(df)
        if direction is None:
            return

        real_market_price = float(last["close"])

        # IMPORTANT: real_market_price decided the DIRECTION and the ATR
        # (volatility) used for SL/TP distance. But the actual order lands
        # on your trade account (demo/testnet), which can be sitting at a
        # different price than the real market. So we anchor the actual
        # stop-loss/take-profit levels to the trade account's OWN current
        # price -- otherwise a stop/target computed off the real market
        # price could be instantly hit or unreachable on testnet.
        exec_price = self.get_exec_price(real_market_price)

        sl_price, tp_price = strategy.compute_sl_tp(exec_price, direction, atr_value)

        if config.FIXED_SIZE > 0:
            size = config.FIXED_SIZE
            log.info("Using fixed lot size from settings: %s", size)
        else:
            size = self.risk.position_size(
                balance, exec_price, sl_price, contract_value=self.contract_value
            )

        if size <= 0:
            log.info("Signal=%s but computed size=0 (balance/risk too small), skipping.", direction)
            return

        side = "buy" if direction == "long" else "sell"
        log.info(
            "SIGNAL %s | real_market_price=%.2f exec_price(testnet)=%.2f sl=%.2f tp=%.2f size=%s",
            direction.upper(), real_market_price, exec_price, sl_price, tp_price, size,
        )

        if config.DRY_RUN:
            log.info("[DRY_RUN] Would place entry + bracket order now.")
            actual_entry_price = exec_price
            final_sl_price, final_tp_price = sl_price, tp_price
        else:
            try:
                entry_result = self.trade_client.place_market_order(self.product_id, side, size)
                log.info("Entry order: %s", entry_result)
            except DeltaAPIError as e:
                log.error("Entry order placement failed: %s", e)
                return

            position = self.trade_client.wait_for_position(self.product_id)
            if position is None:
                log.warning(
                    "Entry placed but no open position detected in time -- "
                    "skipping bracket attach this cycle. Check Delta manually "
                    "and consider closing/managing this position by hand."
                )
                return

            # Recompute SL/TP off the ACTUAL fill price rather than the
            # pre-trade estimate -- the estimate can drift from where the
            # order actually filled (this is what was causing Delta to
            # reject brackets with 'bracket_order_immediate_execution').
            try:
                actual_entry_price = float(position.get("entry_price", exec_price))
            except (TypeError, ValueError):
                actual_entry_price = exec_price
            final_sl_price, final_tp_price = strategy.compute_sl_tp(actual_entry_price, direction, atr_value)

            try:
                bracket_result = self.trade_client.place_bracket_order_safe(
                    self.product_id, config.SYMBOL, side, size,
                    final_sl_price, final_tp_price, direction,
                )
                log.info("Bracket (SL/TP) attached: %s", bracket_result)
            except DeltaAPIError as e:
                log.error(
                    "Bracket (SL/TP) attach failed even after retries: %s. "
                    "Position is OPEN WITHOUT protective orders -- check Delta manually.", e
                )

        # Track this trade so manage_position() can run the 1:1 / trailing
        # logic on subsequent cycles.
        self.open_trade = {
            "direction": direction,
            "side": side,
            "size": size,
            "entry_price": actual_entry_price,
            "sl_distance": abs(actual_entry_price - final_sl_price),
            "entry_atr": atr_value,
            "extreme_price": actual_entry_price,
            "current_stop": final_sl_price,
            "stage": "initial",
            "opened_at": now_ts,
        }
        self._persist_position_state()

        self.risk.mark_trade_taken(now_ts)

    def run_forever(self):
        self.setup()
        _clear_stop_flag()  # remove any leftover flag from a previous run
        self.open_trade = None
        self._persist_position_state()  # clear any stale state from a previous run
        global _running
        while _running:
            if _stop_flag_set():
                log.info("Stop requested via control panel, exiting.")
                break
            try:
                self.run_cycle()
            except DeltaAPIError as e:
                log.error("API error in cycle: %s", e)
            except Exception:
                log.exception("Unexpected error in cycle")
            time.sleep(config.POLL_SECONDS)
        _clear_stop_flag()
        self.open_trade = None
        self._persist_position_state()
        log.info("Bot stopped cleanly.")


if __name__ == "__main__":
    ScalpingBot().run_forever()
