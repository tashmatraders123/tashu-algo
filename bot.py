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
                        "1:1 reached (R=%.2f) and volatility unchanged (entry_atr=%.2f "
                        "current_atr=%.2f) -- closing full position at market.",
                        r, trade["entry_atr"], current_atr,
                    )
                    self._close_full_position(trade)
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
                                new_stop, far_tp,
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
                            candidate_stop, far_tp,
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

    def _close_full_position(self, trade):
        if not config.DRY_RUN:
            try:
                self.trade_client.cancel_all_orders(self.product_id)
                self.trade_client.close_position(
                    self.product_id, config.SYMBOL, trade["side"], trade["size"]
                )
            except DeltaAPIError as e:
                log.error("Failed to close position at 1:1: %s", e)
                return
        self.open_trade = None

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
                log.info("Tracked position no longer open on exchange (SL/TP likely hit). Clearing state.")
                self.open_trade = None

        if self.has_open_position():
            self.manage_position(last)
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
        sl_distance = abs(exec_price - sl_price)

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
        else:
            try:
                result = self.trade_client.enter_with_bracket(
                    self.product_id, config.SYMBOL, side, size, sl_price, tp_price,
                )
                log.info("Entry order: %s", result["entry"])
                if result["bracket"] is not None:
                    log.info("Bracket (SL/TP) attached: %s", result["bracket"])
                else:
                    log.warning(
                        "Entry placed but bracket NOT attached (position not "
                        "detected in time). Check Delta manually and consider "
                        "closing/managing this position by hand."
                    )
            except DeltaAPIError as e:
                log.error("Order placement failed: %s", e)
                return

        # Track this trade so manage_position() can run the 1:1 / trailing
        # logic on subsequent cycles.
        self.open_trade = {
            "direction": direction,
            "side": side,
            "size": size,
            "entry_price": exec_price,
            "sl_distance": sl_distance,
            "entry_atr": atr_value,
            "extreme_price": exec_price,
            "current_stop": sl_price,
            "stage": "initial",
        }

        self.risk.mark_trade_taken(now_ts)

    def run_forever(self):
        self.setup()
        _clear_stop_flag()  # remove any leftover flag from a previous run
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
        log.info("Bot stopped cleanly.")


if __name__ == "__main__":
    ScalpingBot().run_forever()
