// 活動面板的假後端：網址帶 ?mock=1 時 live.js 的 sendFun 會改呼叫這裡，資料形狀照 tools/FUN_PROTOCOL.md。
// 全部存在記憶體，重新整理就歸零。圖片一律給 null，前端用 emoji / 字母佔位。

const rnd = (n) => Math.floor(Math.random() * n);
const pick = (a) => a[rnd(a.length)];
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const need = (n) => { if (W.tickets < n) throw new Error(`券不夠：你有 ${W.tickets} 張，這次要 ${n} 張`); W.tickets -= n; };

// ── 錢包 ──
const W = {
  tickets: 14, coins: 1200, earned: 5, daily: false,
  accounts: { lol: [{ name: '阿璃愛好者', tag: 'TW2' }], valo: [] },
  grants: [
    { day: '今天', game: 'LoL', who: '阿璃', kda: '12/2/8', score: 7, got: 3 },
    { day: '今天', game: 'Valorant', who: '婕提', kda: '18/14/4', score: 5, got: 2 },
    { day: '昨天', game: 'LoL', who: '金克絲', kda: '3/9/6', score: 2, got: 1 },
  ],
  title: null, frame: null, bg: null, owned: ['title_newbie'],
};
const wallet = () => JSON.parse(JSON.stringify(W));

// ── 轉蛋 ──
const CARDS = [];
const NAMES = '阿璃 金克絲 凱特琳 凱莎 悠咪 犽凝 賽特 墨菲特 妮可 布里茨 努努 路西恩 賈克斯 雷歐娜 婕提 菲尼克斯 煞 禦 夜露 零 奇亞 捷特 猩紅 蝰蛇 雷茲 歐門 星朵拉 艾克 吉茵珂絲 蔚'.split(' ');
NAMES.forEach((n, i) => CARDS.push({ id: i + 1, name: n, rarity: i < 14 ? 'N' : i < 24 ? 'R' : i < 28 ? 'SR' : 'SSR' }));
const owned = {};
const rollRarity = () => { const r = Math.random() * 100; return r < 5 ? 'SSR' : r < 18 ? 'SR' : r < 48 ? 'R' : 'N'; };
const SELL = { N: 5, R: 15, SR: 60, SSR: 300 };

// ── 釣魚 ──
const FISH = [
  ['👢', '破靴子', 'trash', 0.4], ['🥫', '空罐頭', 'trash', 0.2], ['🐟', '小鯽魚', 'common', 0.6], ['🐠', '熱帶魚', 'common', 0.3], ['🐡', '河豚', 'common', 1.2],
  ['🦐', '大蝦', 'common', 0.2], ['🦑', '透抽', 'rare', 2], ['🐙', '章魚', 'rare', 3], ['🦈', '小鯊魚', 'epic', 40], ['🐢', '海龜', 'epic', 80], ['🐋', '藍鯨幼體', 'legend', 300], ['🐉', '傳說龍魚', 'legend', 120],
];
const FNAME = { trash: '垃圾', common: '普通', rare: '稀有', epic: '史詩', legend: '傳說' };
const FVAL = { trash: 1, common: 8, rare: 40, epic: 150, legend: 900 };
const best = {}, bag = [], records = {};
const rollFish = () => { const r = Math.random() * 100; const rar = r < 12 ? 'trash' : r < 62 ? 'common' : r < 85 ? 'rare' : r < 97 ? 'epic' : 'legend'; return pick(FISH.filter((f) => f[2] === rar)); };

// ── 21 點 ──
let bj = null;
const RANKS = ['A', '2', '3', '4', '5', '6', '7', '8', '9', '10', 'J', 'Q', 'K'], SUITS = ['♠', '♥', '♦', '♣'];
const draw = () => ({ rank: pick(RANKS), suit: pick(SUITS) });
const val = (hand) => {
  let t = 0, a = 0;
  hand.forEach((c) => { if (c.rank === 'A') { a++; t += 11; } else t += ['J', 'Q', 'K'].includes(c.rank) ? 10 : +c.rank; });
  while (t > 21 && a) { t -= 10; a--; }
  return t;
};
const bjView = () => ({
  player: bj.p, dealer: bj.over ? bj.d : [bj.d[0], null], pv: val(bj.p), dv: bj.over ? val(bj.d) : null, over: bj.over, result: bj.result, payout: bj.payout,
});
const bjEnd = (result) => {
  bj.over = true; bj.result = result;
  bj.payout = { blackjack: 30, win: 20, dealer_bust: 20, push: 0, lose: 0, bust: 0 }[result];
  W.coins += bj.payout;
  if (result === 'push') W.tickets += 1;
};

// ── 踩地雷 ──
let mines = null;
const minesMult = (k) => { let m = 1; for (let i = 0; i < k; i++) m *= (20 - i) / (17 - i); return Math.round(m * 100 * 0.95) / 100; };
const minesView = () => {
  const k = mines.opened.length;
  return {
    cells: 20, cols: 5, mines: 3, opened: mines.opened, k, mult: k ? minesMult(k) : 1, cash: Math.floor(50 * (k ? minesMult(k) : 1)),
    next_mult: minesMult(k + 1), next_cash: Math.floor(50 * minesMult(k + 1)), over: mines.over, exploded: mines.exploded,
    mine_cells: mines.over ? mines.mc : [], payout: mines.payout || 0,
  };
};

// ── 樂透 ──
const lot = { period: 12, pot: 8400, bets: 120, players: 9, mine: 0, next_draw: Math.floor(Date.now() / 1000) + 3 * 3600 + 125,
  last: { period: 11, pot: 6100, bets: 98, players: 8, winners: [{ rank: 1, name: '小明', prize: 3000 }, { rank: 2, name: '阿花', prize: 1500 }], carry: 1600 } };
const lotView = () => ({ period: lot.period, pot: lot.pot, bets: lot.bets, players: lot.players, mine: lot.mine, chance: lot.bets ? lot.mine / lot.bets : 0,
  next_draw: lot.next_draw, prize: Math.floor(lot.pot * 0.5), rules: '每注 1 張券，彩金累積，開獎時依注數抽出得主。', last: lot.last });

// ── 大富翁 ──
const COLORS = ['#e0443e', '#e8863a', '#e8c53a', '#5fb85f', '#3aa6c4', '#4a6ee0', '#a35fd0'];
const PN = '峽谷 河道 野區 龍坑 男爵 高地 水晶 虛空 星界 雙城 祖安 皮城 艾歐 諾克 德瑪 弗雷 班德 恕瑞 暗影 符文 海克 水路 影島 熔爐'.split(' ');
const SPECIAL = { 0: ['起點', 'go'], 8: ['停權區', 'jail'], 16: ['免費停車', 'free'], 24: ['進停權區', 'gotojail'], 4: ['機會', 'chance'], 12: ['機會', 'chance'], 20: ['機會', 'chance'], 28: ['機會', 'chance'], 6: ['所得稅', 'tax'], 18: ['傳送門', 'portal'] };
const BOARD = Array.from({ length: 32 }, (_, i) => {
  const base = { i, name: '', kind: 'prop', color: null, price: 0, rent: 0, owner: null, owner_color: null, level: 0 };
  if (SPECIAL[i]) return { ...base, name: SPECIAL[i][0], kind: SPECIAL[i][1] };
  const price = 60 + i * 12;
  return { ...base, name: PN[i % PN.length], color: COLORS[(i >> 2) % 7], price, rent: Math.round(price / 8) };
});
const bots = [{ uid: 'b1', name: '小明', pos: 3, cash: 900, color: '#ff6b6b', initial: '明' }, { uid: 'b2', name: '阿花', pos: 11, cash: 1200, color: '#4dd0e1', initial: '花' },
  { uid: 'b3', name: 'Tom', pos: 20, cash: 700, color: '#ffd24d', initial: 'T' }];
const mono = { joined: false, me: { uid: 'me', cash: 1500, pos: 0, jail: false, rolled: 0 }, pending: null, level: {} };
const monoView = () => {
  const players = [...(mono.joined ? [{ uid: 'me', name: '測試員', pos: mono.me.pos, cash: mono.me.cash, nw: 0, jail: mono.me.jail, color: '#7c9cff', initial: '我' }] : []), ...bots]
    .map((p) => ({ ...p, nw: p.cash + BOARD.filter((t) => t.owner === p.name).reduce((a, t) => a + t.price, 0) }));
  const standings = [...players].sort((a, b) => b.nw - a.nw).map((p) => ({ uid: p.uid, name: p.name, nw: p.nw }));
  const me = players.find((p) => p.uid === 'me');
  return {
    joined: mono.joined, season: { id: 3, name: '第三賽季・峽谷爭霸', end: Math.floor(Date.now() / 1000) + 5 * 86400, days_left: 5 },
    board: BOARD.map((t) => ({ ...t })), players, standings,
    me: mono.joined ? { uid: 'me', cash: mono.me.cash, nw: me.nw, pos: mono.me.pos, jail: mono.me.jail, rank: standings.findIndex((s) => s.uid === 'me') + 1, rolled: mono.me.rolled } : null,
    pending: mono.pending, champions: [{ season: '第二賽季', name: '阿花', nw: 4210 }], board_png: null,
  };
};

// ── 商店 ──
const ITEMS = [
  ['title_newbie', 'title', '新手冒險家', '剛入門的小可愛', 0], ['title_fisher', 'title', '釣魚達人', '甩竿如神', 300], ['title_king', 'title', '拉霸之王', '手氣爆棚', 800],
  ['frame_bronze', 'frame', '青銅外框', '低調的古銅色', 200], ['frame_silver', 'frame', '白銀外框', '閃亮的銀色', 500], ['frame_gold', 'frame', '黃金外框', '尊榮金邊', 1000],
  ['frame_rainbow', 'frame', '彩虹外框', '七彩流動', 2000], ['frame_galaxy', 'frame', '銀河外框', '星空旋轉', 3000],
  ['bg_rift', 'bg', '峽谷綠', '召喚峽谷的草地', 400], ['bg_void', 'bg', '虛空紫', '神祕的虛空', 600], ['bg_arcane', 'bg', '雙城藍粉', '奧術風格', 900],
  ['bg_lava', 'bg', '熔岩紅橘', '炙熱岩漿', 700], ['bg_ice', 'bg', '冰原青', '寒冰世界', 700],
];
const shopView = () => ITEMS.map(([id, kind, name, desc, price]) => ({ id, kind, name, desc, price, owned: W.owned.includes(id), equipped: W[kind === 'bg' ? 'bg' : kind] === id || (kind === 'title' && W.title === name) }));
const TITLE_BY_ID = Object.fromEntries(ITEMS.filter((i) => i[1] === 'title').map((i) => [i[0], i[2]]));

const wal = () => ({ wallet: wallet() });
const T = (ms) => sleep(ms + rnd(250));

export async function mockFun(op, a = {}) {
  await T(op === 'link_add' ? 1200 : 220);
  switch (op) {
    case 'home': return wal();
    case 'daily': { if (W.daily) throw new Error('今天已經簽到過了'); W.daily = true; W.tickets += 2; W.coins += 100; return { ...wal(), got: 2 }; }
    case 'link_add': {
      const m = /^(.+)#(.+)$/.exec(a.rid || '');
      if (!m) throw new Error('格式要是 名字#tag');
      if (m[1] === 'bad') throw new Error('找不到這個帳號');
      W.accounts[a.game].push({ name: m[1], tag: m[2] });
      return wal();
    }
    case 'link_remove': { const [n, t] = a.rid.split('#'); W.accounts[a.game] = W.accounts[a.game].filter((x) => !(x.name === n && x.tag === t)); return wal(); }

    case 'gacha_pull': {
      need(a.count);
      const results = Array.from({ length: a.count }, () => {
        const rar = rollRarity(), c = pick(CARDS.filter((x) => x.rarity === rar));
        const isNew = !owned[c.id]; owned[c.id] = (owned[c.id] || 0) + 1;
        return { id: c.id, name: c.name, rarity: c.rarity, icon: null, new: isNew };
      });
      return { results, wallet: wallet() };
    }
    case 'gacha_dex': {
      const cards = CARDS.filter((c) => owned[c.id]).map((c) => ({ ...c, count: owned[c.id], icon: null }));
      const by = { N: 0, R: 0, SR: 0, SSR: 0 };
      cards.forEach((c) => { by[c.rarity]++; });
      return { owned: cards.length, total: CARDS.length, by_rarity: by, cards, dupes_value: cards.reduce((s, c) => s + (c.count - 1) * SELL[c.rarity], 0) };
    }
    case 'gacha_sell': {
      const sold = [];
      let total = 0;
      CARDS.forEach((c) => { if (owned[c.id] > 1) { const n = owned[c.id] - 1, coins = n * SELL[c.rarity]; owned[c.id] = 1; total += coins; sold.push({ name: c.name, rarity: c.rarity, count: n, coins }); } });
      W.coins += total;
      return { sold, total, wallet: wallet() };
    }

    case 'fish_cast': {
      const catches = Array.from({ length: a.count }, () => {
        const f = rollFish(), weight = Math.round(f[3] * (0.5 + Math.random()) * 10) / 10;
        const value = Math.round(FVAL[f[2]] * (0.7 + Math.random() * 0.6));
        const record = f[2] !== 'trash' && weight > (records[f[1]] || 0) && Math.random() < 0.5;
        if (record) records[f[1]] = weight;
        best[f[1]] = Math.max(best[f[1]] || 0, weight);
        const it = bag.find((b) => b.name === f[1]);
        if (it) { it.count++; it.value += value; it.weight = Math.max(it.weight, weight); } else bag.push({ emoji: f[0], name: f[1], rarity: f[2], count: 1, weight, value });
        return { id: FISH.indexOf(f), emoji: f[0], name: f[1], rarity: f[2], rarity_name: FNAME[f[2]], weight, value, record };
      });
      return { catches, total: catches.reduce((s, c) => s + c.value, 0), wallet: wallet() };
    }
    case 'fish_bag': return { items: bag.map((b) => ({ ...b })), total: bag.reduce((s, b) => s + b.value, 0) };
    case 'fish_sell': { const count = bag.reduce((s, b) => s + b.count, 0), total = bag.reduce((s, b) => s + b.value, 0); bag.length = 0; W.coins += total; return { count, total, wallet: wallet() }; }
    case 'fish_dex': return { caught: Object.keys(best).length, total: FISH.length, species: FISH.map((f) => ({ emoji: f[0], name: f[1], rarity: f[2], rarity_name: FNAME[f[2]], caught: best[f[1]] ? 1 : 0, best: best[f[1]] || 0 })) };
    case 'fish_records': return { rows: Object.entries(records).map(([n, w]) => ({ emoji: FISH.find((f) => f[1] === n)[0], name: n, rarity: FISH.find((f) => f[1] === n)[2], weight: w, user: '測試員' }))
      .concat([{ emoji: '🐋', name: '藍鯨幼體', rarity: 'legend', weight: 412.5, user: '小明' }]) };

    case 'bj_start': {
      if (bj && !bj.over) return { game: bjView(), wallet: wallet() };
      need(1);
      bj = { p: [draw(), draw()], d: [draw(), draw()], over: false, result: null, payout: 0 };
      if (val(bj.p) === 21) bjEnd('blackjack');
      return { game: bjView(), wallet: wallet() };
    }
    case 'bj_hit': {
      if (!bj || bj.over) throw new Error('沒有進行中的牌局');
      bj.p.push(draw());
      if (val(bj.p) > 21) bjEnd('bust');
      return { game: bjView(), wallet: wallet() };
    }
    case 'bj_stand': {
      if (!bj || bj.over) throw new Error('沒有進行中的牌局');
      while (val(bj.d) < 17) bj.d.push(draw());
      const pv = val(bj.p), dv = val(bj.d);
      bjEnd(dv > 21 ? 'dealer_bust' : pv > dv ? 'win' : pv === dv ? 'push' : 'lose');
      return { game: bjView(), wallet: wallet() };
    }

    case 'mines_start': {
      if (mines && !mines.over) return { game: minesView(), wallet: wallet() };
      need(1);
      const mc = [];
      while (mc.length < 3) { const c = rnd(20); if (!mc.includes(c)) mc.push(c); }
      mines = { mc, opened: [], over: false, exploded: null, payout: 0 };
      return { game: minesView(), wallet: wallet() };
    }
    case 'mines_reveal': {
      if (!mines || mines.over) throw new Error('沒有進行中的遊戲');
      if (mines.mc.includes(a.i)) { mines.over = true; mines.exploded = a.i; } else if (!mines.opened.includes(a.i)) mines.opened.push(a.i);
      return { game: minesView(), wallet: wallet() };
    }
    case 'mines_cash': {
      if (!mines || mines.over || !mines.opened.length) throw new Error('還不能收手');
      const cash = minesView().cash;
      mines.over = true; mines.payout = cash; W.coins += cash;
      return { game: minesView(), wallet: wallet() };
    }

    case 'slots_spin': {
      need(1);
      const S = ['🍒', '🍋', '🔔', '⭐', '💎', '👑', '7️⃣'];
      const jack = Math.random() < 0.08;
      const reels = jack ? Array(3).fill(pick(['💎', '👑', '🍒'])) : [pick(S), pick(S), pick(S)];
      const table = [{ sym: '👑', pay: 1000 }, { sym: '💎', pay: 500 }, { sym: '7️⃣', pay: 200 }, { sym: '⭐', pay: 80 }, { sym: '🔔', pay: 40 }, { sym: '🍋', pay: 20 }, { sym: '🍒', pay: 10 }];
      const pair = { sym: '🍒', pay: 5 };
      let payout = 0;
      if (reels[0] === reels[1] && reels[1] === reels[2]) payout = table.find((t) => t.sym === reels[0]).pay;
      else if (reels.filter((x) => x === '🍒').length >= 2) payout = pair.pay;
      W.coins += payout;
      return { reels, payout, table, pair, wallet: wallet() };
    }
    case 'flip': {
      need(1);
      const f = Math.random() < 0.5 ? 'heads' : 'tails', win = f === a.guess, payout = win ? 20 : 0;
      W.coins += payout;
      return { flip: f, win, payout, wallet: wallet() };
    }

    case 'lottery': return lotView();
    case 'lottery_buy': {
      need(a.count);
      lot.mine += a.count; lot.bets += a.count; lot.pot += a.count * 10;
      return { ...lotView(), wallet: wallet() };
    }

    case 'mono': return monoView();
    case 'mono_join': mono.joined = true; return monoView();
    case 'mono_roll': {
      if (!mono.joined) throw new Error('還沒加入大富翁');
      need(1);
      if (mono.pending) throw new Error('先決定要不要買');
      const dice = [1 + rnd(6), 1 + rnd(6)];
      if (mono.me.jail && dice[0] === dice[1]) { mono.me.jail = false; }
      const from = mono.me.pos, to = (from + dice[0] + dice[1]) % BOARD.length, events = [];
      mono.me.pos = to;
      if (to < from) { mono.me.cash += 200; events.push('經過起點，領 $200'); }
      const t = BOARD[to];
      events.push(`走到「${t.name}」`);
      if (t.kind === 'prop') {
        if (!t.owner) { mono.pending = { type: 'buy', tile: to, cost: t.price, name: t.name }; events.push(`沒有地主，可以花 $${t.price} 買下`); }
        else if (t.owner === '測試員') { if (t.level < 3) { mono.pending = { type: 'up', tile: to, cost: Math.round(t.price / 2), name: t.name }; events.push('這是你的地，可以升級'); } }
        else { mono.me.cash -= t.rent; events.push(`付給 ${t.owner} 租金 $${t.rent}`); }
      } else if (t.kind === 'tax') { mono.me.cash -= 100; events.push('繳稅 $100'); }
      else if (t.kind === 'gotojail' || t.kind === 'jail') { mono.me.jail = true; events.push('被關進停權區'); }
      else if (t.kind === 'chance') { const g = (rnd(5) - 1) * 50; mono.me.cash += g; events.push(g >= 0 ? `機會：撿到 $${g}` : `機會：罰款 $${-g}`); }
      mono.me.rolled++;
      return { ...monoView(), roll: { dice, from, to, events }, wallet: wallet() };
    }
    case 'mono_decide': {
      const p = mono.pending;
      if (!p) throw new Error('沒有待決定的事');
      let msg = '你決定不買';
      if (a.action !== 'skip') {
        if (mono.me.cash < p.cost) throw new Error('現金不夠');
        mono.me.cash -= p.cost;
        const t = BOARD[p.tile];
        if (p.type === 'buy') { t.owner = '測試員'; t.owner_color = '#7c9cff'; msg = `買下了「${t.name}」`; } else { t.level++; t.rent = Math.round(t.rent * 1.8); msg = `「${t.name}」升到 Lv${t.level}`; }
      }
      mono.pending = null;
      return { ...monoView(), msg };
    }
    case 'mono_bail': {
      if (!mono.me.jail) throw new Error('你沒有被停權');
      mono.me.cash -= 50; mono.me.jail = false;
      return { ...monoView(), msg: '付了 $50，出獄' };
    }

    case 'shop': return { items: shopView(), wallet: wallet() };
    case 'shop_buy': {
      const it = ITEMS.find((i) => i[0] === a.item);
      if (!it) throw new Error('沒有這個商品');
      if (W.owned.includes(a.item)) throw new Error('你已經有了');
      if (W.coins < it[4]) throw new Error('代幣不夠');
      W.coins -= it[4]; W.owned.push(a.item);
      return { items: shopView(), wallet: wallet() };
    }
    case 'equip': {
      if (a.item === null || a.item === undefined) { if (a.kind === 'title') W.title = null; else W[a.kind] = null; return { items: shopView(), wallet: wallet() }; }
      const it = ITEMS.find((i) => i[0] === a.item);
      if (!it || !W.owned.includes(a.item)) throw new Error('還沒擁有');
      if (it[1] === 'title') W.title = it[2]; else W[it[1]] = a.item;
      return { items: shopView(), wallet: wallet() };
    }

    case 'profile': {
      const mine = !a.uid;
      const name = mine ? '測試員' : (['小明', '阿花', 'Tom'][+String(a.uid).replace(/\D/g, '') % 3] || '路人');
      return {
        uid: mine ? 'me' : a.uid, name, avatar: null, title: mine ? W.title : '釣魚達人', frame: mine ? W.frame : 'frame_gold', bg: mine ? W.bg : 'bg_arcane',
        accounts: mine ? W.accounts : { lol: [{ name: '某位召喚師', tag: 'TW1' }], valo: [{ name: 'Viper', tag: '777' }] },
        stats: { tickets_total: mine ? 42 : 130, coins: mine ? W.coins : 5400, gacha: { owned: Object.keys(owned).length, total: CARDS.length, ssr: CARDS.filter((c) => c.rarity === 'SSR' && owned[c.id]).length },
          fish: { caught: Object.keys(best).length, total: FISH.length, best: Object.keys(best).length ? { emoji: '🐟', name: Object.keys(best)[0], weight: best[Object.keys(best)[0]] } : null },
          lottery_wins: mine ? 0 : 2, mono_titles: mine ? 0 : 1, mini_best: mine ? '21 點 +30' : '踩地雷 ×6.4' },
      };
    }
    case 'top': {
      const names = ['小明', '阿花', 'Tom', '測試員', '阿璃迷', '飛天小豬', '路過的人', '夜貓子'];
      const unit = { coins: '🪙', gacha: '張', fish: 'kg', mono: '$' }[a.kind] || '';
      return { rows: names.map((n, i) => ({ rank: i + 1, uid: `u${i}`, name: n, avatar: null, value: `${unit === '$' ? '$' : ''}${(9000 - i * 1100)}${unit === '$' ? '' : ` ${unit}`}`, sub: i % 2 ? '' : '本週 +120' })) };
    }
    default: throw new Error(`mock 不認得 op：${op}`);
  }
}

// 每 20 秒假裝推一次 grant 和一次 celebrate
export function startMock(h) {
  const KINDS = [
    { kind: 'ssr', user: '小明', text: '在轉蛋抽到 SSR「凱莎」', big: true },
    { kind: 'legend_fish', user: '阿花', text: '釣到傳說魚「藍鯨幼體」', big: false },
    { kind: 'jackpot', user: 'Tom', text: '拉霸連出三個 💎', big: true },
    { kind: 'max_ticket', user: '小明', text: '打完一場拿滿 4 張券', big: false },
  ];
  let n = 0;
  const tick = () => {
    const got = [1, 2, 3, 4][n % 4];
    W.tickets += got; W.earned += got;
    const g = { game: n % 2 ? 'Valorant' : 'LoL', queue: n % 2 ? '競技模式' : '單雙積分', won: n % 3 !== 0, who: n % 2 ? '婕提' : '阿璃', kda: n % 2 ? '21/9/5' : '12/2/8', score: 4 + got,
      parts: [['勝利', 2], ['KDA 高', 1], ['輸出領先', Math.max(0, got - 3)]].filter((p) => p[1] > 0), got, tickets: W.tickets, earned: W.earned };
    W.grants.unshift({ day: '今天', game: g.game, who: g.who, kda: g.kda, score: g.score, got });
    h.push('grant', g);
    setTimeout(() => h.celebrate({ type: 'celebrate', icon: null, ...KINDS[n % KINDS.length] }), 2500);
    n++;
  };
  setTimeout(tick, 6000);
  setInterval(tick, 20000);
}
