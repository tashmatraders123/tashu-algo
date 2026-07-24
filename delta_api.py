"""
delta_api.py
Thin wrapper around the Delta Exchange REST API (v2).

Docs: https://docs.delta.exchange/
Auth: HMAC-SHA256 signature over  method + timestamp + path + query + body
Headers required on private endpoints: api-key, signature, timestamp
"""
import hashlib
import hmac
import json
import time
import logging

import requests

import config

log = logging.getLogger("delta_api")


class DeltaAPIError(Exception):
    pass


class DeltaClient:
    def __init__(self, base_url=None, api_key=None, api_secret=None, timeout=5):
        self.base_url = (base_url or config.BASE_URL).rstrip("/")
        self.api_key = api_key or config.API_KEY
        self.api_secret = api_secret or config.API_SECRET
        self.timeout = timeout
        self.session = requests.Session()

    # ------------------------------------------------------------------
    # Low level signed request
    # ------------------------------------------------------------------
    def _sign(self, method, path, query_string, body_str, timestamp):
        message = method + timestamp + path + query_string + body_str
        return hmac.new(
            self.api_secret.encode("utf-8"),
            message.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _request(self, method, path, params=None, body=None, auth=True, retries=2):
        params = params or {}
        query_string = ""
        if params:
            # Delta expects the query string (without leading '?') included
            # exactly as sent, sorted keys avoid ambiguity.
            query_string = "?" + "&".join(
                f"{k}={v}" for k, v in sorted(params.items())
            )

        body_str = json.dumps(body, separators=(",", ":")) if body else ""
        url = self.base_url + path

        headers = {"Accept": "application/json"}
        if body:
            headers["Content-Type"] = "application/json"

        last_err = None
        for attempt in range(1, retries + 1):
            timestamp = str(int(time.time()))
            if auth:
                signature = self._sign(method, path, query_string, body_str, timestamp)
                headers.update(
                    {
                        "api-key": self.api_key,
                        "timestamp": timestamp,
                        "signature": signature,
                    }
                )
            try:
                resp = self.session.request(
                    method,
                    url,
                    params=params if params else None,
                    data=body_str if body else None,
                    headers=headers,
                    timeout=self.timeout,
                )
                if resp.status_code == 429:
                    wait = min(2 ** attempt, 3)
                    log.warning("Rate limited, backing off %ss", wait)
                    time.sleep(wait)
                    continue
                data = resp.json()
                if resp.status_code >= 400 or data.get("success") is False:
                    raise DeltaAPIError(f"{resp.status_code}: {data}")
                return data
            except (requests.RequestException, ValueError) as e:
                last_err = e
                log.warning("Request attempt %s/%s failed: %s", attempt, retries, e)
                time.sleep(min(2 ** attempt, 3))
        raise DeltaAPIError(f"Request failed after {retries} attempts: {last_err}")

    # ------------------------------------------------------------------
    # Public market data
    # ------------------------------------------------------------------
    def get_product(self, symbol):
        data = self._request("GET", f"/v2/products/{symbol}", auth=False)
        return data["result"]

    def get_ticker(self, symbol):
        data = self._request("GET", "/v2/tickers", auth=False)
        for t in data.get("result", []):
            if t.get("symbol") == symbol:
                return t
        raise DeltaAPIError(f"Symbol {symbol} not found in tickers")

    def get_candles(self, symbol, resolution, start, end):
        """
        resolution: "1", "3", "5", "15", "30", "60", "1D" etc (Delta uses these codes;
        "1" = 1 minute).
        start/end: unix timestamps in seconds.
        Returns list of dicts: time, open, high, low, close, volume (oldest first).
        """
        params = {
            "symbol": symbol,
            "resolution": resolution,
            "start": str(start),
            "end": str(end),
        }
        data = self._request("GET", "/v2/history/candles", params=params, auth=False)
        candles = data.get("result", [])
        candles.sort(key=lambda c: c["time"])
        return candles

    # ------------------------------------------------------------------
    # Account
    # ------------------------------------------------------------------
    def get_wallet_balances(self):
        data = self._request("GET", "/v2/wallet/balances", auth=True)
        return data["result"]

    def get_available_balance(self, asset_symbol="USDT"):
        balances = self.get_wallet_balances()
        for b in balances:
            if b.get("asset_symbol") == asset_symbol:
                return float(b.get("available_balance", 0))
        return 0.0

    def get_positions(self, product_id=None):
        params = {"product_id": product_id} if product_id else {}
        data = self._request("GET", "/v2/positions/margined", params=params, auth=True)
        return data["result"]

    def set_leverage(self, product_id, leverage):
        body = {"leverage": str(leverage)}
        return self._request(
            "POST", f"/v2/products/{product_id}/orders/leverage", body=body, auth=True
        )

    # ------------------------------------------------------------------
    # Orders
    # ------------------------------------------------------------------
    def place_market_order(self, product_id, side, size, reduce_only=False):
        body = {
            "product_id": product_id,
            "size": size,
            "side": side,               # "buy" or "sell"
            "order_type": "market_order",
            "reduce_only": reduce_only,
        }
        return self._request("POST", "/v2/orders", body=body, auth=True)

    def place_bracket_order(
        self,
        product_id,
        product_symbol,
        side,
        size,
        stop_loss_price,
        take_profit_price,
        trigger_method="last_traded_price",
    ):
        """
        IMPORTANT: Delta's /v2/orders/bracket endpoint only ATTACHES a
        stop-loss/take-profit to a position that already exists -- it does
        NOT open a new position itself. Calling it with no open position
        returns a 'no_open_position' error.

        This method therefore does two steps:
          1. Place a market entry order (opens/adds to the position)
          2. Attach the bracket (stop-loss + take-profit) to that position

        For simplicity, prefer calling enter_with_bracket() instead, which
        also waits briefly for the position to register before step 2.
        """
        body = {
            "product_id": product_id,
            "product_symbol": product_symbol,
            "stop_loss_order": {
                "order_type": "market_order",
                "stop_price": str(stop_loss_price),
            },
            "take_profit_order": {
                "order_type": "market_order",
                "stop_price": str(take_profit_price),
            },
            "bracket_stop_trigger_method": trigger_method,
        }
        return self._request("POST", "/v2/orders/bracket", body=body, auth=True)

    def enter_with_bracket(
        self,
        product_id,
        product_symbol,
        side,
        size,
        stop_loss_price,
        take_profit_price,
        trigger_method="last_traded_price",
        position_check_retries=5,
        position_check_delay=1.0,
    ):
        """
        Full flow: place the market entry order, wait for the position to
        actually appear, then attach the stop-loss/take-profit bracket to it.
        Returns a dict: {"entry": <entry order response>, "bracket": <bracket response or None>}
        """
        entry_result = self.place_market_order(product_id, side, size)

        position_found = False
        for _ in range(position_check_retries):
            time.sleep(position_check_delay)
            positions = self.get_positions(product_id)
            if isinstance(positions, dict):
                positions = [positions]
            for p in positions:
                if float(p.get("size", 0) or 0) != 0:
                    position_found = True
                    break
            if position_found:
                break

        if not position_found:
            log.warning(
                "Entry order placed but no open position detected yet; "
                "skipping bracket attach this cycle. Check Delta manually."
            )
            return {"entry": entry_result, "bracket": None}

        bracket_result = self.place_bracket_order(
            product_id, product_symbol, side, size,
            stop_loss_price, take_profit_price, trigger_method,
        )
        return {"entry": entry_result, "bracket": bracket_result}

    def cancel_all_orders(self, product_id):
        body = {"product_id": product_id}
        return self._request("DELETE", "/v2/orders/all", body=body, auth=True)

    def close_position(self, product_id, symbol, side, size):
        """Market order in the opposite direction, reduce_only=True."""
        close_side = "sell" if side == "buy" else "buy"
        return self.place_market_order(product_id, close_side, size, reduce_only=True)

    def replace_stop_loss(
        self,
        product_id,
        product_symbol,
        side,
        size,
        new_stop_price,
        far_take_profit_price,
        trigger_method="last_traded_price",
    ):
        """
        Used for trailing-stop updates on an already-open position: cancels
        the existing bracket (old fixed SL/TP) and places a fresh one with
        an updated stop-loss. Delta's bracket endpoint requires both a
        stop_loss_order and a take_profit_order, so a deliberately distant
        take-profit price is passed in to keep the TP effectively out of
        the way while the trailing stop does the real exit management.
        """
        self.cancel_all_orders(product_id)
        return self.place_bracket_order(
            product_id, product_symbol, side, size,
            new_stop_price, far_take_profit_price, trigger_method,
        )
