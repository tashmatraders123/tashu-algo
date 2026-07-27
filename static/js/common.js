/* common.js -- shared across dashboard.html and admin.html:
   toast notifications, the contact modal, and the live price ticker tape. */

// ---------------------------------------------------------------------
// Toasts -- every fetch() failure in this app should surface here, so
// nothing fails silently ("no errors tolerated" means the user always
// finds out, not that errors never happen).
// ---------------------------------------------------------------------
function showToast(message, kind = "info", timeoutMs = 5000) {
  const stack = document.getElementById("toast-stack");
  if (!stack) return;
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.textContent = message;
  stack.appendChild(el);
  setTimeout(() => {
    el.style.transition = "opacity 0.3s ease";
    el.style.opacity = "0";
    setTimeout(() => el.remove(), 300);
  }, timeoutMs);
}

// Wraps fetch() so network failures / bad JSON / HTTP errors always
// resolve to a consistent {ok:false, error} shape instead of throwing
// somewhere the caller forgot to catch.
async function safeFetchJson(url, options = {}) {
  try {
    const res = await fetch(url, options);
    let data;
    try {
      data = await res.json();
    } catch (parseErr) {
      return { ok: false, error: `Server returned an unreadable response (HTTP ${res.status})` };
    }
    if (!res.ok && data.ok === undefined) {
      return { ok: false, error: data.error || `Request failed (HTTP ${res.status})` };
    }
    return data;
  } catch (networkErr) {
    return { ok: false, error: "Network error -- check your connection and try again." };
  }
}

// ---------------------------------------------------------------------
// Contact modal
// ---------------------------------------------------------------------
function initContactModal() {
  const openers = document.querySelectorAll("[data-open-contact]");
  const overlay = document.getElementById("contact-modal");
  if (!overlay) return;
  const close = () => overlay.classList.remove("open");
  openers.forEach((btn) => btn.addEventListener("click", (e) => {
    e.preventDefault();
    overlay.classList.add("open");
  }));
  overlay.addEventListener("click", (e) => { if (e.target === overlay) close(); });
  overlay.querySelectorAll("[data-close-modal]").forEach((btn) => btn.addEventListener("click", close));
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") close(); });
}

// ---------------------------------------------------------------------
// Live price ticker tape
// ---------------------------------------------------------------------
const TICKER_REFRESH_MS = 5000;
let _lastTickerPrices = {};

function renderTicker(tickers) {
  const track = document.getElementById("ticker-track");
  if (!track) return;

  if (!tickers || tickers.length === 0) {
    track.innerHTML = `<div class="ticker-empty">Live prices unavailable right now -- retrying...</div>`;
    return;
  }

  const itemHtml = (t) => {
    const prev = _lastTickerPrices[t.symbol];
    const dir = prev === undefined ? "flat" : (t.price > prev ? "up" : (t.price < prev ? "down" : "flat"));
    const arrow = dir === "up" ? "▲" : dir === "down" ? "▼" : "•";
    let chgHtml = "";
    if (typeof t.change_pct === "number") {
      const chgDir = t.change_pct > 0 ? "up" : (t.change_pct < 0 ? "down" : "flat");
      const sign = t.change_pct > 0 ? "+" : "";
      chgHtml = `<span class="chg ${chgDir}">${sign}${t.change_pct.toFixed(2)}%</span>`;
    }
    const priceStr = t.price >= 100 ? t.price.toFixed(1) : t.price.toFixed(4);
    return `<div class="ticker-item">
      <span class="sym">${t.symbol}</span>
      <span class="px ${dir}">${arrow} ${priceStr}</span>
      ${chgHtml}
    </div>`;
  };

  // Duplicate the row so the CSS animation (-50% translateX) loops seamlessly.
  const row = tickers.map(itemHtml).join("");
  track.innerHTML = row + row;

  _lastTickerPrices = {};
  tickers.forEach((t) => { _lastTickerPrices[t.symbol] = t.price; });
}

async function pollTicker() {
  const data = await safeFetchJson("/api/ticker");
  if (data.ok) {
    renderTicker(data.tickers);
  } else {
    const track = document.getElementById("ticker-track");
    if (track && !track.dataset.hasData) {
      track.innerHTML = `<div class="ticker-empty">Live prices unavailable -- ${data.error || "retrying"}...</div>`;
    }
  }
  if (data.tickers && data.tickers.length) {
    const track = document.getElementById("ticker-track");
    if (track) track.dataset.hasData = "1";
  }
}

function initTicker() {
  const track = document.getElementById("ticker-track");
  if (!track) return;
  pollTicker();
  setInterval(pollTicker, TICKER_REFRESH_MS);
}

document.addEventListener("DOMContentLoaded", () => {
  initContactModal();
  initTicker();
});
