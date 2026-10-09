// LoL 模式：選角 / 讀取 / 遊戲中(TAB 計分板) / 結算 / 閒置。
// 資料來源：lol/ws 推 {type:'state', ...}；圖示一律走同源 lol/img/*(Discord CSP 擋外部網域)。

const TIERS = {
  IRON: '鐵牌', BRONZE: '銅牌', SILVER: '銀牌', GOLD: '金牌', PLATINUM: '白金', EMERALD: '翡翠',
  DIAMOND: '鑽石', MASTER: '大師', GRANDMASTER: '宗師', CHALLENGER: '菁英',
};
const APEX = new Set(['MASTER', 'GRANDMASTER', 'CHALLENGER']);   // 這幾階沒有分級
const ITEM_SLOTS = 7;

let root = null;
let ws = null;
let running = false;
let reconnectT = 0;
let tickT = 0;
let staticData = { champions: {}, augments: {}, spells: {} };
let state = null;
let lastKey = '';     // 上次畫的內容(JSON)，一樣就不重畫
let clockBase = null; // {time, at}：遊戲時間本地內插
const badImg = new Set();   // 404 過的圖，之後直接畫替代文字，避免每次重畫都閃一下

// ── 小工具 ──
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const num = (n) => (Number(n) || 0).toLocaleString('zh-TW');
const mmss = (s) => {
  s = Math.max(0, Math.floor(s || 0));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
};
const kGold = (n) => `${((Number(n) || 0) / 1000).toFixed(1)}k`;
const shortQueue = (name) => (name || '').split('：').pop();

// 圖片：壞掉過的直接畫 fallback 文字；第一次 404 由 root 上的 error 監聽處理
function img(src, cls = '', title = '', fallback = '') {
  const t = title ? ` title="${esc(title)}"` : '';
  if (!src || badImg.has(src)) return `<span class="ph ${cls}"${t}>${esc(fallback)}</span>`;
  return `<span class="ph ${cls}"${t}><img src="${esc(src)}" alt="" data-fb="${esc(fallback)}" loading="lazy"></span>`;
}
const champImg = (id, cls = 'champ', title = '') =>
  img(id ? `lol/img/champ/${id}.png` : '', cls, title, id ? (staticData.champions[id] || '?').slice(0, 1) : '?');
const itemImg = (id, name = '') => (id ? img(`lol/img/item/${id}.png`, 'item', name, '?') : '<span class="ph item empty"></span>');
const spellImg = (keyOrId, name = '') =>
  img(keyOrId ? `lol/img/spell/${keyOrId}.png` : '', 'spell', name, (name || '?').slice(0, 1));
const augImg = (id) => {
  const a = staticData.augments[id] || {};
  const rar = { kSilver: 'silver', kGold: 'gold', kPrismatic: 'prism' }[a.rarity] || '';
  return img(`lol/img/aug/${id}.png`, `aug ${rar}`, a.name || `強化 ${id}`, '✦');
};

function riotName(p) {
  if (!p || p.hidden || !p.name) return '<span class="hid">隱藏</span>';
  return `${esc(p.name)}${p.tag ? `<small>#${esc(p.tag)}</small>` : ''}`;
}

// 牌位：單雙 → 彈性 → 未排名
function rankHtml(prof, compact = false) {
  if (!prof) return '<span class="skel"></span>';
  const r = prof.solo || prof.flex;
  if (!r || !r.tier) return '<span class="rank unranked">未排名</span>';
  const tier = r.tier.toUpperCase();
  const label = `${TIERS[tier] || tier}${APEX.has(tier) ? '' : ` ${r.division || ''}`}`;
  // 牌位的勝敗是整季完整場數(不是近期)
  const season = r.w + r.l ? ` · 本季 ${r.w}勝${r.l}敗 ${Math.round((r.w / (r.w + r.l)) * 100)}%` : '';
  const lp = compact ? '' : ` <small>${num(r.lp)} LP${prof.solo ? '' : ' · 彈性'}${season}</small>`;
  return `<span class="rank t-${tier.toLowerCase()}">${img(`lol/img/tier/${tier.toLowerCase()}.svg`, 'crest', label, '')}<b>${esc(label)}</b>${lp}</span>`;
}

// 近期戰績：目前佇列近 N 場；沒有就用 recentAll(近期)
function recentHtml(prof, queueName, compact = false) {
  if (!prof) return '<span class="skel wide"></span>';
  if (prof.private) return '<span class="wr dim">戰績未公開</span>';
  const r = prof.recent || prof.recentAll;
  if (!r || !(r.w + r.l)) return '<span class="wr dim">無近期戰績</span>';
  const n = r.w + r.l, pct = Math.round((r.w / n) * 100);
  const cls = pct >= 55 ? 'good' : pct <= 45 ? 'bad' : '';
  const label = prof.recent ? `${esc(shortQueue(queueName) || '近期')} 近 ${n} 場` : `近期 ${n} 場`;
  if (compact) return `<span class="wr ${cls}"><b>${pct}%</b> <small>${r.w}勝${r.l}敗</small></span>`;
  return `<span class="wr ${cls}">${label} ${r.w}勝 ${r.l}敗 <b>(${pct}%)</b></span>`;
}

// 英雄熟練度(Riot API)：這位玩家用目前這隻英雄的熟練等級與點數
function masteryHtml(prof, champId) {
  const m = prof && champId && prof.mastery ? prof.mastery[String(champId)] : null;
  if (!m) return '';
  const pts = m.points >= 10000 ? `${(m.points / 10000).toFixed(m.points >= 1e6 ? 0 : 1)}萬` : num(m.points);
  return `<span class="mastery" title="英雄熟練度 ${num(m.points)} 點">熟練 Lv${m.level} · ${pts}</span>`;
}

const kda = (p) => `<span class="kda"><b>${p.k}</b>/<b class="d">${p.d}</b>/<b>${p.a}</b></span>`;
const badge = (text, cls = '') => (text ? `<span class="badge ${cls}">${esc(text)}</span>` : '');
const profOf = (puuid) => (puuid && state.profiles ? state.profiles[puuid] : null);

// ── 各畫面 ──
function viewIdle() {
  const off = state.phase === 'offline';
  return `<div class="lol-idle">
    <div class="lol-logo">${off ? '⏻' : '⚔'}</div>
    <div class="lol-idle-text">${esc(state.sub || (off ? 'PC 離線' : '閒置中'))}</div>
    <div class="lol-idle-hint">${off ? '等玩家的電腦上線後會自動顯示' : '開始配對後，這裡會顯示選角與對戰資訊'}</div>
  </div>`;
}

function header(center = '', right = '') {
  const q = state.queue ? esc(state.queue.name) : '英雄聯盟';
  return `<header class="lol-top"><div class="lt-left"><span class="lt-q">${q}</span></div>
    <div class="lt-mid">${center}</div><div class="lt-right">${right}${badge(state.sub)}</div></header>`;
}

// 幾分鐘/小時/天前
const ago = (sec) => {
  const m = Math.max(0, (Date.now() / 1000 - sec) / 60);
  if (m < 60) return `${Math.round(m)}分前`;
  if (m < 60 * 24) return `${Math.round(m / 60)}小時前`;
  return `${Math.round(m / 1440)}天前`;
};

// 選角卡片：最近 10 場(客戶端對戰紀錄)
function gamesHtml(prof) {
  if (!prof) return '<div class="pc-games"><span class="skel wide"></span></div>';
  const gs = prof.games || [];
  if (prof.private || !gs.length) return '';
  const rows = gs.map((g) => {
    const res = g.remake ? 'rm' : g.w ? 'w' : 'l';
    const kd = g.d ? ((g.k + g.a) / g.d).toFixed(1) : 'P';
    return `<li class="g ${res}" title="${esc(`${g.q} · ${mmss(g.dur)} · KDA ${kd}`)}">
      ${champImg(g.c, 'champ xs', staticData.champions[g.c] || '')}
      <span class="g-r">${g.remake ? '重開' : g.w ? '勝' : '敗'}</span>
      <span class="g-kda">${g.k}/<b>${g.d}</b>/${g.a}</span>
      <span class="g-q">${esc(g.q)}</span><span class="g-t">${ago(g.t)}</span></li>`;
  }).join('');
  const w = gs.filter((g) => !g.remake && g.w).length, l = gs.filter((g) => !g.remake && !g.w).length;
  return `<div class="pc-games"><div class="pg-h">近 ${gs.length} 場 <b class="gw">${w}勝</b> <b class="gl">${l}敗</b></div><ul>${rows}</ul></div>`;
}

function playerCard(p, prof, champId, withGames = false) {
  const qn = state.queue?.name;
  const pic = champId !== undefined
    ? champImg(champId, 'champ big', staticData.champions[champId] || '')
    : img(prof?.icon ? `lol/img/icon/${prof.icon}.png` : '', 'champ big icon', '', '?');
  const champName = champId ? esc(staticData.champions[champId] || '') : (champId === 0 ? '選擇中…' : '');
  const lvl = prof ? `Lv ${prof.level}` : '';
  return `<div class="pcard${p.me ? ' me' : ''}">
    ${pic}
    <div class="pc-info">
      <div class="pc-name">${riotName(p)}</div>
      <div class="pc-sub">${champName ? `<span class="pc-champ">${champName}</span>` : ''}<span class="pc-lvl">${esc(lvl)}</span></div>
      <div class="pc-mas">${masteryHtml(prof, champId)}</div>
      <div class="pc-rank">${p.hidden || !p.puuid ? '<span class="dim">—</span>' : rankHtml(prof)}</div>
      <div class="pc-wr">${p.hidden || !p.puuid ? '' : `<span class="full">${recentHtml(prof, qn)}</span><span class="compact">${recentHtml(prof, qn, true)}</span>`}</div>
      ${withGames && !p.hidden && p.puuid ? gamesHtml(prof) : ''}
    </div>
  </div>`;
}

function viewChampSelect() {
  const cs = state.champselect || {};
  const team = cs.team || [];
  const cards = team.map((p) => playerCard(p, profOf(p.puuid), p.championId || 0, true)).join('');
  const bench = cs.benchEnabled
    ? `<div class="bench"><span class="bench-l">板凳</span>${(cs.bench || []).map((id) => champImg(id, 'champ sm', staticData.champions[id] || '')).join('') || '<span class="dim">空</span>'}</div>`
    : '';
  const right = cs.rerolls ? badge(`重骰 ×${cs.rerolls}`, 'gold') : '';
  const enemy = cs.enemyCount ? `<div class="enemy-ph">敵方 ${cs.enemyCount} 人 · 選角中不公開</div>` : '';
  return `${header('<span class="lt-title">選角階段</span>', right)}
    <div class="lol-body cs-body"><div class="cards">${cards}</div>${bench}${enemy}</div>`;
}

function viewLoading() {
  const profs = Object.entries(state.profiles || {});
  profs.sort(([a], [b]) => (b === state.me) - (a === state.me));
  const cards = profs.map(([puuid, prof]) => playerCard({ ...prof, puuid, me: puuid === state.me }, prof)).join('');
  return `${header('<span class="lt-title">載入遊戲中<i class="dots"><i></i><i></i><i></i></i></span>')}
    <div class="lol-body cs-body loading"><div class="cards">${cards || '<div class="dim">等待玩家資料…</div>'}</div></div>`;
}

function liveRow(p) {
  const prof = p.hidden ? null : profOf(p.puuid);
  const items = Array(ITEM_SLOTS).fill(null);
  for (const it of p.items || []) if (it.slot >= 0 && it.slot < ITEM_SLOTS) items[it.slot] = it;
  const itemsHtml = items.map((it) => (it ? itemImg(it.id, it.count > 1 ? `${it.name} ×${it.count}` : it.name) : itemImg(0))).join('');
  const spells = (p.spells || []).slice(0, 2).map((s) => spellImg(s.key, s.name)).join('');
  const dead = p.dead ? `<span class="respawn" data-respawn="${Number(p.respawn) || 0}">${Math.ceil(p.respawn || 0)}</span>` : '';
  return `<div class="lrow${p.me ? ' me' : ''}${p.dead ? ' dead' : ''}">
    <div class="c-champ">${champImg(p.champ?.id || p.champ?.key, 'champ', p.champ?.name || '')}<span class="lvl">${p.level}</span>${dead}</div>
    <div class="c-spells">${spells}</div>
    <div class="c-name"><div class="nm">${riotName(p)}</div><div class="cn">${esc(p.champ?.name || '')} ${masteryHtml(prof, p.champ?.id)}</div></div>
    <div class="c-prof">${p.hidden || !p.puuid ? '<span class="dim">—</span>' : `${rankHtml(prof, true)}${recentHtml(prof, state.queue?.name, true)}`}</div>
    <div class="c-kda">${kda(p)}</div>
    <div class="c-cs">${p.cs}</div>
    <div class="c-gold" title="身上裝備的總價(推算)">${kGold(p.gold)}</div>
    <div class="c-items">${itemsHtml}</div>
  </div>`;
}

// 我方推薦出裝(後端算)：每件都附根據，滑鼠移上去看全部
function recCell(p) {
  const champ = champImg(p.champ?.id || p.champ?.key, 'champ sm', p.champ?.name || '');
  const recs = p.rec || [];
  if (!recs.length) return `<div class="rcell${p.me ? ' me' : ''}">${champ}<span class="pn dim">還沒有出裝資料</span></div>`;
  const icons = recs.map((r) => `<span class="ri${r.counter ? ' counter' : ''}">${itemImg(r.id, `${r.name}\n${r.reasons.join('\n')}`)}</span>`).join('');
  const top = recs[0];
  const why = top.reasons.find((x) => x.includes('→')) || top.reasons[0];
  return `<div class="rcell${p.me ? ' me' : ''}">
    <div class="rc-top">${champ}<div class="rc-items">${icons}</div>${p.full ? '<span class="badge">滿裝可換</span>' : ''}</div>
    <div class="rc-why"><b>${esc(top.name)}</b>${esc(why.replace('op.gg ARAM ', '：'))}</div>
  </div>`;
}

function enemySummary(e) {
  if (!e) return '';
  const hot = (v, t) => (v >= t ? ' class="hot"' : '');
  return `<span class="esum">對面：物理 <b${hot(e.phys, 60)}>${e.phys}%</b> 魔法 <b${hot(e.magic, 60)}>${e.magic}%</b><small>(${esc(e.src)})</small>
    · 護甲 <b${hot(e.armorPct, 20)}>${e.armorPct}%</b> · 魔抗 <b${hot(e.mrPct, 20)}>${e.mrPct}%</b> · 吸血 <b${hot(e.healPct, 10)}>${e.healPct}%</b></span>`;
}

// 下一件大裝備預測：op.gg 該英雄 ARAM 常出裝為主，手上零件加分(後端算)
function predictCell(p) {
  const n = (p.next || [])[0];
  const alt = (p.next || [])[1];
  const champ = champImg(p.champ?.id || p.champ?.key, 'champ sm', p.champ?.name || '');
  if (!n) return `<div class="pcell ${p.ally ? 'al' : 'en'}">${champ}<span class="pn dim">${p.full ? '裝備已滿' : '還猜不到'}</span></div>`;
  return `<div class="pcell ${p.ally ? 'al' : 'en'}${p.me ? ' me' : ''}" title="${esc(alt ? `第二可能：${alt.name}` : '')}">
    ${champ}<span class="arrow">→</span>${itemImg(n.id, n.name)}
    <div class="pn"><b>${esc(n.name)}</b><small>差 ${num(n.left)} 金${n.popular ? ' · <i class="pop">常出</i>' : ''}</small>
      <div class="pbar"><div style="width:${n.progress}%"></div></div></div>
  </div>`;
}

function teamBlock(title, cls, rows, extra = '') {
  return `<section class="team ${cls}"><div class="team-h"><span>${title}</span>${extra}</div>${rows}</section>`;
}

function eventHtml(e) {
  // 「(我方)」「(敵方)」上色
  const text = esc(e.text).replace(/\((我方|敵方)\)/g, (_, w) => `<i class="${w === '我方' ? 'al' : 'en'}">${w}</i>`);
  let side = '';
  if (e.killer) side = e.killer.includes('我方') ? ' ally' : ' enemy';
  else if (e.side) side = e.side === '我方' ? ' enemy' : ' ally';   // 防禦塔：我方的塔倒了算壞消息
  return `<li class="ev k-${esc(e.kind)}${side}"><time>${mmss(e.t)}</time><span>${text}</span></li>`;
}

function bar(cur, max, cls) {
  const pct = max ? Math.max(0, Math.min(100, (cur / max) * 100)) : 0;
  return `<div class="sbar ${cls}"><div style="width:${pct}%"></div><span>${num(Math.round(cur))} / ${num(Math.round(max))}</span></div>`;
}

function viewLive() {
  const L = state.live;
  const ally = L.players.filter((p) => p.ally), enemy = L.players.filter((p) => !p.ally);
  const ga = L.gold?.ally ?? 0, ge = L.gold?.enemy ?? 0, gd = ga - ge;
  const center = `<div class="mid-stack"><span class="score"><b class="al">${L.kills?.ally ?? 0}</b><span class="clock" id="lol-clock">${mmss(L.time)}</span><b class="en">${L.kills?.enemy ?? 0}</b></span>
    <span class="gold-line" title="雙方身上裝備總價(推算)"><b class="al">${kGold(ga)}</b><span class="gdiff ${gd > 0 ? 'up' : gd < 0 ? 'down' : ''}">${gd > 0 ? '+' : ''}${kGold(gd)}</span><b class="en">${kGold(ge)}</b></span></div>`;
  const self = L.self;
  const resCls = { MANA: 'mana', ENERGY: 'energy', NONE: 'none' }[self?.resType] || 'other';
  const selfHtml = self ? `<div class="self">
      <div class="self-h"><span>我的狀態</span><span class="gold-t">${num(Math.floor(self.gold))} <small>金</small></span></div>
      ${bar(self.hp, self.maxHp, 'hp')}
      ${self.maxRes > 0 ? bar(self.res, self.maxRes, resCls) : ''}
    </div>` : '';
  const evs = (L.events || []).map(eventHtml).join('') || '<li class="ev dim">還沒有事件</li>';
  const colh = `<div class="lrow lhead"><div class="c-champ"></div><div class="c-spells"></div><div class="c-name">召喚師</div><div class="c-prof">牌位 / 近期</div><div class="c-kda">K / D / A</div><div class="c-cs">CS</div><div class="c-gold">經濟</div><div class="c-items">裝備</div></div>`;
  const predict = `<section class="predict"><div class="team-h"><span>我方推薦出裝</span>${enemySummary(L.enemyProfile)}</div>
    <div class="pgrid">${ally.map(recCell).join('')}</div>
    <div class="team-h sub"><span>敵方下一件預測</span><small>op.gg 常出裝 + 目前零件</small></div>
    <div class="pgrid">${enemy.map(predictCell).join('')}</div></section>`;
  return `${header(center, self ? `<span class="hdr-gold">${num(Math.floor(self.gold))} 金</span>` : '')}
    <div class="lol-body live-body">
      <div class="teams">
        ${teamBlock('我方', 'ally', colh + ally.map(liveRow).join(''), `<b>${L.kills?.ally ?? 0} 殺 · ${kGold(ga)}</b>`)}
        ${teamBlock('敵方', 'enemy', enemy.map(liveRow).join(''), `<b>${L.kills?.enemy ?? 0} 殺 · ${kGold(ge)}</b>`)}
        ${predict}
      </div>
      <aside class="side">${selfHtml}<div class="feed"><div class="feed-h">即時事件</div><ul>${evs}</ul></div></aside>
    </div>`;
}

// 結算數字：1.2萬 這種短格式；null(拿不到) → —
const short = (n) => (n == null ? '—' : n >= 10000 ? `${(n / 10000).toFixed(1)}萬` : num(n));

function viewEog() {
  const E = state.eog;
  const all = E.teams.flatMap((t) => t.players);
  const max = (k) => Math.max(0, ...all.map((p) => p[k] || 0));
  const top = { dmg: max('dmg'), taken: max('taken'), heal: max('heal'), shield: max('shield'), cc: max('cc'), gold: max('gold') };
  const best = (p, k) => (top[k] > 0 && p[k] === top[k] ? ' best' : '');
  const statBar = (p, k, cls, tip) => `<div class="c-${cls}${best(p, k)}" title="${esc(tip)}"><span>${short(p[k])}</span>
    <div class="dbar ${cls}"><div style="width:${((p[k] || 0) / (top[k] || 1)) * 100}%"></div></div></div>`;
  const cell = (p, k, cls, tip, text = short(p[k])) => `<div class="c-${cls} sc${best(p, k)}" title="${esc(tip)}">${text}</div>`;
  const row = (p) => {
    const dmgTip = p.dmgP == null ? '對英雄傷害' : `物理 ${num(p.dmgP)} · 魔法 ${num(p.dmgM)} · 真實 ${num(p.dmgT)}`;
    const takenTip = p.mitigated == null ? '承受傷害' : `承受 ${num(p.taken)} · 自身減免 ${num(p.mitigated)}`;
    const healTip = p.healAlly == null ? `總治療 ${num(p.heal)}` : `總治療 ${num(p.heal)} · 治療隊友 ${num(p.healAlly)}`;
    const ccTip = p.ccTotal == null ? '控場分數(秒)' : `控場分數 ${p.cc ?? '—'} 秒 · 控場總時間 ${p.ccTotal} 秒`;
    const items = [...(p.items || [])].filter(Boolean);
    while (items.length < ITEM_SLOTS) items.push(0);
    return `<div class="erow${p.me ? ' me' : ''}">
      <div class="c-champ">${champImg(p.champ?.id || p.champ?.key, 'champ', p.champ?.name || '')}<span class="lvl">${p.level}</span></div>
      <div class="c-spells">${(p.spells || []).slice(0, 2).map((id) => spellImg(id, staticData.spells[id] || '')).join('')}</div>
      <div class="c-name"><div class="nm">${riotName(p)}</div><div class="cn">${esc(p.champ?.name || '')}</div></div>
      <div class="c-kda" title="CS ${p.cs}">${kda(p)}</div>
      ${statBar(p, 'dmg', 'dmg', dmgTip)}
      ${statBar(p, 'taken', 'taken', takenTip)}
      ${cell(p, 'heal', 'heal', healTip)}
      ${cell(p, 'shield', 'shield', '護盾隊友(結算資料沒給就是 —)')}
      ${cell(p, 'cc', 'cc', ccTip, p.cc == null ? '—' : `${p.cc}s`)}
      ${cell(p, 'gold', 'gold', `金錢 ${num(p.gold)}`)}
      <div class="c-items">${items.slice(0, ITEM_SLOTS).map((id) => itemImg(id)).join('')}</div>
      <div class="c-augs">${(p.augments || []).map(augImg).join('')}</div>
    </div>`;
  };
  const colh = `<div class="erow lhead"><div class="c-champ"></div><div class="c-spells"></div><div class="c-name">召喚師</div><div class="c-kda">K / D / A</div><div class="c-dmg">輸出</div><div class="c-taken">承受</div><div class="c-heal sc">治療</div><div class="c-shield sc">護盾</div><div class="c-cc sc">控場</div><div class="c-gold sc">金錢</div><div class="c-items">裝備</div><div class="c-augs">強化</div></div>`;
  const teams = [...E.teams].sort((a, b) => b.ally - a.ally).map((t, i) =>
    teamBlock(`${t.ally ? '我方' : '敵方'} · ${t.win ? '勝利' : '失敗'}`, `${t.ally ? 'ally' : 'enemy'}${t.win ? ' won' : ''}`,
      (i === 0 ? colh : '') + t.players.map(row).join(''), `<b>${num(t.kills)} 擊殺</b>`)).join('');
  const stale = state.sub && state.sub !== '結算' ? badge(`上一場 · ${state.sub}`, 'stale') : '';
  const qn = E.queue?.name || state.queue?.name || '';
  return `<div class="eog-banner ${E.win ? 'win' : 'loss'}">
      <div class="eb-title">${E.win ? '勝利' : '失敗'}</div>
      <div class="eb-meta"><span>${esc(qn)}</span><span>遊戲時間 ${mmss(E.length)}</span></div>
      <div class="eb-right">${stale}</div>
    </div>
    <div class="lol-body eog-body">${teams}</div>`;
}

// ── 主畫面 ──
function render() {
  if (!root || !state) return;
  let view = 'idle', html;
  const ph = state.phase;
  if (ph === 'champselect' && state.champselect) { view = 'cs'; html = viewChampSelect(); }
  else if (ph === 'ingame' && state.live) { view = 'live'; html = viewLive(); }
  else if (ph === 'ingame') { view = 'loading'; html = viewLoading(); }
  else if (ph === 'eog' && state.eog) { view = 'eog'; html = viewEog(); }
  else html = viewIdle();
  if (html === lastKey) return;
  lastKey = html;
  // 保留事件列表的捲動位置
  const feed = root.querySelector('.feed ul');
  const scroll = feed ? feed.scrollTop : 0;
  root.className = `v-${view}`;
  root.innerHTML = html;
  const feed2 = root.querySelector('.feed ul');
  if (feed2) feed2.scrollTop = scroll;
  tick();
}

// 遊戲時鐘與復活倒數：伺服器約 2 秒才更新一次，中間用本地時間推算
function tick() {
  if (!root || !clockBase) return;
  const el = (performance.now() - clockBase.at) / 1000;
  const c = root.querySelector('#lol-clock');
  if (c) c.textContent = mmss(clockBase.time + Math.min(el, 15));
  for (const r of root.querySelectorAll('.respawn')) {
    const left = Math.max(0, Math.ceil(Number(r.dataset.respawn) - el));
    r.textContent = left || '';
  }
}

function onState(m) {
  delete m.type;
  state = m;
  const L = m.live;
  if (L) {
    // 時間有變(或第一次)才重設基準，避免同一個時間戳一直把時鐘拉回去
    if (!clockBase || clockBase.time !== L.time || clockBase.phase !== 'live') clockBase = { time: L.time, at: performance.now(), phase: 'live' };
  } else clockBase = null;
  render();
}

function connect() {
  if (!running) return;
  const url = new URL('lol/ws', location.href);
  url.protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const sock = new WebSocket(url);
  ws = sock;
  sock.onmessage = (ev) => {
    let m;
    try { m = JSON.parse(ev.data); } catch { return; }
    if (m.type === 'state') onState(m);
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
  root.className = 'v-idle';
  root.innerHTML = `<div class="lol-idle"><div class="lol-logo">⚔</div><div class="lol-idle-text">${esc(text)}</div></div>`;
}

function onImgError(e) {
  const t = e.target;
  if (t.tagName !== 'IMG') return;
  badImg.add(t.getAttribute('src'));
  const ph = t.parentElement;
  t.remove();
  if (ph) { ph.textContent = t.dataset.fb || ''; ph.classList.add('noimg'); }
}

export function startLol(el) {
  if (running) return;
  running = true;
  root = el;
  root.hidden = false;
  root.addEventListener('error', onImgError, true);
  if (!state) showMsg('連線中…');
  else { lastKey = ''; render(); }
  fetch('lol/static.json').then((r) => r.json()).then((d) => {
    staticData = { champions: {}, augments: {}, spells: {}, ...d };
    lastKey = ''; render();
  }).catch((e) => console.warn('static.json 載入失敗', e));
  connect();
  tickT = setInterval(tick, 250);
}

export function stopLol() {
  if (!running) return;
  running = false;
  clearTimeout(reconnectT);
  clearInterval(tickT);
  const sock = ws; ws = null;
  if (sock) sock.close();
  if (root) {
    root.removeEventListener('error', onImgError, true);
    root.hidden = true;
  }
}
