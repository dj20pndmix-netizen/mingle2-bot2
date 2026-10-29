/**
 * Mingle2 Command Center & Multi-Task Automation Suite
 * Full-Sync Client Application Logic
 */

let ws = null;
let botState = 'STOPPED';
let queueItems = [];
let currentFilter = 'ALL';
let currentSearch = '';
let activeThreadUserId = null;
let activeThreadUserName = '';

// Document Ready Initialization
document.addEventListener('DOMContentLoaded', () => {
  if (window.lucide) {
    window.lucide.createIcons();
  }
  initWebSocket();
  loadAllData();

  // Tick countdowns every second
  setInterval(updateCountdowns, 1000);
});

// ============================================================================
// TAB NAVIGATION
// ============================================================================
function switchTab(tabId) {
  const tabs = ['dashboard', 'account', 'inbox', 'matches', 'search', 'online', 'tasks', 'diagnostics', 'console'];
  tabs.forEach(t => {
    const btn = document.getElementById(`tab-btn-${t}`);
    const content = document.getElementById(`tab-content-${t}`);
    if (btn) {
      if (t === tabId) {
        btn.classList.add('active', 'text-white');
        btn.classList.remove('text-slate-400');
      } else {
        btn.classList.remove('active', 'text-white');
        btn.classList.add('text-slate-400');
      }
    }
    if (content) {
      if (t === tabId) {
        content.classList.remove('hidden');
      } else {
        content.classList.add('hidden');
      }
    }
  });

  if (window.lucide) {
    window.lucide.createIcons();
  }

  // Load specific tab data on switch
  if (tabId === 'account') loadAccount();
  if (tabId === 'inbox') loadInbox();
  if (tabId === 'matches') loadQueue();
  if (tabId === 'online') loadOnlineUsers();
  if (tabId === 'tasks') loadTasks();
  if (tabId === 'diagnostics') runDiagnosticsCheck();
}

// ============================================================================
// WEBSOCKET REAL-TIME STREAM
// ============================================================================
function initWebSocket() {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const wsUrl = `${protocol}//${window.location.host}/ws`;

  ws = new WebSocket(wsUrl);

  ws.onopen = () => {
    appendConsoleLog({ level: 'SUCCESS', message: 'WebSocket connected to Mingle2 automation engine.', timestamp: new Date().toISOString() });
  };

  ws.onmessage = (event) => {
    try {
      const msg = JSON.parse(event.data);
      handleSocketEvent(msg);
    } catch (e) {
      console.error('Error parsing WS message:', e);
    }
  };

  ws.onclose = () => {
    appendConsoleLog({ level: 'WARN', message: 'WebSocket disconnected. Retrying in 4 seconds...', timestamp: new Date().toISOString() });
    setTimeout(initWebSocket, 4000);
  };

  ws.onerror = (err) => {
    console.warn('WS error:', err);
  };
}

function handleSocketEvent(payload) {
  const event = payload.event;
  const data = payload.data;

  if (payload.bot_state) {
    updateBotState(payload.bot_state);
  }

  switch (event) {
    case 'init_sync':
      updateBotState(payload.bot_state);
      if (payload.stats) updateStats(payload.stats);
      if (payload.account) renderAccount(payload.account);
      if (payload.tasks) renderTasks(payload.tasks);
      if (payload.recent_logs) {
        payload.recent_logs.forEach(appendConsoleLog);
      }
      if (payload.settings) {
        populateSettings(payload.settings);
        if (payload.settings.test_mode !== undefined) updateTestModeUI(payload.settings.test_mode);
      }
      break;

    case 'test_mode_changed':
      if (data && data.test_mode !== undefined) {
        updateTestModeUI(data.test_mode);
      }
      break;

    case 'settings_updated':
      if (data) {
        populateSettings(data);
        if (data.test_mode !== undefined) updateTestModeUI(data.test_mode);
      }
      break;

    case 'state_change':
      updateBotState(data.state);
      break;

    case 'session_state_changed':
    case 'session_updated':
      if (data) {
        updateSessionStatus(data.session_state || data.session_status, data.connection_status);
        if (data.browser_launched !== undefined) {
          const bStatus = document.getElementById('overview-browser-status');
          if (bStatus) {
            bStatus.innerText = data.browser_launched ? 'Running' : 'Not Launched';
            bStatus.className = data.browser_launched ? 'text-xs font-bold text-emerald-400 block' : 'text-xs font-bold text-slate-300 block';
          }
        }
        if (data.account) renderAccount(data.account);
      }
      break;

    case 'log_entry':
      appendConsoleLog(data);
      break;

    case 'stats_updated':
      updateStats(data);
      break;

    case 'new_liker_detected':
      showToast(`Detected new interested user: ${data.username}`, 'info');
      loadQueue();
      break;

    case 'queue_updated':
    case 'queue_refreshed':
      loadQueue();
      break;

    case 'task_created':
    case 'task_updated':
    case 'tasks_refreshed':
      loadTasks();
      break;

    case 'account_synced':
      renderAccount(data);
      showToast('Mingle2 Account profile updated.', 'success');
      break;

    case 'inbox_synced':
    case 'inbox_updated':
      renderThreads(data);
      break;

    case 'online_synced':
      renderOnlineUsers(data);
      break;

    case 'logs_cleared':
      document.getElementById('console-logs').innerHTML = '';
      break;
  }
}

// ============================================================================
// DATA LOADING
// ============================================================================
async function loadAllData() {
  await Promise.all([
    checkBotStatus(),
    loadAccount(),
    loadQueue(),
    loadTasks(),
  ]);
}

async function checkBotStatus() {
  try {
    const res = await fetch('/api/status');
    const data = await res.json();
    updateBotState(data.bot_state);
    updateStats(data.stats);
    updateSessionStatus(data.session_state || data.session_status, data.connection_status);

    const bStatus = document.getElementById('overview-browser-status');
    if (bStatus && data.browser_launched !== undefined) {
      bStatus.innerText = data.browser_launched ? 'Running' : 'Not Launched';
      bStatus.className = data.browser_launched ? 'text-xs font-bold text-emerald-400 block' : 'text-xs font-bold text-slate-300 block';
    }

    if (data.account_state && data.account_state.account) {
      const acc = data.account_state.account;
      const accElem = document.getElementById('overview-account-status');
      if (accElem) accElem.innerText = acc.username || 'Connected';
    } else if (data.account) {
      const accElem = document.getElementById('overview-account-status');
      if (accElem) accElem.innerText = data.account.username || 'Connected';
    }

    if (data.account) renderAccount(data.account);
    if (data.settings) populateSettings(data.settings);
  } catch (e) {
    console.error('Error fetching bot status:', e);
  }
}

async function checkAuthStatus() {
  try {
    showToast('Checking Mingle2 login status...', 'info');
    const res = await fetch('/api/auth/status');
    const data = await res.json();
    updateSessionStatus(data.session_state || data.session_status, data.connection_status);
    if (data.is_logged_in) {
      showToast('Authenticated! Mingle2 session is active: CONNECTED.', 'success');
    } else {
      showToast('Login required. Click "Open Browser for Login" to authenticate.', 'warning');
    }
  } catch (e) {
    showToast('Error checking login status.', 'error');
  }
}

function updateBotState(state) {
  botState = state;
  const badge = document.getElementById('bot-status-badge');
  const dot = document.getElementById('bot-status-dot');
  const text = document.getElementById('bot-status-text');
  const btnStart = document.getElementById('btn-start');
  const btnPause = document.getElementById('btn-pause');
  const btnStop = document.getElementById('btn-stop');
  const overviewAuto = document.getElementById('overview-automation-state');

  if (text) text.innerText = state;
  if (overviewAuto) overviewAuto.innerText = state;

  if (dot) dot.className = 'w-2 h-2 rounded-full';
  if (state === 'RUNNING') {
    if (dot) dot.classList.add('bg-emerald-400', 'pulse-running');
    if (badge) badge.className = 'inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-semibold bg-emerald-500/10 text-emerald-300 border border-emerald-500/30';
    if (btnStart) btnStart.classList.add('opacity-50', 'pointer-events-none');
    if (btnPause) btnPause.classList.remove('opacity-50', 'pointer-events-none');
    if (btnStop) btnStop.classList.remove('opacity-50', 'pointer-events-none');
  } else if (state === 'PAUSED') {
    if (dot) dot.classList.add('bg-amber-400', 'pulse-paused');
    if (badge) badge.className = 'inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-semibold bg-amber-500/10 text-amber-300 border border-amber-500/30';
    if (btnStart) btnStart.classList.remove('opacity-50', 'pointer-events-none');
    if (btnPause) btnPause.classList.add('opacity-50', 'pointer-events-none');
    if (btnStop) btnStop.classList.remove('opacity-50', 'pointer-events-none');
  } else {
    if (dot) dot.classList.add('bg-slate-400');
    if (badge) badge.className = 'inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-semibold bg-slate-800 text-slate-300 border border-slate-700';
    if (btnStart) btnStart.classList.remove('opacity-50', 'pointer-events-none');
    if (btnPause) btnPause.classList.remove('opacity-50', 'pointer-events-none');
    if (btnStop) btnStop.classList.add('opacity-50', 'pointer-events-none');
  }
}

function updateSessionStatus(sessionState, connectionStatus) {
  const badge = document.getElementById('session-status-badge');
  const dashBadge = document.getElementById('dashboard-connection-badge');
  const dashStateText = document.getElementById('dashboard-session-state-text');
  const dashInstructions = document.getElementById('dashboard-session-instructions');
  const overviewSession = document.getElementById('overview-session-verif');
  const overviewBrowser = document.getElementById('overview-browser-status');

  const stateStr = sessionState || 'LOGIN_REQUIRED';
  const connStr = connectionStatus || (
    stateStr === 'CONNECTED' || stateStr === 'SESSION_VERIFIED' ? 'CONNECTED' :
    stateStr === 'SESSION_EXPIRED' ? 'SESSION EXPIRED' : 'LOGIN REQUIRED'
  );

  if (dashStateText) dashStateText.innerText = `(${stateStr})`;

  if (dashBadge) {
    dashBadge.innerText = connStr;
    if (connStr === 'CONNECTED') {
      dashBadge.className = 'px-3 py-1 rounded-xl text-xs font-bold bg-emerald-500/20 text-emerald-300 border border-emerald-500/30';
      if (dashInstructions) dashInstructions.innerText = 'Connected and authenticated with Mingle2. Ready for automated operations.';
    } else if (connStr === 'SESSION EXPIRED') {
      dashBadge.className = 'px-3 py-1 rounded-xl text-xs font-bold bg-amber-500/20 text-amber-300 border border-amber-500/30';
      if (dashInstructions) dashInstructions.innerText = 'Session expired — login required. Click "Open Browser for Login" to reconnect.';
    } else if (stateStr === 'OPENING_BROWSER' || stateStr === 'WAITING_FOR_LOGIN') {
      dashBadge.className = 'px-3 py-1 rounded-xl text-xs font-bold bg-purple-500/20 text-purple-300 border border-purple-500/30 animate-pulse';
      if (dashInstructions) dashInstructions.innerText = stateStr === 'OPENING_BROWSER' ? 'Launching Chromium browser...' : 'Complete sign-in in the opened Mingle2 browser window...';
    } else {
      dashBadge.className = 'px-3 py-1 rounded-xl text-xs font-bold bg-rose-500/20 text-rose-300 border border-rose-500/30';
      if (dashInstructions) dashInstructions.innerText = 'Click "Open Browser for Login" to launch visible Chromium and sign in securely.';
    }
  }

  if (overviewSession) {
    if (connStr === 'CONNECTED' || stateStr === 'SESSION_VERIFIED') {
      overviewSession.innerText = 'VERIFIED';
      overviewSession.className = 'text-xs font-bold text-emerald-400 block';
    } else {
      overviewSession.innerText = 'NOT VERIFIED';
      overviewSession.className = 'text-xs font-bold text-rose-400 block';
    }
  }

  if (badge) {
    if (connStr === 'CONNECTED') {
      badge.innerText = 'CONNECTED';
      badge.className = 'px-2.5 py-1 rounded-lg text-xs font-bold bg-emerald-500/20 text-emerald-300 border border-emerald-500/30';
    } else if (connStr === 'SESSION EXPIRED') {
      badge.innerText = 'SESSION EXPIRED';
      badge.className = 'px-2.5 py-1 rounded-lg text-xs font-bold bg-amber-500/20 text-amber-300 border border-amber-500/30';
    } else {
      badge.innerText = 'LOGIN REQUIRED';
      badge.className = 'px-2.5 py-1 rounded-lg text-xs font-bold bg-rose-500/20 text-rose-300 border border-rose-500/30';
    }
  }
}

function updateStats(stats) {
  if (!stats) return;
  const pendingVal = stats.pending_count || 0;
  const likedVal = stats.liked_count || 0;
  const totalVal = stats.total_detected || 0;
  const failedVal = stats.failed_count || 0;
  const matchVal = stats.matches_count || stats.liked_count || 0;

  if (document.getElementById('metric-pending')) document.getElementById('metric-pending').innerText = pendingVal;
  if (document.getElementById('metric-liked')) document.getElementById('metric-liked').innerText = likedVal;
  if (document.getElementById('metric-total')) document.getElementById('metric-total').innerText = totalVal;
  if (document.getElementById('tab-badge-pending')) document.getElementById('tab-badge-pending').innerText = pendingVal;

  if (document.getElementById('overview-queue-pending')) document.getElementById('overview-queue-pending').innerText = pendingVal;
  if (document.getElementById('overview-completed-count')) document.getElementById('overview-completed-count').innerText = likedVal;
  if (document.getElementById('overview-failed-count')) document.getElementById('overview-failed-count').innerText = failedVal;
  if (document.getElementById('overview-matches-count')) document.getElementById('overview-matches-count').innerText = matchVal;

  if (document.getElementById('metric-inbox-threads')) {
    document.getElementById('metric-inbox-threads').innerText = stats.inbox_threads || 0;
  }
  if (document.getElementById('metric-inbox-unread')) {
    document.getElementById('metric-inbox-unread').innerText = stats.inbox_unread || 0;
  }

  const unreadBadge = document.getElementById('tab-badge-unread');
  if (unreadBadge) {
    if (stats.inbox_unread > 0) {
      unreadBadge.innerText = stats.inbox_unread;
      unreadBadge.classList.remove('hidden');
    } else {
      unreadBadge.classList.add('hidden');
    }
  }

  const tasksBadge = document.getElementById('tab-badge-tasks');
  if (tasksBadge) {
    const totalActive = (stats.running_tasks || 0) + (stats.pending_tasks || 0);
    if (totalActive > 0) {
      tasksBadge.innerText = totalActive;
      tasksBadge.classList.remove('hidden');
    } else {
      tasksBadge.classList.add('hidden');
    }
  }
}

// ============================================================================
// BOT ENGINE CONTROLS
// ============================================================================
async function startBot() {
  try {
    const res = await fetch('/api/bot/start', { method: 'POST' });
    const data = await res.json();
    updateBotState(data.bot_state);
    showToast('Automation bot started.', 'success');
  } catch (e) {
    showToast('Failed to start bot.', 'error');
  }
}

async function pauseBot() {
  try {
    const res = await fetch('/api/bot/pause', { method: 'POST' });
    const data = await res.json();
    updateBotState(data.bot_state);
    showToast('Automation bot paused.', 'warning');
  } catch (e) {
    showToast('Failed to pause bot.', 'error');
  }
}

async function triggerScan() {
  const icon = document.getElementById('matches-scan-icon');
  if (icon) icon.classList.add('animate-spin');
  try {
    showToast('Scanning Mingle2 for interested users...', 'info');
    const res = await fetch('/api/bot/scan', { method: 'POST' });
    const data = await res.json();
    if (data.status === 'started') {
      showToast('Scan initiated successfully.', 'success');
    } else {
      showToast(data.message || 'Scan busy.', 'warning');
    }
  } catch (e) {
    showToast('Failed to trigger scan.', 'error');
  } finally {
    if (icon) setTimeout(() => icon.classList.remove('animate-spin'), 1500);
  }
}

async function triggerFullSync() {
  const icon = document.getElementById('sync-all-icon');
  if (icon) icon.classList.add('animate-spin');
  try {
    showToast('Dispatching Full Platform Sync across all modules...', 'info');
    const res = await fetch('/api/sync/all', { method: 'POST' });
    const data = await res.json();
    showToast('Full Platform Sync queued!', 'success');
    loadTasks();
  } catch (e) {
    showToast('Failed to trigger full sync.', 'error');
  } finally {
    if (icon) setTimeout(() => icon.classList.remove('animate-spin'), 1500);
  }
}

let loginPollInterval = null;

async function openLoginBrowser() {
  try {
    showToast('Launching visible browser window for Mingle2 sign-in...', 'info');
    updateSessionStatus('OPENING_BROWSER', 'LOGIN REQUIRED');
    const res = await fetch('/api/browser/open-login', { method: 'POST' });
    const data = await res.json();
    if (data.status === 'cloud_warning') {
      showToast(data.message, 'warning', 8000);
      openCredentialsModal();
      return;
    }

    if (data.status === 'already_open') {
      showToast(data.message, 'warning');
    } else {
      showToast(data.message || 'Browser opened. Please sign in to Mingle2 in the window.', 'success');
    }

    updateSessionStatus(data.session_state || 'WAITING_FOR_LOGIN', 'LOGIN REQUIRED');

    // Poll until login is detected or browser closed
    if (loginPollInterval) clearInterval(loginPollInterval);
    loginPollInterval = setInterval(async () => {
      try {
        const stateRes = await fetch('/api/session/state');
        const stateData = await stateRes.json();
        updateSessionStatus(stateData.session_state, stateData.connection_status);

        const bStatus = document.getElementById('overview-browser-status');
        if (bStatus) {
          bStatus.innerText = stateData.browser_launched ? 'Running' : 'Not Launched';
          bStatus.className = stateData.browser_launched ? 'text-xs font-bold text-emerald-400 block' : 'text-xs font-bold text-slate-300 block';
        }

        if (stateData.is_authenticated) {
          clearInterval(loginPollInterval);
          loginPollInterval = null;
          showToast('Mingle2 login detected & verified! Bot is now CONNECTED.', 'success', 6000);
          if (stateData.account) renderAccount(stateData.account);
          checkBotStatus();
        } else if (stateData.session_state === 'LOGIN_REQUIRED' && !stateData.browser_launched) {
          clearInterval(loginPollInterval);
          loginPollInterval = null;
        }
      } catch (err) {
        console.debug('Login poll check error:', err);
      }
    }, 2500);

  } catch (e) {
    showToast('Failed to open login window: ' + e.message, 'error');
    updateSessionStatus('ERROR', 'LOGIN REQUIRED');
  }
}

async function verifySession() {
  const icon = document.getElementById('verify-session-icon');
  if (icon) icon.classList.add('animate-spin');
  try {
    showToast('Verifying active Mingle2 session...', 'info');
    const res = await fetch('/api/session/verify', { method: 'POST' });
    const data = await res.json();
    updateSessionStatus(data.session_state, data.connection_status);

    if (data.is_authenticated) {
      showToast(data.message || 'Session verified! CONNECTED', 'success');
    } else {
      showToast(data.message || 'Session expired. Login required.', 'warning');
    }

    if (data.account_state && data.account_state.account) {
      renderAccount(data.account_state.account);
    }
    checkBotStatus();
  } catch (e) {
    showToast('Failed to verify session: ' + e.message, 'error');
  } finally {
    if (icon) setTimeout(() => icon.classList.remove('animate-spin'), 1200);
  }
}

async function stopBot() {
  try {
    const res = await fetch('/api/bot/stop', { method: 'POST' });
    const data = await res.json();
    updateBotState(data.bot_state || 'STOPPED');
    showToast('Automation bot stopped.', 'info');
  } catch (e) {
    showToast('Failed to stop bot.', 'error');
  }
}

async function refreshAll() {
  const icon = document.getElementById('refresh-all-icon');
  if (icon) icon.classList.add('animate-spin');
  try {
    await loadAllData();
    showToast('All dashboard metrics refreshed.', 'success');
  } catch (e) {
    showToast('Error refreshing data.', 'error');
  } finally {
    if (icon) setTimeout(() => icon.classList.remove('animate-spin'), 1200);
  }
}

async function runDiagnosticsCheck() {
  const icon = document.getElementById('diag-refresh-icon');
  if (icon) icon.classList.add('animate-spin');
  try {
    const res = await fetch('/api/diagnostics');
    const data = await res.json();

    // 1. Checklist badges
    if (data.checklist) {
      const c = data.checklist;
      const setCheck = (id, val, isPass) => {
        const el = document.getElementById(id);
        if (!el) return;
        el.innerText = val;
        if (isPass) {
          el.className = 'px-2.5 py-0.5 rounded-lg text-xs font-extrabold bg-emerald-500/20 text-emerald-300 border border-emerald-500/30';
        } else {
          el.className = 'px-2.5 py-0.5 rounded-lg text-xs font-extrabold bg-slate-800 text-slate-400 border border-slate-700';
        }
      };

      setCheck('diag-check-browser-launched', c.browser_launched, c.browser_launched === 'YES');
      setCheck('diag-check-reachable', c.mingle2_reachable, c.mingle2_reachable === 'YES');
      setCheck('diag-check-login-detected', c.login_detected, c.login_detected === 'YES');
      setCheck('diag-check-session-verified', c.session_verified, c.session_verified === 'YES');
      setCheck('diag-check-profile-data', c.profile_data_accessible, c.profile_data_accessible === 'YES');
      setCheck('diag-check-who-liked-me', c.who_liked_me_accessible, c.who_liked_me_accessible === 'YES');
      setCheck('diag-check-identity-verif', c.profile_identity_verification, c.profile_identity_verification === 'PASS');
      setCheck('diag-check-like-verif', c.like_back_verification, c.like_back_verification === 'PASS');
      setCheck('diag-check-match-verif', c.match_verification, c.match_verification === 'PASS');

      let verifiedCount = 0;
      if (c.browser_launched === 'YES') verifiedCount++;
      if (c.mingle2_reachable === 'YES') verifiedCount++;
      if (c.login_detected === 'YES') verifiedCount++;
      if (c.session_verified === 'YES') verifiedCount++;
      if (c.profile_data_accessible === 'YES') verifiedCount++;
      if (c.who_liked_me_accessible === 'YES') verifiedCount++;
      if (c.profile_identity_verification === 'PASS') verifiedCount++;
      if (c.like_back_verification === 'PASS') verifiedCount++;
      if (c.match_verification === 'PASS') verifiedCount++;

      const summaryBadge = document.getElementById('checklist-summary-badge');
      if (summaryBadge) {
        summaryBadge.innerText = `${verifiedCount}/9 Verified`;
        summaryBadge.className = verifiedCount >= 6 ? 'text-xs font-mono text-emerald-400 font-bold' : 'text-xs font-mono text-amber-400 font-bold';
      }
    }

    // 2. Telemetry cards
    if (data.subsystems) {
      const s = data.subsystems;
      if (s.platform_session) {
        const el = document.getElementById('diag-session-status');
        if (el) el.innerText = s.platform_session.connection_status || s.platform_session.session_status;
        const det = document.getElementById('diag-session-details');
        if (det) det.innerText = s.platform_session.is_authenticated ? 'Session verified & active' : 'Login required';
      }
      if (s.queue_state_machine) {
        const el = document.getElementById('diag-queue-status');
        if (el) el.innerText = `${s.queue_state_machine.waiting} Waiting / ${s.queue_state_machine.completed} Liked`;
      }
    }

    const jsonEl = document.getElementById('diag-json-output');
    if (jsonEl) {
      jsonEl.innerText = JSON.stringify(data, null, 2);
    }
  } catch (e) {
    showToast('Failed to fetch diagnostics.', 'error');
  } finally {
    if (icon) setTimeout(() => icon.classList.remove('animate-spin'), 1200);
  }
}

async function testVerifyProfile() {
  const input = document.getElementById('verify-test-input');
  const resultDiv = document.getElementById('verify-test-result');
  const pid = (input ? input.value : '').trim();
  if (!pid) {
    showToast('Please enter a Mingle2 profile ID to test.', 'warning');
    return;
  }
  const btn = document.getElementById('btn-test-verify');
  if (btn) btn.disabled = true;

  try {
    showToast(`Verifying profile ID '${pid}' on Mingle2...`, 'info');
    const res = await fetch(`/api/profiles/${encodeURIComponent(pid)}/verify`);
    const data = await res.json();
    if (resultDiv) {
      resultDiv.classList.remove('hidden');
      if (data.success) {
        resultDiv.className = 'p-3 rounded-xl text-xs space-y-1 bg-emerald-500/10 border border-emerald-500/30 text-emerald-300';
        resultDiv.innerHTML = `<strong>PASS: Verified on Mingle2!</strong><br>${escapeHtml(data.message)}`;
      } else {
        resultDiv.className = 'p-3 rounded-xl text-xs space-y-1 bg-rose-500/10 border border-rose-500/30 text-rose-300';
        resultDiv.innerHTML = `<strong>FAIL: Verification rejected.</strong><br>${escapeHtml(data.message)}`;
      }
    }
  } catch (e) {
    showToast('Verification test error: ' + e.message, 'error');
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function uploadSessionZip(event) {
  const file = event.target.files[0];
  if (!file) return;

  const btnIcon = document.getElementById('upload-session-icon');
  if (btnIcon) btnIcon.classList.add('animate-spin');

  const formData = new FormData();
  formData.append('file', file);

  try {
    showToast(`Uploading session archive (${file.name})...`, 'info', 4000);
    const res = await fetch('/api/session/upload', {
      method: 'POST',
      body: formData,
    });
    const data = await res.json();

    if (res.ok && data.is_logged_in) {
      showToast(data.message || 'Session verified! Bot is now AUTHENTICATED.', 'success', 6000);
      updateSessionStatus('LOGGED_IN');
      if (data.account) renderAccount(data.account);
      loadDashboardStatus();
    } else {
      showToast(data.message || 'Upload complete, but cookies appear expired.', 'warning', 6000);
      updateSessionStatus('LOGGED_OUT');
    }
  } catch (e) {
    showToast('Failed to upload session archive: ' + e.message, 'error');
  } finally {
    if (btnIcon) btnIcon.classList.remove('animate-spin');
    event.target.value = '';
  }
}

// ============================================================================
// ACCOUNT MODULE
// ============================================================================
async function loadAccount() {
  try {
    const res = await fetch('/api/account');
    const data = await res.json();
    renderAccount(data);
  } catch (e) {
    console.error('Error loading account:', e);
  }
}

async function syncAccount() {
  const icon = document.getElementById('account-sync-icon');
  if (icon) icon.classList.add('animate-spin');
  try {
    showToast('Syncing profile from Mingle2...', 'info');
    const res = await fetch('/api/account/sync', { method: 'POST' });
    const data = await res.json();
    renderAccount(data.profile);
    showToast('Profile synced successfully!', 'success');
  } catch (e) {
    showToast('Error syncing account profile.', 'error');
  } finally {
    if (icon) icon.classList.remove('animate-spin');
  }
}

function renderAccount(profile) {
  if (!profile) return;
  const username = profile.username || 'Mingle2 User';
  document.getElementById('metric-account-name').innerText = username;
  document.getElementById('account-username').innerText = username;
  document.getElementById('account-location').innerText = profile.location || 'Location Not Specified';
  document.getElementById('account-id-badge').innerText = `ID: ${profile.user_id || 'my_account'}`;
  document.getElementById('account-headline').innerText = profile.headline || 'No headline set.';
  document.getElementById('account-bio').innerText = profile.about_me || 'No biography text found on profile.';

  if (profile.avatar_url) {
    document.getElementById('account-avatar').src = profile.avatar_url;
  }

  // Photos
  const gallery = document.getElementById('account-photos-gallery');
  if (gallery) {
    if (profile.photos && profile.photos.length > 0) {
      gallery.innerHTML = profile.photos.map(p => `
        <img src="${p}" alt="Photo" class="w-20 h-20 rounded-xl object-cover border border-white/10 hover:scale-105 transition shadow">
      `).join('');
    } else {
      gallery.innerHTML = '<span class="text-xs text-slate-500">No additional photos uploaded.</span>';
    }
  }
}

// ============================================================================
// MAILBOX / INBOX MODULE
// ============================================================================
async function loadInbox() {
  try {
    const res = await fetch('/api/inbox/threads');
    const data = await res.json();
    renderThreads(data.threads || []);
  } catch (e) {
    console.error('Error loading inbox threads:', e);
  }
}

async function syncInbox() {
  const icon = document.getElementById('inbox-sync-icon');
  if (icon) icon.classList.add('animate-spin');
  try {
    showToast('Syncing mailbox from Mingle2...', 'info');
    const res = await fetch('/api/inbox/sync', { method: 'POST' });
    const data = await res.json();
    renderThreads(data.threads || []);
    showToast(`Loaded ${data.threads ? data.threads.length : 0} conversation(s).`, 'success');
  } catch (e) {
    showToast('Error syncing inbox.', 'error');
  } finally {
    if (icon) icon.classList.remove('animate-spin');
  }
}

function renderThreads(threads) {
  const container = document.getElementById('threads-list');
  const countLabel = document.getElementById('threads-count-label');
  if (countLabel) countLabel.innerText = `${threads.length} threads`;

  if (!threads || threads.length === 0) {
    container.innerHTML = `
      <div class="p-8 text-center text-xs text-slate-500">
        No conversation threads found. Click "Refresh Inbox" to pull from Mingle2.
      </div>`;
    return;
  }

  container.innerHTML = threads.map(t => {
    const unreadDot = t.unread ? '<span class="w-2 h-2 rounded-full bg-pink-500"></span>' : '';
    const avatar = t.avatar_url || 'https://images.unsplash.com/photo-1535713875002-d1d0cf377fde?w=100';
    const isActive = activeThreadUserId === t.user_id ? 'bg-cyan-500/10 border-l-2 border-l-cyan-400' : 'hover:bg-slate-800/50';

    return `
      <div onclick="selectThread('${t.user_id}', '${escapeHtml(t.username)}', '${t.avatar_url || ''}')" class="p-3 flex items-start gap-3 cursor-pointer transition ${isActive}">
        <img src="${avatar}" alt="" class="w-10 h-10 rounded-xl object-cover border border-white/10 shrink-0">
        <div class="flex-1 min-w-0">
          <div class="flex items-center justify-between gap-1">
            <h5 class="text-xs font-bold text-white truncate flex items-center gap-1.5">
              <span>${escapeHtml(t.username)}</span>
              ${unreadDot}
            </h5>
            <span class="text-[10px] text-slate-500 shrink-0">${escapeHtml(t.last_message_time || '')}</span>
          </div>
          <p class="text-[11px] text-slate-400 truncate mt-0.5">${escapeHtml(t.last_message || 'Start chat...')}</p>
        </div>
      </div>
    `;
  }).join('');

  if (window.lucide) window.lucide.createIcons();
}

async function selectThread(userId, username, avatar) {
  activeThreadUserId = userId;
  activeThreadUserName = username;

  document.getElementById('chat-active-name').innerText = username;
  document.getElementById('chat-active-status').innerText = `ID: ${userId}`;
  const avatarWrap = document.getElementById('chat-active-avatar-wrap');
  if (avatar) {
    avatarWrap.innerHTML = `<img src="${avatar}" alt="" class="w-full h-full object-cover">`;
  } else {
    avatarWrap.innerHTML = `<i data-lucide="user" class="w-5 h-5 text-slate-400"></i>`;
  }

  document.getElementById('btn-sync-thread').classList.remove('hidden');
  document.getElementById('chat-input-text').disabled = false;
  document.getElementById('chat-btn-send').disabled = false;

  loadInbox(); // Refresh selection state
  await loadThreadMessages(userId);
}

async function loadThreadMessages(userId) {
  const container = document.getElementById('chat-messages-container');
  container.innerHTML = '<div class="h-full flex items-center justify-center text-xs text-slate-500">Loading chat history...</div>';

  try {
    const res = await fetch(`/api/inbox/${userId}/messages`);
    const data = await res.json();
    renderMessages(data.messages || []);
  } catch (e) {
    container.innerHTML = '<div class="text-center text-xs text-rose-400">Failed to load chat history.</div>';
  }
}

async function syncActiveThread() {
  if (!activeThreadUserId) return;
  showToast('Fetching latest messages from Mingle2...', 'info');
  try {
    const res = await fetch(`/api/inbox/${activeThreadUserId}/messages/sync`, { method: 'POST' });
    const data = await res.json();
    renderMessages(data.messages || []);
    showToast('Conversation updated.', 'success');
  } catch (e) {
    showToast('Failed to sync conversation messages.', 'error');
  }
}

function renderMessages(messages) {
  const container = document.getElementById('chat-messages-container');
  if (!messages || messages.length === 0) {
    container.innerHTML = `
      <div class="h-full flex flex-col items-center justify-center text-center text-xs text-slate-500">
        <i data-lucide="message-square-dashed" class="w-8 h-8 text-slate-700 mb-2"></i>
        <span>No messages stored yet. Send a direct message below!</span>
      </div>`;
    if (window.lucide) window.lucide.createIcons();
    return;
  }

  container.innerHTML = messages.map(m => {
    const isMe = m.sender === 'You';
    const alignClass = isMe ? 'justify-end' : 'justify-start';
    const bubbleClass = isMe ? 'chat-bubble-me' : 'chat-bubble-them';

    return `
      <div class="flex ${alignClass}">
        <div class="max-w-[75%] p-3 text-xs ${bubbleClass} space-y-1">
          <p class="leading-relaxed">${escapeHtml(m.message_body)}</p>
          <div class="flex items-center justify-end gap-1 text-[10px] opacity-75">
            <span>${escapeHtml(m.sent_at || '')}</span>
          </div>
        </div>
      </div>
    `;
  }).join('');

  // Scroll to bottom
  container.scrollTop = container.scrollHeight;
}

async function sendChatMessage(e) {
  e.preventDefault();
  if (!activeThreadUserId) return;

  const input = document.getElementById('chat-input-text');
  const text = input.value.trim();
  if (!text) return;

  input.value = '';
  showToast(`Sending message to ${activeThreadUserName}...`, 'info');

  try {
    const res = await fetch(`/api/inbox/${activeThreadUserId}/send`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: text })
    });
    const data = await res.json();
    if (res.ok) {
      showToast('Message sent successfully!', 'success');
      if (data.chat) renderMessages(data.chat);
      loadInbox();
    } else {
      showToast(data.detail || 'Failed to send message.', 'error');
    }
  } catch (err) {
    showToast('Network error sending message.', 'error');
  }
}

// ============================================================================
// MUTUAL MATCHES & LIKES QUEUE
// ============================================================================
async function loadQueue() {
  try {
    const params = new URLSearchParams();
    if (currentFilter && currentFilter !== 'ALL') params.append('status', currentFilter);
    if (currentSearch) params.append('search', currentSearch);

    const res = await fetch(`/api/queue?${params.toString()}`);
    const data = await res.json();
    queueItems = data.items || [];
    renderQueueTable(queueItems);
  } catch (e) {
    console.error('Error loading queue:', e);
  }
}

function filterQueue(status) {
  currentFilter = status;
  document.querySelectorAll('.queue-filter-btn').forEach(btn => {
    if (btn.dataset.filter === status) {
      btn.className = 'queue-filter-btn active px-3 py-1 rounded-lg text-xs font-semibold bg-pink-500/20 text-pink-300 border border-pink-500/30';
    } else {
      btn.className = 'queue-filter-btn px-3 py-1 rounded-lg text-xs font-semibold bg-slate-800 text-slate-400 hover:text-white';
    }
  });
  loadQueue();
}

function searchQueue(val) {
  currentSearch = val.trim();
  loadQueue();
}

function renderQueueTable(items) {
  const tbody = document.getElementById('queue-table-body');
  if (!tbody) return;

  if (items.length === 0) {
    tbody.innerHTML = `
      <tr>
        <td colspan="6" class="p-8 text-center text-slate-500">
          No matches found for active filter. Click "Scan Likers" to search Mingle2.
        </td>
      </tr>`;
    return;
  }

  tbody.innerHTML = items.map(u => {
    const avatar = u.avatar_img_url || 'https://images.unsplash.com/photo-1534528741775-53994a69daeb?w=100';
    const isFanPreview = u.is_fan_preview || String(u.user_id).startsWith('fan_preview_') || u.status === 'IDENTITY_FAILED';
    const profileLink = isFanPreview ? 'https://mingle2.com/mutual_match/who_is_interested_in_me' : (u.profile_url || `https://mingle2.com/users/view/${u.user_id}`);

    const statusBadges = {
      WAITING: '<span class="px-2 py-0.5 rounded-full text-[10px] font-bold bg-cyan-500/15 text-cyan-300 border border-cyan-500/30">WAITING</span>',
      READY: '<span class="px-2 py-0.5 rounded-full text-[10px] font-bold bg-amber-500/15 text-amber-300 border border-amber-500/30">READY</span>',
      EXECUTING: '<span class="px-2 py-0.5 rounded-full text-[10px] font-bold bg-purple-500/15 text-purple-300 border border-purple-500/30 inline-flex items-center gap-1"><span class="w-1.5 h-1.5 rounded-full bg-purple-400 animate-ping"></span>EXECUTING</span>',
      VERIFYING: '<span class="px-2 py-0.5 rounded-full text-[10px] font-bold bg-indigo-500/15 text-indigo-300 border border-indigo-500/30">VERIFYING</span>',
      COMPLETED: '<span class="px-2 py-0.5 rounded-full text-[10px] font-bold bg-emerald-500/15 text-emerald-300 border border-emerald-500/30" title="Platform Verified">COMPLETED ✓</span>',
      LIKED: '<span class="px-2 py-0.5 rounded-full text-[10px] font-bold bg-emerald-500/15 text-emerald-300 border border-emerald-500/30">COMPLETED ✓</span>',
      IDENTITY_FAILED: `<span class="px-2 py-0.5 rounded-full text-[10px] font-bold bg-rose-500/15 text-rose-300 border border-rose-500/30" title="${escapeHtml(u.error_message || 'Blocked')}">IDENTITY BLOCKED</span>`,
      VERIFICATION_FAILED: `<span class="px-2 py-0.5 rounded-full text-[10px] font-bold bg-orange-500/15 text-orange-300 border border-orange-500/30" title="${escapeHtml(u.error_message || 'Unverified')}">UNVERIFIED</span>`,
      FAILED: '<span class="px-2 py-0.5 rounded-full text-[10px] font-bold bg-rose-500/10 text-rose-300 border border-rose-500/20">FAILED</span>',
      EXECUTION_FAILED: '<span class="px-2 py-0.5 rounded-full text-[10px] font-bold bg-rose-500/10 text-rose-300 border border-rose-500/20">FAILED</span>',
      TEMPORARY_ERROR: '<span class="px-2 py-0.5 rounded-full text-[10px] font-bold bg-amber-500/10 text-amber-300 border border-amber-500/20">TEMP ERROR</span>',
      PERMANENT_ERROR: '<span class="px-2 py-0.5 rounded-full text-[10px] font-bold bg-rose-500/20 text-rose-400 border border-rose-500/40">ERROR</span>',
      SKIPPED: '<span class="px-2 py-0.5 rounded-full text-[10px] font-bold bg-slate-500/10 text-slate-400 border border-slate-500/20">SKIPPED</span>',
      PENDING: '<span class="px-2 py-0.5 rounded-full text-[10px] font-bold bg-cyan-500/10 text-cyan-300 border border-cyan-500/20">WAITING</span>',
    };
    const badge = statusBadges[u.status] || `<span class="px-2 py-0.5 rounded-full text-[10px] font-bold bg-slate-800 text-slate-400">${u.status}</span>`;

    const isPendingLike = ['WAITING', 'READY', 'QUEUED', 'PENDING'].includes(u.status);
    let actions = '';
    if (isPendingLike) {
      actions = `
        <button onclick="triggerLikeNow('${u.user_id}')" class="px-2 py-1 rounded bg-pink-500/20 hover:bg-pink-500/30 text-pink-300 border border-pink-500/30 text-[11px] font-semibold transition mr-1">
          Like Now
        </button>
        <button onclick="cancelQueueItem('${u.user_id}')" class="px-2 py-1 rounded bg-slate-800 hover:bg-slate-700 text-slate-400 text-[11px] transition">
          Cancel
        </button>
      `;
    } else {
      actions = `
        <button onclick="deleteQueueItem('${u.user_id}')" class="p-1 rounded hover:bg-slate-800 text-slate-500 hover:text-rose-400 transition" title="Delete">
          <i data-lucide="trash" class="w-3.5 h-3.5"></i>
        </button>
      `;
    }

    return `
      <tr class="hover:bg-slate-800/30 transition">
        <td class="p-3 flex items-center gap-3">
          <img src="${avatar}" class="w-9 h-9 rounded-xl object-cover border border-white/10 shrink-0">
          <div>
            <a href="${profileLink}" target="_blank" rel="noopener noreferrer" class="font-bold text-white hover:text-pink-400 flex items-center gap-1" title="${isFanPreview ? 'Paywalled Fan Card' : 'Open Profile on Mingle2'}">
              ${escapeHtml(u.username)}
              <i data-lucide="external-link" class="w-3 h-3 text-slate-500"></i>
            </a>
            <span class="text-[11px] text-slate-400">${escapeHtml(u.age_location || 'Age/Location N/A')}</span>
          </div>
        </td>
        <td class="p-3 text-slate-400 font-mono text-[11px]">${formatTime(u.detected_at)}</td>
        <td class="p-3 text-slate-400 font-mono text-[11px]">${formatTime(u.scheduled_like_at)}</td>
        <td class="p-3 font-mono text-xs">
          <span class="countdown-timer" data-scheduled="${u.scheduled_like_at}" data-status="${u.status}">
            ${isPendingLike ? 'Calculating...' : '—'}
          </span>
        </td>
        <td class="p-3">${badge}</td>
        <td class="p-3 text-right whitespace-nowrap">${actions}</td>
      </tr>
    `;
  }).join('');

  if (window.lucide) window.lucide.createIcons();
}

async function triggerLikeNow(userId) {
  try {
    showToast('Triggering immediate like...', 'info');
    const res = await fetch(`/api/queue/${userId}/like-now`, { method: 'POST' });
    const data = await res.json();
    showToast(data.message, 'success');
  } catch (e) {
    showToast('Failed to trigger immediate like.', 'error');
  }
}

async function cancelQueueItem(userId) {
  try {
    const res = await fetch(`/api/queue/${userId}/cancel`, { method: 'POST' });
    const data = await res.json();
    showToast(data.message, 'info');
    loadQueue();
  } catch (e) {
    showToast('Failed to cancel like.', 'error');
  }
}

async function deleteQueueItem(userId) {
  try {
    const res = await fetch(`/api/queue/${userId}`, { method: 'DELETE' });
    if (res.ok) {
      showToast('Record deleted.', 'info');
      loadQueue();
    }
  } catch (e) {
    showToast('Error deleting item.', 'error');
  }
}

async function simulateIncomingLike() {
  const names = ['Jessica_R', 'Elena_V', 'Sofia_K', 'Amber_Rose', 'Chloe_B', 'Natalie_M'];
  const name = names[Math.floor(Math.random() * names.length)] + '_' + Math.floor(Math.random() * 999);
  const ages = ['23, Miami, FL', '26, New York, NY', '29, Los Angeles, CA', '24, Austin, TX'];
  const loc = ages[Math.floor(Math.random() * ages.length)];

  try {
    const res = await fetch('/api/queue/simulate-liker', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: name, age_location: loc })
    });
    const data = await res.json();
    showToast(`Simulated incoming like from ${name}!`, 'success');
    loadQueue();
  } catch (e) {
    showToast('Error simulating liker.', 'error');
  }
}

async function clearSimulatedLikes() {
  try {
    const res = await fetch('/api/queue/clear-simulated', { method: 'POST' });
    const data = await res.json();
    if (res.ok) {
      showToast(data.message || 'Cleared simulated test items', 'success');
      loadQueue();
      if (typeof loadStats === 'function') loadStats();
    } else {
      showToast(data.detail || 'Failed to clear test items', 'error');
    }
  } catch (e) {
    showToast('Error clearing test items.', 'error');
  }
}

function setSearchProfession(prof) {
  const input = document.getElementById('search-profession');
  if (input) {
    input.value = prof;
    input.focus();
  }
}

function applyTargetPreset(preset) {
  const lookingFor = document.getElementById('search-looking-for');
  const ageFrom = document.getElementById('search-age-from');
  const ageTo = document.getElementById('search-age-to');
  const profession = document.getElementById('search-profession');
  const keywords = document.getElementById('search-keywords');
  const deepMatch = document.getElementById('search-deep-match');

  if (lookingFor) lookingFor.value = 'f';
  if (ageFrom) ageFrom.value = '18';
  if (ageTo) ageTo.value = '25';
  if (deepMatch) deepMatch.checked = true;

  if (preset === 'nurse_young') {
    if (profession) profession.value = 'Nurse';
    if (keywords) keywords.value = 'nursing, RN, hospital, clinic';
    showToast('Applied Preset: Young Nurses (18-25)', 'info');
  } else if (preset === 'brown_ladies') {
    if (profession) profession.value = '';
    if (keywords) keywords.value = 'brown, brunette, latina, tan, olive';
    showToast('Applied Preset: Brown Ladies (18-25)', 'info');
  } else if (preset === 'young_ladies') {
    if (profession) profession.value = '';
    if (keywords) keywords.value = 'young, student, cute, petite';
    showToast('Applied Preset: Young Ladies (18-25)', 'info');
  } else if (preset === 'all_favorites') {
    if (profession) profession.value = 'Nurse';
    if (keywords) keywords.value = 'nurse, brown, young, student';
    showToast('Applied Preset: Nurses & Brown Ladies (18-25)', 'info');
  }

  // Trigger search immediately
  const searchForm = document.getElementById('search-form');
  if (searchForm) {
    searchForm.dispatchEvent(new Event('submit', { cancelable: true }));
  }
}

// ============================================================================
// SEARCH (BASIC & ADVANCED)
// ============================================================================
async function executeSearch(e) {
  e.preventDefault();
  const btn = document.getElementById('btn-run-search');
  btn.disabled = true;
  btn.innerHTML = `<i data-lucide="refresh-cw" class="w-4 h-4 animate-spin"></i><span>Searching...</span>`;

  const profInput = document.getElementById('search-profession');
  const locInput = document.getElementById('search-location');
  const deepMatchInput = document.getElementById('search-deep-match');

  const payload = {
    looking_for: document.getElementById('search-looking-for').value,
    age_from: parseInt(document.getElementById('search-age-from').value) || 18,
    age_to: parseInt(document.getElementById('search-age-to').value) || 99,
    country_id: document.getElementById('search-country').value || null,
    profession: profInput ? profInput.value.trim() || null : null,
    location: locInput ? locInput.value.trim() || null : null,
    keywords: document.getElementById('search-keywords').value.trim() || null,
    deep_search: deepMatchInput ? deepMatchInput.checked : true,
  };

  showToast('Executing search query on Mingle2...', 'info');

  try {
    const res = await fetch('/api/search', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    const data = await res.json();
    renderSearchResults(data.results || []);
    showToast(`Found ${data.count || 0} matching profile(s).`, 'success');
  } catch (err) {
    showToast('Search request failed.', 'error');
  } finally {
    btn.disabled = false;
    btn.innerHTML = `<i data-lucide="search" class="w-4 h-4"></i><span>Execute Search</span>`;
    if (window.lucide) window.lucide.createIcons();
  }
}

function renderSearchResults(results) {
  const container = document.getElementById('search-results-grid');
  const countLabel = document.getElementById('search-results-count');
  if (countLabel) countLabel.innerText = `${results.length} profiles`;

  if (!results || results.length === 0) {
    container.innerHTML = `
      <div class="col-span-full py-12 text-center text-xs text-slate-500">
        No matching profiles found on Mingle2 for these search criteria.
      </div>`;
    return;
  }

  container.innerHTML = results.map(u => {
    const avatar = u.photo_reference || u.avatar_url || 'https://images.unsplash.com/photo-1534528741775-53994a69daeb?w=200';
    const profileUrl = u.profile_url || `https://mingle2.com/users/view/${u.user_id || u.platform_profile_id}`;
    const matchBadge = u.total_match_score !== undefined
      ? `<span class="px-2 py-0.5 rounded-full text-[10px] font-bold bg-amber-500/20 text-amber-300 border border-amber-500/30 font-mono">${u.total_match_score}% Match</span>`
      : '';
    const profSnippet = u.detected_profession_match
      ? `<div class="text-[11px] text-pink-400 font-semibold truncate flex items-center gap-1"><i data-lucide="check" class="w-3 h-3"></i> ${escapeHtml(u.detected_profession_match)} (${u.profession_score || 0}%)</div>`
      : (u.profession ? `<div class="text-[11px] text-slate-300 truncate">${escapeHtml(u.profession)}</div>` : '');

    return `
      <div class="glass-panel glass-panel-hover rounded-2xl p-3 flex flex-col justify-between space-y-3" title="${escapeHtml(u.score_breakdown || '')}">
        <div class="relative">
          <img src="${avatar}" alt="" class="w-full h-40 rounded-xl object-cover border border-white/10">
          <div class="absolute top-2 right-2 flex flex-col items-end gap-1">
            ${u.is_online ? '<span class="px-2 py-0.5 rounded-full text-[10px] font-bold bg-emerald-500 text-white flex items-center gap-1 shadow-md"><span class="w-1.5 h-1.5 rounded-full bg-white animate-ping"></span>Online</span>' : ''}
            ${matchBadge}
          </div>
        </div>
        <div class="space-y-1">
          <div class="flex items-center justify-between">
            <h4 class="text-xs font-bold text-white truncate">${escapeHtml(u.display_name || u.username)}</h4>
            ${u.age ? `<span class="text-[11px] text-slate-400">${u.age}</span>` : ''}
          </div>
          <p class="text-[11px] text-slate-400 truncate">${escapeHtml(u.location || u.age_location || '')}</p>
          ${profSnippet}
        </div>
        <div class="flex items-center gap-1.5 pt-1 border-t border-white/5">
          <button onclick="likeDirectUser('${u.user_id || u.platform_profile_id}', '${escapeHtml(u.display_name || u.username)}', '${profileUrl}')" class="flex-1 py-1.5 rounded-lg bg-pink-600 hover:bg-pink-500 text-white text-[11px] font-bold transition flex items-center justify-center gap-1 shadow-md shadow-pink-600/10">
            <i data-lucide="heart" class="w-3 h-3"></i>
            <span>Like</span>
          </button>
          <a href="${profileUrl}" target="_blank" rel="noopener noreferrer" class="p-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-300 hover:text-white border border-slate-700 transition" title="Open Canonical Profile on Mingle2">
            <i data-lucide="external-link" class="w-3.5 h-3.5"></i>
          </a>
          <button onclick="openMessageDirect('${u.user_id || u.platform_profile_id}', '${escapeHtml(u.display_name || u.username)}', '${avatar}')" class="p-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-cyan-400 border border-slate-700 transition" title="Send Direct Message">
            <i data-lucide="mail" class="w-3.5 h-3.5"></i>
          </button>
        </div>
      </div>
    `;
  }).join('');

  if (window.lucide) window.lucide.createIcons();
}

async function likeDirectUser(userId, username, profileUrl) {
  showToast(`Initiating like for ${username}...`, 'info');
  try {
    const res = await fetch(`/api/queue/${userId}/like-now`, { method: 'POST' });
    const data = await res.json();
    showToast(`Liked ${username}!`, 'success');
  } catch (e) {
    showToast('Failed to like user.', 'error');
  }
}

function openMessageDirect(userId, username, avatar) {
  switchTab('inbox');
  selectThread(userId, username, avatar);
}

// ============================================================================
// WHO'S ONLINE MODULE
// ============================================================================
let allCachedOnlineUsers = [];
let currentOnlineFilter = 'ALL';

function filterOnlineGrid(filter) {
  currentOnlineFilter = filter;
  document.querySelectorAll('.online-filter-btn').forEach(btn => {
    if (btn.getAttribute('data-filter') === filter) {
      btn.className = 'online-filter-btn active px-3 py-1 rounded-lg text-xs font-semibold bg-emerald-500/20 text-emerald-300 border border-emerald-500/30 transition';
    } else {
      btn.className = 'online-filter-btn px-3 py-1 rounded-lg text-xs font-semibold text-slate-400 hover:text-white transition';
    }
  });
  renderOnlineUsers(allCachedOnlineUsers);
}

async function likeFilteredOnlineUsers() {
  const visibleCards = document.querySelectorAll('.online-user-card-item');
  if (!visibleCards || visibleCards.length === 0) {
    showToast('No filtered online users to like.', 'warn');
    return;
  }
  const count = visibleCards.length;
  showToast(`Starting automated likes for ${count} filtered online profile(s)...`, 'info');
  for (const card of visibleCards) {
    const uid = card.getAttribute('data-user-id');
    const uname = card.getAttribute('data-username');
    const purl = card.getAttribute('data-profile-url');
    if (uid) {
      await likeDirectUser(uid, uname, purl);
      await new Promise(r => setTimeout(r, 1500));
    }
  }
  showToast(`Finished liking filtered profiles!`, 'success');
}

async function loadOnlineUsers() {
  try {
    const res = await fetch('/api/online');
    const data = await res.json();
    renderOnlineUsers(data.users || []);
  } catch (e) {
    console.error('Error loading online users:', e);
  }
}

async function syncOnlineUsers() {
  const icon = document.getElementById('online-sync-icon');
  if (icon) icon.classList.add('animate-spin');
  try {
    showToast('Scanning active online users on Mingle2...', 'info');
    const res = await fetch('/api/online/sync', { method: 'POST' });
    const data = await res.json();
    renderOnlineUsers(data.users || []);
    showToast(`Found ${data.count || 0} active online user(s).`, 'success');
  } catch (e) {
    showToast('Failed to sync online users.', 'error');
  } finally {
    if (icon) icon.classList.remove('animate-spin');
  }
}

function renderOnlineUsers(users) {
  if (users) {
    allCachedOnlineUsers = users;
  } else {
    users = allCachedOnlineUsers;
  }
  const container = document.getElementById('online-users-grid');
  if (!container) return;

  let filtered = [...users];
  if (currentOnlineFilter === 'FEMALES') {
    filtered = filtered.filter(u => {
      const g = (u.gender || '').toUpperCase();
      const meta = (u.age_location || '').toLowerCase();
      return g === 'F' || /\b(?:female|woman|girl|f)\b/i.test(meta);
    });
  } else if (currentOnlineFilter === 'FEMALES_18_25') {
    filtered = filtered.filter(u => {
      const g = (u.gender || '').toUpperCase();
      const meta = (u.age_location || '').toLowerCase();
      const isFemale = g === 'F' || /\b(?:female|woman|girl|f)\b/i.test(meta);
      let age = u.age;
      if (!age) {
        const m = meta.match(/(?:^|[,\s•])(\d{2})(?:[,\s•]|$)/);
        if (m) age = parseInt(m[1]);
      }
      const ageMatch = !age || (age >= 18 && age <= 25);
      return isFemale && ageMatch;
    });
  } else if (currentOnlineFilter === 'NURSES') {
    filtered = filtered.filter(u => {
      const text = ((u.age_location || '') + ' ' + (u.username || '')).toLowerCase();
      return /\b(?:nurse|rn|lpn|nursing|clinic|hospital)\b/i.test(text);
    });
  }

  if (!filtered || filtered.length === 0) {
    container.innerHTML = `
      <div class="col-span-full py-12 text-center text-xs text-slate-500">
        No online users found matching filter: <span class="text-emerald-400 font-semibold">${currentOnlineFilter}</span>. Click "Refresh Online" to update.
      </div>`;
    return;
  }

  container.innerHTML = filtered.map(u => {
    const avatar = u.avatar_url || 'https://images.unsplash.com/photo-1517841905240-472988babdf9?w=200';
    return `
      <div class="online-user-card-item glass-panel glass-panel-hover rounded-2xl p-3 flex flex-col justify-between space-y-3"
           data-user-id="${u.user_id}" data-username="${escapeHtml(u.username)}" data-profile-url="${u.profile_url}">
        <div class="relative">
          <img src="${avatar}" alt="" class="w-full h-40 rounded-xl object-cover border border-white/10">
          <span class="absolute top-2 right-2 px-2 py-0.5 rounded-full text-[10px] font-bold bg-emerald-500 text-white flex items-center gap-1 shadow-lg shadow-emerald-500/30">
            <span class="w-1.5 h-1.5 rounded-full bg-white animate-pulse"></span>
            Active
          </span>
          ${u.gender === 'F' ? '<span class="absolute bottom-2 left-2 px-2 py-0.5 rounded-full text-[10px] font-bold bg-pink-600/80 text-white backdrop-blur-sm">Female</span>' : ''}
        </div>
        <div>
          <h4 class="text-xs font-bold text-white truncate">${escapeHtml(u.username)}</h4>
          <p class="text-[11px] text-slate-400 truncate mt-0.5">${escapeHtml(u.age_location || 'Online Member')}</p>
        </div>
        <div class="flex items-center gap-1.5 pt-1 border-t border-white/5">
          <button onclick="likeDirectUser('${u.user_id}', '${escapeHtml(u.username)}', '${u.profile_url}')" class="flex-1 py-1.5 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white text-[11px] font-bold transition flex items-center justify-center gap-1">
            <i data-lucide="heart" class="w-3 h-3"></i>
            <span>Like</span>
          </button>
          <button onclick="openMessageDirect('${u.user_id}', '${escapeHtml(u.username)}', '${u.avatar_url || ''}')" class="p-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-cyan-400 border border-slate-700 transition" title="Message">
            <i data-lucide="mail" class="w-3.5 h-3.5"></i>
          </button>
        </div>
      </div>
    `;
  }).join('');

  if (window.lucide) window.lucide.createIcons();
}

// ============================================================================
// TASK AUTOMATION HUB (DISPATCHER)
// ============================================================================
async function loadTasks() {
  try {
    const res = await fetch('/api/tasks');
    const data = await res.json();
    renderTasks(data.tasks || []);
  } catch (e) {
    console.error('Error loading tasks:', e);
  }
}

async function quickLaunchTask(taskType, params = {}) {
  try {
    showToast(`Dispatching autonomous task: ${taskType}...`, 'info');
    const res = await fetch('/api/tasks/create', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ task_type: taskType, params: params, total_steps: 10 })
    });
    const data = await res.json();
    showToast(`Task #${data.task.id} queued successfully!`, 'success');
    loadTasks();
  } catch (e) {
    showToast('Failed to dispatch task.', 'error');
  }
}

async function createCustomTask(e) {
  e.preventDefault();
  const type = document.getElementById('task-type-select').value;
  const limit = parseInt(document.getElementById('task-limit-input').value) || 10;

  await quickLaunchTask(type, { limit: limit });
}

async function cancelTask(taskId) {
  try {
    showToast(`Cancelling task #${taskId}...`, 'info');
    const res = await fetch(`/api/tasks/${taskId}/cancel`, { method: 'POST' });
    const data = await res.json();
    showToast(data.message || 'Task cancelled.', 'warning');
    loadTasks();
  } catch (e) {
    showToast('Failed to cancel task.', 'error');
  }
}

async function clearCompletedTasks() {
  try {
    const res = await fetch('/api/tasks/clear', { method: 'POST' });
    const data = await res.json();
    showToast(data.message || 'Tasks cleared.', 'info');
    loadTasks();
  } catch (e) {
    showToast('Failed to clear tasks.', 'error');
  }
}

async function clearAllTasks() {
  if (!confirm('Are you sure you want to reset all tasks in the queue?')) return;
  try {
    showToast('Resetting all tasks in queue...', 'info');
    const res = await fetch('/api/tasks/clear-all', { method: 'POST' });
    const data = await res.json();
    showToast(data.message || 'All tasks cleared.', 'success');
    loadTasks();
  } catch (e) {
    showToast('Failed to reset tasks.', 'error');
  }
}

function renderTasks(tasks) {
  // Render in Dispatcher Table
  const tbody = document.getElementById('tasks-table-body');
  if (tbody) {
    if (!tasks || tasks.length === 0) {
      tbody.innerHTML = `
        <tr>
          <td colspan="7" class="p-8 text-center text-slate-500">
            No automation tasks in queue. Dispatch a task above!
          </td>
        </tr>`;
    } else {
      tbody.innerHTML = tasks.map(t => {
        const statusColors = {
          PENDING: '<span class="px-2 py-0.5 rounded-full text-[10px] font-bold bg-amber-500/10 text-amber-300 border border-amber-500/20">PENDING</span>',
          RUNNING: '<span class="px-2 py-0.5 rounded-full text-[10px] font-bold bg-indigo-500/20 text-indigo-300 border border-indigo-500/40 animate-pulse">RUNNING</span>',
          COMPLETED: '<span class="px-2 py-0.5 rounded-full text-[10px] font-bold bg-emerald-500/10 text-emerald-300 border border-emerald-500/20">COMPLETED</span>',
          FAILED: '<span class="px-2 py-0.5 rounded-full text-[10px] font-bold bg-rose-500/10 text-rose-300 border border-rose-500/20">FAILED</span>',
          CANCELLED: '<span class="px-2 py-0.5 rounded-full text-[10px] font-bold bg-slate-500/10 text-slate-400 border border-slate-500/20">CANCELLED</span>',
        };

        const progressBg = t.status === 'RUNNING' ? 'bg-indigo-500 progress-stripes' : (t.status === 'COMPLETED' ? 'bg-emerald-500' : 'bg-slate-600');

        let cancelBtn = '';
        if (t.status === 'RUNNING' || t.status === 'PENDING') {
          cancelBtn = `<button onclick="cancelTask(${t.id})" class="px-2 py-1 rounded bg-rose-500/20 hover:bg-rose-500/30 text-rose-300 text-[10px] font-semibold border border-rose-500/30 transition">Cancel</button>`;
        }

        return `
          <tr class="hover:bg-slate-800/30 transition">
            <td class="p-3 font-mono font-bold text-white">#${t.id}</td>
            <td class="p-3 font-bold text-indigo-300">${t.task_type}</td>
            <td class="p-3">${statusColors[t.status] || t.status}</td>
            <td class="p-3 w-48">
              <div class="w-full bg-slate-800 rounded-full h-2 overflow-hidden">
                <div class="${progressBg} h-2 rounded-full transition-all duration-300" style="width: ${t.progress || 0}%"></div>
              </div>
              <span class="text-[10px] text-slate-400 font-mono mt-0.5 block">${t.progress || 0}%</span>
            </td>
            <td class="p-3 text-slate-300">
              <div class="truncate max-w-xs">${escapeHtml(t.current_step || t.result_summary || '—')}</div>
            </td>
            <td class="p-3 text-slate-500 font-mono text-[11px]">${formatTime(t.created_at)}</td>
            <td class="p-3 text-right">${cancelBtn}</td>
          </tr>
        `;
      }).join('');
    }
  }

  // Render in Dashboard Active Tasks Container
  const dashContainer = document.getElementById('dashboard-tasks-container');
  if (dashContainer) {
    const activeTasks = tasks ? tasks.filter(t => t.status === 'RUNNING' || t.status === 'PENDING') : [];
    if (activeTasks.length === 0) {
      dashContainer.innerHTML = `
        <div class="text-center py-6 text-slate-500 text-xs">
          No tasks currently executing. Click any quick action above to dispatch!
        </div>`;
    } else {
      dashContainer.innerHTML = activeTasks.map(t => `
        <div class="p-3 rounded-xl bg-slate-900/60 border border-indigo-500/20 space-y-2">
          <div class="flex items-center justify-between text-xs">
            <span class="font-bold text-white flex items-center gap-1.5">
              <span class="w-2 h-2 rounded-full bg-indigo-400 animate-ping"></span>
              Task #${t.id}: ${t.task_type}
            </span>
            <span class="text-indigo-400 font-mono font-bold">${t.progress || 0}%</span>
          </div>
          <div class="w-full bg-slate-800 rounded-full h-2 overflow-hidden">
            <div class="bg-indigo-500 progress-stripes h-2 rounded-full transition-all duration-300" style="width: ${t.progress || 0}%"></div>
          </div>
          <p class="text-[11px] text-slate-400 truncate">${escapeHtml(t.current_step || 'Processing...')}</p>
        </div>
      `).join('');
    }
  }

  if (window.lucide) window.lucide.createIcons();
}

// ============================================================================
// LIVE CONSOLE & LOGS
// ============================================================================
function appendConsoleLog(log) {
  const container = document.getElementById('console-logs');
  if (!container) return;

  const levelColors = {
    SUCCESS: 'text-emerald-400 font-bold',
    INFO: 'text-cyan-300',
    WARN: 'text-amber-400 font-semibold',
    ERROR: 'text-rose-400 font-bold',
  };

  const levelClass = levelColors[log.level] || 'text-slate-300';
  const timeStr = log.timestamp ? log.timestamp.split('T')[1].slice(0, 8) : '';

  const div = document.createElement('div');
  div.className = 'flex items-start gap-2 leading-relaxed';
  div.innerHTML = `
    <span class="text-slate-600 select-none shrink-0">[${timeStr}]</span>
    <span class="${levelClass} select-none shrink-0">[${log.level}]</span>
    <span class="text-slate-200 break-all">${escapeHtml(log.message)}</span>
  `;

  container.appendChild(div);

  // Auto-scroll if close to bottom
  container.scrollTop = container.scrollHeight;
}

async function clearConsoleLogs() {
  try {
    await fetch('/api/logs/clear', { method: 'POST' });
    document.getElementById('console-logs').innerHTML = '';
  } catch (e) {
    console.error('Error clearing logs:', e);
  }
}

// ============================================================================
// SETTINGS MODAL
// ============================================================================
function openSettingsModal() {
  document.getElementById('settings-modal').classList.remove('hidden');
  document.getElementById('settings-modal').classList.add('flex');
}

function closeSettingsModal() {
  document.getElementById('settings-modal').classList.add('hidden');
  document.getElementById('settings-modal').classList.remove('flex');
}

// ============================================================================
// DIRECT CREDENTIALS LOGIN MODAL (Render / Cloud Headless)
// ============================================================================
function openCredentialsModal() {
  const modal = document.getElementById('credentials-login-modal');
  if (modal) {
    modal.classList.remove('hidden');
    modal.classList.add('flex');
    const fb = document.getElementById('credentials-login-feedback');
    if (fb) fb.classList.add('hidden');
    const uInput = document.getElementById('login-username');
    if (uInput) setTimeout(() => uInput.focus(), 100);
    if (window.lucide) window.lucide.createIcons();
  }
}

function closeCredentialsModal() {
  const modal = document.getElementById('credentials-login-modal');
  if (modal) {
    modal.classList.add('hidden');
    modal.classList.remove('flex');
  }
}

async function submitCredentialsLogin(event) {
  if (event) event.preventDefault();
  const username = document.getElementById('login-username')?.value?.trim();
  const password = document.getElementById('login-password')?.value;
  const feedback = document.getElementById('credentials-login-feedback');
  const submitBtn = document.getElementById('btn-submit-credentials');
  const submitText = document.getElementById('credentials-submit-text');
  const submitIcon = document.getElementById('credentials-submit-icon');

  if (!username || !password) {
    if (feedback) {
      feedback.className = 'p-3 rounded-xl text-xs bg-rose-500/10 border border-rose-500/30 text-rose-300';
      feedback.innerText = 'Please enter both your Mingle2 username/email and password.';
      feedback.classList.remove('hidden');
    }
    return;
  }

  if (submitBtn) submitBtn.disabled = true;
  if (submitText) submitText.innerText = 'Logging In...';
  if (submitIcon) submitIcon.classList.add('animate-spin');
  if (feedback) {
    feedback.className = 'p-3 rounded-xl text-xs bg-purple-500/10 border border-purple-500/30 text-purple-200';
    feedback.innerHTML = '<span class="animate-pulse">Connecting to Mingle2 and authenticating headless session...</span>';
    feedback.classList.remove('hidden');
  }

  try {
    showToast(`Attempting direct login for ${username}...`, 'info', 6000);
    const res = await fetch('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ login: username, password: password })
    });
    const data = await res.json();

    if (data.status === 'success') {
      showToast(data.message || 'Mingle2 login verified successfully!', 'success', 6000);
      updateSessionStatus('CONNECTED', 'CONNECTED');
      if (feedback) {
        feedback.className = 'p-3 rounded-xl text-xs bg-emerald-500/10 border border-emerald-500/30 text-emerald-300';
        feedback.innerText = data.message || 'Authenticated successfully!';
      }
      setTimeout(() => {
        closeCredentialsModal();
        loadDashboardStatus();
      }, 1200);
    } else {
      showToast(data.message || 'Login failed.', 'error', 8000);
      if (feedback) {
        feedback.className = 'p-3 rounded-xl text-xs bg-rose-500/10 border border-rose-500/30 text-rose-300';
        feedback.innerText = data.message || 'Authentication rejected.';
      }
    }
  } catch (err) {
    showToast('Network error during login: ' + err.message, 'error');
    if (feedback) {
      feedback.className = 'p-3 rounded-xl text-xs bg-rose-500/10 border border-rose-500/30 text-rose-300';
      feedback.innerText = 'Network error: ' + err.message;
    }
  } finally {
    if (submitBtn) submitBtn.disabled = false;
    if (submitText) submitText.innerText = 'Log In';
    if (submitIcon) submitIcon.classList.remove('animate-spin');
  }
}

function populateSettings(settings) {
  if (!settings) return;
  if (settings.like_delay_minutes !== undefined) {
    document.getElementById('input-delay').value = settings.like_delay_minutes;
    document.getElementById('label-delay').innerText = settings.like_delay_minutes;
  }
  if (settings.scan_interval_seconds !== undefined) {
    document.getElementById('input-scan').value = settings.scan_interval_seconds;
    document.getElementById('label-scan').innerText = settings.scan_interval_seconds;
  }
  if (settings.human_jitter_seconds !== undefined) {
    document.getElementById('input-jitter').value = settings.human_jitter_seconds;
    document.getElementById('label-jitter').innerText = settings.human_jitter_seconds;
  }
  if (settings.headless_mode !== undefined) {
    document.getElementById('input-headless').checked = !!settings.headless_mode;
  }
  if (settings.test_mode !== undefined) {
    const cb = document.getElementById('input-test-mode');
    if (cb) cb.checked = !!settings.test_mode;
    updateTestModeUI(settings.test_mode);
  }
}

async function saveSettings() {
  const payload = {
    like_delay_minutes: parseInt(document.getElementById('input-delay').value),
    scan_interval_seconds: parseInt(document.getElementById('input-scan').value),
    human_jitter_seconds: parseInt(document.getElementById('input-jitter').value),
    headless_mode: document.getElementById('input-headless').checked,
    test_mode: document.getElementById('input-test-mode') ? document.getElementById('input-test-mode').checked : false,
  };

  try {
    const res = await fetch('/api/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    if (res.ok) {
      showToast('Settings saved successfully.', 'success');
      closeSettingsModal();
    }
  } catch (e) {
    showToast('Failed to save settings.', 'error');
  }
}

// ============================================================================
// TEST MODE & DIAGNOSTICS MODULES
// ============================================================================
let isTestModeActive = false;

async function toggleTestMode(explicitState) {
  const newState = explicitState !== undefined ? explicitState : !isTestModeActive;
  try {
    const res = await fetch('/api/settings/test-mode', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled: newState }),
    });
    const data = await res.json();
    updateTestModeUI(data.test_mode);
    showToast(`Test Mode ${data.test_mode ? 'ENABLED (Safe simulation)' : 'DISABLED (Live mode)'}`, 'info');
  } catch (e) {
    showToast('Failed to toggle test mode', 'error');
  }
}

function updateTestModeUI(testMode) {
  isTestModeActive = !!testMode;
  const banner = document.getElementById('test-mode-banner');
  const btnText = document.getElementById('test-mode-btn-text');
  const btn = document.getElementById('btn-toggle-test-mode');
  const inputCheckbox = document.getElementById('input-test-mode');

  if (banner) {
    banner.classList.toggle('hidden', !isTestModeActive);
  }
  if (btnText) {
    btnText.innerText = isTestModeActive ? 'Test Mode: ON' : 'Test Mode: OFF';
  }
  if (btn) {
    btn.className = isTestModeActive
      ? 'flex-1 sm:flex-none inline-flex items-center justify-center gap-1.5 px-3 py-2 rounded-xl font-semibold text-xs md:text-sm bg-amber-500/20 text-amber-300 border border-amber-500/40 shadow-lg shadow-amber-500/10 transition'
      : 'flex-1 sm:flex-none inline-flex items-center justify-center gap-1.5 px-3 py-2 rounded-xl font-semibold text-xs md:text-sm bg-slate-800 hover:bg-slate-700 text-slate-300 border border-slate-700 transition';
  }
  if (inputCheckbox) {
    inputCheckbox.checked = isTestModeActive;
  }
}

async function runDiagnosticsCheck() {
  const icon = document.getElementById('diag-refresh-icon');
  if (icon) icon.classList.add('animate-spin');
  try {
    const res = await fetch('/api/diagnostics');
    const data = await res.json();
    renderDiagnostics(data);
    showToast('Subsystem diagnostics completed.', 'success');
  } catch (e) {
    showToast('Diagnostics probe failed.', 'error');
  } finally {
    if (icon) icon.classList.remove('animate-spin');
  }
}

function renderDiagnostics(data) {
  const subs = data.subsystems || {};

  // Platform session
  const sessEl = document.getElementById('diag-session-status');
  const sessDet = document.getElementById('diag-session-details');
  if (sessEl) sessEl.innerText = subs.platform_session?.session_status || 'UNKNOWN';
  if (sessDet) {
    sessDet.innerText = subs.platform_session?.is_authenticated
      ? `Authenticated ${subs.platform_session?.test_mode ? '(Test Mode)' : '(Live)'}`
      : 'Action required: Sign in';
  }

  // Queue State Machine
  const qEl = document.getElementById('diag-queue-status');
  const qDet = document.getElementById('diag-queue-details');
  if (qEl) {
    qEl.innerText = `${subs.queue_state_machine?.waiting || 0} Waiting / ${subs.queue_state_machine?.completed || 0} Done`;
  }
  if (qDet) {
    qDet.innerText = `Total items: ${subs.queue_state_machine?.total_items || 0} (Next: ${subs.queue_state_machine?.next_scheduled_at ? formatTime(subs.queue_state_machine.next_scheduled_at) : 'None'})`;
  }

  // Identity Verifier
  const vEl = document.getElementById('diag-verifier-status');
  const vDet = document.getElementById('diag-verifier-details');
  if (vEl) vEl.innerText = 'Active (Canonical Guard)';
  if (vDet) {
    vDet.innerText = `Blocked/Isolated: ${subs.queue_state_machine?.verification_failed || 0}`;
  }

  // Search Engine
  const sEl = document.getElementById('diag-search-status');
  const sDet = document.getElementById('diag-search-details');
  if (sEl) sEl.innerText = `${subs.search_engine?.profession_clusters_count || 0} Semantic Clusters`;
  if (sDet) {
    sDet.innerText = (subs.search_engine?.taxonomy_sample || []).join(', ');
  }

  // JSON viewer
  const jsonEl = document.getElementById('diag-json-output');
  if (jsonEl) jsonEl.innerText = JSON.stringify(data, null, 2);
}

async function testVerifyProfile() {
  const input = document.getElementById('verify-test-input');
  const resultEl = document.getElementById('verify-test-result');
  if (!input || !resultEl) return;
  const id = input.value.trim();
  if (!id) {
    showToast('Please enter a Mingle2 profile ID to test.', 'warn');
    return;
  }

  resultEl.className = 'p-3 rounded-xl text-xs bg-slate-900 border border-slate-700 text-slate-300';
  resultEl.innerHTML = `<span class="animate-pulse">Probing profile identity '${id}' on Mingle2...</span>`;
  resultEl.classList.remove('hidden');

  try {
    const res = await fetch(`/api/profiles/${encodeURIComponent(id)}/verify`);
    const data = await res.json();
    if (data.success) {
      resultEl.className = 'p-3 rounded-xl text-xs bg-emerald-950/60 border border-emerald-500/40 text-emerald-200 space-y-1';
      resultEl.innerHTML = `
        <div class="font-bold flex items-center gap-1.5"><i data-lucide="check-circle" class="w-4 h-4 text-emerald-400"></i> Identity Verified!</div>
        <div><strong>Canonical URL:</strong> <a href="${data.profile?.profile_url || ''}" target="_blank" rel="noopener noreferrer" class="underline text-emerald-300">${data.profile?.profile_url || ''}</a></div>
        <div><strong>Username:</strong> ${escapeHtml(data.profile?.username || 'Unknown')}</div>
        <div><strong>Platform ID:</strong> ${escapeHtml(data.profile?.platform_profile_id || id)}</div>
      `;
    } else {
      resultEl.className = 'p-3 rounded-xl text-xs bg-rose-950/60 border border-rose-500/40 text-rose-200 space-y-1';
      resultEl.innerHTML = `
        <div class="font-bold flex items-center gap-1.5"><i data-lucide="alert-triangle" class="w-4 h-4 text-rose-400"></i> Verification Rejected / Blocked</div>
        <div><strong>Reason:</strong> ${escapeHtml(data.message || 'Cannot verify')}</div>
      `;
    }
    if (window.lucide) window.lucide.createIcons();
  } catch (e) {
    resultEl.className = 'p-3 rounded-xl text-xs bg-rose-950/60 border border-rose-500/40 text-rose-200';
    resultEl.innerHTML = `<strong>Error:</strong> Request failed`;
  }
}

// ============================================================================
// COUNTDOWN TICKER & UTILITIES
// ============================================================================
function updateCountdowns() {
  const now = new Date().getTime();
  const timerElements = document.querySelectorAll('.countdown-timer');

  let nextUpcomingMs = null;

  timerElements.forEach(el => {
    const scheduledStr = el.dataset.scheduled;
    const status = el.dataset.status;

    const isPending = ['PENDING', 'WAITING', 'QUEUED', 'READY'].includes(status);
    if (!isPending || !scheduledStr) {
      el.innerText = '—';
      return;
    }

    const target = new Date(scheduledStr).getTime();
    const diff = target - now;

    if (diff <= 0) {
      el.innerText = 'READY (00:00)';
      el.className = 'countdown-timer text-amber-400 font-bold';
    } else {
      const minutes = Math.floor(diff / 60000);
      const seconds = Math.floor((diff % 60000) / 1000);
      el.innerText = `${minutes}m ${seconds < 10 ? '0' : ''}${seconds}s`;
      el.className = 'countdown-timer text-cyan-300 font-mono';

      if (nextUpcomingMs === null || diff < nextUpcomingMs) {
        nextUpcomingMs = diff;
      }
    }
  });

  const nextDisplay = document.getElementById('next-timer-val');
  if (nextDisplay) {
    if (nextUpcomingMs !== null) {
      const m = Math.floor(nextUpcomingMs / 60000);
      const s = Math.floor((nextUpcomingMs % 60000) / 1000);
      nextDisplay.innerText = `${m}m ${s < 10 ? '0' : ''}${s}s`;
    } else {
      nextDisplay.innerText = 'None scheduled';
    }
  }
}

function showToast(message, type = 'info') {
  const container = document.getElementById('toast-container');
  if (!container) return;

  const bgColors = {
    info: 'bg-slate-900 border-cyan-500/40 text-cyan-300',
    success: 'bg-slate-900 border-emerald-500/40 text-emerald-300',
    warning: 'bg-slate-900 border-amber-500/40 text-amber-300',
    error: 'bg-slate-900 border-rose-500/40 text-rose-300',
  };

  const icons = {
    info: 'info',
    success: 'check-circle-2',
    warning: 'alert-triangle',
    error: 'x-circle',
  };

  const div = document.createElement('div');
  div.className = `toast-item glass-panel p-3.5 rounded-xl border flex items-center gap-2.5 text-xs font-medium shadow-xl ${bgColors[type] || bgColors.info}`;
  div.innerHTML = `
    <i data-lucide="${icons[type] || 'info'}" class="w-4 h-4 shrink-0"></i>
    <span class="flex-1">${escapeHtml(message)}</span>
  `;

  container.appendChild(div);
  if (window.lucide) window.lucide.createIcons();

  setTimeout(() => {
    div.style.transition = 'opacity 0.3s ease, transform 0.3s ease';
    div.style.opacity = '0';
    div.style.transform = 'translateY(10px)';
    setTimeout(() => div.remove(), 300);
  }, 4000);
}

function formatTime(isoStr) {
  if (!isoStr) return '—';
  try {
    const d = new Date(isoStr);
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  } catch (e) {
    return isoStr;
  }
}

function escapeHtml(str) {
  if (!str) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}
