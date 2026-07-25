// Aurelius Algo — dashboard interactivity.
// Only runs the parts that apply to whichever page is loaded (guards
// check for element presence so this is safe to include everywhere).

(function () {
  const $ = (id) => document.getElementById(id);
  const statusPill = $('status-pill');

  if (!statusPill) return; // not on the dashboard page

  const symbolSelect = $('symbol');
  const fixedSizeInput = $('fixed_size');
  const liveTradingToggle = $('live_trading');
  const useTestnetToggle = $('use_testnet');
  const apiKeyInput = $('api_key');
  const apiSecretInput = $('api_secret');
  const currentKeyLabel = $('current-key-label');
  const btnChangeKey = $('btn-change-key');
  const btnStart = $('btn-start');
  const btnStop = $('btn-stop');
  const btnSaveSettings = $('btn-save-settings');
  const btnCancelModal = $('btn-cancel-modal');
  const apiModalOverlay = $('api-modal-overlay');
  const toastStack = $('toast-stack');
  const tickerTrack = $('ticker-track');
  const positionDetails = $('position-details');
  const floatBalance = $('float-balance');
  const floatPnl = $('float-pnl');

  let hadApiKeys = false;
  let wasInPosition = false;
  let isFirstPositionFetch = true;

  // ------------------------------------------------------------
  // Toasts
  // ------------------------------------------------------------
  function showToast(kind, title, body) {
    if (!toastStack) return;
    const el = document.createElement('div');
    el.className = 'toast ' + kind;
    el.innerHTML = `<div class="toast-title">${title}</div><div class="toast-body">${body}</div>`;
    toastStack.appendChild(el);
    setTimeout(() => {
      el.classList.add('leaving');
      setTimeout(() => el.remove(), 320);
    }, 6000);
  }

  // ------------------------------------------------------------
  // Status (symbol / lot size / toggles / running state)
  // ------------------------------------------------------------
  function setStatusPill(running) {
    statusPill.classList.remove('running', 'stopped');
    statusPill.classList.add(running ? 'running' : 'stopped');
    statusPill.innerHTML = `<span class="dot"></span>${running ? 'Running' : 'Stopped'}`;
    btnStart.disabled = running;
    btnStop.disabled = !running;
  }

  async function refreshStatus() {
    try {
      const res = await fetch('/api/status');
      if (!res.ok) return;
      const data = await res.json();
      setStatusPill(data.running);
      if (document.activeElement !== symbolSelect) symbolSelect.value = data.symbol;
      if (document.activeElement !== fixedSizeInput) fixedSizeInput.value = data.fixed_size;
      liveTradingToggle.checked = !data.dry_run;
      useTestnetToggle.checked = data.use_testnet;
      hadApiKeys = data.has_api_keys;
      currentKeyLabel.textContent = data.has_api_keys ? data.api_key_masked : 'No API key saved yet';
      btnChangeKey.textContent = data.has_api_keys ? 'Change API Key' : 'Add API Key';
    } catch (e) { /* network hiccup, try again next tick */ }
  }

  btnStart.addEventListener('click', async () => {
    btnStart.disabled = true;
    try {
      const res = await fetch('/api/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          symbol: symbolSelect.value,
          live_trading: liveTradingToggle.checked,
          fixed_size: Number(fixedSizeInput.value || 0),
        }),
      });
      const data = await res.json();
      if (!data.ok) alert(data.error || 'Could not start bot');
    } catch (e) {
      alert('Could not reach the server.');
    }
    refreshStatus();
  });

  btnStop.addEventListener('click', async () => {
    btnStop.disabled = true;
    try {
      await fetch('/api/stop', { method: 'POST' });
    } catch (e) {
      alert('Could not reach the server.');
    }
    refreshStatus();
  });

  // ------------------------------------------------------------
  // API key modal
  // ------------------------------------------------------------
  function openModal() {
    apiKeyInput.value = '';
    apiSecretInput.value = '';
    apiModalOverlay.classList.remove('hidden');
  }
  function closeModal() {
    apiModalOverlay.classList.add('hidden');
  }

  btnChangeKey.addEventListener('click', openModal);
  btnCancelModal.addEventListener('click', closeModal);
  apiModalOverlay.addEventListener('click', (e) => {
    if (e.target === apiModalOverlay) closeModal();
  });

  btnSaveSettings.addEventListener('click', async () => {
    const payload = { use_testnet: useTestnetToggle.checked };
    if (apiKeyInput.value.trim()) payload.api_key = apiKeyInput.value.trim();
    if (apiSecretInput.value.trim()) payload.api_secret = apiSecretInput.value.trim();
    try {
      const res = await fetch('/api/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (data.ok) {
        closeModal();
        refreshStatus();
      } else {
        alert(data.error || 'Could not save settings.');
      }
    } catch (e) {
      alert('Could not reach the server.');
    }
  });

  // ------------------------------------------------------------
  // Live price ticker tape
  // ------------------------------------------------------------
  async function refreshTicker() {
    if (!tickerTrack) return;
    try {
      const res = await fetch('/api/market-ticker');
      if (!res.ok) return;
      const data = await res.json();
      if (!data.ok || !data.tickers || !data.tickers.length) return;
      const itemsHtml = data.tickers
        .map(t => `<span class="ticker-item"><span class="sym">${t.symbol}</span>${Number(t.price).toLocaleString(undefined, {maximumFractionDigits: 2})}</span>`)
        .join('');
      // Duplicated once for a seamless CSS marquee loop.
      tickerTrack.innerHTML = itemsHtml + itemsHtml;
    } catch (e) { /* network hiccup, try again next tick */ }
  }

  // ------------------------------------------------------------
  // Position details + floating P&L + trade-event toasts
  // ------------------------------------------------------------
  function fmtMoney(n) {
    if (n == null || isNaN(n)) return '—';
    const v = Number(n);
    return `${v >= 0 ? '+' : ''}$${v.toFixed(2)}`;
  }

  function renderPosition(data) {
    if (!data.has_position) {
      positionDetails.innerHTML = '<div class="empty-state">No open position. Waiting for a signal.</div>';
      return;
    }
    const side = (data.side || '').toLowerCase();
    const sideLabel = side === 'long' ? 'Long' : side === 'short' ? 'Short' : '—';
    const pnl = data.unrealized_pnl != null ? Number(data.unrealized_pnl) : null;
    const pnlClass = pnl == null ? '' : (pnl >= 0 ? 'pos' : 'neg');
    const rMult = data.r_multiple != null ? Number(data.r_multiple).toFixed(2) + 'R' : '—';
    const stageLabel = data.stage === 'trailing' ? 'Trailing stop' : data.stage === 'initial' ? 'Initial (1:2)' : '—';
    const openedAt = data.opened_at ? new Date(data.opened_at * 1000).toLocaleTimeString() : '—';

    positionDetails.innerHTML = `
      <div style="margin-bottom:16px;">
        <span class="side-badge ${side}">${sideLabel} · ${data.symbol || ''}</span>
      </div>
      <div class="position-grid">
        <div class="pos-item"><div class="label">Entry Price</div><div class="value">${data.entry_price != null ? Number(data.entry_price).toFixed(2) : '—'}</div></div>
        <div class="pos-item"><div class="label">Current Price</div><div class="value">${data.current_price != null ? Number(data.current_price).toFixed(2) : '—'}</div></div>
        <div class="pos-item"><div class="label">Stop / Trail</div><div class="value">${data.current_stop != null ? Number(data.current_stop).toFixed(2) : '—'}</div></div>
        <div class="pos-item"><div class="label">Size</div><div class="value">${data.size != null ? data.size : '—'}</div></div>
        <div class="pos-item"><div class="label">Stage</div><div class="value">${stageLabel}</div></div>
        <div class="pos-item"><div class="label">R-Multiple</div><div class="value">${rMult}</div></div>
        <div class="pos-item span-2"><div class="label">Unrealized P&amp;L</div><div class="value ${pnlClass}">${fmtMoney(pnl)}</div></div>
        <div class="pos-item span-2"><div class="label">Opened At</div><div class="value">${openedAt}</div></div>
      </div>
    `;
  }

  async function refreshPosition() {
    try {
      const res = await fetch('/api/position');
      if (!res.ok) return;
      const data = await res.json();

      if (!data.ok) {
        floatBalance.textContent = '—';
        floatPnl.textContent = '—';
        floatPnl.classList.remove('pos', 'neg');
        return;
      }

      floatBalance.textContent = data.balance != null ? `$${Number(data.balance).toFixed(2)}` : '—';
      if (data.has_position && data.unrealized_pnl != null) {
        const pnl = Number(data.unrealized_pnl);
        floatPnl.textContent = fmtMoney(pnl);
        floatPnl.classList.remove('pos', 'neg');
        floatPnl.classList.add(pnl >= 0 ? 'pos' : 'neg');
      } else {
        floatPnl.textContent = 'Flat';
        floatPnl.classList.remove('pos', 'neg');
      }

      renderPosition(data);

      // Trade-event popups: fire on has_position transitions, not on the
      // very first fetch after page load (that would just be the current
      // state, not a new event).
      if (!isFirstPositionFetch) {
        if (!wasInPosition && data.has_position) {
          const dir = (data.side || '').toUpperCase();
          showToast(
            (data.side || '').toLowerCase() === 'short' ? 'short' : 'long',
            `${dir} position opened`,
            `Entry ~${data.entry_price != null ? Number(data.entry_price).toFixed(2) : '—'} · size ${data.size ?? '—'}`
          );
        } else if (wasInPosition && !data.has_position) {
          showToast('closed', 'Position closed', 'Closed via 1:1 exit, SL/TP fill, or manual close.');
        }
      }
      wasInPosition = data.has_position;
      isFirstPositionFetch = false;
    } catch (e) { /* network hiccup, try again next tick */ }
  }

  refreshStatus();
  refreshTicker();
  refreshPosition();
  setInterval(refreshStatus, 3000);
  setInterval(refreshTicker, 6000);
  setInterval(refreshPosition, 3000);
})();
