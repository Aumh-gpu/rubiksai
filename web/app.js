/* RUBIKS web app: one codebase, three layouts (phone / tablet / desktop). */
(() => {
  'use strict';

  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const html = document.documentElement;
  const reduceMotion = matchMedia('(prefers-reduced-motion: reduce)');
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const icon = (id, cls = '') => `<svg class="${cls}" aria-hidden="true"><use href="#i-${id}"/></svg>`;
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const timeOf = (ts) => new Date(ts * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  const clock = (ts) => new Date(ts * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hourCycle: 'h23' });

  const store = {
    get(k, d) { try { const v = localStorage.getItem('rubiks.' + k); return v === null ? d : JSON.parse(v); } catch { return d; } },
    set(k, v) { try { localStorage.setItem('rubiks.' + k, JSON.stringify(v)); } catch { /* private mode */ } },
  };

  const S = {
    user: null, engine: 'offline', mode: null, layout: 'desktop', convId: null, convs: [], busy: false,
    authMode: 'login', approvals: new Map(), myTurns: new Set(), remoteTurns: new Set(), es: null,
    settings: null, installEvt: null, task: null, vitalsTimer: null, taskTimer: null, firstSessions: true,
  };

  const MODES = {
    phone: { label: 'Phone', name: 'Pocket mode', icon: 'phone', note: 'Short answers, voice, big approvals' },
    tablet: { label: 'Tablet', name: 'Studio mode', icon: 'tablet', note: 'Chats beside the conversation' },
    desktop: { label: 'Desktop', name: 'Command mode', icon: 'desktop', note: 'Full detail, shortcuts, live rail' },
  };
  const DEVICE_ICON = { phone: 'phone', tablet: 'tablet', desktop: 'desktop', terminal: 'terminal' };

  const SUGGEST = {
    phone: [['Show my screen', 'See your PC right now', 'Show my screen'], ['PC status', 'CPU, memory, battery', 'System status'],
      ["What's running?", 'Top programs', "What's running on my PC?"], ['Brief me', 'Weather, news, PC', 'Brief me']],
    tablet: [['Show my screen', 'See your PC from here', 'Show my screen'], ['Brief me', 'Weather, news and PC', 'Brief me'],
      ['Recent files', 'What you worked on last', 'Show my recent files'], ['Research', 'Search and summarize', 'Search best laptops under 60k']],
    desktop: [['System status', 'CPU, memory, disk', 'System status'], ['Recent files', 'The last files you touched', 'Show my recent files'],
      ['Run a command', 'Asks before it runs', 'run Get-Date'], ['Research', 'Search and summarize', 'Search best laptops under 60k']],
  };

  /* ─── Feedback ─────────────────────────────────────────────────────── */
  const haptic = {
    tap: () => !reduceMotion.matches && navigator.vibrate?.(8),
    success: () => !reduceMotion.matches && navigator.vibrate?.([10, 40, 14]),
    warn: () => !reduceMotion.matches && navigator.vibrate?.([20, 30, 20]),
  };

  let toastT;
  function toast(msg) {
    const t = $('#toast');
    t.textContent = msg;
    t.classList.add('on');
    clearTimeout(toastT);
    toastT = setTimeout(() => t.classList.remove('on'), 2400);
  }

  // Press state on pointerdown, so taps respond before click fires.
  document.addEventListener('pointerdown', (e) => {
    const b = e.target.closest('.btn, .device-opt, .suggest button');
    if (!b || b.disabled) return;
    b.setAttribute('data-pressed', '');
    const up = () => { b.removeAttribute('data-pressed'); ['pointerup', 'pointerleave', 'pointercancel'].forEach((ev) => b.removeEventListener(ev, up)); };
    ['pointerup', 'pointerleave', 'pointercancel'].forEach((ev) => b.addEventListener(ev, up));
  });

  /* ─── API ──────────────────────────────────────────────────────────── */
  async function api(path, { method = 'GET', body } = {}) {
    const opts = { method, headers: { 'X-Rubiks': '1' }, credentials: 'same-origin' };
    if (body !== undefined) { opts.headers['Content-Type'] = 'application/json'; opts.body = JSON.stringify(body); }
    let res;
    try { res = await fetch(path, opts); } catch { const e = new Error("Can't reach your PC."); e.network = true; throw e; }
    let data = {};
    try { data = await res.json(); } catch { /* empty */ }
    if (res.status === 401 && path !== '/api/auth/login') { signedOut(); }
    if (!res.ok || data.ok === false) { const e = new Error(data.error || `Request failed (${res.status})`); e.status = res.status; throw e; }
    return data;
  }

  /* ─── Presence cube ────────────────────────────────────────────────── */
  const CUBE_COLORS = ['var(--c1)', 'var(--c2)', 'var(--c3)', 'var(--c4)', 'var(--c5)', 'var(--c6)'];
  function fillCubes(root = document) {
    $$('[data-cube]', root).forEach((c) => {
      if (c.children.length) return;
      for (let i = 0; i < 9; i++) { const cell = document.createElement('i'); cell.style.setProperty('--i', i); c.appendChild(cell); }
    });
  }
  const scramble = (c) => [...c.children].forEach((cell) => { cell.style.background = CUBE_COLORS[(Math.random() * 6) | 0]; });
  const unscramble = (c) => [...c.children].forEach((cell) => { cell.style.background = ''; });

  // Arrival: the cube assembles scrambled, then solves itself.
  function assemble(cube) {
    if (!cube) return;
    fillCubes(cube.parentElement);
    cube.classList.remove('assemble'); void cube.offsetWidth; cube.classList.add('assemble');
    scramble(cube);
    setTimeout(() => { unscramble(cube); cube.dataset.state = 'solved'; }, 620);
    setTimeout(() => { cube.dataset.state = ''; }, 1250);
  }

  const Cube = {
    state: 'idle', timer: null, back: null,
    els() { return $$('#presence, .cube[data-live]'); },
    set(state) {
      if (state === 'idle' && (S.busy || S.remoteTurns.size)) state = 'thinking';
      this.state = state;
      clearInterval(this.timer); clearTimeout(this.back);
      const label = $('#presence-label');
      const words = { idle: 'Ready', thinking: 'Working', waiting: 'Needs you', solved: 'Done', error: 'Error' };
      if (label) { label.textContent = words[state]; label.dataset.state = state; }
      this.els().forEach((c) => { unscramble(c); c.dataset.state = state === 'thinking' || state === 'idle' ? '' : state; });
      if (state === 'thinking') {
        const tick = () => this.els().forEach(scramble);
        tick();
        this.timer = setInterval(tick, reduceMotion.matches ? 900 : 260);
      }
      if (state === 'solved') this.back = setTimeout(() => this.set('idle'), 1500);
      if (state === 'error') this.back = setTimeout(() => this.set('idle'), 2600);
    },
    flash() {
      const c = $('#presence');
      if (!c || reduceMotion.matches) return;
      const cell = c.children[(Math.random() * 9) | 0];
      cell?.classList.add('flash');
      setTimeout(() => cell?.classList.remove('flash'), 380);
    },
  };

  /* ─── Screens & layout ─────────────────────────────────────────────── */
  function show(id) { $$('.screen').forEach((s) => { s.hidden = s.id !== id; }); }

  function guessMode() {
    const ua = navigator.userAgent;
    const coarse = matchMedia('(pointer: coarse)').matches;
    const short = Math.min(screen.width, screen.height);
    if (/iPad|Tablet/i.test(ua) || (navigator.maxTouchPoints > 1 && /Macintosh/.test(ua))) return 'tablet';
    if (/Android/i.test(ua) && !/Mobile/i.test(ua)) return 'tablet';
    if (coarse && short < 600) return 'phone';
    if (coarse) return 'tablet';
    return 'desktop';
  }

  function computeLayout() {
    const w = innerWidth;
    const m = S.mode || 'desktop';
    if (m === 'phone' || w < 640) return 'phone';
    if (m === 'tablet' || w < 1024) return 'tablet';
    return 'desktop';
  }

  function applyLayout() {
    const prev = S.layout;
    S.layout = computeLayout();
    html.dataset.mode = S.mode || 'desktop';
    html.dataset.layout = S.layout;
    html.classList.toggle('no-activity', S.layout === 'desktop' && !store.get('activity', true));
    if (prev !== S.layout) closeSheets(true);
    const mb = $('#mode-btn use');
    if (mb) mb.setAttribute('href', '#i-' + MODES[S.mode || 'desktop'].icon);
    setupMic();
    pollVitals();
  }
  let rz;
  addEventListener('resize', () => { cancelAnimationFrame(rz); rz = requestAnimationFrame(() => { if (!$('#screen-app').hidden) applyLayout(); }); });

  function applyTheme(t) {
    if (t === 'light' || t === 'dark') html.dataset.theme = t; else delete html.dataset.theme;
  }

  /* ─── Boot ─────────────────────────────────────────────────────────── */
  async function boot() {
    fillCubes();
    applyTheme(store.get('theme', 'system'));
    assemble($('#screen-boot [data-cube]'));
    registerSW();
    let b;
    try { b = await api('/api/bootstrap'); } catch { show('screen-offline'); return; }
    S.engine = b.engine;
    if (!b.user) { showAuth(b.has_users); return; }
    signedIn(b.user);
  }

  function registerSW() {
    if (!('serviceWorker' in navigator) || !window.isSecureContext) return;
    navigator.serviceWorker.register('/sw.js').catch(() => {});
  }
  addEventListener('beforeinstallprompt', (e) => { e.preventDefault(); S.installEvt = e; });

  /* ─── Auth ─────────────────────────────────────────────────────────── */
  function showAuth(hasUsers) {
    show('screen-auth');
    S.authMode = hasUsers ? 'login' : 'setup';
    $('#auth-tabs').hidden = !hasUsers;
    renderAuthMode();
    assemble($('#screen-auth [data-cube]'));
    setTimeout(() => $('#auth-form input:not([hidden])')?.focus({ preventScroll: true }), 300);
  }

  function renderAuthMode() {
    const m = S.authMode;
    const copy = {
      setup: ['Set up your Rubiks', "This first account owns the Rubiks on this PC. You can invite your team later.", 'Create owner account'],
      login: ['Welcome back', 'Sign in to the Rubiks running on this PC.', 'Sign in'],
      signup: ['Join with an invite', 'Ask the owner for a code. They can make one in Settings, or with rubiks invite.', 'Create account'],
    }[m];
    $('#auth-title').textContent = copy[0];
    $('#auth-sub').textContent = copy[1];
    $('#auth-submit').textContent = copy[2];
    $$('[data-only="name"]').forEach((el) => { el.hidden = m === 'login'; });
    $$('[data-only="invite"]').forEach((el) => { el.hidden = m !== 'signup'; });
    $('#auth-form [name="password"]').autocomplete = m === 'login' ? 'current-password' : 'new-password';
    const tabs = $('#auth-tabs');
    const idx = m === 'signup' ? 1 : 0;
    tabs.style.setProperty('--idx', idx);
    tabs.style.setProperty('--n', 2);
    $$('[role="tab"]', tabs).forEach((t, i) => t.setAttribute('aria-selected', String(i === idx)));
    $('#auth-error').textContent = '';
  }

  $('#auth-tabs').addEventListener('click', (e) => {
    const t = e.target.closest('[data-tab]');
    if (!t) return;
    S.authMode = t.dataset.tab;
    renderAuthMode();
  });

  $('#auth-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const f = e.currentTarget;
    const btn = $('#auth-submit');
    const data = Object.fromEntries(new FormData(f));
    const err = $('#auth-error');
    err.textContent = '';
    if (!data.email || !data.password) { err.textContent = 'Enter your email and password.'; shake(); return; }
    const label = btn.textContent;
    btn.disabled = true;
    btn.textContent = S.authMode === 'login' ? 'Signing in…' : 'Creating account…';
    try {
      const path = S.authMode === 'login' ? '/api/auth/login' : '/api/auth/signup';
      const r = await api(path, { method: 'POST', body: { ...data, client: navigator.userAgent.slice(0, 80) } });
      haptic.success();
      f.reset();
      signedIn(r.user);
    } catch (ex) {
      err.textContent = ex.message;
      shake();
      haptic.warn();
    } finally {
      btn.disabled = false;
      btn.textContent = label;
    }
  });

  function shake() { const c = $('.auth-card'); c.classList.remove('shake'); void c.offsetWidth; c.classList.add('shake'); }

  document.addEventListener('click', (e) => {
    const t = e.target.closest('[data-action="toggle-pw"]');
    if (!t) return;
    const input = t.parentElement.querySelector('input');
    const showing = input.type === 'text';
    input.type = showing ? 'password' : 'text';
    t.setAttribute('aria-label', showing ? 'Show password' : 'Hide password');
  });

  function signedIn(user) {
    S.user = user;
    const local = store.get('mode', null);
    if (!local) { showDevicePicker(); return; }
    S.mode = local;
    enterApp();
  }

  function signedOut() {
    if (!S.user) return;
    S.user = null;
    S.es?.close(); S.es = null;
    clearInterval(S.vitalsTimer); clearTimeout(S.taskTimer);
    closeSheets(true); closeModal(); closePop();
    showAuth(true);
  }

  /* ─── Device picker ────────────────────────────────────────────────── */
  let pickedMode = null;
  function showDevicePicker() {
    show('screen-device');
    const guess = guessMode();
    pickedMode = S.mode || guess;
    const box = $('.device-options');
    $$('.device-opt', box).forEach((o, i) => {
      o.style.setProperty('--i', i);
      o.querySelector('.badge').hidden = o.dataset.mode !== guess;
      o.setAttribute('aria-checked', String(o.dataset.mode === pickedMode));
      o.tabIndex = o.dataset.mode === pickedMode ? 0 : -1;
    });
    box.classList.remove('stagger'); void box.offsetWidth; box.classList.add('stagger');
    $('#device-continue').disabled = false;
  }

  function pickMode(m, focus) {
    pickedMode = m;
    $$('.device-opt').forEach((o) => { const on = o.dataset.mode === m; o.setAttribute('aria-checked', String(on)); o.tabIndex = on ? 0 : -1; if (on && focus) o.focus(); });
    haptic.tap();
  }

  $('.device-options').addEventListener('click', (e) => { const o = e.target.closest('.device-opt'); if (o) pickMode(o.dataset.mode); });
  $('.device-options').addEventListener('keydown', (e) => {
    const order = ['phone', 'tablet', 'desktop'];
    const i = order.indexOf(pickedMode);
    if (e.key === 'ArrowDown' || e.key === 'ArrowRight') { e.preventDefault(); pickMode(order[(i + 1) % 3], true); }
    if (e.key === 'ArrowUp' || e.key === 'ArrowLeft') { e.preventDefault(); pickMode(order[(i + 2) % 3], true); }
  });
  $('#device-continue').addEventListener('click', () => {
    S.mode = pickedMode;
    store.set('mode', S.mode);
    api('/api/me', { method: 'PATCH', body: { device_mode: S.mode } }).catch(() => {});
    enterApp();
  });

  function setMode(m) {
    if (!MODES[m] || m === S.mode) return;
    S.mode = m;
    store.set('mode', m);
    api('/api/me', { method: 'PATCH', body: { device_mode: m } }).catch(() => {});
    applyLayout();
    if (!S.convId) renderEmpty();
    toast(`${MODES[m].name} on. ${MODES[m].note}.`);
  }

  /* ─── App shell ────────────────────────────────────────────────────── */
  function enterApp() {
    show('screen-app');
    fillCubes($('#screen-app'));
    applyLayout();
    renderUser();
    updateEngine(S.engine);
    const labels = api('/api/tools').then((d) => d.tools.forEach((t) => { TOOL_LABELS[t.name] = t.label; })).catch(() => {});
    Promise.all([loadConversations(), labels]).then(() => {
      const last = store.get('lastConv', null);
      if (last && S.convs.some((c) => c.id === last)) openConversation(last); else newChat(true);
      // Home-screen shortcuts open /?q=…
      const q = new URLSearchParams(location.search).get('q');
      if (q) { history.replaceState(null, '', '/'); newChat(true); send(q); }
    });
    connectEvents();
    loadActivity();
    pollTask();
    if (S.layout === 'desktop') setTimeout(() => $('#input').focus({ preventScroll: true }), 50);
    api('/api/settings').then((s) => { S.settings = s; updateEngine(s.engine); }).catch(() => {});
  }

  function renderUser() {
    const u = S.user;
    $('#user-name').textContent = u.name;
    $('#user-role').textContent = u.role === 'owner' ? 'Owner' : 'Member';
    $('#avatar').textContent = (u.name || 'R').trim()[0].toUpperCase();
  }

  function updateEngine(engine) {
    S.engine = engine;
    const pill = $('#engine-pill');
    pill.dataset.engine = engine;
    $('#engine-label').innerHTML = engine === 'gemini' ? 'Gemini' : 'Offline<span class="not-phone"> mode</span>';
    pill.title = engine === 'gemini' ? 'Full brain connected' : 'Add a Gemini key to unlock the full brain';
  }

  /* ─── Conversations ────────────────────────────────────────────────── */
  async function loadConversations() {
    try { S.convs = (await api('/api/conversations')).items; } catch { S.convs = []; }
    renderSessions();
  }

  function dayLabel(ts) {
    const d = new Date(ts * 1000); const now = new Date();
    const start = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
    if (d.getTime() >= start) return 'Today';
    if (d.getTime() >= start - 864e5) return 'Yesterday';
    if (d.getTime() >= start - 6 * 864e5) return 'This week';
    return 'Earlier';
  }

  function renderSessions() {
    const list = $('#session-list');
    if (!S.convs.length) { list.innerHTML = '<p class="session-empty">No chats yet. What you ask from any device shows up here.</p>'; return; }
    let out = ''; let last = '';
    S.convs.forEach((c, i) => {
      const day = dayLabel(c.updated_at);
      if (day !== last) { out += `<div class="session-day">${day}</div>`; last = day; }
      out += `<button class="session" data-id="${esc(c.id)}" aria-current="${c.id === S.convId}" style="--i:${Math.min(i, 6)}">
        <strong>${esc(c.title)}</strong><small>${esc(c.last || '')}</small></button>`;
    });
    list.innerHTML = out;
    if (S.firstSessions) { list.classList.add('stagger'); S.firstSessions = false; setTimeout(() => list.classList.remove('stagger'), 900); }
  }

  $('#session-list').addEventListener('click', (e) => {
    const b = e.target.closest('.session');
    if (!b) return;
    openConversation(b.dataset.id);
    if (S.layout === 'phone') closeSheets();
  });

  async function openConversation(id) {
    S.convId = id;
    store.set('lastConv', id);
    renderSessions();
    let d;
    try { d = await api(`/api/conversations/${id}`); } catch { newChat(true); return; }
    if (S.convId !== id) return;
    const inner = $('#thread-inner');
    inner.innerHTML = '';
    d.messages.forEach((m) => {
      if (m.role === 'user') inner.append(userMsg(m.content, m.device, m.created_at, false));
      else inner.append(aiMsg(m, false).el);
    });
    scrollToBottom(false);
  }

  function newChat(silent) {
    S.convId = null;
    store.set('lastConv', null);
    renderSessions();
    renderEmpty();
    if (!silent && S.layout === 'phone') closeSheets();
    if (S.layout === 'desktop') $('#input').focus({ preventScroll: true });
  }

  function renderEmpty() {
    const h = new Date().getHours();
    const greet = h < 5 ? 'Still up' : h < 12 ? 'Good morning' : h < 17 ? 'Good afternoon' : 'Good evening';
    const first = esc((S.user?.name || '').split(' ')[0]);
    const items = SUGGEST[S.mode || 'desktop'];
    $('#thread-inner').innerHTML = `<div class="empty">
      <h2>${greet}, ${first}.<br><em>What should your PC do?</em></h2>
      <div class="suggest stagger">${items.map((s, i) => `<button class="lit" style="--i:${i}" data-prompt="${esc(s[2])}"><strong>${esc(s[0])}</strong><small>${esc(s[1])}</small></button>`).join('')}</div>
    </div>`;
  }

  $('#thread-inner').addEventListener('click', (e) => {
    const s = e.target.closest('[data-prompt]');
    if (s) { send(s.dataset.prompt); return; }
    const img = e.target.closest('.shot');
    if (img) { lightbox(img.src); return; }
    const a = e.target.closest('[data-msg-action]');
    if (a) msgAction(a);
  });

  /* ─── Messages ─────────────────────────────────────────────────────── */
  function userMsg(text, device, ts, animate = true) {
    const el = document.createElement('article');
    el.className = 'msg msg--user' + (animate ? ' msg-in' : '');
    const dev = device && device !== S.mode ? `${icon(DEVICE_ICON[device] || 'desktop')}from ${esc(device)} · ` : '';
    el.innerHTML = `<div class="bubble">${esc(text)}</div><div class="bubble-meta">${dev}${timeOf(ts || Date.now() / 1000)}</div>`;
    return el;
  }

  function moveRow(ev) {
    const d = document.createElement('div');
    d.className = 'move';
    d.dataset.id = ev.id || '';
    d.dataset.status = ev.status || (ev.ok === false ? 'fail' : 'ok');
    const arg = firstArg(ev.args);
    d.innerHTML = `<span class="move-dot"></span><span class="move-label">${esc(ev.label || ev.name)}</span><span class="move-arg">${esc(arg)}</span><span class="move-ms">${ev.ms ? fmtMs(ev.ms) : ''}</span>`;
    return d;
  }
  const firstArg = (args) => { if (!args) return ''; const v = Object.values(args).find((x) => x !== '' && x != null); return v == null ? '' : String(v).replace(/\s+/g, ' ').slice(0, 80); };
  const fmtMs = (ms) => (ms >= 1000 ? (ms / 1000).toFixed(1) + 's' : ms + 'ms');

  function aiMsg(m, live) {
    const el = document.createElement('article');
    el.className = 'msg msg--ai' + (live ? ' msg-in' : '') + (m?.meta?.error ? ' msg-error' : '');
    el.innerHTML = `<div class="msg-head"><span class="cube cube--xs" data-cube ${live ? 'data-live' : ''} aria-hidden="true"></span><strong>Rubiks</strong>
      <span class="engine-tag" hidden></span><time></time></div>
      <div class="moves-inline" hidden></div><div class="approvals-inline"></div><div class="images"></div>
      <div class="md"></div><div class="msg-actions" hidden>
        <button class="icon-btn" data-msg-action="copy" aria-label="Copy">${icon('copy')}</button>
        <button class="icon-btn" data-msg-action="speak" aria-label="Read aloud">${icon('speaker')}</button>
      </div>`;
    fillCubes(el);
    const parts = { el, moves: $('.moves-inline', el), approvals: $('.approvals-inline', el), images: $('.images', el), md: $('.md', el), actions: $('.msg-actions', el) };
    if (m) {
      const meta = m.meta || {};
      (meta.tools || []).forEach((t) => { parts.moves.hidden = false; parts.moves.append(moveRow({ ...t, label: toolLabel(t.name) })); });
      (meta.images || []).forEach((u) => parts.images.append(shotImg(u)));
      parts.md.innerHTML = md(m.content);
      el.dataset.raw = m.content;
      $('time', el).textContent = timeOf(m.created_at);
      if (meta.engine === 'offline') { const t = $('.engine-tag', el); t.hidden = false; t.textContent = 'offline'; }
      parts.actions.hidden = false;
    }
    return parts;
  }

  const TOOL_LABELS = {};
  const toolLabel = (name) => TOOL_LABELS[name] || name.replace(/_/g, ' ');

  function shotImg(url) {
    const img = document.createElement('img');
    img.className = 'shot'; img.src = url; img.alt = 'Screenshot of your PC'; img.loading = 'lazy';
    return img;
  }

  async function msgAction(btn) {
    const msg = btn.closest('.msg');
    const raw = msg.dataset.raw || msg.querySelector('.md')?.innerText || '';
    if (btn.dataset.msgAction === 'copy') { await copyText(raw); toast('Copied'); }
    if (btn.dataset.msgAction === 'speak') {
      if (btn.classList.contains('playing')) { stopSpeaking(); return; }
      unlockAudio();
      speak(msg.querySelector('.md').innerText, btn);
    }
  }

  async function copyText(t) {
    try { await navigator.clipboard.writeText(t); return; } catch { /* fallback */ }
    const ta = document.createElement('textarea');
    ta.value = t; ta.style.position = 'fixed'; ta.style.opacity = '0';
    document.body.append(ta); ta.select();
    try { document.execCommand('copy'); } catch { /* ignore */ }
    ta.remove();
  }

  /* ─── Markdown (escape first, then a small safe subset) ────────────── */
  function md(src) {
    const blocks = [];
    let s = esc(src || '').replace(/```[\w-]*\n?([\s\S]*?)```/g, (_, code) => { blocks.push(`<pre><code>${code.replace(/\n$/, '')}</code></pre>`); return `\u0000${blocks.length - 1}\u0000`; });
    const inline = (t) => t
      .replace(/`([^`\n]+)`/g, '<code>$1</code>')
      .replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>')
      .replace(/(^|[^*\w])\*([^*\n]+)\*(?!\w)/g, '$1<em>$2</em>')
      .replace(/(^|[^\w])_([^_\n]+)_(?!\w)/g, '$1<em>$2</em>')
      .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>')
      .replace(/(^|[\s(])(https?:\/\/[^\s<)]+)/g, '$1<a href="$2" target="_blank" rel="noopener noreferrer">$2</a>');
    const lines = s.split('\n');
    const out = [];
    let i = 0;
    while (i < lines.length) {
      const line = lines[i];
      if (/^\u0000\d+\u0000$/.test(line.trim())) { out.push(line.trim()); i++; continue; }
      if (/^\s*\|.*\|\s*$/.test(line) && i + 1 < lines.length && /^\s*\|[\s:|-]+\|\s*$/.test(lines[i + 1])) {
        const row = (l) => l.trim().replace(/^\||\|$/g, '').split('|').map((c) => inline(c.trim()));
        const head = row(line); i += 2;
        let body = '';
        while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) { body += `<tr>${row(lines[i]).map((c) => `<td>${c}</td>`).join('')}</tr>`; i++; }
        out.push(`<table><thead><tr>${head.map((c) => `<th>${c}</th>`).join('')}</tr></thead><tbody>${body}</tbody></table>`);
        continue;
      }
      const h = line.match(/^(#{1,4})\s+(.*)$/);
      if (h) { out.push(`<h${Math.min(h[1].length + 2, 4)}>${inline(h[2])}</h${Math.min(h[1].length + 2, 4)}>`); i++; continue; }
      if (/^\s*[-*•]\s+/.test(line)) {
        let items = '';
        while (i < lines.length && /^\s*[-*•]\s+/.test(lines[i])) { items += `<li>${inline(lines[i].replace(/^\s*[-*•]\s+/, ''))}</li>`; i++; }
        out.push(`<ul>${items}</ul>`); continue;
      }
      if (/^\s*\d+[.)]\s+/.test(line)) {
        let items = '';
        while (i < lines.length && /^\s*\d+[.)]\s+/.test(lines[i])) { items += `<li>${inline(lines[i].replace(/^\s*\d+[.)]\s+/, ''))}</li>`; i++; }
        out.push(`<ol>${items}</ol>`); continue;
      }
      if (!line.trim()) { i++; continue; }
      const para = [];
      while (i < lines.length && lines[i].trim() && !/^(#{1,4}\s|\s*[-*•]\s|\s*\d+[.)]\s|\s*\|)/.test(lines[i]) && !/^\u0000\d+\u0000$/.test(lines[i].trim())) { para.push(inline(lines[i])); i++; }
      out.push(`<p>${para.join('<br>')}</p>`);
    }
    return out.join('').replace(/\u0000(\d+)\u0000/g, (_, n) => blocks[+n]);
  }

  /* ─── Sending & streaming ──────────────────────────────────────────── */
  const input = $('#input');
  const sendBtn = $('#send');

  function autosize() { input.style.height = 'auto'; input.style.height = Math.min(input.scrollHeight, 200) + 'px'; }
  function updateSend() { sendBtn.disabled = S.busy || !input.value.trim(); }
  input.addEventListener('input', () => { autosize(); updateSend(); });
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(input.value); }
  });
  $('#composer').addEventListener('submit', (e) => { e.preventDefault(); send(input.value); });

  const thread = $('#thread');
  const nearBottom = () => thread.scrollHeight - thread.scrollTop - thread.clientHeight < 140;

  // The composer floats over the thread; the thread pads itself to the composer's live height.
  const composerWrap = $('.composer-wrap');
  if ('ResizeObserver' in window) {
    new ResizeObserver(() => html.style.setProperty('--composer-h', composerWrap.offsetHeight + 'px')).observe(composerWrap);
  }

  // Liquid glass catches the light where the pointer is (desktop only).
  if (matchMedia('(hover: hover)').matches) {
    let litRaf = 0, litEv = null;
    document.addEventListener('pointermove', (e) => {
      litEv = e;
      if (litRaf) return;
      litRaf = requestAnimationFrame(() => {
        litRaf = 0;
        const el = litEv.target.closest?.('.lit');
        if (!el) return;
        const r = el.getBoundingClientRect();
        el.style.setProperty('--mx', `${litEv.clientX - r.left}px`);
        el.style.setProperty('--my', `${litEv.clientY - r.top}px`);
      });
    }, { passive: true });
  }

  /* ─── Voice out: ElevenLabs through the PC, browser voice as fallback ─ */
  let actx = null, currentSrc = null;
  function unlockAudio() {
    try {
      actx ||= new (window.AudioContext || window.webkitAudioContext)();
      if (actx.state === 'suspended') actx.resume();
    } catch { /* no Web Audio */ }
  }
  function stopSpeaking() {
    try { currentSrc?.stop(); } catch { /* already stopped */ }
    currentSrc = null;
    try { speechSynthesis.cancel(); } catch { /* unsupported */ }
    $$('.playing').forEach((b) => b.classList.remove('playing'));
  }
  async function speak(text, btn) {
    stopSpeaking();
    btn?.classList.add('playing');
    if (S.settings?.voice?.configured && actx) {
      try {
        const res = await fetch('/api/tts', { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json', 'X-Rubiks': '1' }, body: JSON.stringify({ text }) });
        if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error || 'The voice failed.');
        const audio = await actx.decodeAudioData(await res.arrayBuffer());
        const src = actx.createBufferSource();
        src.buffer = audio;
        src.connect(actx.destination);
        src.onended = () => { if (currentSrc === src) { currentSrc = null; btn?.classList.remove('playing'); } };
        currentSrc = src;
        src.start();
        return;
      } catch (e) {
        toast(`${e.message} Using the browser voice.`);
      }
    }
    if ('speechSynthesis' in window) {
      const u = new SpeechSynthesisUtterance(text);
      u.rate = 1.04;
      u.onend = () => btn?.classList.remove('playing');
      speechSynthesis.speak(u);
    } else btn?.classList.remove('playing');
  }
  function scrollToBottom(smooth = true) { thread.scrollTo({ top: thread.scrollHeight, behavior: smooth && !reduceMotion.matches ? 'smooth' : 'auto' }); }
  thread.addEventListener('scroll', () => $('.topbar').classList.toggle('scrolled', thread.scrollTop > 4), { passive: true });

  async function send(text, opts = {}) {
    text = (text || '').trim();
    if (!text || S.busy) return;
    unlockAudio(); // still inside the tap/keypress, so the reply can be spoken later
    S.lastByVoice = !!opts.voice;
    S.busy = true;
    input.value = ''; autosize(); updateSend();
    sendBtn.classList.remove('launch'); void sendBtn.offsetWidth; sendBtn.classList.add('launch');
    haptic.tap();
    if ($('.empty')) $('#thread-inner').innerHTML = '';
    $('#thread-inner').append(userMsg(text, S.mode, Date.now() / 1000));
    const ai = aiMsg(null, true);
    $('#thread-inner').append(ai.el);
    const skel = setTimeout(() => { if (!ai.md.textContent) ai.md.innerHTML = '<div class="skeleton-line"></div><div class="skeleton-line"></div>'; }, 280);
    Cube.set('thinking');
    scrollToBottom();

    let full = '';
    let ended = false;
    // Name the turn up front: the live bus can deliver its events before this stream starts.
    const turnId = Array.from({ length: 16 }, () => '0123456789abcdefghijklmnopqrstuvwxyz'[(Math.random() * 36) | 0]).join('');
    S.myTurns.add(turnId);
    try {
      const res = await fetch('/api/chat', {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', 'X-Rubiks': '1' },
        body: JSON.stringify({ message: text, conversation_id: S.convId, device: S.mode, turn_id: turnId }),
      });
      if (!res.ok) {
        let d = {}; try { d = await res.json(); } catch { /* empty */ }
        if (res.status === 401) signedOut();
        throw new Error(d.error || `Request failed (${res.status})`);
      }
      const reader = res.body.getReader();
      const dec = new TextDecoder();
      let buf = '';
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        let cut;
        while ((cut = buf.indexOf('\n\n')) >= 0) {
          const chunk = buf.slice(0, cut); buf = buf.slice(cut + 2);
          for (const line of chunk.split('\n')) {
            if (!line.startsWith('data: ')) continue;
            let ev; try { ev = JSON.parse(line.slice(6)); } catch { continue; }
            if (ev.type === 'delta') { clearTimeout(skel); full += ev.text; ai.md.innerHTML = md(full) + '<span class="caret"></span>'; if (nearBottom()) scrollToBottom(false); }
            else if (ev.type === 'done' || ev.type === 'error') { ended = true; clearTimeout(skel); finishTurn(ai, ev); }
            else turnEvent(ev, ai);
          }
        }
      }
      if (!ended) throw new Error('The connection dropped before Rubiks finished. The answer will be in this chat when it completes.');
    } catch (e) {
      clearTimeout(skel);
      finishTurn(ai, { type: 'error', message: e.message || "Couldn't reach your PC." });
    } finally {
      S.busy = false;
      updateSend();
      if (S.layout === 'desktop') input.focus({ preventScroll: true });
    }
  }

  function turnEvent(ev, ai) {
    if (ev.type === 'start') {
      S.myTurns.add(ev.turn_id);
      S.remoteTurns.delete(ev.turn_id); // the bus may have announced our own turn first
      if (S.convId !== ev.conversation_id) { S.convId = ev.conversation_id; store.set('lastConv', S.convId); loadConversations(); }
      if (ev.engine) updateEngine(ev.engine);
    } else if (ev.type === 'tool') {
      ai.moves.hidden = false;
      const existing = $(`.move[data-id="${ev.id}"]`, ai.moves);
      const row = moveRow(ev);
      existing ? existing.replaceWith(row) : ai.moves.append(row);
      Cube.flash();
    } else if (ev.type === 'tool_done') {
      const row = $(`.move[data-id="${ev.id}"]`, ai.moves);
      if (row) { row.dataset.status = ev.ok ? 'ok' : 'fail'; $('.move-ms', row).textContent = ev.ms ? fmtMs(ev.ms) : ''; }
    } else if (ev.type === 'approval') {
      const card = approvalCard(ev);
      ai.approvals.append(card);
      showApproval(ev);
      // Approvals block the work, so always bring them into view.
      requestAnimationFrame(() => card.scrollIntoView({ block: 'nearest', behavior: reduceMotion.matches ? 'auto' : 'smooth' }));
    } else if (ev.type === 'approval_resolved') {
      resolvedApproval(ev.id, ev.decision);
    } else if (ev.type === 'image') {
      ai.images.append(shotImg(ev.url));
      setTimeout(() => scrollToBottom(), 60);
    } else if (ev.type === 'status') {
      let note = $('.status-note', ai.el);
      if (!note) { note = document.createElement('p'); note.className = 'status-note'; ai.md.before(note); }
      note.textContent = ev.text;
    }
  }

  function finishTurn(ai, ev) {
    $$('.cube[data-live]').forEach((c) => { unscramble(c); c.removeAttribute('data-live'); });
    $('.status-note', ai.el)?.remove();
    if (ev.type === 'error') {
      ai.el.classList.add('msg-error');
      ai.md.innerHTML = md(ev.message);
      Cube.set('error');
    } else {
      ai.md.innerHTML = md(ev.text);
      ai.el.dataset.raw = ev.text;
      ai.actions.hidden = false;
      if (ev.engine === 'offline') { const t = $('.engine-tag', ai.el); t.hidden = false; t.textContent = 'offline'; }
      Cube.set('solved');
      // Voice in, voice out. Or every reply, if this device opted in.
      if (S.lastByVoice || store.get('speakReplies', false)) speak(ai.md.innerText, $('[data-msg-action="speak"]', ai.el));
    }
    $('time', ai.el).textContent = timeOf(Date.now() / 1000);
    if (nearBottom()) scrollToBottom();
    loadConversations();
  }

  /* ─── Approvals (shared across every device) ───────────────────────── */
  const DEVICE_NAME = { phone: 'your phone', tablet: 'your tablet', desktop: 'your desktop', terminal: 'the terminal' };

  function approvalCard(ev, opts = {}) {
    const rec = S.approvals.get(ev.id) || { ev, els: new Set(), decision: null };
    S.approvals.set(ev.id, rec);
    const el = document.createElement('div');
    el.className = 'approval';
    el.dataset.approval = ev.id;
    const remaining = Math.max(0, (ev.expires_at || 0) - Date.now() / 1000);
    const args = Object.entries(ev.args || {}).map(([k, v]) => `${k}: ${v}`).join('\n');
    const from = !S.myTurns.has(ev.turn_id) || (ev.device && ev.device !== S.mode) ? `<span class="from">Asked from ${esc(DEVICE_NAME[ev.device] || ev.device)}</span>` : '';
    el.innerHTML = `<div class="approval-top">${icon('shield')}<div>${from}<h4>${esc(ev.label)}?</h4><p class="risk">${esc(ev.risk || 'Rubiks wants to do this on your PC.')}</p></div></div>
      ${args ? `<pre>${esc(args)}</pre>` : ''}
      <div class="approval-timer"><i style="animation-duration:${remaining}s"></i></div>
      <div class="approval-actions"><button class="btn btn-quiet" data-decide="deny">Don't allow</button><button class="btn btn-primary" data-decide="approve">Allow once</button></div>`;
    if (opts.big) el.classList.add('approval--big');
    rec.els.add(el);
    if (rec.decision) markResolved(el, rec.decision);
    return el;
  }

  function markResolved(el, decision) {
    el.classList.add('resolved');
    if (el.querySelector('.approval-result')) return;
    const ok = decision === 'approve';
    const msg = ok ? 'Allowed' : decision === 'timeout' ? 'Timed out, nothing ran' : 'Not allowed, nothing ran';
    const p = document.createElement('div');
    p.className = 'approval-result ' + (ok ? 'ok' : 'no');
    p.innerHTML = ok ? `<svg class="draw" aria-hidden="true"><path d="M5 12.5 9.5 17 19 7.5"/></svg>${msg}` : `${icon('x')}${msg}`;
    el.append(p);
  }

  function resolvedApproval(id, decision) {
    const rec = S.approvals.get(id);
    if (!rec || rec.decision) return;
    rec.decision = decision;
    rec.els.forEach((el) => markResolved(el, decision));
    $$(`#approvals-box [data-approval="${id}"]`).forEach((el) => setTimeout(() => el.remove(), 2200));
    if (S.approvalModal?.id === id) setTimeout(() => closeModal(), decision === 'approve' ? 650 : 250);
    const pending = [...S.approvals.values()].some((r) => !r.decision);
    Cube.set(pending ? 'waiting' : (S.busy || S.remoteTurns.size ? 'thinking' : 'idle'));
  }

  document.addEventListener('click', async (e) => {
    const b = e.target.closest('[data-decide]');
    if (!b) return;
    const card = b.closest('[data-approval]');
    const id = card.dataset.approval;
    const decision = b.dataset.decide;
    $$('[data-decide]', card).forEach((x) => { x.disabled = true; });
    if (decision === 'approve') haptic.success(); else haptic.tap();
    resolvedApproval(id, decision); // optimistic
    try {
      await api(`/api/approvals/${id}`, { method: 'POST', body: { decision } });
    } catch (ex) {
      toast(ex.message);
    }
  });

  function showApproval(ev) {
    Cube.set('waiting');
    haptic.warn();
    if (!$(`#approvals-box [data-approval="${ev.id}"]`)) $('#approvals-box').prepend(approvalCard(ev));
    if (S.layout === 'phone' && !S.approvalModal) {
      const body = document.createElement('div');
      body.append(approvalCard(ev, { big: true }));
      const m = openModal({ title: 'Needs your OK', body, onClose: () => { S.approvalModal = null; } });
      S.approvalModal = { id: ev.id, ...m };
    } else if (S.layout !== 'desktop' && !S.myTurns.has(ev.turn_id)) {
      toast(`Rubiks needs your OK: ${ev.label}`);
    } else if (S.layout === 'desktop' && !S.myTurns.has(ev.turn_id) && html.classList.contains('no-activity')) {
      toggleActivity(true);
    }
  }

  /* ─── Live events from every device ────────────────────────────────── */
  function connectEvents() {
    S.es?.close();
    const es = new EventSource('/api/events');
    S.es = es;
    es.onmessage = (m) => {
      let ev; try { ev = JSON.parse(m.data); } catch { return; }
      if (ev.type === 'hello') { (ev.pending || []).forEach((p) => { if (!S.approvals.has(p.id)) showApproval(p); }); }
      else if (ev.type === 'approval') { if (!S.approvals.get(ev.id)?.els.size || !$(`#approvals-box [data-approval="${ev.id}"]`)) showApproval(ev); }
      else if (ev.type === 'approval_resolved') resolvedApproval(ev.id, ev.decision);
      else if (ev.type === 'tool' || ev.type === 'tool_done') railMove(ev);
      else if (ev.type === 'turn') {
        if (ev.state === 'end') S.remoteTurns.delete(ev.turn_id);
        if (S.myTurns.has(ev.turn_id)) return;
        if (ev.state === 'start') { S.remoteTurns.add(ev.turn_id); if (Cube.state === 'idle') Cube.set('thinking'); }
        else {
          S.remoteTurns.delete(ev.turn_id);
          loadConversations();
          if (ev.conversation_id === S.convId && !S.busy) openConversation(S.convId);
          if (!S.busy && Cube.state === 'thinking') Cube.set('idle');
        }
      }
    };
    es.onerror = () => { if (es.readyState === EventSource.CLOSED) setTimeout(() => S.user && connectEvents(), 4000); };
  }

  async function loadActivity() {
    try {
      const d = await api('/api/activity?limit=30');
      const list = $('#moves');
      list.innerHTML = '';
      d.items.reverse().forEach((r) => railMove({ type: 'tool_done', id: 'a' + r.id, name: r.tool, label: r.label, args: r.args, ok: r.ok !== 0 && r.decision !== 'deny' && r.decision !== 'timeout', ms: r.ms, ts: r.created_at, device: r.device, historic: true }));
      (d.pending || []).forEach((p) => { if (!S.approvals.has(p.id)) showApproval(p); });
    } catch { /* offline */ }
  }

  function railMove(ev) {
    const list = $('#moves');
    if (ev.label) TOOL_LABELS[ev.name] = ev.label;
    let li = ev.id ? $(`li[data-id="${ev.id}"]`, list) : null;
    if (!li) {
      li = document.createElement('li');
      li.dataset.id = ev.id || '';
      li.innerHTML = `<span class="t">${clock(ev.ts || Date.now() / 1000)}</span><span class="move-dot"></span><span class="lbl"><b></b><small></small></span><span class="move-ms"></span>`;
      list.prepend(li);
      if (!ev.historic) { li.classList.add('fresh'); requestAnimationFrame(() => setTimeout(() => li.classList.remove('fresh'), 1200)); }
      while (list.children.length > 40) list.lastElementChild.remove();
    }
    if (ev.label || !$('b', li).textContent) $('b', li).textContent = ev.label || toolLabel(ev.name);
    const arg = firstArg(ev.args);
    const src = ev.device && ev.device !== S.mode ? ` · ${ev.device}` : '';
    if (ev.type === 'tool') $('small', li).textContent = arg + src;
    const status = ev.type === 'tool' ? ev.status : ev.ok ? 'ok' : 'fail';
    li.setAttribute('data-status', status);
    li.querySelector('.move-dot').style.background = status === 'ok' ? 'var(--ok)' : status === 'fail' ? 'var(--danger)' : status === 'waiting' ? 'var(--amber)' : 'var(--ink)';
    if (ev.type === 'tool_done') { $('.move-ms', li).textContent = ev.ms ? fmtMs(ev.ms) : ''; if (ev.historic) $('small', li).textContent = arg + src; }
    $('#moves-empty').hidden = list.children.length > 0;
    $('#moves-count').textContent = list.children.length ? `· ${list.children.length}` : '';
  }

  /* ─── Vitals & tasks ───────────────────────────────────────────────── */
  function railVisible() {
    if (S.layout === 'desktop') return !html.classList.contains('no-activity');
    return $('#activity').classList.contains('open');
  }

  function pollVitals() {
    clearInterval(S.vitalsTimer);
    const tick = async () => {
      if (document.visibilityState !== 'visible' || !railVisible() || !S.user) return;
      try {
        const v = await api('/api/vitals');
        setVital('cpu', v.cpu, '%'); setVital('ram', v.ram, '%'); setVital('disk', v.disk, '%');
        if (v.battery) setVital('batt', v.battery.percent, v.battery.plugged ? '% ⚡' : '%', 100 - v.battery.percent);
        else { $('#v-batt').textContent = 'AC'; $('#vb-batt').style.width = '100%'; }
        $('#host-name').textContent = v.host || 'this PC';
      } catch { /* ignore */ }
    };
    tick();
    S.vitalsTimer = setInterval(tick, 5000);
  }
  function setVital(k, val, unit, heat) {
    if (val == null) return;
    $(`#v-${k}`).innerHTML = `${val}<small>${unit}</small>`;
    const bar = $(`#vb-${k}`);
    bar.style.width = Math.max(2, Math.min(100, val)) + '%';
    bar.classList.toggle('hot', (heat ?? val) > 85);
  }

  async function pollTask() {
    clearTimeout(S.taskTimer);
    if (!S.user) return;
    let t = null;
    try { t = (await api('/api/tasks')).task; } catch { /* ignore */ }
    const card = $('#task-card');
    const was = S.task;
    S.task = t;
    if (t) {
      card.hidden = false;
      $('#task-goal').textContent = t.goal;
      $('#task-progress').textContent = t.running ? t.progress : (t.summary || t.status);
      const [done, total] = (t.progress || '0/0').split('/').map(Number);
      $('#task-bar').style.width = total ? `${(done / total) * 100}%` : '6%';
      $('#task-steps').innerHTML = t.steps.map((s) => `<li data-s="${esc(s.status)}">${esc(s.description)}</li>`).join('');
      $('#task-stop').hidden = !t.running;
      if (was?.running && !t.running) { Cube.set('solved'); toast('Autonomous task finished'); haptic.success(); }
    } else card.hidden = true;
    S.taskTimer = setTimeout(pollTask, t?.running ? 3000 : 20000);
  }

  /* ─── Sheets & drawers ─────────────────────────────────────────────── */
  function openSheet(id) {
    const el = $('#' + id);
    closeSheets(true, id);
    el.style.setProperty('--drag', '0px');
    el.classList.add('open');
    $('#scrim').classList.add('on');
    if (id === 'activity') pollVitals();
  }
  function closeSheets(instant, except) {
    $$('.sheet-able.open').forEach((el) => { if (el.id !== except) el.classList.remove('open'); });
    if (!except) $('#scrim').classList.remove('on');
  }
  function toggleActivity(force) {
    if (S.layout === 'desktop') {
      const on = force ?? html.classList.contains('no-activity');
      html.classList.toggle('no-activity', !on);
      store.set('activity', on);
      if (on) pollVitals();
    } else if ($('#activity').classList.contains('open')) closeSheets(); else openSheet('activity');
  }

  // Drag-to-dismiss with rubber banding (phone sheets and modals).
  function draggable(sheet, handles, onDismiss) {
    let startY = 0, dy = 0, lastY = 0, lastT = 0, v = 0, active = false;
    const down = (e) => {
      if (S.layout !== 'phone' || e.button > 0) return;
      if (e.target.closest('button, a, input, textarea')) return;
      active = true; startY = lastY = e.clientY; lastT = performance.now(); dy = 0; v = 0;
      sheet.classList.add('dragging');
      e.currentTarget.setPointerCapture(e.pointerId);
    };
    const move = (e) => {
      if (!active) return;
      const now = performance.now();
      v = ((e.clientY - lastY) / Math.max(now - lastT, 1)) * 1000;
      lastY = e.clientY; lastT = now;
      const raw = e.clientY - startY;
      dy = raw < 0 ? -(1 - 1 / ((-raw * 0.55) / 300 + 1)) * 60 : raw; // resist upward
      sheet.style.setProperty('--drag', dy + 'px');
    };
    const up = () => {
      if (!active) return;
      active = false;
      sheet.classList.remove('dragging');
      if (dy > sheet.offsetHeight * 0.28 || v > 900) onDismiss(); else sheet.style.setProperty('--drag', '0px');
    };
    handles.forEach((h) => { h.addEventListener('pointerdown', down); h.addEventListener('pointermove', move); h.addEventListener('pointerup', up); h.addEventListener('pointercancel', up); });
  }
  ['sessions', 'activity'].forEach((id) => {
    const el = $('#' + id);
    draggable(el, [$('.sheet-grip', el), $('.rail-head', el)], () => closeSheets());
  });

  /* ─── Popovers (grow from their trigger) ───────────────────────────── */
  let popAnchor = null;
  function openPop(anchor, build) {
    closePop(true);
    const pop = document.createElement('div');
    pop.className = 'pop';
    pop.setAttribute('role', 'menu');
    build(pop);
    $('#layer').append(pop);
    const r = anchor.getBoundingClientRect();
    const pw = pop.offsetWidth, ph = pop.offsetHeight;
    let left = r.left + r.width / 2 > innerWidth / 2 ? r.right - pw : r.left;
    left = Math.max(8, Math.min(left, innerWidth - pw - 8));
    let top = r.bottom + 6, oy = '0px';
    if (top + ph > innerHeight - 8) { top = Math.max(8, r.top - ph - 6); oy = '100%'; }
    pop.style.left = left + 'px';
    pop.style.top = top + 'px';
    pop.style.transformOrigin = `${r.left + r.width / 2 - left}px ${oy}`;
    anchor.setAttribute('aria-expanded', 'true');
    popAnchor = anchor;
    requestAnimationFrame(() => pop.classList.add('open'));
    setTimeout(() => pop.querySelector('[role="menuitemradio"][aria-checked="true"], [role="menuitem"], [role="menuitemradio"]')?.focus({ preventScroll: true }), 30);
  }
  function closePop(instant) {
    const p = $('.pop');
    popAnchor?.setAttribute('aria-expanded', 'false');
    popAnchor = null;
    if (!p) return;
    p.classList.remove('open');
    if (instant) p.remove(); else setTimeout(() => p.remove(), 170);
  }
  document.addEventListener('pointerdown', (e) => {
    if ($('.pop') && !e.target.closest('.pop') && !e.target.closest('[aria-expanded="true"]')) closePop();
  });

  $('#mode-btn').addEventListener('click', (e) => {
    const btn = e.currentTarget;
    if (btn.getAttribute('aria-expanded') === 'true') { closePop(); return; }
    openPop(btn, (pop) => {
      pop.innerHTML = `<div class="pop-title">Using Rubiks on</div>` + Object.entries(MODES).map(([k, m]) =>
        `<button class="pop-item" role="menuitemradio" aria-checked="${k === S.mode}" data-mode="${k}">${icon(m.icon)}<span>${m.label}</span><small>${m.name}</small></button>`).join('');
      pop.addEventListener('click', (ev) => { const b = ev.target.closest('[data-mode]'); if (b) { setMode(b.dataset.mode); closePop(); } });
    });
  });

  $('#user-chip').addEventListener('click', (e) => {
    const btn = e.currentTarget;
    if (btn.getAttribute('aria-expanded') === 'true') { closePop(); return; }
    const owner = S.user?.role === 'owner';
    openPop(btn, (pop) => {
      pop.innerHTML = `
        <button class="pop-item" role="menuitem" data-go="settings">${icon('sliders')}<span>Settings</span></button>
        ${owner ? `<button class="pop-item" role="menuitem" data-go="connect">${icon('link')}<span>Connect your phone</span></button>` : ''}
        ${S.installEvt ? `<button class="pop-item" role="menuitem" data-go="install">${icon('download')}<span>Install app</span></button>` : ''}
        <button class="pop-item" role="menuitem" data-go="palette">${icon('command')}<span>Commands</span><small>Ctrl K</small></button>
        <div class="pop-sep"></div>
        <button class="pop-item danger" role="menuitem" data-go="logout">${icon('logout')}<span>Sign out</span></button>`;
      pop.addEventListener('click', (ev) => {
        const b = ev.target.closest('[data-go]');
        if (!b) return;
        closePop();
        ({ settings: () => openSettings(), connect: () => openSettings('devices'), install: installApp, palette: openPalette, logout })[b.dataset.go]();
      });
    });
  });

  async function installApp() {
    if (!S.installEvt) return;
    S.installEvt.prompt();
    try { await S.installEvt.userChoice; } catch { /* ignore */ }
    S.installEvt = null;
  }

  async function logout() {
    try { await api('/api/auth/logout', { method: 'POST' }); } catch { /* ignore */ }
    signedOut();
  }

  /* ─── Modal / bottom sheet ─────────────────────────────────────────── */
  let modal = null;
  function openModal({ title, body, onClose }) {
    closeModal(true);
    const wrap = document.createElement('div');
    wrap.className = 'modal-wrap';
    wrap.innerHTML = `<div class="modal-scrim"></div><div class="modal" role="dialog" aria-modal="true" aria-label="${esc(title)}">
      <div class="sheet-grip" aria-hidden="true"></div>
      <div class="modal-head"><h2>${esc(title)}</h2><button class="icon-btn" data-close aria-label="Close">${icon('x')}</button></div>
      <div class="modal-body"></div></div>`;
    $('.modal-body', wrap).append(body);
    $('#layer').append(wrap);
    const m = $('.modal', wrap);
    const close = () => closeModal();
    $('.modal-scrim', wrap).addEventListener('click', close);
    $('[data-close]', wrap).addEventListener('click', close);
    draggable(m, [$('.sheet-grip', m), $('.modal-head', m)], close);
    modal = { wrap, onClose, prevFocus: document.activeElement };
    m.tabIndex = -1;
    requestAnimationFrame(() => { wrap.classList.add('open'); setTimeout(() => m.focus({ preventScroll: true }), 40); });
    return { close, el: m };
  }
  function closeModal(instant) {
    if (!modal) return;
    const { wrap, onClose, prevFocus } = modal;
    modal = null;
    wrap.classList.remove('open');
    onClose?.();
    if (instant) wrap.remove(); else setTimeout(() => wrap.remove(), 200);
    prevFocus?.focus?.({ preventScroll: true });
  }

  function lightbox(src) {
    const lb = document.createElement('div');
    lb.className = 'lightbox';
    lb.innerHTML = `<img src="${esc(src)}" alt="Screenshot of your PC">`;
    lb.addEventListener('click', () => { lb.classList.remove('open'); setTimeout(() => lb.remove(), 180); });
    document.body.append(lb);
    requestAnimationFrame(() => lb.classList.add('open'));
  }

  /* ─── Settings ─────────────────────────────────────────────────────── */
  async function openSettings(focus) {
    let s;
    try { s = await api('/api/settings'); S.settings = s; } catch (e) { toast(e.message); return; }
    const u = S.user;
    const b = document.createElement('div');
    b.style.display = 'contents';
    const theme = store.get('theme', 'system');
    const themeIdx = ['system', 'light', 'dark'].indexOf(theme);
    b.innerHTML = `
      <section class="set-section" id="set-brain">
        <h3>Brain</h3>
        <p class="set-note">${s.engine === 'gemini'
          ? `Connected to <b>Gemini</b> (${esc(s.model)}) with ${s.key_count} key${s.key_count === 1 ? '' : 's'}. ${s.tools} PC tools ready.`
          : 'Offline mode: quick PC commands only. Add a free Gemini key to unlock the full brain, vision and autonomous tasks.'}</p>
        ${(s.key_notes || []).map((n) => `<p class="set-warn">${esc(n)}</p>`).join('')}
        ${s.is_owner ? `
          ${s.keys.length ? `<div class="key-list">${s.keys.map((k) => `<code>${esc(k)}<span class="muted">active</span></code>`).join('')}</div>` : ''}
          <div class="input-row"><input class="input" id="key-input" type="password" autocomplete="off" spellcheck="false" placeholder="Gemini key (AIza… or AQ.…)"><button class="btn btn-primary btn-sm" id="key-save">Add key</button></div>
          <p class="set-note">Get one at aistudio.google.com/apikey. Keys are saved to <code>.env</code> on this PC only. Add a few and Rubiks rotates between them when one hits a limit.</p>`
          : '<p class="set-note">Only the owner can change the AI key.</p>'}
      </section>
      <section class="set-section" id="set-voice">
        <h3>Voice</h3>
        <p class="set-note">${s.voice?.configured
          ? `Rubiks speaks with your ElevenLabs voice on every device (${esc(s.voice.model)}).`
          : "Rubiks uses this browser's built-in voice. Add ElevenLabs for a natural voice on every device."}</p>
        <div class="toggle-row"><span>Speak every reply on this device</span>
          <button class="switch" role="switch" id="speak-toggle" aria-checked="${store.get('speakReplies', false)}" aria-label="Speak every reply on this device"></button></div>
        <p class="set-note">Replies to messages you speak are always read back.</p>
        ${s.is_owner ? `
          <div class="input-row"><input class="input" id="el-key" type="password" autocomplete="off" spellcheck="false" placeholder="${s.voice?.key ? `ElevenLabs key saved (${esc(s.voice.key)})` : 'ElevenLabs API key (sk_…)'}"></div>
          <div class="input-row"><input class="input" id="el-voice" autocomplete="off" spellcheck="false" placeholder="Voice ID" value="${esc(s.voice?.voice_id || '')}"><button class="btn btn-quiet btn-sm" id="el-save">Save</button></div>
          ${s.voice?.configured ? '<button class="btn btn-quiet btn-sm" id="el-test">Play a sample</button>' : ''}` : ''}
      </section>
      <section class="set-section">
        <h3>How Rubiks talks</h3>
        <div class="radio-list" data-pref="persona">
          <button class="radio" role="radio" aria-checked="${u.persona !== 'casual'}" data-v="professional"><span><b>Professional<span class="rec">Recommended</span></b><small>Calm and direct, like a chief of staff.</small></span></button>
          <button class="radio" role="radio" aria-checked="${u.persona === 'casual'}" data-v="casual"><span><b>Casual</b><small>Witty, Gen Z energy. The original Rubiks vibe.</small></span></button>
        </div>
      </section>
      <section class="set-section">
        <h3>Safety</h3>
        <div class="radio-list" data-pref="approval_mode">
          <button class="radio" role="radio" aria-checked="${u.approval_mode !== 'autopilot'}" data-v="ask"><span><b>Ask before risky actions<span class="rec">Recommended</span></b><small>Commands, code, messages and closing apps wait for your OK on any device.</small></span></button>
          <button class="radio" role="radio" aria-checked="${u.approval_mode === 'autopilot'}" data-v="autopilot"><span><b>Full autopilot at the desk</b><small>Desktop and terminal run everything without asking. Phone and tablet still ask.</small></span></button>
        </div>
      </section>
      <section class="set-section">
        <h3>About you</h3>
        <label class="field"><textarea id="about-input" maxlength="2000" placeholder="What you do, how you like things done, people you message often. Rubiks reads this before every answer.">${esc(u.about || '')}</textarea></label>
        <div class="set-row"><span class="set-note">Private to you.</span><button class="btn btn-quiet btn-sm" id="about-save">Save</button></div>
      </section>
      <section class="set-section" id="set-devices">
        <h3>Your devices</h3>
        ${s.is_owner && s.public_url ? `
          <p class="set-note"><b>Secure link</b> (works anywhere, mic and app install included):</p>
          <button class="code-copy" data-copy="${esc(s.public_url)}"><code>${esc(s.public_url)}</code>${icon('copy')}</button>
          <p class="set-note">Open it on your phone, sign in, then <b>Add to Home Screen</b>. This address changes each time <code>rubiks tunnel</code> restarts.</p>` : ''}
        ${s.is_owner ? (s.lan_urls.length
          ? `<p class="set-note">${s.public_url ? 'Same Wi-Fi only (no mic or install on plain http):' : 'On your phone or tablet (same Wi-Fi), open:'}</p>${s.lan_urls.map((url) => `<button class="code-copy" data-copy="${esc(url)}"><code>${esc(url)}</code>${icon('copy')}</button>`).join('')}
             ${s.public_url ? '' : '<p class="set-note">For the mic and installing the app, run <code>rubiks tunnel</code> on the PC to get a secure https link.</p>'}`
          : `<p class="set-note">Phone access is off. In a terminal on this PC, run <code>rubiks serve --lan</code>, or <code>rubiks tunnel</code> for a secure link that works anywhere.</p>`) : ''}
        ${s.is_owner ? `<div class="set-row"><span class="set-note">Give a teammate their own login.</span><button class="btn btn-quiet btn-sm" id="invite-btn">Create invite</button></div><div id="invite-out"></div>` : ''}
      </section>
      <section class="set-section">
        <h3>Appearance</h3>
        <div class="seg" id="theme-seg" style="--n:3;--idx:${themeIdx}"><span class="seg-thumb"></span>
          ${['system', 'light', 'dark'].map((t) => `<button type="button" role="radio" aria-checked="${t === theme}" data-theme-v="${t}">${t[0].toUpperCase() + t.slice(1)}</button>`).join('')}
        </div>
      </section>
      <p class="set-note">Rubiks ${esc(s.version)} · ${s.tools} tools · signed in as ${esc(u.email)}</p>`;

    openModal({ title: 'Settings', body: b });

    $$('[data-pref]', b).forEach((group) => group.addEventListener('click', async (e) => {
      const r = e.target.closest('.radio');
      if (!r) return;
      $$('.radio', group).forEach((x) => x.setAttribute('aria-checked', String(x === r)));
      haptic.tap();
      try { S.user = (await api('/api/me', { method: 'PATCH', body: { [group.dataset.pref]: r.dataset.v } })).user; toast('Saved'); } catch (ex) { toast(ex.message); }
    }));
    $('#about-save', b)?.addEventListener('click', async () => {
      try { S.user = (await api('/api/me', { method: 'PATCH', body: { about: $('#about-input', b).value } })).user; toast('Saved. Rubiks will use this from now on.'); } catch (ex) { toast(ex.message); }
    });
    $('#key-save', b)?.addEventListener('click', async () => {
      const key = $('#key-input', b).value.trim();
      if (!key) { $('#key-input', b).focus(); return; }
      try {
        const r = await api('/api/settings/key', { method: 'POST', body: { key } });
        updateEngine(r.engine);
        toast('Key added. Full brain is on.');
        closeModal();
        Cube.set('solved');
      } catch (ex) { toast(ex.message); }
    });
    $('#speak-toggle', b).addEventListener('click', (e) => {
      const on = e.currentTarget.getAttribute('aria-checked') !== 'true';
      e.currentTarget.setAttribute('aria-checked', String(on));
      store.set('speakReplies', on);
      unlockAudio();
      haptic.tap();
    });
    $('#el-save', b)?.addEventListener('click', async () => {
      const body = { api_key: $('#el-key', b).value.trim(), voice_id: $('#el-voice', b).value.trim() };
      try {
        const r = await api('/api/settings/voice', { method: 'POST', body });
        S.settings = await api('/api/settings');
        toast(r.configured ? 'Voice saved. Rubiks sounds like this everywhere now.' : 'Saved. Add both the key and a voice ID.');
        $('#el-key', b).value = '';
      } catch (ex) { toast(ex.message); }
    });
    $('#el-test', b)?.addEventListener('click', (e) => {
      unlockAudio();
      speak(`Hey ${(S.user?.name || '').split(' ')[0]}, this is Rubiks. Your PC is ready when you are.`, e.currentTarget);
    });
    $('#invite-btn', b)?.addEventListener('click', async () => {
      try { const r = await api('/api/invites', { method: 'POST' }); $('#invite-out', b).innerHTML = `<p class="invite-code">${esc(r.code)}</p><p class="set-note">Single use. They pick "Join with invite" on the sign-in screen.</p>`; } catch (ex) { toast(ex.message); }
    });
    $('#theme-seg', b).addEventListener('click', (e) => {
      const t = e.target.closest('[data-theme-v]');
      if (!t) return;
      const v = t.dataset.themeV;
      store.set('theme', v); applyTheme(v);
      e.currentTarget.style.setProperty('--idx', ['system', 'light', 'dark'].indexOf(v));
      $$('[data-theme-v]', b).forEach((x) => x.setAttribute('aria-checked', String(x === t)));
    });
    if (focus === 'devices') setTimeout(() => $('#set-devices', b)?.scrollIntoView({ block: 'start', behavior: reduceMotion.matches ? 'auto' : 'smooth' }), 120);
  }

  document.addEventListener('click', async (e) => {
    const c = e.target.closest('[data-copy]');
    if (!c) return;
    await copyText(c.dataset.copy);
    toast('Copied');
  });

  /* ─── Command palette (instant: it's used constantly) ──────────────── */
  function openPalette() {
    if ($('.palette-wrap')) return;
    closePop(true);
    const base = [
      { label: 'New chat', icon: 'new', run: () => newChat() },
      { label: 'Toggle live activity', icon: 'panel', hint: 'Ctrl .', run: () => toggleActivity() },
      ...Object.entries(MODES).map(([k, m]) => ({ label: `Switch to ${m.label} mode`, icon: m.icon, hint: m.name, run: () => setMode(k) })),
      { label: 'Settings', icon: 'sliders', run: () => openSettings() },
      ...(S.user?.role === 'owner' ? [{ label: 'Connect your phone', icon: 'link', run: () => openSettings('devices') }] : []),
      { label: 'Sign out', icon: 'logout', run: logout },
      ...S.convs.slice(0, 8).map((c) => ({ label: c.title, icon: 'history', hint: 'Chat', run: () => openConversation(c.id) })),
    ];
    const wrap = document.createElement('div');
    wrap.className = 'palette-wrap';
    wrap.innerHTML = `<div class="palette" role="dialog" aria-label="Commands"><input placeholder="Type a command or search chats…" aria-label="Command"><ul role="listbox"></ul></div>`;
    document.body.append(wrap);
    const inp = $('input', wrap), ul = $('ul', wrap);
    let items = base, sel = 0;
    const render = () => {
      ul.innerHTML = items.length ? items.map((it, i) => `<li role="option" aria-selected="${i === sel}" data-i="${i}">${icon(it.icon)}<span>${esc(it.label)}</span>${it.hint ? `<small>${esc(it.hint)}</small>` : ''}</li>`).join('')
        : `<li class="none">Nothing matches. Press Enter to ask Rubiks.</li>`;
      ul.querySelector('[aria-selected="true"]')?.scrollIntoView({ block: 'nearest' });
    };
    const close = () => wrap.remove();
    const runAt = (i) => { const it = items[i]; close(); if (it) it.run(); else if (inp.value.trim()) send(inp.value); };
    inp.addEventListener('input', () => { const q = inp.value.toLowerCase().trim(); items = base.filter((it) => it.label.toLowerCase().includes(q)); sel = 0; render(); });
    inp.addEventListener('keydown', (e) => {
      if (e.key === 'ArrowDown') { e.preventDefault(); sel = Math.min(sel + 1, items.length - 1); render(); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); sel = Math.max(sel - 1, 0); render(); }
      else if (e.key === 'Enter') { e.preventDefault(); runAt(sel); }
      else if (e.key === 'Escape') close();
    });
    ul.addEventListener('click', (e) => { const li = e.target.closest('[data-i]'); if (li) runAt(+li.dataset.i); });
    wrap.addEventListener('pointerdown', (e) => { if (e.target === wrap) close(); });
    render();
    requestAnimationFrame(() => wrap.classList.add('open'));
    inp.focus();
  }

  /* ─── Voice input ──────────────────────────────────────────────────── */
  // Two engines: the browser's own speech recognition (live words, Chrome/Safari), or
  // record-and-transcribe through the PC (Gemini) everywhere else. Both need https.
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  const canRecord = !!(navigator.mediaDevices?.getUserMedia && window.MediaRecorder);
  let rec = null, recorder = null, recStop = null;
  let preferRecorder = store.get('preferRecorder', false);
  function setupMic() {
    const mic = $('#mic');
    mic.hidden = false; // always offered; explains itself when it can't work
    mic.setAttribute('aria-label', 'Talk to Rubiks');
  }
  const MIC_BLOCKED = 'Microphone is blocked for this site. Tap the lock icon next to the address, allow Microphone, then try again.';

  function needSecureLink() {
    const url = S.settings?.public_url;
    const b = document.createElement('div');
    b.style.display = 'contents';
    b.innerHTML = `<p class="set-note">Phones only allow the microphone (and installing the app) on a secure <b>https://</b> link. You're on a plain local link.</p>
      ${url ? `<a class="btn btn-primary btn-block" href="${esc(url)}">Open the secure link</a><button class="code-copy" data-copy="${esc(url)}"><code>${esc(url)}</code>${icon('copy')}</button>`
        : `<p class="set-note">On the PC, run <code>rubiks tunnel</code>. It prints an <b>https://</b> address. Open that on your phone and the mic works.</p>`}`;
    openModal({ title: 'Voice needs the secure link', body: b });
  }

  function micOn() { $('#mic').classList.add('listening'); haptic.tap(); unlockAudio(); stopSpeaking(); }
  function micOff() { $('#mic').classList.remove('listening'); }

  function startBrowserRecognition() {
    rec = new SR();
    rec.lang = navigator.language || 'en-IN';
    rec.interimResults = true;
    let finalText = '';
    rec.onresult = (e) => {
      let interim = '';
      for (let i = e.resultIndex; i < e.results.length; i++) { const r = e.results[i]; if (r.isFinal) finalText += r[0].transcript; else interim += r[0].transcript; }
      input.value = (finalText + interim).trim(); autosize(); updateSend();
    };
    rec.onend = () => { micOff(); rec = null; if (finalText.trim()) send(finalText, { voice: true }); };
    rec.onerror = (e) => {
      micOff(); rec = null;
      if (e.error === 'not-allowed') toast(MIC_BLOCKED);
      else if (e.error === 'no-speech' || e.error === 'aborted') toast("Didn't hear anything. Tap the mic and speak.");
      else if (canRecord) { preferRecorder = true; store.set('preferRecorder', true); toast('Switched to a different voice engine. Tap the mic again.'); }
      else toast("Couldn't hear that. Try again.");
    };
    micOn();
    rec.start();
  }

  async function startRecording() {
    let stream;
    try { stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } }); }
    catch (e) { toast(e.name === 'NotAllowedError' ? MIC_BLOCKED : "Couldn't open the microphone."); return; }
    const type = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4', 'audio/ogg'].find((t) => MediaRecorder.isTypeSupported?.(t)) || '';
    recorder = new MediaRecorder(stream, type ? { mimeType: type } : undefined);
    const chunks = [];
    recorder.ondataavailable = (e) => { if (e.data.size) chunks.push(e.data); };
    recorder.onstop = async () => {
      stream.getTracks().forEach((t) => t.stop());
      clearTimeout(recStop);
      micOff();
      recorder = null;
      const blob = new Blob(chunks, { type: (type || 'audio/webm').split(';')[0] });
      if (blob.size < 800) { toast("Didn't hear anything. Tap, speak, then tap again."); return; }
      input.placeholder = 'Listening back…';
      try {
        const res = await fetch('/api/transcribe', { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': blob.type, 'X-Rubiks': '1' }, body: blob });
        const d = await res.json().catch(() => ({}));
        if (!res.ok || d.ok === false) throw new Error(d.error || 'Transcription failed.');
        if (d.text) send(d.text, { voice: true }); else toast("Didn't catch any words. Try again.");
      } catch (e) { toast(e.message); }
      finally { input.placeholder = 'Ask Rubiks to do something…'; }
    };
    micOn();
    recorder.start();
    input.placeholder = 'Listening… tap the mic when done';
    recStop = setTimeout(() => recorder?.state === 'recording' && recorder.stop(), 60000);
  }

  $('#mic').addEventListener('click', () => {
    if (rec) { rec.stop(); return; }
    if (recorder) { recorder.stop(); return; }
    if (!window.isSecureContext) { needSecureLink(); return; }
    if (SR && !preferRecorder) startBrowserRecognition();
    else if (canRecord) startRecording();
    else toast("This browser can't record audio. Try Chrome or Safari.");
  });

  /* ─── Global actions & shortcuts ───────────────────────────────────── */
  document.addEventListener('click', (e) => {
    const a = e.target.closest('[data-action]');
    if (!a) return;
    const act = a.dataset.action;
    if (act === 'new-chat') newChat();
    else if (act === 'open-sessions') openSheet('sessions');
    else if (act === 'open-activity') { if (S.layout !== 'desktop') openSheet('activity'); else toggleActivity(true); }
    else if (act === 'close-activity' || act === 'close-sheets') closeSheets();
    else if (act === 'toggle-activity') toggleActivity();
    else if (act === 'settings') openSettings();
    else if (act === 'stop-task') api('/api/tasks/stop', { method: 'POST' }).then((r) => toast(r.message)).catch((ex) => toast(ex.message));
    else if (act === 'retry') { show('screen-boot'); boot(); }
  });

  document.addEventListener('keydown', (e) => {
    if ($('#screen-app').hidden) return;
    const mod = e.ctrlKey || e.metaKey;
    if (mod && e.key.toLowerCase() === 'k') { e.preventDefault(); openPalette(); }
    else if (mod && e.key === '.') { e.preventDefault(); toggleActivity(); }
    else if (e.key === 'Escape') { if ($('.pop')) closePop(); else if (modal) closeModal(); else closeSheets(); }
    else if (e.key === '/' && !e.target.closest('input, textarea')) { e.preventDefault(); input.focus(); }
  });

  document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible' && S.user && !$('#screen-app').hidden) { pollTask(); loadConversations(); } });

  boot();
})();
