"""
log_viewer.pyw
A simple, separate window that shows the bot's log live -- no black
Command Prompt background, just a clean readable window.

Run this ALONGSIDE bot.py (in a second window), not instead of it.
Double-click this file (it ends in .pyw so no console window pops up),
or run:  pythonw log_viewer.pyw

Color coding:
  green  = trade signals / successful orders
  yellow = warnings
  red    = errors
  white  = normal info lines
"""
import os
import time
import tkinter as tk
from tkinter import scrolledtext

import config

LOG_PATH = config.LOG_FILE
REFRESH_MS = 500  # how often to check the log file for new lines


class LogViewer:
    def __init__(self, root):
        self.root = root
        self.root.title("Delta Scalper Bot - Live Log")
        self.root.geometry("1000x600")
        self.root.configure(bg="#1e1e1e")

        # Top status bar
        self.status_var = tk.StringVar(value="Waiting for log file...")
        status_bar = tk.Label(
            root, textvariable=self.status_var, bg="#1e1e1e", fg="#888888",
            anchor="w", font=("Consolas", 9),
        )
        status_bar.pack(fill="x", padx=8, pady=(6, 0))

        # Main scrollable text area
        self.text = scrolledtext.ScrolledText(
            root, bg="#1e1e1e", fg="#d4d4d4", insertbackground="#d4d4d4",
            font=("Consolas", 11), wrap="word", state="disabled",
        )
        self.text.pack(fill="both", expand=True, padx=8, pady=8)

        # Tag colors for different line types
        self.text.tag_config("error", foreground="#f14c4c")
        self.text.tag_config("warning", foreground="#e5c07b")
        self.text.tag_config("signal", foreground="#4caf50")
        self.text.tag_config("info", foreground="#d4d4d4")

        # Bottom controls
        controls = tk.Frame(root, bg="#1e1e1e")
        controls.pack(fill="x", padx=8, pady=(0, 8))

        self.autoscroll = tk.BooleanVar(value=True)
        tk.Checkbutton(
            controls, text="Auto-scroll", variable=self.autoscroll,
            bg="#1e1e1e", fg="#d4d4d4", selectcolor="#1e1e1e",
            activebackground="#1e1e1e", activeforeground="#d4d4d4",
        ).pack(side="left")

        tk.Button(controls, text="Clear view", command=self.clear_view).pack(side="left", padx=8)

        self._file_pos = 0
        self._last_size = -1
        self.poll_log_file()

    def clear_view(self):
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.configure(state="disabled")

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
                    # Log file was rotated/cleared, start over
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
                self.status_var.set(f"Watching: {os.path.abspath(LOG_PATH)}  |  Last check: {time.strftime('%H:%M:%S')}")
            else:
                self.status_var.set(f"Waiting for {LOG_PATH} to be created (start bot.py)...")
        except Exception as e:
            self.status_var.set(f"Error reading log: {e}")

        self.root.after(REFRESH_MS, self.poll_log_file)


if __name__ == "__main__":
    root = tk.Tk()
    app = LogViewer(root)
    root.mainloop()
