// 活動面板：券、簽到、綁帳號、轉蛋、釣魚、小遊戲、樂透、大富翁、商店、個人卡片、排行 + 慶祝特效。
// 協定見 tools/FUN_PROTOCOL.md；傳輸走 live.js 的 sendFun。後端 cogs/fun_web.py。
// ?mock=1 時 sendFun 改走 fun_mock.js 的假後端，可在一般瀏覽器測 UI。

import { sendFun, getMe, isMock, onFun } from './live.js';
import { startMock } from './fun_mock.js';

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const num = (v) => (Number(v) || 0).toLocaleString('en-US');
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const SOUND_KEY = 'funSound';

const TABS = [['home', '🏠', '首頁'], ['gacha', '🎰', '轉蛋'], ['fish', '🎣', '釣魚'], ['games', '🃏', '小遊戲'], ['lottery', '🎫', '樂透'],
  ['mono', '🎲', '大富翁'], ['shop', '🛒', '商店'], ['profile', '🪪', '我的卡片'], ['top', '🏆', '排行']];
const RAR_NAME = { N: '普通', R: '稀有', SR: '超稀有', SSR: '傳說' };
const FISH_RAR = { trash: '垃圾', common: '普通', rare: '稀有', epic: '史詩', legend: '傳說' };
const GAME_NAME = { lol: 'LoL', valo: 'Valorant' };
const SLOT_SYMS = ['🍒', '🍋', '🔔', '⭐', '💎', '👑', '7️⃣'];
const DICE = ['⚀', '⚁', '⚂', '⚃', '⚄', '⚅'];
const KIND_TITLE = { max_ticket: '滿貫對局', ssr: '轉蛋傳說降臨', legend_fish: '傳說魚上鉤', fish_record: '刷新伺服器紀錄', jackpot: '拉霸大獎',
  mines_big: '踩地雷大豐收', lottery: '樂透頭獎', mono_champion: '大富翁賽季冠軍' };
const KIND_IC = { max_ticket: '🎟️', ssr: '🌟', legend_fish: '🐉', fish_record: '🏅', jackpot: '💎', mines_big: '💣', lottery: '🎫', mono_champion: '👑' };
const TILE_IC = { go: '🏁', jail: '⛓️', free: '🅿️', gotojail: '🚓', chance: '❓', tax: '💸', portal: '🌀' };

const S = {
  tab: 'home', wallet: null, busy: false, sound: true, hold: 0, held: [],
  home: { game: 'lol' },
  gacha: { last: null, dex: null },
  fish: { sub: 'bag', bag: null, dex: null, rec: null, last: null },
  games: { sub: 'bj', bj: null, bjPrev: null, mines: null, minesPrev: null, slots: null, flip: null, flipMsg: '' },
  lot: null, mono: null, monoEv: [], monoRoll: null, monoSel: null,
  shop: { items: null, kind: 'title' },
  profile: { uid: null, data: null },
  top: { kind: 'coins', rows: null },
};
try { S.sound = localStorage.getItem(SOUND_KEY) !== '0'; } catch { /* 無痕或被擋 */ }

const locked = () => !getMe();

// ═══════════ 音效(WebAudio 合成，音量小) ═══════════
let ac = null;
function audio() {
  if (!S.sound) return null;
  try {
    if (!ac) { const C = window.AudioContext || window.webkitAudioContext; if (!C) return null; ac = new C(); }
    if (ac.state === 'suspended') ac.resume();
    return ac;
  } catch { return null; }
}
function tone(freq, t0, dur, type = 'triangle', vol = 0.05) {
  const c = audio();
  if (!c) return;
  try {
    const o = c.createOscillator(), g = c.createGain(), t = c.currentTime + t0;
    o.type = type; o.frequency.value = freq;
    g.gain.setValueAtTime(0.0001, t);
    g.gain.exponentialRampToValueAtTime(vol, t + 0.02);
    g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
    o.connect(g); g.connect(c.destination);
    o.start(t); o.stop(t + dur + 0.05);
  } catch { /* 音效失敗不影響玩 */ }
}
function chime(level = 1) {          // 上行琶音，level 越高音越多
  const notes = [523.25, 659.25, 783.99, 1046.5, 1318.5].slice(0, level >= 3 ? 5 : level === 2 ? 4 : 3);
  notes.forEach((f, i) => { tone(f, i * 0.09, 0.5); tone(f * 2, i * 0.09, 0.25, 'sine', 0.02); });
}
const blip = () => tone(880, 0, 0.07, 'square', 0.018);
const thud = () => tone(196, 0, 0.22, 'sawtooth', 0.03);

// ═══════════ 慶祝特效：canvas 彩帶 / 金光 + 橫幅 ═══════════
let fxCv = null, fxCtx = null, parts = [], fxRaf = 0, fxLast = 0;
const COLORS = ['#ff6b9d', '#ffd24d', '#6be0ff', '#9d7bff', '#6bf59b', '#ff9a4d'];

function fxInit() {
  if (fxCv) return;
  fxCv = document.createElement('canvas');
  fxCv.id = 'fun-fx';
  document.body.appendChild(fxCv);
  fxCtx = fxCv.getContext('2d');
  const fit = () => { fxCv.width = window.innerWidth; fxCv.height = window.innerHeight; };
  fit();
  window.addEventListener('resize', fit);
}
function burst(x, y, { n = 60, gold = false, power = 1, up = true } = {}) {
  fxInit();
  for (let i = 0; i < n; i++) {
    const a = up ? -Math.PI / 2 + (Math.random() - 0.5) * Math.PI * 1.1 : Math.random() * Math.PI * 2;
    const sp = (180 + Math.random() * 420) * power;
    const spark = gold || Math.random() < 0.3;
    parts.push({ x, y, vx: Math.cos(a) * sp, vy: Math.sin(a) * sp, g: spark ? 260 : 620, life: 0, max: 1.2 + Math.random() * 1.2,
      size: spark ? 2 + Math.random() * 3 : 5 + Math.random() * 6, rot: Math.random() * 6, vr: (Math.random() - 0.5) * 12, spark,
      color: gold ? (Math.random() < 0.5 ? '#ffd24d' : '#fff3b0') : COLORS[(Math.random() * COLORS.length) | 0] });
  }
  if (parts.length > 700) parts.splice(0, parts.length - 700);
  if (!fxRaf) { fxLast = performance.now(); fxRaf = requestAnimationFrame(fxLoop); }
}
function rain(count = 14) {
  fxInit();
  for (let i = 0; i < count; i++) {
    parts.push({ x: Math.random() * fxCv.width, y: -12, vx: (Math.random() - 0.5) * 120, vy: 80 + Math.random() * 180, g: 160, life: 0, max: 3 + Math.random() * 2,
      size: 5 + Math.random() * 6, rot: Math.random() * 6, vr: (Math.random() - 0.5) * 10, spark: false, color: COLORS[(Math.random() * COLORS.length) | 0] });
  }
  if (!fxRaf) { fxLast = performance.now(); fxRaf = requestAnimationFrame(fxLoop); }
}
function fxLoop(t) {
  const dt = Math.min(0.05, (t - fxLast) / 1000);
  fxLast = t;
  const c = fxCtx;
  c.clearRect(0, 0, fxCv.width, fxCv.height);
  parts = parts.filter((p) => (p.life += dt) < p.max && p.y < fxCv.height + 30);
  for (const p of parts) {
    p.vy += p.g * dt; p.x += p.vx * dt; p.y += p.vy * dt; p.rot += p.vr * dt;
    c.globalAlpha = Math.max(0, Math.min(1, (p.max - p.life) * 1.5));
    c.fillStyle = p.color;
    if (p.spark) {
      c.globalCompositeOperation = 'lighter';
      c.beginPath(); c.arc(p.x, p.y, p.size, 0, 6.3); c.fill();
      c.globalCompositeOperation = 'source-over';
    } else {
      c.save(); c.translate(p.x, p.y); c.rotate(p.rot); c.scale(1, Math.abs(Math.cos(p.rot * 1.7)) * 0.8 + 0.2);
      c.fillRect(-p.size / 2, -p.size / 3, p.size, p.size * 0.66); c.restore();
    }
  }
  c.globalAlpha = 1;
  fxRaf = parts.length ? requestAnimationFrame(fxLoop) : 0;
  if (!parts.length) c.clearRect(0, 0, fxCv.width, fxCv.height);
}
const burstAt = (el, opt) => {
  if (!el) return;
  const r = el.getBoundingClientRect();
  burst(r.left + r.width / 2, r.top + r.height / 2, { up: false, power: 0.7, ...opt });
};

// 慶祝佇列：一次放一個；動畫進行中(轉蛋翻牌、拉霸…)先扣住，免得劇透
const celQ = [];
let celBusy = false;
function celebrate(m) {
  if (S.hold) { S.held.push(m); return; }
  if (celQ.length < 4) celQ.push(m);
  if (!celBusy) nextCel();
}
function holdCel(on) {
  S.hold = Math.max(0, S.hold + (on ? 1 : -1));
  if (!S.hold && S.held.length) { const h = S.held.splice(0); h.forEach(celebrate); }
}
async function nextCel() {
  const m = celQ.shift();
  if (!m) { celBusy = false; return; }
  celBusy = true;
  fxInit();
  let bn = $('fun-banner');
  if (!bn) { bn = document.createElement('div'); bn.id = 'fun-banner'; document.body.appendChild(bn); }
  const ic = m.icon ? `<span class="ph fb-img" data-fb="${esc(KIND_IC[m.kind] || '🎉')}"><img src="${esc(m.icon)}" alt=""></span>` : `<span class="fb-emoji">${KIND_IC[m.kind] || '🎉'}</span>`;
  const title = KIND_TITLE[m.kind] || '恭喜';
  bn.className = m.big ? 'big' : 'small';
  bn.innerHTML = `<div class="fb-in">${ic}<div class="fb-t"><div class="fb-title">${esc(title)}</div><div class="fb-who">${esc(m.user)}</div><div class="fb-text">${esc(m.text)}</div></div></div>`;
  void bn.offsetWidth;
  bn.classList.add('show');
  const ms = m.big ? 3800 : 3000;
  if (m.big) {
    chime(3);
    burst(0, window.innerHeight, { n: 90, power: 1.5 }); burst(window.innerWidth, window.innerHeight, { n: 90, power: 1.5 });
    burst(window.innerWidth / 2, window.innerHeight * 0.45, { n: 80, gold: true, up: false, power: 1.2 });
    const end = performance.now() + ms - 600;
    while (performance.now() < end) { rain(8); await sleep(260); }
    await sleep(Math.max(0, end + 600 - performance.now()));
  } else {
    chime(1);
    burst(window.innerWidth / 2, 70, { n: 40, up: false, power: 0.8 });
    await sleep(ms);
  }
  bn.classList.remove('show');
  await sleep(450);
  nextCel();
}

// ═══════════ 小工具 ═══════════
function ftoast(text, ok = true) {
  const t = $('fp-toast');
  if (!t) return;
  t.textContent = text;
  t.className = `show ${ok ? 'ok' : 'bad'}`;
  clearTimeout(ftoast.t);
  ftoast.t = setTimeout(() => { t.className = ''; }, 3200);
}

// 圖片：沒有圖(null)或載入失敗 → 顯示 emoji / 字母佔位
const img = (src, fb, cls = '') => `<span class="ph ${cls}" data-fb="${esc(fb)}">${src ? `<img src="${esc(src)}" alt="" loading="lazy">` : esc(fb)}</span>`;
const av = (name, src, frame, cls = '') => `<span class="av fr-${esc(frame || 'none')} ${cls}">${img(src, (String(name || '?').trim()[0] || '?').toUpperCase())}</span>`;

const chips = (items, cur, act) => `<div class="f-chips">${items.map(([v, l]) => `<button type="button" data-act="${act}" data-v="${v}" class="${v === cur ? 'on' : ''}">${l}</button>`).join('')}</div>`;
const loadingHtml = () => '<div class="f-empty">載入中…<br><button type="button" class="f-btn sm" data-act="reload">重新載入</button></div>';

async function call(op, args) {
  try {
    const d = await sendFun(op, args);
    if (d && d.wallet) setWallet(d.wallet);
    return d || {};
  } catch (e) {
    ftoast(e.message || '失敗了', false);
    return null;
  }
}
// 一次只跑一個動作，避免連點
async function run(fn) {
  if (S.busy) return;
  S.busy = true;
  $('fp-body').classList.add('busy');
  try { await fn(); } catch (e) { console.warn(e); ftoast('發生錯誤，請再試一次', false); } finally {
    S.busy = false;
    $('fp-body').classList.remove('busy');
  }
}

function setWallet(w) {
  S.wallet = w;
  $('fp-t').textContent = `🎟️ ${num(w.tickets)}`;
  $('fp-c').textContent = `🪙 ${num(w.coins)}`;
  updateFab();
  if (!$('funpanel').hidden && (S.tab === 'home' || S.tab === 'shop')) render();
}
function updateFab() {
  const w = S.wallet;
  $('fun-fab-n').textContent = w && !locked() ? ` ${w.tickets}` : '';
}

// ═══════════ 面板骨架 ═══════════
function buildDom() {
  const p = document.createElement('div');
  p.id = 'funpanel';
  p.hidden = true;
  p.innerHTML = `<div class="fp-shell">
    <header class="fp-head"><b>🎮 活動</b><span class="fp-chip" id="fp-t">🎟️ –</span><span class="fp-chip" id="fp-c">🪙 –</span><span class="fp-gap"></span>
      <button type="button" id="fp-snd" title="音效開關"></button><button type="button" id="fp-x" title="關閉">✕</button></header>
    <nav id="fp-nav">${TABS.map(([k, ic, l]) => `<button type="button" data-tab="${k}"><span>${ic}</span><small>${l}</small></button>`).join('')}</nav>
    <section id="fp-body"></section>
  </div><div id="fp-toast"></div>`;
  document.body.appendChild(p);
  const card = document.createElement('div');
  card.id = 'fun-card';
  document.body.appendChild(card);
  showSnd();
}
function showSnd() { const b = $('fp-snd'); b.textContent = S.sound ? '🔊' : '🔇'; b.title = S.sound ? '音效：開(點一下關閉)' : '音效：關(點一下開啟)'; }

function openPanel(tab) {
  $('funpanel').hidden = false;
  document.documentElement.classList.add('fun-open');
  go(tab || S.tab);
}
function closePanel() {
  $('funpanel').hidden = true;
  document.documentElement.classList.remove('fun-open');
}

// ═══════════ 分頁 ═══════════
let rendered = '';
function go(tab) {
  S.tab = tab;
  render();
  if (!locked()) (LOAD[tab] || (() => {}))();
}
function render() {
  const body = $('fp-body');
  if ($('funpanel').hidden) return;
  const keep = rendered === S.tab ? body.scrollTop : 0;
  const lock = locked();
  document.querySelectorAll('#fp-nav button').forEach((b) => b.classList.toggle('on', b.dataset.tab === S.tab));
  body.innerHTML = lock ? '<div class="f-lock">🔒 要授權登入才能玩<br><small>重開 Activity 時按「授權」，登入後就能用了</small></div>' : (VIEW[S.tab] || (() => ''))();
  body.scrollTop = keep;
  rendered = S.tab;
  if (!lock && POST[S.tab]) POST[S.tab]();
}
function rerenderIf(tab) { if (S.tab === tab && !$('funpanel').hidden) render(); }

// ───────── 首頁 ─────────
function vHome() {
  const w = S.wallet;
  if (!w) return loadingHtml();
  const grants = (w.grants || []).map((g) => `<li><span class="g-day">${esc(g.day)}</span><span class="g-who">${esc(g.game)}・${esc(g.who)} <small>${esc(g.kda)}</small></span>
      <span class="g-sc">表現 ${esc(g.score)}/8</span><b class="g-got">+${esc(g.got)} 張</b>${g.parts ? `<small class="g-parts">${g.parts.map((p) => `${esc(p[0])} +${esc(p[1])}`).join('・')}</small>` : ''}</li>`).join('');
  const accs = ['lol', 'valo'].map((g) => ((w.accounts || {})[g] || []).map((a) => `<span class="acc ${g}">${GAME_NAME[g]}・${esc(a.name)}<small>#${esc(a.tag)}</small>
      <button type="button" data-act="unlink" data-g="${g}" data-r="${esc(`${a.name}#${a.tag}`)}" title="移除這個帳號">✕</button></span>`).join('')).join('');
  return `<div class="f-stats">
      <div class="f-stat"><small>券</small><b>🎟️ ${num(w.tickets)}</b></div>
      <div class="f-stat"><small>代幣</small><b>🪙 ${num(w.coins)}</b></div>
      <div class="f-stat"><small>今天對局拿券</small><b>🎟️ ${num(w.earned)}</b></div>
    </div>
    <p class="f-hint">打得好拿比較多張券，每張券機率都一樣。打完 LoL / Valorant 會自動換券。</p>
    <div class="f-row"><button type="button" class="f-btn pri" data-act="daily"${w.daily ? ' disabled' : ''}>${w.daily ? '✅ 今天已簽到' : '📅 每日簽到'}</button>
      <button type="button" class="f-btn" data-act="tab" data-v="gacha">🎰 去轉蛋</button></div>
    <h3>最近換券</h3>
    ${grants ? `<ul class="f-list grants">${grants}</ul>` : '<div class="f-empty sm">還沒有紀錄，打一場對局試試</div>'}
    <h3>綁定帳號</h3>
    <div class="accs">${accs || '<span class="f-empty sm">還沒綁定</span>'}</div>
    <form class="f-form" data-form="link"><select id="fl-game"><option value="lol"${S.home.game === 'lol' ? ' selected' : ''}>LoL</option><option value="valo"${S.home.game === 'valo' ? ' selected' : ''}>Valorant</option></select>
      <input id="fl-rid" maxlength="40" placeholder="名字#tag" autocomplete="off"><button type="submit" class="f-btn pri">綁定</button></form>
    <small class="f-note">綁定時會向 Riot 查帳號，要等 1–3 秒。</small>`;
}

// ───────── 轉蛋 ─────────
function gcard(r, flipped) {
  return `<div class="gcard r-${esc(r.rarity)}${flipped ? ' flipped' : ''}"><div class="gc-in"><div class="gc-back">🎴</div>
    <div class="gc-front">${img(r.icon, r.rarity === 'SSR' ? '🌟' : '🎴', 'gc-img')}<b>${esc(r.name)}</b><small>${esc(r.rarity)}${r.new ? ' ・新' : ''}</small></div></div></div>`;
}
function vGacha() {
  const last = S.gacha.last;
  return `<div class="f-row"><button type="button" class="f-btn pri" data-act="pull" data-n="1">抽 1 次・1 張券</button><button type="button" class="f-btn pri" data-act="pull" data-n="10">抽 10 次・10 張券</button></div>
    <div id="gx-stage" class="gx-stage">${last ? last.map((r) => gcard(r, true)).join('') : '<div class="f-empty">抽一張試試手氣</div>'}</div>
    <div id="gx-sum" class="f-sum"></div>
    <h3>圖鑑</h3><div id="gx-dex">${S.gacha.dex ? '' : '<div class="f-empty sm">載入中…</div>'}</div>`;
}
function fillDex() {
  const el = $('gx-dex'), d = S.gacha.dex;
  if (!el || !d) return;
  const cards = (d.cards || []).filter((c) => c.count > 0);
  el.innerHTML = `<div class="f-sub">已收集 <b>${num(d.owned)} / ${num(d.total)}</b> ${['SSR', 'SR', 'R', 'N'].map((k) => `<span class="rt r-${k}">${k} ${(d.by_rarity || {})[k] || 0}</span>`).join('')}</div>
    <div class="dex-grid">${cards.map((c) => `<div class="dc r-${esc(c.rarity)}" title="${esc(c.name)}">${img(c.icon, '🎴', 'dc-img')}<b>${esc(c.name)}</b><small>${esc(c.rarity)}${c.count > 1 ? ` ×${c.count}` : ''}</small></div>`).join('') || '<div class="f-empty sm">還沒有卡片</div>'}</div>
    <div class="f-row"><button type="button" class="f-btn" data-act="sell"${d.dupes_value > 0 ? '' : ' disabled'}>♻️ 賣掉重複卡${d.dupes_value > 0 ? `(+${num(d.dupes_value)} 🪙)` : ''}</button></div>`;
}
async function loadDex() {
  const d = await call('gacha_dex');
  if (d) { S.gacha.dex = d; if (S.tab === 'gacha') fillDex(); }
}
async function doPull(n) {
  const d = await call('gacha_pull', { count: n });
  if (!d || !d.results) return;
  holdCel(true);
  try {
    S.gacha.last = d.results;
    const stage = $('gx-stage'), sum = $('gx-sum');
    if (!stage) return;
    sum.textContent = '';
    stage.innerHTML = d.results.map((r) => gcard(r, false)).join('');
    await sleep(400);
    const els = [...stage.children];
    for (let i = 0; i < els.length; i++) {
      els[i].classList.add('flipped');
      const r = d.results[i];
      if (r.rarity === 'SSR') { els[i].classList.add('glow'); burstAt(els[i], { n: 70, gold: true }); chime(2); await sleep(900); }
      else if (r.rarity === 'SR') { burstAt(els[i], { n: 16 }); blip(); await sleep(n === 1 ? 500 : 300); }
      else { blip(); await sleep(n === 1 ? 500 : 230); }
    }
    const nw = d.results.filter((r) => r.new).length, ssr = d.results.filter((r) => r.rarity === 'SSR').length;
    if ($('gx-sum')) $('gx-sum').innerHTML = `共 ${d.results.length} 張・新卡 <b>${nw}</b> 張${ssr ? `・<b class="gold">SSR ${ssr} 張</b>` : ''}`;
  } finally { holdCel(false); }
  loadDex();
}

// ───────── 釣魚 ─────────
function fishRow(c, i) {
  const rn = c.rarity_name || FISH_RAR[c.rarity] || c.rarity;
  return `<li class="fr fr-${esc(c.rarity)}" style="animation-delay:${i * 90}ms"><span class="fr-e">${esc(c.emoji)}</span><b>${esc(c.name)}</b><span class="fr-r">${esc(rn)}</span>
    <small>${esc(c.weight)} kg</small><span class="fr-v">🪙 ${num(c.value)}</span>${c.record ? '<i class="fr-rec">🏅 新紀錄</i>' : ''}</li>`;
}
function vFish() {
  const f = S.fish;
  return `<div class="f-row"><button type="button" class="f-btn pri" data-act="cast" data-n="1">🎣 甩 1 竿・1 張券</button><button type="button" class="f-btn pri" data-act="cast" data-n="10">🎣 甩 10 竿・10 張券</button></div>
    <div id="fx-stage" class="fish-stage">${f.last ? `<ul class="f-list fishes">${f.last.catches.map(fishRow).join('')}</ul><div class="f-sum">本次共 <b>🪙 ${num(f.last.total)}</b></div>`
      : '<div class="fish-scene idle"><div class="water"></div><span class="rod">🎣</span><span class="bobber">🔴</span></div>'}</div>
    ${chips([['bag', '🎒 背包'], ['dex', '📖 圖鑑'], ['rec', '🏅 伺服器紀錄']], f.sub, 'fishsub')}
    <div id="fs-body"></div>`;
}
function fillFish() {
  const el = $('fs-body');
  if (!el) return;
  const f = S.fish, d = f[f.sub];
  if (!d) { el.innerHTML = '<div class="f-empty sm">載入中…</div>'; return; }
  if (f.sub === 'bag') {
    el.innerHTML = d.items.length ? `<ul class="f-list bag">${d.items.map((it) => `<li class="fr fr-${esc(it.rarity)}"><span class="fr-e">${esc(it.emoji)}</span><b>${esc(it.name)}</b><span class="fr-r">×${it.count}</span>
        <small>${esc(it.weight)} kg</small><span class="fr-v">🪙 ${num(it.value)}</span></li>`).join('')}</ul>
      <div class="f-row"><button type="button" class="f-btn pri" data-act="fsell">💰 全部賣出 (+${num(d.total)} 🪙)</button></div>` : '<div class="f-empty sm">背包是空的，甩竿試試</div>';
  } else if (f.sub === 'dex') {
    el.innerHTML = `<div class="f-sub">已釣到 <b>${d.caught} / ${d.total}</b> 種</div><div class="dex-grid fish">${d.species.map((s) => (s.caught
      ? `<div class="dc fr-${esc(s.rarity)}"><span class="dc-e">${esc(s.emoji)}</span><b>${esc(s.name)}</b><small>${esc(s.rarity_name || FISH_RAR[s.rarity] || '')}・最大 ${esc(s.best)}kg</small></div>`
      : '<div class="dc unk"><span class="dc-e">❔</span><b>???</b><small>還沒釣到</small></div>')).join('')}</div>`;
  } else {
    el.innerHTML = d.rows.length ? `<ul class="f-list recs">${d.rows.map((r) => `<li class="fr fr-${esc(r.rarity)}"><span class="fr-e">${esc(r.emoji)}</span><b>${esc(r.name)}</b><small>${esc(r.weight)} kg</small><span class="fr-r">${esc(r.user)}</span></li>`).join('')}</ul>`
      : '<div class="f-empty sm">還沒有人創下紀錄</div>';
  }
}
async function loadFish() {
  const sub = S.fish.sub;
  const d = await call({ bag: 'fish_bag', dex: 'fish_dex', rec: 'fish_records' }[sub]);
  if (d) { S.fish[sub] = d; if (S.tab === 'fish' && S.fish.sub === sub) fillFish(); }
}
async function doCast(n) {
  const stage = $('fx-stage');
  if (!stage) return;
  holdCel(true);
  try {
    stage.innerHTML = '<div class="fish-scene"><div class="water"></div><span class="rod">🎣</span><span class="bobber">🔴</span><div class="fish-say">等魚上鉤…</div></div>';
    const scene = stage.firstElementChild;
    scene.classList.add('cast');
    const [d] = await Promise.all([call('fish_cast', { count: n }), sleep(1500)]);
    if (!d || !d.catches) { stage.innerHTML = '<div class="fish-scene idle"><div class="water"></div><span class="rod">🎣</span><span class="bobber">🔴</span></div>'; return; }
    scene.classList.add('bite');
    scene.querySelector('.fish-say').textContent = '❗ 上鉤了！';
    tone(660, 0, 0.12, 'square', 0.02); tone(990, 0.1, 0.16, 'square', 0.02);
    await sleep(750);
    S.fish.last = d;
    stage.innerHTML = `<ul class="f-list fishes">${d.catches.map(fishRow).join('')}</ul><div class="f-sum">本次共 <b>🪙 ${num(d.total)}</b></div>`;
    await sleep(Math.min(d.catches.length * 90 + 300, 1200));
    const top = d.catches.find((c) => c.rarity === 'legend') || d.catches.find((c) => c.record);
    if (top) { burstAt(stage, { n: 80, gold: true }); chime(2); }
  } finally { holdCel(false); }
  S.fish.bag = null;
  if (S.fish.sub === 'bag') loadFish();
}

// ───────── 小遊戲 ─────────
const pcard = (c, isNew) => (c ? `<div class="pc${c.suit === '♥' || c.suit === '♦' ? ' red' : ''}${isNew ? ' deal' : ''}"><b>${esc(c.rank)}</b><i>${esc(c.suit)}</i></div>` : `<div class="pc back${isNew ? ' deal' : ''}"></div>`);
const RESUME = '<small class="f-note">有沒打完的局會直接接著打</small>';
const BJ_RES = { blackjack: ['🃏 Blackjack！', 'win'], win: ['你贏了', 'win'], dealer_bust: ['莊家爆牌，你贏了', 'win'], push: ['平手，券退你', 'push'], lose: ['你輸了', 'lose'], bust: ['爆牌了', 'lose'] };
function vBj() {
  const g = S.games.bj, prev = S.games.bjPrev;
  if (!g) return '<div class="f-empty sm">莊家與你各發兩張，點數最接近 21 的贏。</div><div class="f-row"><button type="button" class="f-btn pri" data-act="bj_start">🃏 開一局(花 1 張券)</button></div>' + RESUME;
  const res = g.over && BJ_RES[g.result] ? BJ_RES[g.result] : null;
  return `<div class="bj-side"><small>莊家${g.dv != null ? ` ・ ${g.dv} 點` : ''}</small><div class="hand">${g.dealer.map((c, i) => pcard(c, !prev || !prev.dealer[i])).join('')}</div></div>
    <div class="bj-side"><small>你 ・ ${g.pv} 點</small><div class="hand">${g.player.map((c, i) => pcard(c, !prev || !prev.player[i])).join('')}</div></div>
    ${res ? `<div class="g-res ${res[1]}">${res[0]}${g.payout ? ` <b>+${num(g.payout)} 🪙</b>` : ''}</div>` : ''}
    <div class="f-row">${g.over ? '<button type="button" class="f-btn pri" data-act="bj_start">再來一局(花 1 張券)</button>' + RESUME
      : '<button type="button" class="f-btn pri" data-act="bj_hit">要牌</button><button type="button" class="f-btn" data-act="bj_stand">停牌</button>'}</div>`;
}

function vMines() {
  const g = S.games.mines, prev = S.games.minesPrev;
  if (!g) return '<div class="f-empty sm">20 格裡藏了地雷，每翻一格安全格倍率就上升，隨時可以收手。</div><div class="f-row"><button type="button" class="f-btn pri" data-act="mines_start">💣 開始(花 1 張券)</button></div>' + RESUME;
  const op = new Set(g.opened || []), mc = new Set(g.mine_cells || []), po = new Set((prev && prev.opened) || []);
  const cells = Array.from({ length: g.cells }, (_, i) => {
    const o = op.has(i), m = mc.has(i);
    const cls = o ? `open${po.has(i) ? '' : ' pop'}` : m ? `mine${g.exploded === i ? ' boom' : ''}` : '';
    return `<button type="button" class="mc ${cls}" data-act="reveal" data-i="${i}"${g.over || o ? ' disabled' : ''}>${o ? '💎' : m ? (g.exploded === i ? '💥' : '💣') : ''}</button>`;
  }).join('');
  return `<div class="mines-info"><span>💣 ${g.mines} 顆地雷</span><span>已翻 <b>${g.k}</b> 格</span><span>倍率 <b class="gold">×${g.mult}</b></span><span>收手可拿 <b>🪙 ${num(g.cash)}</b></span></div>
    <div class="mines-grid" style="grid-template-columns:repeat(${g.cols},1fr)">${cells}</div>
    ${g.over ? `<div class="g-res ${g.payout > 0 ? 'win' : 'lose'}">${g.payout > 0 ? `收手成功 <b>+${num(g.payout)} 🪙</b>` : '踩到地雷了…'}</div>
      <div class="f-row"><button type="button" class="f-btn pri" data-act="mines_start">再來一局(花 1 張券)</button></div>${RESUME}`
    : `<div class="mines-next">下一格若安全：×${g.next_mult}(收手 🪙 ${num(g.next_cash)})</div>
      <div class="f-row"><button type="button" class="f-btn pri" data-act="mines_cash"${g.k ? '' : ' disabled'}>💰 收手 +${num(g.cash)}</button></div>`}`;
}

function vSlots() {
  const s = S.games.slots;
  return `<div class="slot-box"><div class="reels"><div class="reel"><div class="strip"></div></div><div class="reel"><div class="strip"></div></div><div class="reel"><div class="strip"></div></div></div></div>
    <div id="sl-msg" class="g-res">${s ? (s.payout > 0 ? `中獎 <b>+${num(s.payout)} 🪙</b>` : '沒中，再接再厲') : ''}</div>
    <div class="f-row"><button type="button" class="f-btn pri" data-act="spin">🎰 轉一次(花 1 張券)</button></div>
    <div class="paytable">${s ? `${s.table.map((t) => `<span>${esc(t.sym)}${esc(t.sym)}${esc(t.sym)} <b>${esc(t.pay)}</b></span>`).join('')}<span>${esc(s.pair.sym)}${esc(s.pair.sym)} <b>${esc(s.pair.pay)}</b></span>` : '<small>轉一次就會顯示賠率</small>'}</div>`;
}

function vFlip() {
  const f = S.games.flip;
  return `<div class="coin-wrap"><div class="coin" id="coin" style="transform:rotateY(${f && f.flip === 'tails' ? 180 : 0}deg)"><div class="cf head">🪙<small>正面</small></div><div class="cf tail">🌑<small>反面</small></div></div></div>
    <div id="fl-msg" class="g-res ${f ? (f.win ? 'win' : 'lose') : ''}">${f ? (f.win ? `猜中了 <b>+${num(f.payout)} 🪙</b>` : '猜錯了') : '猜猜看是正面還是反面'}</div>
    <div class="f-row"><button type="button" class="f-btn pri" data-act="flip" data-v="heads">猜正面(花 1 張券)</button><button type="button" class="f-btn pri" data-act="flip" data-v="tails">猜反面(花 1 張券)</button></div>`;
}
function vGames() {
  const sub = S.games.sub;
  return `${chips([['bj', '🃏 21 點'], ['slots', '🎰 拉霸'], ['mines', '💣 踩地雷'], ['flip', '🪙 猜正反']], sub, 'gamesub')}<div class="game-box" id="gm-body">${{ bj: vBj, slots: vSlots, mines: vMines, flip: vFlip }[sub]()}</div>`;
}

// 拉霸：每個轉輪是一條直向的圖案帶，用 rAF 捲動；停輪時減速到目標圖案
const REEL_H = 78;
let reels = [], reelRaf = 0, reelLast = 0;
function reelFill(r) { r.strip.innerHTML = [...r.pool, r.pool[0]].map((s) => `<div>${esc(s)}</div>`).join(''); }
function setupReels() {
  const pool = [...SLOT_SYMS];
  const s = S.games.slots;
  if (s) { s.table.forEach((t) => !pool.includes(t.sym) && pool.push(t.sym)); s.reels.forEach((x) => !pool.includes(x) && pool.push(x)); }
  reels = [...document.querySelectorAll('.reel')].map((el, i) => {
    const r = { el, strip: el.firstElementChild, pool: [...pool], y: 0, mode: 'idle' };
    reelFill(r);
    r.y = (s ? Math.max(0, r.pool.indexOf(s.reels[i])) : i * 2) * REEL_H;
    r.strip.style.transform = `translateY(${-r.y}px)`;
    return r;
  });
}
function reelLoop(t) {
  const dt = t - reelLast;
  reelLast = t;
  let live = false;
  for (const r of reels) {
    if (r.mode === 'idle') continue;
    live = true;
    const L = r.pool.length * REEL_H;
    if (r.mode === 'spin') r.y = (r.y + dt * 1.1) % L;
    else {
      const p = Math.min(1, (t - r.t0) / r.dur);
      r.y = r.from + (r.to - r.from) * (1 - (1 - p) ** 3);
      if (p >= 1) { r.y %= L; r.mode = 'idle'; r.el.classList.add('landed'); blip(); if (r.done) r.done(); }
    }
    r.strip.style.transform = `translateY(${-r.y}px)`;
  }
  reelRaf = live ? requestAnimationFrame(reelLoop) : 0;
}
function reelStart() {
  reels.forEach((r) => { r.mode = 'spin'; r.el.classList.remove('landed'); r.el.classList.add('spinning'); });
  if (!reelRaf) { reelLast = performance.now(); reelRaf = requestAnimationFrame(reelLoop); }
}
function reelStop(i, sym) {
  const r = reels[i];
  return new Promise((res) => {
    if (!r.pool.includes(sym)) { r.pool.push(sym); reelFill(r); }
    const L = r.pool.length * REEL_H, k = r.pool.indexOf(sym) * REEL_H;
    r.from = r.y;
    r.to = r.y + ((k - r.y) % L + L) % L + L;
    r.t0 = performance.now(); r.dur = 800;
    r.mode = 'tween';
    r.done = () => { r.el.classList.remove('spinning'); res(); };
  });
}
async function doSpin() {
  if (!reels.length) return;
  holdCel(true);
  try {
    reelStart();
    const msg = $('sl-msg');
    msg.className = 'g-res'; msg.textContent = '轉轉轉…';
    const [d] = await Promise.all([call('slots_spin'), sleep(900)]);
    if (!d || !d.reels) { reels.forEach((r) => { r.mode = 'idle'; r.el.classList.remove('spinning'); }); msg.textContent = ''; return; }
    S.games.slots = d;
    const stops = d.reels.map(async (sym, i) => { await sleep(i * 600); await reelStop(i, sym); });
    await Promise.all(stops);
    msg.className = `g-res ${d.payout > 0 ? 'win' : 'lose'}`;
    msg.innerHTML = d.payout > 0 ? `中獎 <b>+${num(d.payout)} 🪙</b>` : '沒中，再接再厲';
    if (d.payout > 0) { chime(d.payout >= 500 ? 2 : 1); burstAt(msg, { n: 30 }); }
    const pt = document.querySelector('.paytable');
    if (pt) pt.innerHTML = `${d.table.map((t) => `<span>${esc(t.sym)}${esc(t.sym)}${esc(t.sym)} <b>${esc(t.pay)}</b></span>`).join('')}<span>${esc(d.pair.sym)}${esc(d.pair.sym)} <b>${esc(d.pair.pay)}</b></span>`;
  } finally { holdCel(false); }
}

async function doFlip(guess) {
  const coin = $('coin'), msg = $('fl-msg');
  if (!coin) return;
  holdCel(true);
  try {
    msg.className = 'g-res'; msg.textContent = '拋出去了…';
    coin.getAnimations().forEach((a) => a.cancel());
    coin.style.transform = '';
    const spin = coin.animate([{ transform: 'rotateY(0)' }, { transform: 'rotateY(360deg)' }], { duration: 450, iterations: Infinity });
    const [d] = await Promise.all([call('flip', { guess }), sleep(800)]);
    spin.cancel();
    if (!d || !d.flip) { return; }
    const end = (d.flip === 'tails' ? 180 : 0) + 360 * 5;
    const a = coin.animate([{ transform: 'rotateY(0) translateY(0)' }, { transform: `rotateY(${end / 2}deg) translateY(-50px)`, offset: 0.5 }, { transform: `rotateY(${end}deg) translateY(0)` }],
      { duration: 1400, easing: 'cubic-bezier(.2,.7,.3,1)', fill: 'forwards' });
    await a.finished.catch(() => {});
    coin.style.transform = `rotateY(${d.flip === 'tails' ? 180 : 0}deg)`;
    a.cancel();
    S.games.flip = d;
    msg.className = `g-res ${d.win ? 'win' : 'lose'}`;
    msg.innerHTML = d.win ? `猜中了 <b>+${num(d.payout)} 🪙</b>` : '猜錯了';
    if (d.win) { chime(1); burstAt(coin, { n: 30 }); } else thud();
  } finally { holdCel(false); }
}

// ───────── 樂透 ─────────
function vLottery() {
  const l = S.lot;
  if (!l) return loadingHtml();
  const last = l.last;
  return `<div class="lot-pot"><small>第 ${esc(l.period)} 期彩金</small><b>🪙 ${num(l.pot)}</b>${l.prize != null ? `<small>頭獎 ${esc(l.prize)}</small>` : ''}</div>
    <div class="f-stats"><div class="f-stat"><small>總注數</small><b>${num(l.bets)}</b></div><div class="f-stat"><small>參加人數</small><b>${num(l.players)}</b></div>
      <div class="f-stat"><small>我的注數</small><b>${num(l.mine)}</b><small>中獎機率 ${(l.chance * 100).toFixed(1)}%</small></div></div>
    <div class="lot-cd">距離開獎 <b data-cd="${l.next_draw}">--</b></div>
    <div class="f-row"><button type="button" class="f-btn pri" data-act="lbuy" data-n="1">買 1 注・1 張券</button><button type="button" class="f-btn pri" data-act="lbuy" data-n="5">買 5 注・5 張券</button><button type="button" class="f-btn pri" data-act="lbuy" data-n="20">買 20 注・20 張券</button></div>
    ${l.rules ? `<small class="f-note">${esc(l.rules)}</small>` : ''}
    <h3>上期結果</h3>${last ? `<div class="lot-last"><small>第 ${esc(last.period)} 期・彩金 ${num(last.pot)}・${num(last.bets)} 注・${num(last.players)} 人${last.carry ? `・累積 ${num(last.carry)} 到下期` : ''}</small>
      <ul class="f-list">${(last.winners || []).map((w) => `<li><span class="g-day">第 ${esc(w.rank)} 名</span><span class="g-who">${esc(w.name)}</span><b class="g-got">🪙 ${num(w.prize)}</b></li>`).join('') || '<li class="dim">沒有人中獎</li>'}</ul></div>` : '<div class="f-empty sm">還沒有開過獎</div>'}`;
}
const fmtCd = (s) => {
  s = Math.max(0, Math.floor(s));
  const d = Math.floor(s / 86400), h = Math.floor(s % 86400 / 3600), m = Math.floor(s % 3600 / 60);
  return `${d ? `${d} 天 ` : ''}${h || d ? `${h} 時 ` : ''}${m} 分 ${s % 60} 秒`;
};
let cdReload = 0;
setInterval(() => {
  document.querySelectorAll('[data-cd]').forEach((el) => {
    const left = +el.dataset.cd - Date.now() / 1000;
    el.textContent = left > 0 ? fmtCd(left) : '開獎中…';
    if (left <= 0 && cdReload !== +el.dataset.cd && S.tab === 'lottery') { cdReload = +el.dataset.cd; setTimeout(LOAD.lottery, 3000); }
  });
}, 1000);

// ───────── 大富翁 ─────────
function boardHtml(m) {
  const N = m.board.length;
  if (!N) return m.board_png ? `<img class="mono-png" src="${esc(m.board_png)}" alt="棋盤">` : '';
  const s = Math.ceil(N / 4), size = s + 1;
  const posOf = (i) => (i < s ? [0, i] : i < 2 * s ? [i - s, s] : i < 3 * s ? [s, s - (i - 2 * s)] : [s - (i - 3 * s), 0]);
  const at = {};
  (m.players || []).forEach((p) => { (at[p.pos] = at[p.pos] || []).push(p); });
  const tiles = m.board.map((t, i) => {
    const [r, c] = posOf(i);
    const ic = t.kind === 'prop' ? '' : TILE_IC[t.kind] || '';
    const lv = t.level > 0 ? `<span class="mt-lv">${'★'.repeat(t.level)}</span>` : '';
    const who = (at[i] || []).map((p) => `<u style="background:${esc(p.color)}" title="${esc(p.name)}">${esc(p.initial || p.name[0] || '?')}</u>`).join('');
    return `<button type="button" class="mt k-${esc(t.kind)}${S.monoSel === i ? ' sel' : ''}" data-act="tile" data-i="${i}" style="grid-row:${r + 1};grid-column:${c + 1}">
      ${t.kind === 'prop' ? `<i class="mt-bar" style="background:${esc(t.color || '#888')}"></i>` : ''}<span class="mt-n">${ic || esc(t.name)}</span>${lv}
      ${t.owner ? `<em class="mt-own" style="background:${esc(t.owner_color || '#fff')}"></em>` : ''}<span class="mt-who">${who}</span></button>`;
  }).join('');
  const r = S.monoRoll;
  const center = `<div class="mono-center" style="grid-area:2/2/${size}/${size}"><div class="mono-dice" id="mono-dice">${r ? `${DICE[r.dice[0] - 1]} ${DICE[r.dice[1] - 1]}` : '🎲'}</div>
    <small>${esc(m.season.name)}・剩 ${esc(m.season.days_left)} 天</small></div>`;
  return `<div class="mono-board" style="grid-template-columns:repeat(${size},1fr);grid-template-rows:repeat(${size},1fr)">${tiles}${center}</div>`;
}
function vMono() {
  const m = S.mono;
  if (!m) return loadingHtml();
  const st = `<h3>賽季排行</h3><ul class="f-list">${(m.standings || []).map((p, i) => `<li><span class="g-day">#${i + 1}</span><span class="g-who">${esc(p.name)}</span><b class="g-got">$${num(p.nw)}</b></li>`).join('') || '<li class="dim">還沒有人加入</li>'}</ul>
    ${(m.champions || []).length ? `<h3>歷屆冠軍</h3><ul class="f-list">${m.champions.map((c) => `<li><span class="g-day">${esc(c.season)}</span><span class="g-who">👑 ${esc(c.name)}</span><b class="g-got">$${num(c.nw)}</b></li>`).join('')}</ul>` : ''}`;
  if (!m.joined) {
    return `<div class="mono-join"><div class="mono-dice">🎲</div><p>${esc(m.season.name)}・剩 ${esc(m.season.days_left)} 天</p><p class="f-hint">加入後每天擲骰、買地、收租，賽季結束時總資產最高的人拿冠軍。</p>
      <button type="button" class="f-btn pri" data-act="mjoin">加入大富翁</button></div>${boardHtml(m)}${st}`;
  }
  const me = m.me || {}, pd = m.pending;
  const tile = m.board[me.pos];
  const sel = S.monoSel != null ? m.board[S.monoSel] : null;
  let actions;
  if (pd) {
    actions = `<div class="mono-pend">在「${esc(pd.name)}」</div><div class="f-row"><button type="button" class="f-btn pri" data-act="mdec" data-v="${pd.type === 'up' ? 'up' : 'buy'}">${pd.type === 'up' ? '升級' : '買下'} $${num(pd.cost)}</button>
      <button type="button" class="f-btn" data-act="mdec" data-v="skip">不要</button></div>`;
  } else {
    actions = `<div class="f-row"><button type="button" class="f-btn pri" data-act="mroll">🎲 擲骰子(花 1 張券)</button>
      ${me.jail ? '<button type="button" class="f-btn" data-act="mbail">付 50 出獄</button>' : ''}</div>`;
  }
  return `${boardHtml(m)}
    ${sel ? `<div class="tile-info"><b>${esc(sel.name)}</b>${sel.kind === 'prop' ? `・地價 $${num(sel.price)}・租金 $${num(sel.rent)}・${sel.owner ? `地主 ${esc(sel.owner)}・Lv${sel.level}` : '無主'}` : ''}</div>` : ''}
    <div class="f-stats"><div class="f-stat"><small>現金</small><b>$${num(me.cash)}</b></div><div class="f-stat"><small>總資產</small><b>$${num(me.nw)}</b></div>
      <div class="f-stat"><small>排名</small><b>#${esc(me.rank)}</b><small>${tile ? esc(tile.name) : ''}</small></div></div>
    ${me.jail ? '<div class="g-res lose">⛓️ 停權中(擲出對子就能出獄)</div>' : ''}
    ${actions}
    ${S.monoEv.length ? `<ul class="mono-ev">${S.monoEv.map((e) => `<li>${esc(e)}</li>`).join('')}</ul>` : ''}${st}`;
}
async function monoRollAnim() {
  const dice = $('mono-dice');
  let iv = 0;
  if (dice) { dice.classList.add('rolling'); iv = setInterval(() => { dice.textContent = `${DICE[Math.random() * 6 | 0]} ${DICE[Math.random() * 6 | 0]}`; blip(); }, 90); }
  const [d] = await Promise.all([call('mono_roll'), sleep(900)]);
  clearInterval(iv);
  if (dice) dice.classList.remove('rolling');
  if (!d || !d.roll) { render(); return; }
  S.monoRoll = d.roll;
  if (dice) dice.textContent = `${DICE[d.roll.dice[0] - 1]} ${DICE[d.roll.dice[1] - 1]}`;
  S.monoEv = d.roll.events || [];
  // 棋子一格一格走
  const N = (S.mono && S.mono.board.length) || 0;
  if (N) {
    const steps = ((d.roll.to - d.roll.from) % N + N) % N;
    for (let k = 1; k <= steps && k <= 14; k++) {
      const i = (d.roll.from + (steps > 14 ? steps - 14 + k : k)) % N;
      document.querySelectorAll('.mt.hop').forEach((e) => e.classList.remove('hop'));
      const t = document.querySelector(`.mt[data-i="${i}"]`);
      if (t) t.classList.add('hop');
      blip();
      await sleep(130);
    }
  }
  S.mono = d;
  render();
}

// ───────── 商店 ─────────
const KIND_NAME = { title: '稱號', frame: '外框', bg: '背景' };
function itemPreview(it) {
  if (it.kind === 'title') return `<span class="title-chip">${esc(it.name)}</span>`;
  if (it.kind === 'frame') return av('預覽', null, it.id, 'lg');
  return `<span class="bg-sw bgc-${esc(it.id)}"></span>`;
}
function vShop() {
  const sh = S.shop;
  if (!sh.items) return loadingHtml();
  const coins = S.wallet ? S.wallet.coins : 0;
  const list = sh.items.filter((i) => i.kind === sh.kind).map((it) => {
    let btn;
    if (it.equipped) btn = `<button type="button" class="f-btn sm" data-act="unequip" data-k="${esc(it.kind)}">卸下</button>`;
    else if (it.owned) btn = `<button type="button" class="f-btn pri sm" data-act="equip" data-id="${esc(it.id)}">裝備</button>`;
    else btn = `<button type="button" class="f-btn pri sm" data-act="buy" data-id="${esc(it.id)}"${coins < it.price ? ' disabled' : ''}>🪙 ${num(it.price)}</button>`;
    return `<div class="shop-item${it.equipped ? ' eq' : ''}"><div class="si-pv">${itemPreview(it)}</div><div class="si-t"><b>${esc(it.name)}</b>${it.equipped ? '<i>使用中</i>' : it.owned ? '<i>已擁有</i>' : ''}<small>${esc(it.desc)}</small></div>${btn}</div>`;
  }).join('');
  return `${chips(Object.entries(KIND_NAME), sh.kind, 'shopkind')}<div class="shop-list">${list || '<div class="f-empty sm">沒有商品</div>'}</div>`;
}

// ───────── 個人卡片 ─────────
function statLine(label, v) { return `<div class="f-stat"><small>${label}</small><b>${v}</b></div>`; }
function vProfile() {
  const p = S.profile.data;
  if (!p) return loadingHtml();
  const st = p.stats || {};
  const g = st.gacha || {}, f = st.fish || {};
  let mini = st.mini_best;
  if (mini && typeof mini === 'object') mini = Object.entries(mini).map(([k, v]) => `${k} ${v}`).join('・');
  const accs = ['lol', 'valo'].map((k) => ((p.accounts || {})[k] || []).map((a) => `<span class="acc ${k}">${GAME_NAME[k]}・${esc(a.name)}<small>#${esc(a.tag)}</small></span>`).join('')).join('');
  return `${S.profile.uid ? '<button type="button" class="f-btn sm" data-act="myprofile">← 回到我的卡片</button>' : ''}
    <div class="pcard bgc-${esc(p.bg || 'none')}"><div class="pc-top">${av(p.name, p.avatar, p.frame, 'xl')}<div><div class="pc-name">${esc(p.name)}</div>${p.title ? `<span class="title-chip">${esc(p.title)}</span>` : '<small class="dim">還沒有稱號</small>'}</div></div>
      <div class="accs">${accs || '<small class="dim">沒有綁定帳號</small>'}</div></div>
    <div class="f-stats wrap">${statLine('累計換券', `🎟️ ${num(st.tickets_total)}`)}${statLine('代幣', `🪙 ${num(st.coins)}`)}
      ${statLine('轉蛋收集', `${num(g.owned)} / ${num(g.total)}<small>SSR ${num(g.ssr)}</small>`)}
      ${statLine('釣魚圖鑑', `${num(f.caught)} / ${num(f.total)}<small>${f.best ? `${esc(f.best.emoji)} ${esc(f.best.name)} ${esc(f.best.weight)}kg` : '還沒有紀錄'}</small>`)}
      ${statLine('樂透中獎', `${num(st.lottery_wins)} 次`)}${statLine('大富翁冠軍', `${num(st.mono_titles)} 次`)}${mini != null ? statLine('小遊戲最佳', esc(mini)) : ''}</div>`;
}

// ───────── 排行 ─────────
function vTop() {
  const t = S.top;
  return `${chips([['coins', '🪙 代幣'], ['gacha', '🎰 收集'], ['fish', '🎣 釣魚'], ['mono', '🎲 大富翁']], t.kind, 'topkind')}
    ${t.rows ? `<ul class="f-list top">${t.rows.map((r) => `<li class="clk r${r.rank}" data-act="viewp" data-u="${esc(r.uid)}"><span class="rank">${r.rank <= 3 ? ['🥇', '🥈', '🥉'][r.rank - 1] : `#${esc(r.rank)}`}</span>
      ${av(r.name, r.avatar, null, 'sm')}<span class="g-who">${esc(r.name)}${r.sub ? `<small>${esc(r.sub)}</small>` : ''}</span><b class="g-got">${esc(r.value)}</b></li>`).join('') || '<li class="dim">還沒有資料</li>'}</ul>` : '<div class="f-empty">載入中…</div>'}`;
}

const VIEW = { home: vHome, gacha: vGacha, fish: vFish, games: vGames, lottery: vLottery, mono: vMono, shop: vShop, profile: vProfile, top: vTop };
const POST = {
  gacha: () => fillDex(),
  fish: () => fillFish(),
  games: () => { if (S.games.sub === 'slots') setupReels(); },
};
const LOAD = {
  home: () => call('home'),
  gacha: loadDex,
  fish: loadFish,
  games: () => {},
  lottery: async () => { const d = await call('lottery'); if (d) { S.lot = d; rerenderIf('lottery'); } },
  mono: async () => { const d = await call('mono'); if (d) { S.mono = d; if (!S.busy) rerenderIf('mono'); } },
  shop: async () => { const d = await call('shop'); if (d) { S.shop.items = d.items; rerenderIf('shop'); } },
  profile: async () => {
    const uid = S.profile.uid, d = await call('profile', uid ? { uid } : {});
    if (d) { S.profile.data = d; rerenderIf('profile'); }
  },
  top: async () => {
    const kind = S.top.kind, d = await call('top', { kind });
    if (d && S.top.kind === kind) { S.top.rows = d.rows; rerenderIf('top'); }
  },
};

// ═══════════ 點擊動作 ═══════════
const ACT = {
  tab: (d) => go(d.v),
  reload: () => LOAD[S.tab](),
  async daily() {
    const d = await call('daily');
    if (d) { ftoast(`簽到成功！${d.got != null ? `+${d.got}` : ''}`); chime(1); }
  },
  async link() {
    const game = $('fl-game').value, rid = $('fl-rid').value.trim();
    S.home.game = game;
    if (!/^.+#.+$/.test(rid)) { ftoast('請輸入「名字#tag」', false); return; }
    ftoast('向 Riot 查帳號中…');
    const d = await call('link_add', { game, rid });
    if (d) { ftoast('綁定成功'); render(); }
  },
  async unlink(d) { if (await call('link_remove', { game: d.g, rid: d.r })) ftoast('已移除'); },
  pull: (d) => doPull(+d.n),
  async sell() {
    const d = await call('gacha_sell');
    if (d) { ftoast(`賣掉 ${(d.sold || []).reduce((a, x) => a + x.count, 0)} 張重複卡，+${num(d.total)} 🪙`); loadDex(); }
  },
  cast: (d) => doCast(+d.n),
  fishsub(d) { S.fish.sub = d.v; S.fish[d.v] = null; render(); },
  async fsell() {
    const d = await call('fish_sell');
    if (d) { ftoast(`賣出 ${d.count} 條，+${num(d.total)} 🪙`); S.fish.bag = null; loadFish(); }
  },
  gamesub(d) { S.games.sub = d.v; render(); },
  async bj_start() { const d = await call('bj_start'); if (d && d.game) { S.games.bjPrev = null; S.games.bj = d.game; render(); finishBj(d.game); } },
  async bj_hit() { await bjStep('bj_hit'); },
  async bj_stand() { await bjStep('bj_stand'); },
  async mines_start() { const d = await call('mines_start'); if (d && d.game) { S.games.minesPrev = null; S.games.mines = d.game; render(); } },
  async reveal(d) {
    const g = await call('mines_reveal', { i: +d.i });
    if (g && g.game) { S.games.minesPrev = S.games.mines; S.games.mines = g.game; render(); if (g.game.exploded != null && g.game.over) thud(); else blip(); }
  },
  async mines_cash() {
    const d = await call('mines_cash');
    if (d && d.game) { S.games.minesPrev = S.games.mines; S.games.mines = d.game; render(); if (d.game.payout > 0) { chime(d.game.payout >= 1000 ? 2 : 1); burstAt($('gm-body'), { n: 40 }); } }
  },
  spin: () => doSpin(),
  flip: (d) => doFlip(d.v),
  async lbuy(d) {
    const l = await call('lottery_buy', { count: +d.n });
    if (l) { S.lot = l; ftoast(`買了 ${d.n} 注`); blip(); render(); }
  },
  async mjoin() { const d = await call('mono_join'); if (d) { S.mono = d; ftoast('歡迎加入大富翁'); render(); } },
  mroll: () => monoRollAnim(),
  async mdec(d) {
    const r = await call('mono_decide', { action: d.v });
    if (r) { S.mono = r; S.monoEv = r.msg ? [r.msg] : []; render(); }
  },
  async mbail() {
    const r = await call('mono_bail');
    if (r) { S.mono = r; S.monoEv = r.msg ? [r.msg] : []; render(); }
  },
  tile(d) { S.monoSel = S.monoSel === +d.i ? null : +d.i; render(); },
  shopkind(d) { S.shop.kind = d.v; render(); },
  async buy(d) {
    const r = await call('shop_buy', { item: d.id });
    if (r) { S.shop.items = r.items; S.profile.data = null; ftoast('購買成功'); chime(1); render(); }
  },
  async equip(d) { const r = await call('equip', { item: d.id }); if (r) { S.shop.items = r.items; S.profile.data = null; render(); } },
  async unequip(d) { const r = await call('equip', { kind: d.k, item: null }); if (r) { S.shop.items = r.items; S.profile.data = null; render(); } },
  topkind(d) { S.top.kind = d.v; S.top.rows = null; render(); LOAD.top(); },
  viewp(d) { S.profile.uid = d.u; S.profile.data = null; go('profile'); },
  myprofile() { S.profile.uid = null; S.profile.data = null; go('profile'); },
};
async function bjStep(op) {
  const d = await call(op);
  if (d && d.game) { S.games.bjPrev = S.games.bj; S.games.bj = d.game; render(); finishBj(d.game); }
}
function finishBj(g) {
  if (!g.over) { blip(); return; }
  const r = BJ_RES[g.result];
  if (r && r[1] === 'win') { chime(g.result === 'blackjack' ? 2 : 1); burstAt($('gm-body'), { n: 36 }); } else if (r && r[1] === 'lose') thud();
}

// ═══════════ 推播：錢包、對局換券卡片 ═══════════
let cardTimer = 0;
function showGrant(g) {
  if (S.wallet) { S.wallet.tickets = g.tickets; S.wallet.earned = g.earned; setWallet(S.wallet); }
  const el = $('fun-card');
  const parts = (g.parts || []).map((p) => `${esc(p[0])} +${esc(p[1])}`).join('・');
  el.innerHTML = `<div class="fc-t">打完 ${esc(g.game)} ${esc(g.queue)}(${g.won ? '勝利' : '落敗'}) <b>${esc(g.who)}</b> <small>${esc(g.kda)}</small></div>
    <div class="fc-s">→ 表現 ${esc(g.score)}/8${parts ? `：${parts}` : ''}</div><div class="fc-g">→ <b>+${esc(g.got)} 張券</b>(現在 ${esc(g.tickets)} 張)</div>
    <div class="fc-b"><button type="button" class="f-btn pri sm" id="fc-go">去玩</button><button type="button" class="f-btn sm" id="fc-x">收起</button></div>`;
  el.classList.add('show');
  $('fc-go').onclick = () => { el.classList.remove('show'); openPanel('home'); };
  $('fc-x').onclick = () => el.classList.remove('show');
  clearTimeout(cardTimer);
  cardTimer = setTimeout(() => el.classList.remove('show'), 8000);
  if (g.got >= 4) { chime(2); const r = el.getBoundingClientRect(); burst(r.left + r.width / 2, r.top + 20, { n: 70, gold: true, up: false }); }
}
function onPush(what, data) {
  if (what === 'wallet' && data) setWallet(data);
  else if (what === 'grant' && data) showGrant(data);
}

// ═══════════ 啟動 ═══════════
export function initFun() {
  if (initFun.done) return;
  initFun.done = true;
  buildDom();
  const panel = $('funpanel'), body = $('fp-body');
  $('fun-fab').onclick = () => (panel.hidden ? openPanel() : closePanel());
  $('fp-x').onclick = closePanel;
  $('fp-snd').onclick = () => {
    S.sound = !S.sound;
    try { localStorage.setItem(SOUND_KEY, S.sound ? '1' : '0'); } catch { /* ignore */ }
    showSnd();
    if (S.sound) chime(1);
  };
  $('fp-nav').addEventListener('click', (ev) => { const b = ev.target.closest('button[data-tab]'); if (b) go(b.dataset.tab); });
  body.addEventListener('click', (ev) => {
    const el = ev.target.closest('[data-act]');
    if (!el || el.disabled || !ACT[el.dataset.act]) return;
    run(() => ACT[el.dataset.act](el.dataset, el));
  });
  body.addEventListener('submit', (ev) => {
    ev.preventDefault();
    if (ev.target.dataset.form === 'link') run(() => ACT.link());
  });
  // 結算畫面的「去玩活動」：lol.js / valo.js 會一直重畫 innerHTML，所以事件掛在 document
  document.addEventListener('click', (ev) => { if (ev.target.closest('.fun-go')) openPanel('home'); });
  document.addEventListener('keydown', (ev) => { if (ev.key === 'Escape' && !panel.hidden) closePanel(); });
  // 圖片載入失敗 → 換成佔位文字
  document.addEventListener('error', (ev) => {
    const t = ev.target;
    if (t.tagName !== 'IMG' || !t.parentElement || t.parentElement.dataset.fb === undefined) return;
    t.replaceWith(document.createTextNode(t.parentElement.dataset.fb));
  }, true);
  onFun({
    push: onPush,
    celebrate: celebrate,
    auth: () => { updateFab(); if (!locked()) { call('home'); } if (!panel.hidden) render(); },
  });
  if (isMock) startMock({ push: onPush, celebrate });
  if (!locked()) call('home');
}
