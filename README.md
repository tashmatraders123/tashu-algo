# Delta Scalper -- Dashboard Update

## What's new

- **New glass-UI web dashboard** (`templates/` + `static/`) -- these didn't
  exist in the files you uploaded (only `app.py`/`users.py` referenced them),
  so they've been built from scratch: `login.html`, `register.html`,
  `index.html`, `admin.html`, `contact.html`, plus `static/css/style.css`
  and `static/js/*.js`.
- **Register now asks for email + confirm password** (`users.py`, `app.py`).
  You can also sign in with either your username or your email.
- **Position & Trade Details card** replaces a raw log feed -- live entry
  price, mark price, size, leverage, real attached stop-loss/take-profit
  (pulled from Delta's open orders), and live P&L, refreshed every few
  seconds.
- **Floating, draggable P&L popup** (bottom-right, collapsible) -- always
  visible, live, with its own mini sparkline.
- **Algorithm card** -- a clean read-out of the strategy/risk config
  (EMA/RSI/ATR settings, risk %, leverage, cooldown) so "what the bot is
  doing" is visible at a glance.
- **Live price ticker tape** across the top of the dashboard (public
  market data, no API key needed, cached a few seconds server-side).
- **API key is hidden once set** -- you'll see a masked key with a
  "Change API key" button instead of raw input fields sitting open.
- **Contact** -- a modal (footer link, top nav) plus a standalone
  `/contact` page.
- **"Made with (heart) by Tashu Gaur"** in the footer of every page.
- Smooth entrance animations, an ambient animated background, skeleton
  loading states, animated counters, and live sparklines throughout.

## Before you run it

1. **Edit the placeholder contact details** in `templates/base.html` and
   `templates/contact.html` (search for `tashu.gaur@example.com`,
   `@tashugaur`, `github.com/tashugaur`) -- replace with your real ones.
2. Install/confirm dependencies: `pip install flask python-dotenv pandas
   numpy requests werkzeug`.
3. Run with `python app.py`, then open `http://localhost:5000/register`
   to create the first account (it becomes the admin automatically).
4. `static/css/style.css` pulls Space Grotesk / Inter / JetBrains Mono
   from Google Fonts over the internet -- if the machine running this has
   no internet access, swap that `@import` line for local font files or
   just delete it (it'll fall back to system fonts).

## Notes

- `control_panel.pyw` and `log_viewer.pyw` (the desktop Tkinter tools)
  are unchanged -- everything above is about the web dashboard, since
  that's where a "login window" lives. Say the word if you'd like those
  restyled or merged into the web dashboard too.
- `delta_api.py` gained two small additions: `get_open_orders()` (to read
  the real SL/TP off the exchange) and `get_tickers_bulk()` (for the
  ticker tape). Nothing existing was changed.
