"""
control_panel.pyw
One window to control everything:
  - Pick which coin to trade
  - Set your lot size (or leave 0 to let the bot auto-size based on risk %)
  - Toggle Live Trading on/off (safety: starts OFF = dry run, no real orders)
  - Start / Stop the bot
  - Watch the live log, color-coded

Double-click this file to run it (it ends in .pyw so no black console
window appears). Do NOT run bot.py separately at the same time as this --
this window starts and stops bot.py for you.
"""
import os
import re
import subprocess
import sys
import time
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(BASE_DIR, ".env")
LOG_PATH = os.path.join(BASE_DIR, "bot.log")
STOP_FLAG_PATH = os.path.join(BASE_DIR, "stop.flag")
BOT_SCRIPT = os.path.join(BASE_DIR, "bot.py")

COMMON_SYMBOLS = [
    "BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD", "DOGEUSD",
    "ADAUSD", "MATICUSD", "LTCUSD", "BNBUSD", "AVAXUSD",
]

REFRESH_MS = 500


def read_env():
    """Returns dict of current .env values, and the raw lines (to preserve formatting)."""
    values = {}
    lines = []
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH, "r", encoding="utf-8") as f:
            lines = f.readlines()
        for line in lines:
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                k, v = stripped.split("=", 1)
                values[k.strip()] = v.strip()
    return values, lines


def write_env_values(updates):
    """Updates specific keys in .env, preserving everything else. Adds the key if missing."""
    _, lines = read_env()
    keys_written = set()
    new_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            k = stripped.split("=", 1)[0].strip()
            if k in updates:
                new_lines.append(f"{k}={updates[k]}\n")
                keys_written.add(k)
                continue
        new_lines.append(line)
    for k, v in updates.items():
        if k not in keys_written:
            new_lines.append(f"{k}={v}\n")
    with open(ENV_PATH, "w", encoding="utf-8") as f:
        f.writelines(new_lines)


class ControlPanel:
    def __init__(self, root):
        self.root = root
        self.root.title("Delta Scalper - Control Panel")
        self.root.geometry("1000x700")
        self.root.configure(bg="#1e1e1e")
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        self.process = None
        self._file_pos = 0
        self._last_size = -1
        self._stop_requested_at = None

        self.build_ui()
        self.load_current_settings()
        self.poll_log_file()
        self.poll_process_status()

    # ------------------------------------------------------------------
    def build_ui(self):
        pad = {"padx": 8, "pady": 6}

        top = tk.Frame(self.root, bg="#1e1e1e")
        top.pack(fill="x", **pad)

        # Coin selector
        tk.Label(top, text="Coin:", bg="#1e1e1e", fg="#d4d4d4", font=("Segoe UI", 10)).grid(row=0, column=0, sticky="w")
        self.symbol_var = tk.StringVar()
        self.symbol_box = ttk.Combobox(top, textvariable=self.symbol_var, values=COMMON_SYMBOLS, width=15)
        self.symbol_box.grid(row=0, column=1, sticky="w", padx=(4, 20))

        # Lot size
        tk.Label(top, text="Lot size (0 = auto):", bg="#1e1e1e", fg="#d4d4d4", font=("Segoe UI", 10)).grid(row=0, column=2, sticky="w")
        self.lot_var = tk.StringVar(value="0")
        tk.Entry(top, textvariable=self.lot_var, width=8).grid(row=0, column=3, sticky="w", padx=(4, 20))

        # Live trading toggle
        self.live_var = tk.BooleanVar(value=False)
        live_check = tk.Checkbutton(
            top, text="Live trading (real orders)", variable=self.live_var,
            bg="#1e1e1e", fg="#f14c4c", selectcolor="#1e1e1e",
            activebackground="#1e1e1e", activeforeground="#f14c4c",
            font=("Segoe UI", 10, "bold"),
        )
        live_check.grid(row=0, column=4, sticky="w", padx=(0, 20))

        # Start/Stop buttons
        self.start_btn = tk.Button(top, text="▶ Start", width=10, bg="#2e7d32", fg="white",
                                    font=("Segoe UI", 10, "bold"), command=self.start_bot)
        self.start_btn.grid(row=0, column=5, padx=4)
        self.stop_btn = tk.Button(top, text="■ Stop", width=10, bg="#b71c1c", fg="white",
                                   font=("Segoe UI", 10, "bold"), command=self.stop_bot, state="disabled")
        self.stop_btn.grid(row=0, column=6, padx=4)

        # Status line
        status_frame = tk.Frame(self.root, bg="#1e1e1e")
        status_frame.pack(fill="x", padx=8)
        self.status_var = tk.StringVar(value="Status: STOPPED")
        tk.Label(status_frame, textvariable=self.status_var, bg="#1e1e1e", fg="#e5c07b",
                 font=("Segoe UI", 10, "bold")).pack(side="left")

        # Log area
        self.text = scrolledtext.ScrolledText(
            self.root, bg="#141414", fg="#d4d4d4", insertbackground="#d4d4d4",
            font=("Consolas", 10), wrap="word", state="disabled",
        )
        self.text.pack(fill="both", expand=True, padx=8, pady=8)
        self.text.tag_config("error", foreground="#f14c4c")
        self.text.tag_config("warning", foreground="#e5c07b")
        self.text.tag_config("signal", foreground="#4caf50")
        self.text.tag_config("info", foreground="#d4d4d4")

        bottom = tk.Frame(self.root, bg="#1e1e1e")
        bottom.pack(fill="x", padx=8, pady=(0, 8))
        self.autoscroll = tk.BooleanVar(value=True)
        tk.Checkbutton(bottom, text="Auto-scroll", variable=self.autoscroll, bg="#1e1e1e", fg="#d4d4d4",
                        selectcolor="#1e1e1e", activebackground="#1e1e1e", activeforeground="#d4d4d4").pack(side="left")
        tk.Button(bottom, text="Clear view", command=self.clear_view).pack(side="left", padx=8)

    # ------------------------------------------------------------------
    def load_current_settings(self):
        values, _ = read_env()
        self.symbol_var.set(values.get("SYMBOL", "BTCUSD"))
        self.lot_var.set(values.get("FIXED_SIZE", "0"))
        self.live_var.set(values.get("DRY_RUN", "true").lower() != "true")

    def clear_view(self):
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.configure(state="disabled")

    # ------------------------------------------------------------------
    def start_bot(self):
        if self.process is not None:
            return

        symbol = self.symbol_var.get().strip().upper()
        if not symbol:
            messagebox.showerror("Missing coin", "Please choose or type a coin symbol, e.g. BTCUSD")
            return

        lot_text = self.lot_var.get().strip()
        try:
            lot_size = int(lot_text) if lot_text else 0
            if lot_size < 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("Invalid lot size", "Lot size must be a whole number, 0 or more.")
            return

        dry_run = "false" if self.live_var.get() else "true"

        if self.live_var.get():
            confirmed = messagebox.askyesno(
                "Confirm live trading",
                "Live trading is ON. This will place REAL orders using REAL "
                "funds if USE_TESTNET=false in your .env, or real orders on "
                "your testnet demo account if USE_TESTNET=true.\n\n"
                "Continue?",
            )
            if not confirmed:
                return

        write_env_values({
            "SYMBOL": symbol,
            "FIXED_SIZE": str(lot_size),
            "DRY_RUN": dry_run,
        })

        if os.path.exists(STOP_FLAG_PATH):
            os.remove(STOP_FLAG_PATH)

        creationflags = 0
        if sys.platform == "win32":
            creationflags = subprocess.CREATE_NO_WINDOW

        try:
            self.process = subprocess.Popen(
                [sys.executable, BOT_SCRIPT],
                cwd=BASE_DIR,
                creationflags=creationflags,
            )
        except Exception as e:
            messagebox.showerror("Failed to start", f"Could not start bot.py:\n{e}")
            self.process = None
            return

        self.status_var.set(f"Status: RUNNING  |  {symbol}  |  lot={'auto' if lot_size == 0 else lot_size}  |  "
                             f"{'LIVE' if self.live_var.get() else 'DRY RUN'}")
        self.start_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self.symbol_box.config(state="disabled")

    def stop_bot(self):
        if self.process is None:
            return
        # Ask the bot to stop gracefully via the flag file
        with open(STOP_FLAG_PATH, "w") as f:
            f.write("stop")
        self._stop_requested_at = time.time()
        self.status_var.set("Status: STOPPING...")
        self.stop_btn.config(state="disabled")

    # ------------------------------------------------------------------
    def poll_process_status(self):
        if self.process is not None:
            exit_code = self.process.poll()
            if exit_code is not None:
                # Process has ended
                self.process = None
                self._stop_requested_at = None
                self.status_var.set("Status: STOPPED")
                self.start_btn.config(state="normal")
                self.stop_btn.config(state="disabled")
                self.symbol_box.config(state="readonly")
                if os.path.exists(STOP_FLAG_PATH):
                    os.remove(STOP_FLAG_PATH)
            elif self._stop_requested_at is not None and (time.time() - self._stop_requested_at) > 15:
                # Bot didn't exit in time on its own, force-stop it
                self.process.terminate()
        self.root.after(REFRESH_MS, self.poll_process_status)

    def on_close(self):
        if self.process is not None:
            if not messagebox.askyesno("Bot is running", "The bot is still running. Stop it and exit?"):
                return
            self.stop_bot()
            # give it a moment to shut down
            for _ in range(10):
                if self.process is None or self.process.poll() is not None:
                    break
                time.sleep(0.5)
            if self.process is not None and self.process.poll() is None:
                self.process.terminate()
        self.root.destroy()

    # ------------------------------------------------------------------
    def classify_line(self, line):
        if "[ERROR]" in line:
            return "error"
        if "[WARNING]" in line:
            return "warning"
        if "SIGNAL" in line or "Order placed" in line or "Entry order" in line or "Bracket" in line:
            return "signal"
        return "info"

    def append_line(self, line):
        tag = self.classify_line(line)
        self.text.configure(state="normal")
        self.text.insert("end", line, tag)
        self.text.configure(state="disabled")
        if self.autoscroll.get():
            self.text.see("end")

    def poll_log_file(self):
        try:
            if os.path.exists(LOG_PATH):
                size = os.path.getsize(LOG_PATH)
                if size < self._file_pos:
                    self._file_pos = 0
                if size != self._last_size:
                    with open(LOG_PATH, "r", encoding="utf-8", errors="replace") as f:
                        f.seek(self._file_pos)
                        new_data = f.read()
                        self._file_pos = f.tell()
                    if new_data:
                        for line in new_data.splitlines(keepends=True):
                            self.append_line(line)
                    self._last_size = size
        except Exception:
            pass
        self.root.after(REFRESH_MS, self.poll_log_file)


if __name__ == "__main__":
    root = tk.Tk()
    app = ControlPanel(root)
    root.mainloop()
