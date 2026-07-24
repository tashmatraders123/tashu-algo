// Aurelius Algo — dashboard interactivity.
// Only runs the parts that apply to whichever page is loaded (guards
// check for element presence so this is safe to include everywhere).

(function () {
  const $ = (id) => document.getElementById(id);
  const logConsole = $('log-console');

  if (!logConsole) return; // not on the dashboard page

  const statusPill = $('status-pill');
  const symbolSelect = $('symbol');
  const fixedSizeInput = $('fixed_size');
  const liveTradingToggle = $('live_trading');
  const useTestnetToggle = $('use_testnet');
  const apiKeyInput = $('api_key');
  const apiSecretInput = $('api_secret');
  const currentKeyLabel = $('current-key-label');
  const btnStart = $('btn-start');
  const btnStop = $('btn-stop');
  const btnSaveSettings = $('btn-save-settings');
  const btnClearLog = $('btn-clear-log');
  const autoScrollCheckbox = $('auto-scroll');

  let logOffset = 0;
  let logLinesRendered = 0;

  function setStatusPill(running) {
    statusPill.classList.remove('running', 'stopped');
    statusPill.classList.add(running ? 'running' : 'stopped');
    statusPill.innerHTML = `<span class="dot"></span>${running ? 'Running' : 'Stopped'}`;
    btnStart.disabled = running;
    btnStop.disabled = !running;
  }

  function classifyLine(line) {
    if (line.includes('[ERROR]')) return 'error';
    if (line.includes('[WARNING]')) return 'warning';
    return 'info';
  }

  function appendLogLines(lines) {
    if (!lines.length) return;
    if (logLinesRendered === 0) logConsole.innerHTML = '';
    const atBottom = logConsole.scrollTop + logConsole.clientHeight >= logConsole.scrollHeight - 20;
    for (const line of lines) {
      const div = document.createElement('div');
      div.className = 'line ' + classifyLine(line);
      div.textContent = line;
      logConsole.appendChild(div);
      logLinesRendered++;
    }
    if (autoScrollCheckbox.checked && (atBottom || logLinesRendered === lines.length)) {
      logConsole.scrollTop = logConsole.scrollHeight;
    }
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
      currentKeyLabel.textContent = data.has_api_keys
        ? `Current key: ${data.api_key_masked}`
        : 'No API key saved yet';
    } catch (e) { /* network hiccup, try again next tick */ }
  }

  async function refreshLogs() {
    try {
      const res = await fetch(`/api/logs?since=${logOffset}`);
      if (!res.ok) return;
      const data = await res.json();
      logOffset = data.offset;
      appendLogLines(data.lines || []);
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
        apiKeyInput.value = '';
        apiSecretInput.value = '';
        btnSaveSettings.textContent = 'Saved';
        setTimeout(() => { btnSaveSettings.textContent = 'Save Settings'; }, 1500);
        refreshStatus();
      }
    } catch (e) {
      alert('Could not reach the server.');
    }
  });

  btnClearLog.addEventListener('click', () => {
    logConsole.innerHTML = '<div class="empty">Cleared — new lines will appear here.</div>';
    logLinesRendered = 0;
  });

  refreshStatus();
  refreshLogs();
  setInterval(refreshStatus, 3000);
  setInterval(refreshLogs, 1500);
})();
