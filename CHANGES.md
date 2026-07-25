# What changed in this update

## Design
- Full glass-morphism redesign (blurred translucent panels, soft glow
  background, "Space Grotesk" + "Inter" + "JetBrains Mono" type system).
- Footer on every page: **Made with ❤ by Tashu Gaur**

## Login / register
- Register now asks for **username, email, password, confirm password**.
  Confirm password is checked both in the browser and on the server.
- Email is stored per account (contact only, never shown to other users).

## Dashboard
- **Raw trade log removed.** Replaced with a "Recent activity" feed that
  shows only signals, warnings, and errors as clean one-line cards —
  no scrolling wall of text.
- **Position details** card: symbol, direction (long/short), size, entry
  price, mark price, and live unrealized P&L, refreshed every few seconds.
- **Floating P&L widget**: a small glass bubble, bottom-right, that you
  can drag anywhere on screen or hide (and bring back with the round
  reopen button). Turns green in profit, red in loss.
- **Live market price ticker**: a scrolling marquee strip under the top
  bar showing price, % change, and daily high/low for your watchlist of
  coins, pulled straight from the exchange.

## API keys
- If you already have an API key saved, the dashboard no longer shows
  raw key/secret input boxes — it shows the masked key plus a
  **"Change API key"** button that opens a small popup to update both
  values together (so you can never end up with a mismatched key/secret
  pair). If you have no key yet, the setup fields appear instead.
- The bot must be stopped before the key can be changed.

## Reliability
- Every dashboard fetch call is wrapped in error handling — a failed
  request shows a toast instead of breaking the page.
- New `/api/position`, `/api/price`, and `/api/activity` endpoints back
  the new UI; the old `/api/logs` and `/api/account` endpoints were
  folded into these.

## Unchanged
- `bot.py`, `delta_api.py`, `strategy.py`, `risk_manager.py`, `config.py`,
  `test_order.py`, `control_panel.pyw`, and `log_viewer.pyw` (the desktop
  tools) are untouched — all trading logic is exactly as before.

## Setup
```
pip install -r requirements.txt
python app.py
```
The first account you register becomes the admin automatically.
