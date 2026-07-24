"""
test_order.py
Fires ONE manual test order right now, bypassing the strategy signal logic
entirely. Use this to confirm your API keys, permissions, and the order
pipeline actually work end-to-end on Delta before waiting for a real
strategy signal.

Safe to run on testnet (fake money). Do NOT run this with USE_TESTNET=false
unless you genuinely want to risk real funds on a throwaway test trade.

Usage:
    python test_order.py
"""
import sys
import time

import config
from delta_api import DeltaClient, DeltaAPIError

MIN_SIZE = 1  # smallest possible order, in contracts


def main():
    print(f"Target server : {config.BASE_URL}")
    print(f"Testnet mode  : {config.USE_TESTNET}")
    print(f"Symbol        : {config.SYMBOL}")
    print()

    if not config.USE_TESTNET:
        confirm = input(
            "!!  USE_TESTNET=false -- this will place a REAL order with REAL "
            "money.  Type YES to continue, anything else to abort: "
        )
        if confirm.strip() != "YES":
            print("Aborted.")
            return

    client = DeltaClient()

    print("Fetching product info...")
    product = client.get_product(config.SYMBOL)
    product_id = product["id"]
    print(f"  product_id = {product_id}")

    print("Fetching current price...")
    ticker = client.get_ticker(config.SYMBOL)
    last_price = float(ticker["close"])
    print(f"  last price = {last_price}")

    # Put the stop-loss/take-profit a comfortable distance away so this
    # test order doesn't get closed out instantly by normal price wiggle.
    sl_price = round(last_price * 0.98, 1)   # 2% below entry
    tp_price = round(last_price * 1.02, 1)   # 2% above entry

    print()
    print("About to place a TEST BUY (long) bracket order:")
    print(f"  size            = {MIN_SIZE} contract(s)")
    print(f"  entry (market)  ~ {last_price}")
    print(f"  stop-loss       = {sl_price}")
    print(f"  take-profit     = {tp_price}")
    print()

    if config.DRY_RUN:
        print("[DRY_RUN] Skipping real order call. Set DRY_RUN=false in .env "
              "to actually send this test order.")
        return

    try:
        result = client.enter_with_bracket(
            product_id, config.SYMBOL, "buy", MIN_SIZE, sl_price, tp_price
        )
        print("Entry order response:")
        print(result["entry"])
        if result["bracket"] is not None:
            print()
            print("Bracket (stop-loss/take-profit) attached:")
            print(result["bracket"])
            print()
            print("Check your Delta Positions page now -- you should see a "
                  f"{MIN_SIZE}-contract long position on {config.SYMBOL}, "
                  "with a stop-loss and take-profit already attached.")
        else:
            print()
            print("WARNING: entry order was sent, but the position wasn't "
                  "detected in time to attach the stop-loss/take-profit. "
                  "Check your Delta Positions page manually and close/manage "
                  "this test position by hand if needed.")
    except DeltaAPIError as e:
        print("ORDER FAILED:")
        print(e)
        print()
        print("Common causes:")
        print(" - API key permissions don't include Trading/Order placement")
        print(" - Whitelisted IP doesn't match your current IP")
        print(" - Leverage/margin mode issue for this product")
        sys.exit(1)


if __name__ == "__main__":
    main()
