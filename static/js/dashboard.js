/* dashboard.js -- the control panel: start/stop, live position card,
   the floating P&L HUD, and the API-key change flow. */

const STATUS_REFRESH_MS = 4000;
const ACCOUNT_REFRESH_MS = 4000;

let currentSymbol = window.__DASH.symbol || "BTCUSD";
let currentFixedSize = window.__DASH.fixedSize || 0;
let isRunning = false;

function fmtMoney(n, opts = {}) {
  if (n === null || n === undefined || Number.isNaN(n)) return "--";
  const sign = n > 0 ? "+" : "";
  const digits = opts.digits ?? 2;
  return `${sign}$${n.toFixed(digits)}`;
}
function fmtPrice(n) {
  if (n === null || n === undefined || Number.isNaN(n)) return "--";
  return n >= 100 ? n.toFixed(2) : n.toFixed(4);
}
function fmtPct(n) {
  if (n === null || n === undefined || Number.isNaN(n)) return "--";
  const sign = n > 0 ? "+" : "";
  return `${sign}${n.toFixed(2)}%`;
}

// ---------------------------------------------------------------------
// Smooth count-up tween for live numbers (balance, P&L) -- turns a raw
// value jump into a quick, legible glide instead of a hard flicker.
// ---------------------------------------------------------------------
function animateValue(el, fromVal, toVal, formatFn, duration = 550) {
  if (!el) return;
  if (fromVal === null || fromVal === undefined || Number.isNaN(fromVal)) {
    el.textContent = formatFn(toVal);
    return;
  }
  const start = performance.now();
  const ease = (t) => 1 - Math.pow(1 - t, 3); // ease-out cubic
  function step(now) {
    const t = Math.min(1, (now - start) / duration);
    const current = fromVal + (toVal - fromVal) * ease(t);
    el.textContent = formatFn(current);
    if (t < 1) requestAnimationFrame(step);
  }
  requestAnimationFrame(step);
}

function popValue(el) {
  if (!el) return;
  el.classList.remove("value-pop");
  // force reflow so the animation can retrigger on rapid updates
  void el.offsetWidth;
  el.classList.add("value-pop");
}

// ---------------------------------------------------------------------
// Tiny canvas sparkline -- no chart library needed for a rolling trend line.
// ---------------------------------------------------------------------
function drawSparkline(canvas, values, colorVar) {
  if (!canvas || values.length < 2) return;
  const dpr = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  const w = Math.max(rect.width, 40);
  const h = Math.max(rect.height, 20);
  canvas.width = w * dpr;
  canvas.height = h * dpr;
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);

  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const stepX = w / (values.length - 1);
  const color = getComputedStyle(document.documentElement).getPropertyValue(colorVar).trim() || "#2fe6c4";

  const points = values.map((v, i) => [i * stepX, h - ((v - min) / range) * (h - 6) - 3]);

  ctx.beginPath();
  ctx.moveTo(points[0][0], points[0][1]);
  for (let i = 1; i < points.length; i++) {
    const [x0, y0] = points[i - 1];
    const [x1, y1] = points[i];
    const mx = (x0 + x1) / 2;
    ctx.quadraticCurveTo(x0, y0, mx, (y0 + y1) / 2);
  }
  ctx.lineTo(points[points.length - 1][0], points[points.length - 1][1]);
  ctx.strokeStyle = color;
  ctx.lineWidth = 1.75;
  ctx.lineJoin = "round";
  ctx.stroke();

  const grad = ctx.createLinearGradient(0, 0, 0, h);
  grad.addColorStop(0, color + "33");
  grad.addColorStop(1, color + "00");
  ctx.lineTo(points[points.length - 1][0], h);
  ctx.lineTo(points[0][0], h);
  ctx.closePath();
  ctx.fillStyle = grad;
  ctx.fill();
}

const MAX_HISTORY = 40;
const balanceHistory = [];
const pnlHistory = [];
let lastBalance = null;
let lastPnl = null;

function pushHistory(arr, val) {
  arr.push(val);
  if (arr.length > MAX_HISTORY) arr.shift();
}

// ---------------------------------------------------------------------
// Status pill + control form
// ---------------------------------------------------------------------
function setStatusPill(running, dryRun) {
  const pill = document.getElementById("status-pill");
  const dot = pill.querySelector(".dot");
  const label = pill.querySelector(".label");
  pill.classList.remove("running", "stopped", "live", "dry");
  if (running) {
    pill.classList.add("running");
    label.textContent = dryRun ? "Running -- dry run" : "Running -- LIVE";
    if (!dryRun) pill.classList.add("live");
  } else {
    pill.classList.add("stopped");
    label.textContent = "Stopped";
  }
}

async function refreshStatus() {
  const data = await safeFetchJson("/api/status");
  if (!data || data.ok === false) {
    if (data && data.error) showToast(data.error, "error");
    return;
  }
  isRunning = data.running;
  setStatusPill(data.running, data.dry_run);
  document.getElementById("start-btn").disabled = data.running;
  document.getElementById("stop-btn").disabled = !data.running;
  document.getElementById("symbol-select").disabled = data.running;
}

document.getElementById("start-btn")?.addEventListener("click", async () => {
  const symbol = document.getElementById("symbol-select").value;
  const lotInput = document.getElementById("lot-size").value.trim();
  const liveTrading = document.getElementById("live-toggle").checked;

  let fixedSize = 0;
  if (lotInput !== "") {
    fixedSize = parseInt(lotInput, 10);
    if (Number.isNaN(fixedSize) || fixedSize < 0) {
      showToast("Lot size must be a whole number, 0 or more.", "error");
      return;
    }
  }

  if (liveTrading) {
    const confirmed = confirm(
      "Live trading is ON. This will place REAL orders. Continue?"
    );
    if (!confirmed) return;
  }

  const btn = document.getElementById("start-btn");
  btn.disabled = true;
  btn.textContent = "Starting...";
  const data = await safeFetchJson("/api/start", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ symbol, fixed_size: fixedSize, live_trading: liveTrading }),
  });
  btn.textContent = "▶ Start bot";
  if (data.ok) {
    showToast("Bot started.", "success");
    currentSymbol = symbol;
    currentFixedSize = fixedSize;
  } else {
    showToast(data.error || "Could not start the bot.", "error");
  }
  refreshStatus();
});

document.getElementById("stop-btn")?.addEventListener("click", async () => {
  const btn = document.getElementById("stop-btn");
  btn.disabled = true;
  btn.textContent = "Stopping...";
  const data = await safeFetchJson("/api/stop", { method: "POST" });
  btn.textContent = "■ Stop bot";
  if (data.ok) {
    showToast("Stop requested.", "info");
  } else {
    showToast(data.error || "Could not stop the bot.", "error");
  }
  refreshStatus();
});

document.getElementById("live-toggle")?.addEventListener("change", (e) => {
  const box = document.getElementById("live-toggle-box");
  box.classList.toggle("on", e.target.checked);
});

// ---------------------------------------------------------------------
// Position & Trade Details card + floating P&L HUD
// ---------------------------------------------------------------------
function renderNoPosition(message) {
  document.getElementById("position-card-body").innerHTML = `
    <div class="position-empty">
      <div class="big">No open position</div>
      <div class="small">${message || "Flat right now -- the algorithm is watching for its next signal."}</div>
    </div>`;
  pnlHistory.length = 0;
  lastPnl = null;
  updatePnlFloat(null);
}

function renderPosition(pos) {
  const pnl = pos.unrealized_pnl;
  const pnlClass = pnl > 0 ? "profit" : pnl < 0 ? "loss" : "";
  const dirClass = pos.direction === "long" ? "long" : "short";

  document.getElementById("position-card-body").innerHTML = `
    <div class="pos-head">
      <div class="pos-dir">
        <span class="badge ${dirClass}">${pos.direction.toUpperCase()}</span>
        <span class="pos-symbol">${pos.symbol}</span>
      </div>
      <div class="pnl-hero">
        <div class="amount ${pnlClass}" id="pos-pnl-amount">${fmtMoney(pnl)}</div>
        <div class="pct">${fmtPct(pos.unrealized_pnl_pct)}</div>
      </div>
    </div>
    <div class="sparkline-wrap"><canvas id="position-sparkline"></canvas></div>
    <div class="pos-detail-grid">
      <div class="pos-stat"><div class="k">Size</div><div class="v">${pos.size}</div></div>
      <div class="pos-stat"><div class="k">Leverage</div><div class="v">${pos.leverage}x</div></div>
      <div class="pos-stat"><div class="k">Entry price</div><div class="v">${fmtPrice(pos.entry_price)}</div></div>
      <div class="pos-stat"><div class="k">Mark price</div><div class="v">${fmtPrice(pos.mark_price)}</div></div>
      <div class="pos-stat"><div class="k">Stop-loss</div><div class="v ${pos.stop_loss ? 'loss' : 'muted'}">${pos.stop_loss ? fmtPrice(pos.stop_loss) : "not attached"}</div></div>
      <div class="pos-stat"><div class="k">Take-profit</div><div class="v ${pos.take_profit ? 'profit' : 'muted'}">${pos.take_profit ? fmtPrice(pos.take_profit) : "not attached"}</div></div>
    </div>`;

  pushHistory(pnlHistory, pnl);
  const sparkColor = pnl >= 0 ? "--profit" : "--loss";
  drawSparkline(document.getElementById("position-sparkline"), pnlHistory, sparkColor);

  const amountEl = document.getElementById("pos-pnl-amount");
  if (lastPnl !== null && Math.abs(lastPnl - pnl) > 0.0001) popValue(amountEl);
  lastPnl = pnl;

  updatePnlFloat(pos);
}

function updatePnlFloat(pos) {
  const float = document.getElementById("pnl-float");
  if (!float) return;
  const amountEl = float.querySelector(".amount");
  const subEl = float.querySelector(".sub");
  const dot = float.querySelector(".pnl-float-collapsed-dot");
  const sparkCanvas = document.getElementById("pnl-float-sparkline");

  if (!pos) {
    amountEl.textContent = "--";
    amountEl.className = "amount flat";
    subEl.textContent = "No open position";
    if (sparkCanvas) { const ctx = sparkCanvas.getContext("2d"); ctx.clearRect(0, 0, sparkCanvas.width, sparkCanvas.height); }
    if (dot) { dot.textContent = "--"; dot.style.background = "var(--glass-strong)"; dot.style.color = "var(--ink-2)"; }
    return;
  }
  const pnl = pos.unrealized_pnl;
  const cls = pnl > 0 ? "profit" : pnl < 0 ? "loss" : "flat";
  amountEl.textContent = fmtMoney(pnl);
  amountEl.className = `amount ${cls}`;
  subEl.textContent = `${pos.symbol} ${pos.direction.toUpperCase()} · ${fmtPct(pos.unrealized_pnl_pct)}`;
  popValue(amountEl);
  drawSparkline(sparkCanvas, pnlHistory, pnl >= 0 ? "--profit" : "--loss");
  if (dot) {
    dot.textContent = fmtMoney(pnl, { digits: 0 });
    dot.style.background = pnl > 0 ? "rgba(39,224,143,0.18)" : pnl < 0 ? "rgba(251,69,112,0.18)" : "var(--glass-strong)";
    dot.style.color = pnl > 0 ? "var(--profit)" : pnl < 0 ? "var(--loss)" : "var(--ink-2)";
  }
}

let _accountErrorShown = false;

async function refreshAccount() {
  const data = await safeFetchJson("/api/account");
  const balanceEl = document.getElementById("balance-value");

  if (!data.ok) {
    if (balanceEl) balanceEl.textContent = "--";
    renderNoPosition(data.error);
    if (!_accountErrorShown) {
      showToast(data.error || "Could not load your account.", "error");
      _accountErrorShown = true;
    }
    return;
  }
  _accountErrorShown = false;

  if (balanceEl) {
    const newBalance = Number(data.balance);
    animateValue(balanceEl, lastBalance, newBalance, (v) => `$${v.toFixed(2)}`);
    pushHistory(balanceHistory, newBalance);
    drawSparkline(document.getElementById("balance-sparkline"), balanceHistory, "--mint");
    lastBalance = newBalance;
  }
  if (data.position) {
    renderPosition(data.position);
  } else {
    renderNoPosition();
  }
}

// ---------------------------------------------------------------------
// Algorithm card (loaded once -- config doesn't change while the page is open)
// ---------------------------------------------------------------------
async function loadAlgo() {
  const data = await safeFetchJson("/api/algo");
  const el = document.getElementById("algo-card-body");
  if (!el) return;
  if (!data.ok) {
    el.innerHTML = `<div class="empty-note">Could not load strategy settings.</div>`;
    return;
  }
  el.innerHTML = `
    <div class="row" style="margin-bottom:14px;">
      <span class="algo-mode-chip">EMA ${data.ema_fast}/${data.ema_slow} · ${data.mode}</span>
    </div>
    <div class="algo-grid">
      <div class="algo-stat"><span class="k">Candle resolution</span><span class="v">${data.resolution}m</span></div>
      <div class="algo-stat"><span class="k">RSI period</span><span class="v">${data.rsi_period}</span></div>
      <div class="algo-stat"><span class="k">RSI long range</span><span class="v">${data.rsi_long_range[0]}–${data.rsi_long_range[1]}</span></div>
      <div class="algo-stat"><span class="k">RSI short range</span><span class="v">${data.rsi_short_range[0]}–${data.rsi_short_range[1]}</span></div>
      <div class="algo-stat"><span class="k">ATR period</span><span class="v">${data.atr_period}</span></div>
      <div class="algo-stat"><span class="k">Stop-loss</span><span class="v">${data.sl_atr_mult}× ATR</span></div>
      <div class="algo-stat"><span class="k">Take-profit</span><span class="v">1:${data.tp_rr_mult} R/R</span></div>
      <div class="algo-stat"><span class="k">Risk per trade</span><span class="v">${data.risk_per_trade_pct}%</span></div>
      <div class="algo-stat"><span class="k">Leverage</span><span class="v">${data.leverage}x</span></div>
      <div class="algo-stat"><span class="k">Daily loss limit</span><span class="v">${data.max_daily_loss_pct}%</span></div>
      <div class="algo-stat"><span class="k">Cooldown</span><span class="v">${data.cooldown_seconds}s</span></div>
    </div>`;
}

// ---------------------------------------------------------------------
// Recent Trades (real exchange fills)
// ---------------------------------------------------------------------
function fmtTime(epochSeconds) {
  if (!epochSeconds) return "--";
  const d = new Date(epochSeconds * 1000);
  return d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

async function loadTrades() {
  const data = await safeFetchJson("/api/trades");
  const el = document.getElementById("trades-card-body");
  if (!el) return;

  if (!data.ok) {
    el.innerHTML = `<div class="empty-note">${data.error || "Could not load trade history."}</div>`;
    return;
  }
  if (!data.trades || data.trades.length === 0) {
    el.innerHTML = `<div class="empty-note">No trades yet -- they'll show up here as soon as the bot fills an order.</div>`;
    return;
  }

  el.innerHTML = data.trades.map((t) => `
    <div class="trade-row">
      <div class="trade-left">
        <span class="trade-side ${t.side === 'buy' ? 'buy' : 'sell'}">${(t.side || '?').toUpperCase()}</span>
        <div class="trade-meta">
          <span class="role">${t.role === 'exit' ? 'Exit' : 'Entry'} &middot; ${t.order_type}</span>
          <span class="time">${fmtTime(t.time)}</span>
        </div>
      </div>
      <div class="trade-right">
        <div class="price">${fmtPrice(t.price)}</div>
        <div class="size">${t.size} ${t.symbol}</div>
      </div>
    </div>
  `).join("");
}

// ---------------------------------------------------------------------
// Activity log (collapsible, same color-coding as the old .pyw viewers)
// ---------------------------------------------------------------------
let _logOffset = 0;
let _logPollTimer = null;

function classifyLogLine(line) {
  if (line.includes("[ERROR]")) return "error";
  if (line.includes("[WARNING]")) return "warning";
  if (line.includes("SIGNAL") || line.includes("Order placed") || line.includes("Entry order") || line.includes("Bracket")) return "signal";
  return "info";
}

async function pollActivityLog() {
  const data = await safeFetchJson(`/api/logs?since=${_logOffset}`);
  if (!data || !data.lines) return;
  if (data.lines.length) {
    const container = document.getElementById("log-lines");
    const atBottom = container.scrollHeight - container.clientHeight <= container.scrollTop + 4;
    const html = data.lines.map((l) => `<div class="log-line ${classifyLogLine(l)}">${l.replace(/</g, "&lt;")}</div>`).join("");
    container.insertAdjacentHTML("beforeend", html);
    if (atBottom) container.scrollTop = container.scrollHeight;
  }
  _logOffset = data.offset ?? _logOffset;
}

function initActivityLog() {
  const toggle = document.getElementById("log-toggle");
  const body = document.getElementById("log-body");
  if (!toggle || !body) return;
  toggle.addEventListener("click", () => {
    const opening = !body.classList.contains("open");
    toggle.classList.toggle("open", opening);
    body.classList.toggle("open", opening);
    if (opening && !_logPollTimer) {
      pollActivityLog();
      _logPollTimer = setInterval(pollActivityLog, 3000);
    } else if (!opening && _logPollTimer) {
      clearInterval(_logPollTimer);
      _logPollTimer = null;
    }
  });
}

// ---------------------------------------------------------------------
// API key: hidden once set, revealed only via "Change API key"
// ---------------------------------------------------------------------
function initApiKeyFlow() {
  const changeBtn = document.getElementById("change-api-key-btn");
  const form = document.getElementById("api-key-form");
  const cancelBtn = document.getElementById("cancel-api-key-btn");
  if (!changeBtn || !form) return;

  changeBtn.addEventListener("click", () => {
    form.classList.add("open");
    changeBtn.parentElement.style.display = "none";
  });
  cancelBtn?.addEventListener("click", () => {
    form.classList.remove("open");
    document.getElementById("api-key-set-row").style.display = "flex";
    form.reset();
  });

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const apiKey = document.getElementById("new-api-key").value.trim();
    const apiSecret = document.getElementById("new-api-secret").value.trim();
    const useTestnet = document.getElementById("use-testnet").checked;

    if (!apiKey || !apiSecret) {
      showToast("Enter both the API key and secret.", "error");
      return;
    }
    const submitBtn = form.querySelector("button[type=submit]");
    submitBtn.disabled = true;
    submitBtn.textContent = "Saving...";
    const data = await safeFetchJson("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ api_key: apiKey, api_secret: apiSecret, use_testnet: useTestnet }),
    });
    submitBtn.disabled = false;
    submitBtn.textContent = "Save new key";
    if (data.ok) {
      showToast("API key updated.", "success");
      form.reset();
      form.classList.remove("open");
      document.getElementById("api-key-masked-value").textContent = data.api_key_masked;
      document.getElementById("api-key-set-row").style.display = "flex";
    } else {
      showToast(data.error || "Could not save the API key.", "error");
    }
  });
}

// ---------------------------------------------------------------------
// Draggable floating P&L HUD
// ---------------------------------------------------------------------
function initPnlFloatDrag() {
  const float = document.getElementById("pnl-float");
  if (!float) return;
  let dragging = false, offsetX = 0, offsetY = 0, moved = false;

  const start = (clientX, clientY) => {
    dragging = true;
    moved = false;
    const rect = float.getBoundingClientRect();
    offsetX = clientX - rect.left;
    offsetY = clientY - rect.top;
    float.classList.add("dragging");
  };
  const move = (clientX, clientY) => {
    if (!dragging) return;
    moved = true;
    const x = Math.min(Math.max(0, clientX - offsetX), window.innerWidth - float.offsetWidth);
    const y = Math.min(Math.max(0, clientY - offsetY), window.innerHeight - float.offsetHeight);
    float.style.left = `${x}px`;
    float.style.top = `${y}px`;
    float.style.right = "auto";
    float.style.bottom = "auto";
  };
  const end = () => { dragging = false; float.classList.remove("dragging"); };

  float.addEventListener("mousedown", (e) => { if (e.target.closest("button")) return; start(e.clientX, e.clientY); });
  window.addEventListener("mousemove", (e) => move(e.clientX, e.clientY));
  window.addEventListener("mouseup", end);

  float.addEventListener("touchstart", (e) => { if (e.target.closest("button")) return; const t = e.touches[0]; start(t.clientX, t.clientY); }, { passive: true });
  window.addEventListener("touchmove", (e) => { const t = e.touches[0]; move(t.clientX, t.clientY); }, { passive: true });
  window.addEventListener("touchend", end);

  document.getElementById("pnl-float-collapse")?.addEventListener("click", () => {
    float.classList.toggle("collapsed");
  });
  float.addEventListener("click", () => {
    if (moved) return; // don't expand right after a drag
    if (float.classList.contains("collapsed")) float.classList.remove("collapsed");
  });
}

document.addEventListener("DOMContentLoaded", () => {
  initApiKeyFlow();
  initPnlFloatDrag();
  initActivityLog();
  refreshStatus();
  refreshAccount();
  loadAlgo();
  loadTrades();
  setInterval(refreshStatus, STATUS_REFRESH_MS);
  setInterval(refreshAccount, ACCOUNT_REFRESH_MS);
  setInterval(loadTrades, 8000);
});

window.addEventListener("error", (e) => {
  showToast("Something went wrong on this page. Try refreshing.", "error");
});
