"""賽季制大富翁的純邏輯(不 import discord)。

一個伺服器一張 32 格的棋盤，賽季 14 天；1 張券 = 擲 1 次 2d6，每次機率完全一樣(只有骰子與機會卡用 rng)。
資料全是可 JSON 化的 dict：
  guild = {'members': [uid...], 'season': season | None, 'history': [...], 'next_id': int, 'seq': int}
  season = {'id', 'start', 'end', 'seq', 'players': {uid字串: player}, 'owners': {格子字串: {'owner': uid, 'level': 0-3}}}
  player = {'cash', 'pos', 'jail', 'tries', 'rolled', 'turn', 'pending'}
uid 在 players 裡用字串當 key，對外一律傳 int。
"""
import random
from typing import Optional

N = 32
START_CASH = 1500
PASS_START = 200
SEASON_DAYS = 14
SEASON_SECONDS = SEASON_DAYS * 86400
BAIL = 50
JAIL_MAX_TRIES = 2          # 連續 2 次沒擲出對子，第 3 次自動付保釋金出來
RENT_MULT = (1, 3, 6, 10)
MAX_LEVEL = 3
DECISION_TIMEOUT = 300      # 秒：買地 / 升級的按鈕多久沒按當作不要
REWARDS = (3000, 2000, 1000)
PARTICIPATE_REWARD = 300
HISTORY_KEEP = 30

JAIL_POS = 8
REPORT_POS = 24

# 7 組地：(遊戲, [(名字, 價錢)...])，前段便宜後段貴
GROUPS = [
    ('lol', [('德瑪西亞', 60), ('諾克薩斯', 60), ('艾歐尼亞', 80)]),
    ('valo', [('劫境之地', 100), ('天漠之峽', 100), ('日落之城', 120)]),
    ('lol', [('皮爾托福', 140), ('祖安', 140), ('弗雷爾卓德', 160)]),
    ('valo', [('深海遺珠', 180), ('熱帶樂園', 180), ('蓮華古城', 200)]),
    ('lol', [('比爾吉沃特', 220), ('恕瑞瑪', 240), ('暗影島', 260)]),
    ('valo', [('遺落境地', 280), ('義境空島', 300), ('極地寒港', 320)]),
    ('lol', [('巨神峰', 360), ('虛空', 400)]),
]

_SEQ = ['start', 'L0', 'L0', 'chance', 'L0', 'tax0', 'L1', 'L1',
        'jail', 'L1', 'portal', 'L2', 'L2', 'chance', 'L2', 'L3',
        'rest', 'L3', 'L3', 'portal', 'chance', 'L4', 'L4', 'L4',
        'report', 'L5', 'tax1', 'L5', 'L5', 'chance', 'L6', 'L6']
TAXES = {'tax0': ('買裝稅', 100), 'tax1': ('段位維持費', 150)}


def _build_tiles() -> list[dict]:
    nxt = [0] * len(GROUPS)
    tiles = []
    for i, code in enumerate(_SEQ):
        t = {'i': i, 'type': code, 'name': '', 'price': 0, 'group': -1, 'game': ''}
        if code[0] == 'L' and code[1:].isdigit():
            g = int(code[1:])
            game, lst = GROUPS[g]
            name, price = lst[nxt[g]]
            nxt[g] += 1
            t.update(type='land', name=name, price=price, group=g, game=game,
                     rent=max(5, price // 10), upgrade=price // 2)
        elif code in TAXES:
            t.update(type='tax', name=TAXES[code][0], price=TAXES[code][1])
        else:
            t['name'] = {'start': '起點', 'jail': '停權中', 'rest': '泉水休息', 'report': '被檢舉',
                         'chance': '海克斯機會', 'portal': '傳送門'}[code]
        tiles.append(t)
    return tiles


TILES = _build_tiles()
PORTALS = [t['i'] for t in TILES if t['type'] == 'portal']
GROUP_TILES = [[t['i'] for t in TILES if t['group'] == g] for g in range(len(GROUPS))]
NAME_TO_TILE = {t['name']: t['i'] for t in TILES if t['type'] == 'land'}

# 機會卡：(種類, 數值, 文字)；每張被抽到的機率相同
CARDS = [
    ('each_give', 50, '五殺！每位玩家給你 50'),
    ('rel', -3, '被 gank 了，後退 3 格'),
    ('goto', 0, '回泉水補血：前往起點'),
    ('jail', 0, '被檢舉：直接停權'),
    ('pay', 150, '隊友掛機：付 150'),
    ('gain', 150, '拿到巴龍 buff：領 150'),
    ('gain', 200, '拿下大龍，推掉水晶：領 200'),
    ('rel', 3, '閃現開團成功：前進 3 格'),
    ('gain', 100, '對面 Jett 空大：撿到 100'),
    ('pay', 80, '忘記買裝備：付 80'),
    ('give_each', 30, '連敗被嘲諷，請每位玩家喝飲料：付每人 30'),
    ('gain', 120, '拆包成功：領 120'),
    ('goto_name', '日落之城', 'Omen 暗影步：前往 日落之城'),
    ('gain', 80, '被 Ace 隊友帶飛：領 80'),
    ('pay', 40, '手滑買了 Ward 沒插：付 40'),
]


def tile_name(i: int) -> str:
    return TILES[i]['name']


# ── 建立 / 查詢 ──
def new_player() -> dict:
    return {'cash': START_CASH, 'pos': 0, 'jail': False, 'tries': 0, 'rolled': 0, 'turn': 0, 'pending': None}


def new_season(roster, now: float, sid: int, seq: int = 0) -> dict:
    return {'id': sid, 'start': now, 'end': now + SEASON_SECONDS, 'seq': seq,
            'players': {str(u): new_player() for u in roster}, 'owners': {}}


def season_name(season: dict) -> str:
    return f"第 {season['id']} 季"


def new_guild() -> dict:
    return {'members': [], 'season': None, 'history': [], 'next_id': 1, 'seq': 0}


def start_season(g: dict, now: float) -> dict:
    """以名單開新的一季(名單保留，錢地位置重置)。"""
    seq = g['season']['seq'] if g.get('season') else g.get('seq', 0)
    g['season'] = new_season(g['members'], now, g['next_id'], seq)
    g['next_id'] += 1
    return g['season']


def join(g: dict, uid: int, now: float) -> str:
    """加入本季；回傳 'new_season' / 'joined' / 'already'。"""
    if uid not in g['members']:
        g['members'].append(uid)
    s = g.get('season')
    if not s:
        start_season(g, now)
        return 'new_season'
    if str(uid) in s['players']:
        return 'already'
    s['players'][str(uid)] = new_player()
    return 'joined'


def owned(season: dict, uid: int) -> list[int]:
    return sorted(int(t) for t, o in season['owners'].items() if o['owner'] == uid)


def invested(tile: int, level: int) -> int:
    t = TILES[tile]
    return t['price'] + t['upgrade'] * level


def net_worth(season: dict, uid: int) -> int:
    p = season['players'][str(uid)]
    return p['cash'] + sum(invested(int(t), o['level']) for t, o in season['owners'].items() if o['owner'] == uid)


def standings(season: dict, only_rolled: bool = False) -> list[tuple[int, int]]:
    """[(uid, 淨值)]，淨值高到低；同分比現金，再比 uid。"""
    rows = []
    for k, p in season['players'].items():
        if only_rolled and p['rolled'] <= 0:
            continue
        rows.append((int(k), net_worth(season, int(k)), p['cash']))
    rows.sort(key=lambda r: (-r[1], -r[2], r[0]))
    return [(u, nw) for u, nw, _ in rows]


def rent_for(season: dict, tile: int) -> int:
    """這格現在要付多少租金(同組全擁有且 0 級 → 基本租 ×2)。"""
    t = TILES[tile]
    o = season['owners'][str(tile)]
    rent = t['rent'] * RENT_MULT[o['level']]
    if o['level'] == 0:
        owners = {season['owners'].get(str(x), {}).get('owner') for x in GROUP_TILES[t['group']]}
        if owners == {o['owner']}:
            rent *= 2
    return rent


# ── 付錢 ──
def pay(season: dict, uid: int, amount: int, events: list, to: Optional[int] = None) -> int:
    """付錢給銀行或某玩家；錢不夠就把自己的地(含升級)以總投入的一半賣給銀行(最便宜的先賣)。
    賣光還不夠就付光現金，剩下的債免除。回傳實際付出的金額。"""
    p = season['players'][str(uid)]
    sold = []
    while p['cash'] < amount:
        lands = owned(season, uid)
        if not lands:
            break
        t = min(lands, key=lambda i: (TILES[i]['price'], i))
        lv = season['owners'][str(t)]['level']
        p['cash'] += invested(t, lv) // 2
        del season['owners'][str(t)]
        sold.append(TILES[t]['name'])
    if sold:
        events.append(f"💸 現金不夠，賣掉 {'、'.join(sold)}（以總投入的一半賣回銀行）")
    paid = min(p['cash'], amount)
    p['cash'] -= paid
    if to is not None and str(to) in season['players']:
        season['players'][str(to)]['cash'] += paid
    if paid < amount:
        events.append(f'🙏 已經賣光了，只付出 {paid}，剩下 {amount - paid} 的債免除')
    return paid


# ── 移動 ──
def _advance(p: dict, steps: int, events: list):
    old = p['pos']
    p['pos'] = (old + steps) % N
    if old + steps >= N:
        p['cash'] += PASS_START
        events.append(f'🏁 經過（或停在）起點，領 {PASS_START}')


def _send_jail(p: dict, events: list):
    p['pos'] = JAIL_POS
    p['jail'] = True
    p['tries'] = 0
    events.append('🚔 被送去停權！')


def _land(season: dict, uid: int, events: list, rng: random.Random, now: float, depth: int, card=None):
    p = season['players'][str(uid)]
    p.setdefault('_stops', []).append(p['pos'])          # 給 roll() 回傳每個停點(網頁照順序演)
    t = TILES[p['pos']]
    kind = t['type']
    events.append(f"📍 停在 **{t['name']}**")
    if kind == 'report':
        _send_jail(p, events)
    elif kind == 'tax':
        paid = pay(season, uid, t['price'], events)
        events.append(f"💰 繳了 {paid}")
    elif kind == 'portal':
        nxt = next((x for x in PORTALS if x > p['pos']), PORTALS[0])
        p['pos'] = nxt
        events.append(f"🌀 穿過傳送門，被傳到另一個傳送門（{TILES[nxt - 1]['name']} 旁）")
    elif kind == 'chance':
        if depth >= 2:
            events.append('（連續機會，這次不再抽）')
        else:
            _draw_card(season, uid, events, rng, now, depth + 1, card)
    elif kind == 'land':
        o = season['owners'].get(str(t['i']))
        if o is None:
            if p['cash'] >= t['price']:
                p['pending'] = {'type': 'buy', 'tile': t['i'], 'turn': p['turn'], 'at': now}
                events.append(f"🏷️ 無主的地，要 **{t['price']}** 買下嗎？")
            else:
                events.append(f"🏷️ 無主的地（{t['price']}），但你現金不夠買")
        elif o['owner'] == uid:
            if o['level'] >= MAX_LEVEL:
                events.append('⭐ 自己的地，已經滿級')
            elif p['cash'] >= t['upgrade']:
                p['pending'] = {'type': 'up', 'tile': t['i'], 'turn': p['turn'], 'at': now}
                events.append(f"🔧 自己的地（{o['level']} 級），要花 **{t['upgrade']}** 升到 {o['level'] + 1} 級嗎？")
            else:
                events.append(f"🔧 自己的地（{o['level']} 級），但現金不夠升級（要 {t['upgrade']}）")
        else:
            rent = rent_for(season, t['i'])
            paid = pay(season, uid, rent, events, to=o['owner'])
            events.append(f"🏠 這是 <@{o['owner']}> 的地（{o['level']} 級），付租金 **{paid}**"
                          + ('（同組全擁有，租金加倍）' if o['level'] == 0 and rent > t['rent'] else ''))


def apply_card(season: dict, uid: int, idx: int, events: list, rng: random.Random, now: float, depth: int = 1):
    """執行第 idx 張機會卡(測試也直接用這個)。"""
    p = season['players'][str(uid)]
    kind, val, text = CARDS[idx]
    events.append(f'🎴 海克斯機會：**{text}**')
    others = [int(k) for k in season['players'] if int(k) != uid]
    if kind == 'gain':
        p['cash'] += val
    elif kind == 'pay':
        pay(season, uid, val, events)
    elif kind == 'each_give':
        got = 0
        for o in others:
            got += pay(season, o, val, events, to=uid)
        events.append(f'💵 一共收到 {got}')
    elif kind == 'give_each':
        for o in others:
            pay(season, uid, val, events, to=o)
    elif kind == 'jail':
        _send_jail(p, events)
    elif kind == 'rel':
        if val >= 0:
            _advance(p, val, events)
        else:
            p['pos'] = (p['pos'] + val) % N
        _land(season, uid, events, rng, now, depth)
    elif kind in ('goto', 'goto_name'):
        dest = val if kind == 'goto' else NAME_TO_TILE[val]
        _advance(p, (dest - p['pos']) % N, events) if dest != p['pos'] else None
        _land(season, uid, events, rng, now, depth)


def _draw_card(season, uid, events, rng, now, depth, card=None):
    idx = rng.randrange(len(CARDS)) if card is None else card
    apply_card(season, uid, idx, events, rng, now, depth)


# ── 擲骰 ──
def roll(season: dict, uid: int, rng: random.Random, now: float, dice=None, card: Optional[int] = None) -> dict:
    """扣券之後呼叫：擲 2d6 並處理一回合。dice / card 可注入(測試用)。
    回傳 {'dice': (a,b), 'from', 'stops', 'to', 'events': [...], 'pending': dict|None, 'jail': bool}
    stops = 依序停過的格子：骰子走到的那格 → 機會卡移到的格子 → … → to(傳送門 / 送停權也算一站)。"""
    p = season['players'][str(uid)]
    season['seq'] += 1
    p['turn'] = season['seq']
    p['rolled'] += 1
    p['pending'] = None
    a, b = dice if dice else (rng.randint(1, 6), rng.randint(1, 6))
    total = a + b
    start = p['pos']
    events: list[str] = []
    move = True
    if p['jail']:
        if a == b:
            p['jail'], p['tries'] = False, 0
            events.append('🎉 擲出對子，出獄了！')
        elif p['tries'] >= JAIL_MAX_TRIES:
            paid = pay(season, uid, BAIL, events)
            p['jail'], p['tries'] = False, 0
            events.append(f'💸 連續沒出來，自動付 {paid} 出獄')
        else:
            p['tries'] += 1
            events.append(f"🔒 沒擲出對子，繼續待在停權（第 {p['tries']} 次，連續 {JAIL_MAX_TRIES} 次沒出來後第 {JAIL_MAX_TRIES + 1} 次自動付 {BAIL} 出獄）")
            move = False
    if move:
        _advance(p, total, events)
        _land(season, uid, events, rng, now, 0, card)
    stops = p.pop('_stops', [])
    if move and stops[-1] != p['pos']:
        stops.append(p['pos'])
    return {'dice': (a, b), 'from': start, 'stops': stops, 'to': p['pos'], 'events': events,
            'pending': p['pending'], 'jail': p['jail'], 'turn': p['turn']}


# ── 按鈕決定 ──
def pending_error(season: dict, uid: int, kind: str, tile: int, turn: int, now: float) -> Optional[str]:
    """檢查按鈕對應的待決定狀態還有沒有效；有效回 None，否則回原因。"""
    p = season['players'].get(str(uid))
    if not p:
        return '你不在這一季裡'
    if kind == 'bail':
        if not p['jail'] or p['turn'] != turn:
            return '這個按鈕已經失效了'
        return None
    pd = p.get('pending')
    if not pd or pd['turn'] != turn or pd['tile'] != tile or pd['type'] != kind:
        return '這個按鈕已經失效了'
    if now - pd['at'] > DECISION_TIMEOUT:
        p['pending'] = None
        return '超過 5 分鐘沒按，當作不買了'
    return None


def decline(season: dict, uid: int):
    season['players'][str(uid)]['pending'] = None


def buy(season: dict, uid: int, tile: int) -> tuple[bool, str]:
    p = season['players'][str(uid)]
    t = TILES[tile]
    if str(tile) in season['owners']:
        p['pending'] = None
        return False, '這塊地已經有主了'
    if p['cash'] < t['price']:
        return False, '現金不夠'
    p['cash'] -= t['price']
    season['owners'][str(tile)] = {'owner': uid, 'level': 0}
    p['pending'] = None
    return True, f"買下 **{t['name']}**（{t['price']}），剩 {p['cash']}"


def upgrade(season: dict, uid: int, tile: int) -> tuple[bool, str]:
    p = season['players'][str(uid)]
    t = TILES[tile]
    o = season['owners'].get(str(tile))
    if not o or o['owner'] != uid or o['level'] >= MAX_LEVEL:
        p['pending'] = None
        return False, '現在不能升級'
    if p['cash'] < t['upgrade']:
        return False, '現金不夠'
    p['cash'] -= t['upgrade']
    o['level'] += 1
    p['pending'] = None
    return True, f"**{t['name']}** 升到 {o['level']} 級（花 {t['upgrade']}），剩 {p['cash']}"


def bail(season: dict, uid: int) -> tuple[bool, str]:
    """付 50 立刻出獄(下一次擲骰正常移動)。"""
    p = season['players'][str(uid)]
    if not p['jail']:
        return False, '你沒有在停權'
    events: list[str] = []
    paid = pay(season, uid, BAIL, events)
    p['jail'], p['tries'] = False, 0
    return True, f'付了 {paid} 出獄，下一次擲骰正常移動' + ''.join('\n' + e for e in events)


# ── 賽季結算 ──
def compute_rewards(season: dict) -> dict[int, int]:
    """只算有擲過骰的人：淨值前 3 名 3000/2000/1000，其他 300。"""
    out = {}
    for rank, (uid, _) in enumerate(standings(season, only_rolled=True)):
        out[uid] = REWARDS[rank] if rank < len(REWARDS) else PARTICIPATE_REWARD
    return out


def settle_if_due(g: dict, now: float) -> Optional[dict]:
    """賽季到期就結算並自動開新的一季(名單保留)。回傳結算紀錄(沒到期回 None)。
    呼叫端要先存檔再發獎勵、公告，這樣重啟不會重複發獎。"""
    s = g.get('season')
    if not s or now < s['end']:
        return None
    rewards = compute_rewards(s)
    rank = [{'uid': u, 'nw': nw, 'reward': rewards.get(u, 0), 'rolled': s['players'][str(u)]['rolled']}
            for u, nw in standings(s)]
    ranked = [r for r in rank if r['reward']]
    rec = {'id': s['id'], 'start': s['start'], 'end': s['end'],
           'champion': ranked[0]['uid'] if ranked else None,
           'champion_nw': ranked[0]['nw'] if ranked else 0, 'ranking': rank}
    g['history'] = (g.get('history', []) + [rec])[-HISTORY_KEEP:]
    g['seq'] = s['seq']
    start_season(g, now)
    return rec
