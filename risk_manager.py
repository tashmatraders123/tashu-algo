"""
risk_manager.py
Position sizing and a daily-loss kill switch. Keeping this separate from
strategy.py means you can change how much you bet without touching the
signal logic at all.
"""
import logging
from datetime import datetime, timezone

import config

log = logging.getLogger("risk_manager")


class RiskManager:
    def __init__(self):
        self.day = datetime.now(timezone.utc).date()
        self.start_of_day_balance = None
        self.realized_pnl_today = 0.0
        self.last_trade_time = 0

    def _roll_day_if_needed(self, current_balance):
        today = datetime.now(timezone.utc).date()
        if today != self.day:
            self.day = today
            self.start_of_day_balance = current_balance
            self.realized_pnl_today = 0.0
            log.info("New trading day, resetting daily PnL tracker.")

    def register_fill_pnl(self, pnl):
        self.realized_pnl_today += pnl

    def cooldown_ok(self, now_ts):
        return (now_ts - self.last_trade_time) >= config.COOLDOWN_SECONDS

    def mark_trade_taken(self, now_ts):
        self.last_trade_time = now_ts

    def daily_kill_switch_triggered(self, current_balance):
        if self.start_of_day_balance is None:
            self.start_of_day_balance = current_balance
        self._roll_day_if_needed(current_balance)

        if self.start_of_day_balance <= 0:
            return False
        loss_pct = (self.start_of_day_balance - current_balance) / self.start_of_day_balance * 100
        if loss_pct >= config.MAX_DAILY_LOSS_PCT:
            log.error(
                "Daily loss limit hit: %.2f%% >= %.2f%%. Halting new entries.",
                loss_pct, config.MAX_DAILY_LOSS_PCT,
            )
            return True
        return False

    def position_size(self, balance, entry_price, stop_price, contract_value=1.0, min_size=1):
        """
        Risk-based sizing: risk RISK_PER_TRADE_PCT of balance on the distance
        between entry and stop-loss. contract_value = underlying units per
        contract for the product (check via get_product()['contract_value']).

        Returns an integer number of contracts (Delta order sizes are in
        contracts, not underlying units).
        """
        risk_amount = balance * (config.RISK_PER_TRADE_PCT / 100)
        stop_distance = abs(entry_price - stop_price)
        if stop_distance <= 0:
            return 0

        # Underlying units we can afford to risk that much on:
        units = risk_amount / stop_distance
        contracts = int(units / contract_value)

        # Respect leverage / margin ceiling roughly: notional shouldn't
        # exceed balance * leverage.
        max_notional = balance * config.LEVERAGE
        max_contracts_by_margin = int(max_notional / (entry_price * contract_value))
        contracts = min(contracts, max_contracts_by_margin)

        return max(contracts, 0) if contracts >= min_size else 0
