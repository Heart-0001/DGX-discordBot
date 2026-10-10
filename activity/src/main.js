import { DiscordSDK } from '@discord/embedded-app-sdk';
import { startLol, stopLol } from './lol.js';
import { startValo, stopValo } from './valo.js';

// 機器人量到的位置 = 已送出的音框。實測(2026-10-08)歌詞要比這個位置早 0.25 秒亮才對得上耳朵，
// 所以預設提前 0.25 秒；使用者可用右下角 ± 再微調(存在 localStorage)。
const BASE_LEAD = 0.25;
const OFFSET_KEY = 'lyricsOffset.v2';  // v1 的值是以舊基準(慢 0.25s)存的，換 key 讓大家從 0 開始
const OFFSET_STEP = 0.1;
const GAP_MIN = 4;          // 秒：空白超過這麼久就顯示間奏三個點
const SAMPLE_WINDOW = 6000; // ms：對時取樣視窗
const ACTIVE_ANCHOR = 0.32; // 目前這行停在歌詞區的高度比例
const PIP_MIN_FONT = 15;    // px：小視窗縮字下限
const PIP_LINE_HEIGHT = 1.2; // 要跟 style.css 的 html.pip .line.active line-height 一致
const FADE_TOP = 0.16;      // style.css 的 mask 上方淡出到 14%，留一點餘裕

const $ = (id) => document.getElementById(id);
const app = $('app'), lyricsEl = $('lyrics'), statusEl = $('status');
const mini = $('mini');   // 遊戲畫面時，歌詞縮成頂列左邊的一小條

// ── 使用者延遲微調 ──
let userOffset = 0;
try { userOffset = parseFloat(localStorage.getItem(OFFSET_KEY)) || 0; } catch { /* 無痕或被擋 */ }
function setOffset(v) {
  userOffset = Math.round(v * 10) / 10;
  $('off-val').textContent = `${userOffset > 0 ? '+' : ''}${userOffset.toFixed(1)}s`;
  try { localStorage.setItem(OFFSET_KEY, String(userOffset)); } catch { /* ignore */ }
  const box = $('offset');
  box.classList.add('show');
  clearTimeout(setOffset.t);
  setOffset.t = setTimeout(() => box.classList.remove('show'), 1500);
}
$('off-minus').onclick = () => setOffset(userOffset - OFFSET_STEP);
$('off-plus').onclick = () => setOffset(userOffset + OFFSET_STEP);
setOffset(userOffset);
$('offset').classList.remove('show');

// ── 對時：位置 - 本地時間 在播放中是常數；網路延遲只會讓它變小，取視窗內最大值 ──
const clock = {
  samples: [], best: null, paused: true, pausedPos: 0,
  reset() { this.samples = []; this.best = null; },
  feed(position, paused) {
    const now = performance.now();
    if (paused) {
      this.paused = true; this.pausedPos = position; this.reset();
      return;
    }
    if (this.paused) { this.paused = false; this.reset(); }
    const off = position - now / 1000;
    // 跳轉(續播) → 大幅變大；卡頓(串流斷) → 大幅變小。兩者都重新取樣
    if (this.best !== null && (off > this.best + 0.5 || off < this.best - 0.3)) this.reset();
    this.samples.push({ at: now, off });
    this.samples = this.samples.filter((s) => now - s.at < SAMPLE_WINDOW);
    this.best = Math.max(...this.samples.map((s) => s.off));
  },
  now() {
    if (this.paused || this.best === null) return this.pausedPos;
    return performance.now() / 1000 + this.best;
  },
};

// ── 畫面狀態 ──
let track = null;     // {key,title,artist,duration,thumb}
let lines = [];       // [{t, text, el, gap}]
let activeIdx = -2;

const fmt = (s) => {
  s = Math.max(0, Math.floor(s));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
};

function showStatus(text) { statusEl.textContent = text || ''; }

function setImage(src) {
  for (const img of [$('cover'), $('bg-img'), $('bg-img2')]) {
    img.classList.remove('show');
    if (!src) { img.removeAttribute('src'); continue; }
    img.onload = () => img.classList.add('show');
    img.src = src;
  }
}

function onTrack(m) {
  track = m;
  app.classList.remove('idle');
  $('title').textContent = m.title;
  $('artist').textContent = m.artist;
  $('mini-title').textContent = m.artist ? `${m.title} — ${m.artist}` : m.title;
  $('mini-cover').src = m.thumb || '';
  setImage(m.thumb);
  clock.reset();
  clock.pausedPos = 0;
  renderLines(null);
  showStatus('搜尋歌詞中…');
}

function renderLines(data) {
  lyricsEl.innerHTML = '';
  lyricsEl.className = '';
  lyricsEl.style.transform = '';
  lines = [];
  activeIdx = -2;
  if (!data) return;
  if (data.synced) {
    const src = data.synced;
    const add = (t, text) => {
      const el = document.createElement('div');
      const gap = !text;
      el.className = gap ? 'line gap' : 'line';
      if (gap) el.innerHTML = '<i></i><i></i><i></i>';
      else el.textContent = text;
      lyricsEl.appendChild(el);
      lines.push({ t, text, el, gap });
    };
    if (src.length && src[0].t > GAP_MIN) add(0, '');
    src.forEach((l, i) => {
      const next = src[i + 1];
      if (!l.text) {
        // 空白行：間隔夠長才畫三個點，太短就當作延續上一行
        if (next && next.t - l.t >= GAP_MIN) add(l.t, '');
        return;
      }
      add(l.t, l.text);
    });
    showStatus('');
  } else if (data.plain) {
    lyricsEl.className = 'plain';
    const el = document.createElement('div');
    el.className = 'line';
    el.textContent = data.plain;
    lyricsEl.appendChild(el);
    showStatus('');
  } else {
    showStatus('找不到這首歌的歌詞');
  }
}

function setActive(idx) {
  if (idx === activeIdx) return;
  activeIdx = idx;
  lines.forEach((l, i) => {
    const d = Math.abs(i - idx);
    l.el.classList.toggle('active', i === idx);
    l.el.classList.toggle('past', i < idx);
    l.el.style.setProperty('--blur', `${Math.min(d, 4) * 0.5}px`);
  });
  fitPip();
  const wrapH = $('lyrics-wrap').clientHeight;
  const target = lines[Math.max(idx, 0)]?.el;
  if (!target) return;
  // 目前這句置中在 ACTIVE_ANCHOR，但頂端不能進入上方 14% 的淡出區(兩、三行的長句會被淡掉)
  const top = Math.max(wrapH * ACTIVE_ANCHOR - target.offsetHeight / 2, wrapH * FADE_TOP);
  const y = top - target.offsetTop;
  lyricsEl.style.transform = `translateY(${Math.round(y)}px)`;
}

// 小視窗：目前這句最多排兩行(英文長句常排到三行) → 逐步縮字；縮到最小還超出高度就藏下一句
const pipMQ = matchMedia('(max-height: 360px)');
let sdkPip = false;   // Discord SDK 回報的 PiP 模式(小視窗可能比 360px 高，光看尺寸不準)
function updatePip() {
  document.documentElement.classList.toggle('pip', sdkPip || pipMQ.matches);
  const i = activeIdx; activeIdx = -2; setActive(i);
}
function fitPip() {
  for (const l of lines) { l.el.style.fontSize = ''; l.el.style.display = ''; }
  const cur = lines[activeIdx];
  if (!document.documentElement.classList.contains('pip') || !cur || cur.gap) return;
  const wrap = $('lyrics-wrap');
  const el = cur.el;
  const base = parseFloat(getComputedStyle(el).fontSize);
  const rows = () => Math.round(el.offsetHeight / (parseFloat(getComputedStyle(el).fontSize) * PIP_LINE_HEIGHT));
  const fits = () => lyricsEl.scrollHeight <= wrap.clientHeight;
  // 每句即時判斷：超過兩行或超出高度就逐步縮字，最小 PIP_MIN_FONT
  for (let size = base; (rows() > 2 || !fits()) && size > PIP_MIN_FONT; ) {
    size = Math.max(PIP_MIN_FONT, size - 0.5);
    el.style.fontSize = `${size}px`;
  }
  // 縮到最小還放不下 → 藏下一句
  const next = el.nextElementSibling;
  if (!fits() && next) next.style.display = 'none';
}
pipMQ.addEventListener('change', updatePip);


function frame() {
  if (track) {
    const pos = clock.now();
    const dur = track.duration || 0;
    $('t-cur').textContent = fmt(pos);
    $('t-left').textContent = dur ? `-${fmt(dur - pos)}` : '';
    $('bar-fill').style.width = dur ? `${Math.min(100, (pos / dur) * 100)}%` : '0';
    if (lines.length) {
      const t = pos + BASE_LEAD + userOffset;
      let idx = -1;
      for (let i = 0; i < lines.length; i++) { if (lines[i].t <= t) idx = i; else break; }
      setActive(idx);
    }
  }
  updateMini();
  requestAnimationFrame(frame);
}

// 小歌詞條：遊戲模式 + 有在放歌才顯示(PiP 由 CSS 藏)
function updateMini() {
  const show = track && mode && mode !== 'lyrics';
  mini.hidden = !show;
  if (!show) return;
  const cur = lines[activeIdx];
  const text = cur && !cur.gap ? cur.text : (lines.length ? '♪' : (statusEl.textContent || ''));
  if ($('mini-line').textContent !== text) $('mini-line').textContent = text;
  mini.classList.toggle('paused', app.classList.contains('paused'));
}

window.addEventListener('resize', () => { const i = activeIdx; activeIdx = -2; setActive(i); });

// ── WebSocket ──
let lyricsWs = null;
let lyricsOn = false;
function connect(guildId) {
  if (!lyricsOn) return;
  const url = new URL(`ws?g=${encodeURIComponent(guildId)}`, location.href);
  url.protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const ws = new WebSocket(url);
  lyricsWs = ws;
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.type === 'idle') {
      track = null; lines = []; mini.hidden = true;
      app.classList.add('idle');
      setImage('');
      lyricsEl.innerHTML = '';
      showStatus('目前沒有在播放音樂\n用 /play 點一首歌吧');
    } else if (m.type === 'track') {
      onTrack(m);
    } else if (m.type === 'lyrics' && track && m.key === track.key) {
      renderLines(m);
    } else if (m.type === 'pos' && track && m.key === track.key) {
      clock.feed(m.position, m.paused);
      app.classList.toggle('paused', m.paused);
    }
  };
  ws.onclose = () => {
    if (lyricsWs !== ws || !lyricsOn) return;
    showStatus('連線中斷，重新連線中…');
    setTimeout(() => connect(guildId), 2000);
  };
}

// ── 模式切換：同一個 Activity 依頻道最近用的指令顯示歌詞 / LoL / Valorant(/act 則由機器人自動判斷) ──
// 歌詞的 WebSocket 一直連著：遊戲畫面時歌詞縮成頂列的小條(#mini)
let mode = null;
let rafStarted = false;
function startLyrics(guildId) {
  if (lyricsOn) return;
  if (!guildId) {
    showStatus('請在 Discord 伺服器的語音頻道中開啟');
    return;
  }
  lyricsOn = true;
  app.classList.add('idle');
  lyricsEl.innerHTML = '';
  showStatus('連線中…');
  connect(guildId);
  if (!rafStarted) { rafStarted = true; requestAnimationFrame(frame); }
}
function setMode(m, guildId) {
  startLyrics(guildId);
  if (m === mode) return;
  mode = m;
  // LoL 與 Valorant 共用 #lol 這塊畫面(樣式也共用，valo 只換配色)
  document.documentElement.classList.toggle('mode-lol', m === 'lol' || m === 'valo');
  if (m === 'lol') { stopValo(); startLol($('lol')); } else if (m === 'valo') { stopLol(); startValo($('lol')); } else { stopLol(); stopValo(); }
  updateMini();
  // 切回歌詞時重新定位目前那句(藏著的時候 offsetHeight 都是 0)
  if (m === 'lyrics') { const i = activeIdx; activeIdx = -2; setActive(i); }
}

// 部署新版後，開著的 Activity 會一直跑舊程式 → 每分鐘看一下 index.html 的版本號，變了就重新載入
const BUILD = new URL(import.meta.url).searchParams.get('v');
async function checkUpdate() {
  try {
    const html = await fetch('./', { cache: 'no-store' }).then((r) => r.text());
    const v = (html.match(/main\.js\?v=(\d+)/) || [])[1];
    if (BUILD && v && v !== BUILD) location.reload();
  } catch { /* 網路斷了就下次再看 */ }
}
setInterval(checkUpdate, 60000);

async function main() {
  const params = new URLSearchParams(location.search);
  let guildId = params.get('g') || params.get('guild_id');
  updatePip();
  if (params.has('frame_id')) {
    // 在 Discord 裡：完成 SDK 握手，guild 從 SDK 拿
    const cfg = await fetch('api/config').then((r) => r.json());
    const sdk = new DiscordSDK(cfg.client_id);
    await sdk.ready();
    guildId = sdk.guildId || guildId;
    try {
      await sdk.subscribe('ACTIVITY_LAYOUT_MODE_UPDATE', ({ layout_mode }) => {
        sdkPip = layout_mode === 1;   // 0 = 全畫面, 1 = PiP, 2 = Grid
        updatePip();
      });
    } catch (e) { console.warn('layout mode 訂閱失敗，改用尺寸判斷', e); }
    // 問機器人這個語音頻道現在是 /lol 還是 /lyrics，之後每 3 秒再問一次(可能中途換指令)
    let first = true;
    const ask = async () => {
      try {
        const q = `c=${encodeURIComponent(sdk.channelId || '')}${first ? '&first=1' : ''}`;
        first = false;
        const r = await fetch(`api/mode?${q}`);
        const j = await r.json();
        setMode(['lol', 'valo'].includes(j.mode) ? j.mode : 'lyrics', guildId);
      } catch (e) {
        if (!mode) setMode('lyrics', guildId);
      }
    };
    await ask();
    setInterval(ask, 3000);
    return;
  }
  // 瀏覽器測試：?mode=lol / ?mode=valo 強制模式
  setMode(['lol', 'valo'].includes(params.get('mode')) ? params.get('mode') : 'lyrics', guildId);
}

main().catch((e) => showStatus(`載入失敗：${e.message || e}`));
