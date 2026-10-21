// 互動：Discord 登入、點歌控制(佇列面板 + 歌詞畫面的 ⏸ ⏭)、表情反應飄字。後端 cogs/act_live.py。
// 沒登入(或機器人沒設 client secret)也看得到佇列，只是按鈕停用。

import { mockFun } from './fun_mock.js';

const $ = (id) => document.getElementById(id);
const EMOJIS = ['👍', '😂', '🔥', '😭', '❤️', '🎉', '👏', '💀', '🤯', '😎'];
const MAX_FLOAT = 40;          // 同時飄在畫面上的表情上限
const TOAST_MS = 3200;
const VOL_GAP = 150;           // ms：拖音量條時送出的最短間隔

const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const fmt = (s) => { s = Math.max(0, Math.floor(s || 0)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`; };
const ytId = (u) => ((u || '').match(/(?:v=|youtu\.be\/)([\w-]{11})/) || [])[1] || '';

// ── 活動面板(fun.js)用的請求/推播 ──
const MOCK = new URLSearchParams(location.search).has('mock');   // ?mock=1：不連 WebSocket，用 fun_mock.js 的假後端
const funHandlers = { push: null, celebrate: null, auth: null };
const funPending = new Map();   // id → {resolve, reject, timer}
let funSeq = 0;
export const isMock = MOCK;
export const getMe = () => (MOCK ? '測試員' : me);
export function onFun(h) { Object.assign(funHandlers, h); }

// 送一個活動 op，回傳 data；失敗 / 逾時 reject(Error)
export function sendFun(op, args = {}) {
  if (MOCK) return mockFun(op, args);
  if (!ws || ws.readyState !== WebSocket.OPEN) return Promise.reject(new Error('還沒連上伺服器，稍後再試'));
  const id = ++funSeq;
  const ms = op === 'link_add' ? 30000 : 20000;
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => { funPending.delete(id); reject(new Error('伺服器沒有回應，請再試一次')); }, ms);
    funPending.set(id, { resolve, reject, timer });
    ws.send(JSON.stringify({ ...args, type: 'fun', id, op }));
  });
}
function funReply(m) {
  const p = funPending.get(m.id);
  if (!p) return;
  funPending.delete(m.id);
  clearTimeout(p.timer);
  if (m.ok) p.resolve(m.data); else p.reject(new Error(m.error || '失敗了'));
}

let ws = null;
let sid = '';
let me = null;          // 登入的名字；null = 沒登入
let canAuth = false;
let queue = null;       // 最後一份 {type:'queue', current, queue, total, autoplay, paused, repeat}
let target = { g: '', c: '' };
let volDrag = false;    // 正在拖音量條：別被伺服器推來的舊值拉回去
let volSent = 0;

// ── 登入：authorize → 後端換 token → authenticate ──
export async function login(sdk, clientId) {
  try {
    const { code } = await sdk.commands.authorize({
      client_id: clientId, response_type: 'code', state: '', prompt: 'none', scope: ['identify'],
    });
    const r = await fetch('api/token', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ code }) });
    if (!r.ok) throw new Error(`token ${r.status}`);
    const j = await r.json();
    await sdk.commands.authenticate({ access_token: j.access_token });
    sid = j.sid;
    return j.user;
  } catch (e) {
    console.warn('Activity 登入失敗，改成只能看', e);
    return null;
  }
}

function send(obj) {
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(obj));
}

export function startLive(guildId, channelId) {
  target = { g: guildId || '', c: channelId || '' };
  buildReactBar();
  bindQueue();
  connect();
}

function connect() {
  const url = new URL(`api/live?g=${encodeURIComponent(target.g)}&c=${encodeURIComponent(target.c)}&sid=${encodeURIComponent(sid)}`, location.href);
  url.protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const sock = new WebSocket(url);
  ws = sock;
  sock.onmessage = (ev) => {
    let m;
    try { m = JSON.parse(ev.data); } catch { return; }
    if (m.type === 'me') { me = m.user; canAuth = m.canAuth; updateLock(); if (funHandlers.auth) funHandlers.auth(); }
    else if (m.type === 'fun') funReply(m);
    else if (m.type === 'fun-push') { if (funHandlers.push) funHandlers.push(m.what, m.data); }
    else if (m.type === 'celebrate') { if (funHandlers.celebrate) funHandlers.celebrate(m); }
    else if (m.type === 'queue') { queue = m; renderQueue(); }
    else if (m.type === 'react') floatEmoji(m.emoji, m.name);
    else if (m.type === 'toast') toast(m.text, m.ok);
  };
  sock.onclose = () => {
    if (ws !== sock) return;
    for (const [id, p] of funPending) { clearTimeout(p.timer); p.reject(new Error('連線中斷，請再試一次')); funPending.delete(id); }
    setTimeout(connect, 2000);
  };
}

// 沒登入：按鈕停用，滑過去說明原因
function updateLock() {
  const why = me ? '' : canAuth ? '要先授權登入才能操作(重開 Activity 時按「授權」)' : '機器人還沒設定登入，暫時只能看';
  document.documentElement.classList.toggle('live-locked', !me);
  document.querySelectorAll('.act, #react-bar button').forEach((b) => {
    b.disabled = !me;
    if (b.dataset.title === undefined) b.dataset.title = b.title;
    b.title = why || b.dataset.title;
  });
  $('q-input').disabled = !me;
  $('q-input').placeholder = me ? '歌名或 YouTube 網址' : why;
  $('q-who').textContent = me ? `・${me}` : '';
}

// ── 表情反應 ──
function buildReactBar() {
  const bar = $('react-bar');
  if (bar.childElementCount) return;
  bar.innerHTML = EMOJIS.map((e) => `<button data-e="${e}" title="送出 ${e}">${e}</button>`).join('');
  bar.addEventListener('click', (ev) => {
    const b = ev.target.closest('button[data-e]');
    if (b && !b.disabled) send({ type: 'react', emoji: b.dataset.e });
  });
}

function floatEmoji(emoji, name) {
  const layer = $('reacts');
  while (layer.childElementCount >= MAX_FLOAT) layer.firstElementChild.remove();
  const el = document.createElement('span');
  el.className = 'fl';
  el.style.left = `${8 + Math.random() * 84}%`;
  el.style.setProperty('--drift', `${Math.round((Math.random() - 0.5) * 120)}px`);
  el.style.animationDuration = `${2.6 + Math.random() * 1.2}s`;
  el.innerHTML = `<b>${esc(emoji)}</b><small>${esc(name)}</small>`;
  el.addEventListener('animationend', () => el.remove());
  layer.appendChild(el);
}

function toast(text, ok = true) {
  const t = $('toast');
  t.textContent = text;
  t.className = `show ${ok ? 'ok' : 'bad'}`;
  clearTimeout(toast.t);
  toast.t = setTimeout(() => { t.className = ''; }, TOAST_MS);
}

// ── 佇列面板 ──
function bindQueue() {
  if (bindQueue.done) return;
  bindQueue.done = true;
  const panel = $('qpanel');
  $('q-fab').onclick = () => { panel.hidden = !panel.hidden; if (!panel.hidden && me) $('q-input').focus(); };
  $('q-close').onclick = () => { panel.hidden = true; };
  const add = (next) => {
    const q = $('q-input').value.trim();
    if (!q || !me) return;
    send({ type: 'add', query: q, next });
    $('q-input').value = '';
  };
  $('q-form').onsubmit = (ev) => { ev.preventDefault(); add(false); };
  $('q-next').onclick = () => add(true);
  // 音量：拖的途中每 VOL_GAP 毫秒送一次(quiet，不廣播)，放開才送正式的那次
  const vol = $('vol-in');
  vol.addEventListener('input', () => {
    volDrag = true;
    showVol(+vol.value);
    const now = performance.now();
    if (now - volSent >= VOL_GAP) { volSent = now; send({ type: 'music', action: 'volume', level: +vol.value, quiet: true }); }
  });
  vol.addEventListener('change', () => { volDrag = false; send({ type: 'music', action: 'volume', level: +vol.value }); });
  $('c-play').onclick = () => send({ type: 'music', action: queue && queue.paused ? 'resume' : 'pause' });
  $('c-skip').onclick = () => send({ type: 'music', action: 'skip' });
  $('q-list').addEventListener('click', (ev) => {
    const b = ev.target.closest('button[data-i]');
    if (b && !b.disabled) send({ type: 'music', action: 'remove', index: +b.dataset.i, url: b.dataset.u });
  });
  document.addEventListener('keydown', (ev) => { if (ev.key === 'Escape') panel.hidden = true; });
  // 封面抓不到(影片下架、不是 YouTube)→ 換成音符
  panel.addEventListener('error', (ev) => {
    if (ev.target.tagName !== 'IMG') return;
    const ph = document.createElement('span');
    ph.className = 'nothumb';
    ph.textContent = '♪';
    ev.target.replaceWith(ph);
  }, true);
}

function songRow(s, i) {
  const vid = ytId(s.url);
  const thumb = vid ? `<img src="api/ytthumb/${vid}.jpg" alt="" loading="lazy">` : '<span class="nothumb">♪</span>';
  const rm = i == null ? '' : `<button class="act rm" data-i="${i}" data-u="${esc(s.url)}" title="從佇列移除"${me ? '' : ' disabled'}>✕</button>`;
  return `<li>${thumb}<div class="qt"><span>${esc(s.title)}</span><small>${fmt(s.duration)}${s.by ? `・${esc(s.by)} 點的` : ''}</small></div>${rm}</li>`;
}

function showVol(v) {
  $('vol-val').textContent = v;
  $('vol-ic').textContent = v === 0 ? '🔇' : v < 40 ? '🔈' : v < 75 ? '🔉' : '🔊';
}

function renderQueue() {
  const q = queue;
  $('q-count').textContent = q && q.total ? ` ${q.total}` : '';
  if (q && q.volume != null && !volDrag) { $('vol-in').value = q.volume; showVol(q.volume); }   // 別人調的也跟著動
  $('c-play').textContent = q && q.paused ? '▶' : '⏸';
  $('c-play').title = q && q.paused ? '繼續播放' : '暫停';
  if (!q) return;
  $('q-now').innerHTML = q.current ? `<div class="qh">正在播放${q.repeat !== 'off' ? `<small>循環 ${q.repeat === 'one' ? '單曲' : '全部'}</small>` : ''}</div><ul>${songRow(q.current)}</ul>`
    : '<div class="qh dim">目前沒有在播放，輸入歌名就會開始播</div>';
  const rows = q.queue.map((s, i) => songRow(s, i)).join('');
  const more = q.total > q.queue.length ? `<li class="more">… 還有 ${q.total - q.queue.length} 首</li>` : '';
  const auto = q.autoplay ? `<div class="qh">Autoplay 下一首</div><ul class="auto">${songRow(q.autoplay)}</ul>` : '';
  $('q-list').innerHTML = (rows ? `<div class="qh">待播 ${q.total} 首</div><ul>${rows}${more}</ul>` : '<div class="qh dim">待播清單是空的</div>') + auto;
  // 重畫後補上停用狀態
  updateLock();
}
