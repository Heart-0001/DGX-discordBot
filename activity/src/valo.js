// Valorant 模式：選角(我方 5 人) / 對局中(雙方 10 人) / 賽後計分板 / 閒置。
// 資料來源：valo/ws 推 {type:'state', phase, match, recents, eog}；圖示走同源 valo/img/*。
// 畫在跟 LoL 同一個 #lol 容器裡，加 class "valo" 換成 Valorant 配色。

let root = null;
let ws = null;
let running = false;
let reconnectT = 0;
let state = null;
let lastKey = '';
const badImg = new Set();

const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const num = (n) => (Number(n) || 0).toLocaleString('zh-TW');
const mmss = (s) => { s = Math.max(0, Math.floor(s || 0)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`; };
const ago = (ms) => {
  const m = Math.max(0, (Date.now() - ms) / 60000);
  if (m < 60) return `${Math.round(m)}分前`;
  if (m < 60 * 24) return `${Math.round(m / 60)}小時前`;
  return `${Math.round(m / 1440)}天前`;
};

function img(src, cls = '', title = '', fallback = '') {
  const t = title ? ` title="${esc(title)}"` : '';
  if (!src || badImg.has(src)) return `<span class="ph ${cls}"${t}>${esc(fallback)}</span>`;
  return `<span class="ph ${cls}"${t}><img src="${esc(src)}" alt="" data-fb="${esc(fallback)}" loading="lazy"></span>`;
}
const agentImg = (p, cls = 'champ big') => img(p.agentId ? `valo/img/agent/${p.agentId}.png` : '', cls, p.agent || '', (p.agent || '?').slice(0, 1));
const tierImg = (tier) => (tier ? img(`valo/img/tier/${tier}.png`, 'crest', '', '') : '');

const TIER_CLS = (t) => (t >= 27 ? 't-challenger' : t >= 24 ? 't-master' : t >= 21 ? 't-diamond' : t >= 18 ? 't-emerald'
  : t >= 15 ? 't-platinum' : t >= 12 ? 't-gold' : t >= 9 ? 't-silver' : t >= 6 ? 't-bronze' : 't-iron');

function nameHtml(p) {
  if (p.hidden) return '<span class="hid">隱藏名字</span>';
  if (!p.name) return '<span class="hid">—</span>';
  return `${esc(p.name)}${p.tag ? `<small>#${esc(p.tag)}</small>` : ''}`;
}

function rankHtml(r) {
  if (!r || !r.tier) return '<span class="rank unranked">未定級</span>';
  return `<span class="rank ${TIER_CLS(r.tier)}">${tierImg(r.tier)}<b>${esc(r.name)}</b><small>${r.rr} RR</small></span>`;
}

// 本季勝率 + 歷史最高
function seasonHtml(r, compact = false) {
  if (!r) return '';
  const parts = [];
  if (r.games) {
    const cls = r.wr >= 55 ? 'good' : r.wr <= 45 ? 'bad' : '';
    parts.push(compact ? `<span class="wr ${cls}"><b>${r.wr}%</b></span>`
      : `<span class="wr ${cls}">本季 ${r.wins}勝${r.games - r.wins}敗 <b>${r.wr}%</b></span>`);
  }
  if (!compact && r.peak && r.peak > r.tier) parts.push(`<span class="dim">峰 ${esc(r.peakName)}</span>`);
  return parts.join(' ');
}

// 最近 5 場：選角(5 張卡)列成清單；對局中(10 張卡)塞不下，改成一排小籤
function gamesHtml(puuid, chips = false) {
  const gs = (state.recents || {})[puuid];
  if (gs === undefined) return '<div class="pc-games"><span class="skel wide"></span></div>';
  if (!gs.length) return '';
  if (chips) {
    const k = gs.reduce((s, g) => s + g.k + g.a, 0), d = gs.reduce((s, g) => s + g.d, 0);
    const c = gs.map((g) => `<span class="chip ${g.w === null ? 'rm' : g.w ? 'w' : 'l'}" title="${esc(`${g.agent} · ${g.queue} · ${g.k}/${g.d}/${g.a}`)}">${g.w === null ? '—' : g.w ? '勝' : '敗'}</span>`).join('');
    return `<div class="pc-chips">${c}<span class="dim">KDA ${(k / Math.max(1, d)).toFixed(1)}</span></div>`;
  }
  const rows = gs.map((g) => {
    const res = g.w === null ? 'rm' : g.w ? 'w' : 'l';
    const kd = ((g.k + g.a) / Math.max(1, g.d)).toFixed(1);
    return `<li class="g ${res}" title="${esc(`${g.queue} · KDA ${kd}`)}">
      ${agentImg({ agentId: g.agentId, agent: g.agent }, 'champ xs')}
      <span class="g-r">${g.w === null ? '—' : g.w ? '勝' : '敗'}</span>
      <span class="g-kda">${g.k}/<b>${g.d}</b>/${g.a}</span>
      <span class="g-q">${esc(g.agent)} · ${esc(g.queue)}</span><span class="g-t">${ago(g.t)}</span></li>`;
  }).join('');
  const w = gs.filter((g) => g.w === true).length, l = gs.filter((g) => g.w === false).length;
  return `<div class="pc-games"><div class="pg-h">近 ${gs.length} 場 <b class="gw">${w}勝</b> <b class="gl">${l}敗</b></div><ul>${rows}</ul></div>`;
}

function playerCard(p, chips = false) {
  const r = p.rank;
  const lvl = p.level ? `Lv ${p.level}` : '';
  return `<div class="pcard${p.me ? ' me' : ''}">
    ${agentImg(p)}
    <div class="pc-info">
      <div class="pc-name">${nameHtml(p)}</div>
      <div class="pc-sub"><span class="pc-champ">${esc(p.agent)}${p.locked === false ? '…' : ''}</span><span class="pc-lvl">${esc(lvl)}</span></div>
      <div class="pc-rank">${rankHtml(r)}</div>
      <div class="pc-wr"><span class="full">${seasonHtml(r)}</span><span class="compact">${seasonHtml(r, true)}</span></div>
      ${gamesHtml(p.puuid, chips)}
    </div>
  </div>`;
}

const badge = (text, cls = '') => (text ? `<span class="badge ${cls}">${esc(text)}</span>` : '');
function header(center = '', right = '') {
  const m = state.match || state.eog;
  const q = m ? `${esc(m.queue)} · ${esc(m.map)}` : 'VALORANT';
  return `<header class="lol-top"><div class="lt-left"><span class="lt-q">${q}</span></div>
    <div class="lt-mid">${center}</div><div class="lt-right">${right}${badge(state.sub)}</div></header>`;
}

function viewIdle() {
  const off = state.phase === 'offline';
  return `<div class="lol-idle">
    <div class="lol-logo">${off ? '⏻' : '◆'}</div>
    <div class="lol-idle-text">${esc(state.sub || (off ? 'PC 離線' : '閒置中'))}</div>
    <div class="lol-idle-hint">${off ? '等玩家的電腦和 Riot Client 上線後會自動顯示' : '開始配對後，這裡會顯示隊友與對手的牌位'}</div>
  </div>`;
}

// 近 N 場的總結：KDA、ACS、ADR、HS%、首殺(回合制才算 ACS/ADR；團隊死鬥 rounds=1 沒意義)
function aggregate(gs) {
  const s = { n: gs.length, w: 0, l: 0, k: 0, d: 0, a: 0, score: 0, rounds: 0, dmg: 0, hs: 0, shots: 0, fb: 0 };
  for (const g of gs) {
    if (g.w === true) s.w++; else if (g.w === false) s.l++;
    s.k += g.k; s.d += g.d; s.a += g.a; s.fb += g.fb || 0;
    s.hs += g.hs || 0; s.shots += (g.hs || 0) + (g.bs || 0) + (g.ls || 0);
    if (g.queue !== '團隊死鬥' && g.queue !== '死鬥' && g.rounds > 1) { s.score += g.score || 0; s.rounds += g.rounds; s.dmg += g.dmg || 0; }
  }
  s.kda = ((s.k + s.a) / Math.max(1, s.d)).toFixed(1);
  s.acs = s.rounds ? Math.round(s.score / s.rounds) : null;
  s.adr = s.rounds ? Math.round(s.dmg / s.rounds) : null;
  s.hsp = s.shots ? Math.round(s.hs * 100 / s.shots) : null;
  return s;
}

const cls3 = (v, hi, lo) => (v == null ? '' : v >= hi ? 'good' : v <= lo ? 'bad' : '');
const stat = (v, suffix = '') => (v == null ? '<span class="dim">—</span>' : `<b>${v}</b>${suffix}`);

// 對局中：一人一列(像遊戲內 TAB，但多了牌位、本季、近 5 場數據)，第二行貼出近 5 場
function liveRow(p) {
  const r = p.rank || {};
  const gs = (state.recents || {})[p.puuid];
  const s = gs ? aggregate(gs) : null;
  const games = gs === undefined ? '<span class="skel wide"></span>' : (gs.length ? gs.map((g) => {
    const res = g.w === null ? 'rm' : g.w ? 'w' : 'l';
    return `<span class="hg ${res}" title="${esc(`${g.queue} · ${ago(g.t)}`)}">${agentImg({ agentId: g.agentId, agent: g.agent }, 'champ xs')}
      <span class="g-r">${g.w === null ? '—' : g.w ? '勝' : '敗'}</span><span class="g-kda">${g.k}/<b>${g.d}</b>/${g.a}</span><small>${esc(g.queue)}</small></span>`;
  }).join('') : '<span class="dim">沒有近期對戰</span>');
  const season = r.games ? `<span class="wr ${cls3(r.wr, 55, 45)}"><small>競技</small> ${r.wins}勝${r.games - r.wins}敗 <b>${r.wr}%</b></span>` : '<span class="dim"><small>競技</small> —</span>';
  // 當前模式：最近 10 場(mmr 裡非競技模式的本季資料是壞的，所以用對戰紀錄算)
  const md = (state.modes || {})[p.puuid];
  const mode = md === undefined ? '<span class="skel"></span>' : !md.n ? `<span class="dim"><small>${esc(state.match.queue)}</small> 沒紀錄</span>`
    : md.w + md.l ? `<span class="wr ${cls3(Math.round(md.w * 100 / (md.w + md.l)), 55, 45)}"><small>${esc(md.q)}</small> 近${md.n} ${md.w}勝${md.l}敗 <b>${Math.round(md.w * 100 / (md.w + md.l))}%</b></span>`
    : `<span class="wr"><small>${esc(md.q)}</small> 近${md.n} KDA <b>${md.kda}</b></span>`;
  const peak = r.peak && r.peak > r.tier ? `<small>峰 ${esc(r.peakName)}</small>` : '';
  return `<div class="lrow${p.me ? ' me' : ''}">
    <span class="c-champ">${agentImg(p, 'champ')}</span>
    <span class="c-name"><span class="nm">${nameHtml(p)}</span><small class="cn">${esc(p.agent)}${p.locked === false ? '…' : ''}${p.level ? ` · Lv ${p.level}` : ''}</small></span>
    <span class="c-rank">${rankHtml(r)}${peak}</span>
    <span class="c-season" title="本季競技 / 當前模式最近 10 場">${season}<br>${mode}</span>
    <span class="c-rec" title="近 5 場勝敗">${s ? `<span class="chip w">${s.w}</span><span class="chip l">${s.l}</span>` : '<span class="skel"></span>'}</span>
    <span class="c-kda ${s ? cls3(+s.kda, 1.3, 0.8) : ''}">${s ? stat(s.kda) : ''}</span>
    <span class="c-acs ${s ? cls3(s.acs, 230, 150) : ''}">${s ? stat(s.acs) : ''}</span>
    <span class="c-adr ${s ? cls3(s.adr, 150, 100) : ''}">${s ? stat(s.adr) : ''}</span>
    <span class="c-hs ${s ? cls3(s.hsp, 25, 12) : ''}">${s ? stat(s.hsp, '%') : ''}</span>
    <span class="c-fb">${s ? stat(s.fb) : ''}</span>
    <span class="c-hist">${games}</span>
  </div>`;
}

function teamTable(t, title) {
  const head = `<div class="lrow lhead"><span></span><span>玩家</span><span>牌位</span><span>本季競技 / 目前模式</span><span>近5</span>
    <span>KDA</span><span title="近 5 場平均每回合分數">ACS</span><span title="近 5 場平均每回合傷害">ADR</span><span title="近 5 場爆頭率">HS%</span><span title="近 5 場首殺">首殺</span><span>近 5 場</span></div>`;
  return `<div class="team ${t.mine ? 'ally' : 'enemy'}"><div class="team-h"><span>${title}</span><small>近 5 場數據 · ACS/ADR 只算回合制</small></div>${head}${t.players.map(liveRow).join('')}</div>`;
}

function viewMatch() {
  const m = state.match;
  const title = state.phase === 'pregame' ? '選角階段' : '對局中';
  const blocks = m.teams.map((t) => teamTable(t, t.mine ? '我方' : '敵方')).join('');
  const enemy = state.phase === 'pregame' ? '<div class="enemy-ph">敵方 5 人 · 進讀取畫面後才看得到</div>' : '';
  const sc = state.score;
  const score = sc && sc.ally != null ? `<span class="score" title="回合比數(我方 : 敵方)"><b class="sa">${sc.ally}</b> : <b class="se">${sc.enemy}</b></span>` : '';
  return `${header(`<span class="lt-title">${title}</span>`, score)}
    <div class="lol-body live-body v-teams"><div class="teams">${blocks}</div>${enemy}</div>`;
}

function viewEog() {
  const e = state.eog;
  const mine = e.teams.find((t) => t.mine);
  const res = mine && mine.won != null ? (mine.won ? '勝利' : '落敗') : '結束';
  const score = e.teams.length === 2 ? `${e.teams[0].rounds} : ${e.teams[1].rounds}` : '';
  // 回合制跟遊戲內 TAB 一樣顯示 ACS / ADR(每回合平均)；團隊死鬥只有 1 回合 → 顯示整場總分
  const rb = e.teams.some((t) => t.players.some((p) => p.rounds > 1));
  const rows = (t) => t.players.map((p) => `<div class="erow${p.me ? ' me' : ''}">
      <span class="c-champ">${agentImg(p, 'champ')}</span>
      <span class="c-name">${nameHtml(p)}${p.mvp ? ' <span class="badge gold">MVP</span>' : ''}<small class="cn">${esc(p.agent)}</small></span>
      <span class="c-rank">${p.tier ? `<span class="rank ${TIER_CLS(p.tier)}">${tierImg(p.tier)}</span>` : ''}</span>
      <span class="c-kda"><span class="kda"><b>${p.k}</b>/<b class="d">${p.d}</b>/<b>${p.a}</b></span></span>
      <span class="c-score" title="${rb ? 'ACS：平均每回合戰鬥分數' : '整場戰鬥分數'}">${num(rb ? Math.round(p.score / p.rounds) : p.score)}</span>
      <span class="c-dmg" title="${rb ? 'ADR：平均每回合傷害' : '整場傷害'}">${num(rb ? Math.round(p.dmg / p.rounds) : p.dmg)}</span>
    </div>`).join('');
  const head = `<div class="erow lhead"><span></span><span>玩家</span><span></span><span>K/D/A</span><span>${rb ? 'ACS' : '戰鬥分數'}</span><span>${rb ? 'ADR' : '傷害'}</span></div>`;
  const blocks = e.teams.map((t) => `<div class="team ${t.mine ? 'ally' : 'enemy'}${t.won ? ' won' : ''}">
      <div class="team-h"><span>${e.teams.length === 1 ? '全部玩家' : t.mine ? '我方' : '敵方'}</span><span>${t.won == null ? '' : t.won ? '勝' : '敗'}</span></div>${head}${rows(t)}</div>`).join('');
  return `${header(`<span class="lt-title eb-title">${res}</span>`, score ? `<span class="score">${score}</span>` : '')}
    <div class="lol-body eog-body"><div class="eb-meta"><span>${mmss(e.length)}</span><span>${e.start ? ago(e.start * 1000) : ''}</span></div><div class="teams">${blocks}</div></div>`;
}

function render() {
  if (!root || !state) return;
  let view = 'idle', html;
  const ph = state.phase;
  if ((ph === 'pregame' || ph === 'coregame') && state.match) { view = 'cs'; html = viewMatch(); }
  else if (ph === 'eog' && state.eog) { view = 'eog'; html = viewEog(); }
  else html = viewIdle();
  if (html === lastKey) return;
  lastKey = html;
  root.className = `valo v-${view}`;
  root.innerHTML = html;
}

function connect() {
  if (!running) return;
  const url = new URL('valo/ws', location.href);
  url.protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const sock = new WebSocket(url);
  ws = sock;
  sock.onmessage = (ev) => {
    let m;
    try { m = JSON.parse(ev.data); } catch { return; }
    if (m.type === 'state') { delete m.type; state = m; render(); }
  };
  sock.onclose = () => {
    if (ws !== sock || !running) return;
    if (!state) showMsg('連線中斷，重新連線中…');
    reconnectT = setTimeout(connect, 2000);
  };
}

function showMsg(text) {
  if (!root) return;
  lastKey = '';
  root.className = 'valo v-idle';
  root.innerHTML = `<div class="lol-idle"><div class="lol-logo">◆</div><div class="lol-idle-text">${esc(text)}</div></div>`;
}

function onImgError(e) {
  const t = e.target;
  if (t.tagName !== 'IMG') return;
  badImg.add(t.getAttribute('src'));
  const ph = t.parentElement;
  t.remove();
  if (ph) { ph.textContent = t.dataset.fb || ''; ph.classList.add('noimg'); }
}

export function startValo(el) {
  if (running) return;
  running = true;
  root = el;
  root.hidden = false;
  root.addEventListener('error', onImgError, true);
  if (!state) showMsg('連線中…');
  else { lastKey = ''; render(); }
  connect();
}

export function stopValo() {
  if (!running) return;
  running = false;
  clearTimeout(reconnectT);
  const sock = ws; ws = null;
  if (sock) sock.close();
  if (root) {
    root.removeEventListener('error', onImgError, true);
    root.classList.remove('valo');
    root.hidden = true;
  }
}
