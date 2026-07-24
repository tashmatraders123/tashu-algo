# Delta Exchange 1-Min Scalping Bot

A self-contained algo-trading bot for **Delta Exchange** (India). Strategy:
EMA(9/21) crossover for direction, RSI filter to avoid chasing extremes,
ATR-based stop-loss/take-profit sized to current volatility, and a
risk-based position sizer with a daily loss kill switch.

**This is not financial advice and there is no guarantee of profitability.**
Scalping with leverage is high-risk — you can lose your entire margin
quickly, especially with slippage and fees on 1-minute timeframes. Test on
testnet and in `DRY_RUN` mode extensively before risking real money, and
never risk more than you can afford to lose.

---

## 1. Project layout

```
delta-scalper/
├── bot.py              # main loop — run this
├── config.py            # settings, all overridable via .env
├── delta_api.py         # signed REST client for Delta Exchange
├── strategy.py           # EMA/RSI/ATR signal logic
├── risk_manager.py      # position sizing + daily kill switch
├── requirements.txt
├── .env.example          # copy to .env and fill in
├── delta-scalper.service # systemd unit for 24/7 running
└── Dockerfile
```

## 2. Get API keys

1. Create an account at https://www.delta.exchange (or the testnet at
   https://testnet.delta.exchange for paper trading).
2. Go to Account → API Keys → generate a new key. Restrict IP access to
   your server's IP once you know it (see hosting section).
3. Copy `.env.example` to `.env` and fill in `DELTA_API_KEY` / `DELTA_API_SECRET`.

## 3. Install & run locally

```bash
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env              # then edit .env with your keys/settings
python bot.py
```

With `.env` defaults (`USE_TESTNET=true`, `DRY_RUN=true`) the bot only
**logs** what it would do — no orders are sent. Watch `bot.log` for a
few hours/days, confirm the signals look sane, then:

1. Set `DRY_RUN=false` while still on `USE_TESTNET=true` to fire real
   orders against Delta's testnet (fake money).
2. Only once you're confident, set `USE_TESTNET=false` to go live.

## 4. Tuning the strategy

All knobs live in `.env` (see `.env.example`): EMA lengths, RSI bounds,
ATR multipliers for SL/TP, risk % per trade, leverage, and the daily
loss kill switch. Nothing requires touching the code.

---

## 5. Running it 24/7 on a FREE server

A scalping bot needs to run continuously, so your laptop sleeping or
losing Wi-Fi will cause missed candles/signals. Below are free options,
best first.

### Option A (recommended): Oracle Cloud "Always Free" VM

Oracle's free tier is genuinely free forever (not a 12-month trial) and
gives you a real Linux VM with enough resources for this bot.

1. Sign up at https://www.oracle.com/cloud/free/ (needs a card for
   verification, but the Always Free resources are not charged).
2. Create a Compute Instance:
   - Shape: **VM.Standard.E2.1.Micro** (AMD, always free) or an Ampere
     ARM shape (also free, more resources) — either works fine for this bot.
   - Image: Ubuntu 22.04 or 24.04.
   - Save the SSH key pair Oracle gives you.
3. Open port 22 (SSH) in the VM's security list — it's usually open by
   default. You do NOT need to open any other ports; the bot only makes
   outbound calls to Delta's API.
4. SSH in:
   ```bash
   ssh -i your_key.pem ubuntu@<your-vm-public-ip>
   ```
5. Install Python and clone/upload your project:
   ```bash
   sudo apt update && sudo apt install -y python3-venv python3-pip git
   # upload the delta-scalper folder via scp, or git clone your own repo
   scp -i your_key.pem -r delta-scalper ubuntu@<vm-ip>:~/
   ```
6. Set it up and do a manual test run:
   ```bash
   cd ~/delta-scalper
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   cp .env.example .env
   nano .env      # fill in your keys and settings
   python bot.py  # Ctrl+C to stop once you see it logging correctly
   ```
7. Install it as a systemd service so it survives reboots and restarts
   automatically if it crashes:
   ```bash
   nano delta-scalper.service   # confirm the User/paths match your VM
   sudo cp delta-scalper.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now delta-scalper
   sudo systemctl status delta-scalper     # check it's running
   journalctl -u delta-scalper -f          # live logs (Ctrl+C to exit)
   ```
8. To update the bot later: stop the service, `git pull` or re-upload
   files, then `sudo systemctl restart delta-scalper`.

### Option B: Google Cloud free tier (e2-micro)

Google gives one `e2-micro` instance free per month in specific US
regions (us-west1, us-central1, us-east1). Same steps as Option A once
the VM is up — SSH in, install Python, use the same systemd service file.

### Option C: Fly.io / Railway.app free tier

Both support long-running background workers via Docker and have small
free monthly allowances (hours or credits, not unlimited like Oracle).
With the included `Dockerfile`:

```bash
# Fly.io example
fly launch          # follow prompts, don't expose any HTTP port
fly secrets set DELTA_API_KEY=xxx DELTA_API_SECRET=yyy USE_TESTNET=false DRY_RUN=false
fly deploy
```
Check current free-tier limits on their pricing pages before relying on
this for real-money trading, as free allowances change over time.

### Option D: A spare Raspberry Pi / old PC at home

Zero cost if you already own the hardware. Same systemd steps as Option A.
Downside: depends on your home internet/power staying up.

### What NOT to use for this

- **Render/PythonAnywhere free tiers**: free web services spin down on
  inactivity, and PythonAnywhere's free tier restricts outbound network
  calls to a domain whitelist that won't include Delta's API — a
  1-minute scalper needs to run continuously and call an arbitrary host.
- **Google Colab / Kaggle notebooks**: sessions disconnect after a few
  hours of inactivity, unsuitable for 24/7 trading.

---

## 6. Operational safety checklist before going live

- [ ] Ran in `DRY_RUN=true` for at least a few days and reviewed `bot.log`
- [ ] Tested with real orders on `USE_TESTNET=true` first
- [ ] Set `RISK_PER_TRADE_PCT` and `MAX_DAILY_LOSS_PCT` to values you're
      genuinely comfortable losing
- [ ] Restricted your API key to your server's IP in Delta's dashboard
- [ ] Confirmed the systemd service auto-restarts after a VM reboot
      (`sudo reboot` then check `systemctl status delta-scalper`)
- [ ] Set up some way to be notified of crashes (e.g. a cron job that
      emails you if the process isn't running, or a service like
      UptimeRobot pinging a small healthcheck endpoint you add)
- [ ] Understand that exchange downtime, API changes, slippage, and
      fees are not simulated in `DRY_RUN` mode and will affect real
      performance
