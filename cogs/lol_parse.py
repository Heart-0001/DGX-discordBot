"""把 LoL 客戶端(LCU)與 Live Client Data API 的原始 JSON 整理成 Activity 要的格式。

全部是純函式，不碰網路，方便用錄下來的資料測試。
"""
import re
from typing import Optional

# 佇列名稱：客戶端給的 description 有時是空的，常見的自己補
QUEUE_NAMES = {
    400: '一般對戰(選秀)', 420: '單/雙積分', 430: '一般對戰(盲選)', 440: '彈性積分', 450: '隨機單中',
    490: '快速對戰', 700: '衝突', 900: '阿福快打', 1700: '鬥魂競技場', 1900: '阿福快打', 2400: '隨機單中：大混戰',
}
# 近期對局列表用的短名稱
QUEUE_SHORT = {
    400: '一般', 420: '單雙', 430: '一般', 440: '彈性', 450: 'ARAM', 490: '快速', 700: '衝突',
    900: '阿福', 1900: '阿福', 1700: '競技場', 2400: '大混戰',
}
RECENT_GAMES = 10
APEX = {'MASTER', 'GRANDMASTER', 'CHALLENGER'}

# 對局進行中的 gameflow phase
INGAME_PHASES = {'GameStart', 'InProgress', 'Reconnect'}
EOG_PHASES = {'WaitingForStats', 'PreEndOfGame', 'EndOfGame'}

_SPELL_RE = re.compile(r'GeneratedTip_SummonerSpell_(\w+?)_DisplayName')
_CHAMP_RE = re.compile(r'game_character_displayname_(\w+)')


def queue_name(qid: Optional[int], desc: str = '') -> str:
    if qid in QUEUE_NAMES:
        return QUEUE_NAMES[qid]
    return desc or (f'佇列 {qid}' if qid else '')


def riot_id(name: str, tag: str) -> str:
    return f'{name}#{tag}' if name else ''


# ── 選角 ──
def parse_champselect(sess: dict, my_puuid: str) -> dict:
    team = []
    for p in sess.get('myTeam') or []:
        hidden = p.get('nameVisibilityType') == 'HIDDEN' or not p.get('gameName')
        team.append({
            # 被隱藏的人連 puuid 都不帶，避免拿去查牌位/戰績(等於破解匿名)
            'puuid': None if hidden else (p.get('puuid') or None),
            'name': '' if hidden else p.get('gameName', ''),
            'tag': '' if hidden else p.get('tagLine', ''),
            'hidden': hidden,
            'championId': p.get('championId') or p.get('championPickIntent') or 0,
            'me': p.get('cellId') == sess.get('localPlayerCellId'),
        })
    timer = sess.get('timer') or {}
    return {
        'bench': [b.get('championId') for b in sess.get('benchChampions') or [] if b.get('championId')],
        'benchEnabled': bool(sess.get('benchEnabled')),
        'team': team,
        'enemyCount': len(sess.get('theirTeam') or []),
        'phase': timer.get('phase', ''),
        'rerolls': sess.get('rerollsRemaining'),
    }


def session_puuids(sess: dict) -> list[str]:
    """遊戲進行中的 gameflow session：雙方可見玩家的 puuid(被隱藏的人不會出現)。"""
    g = (sess or {}).get('gameData') or {}
    out = []
    for t in ('teamOne', 'teamTwo'):
        for p in g.get(t) or []:
            if p.get('puuid'):
                out.append(p['puuid'])
    return out


def session_champ_puuids(sess: dict) -> dict:
    """gameflow session → {'ORDER:154': puuid}。名字對不上時用「隊伍+英雄」找人(被隱藏的人 session 不帶 puuid)。"""
    g = (sess or {}).get('gameData') or {}
    out = {}
    for t, side in (('teamOne', 'ORDER'), ('teamTwo', 'CHAOS')):
        for p in g.get(t) or []:
            if p.get('puuid') and p.get('championId'):
                out[f"{side}:{p['championId']}"] = p['puuid']
    return out


# ── 遊戲中 ──
def _spell(s: dict, static: dict) -> dict:
    raw = s.get('rawDisplayName', '')
    m = _SPELL_RE.search(raw)
    key = m.group(1) if m else ''
    return {'key': key if key in static.get('spellKeys', {}) else '', 'name': s.get('displayName', '')}


def _champ_from_raw(p: dict, static: dict) -> dict:
    m = _CHAMP_RE.search(p.get('rawChampionName', ''))
    key = m.group(1) if m else ''
    cid = static.get('champKeyToId', {}).get(key.lower(), 0)
    return {'id': cid, 'key': key, 'name': p.get('championName', '')}


def _event_text(e: dict, names: dict, my_team: str = 'ORDER') -> Optional[dict]:
    """names: 召喚師名/riotId 遊戲名 → 英雄名(敵我)。回 None 表示不顯示。"""
    n = e.get('EventName')
    t = round(e.get('EventTime', 0))

    def who(x: str) -> str:
        if not x:
            return '?'
        if x.startswith('Turret_'):
            return '防禦塔'
        if x.startswith('Minion_') or x.startswith('SRU_'):
            return '小兵' if x.startswith('Minion_') else '野怪'
        return names.get(x, x)

    if n == 'ChampionKill':
        txt = f"{who(e.get('KillerName'))} 擊殺 {who(e.get('VictimName'))}"
        return {'t': t, 'kind': 'kill', 'text': txt, 'killer': who(e.get('KillerName')),
                'victim': who(e.get('VictimName'))}
    if n == 'Multikill':
        label = {2: '雙殺', 3: '三殺', 4: '四殺', 5: '五殺'}.get(e.get('KillStreak'), f"{e.get('KillStreak')}連殺")
        return {'t': t, 'kind': 'multikill', 'text': f"{who(e.get('KillerName'))} {label}"}
    if n == 'FirstBlood':
        return {'t': t, 'kind': 'first', 'text': f"{who(e.get('Recipient'))} 拿下首殺"}
    if n == 'FirstBrick':
        return {'t': t, 'kind': 'first', 'text': f"{who(e.get('KillerName'))} 推倒第一座塔"}
    if n == 'TurretKilled':
        owner = 'CHAOS' if '_TChaos_' in e.get('TurretKilled', '') else 'ORDER'
        side = '我方' if owner == my_team else '敵方'
        return {'t': t, 'kind': 'turret', 'text': f"{who(e.get('KillerName'))} 推倒防禦塔", 'side': side}
    if n == 'InhibKilled':
        return {'t': t, 'kind': 'inhib', 'text': f"{who(e.get('KillerName'))} 摧毀兵營"}
    if n == 'InhibRespawned':
        return {'t': t, 'kind': 'inhib', 'text': '兵營重生'}
    if n == 'Ace':
        return {'t': t, 'kind': 'ace', 'text': f"{who(e.get('Acer'))} 團滅對手(ACE)", 'team': e.get('AcingTeam')}
    if n in ('DragonKill', 'BaronKill', 'HeraldKill', 'HordeKill', 'AtakhanKill'):
        label = {'DragonKill': '小龍', 'BaronKill': '巴龍', 'HeraldKill': '預示者',
                 'HordeKill': '虛空幼蟲', 'AtakhanKill': '阿塔坎'}[n]
        return {'t': t, 'kind': 'objective', 'text': f"{who(e.get('KillerName'))} 擊殺{label}"}
    if n == 'GameStart':
        return {'t': t, 'kind': 'other', 'text': '遊戲開始'}
    if n == 'GameEnd':
        return {'t': t, 'kind': 'other', 'text': '遊戲結束'}
    return None


# ── 經濟與下一件大裝備預測 ──
LEGEND_MIN = 2000   # 合成完、這個價錢以上才算「大裝備」(鞋子另外排除)

# 裝備標籤 → 看英雄的哪個屬性(ad/ap/tank 是 Data Dragon 的 0~10 分)
_TAG_STAT = {
    'SpellDamage': 'ap', 'MagicPenetration': 'ap', 'Mana': 'ap',
    'Damage': 'ad', 'CriticalStrike': 'crit', 'AttackSpeed': 'as', 'ArmorPenetration': 'ad', 'OnHit': 'as',
    'LifeSteal': 'ad', 'Health': 'tank', 'Armor': 'tank', 'SpellBlock': 'tank',
}


def item_value(it: dict, items: dict) -> int:
    """一格裝備的價值：靜態資料的合成總價 × 數量；查不到用 Live API 給的 price。"""
    st = items.get(str(it.get('itemID') or it.get('id')))
    each = st['total'] if st else (it.get('price') or 0)
    return each * (it.get('count') or 1)


def _legendaries(items: dict, map_id: Optional[int]) -> list[str]:
    out = []
    for k, v in items.items():
        if int(k) >= 10000 or v.get('special') or not v.get('buy') or v.get('into'):
            continue   # 10000 以上是競技場/其他模式的複製品
        if v['total'] < LEGEND_MIN or 'Boots' in v['tags'] or 'Consumable' in v['tags']:
            continue
        if map_id and str(map_id) not in v.get('maps', []):
            continue
        out.append(k)
    return out


def _match(iid: str, owned: dict, items: dict) -> int:
    """iid 的合成樹裡，用掉手上已有的零件(會從 owned 扣掉)，回傳這些零件值多少錢。"""
    got = 0
    for c in (items.get(iid) or {}).get('from', []):
        if owned.get(c, 0) > 0:
            owned[c] -= 1
            got += items[c]['total']
        else:
            got += _match(c, owned, items)
    return got


def _evidence(iid: str, owned: dict, items: dict) -> tuple[int, float]:
    """(湊到的零件值多少錢, 直接合成表湊齊幾成)。後者讓便宜零件也算數：
    長劍+紅水晶 只值黑切的 25% 金錢，但黑切的兩個零件都齊了。"""
    owned = dict(owned)
    got, frac = 0, 0.0
    parts = (items.get(iid) or {}).get('from', [])
    for c in parts:
        if owned.get(c, 0) > 0:
            owned[c] -= 1
            got += items[c]['total']
            frac += 1
        else:
            g = _match(c, owned, items)
            got += g
            frac += g / items[c]['total'] if items.get(c, {}).get('total') else 0
    return got, (frac / len(parts) if parts else 0.0)


def _fit(champ_id, owned_ids: list[str], item: dict, static: dict) -> float:
    """這件裝備跟英雄(與他已買的東西)搭不搭，0~10 左右。"""
    info = static.get('champInfo', {}).get(str(champ_id)) or {}
    tags = info.get('tags', [])
    mm = 'Marksman' in tags
    stat = {'ad': info.get('ad', 5), 'ap': info.get('ap', 5), 'tank': info.get('tank', 5),
            'crit': info.get('ad', 5) * (1 if mm else .2), 'as': info.get('ad', 5) * (1 if mm else .5)}
    # 已經買的零件/裝備帶的標籤 → 代表他現在的出裝方向
    items = static.get('items', {})
    bought = {}
    for o in owned_ids:
        for t in (items.get(o) or {}).get('tags', []):
            bought[t] = bought.get(t, 0) + 1
    rel = [t for t in item['tags'] if t in _TAG_STAT]
    if not rel:
        return 2.0
    return sum(stat[_TAG_STAT[t]] + 2 * min(bought.get(t, 0), 2) for t in rel) / len(rel)


def _split_top(s: str) -> list[str]:
    """op.gg 回的文字格式 A(x,[B(..),C(..)],D) → 依最外層逗號切開。"""
    out, depth, cur = [], 0, ''
    for ch in s:
        if ch in '([':
            depth += 1
        elif ch in ')]':
            depth -= 1
        if ch == ',' and depth == 0:
            out.append(cur)
            cur = ''
        else:
            cur += ch
    if cur:
        out.append(cur)
    return out


_COMBO_RE = re.compile(r'\w+\(\[([\d,]*)\],(\d+)(?:,(\d+))?,([\d.]+)\)')
SLOT_LABEL = {'core_items': '核心裝', 'fourth_items': '第 4 件', 'fifth_items': '第 5 件',
              'sixth_items': '第 6 件', 'last_items': '最後一件'}
# op.gg 記的是「升級後」的裝備(買不到)，換回商店買的那件：終極魔劍←魔劍、熾天使←大天使、凜冬之臨←凜冬將至
UPGRADED_FROM = {'3042': '3004', '3040': '3003', '3121': '3119'}
BUILD_SLOTS = ('core_items', 'fourth_items', 'fifth_items', 'sixth_items', 'last_items')


def parse_opgg_build(text: str) -> Optional[dict]:
    """op.gg MCP lol_get_champion_analysis 的回傳 → {'core': [裝備id...], 'pop': {裝備id: 熱門分數}}。
    ARAM 專用的複製品 id(例如 126697)換回一般 id(6697)。"""
    m = re.search(r'class Data: ([\w,]+)', text)
    k = text.find('(Data(')
    if not m or k < 0:
        return None
    fields = m.group(1).split(',')
    body = text[k + len('(Data('):].rstrip(')')
    args = _split_top(body)
    def norm(i):
        i = str(int(i) % 10000) if int(i) >= 10000 else str(int(i))
        return UPGRADED_FROM.get(i, i)
    core, pop, stats = [], {}, {}
    for name, arg in zip(fields, args):
        if name not in BUILD_SLOTS:
            continue
        for ids, play, win, rate in _COMBO_RE.findall(arg):
            ids = [norm(x) for x in ids.split(',') if x]
            play, rate = int(play), float(rate)
            src = {'slot': SLOT_LABEL[name], 'rate': rate, 'play': play,
                   'wr': round(int(win) / play, 3) if win and play else None}
            if name == 'core_items':
                core = ids
                for n, x in enumerate(ids):
                    stats[x] = dict(src, slot=f'核心裝第 {n + 1} 件')
            else:
                for x in ids:
                    pop[x] = pop.get(x, 0) + rate * .8
                    if x not in stats or (not stats[x]['slot'].startswith('核心') and rate > stats[x]['rate']):
                        stats[x] = src
    for n, x in enumerate(core):   # 核心裝：越前面越先買
        pop[x] = pop.get(x, 0) + 1 - .1 * n
    # stats：每件裝備在 op.gg 出現的位置與數據(推薦出裝的「根據」)
    return {'core': core, 'pop': pop, 'stats': stats} if pop else None


def predict_next(champ_id, item_ids: list, static: dict, map_id: Optional[int] = None, n: int = 2,
                 build: Optional[dict] = None) -> list[dict]:
    """猜下一件大裝備。手上零件湊得越完整越優先；沒零件或同分時照 op.gg 常出裝(核心裝依序 >
    第 4~6 件選用率)；沒有出裝資料才退回「零件湊齊度 + 英雄類型」推算。"""
    items = static.get('items') or {}
    owned_ids = [str(i) for i in item_ids if i and str(i) in items]
    parts = {}
    for i in owned_ids:
        if items[i]['into']:   # 只算還能往上合的零件
            parts[i] = parts.get(i, 0) + 1
    pop = (build or {}).get('pop') or {}
    if not parts and not pop:
        return []
    have = set(owned_ids)
    scored = []
    for k in _legendaries(items, map_id):
        if k in have:
            continue
        got, frac = _evidence(k, parts, items) if parts else (0, 0.0)
        p = pop.get(k, 0)
        if not got and not p:
            continue
        total = items[k]['total']
        prog = got / total
        if pop:
            ev = (prog + frac) / 2   # 排序用；畫面上的進度條還是照金錢
            # 手上的零件最準：湊越多越優先；常出裝只用來決定「還沒開始湊」或同分時的順序。
            # 不在常出清單的裝備打 8 折(零件可能是要合別的)
            score = ev * 10 * (1 if p else .8) + p * 3
        else:
            score = prog * 10 + _fit(champ_id, owned_ids, items[k], static) * .5
        scored.append((score, {'id': int(k), 'name': items[k]['name'], 'progress': round(prog * 100),
                               'left': total - got, 'popular': p > 0}))
    scored.sort(key=lambda x: -x[0])
    return [x[1] for x in scored[:n]]


# ── 我方推薦出裝：op.gg 常出清單 × 敵方實際裝備 ──
AD_TAGS = {'Damage', 'CriticalStrike', 'AttackSpeed', 'ArmorPenetration', 'OnHit'}
AP_TAGS = {'SpellDamage', 'MagicPenetration'}
HEAL_TAGS = {'LifeSteal', 'SpellVamp'}
# 觸發「針對裝」的門檻(都會把實際數字寫進理由，讓人自己判斷)
DMG_SHARE = 60        # 敵方某種傷害佔 ≥ 60% → 對應防禦
DEF_SHARE = 20        # 敵方護甲/魔抗裝佔敵方裝備總額 ≥ 20% → 穿透
HEAL_SHARE = 10       # 敵方吸血/全能吸血裝佔 ≥ 10% → 重創
MIN_GOLD = 2500       # 上面兩項另外要求金額至少這麼多(開局一兩件小裝不算)
ITEM_GOLD_MIN = 3000  # 敵方攻擊裝少於這個金額(開局)時，傷害類型改看英雄屬性


def enemy_profile(raw_players: list, champ_ids: list, static: dict) -> dict:
    """敵方陣容：依實際裝備金額算物理/魔法比例、護甲、魔抗、吸血；開局沒裝備就用英雄屬性(Data Dragon)。"""
    items = static.get('items') or {}
    info = static.get('champInfo') or {}
    phys = magic = armor = mr = heal = total = 0
    healers = []
    for p in raw_players:
        h = 0
        for it in p.get('items') or []:
            st = items.get(str(it.get('itemID')))
            if not st:
                continue
            g, tags = st['total'] * (it.get('count') or 1), set(st['tags'])
            total += g
            phys += g if tags & AD_TAGS else 0
            magic += g if tags & AP_TAGS else 0
            armor += g if 'Armor' in tags else 0
            mr += g if 'SpellBlock' in tags else 0
            h += g if tags & HEAL_TAGS else 0
        heal += h
        if h:
            healers.append(p.get('championName', ''))
    if phys + magic >= ITEM_GOLD_MIN:
        src, ad, ap = '依敵方裝備', phys, magic
    else:
        src = '依英雄屬性'
        ad = sum((info.get(str(c)) or {}).get('ad', 5) for c in champ_ids)
        ap = sum((info.get(str(c)) or {}).get('ap', 5) for c in champ_ids)
    share = round(ad / (ad + ap) * 100) if ad + ap else 50
    pct = lambda g: round(g / total * 100) if total else 0
    return {'phys': share, 'magic': 100 - share, 'src': src, 'total': total,
            'armor': armor, 'armorPct': pct(armor), 'mr': mr, 'mrPct': pct(mr),
            'heal': heal, 'healPct': pct(heal), 'healers': healers}


def _build_type(build: dict, items: dict) -> str:
    """這隻英雄實際走哪種出裝：看 op.gg 常出清單的裝備標籤(依熱門度加權)。"""
    score = {'SpellDamage': 0.0, 'Damage': 0.0, 'Armor': 0.0}
    for k, w in (build.get('pop') or {}).items():
        tags = set((items.get(k) or {}).get('tags', []))
        if 'SpellDamage' in tags:
            score['SpellDamage'] += w
        elif tags & AD_TAGS:
            score['Damage'] += w
        elif tags & {'Armor', 'SpellBlock', 'Health'}:
            score['Armor'] += w
    return max(score, key=score.get)


def _gw_for(build: dict, static: dict, map_id) -> Optional[str]:
    """常出清單沒有重創裝時，依他常出裝的類型(魔法/物理/坦)挑一件最便宜的重創大裝。"""
    items = static.get('items') or {}
    want = _build_type(build, items)
    cands = [k for k in _legendaries(items, map_id) if items[k].get('gw') and want in items[k]['tags']]
    return min(cands, key=lambda k: items[k]['total']) if cands else None


def recommend(champ_id, item_ids: list, build: Optional[dict], prof: dict, static: dict,
              map_id: Optional[int] = None, n: int = 3) -> list[dict]:
    """推薦接下來的大裝備。只從這隻英雄 op.gg 常出清單挑；敵方陣容符合條件時把對應的裝往前排，
    每件都附「根據」(op.gg 數據 / 敵方實際數字)。"""
    if not build or not build.get('stats'):
        return []
    items = static.get('items') or {}
    owned = {str(i) for i in item_ids if i}
    parts = {}
    for i in item_ids:
        if i and str(i) in items and items[str(i)]['into']:
            parts[str(i)] = parts.get(str(i), 0) + 1
    legend = set(_legendaries(items, map_id))
    pool = [k for k in build['stats'] if k in legend and k not in owned]
    out = []

    heal_on = prof['healPct'] >= HEAL_SHARE and prof['heal'] >= MIN_GOLD
    heal_why = (f"敵方吸血裝 {prof['heal']:,} 金(佔 {prof['healPct']}%，"
                f"{'、'.join(prof['healers'][:3])}) → 重創")

    def why_counter(k):
        tags, rs, bonus = set(items[k]['tags']), [], 0.0
        if prof['phys'] >= DMG_SHARE and 'Armor' in tags:
            rs.append(f"敵方物理傷害 {prof['phys']}%({prof['src']}) → 護甲"); bonus += .6
        if prof['magic'] >= DMG_SHARE and 'SpellBlock' in tags:
            rs.append(f"敵方魔法傷害 {prof['magic']}%({prof['src']}) → 魔抗"); bonus += .6
        if prof['armorPct'] >= DEF_SHARE and prof['armor'] >= MIN_GOLD and 'ArmorPenetration' in tags:
            rs.append(f"敵方護甲裝 {prof['armor']:,} 金(佔 {prof['armorPct']}%) → 物理穿透"); bonus += .5
        if prof['mrPct'] >= DEF_SHARE and prof['mr'] >= MIN_GOLD and 'MagicPenetration' in tags:
            rs.append(f"敵方魔抗裝 {prof['mr']:,} 金(佔 {prof['mrPct']}%) → 魔法穿透"); bonus += .5
        if heal_on and items[k].get('gw'):
            rs.append(heal_why); bonus += .8
        return rs, bonus

    for k in pool:
        st = build['stats'][k]
        base = [f"op.gg ARAM {st['slot']}"
                + (f" · 選用 {round(st['rate'] * 100)}%" if not st['slot'].startswith('核心') else '')
                + (f" · 勝率 {round(st['wr'] * 100)}%" if st.get('wr') is not None else '')]
        rs, bonus = why_counter(k)
        ev = _evidence(k, parts, items)[1] if parts else 0
        if ev:
            base.append(f'已湊 {round(ev * 100)}% 零件')
        out.append((build['pop'].get(k, 0) + bonus + ev, {'id': int(k), 'name': items[k]['name'],
                                                          'reasons': base + rs, 'counter': bool(rs)}))
    # 敵方很會吸血、常出清單又沒重創裝 → 補一件(理由寫明是補的)
    if heal_on and not any(items.get(k, {}).get('gw') for k in owned | set(pool)):
        g = _gw_for(build, static, map_id)
        if g and g not in owned:
            kind = {'SpellDamage': '魔法', 'Damage': '物理', 'Armor': '坦克'}[_build_type(build, items)]
            out.append((1.3, {'id': int(g), 'name': items[g]['name'], 'counter': True,
                              'reasons': [f'常出清單沒有重創裝；他常出{kind}裝，挑最便宜的{kind}重創裝', heal_why]}))
    out.sort(key=lambda x: -x[0])
    return [x[1] for x in out[:n]]


def parse_live(d: dict, static: dict, name_to_puuid: dict, builds: Optional[dict] = None,
               champ_to_puuid: Optional[dict] = None) -> dict:
    ap = d.get('activePlayer') or {}
    my_rid = ap.get('riotId', '')
    players = d.get('allPlayers') or []
    me = next((p for p in players if p.get('riotId') == my_rid), None)
    my_team = me.get('team') if me else 'ORDER'

    out_players = []
    names = {}
    enemy_raw = [p for p in players if p.get('team') != my_team]
    prof = enemy_profile(enemy_raw, [_champ_from_raw(p, static)['id'] for p in enemy_raw], static)
    st_items = static.get('items') or {}
    map_id = (d.get('gameData') or {}).get('mapNumber')
    for p in players:
        champ = _champ_from_raw(p, static)
        ally = p.get('team') == my_team
        label = f"{champ['name']}({'我方' if ally else '敵方'})"
        for k in (p.get('riotIdGameName'), p.get('summonerName'), p.get('riotId'), champ['name']):
            if k:
                names.setdefault(k, label)
        rid = p.get('riotId') or ''
        hidden = not p.get('riotIdGameName') or rid in ('', '#')
        sc = p.get('scores') or {}
        # 前 6 格都是非消耗品 = 裝備滿了，不猜下一件
        full = sum(1 for i in p.get('items') or [] if i.get('slot', 9) < 6 and not i.get('consumable')) >= 6
        out_players.append({
            'team': p.get('team'), 'ally': ally, 'me': p is me,
            'name': '' if hidden else p.get('riotIdGameName', ''),
            'tag': '' if hidden else p.get('riotIdTagLine', ''),
            'hidden': hidden,
            'puuid': None if hidden else (name_to_puuid.get(rid.lower())
                                          or (champ_to_puuid or {}).get(f"{p.get('team')}:{champ['id']}")),
            'champ': champ,
            'skin': p.get('skinName') or '',
            'level': p.get('level', 0),
            'k': sc.get('kills', 0), 'd': sc.get('deaths', 0), 'a': sc.get('assists', 0),
            'cs': sc.get('creepScore', 0), 'ward': round(sc.get('wardScore', 0) or 0),
            'dead': bool(p.get('isDead')), 'respawn': round(p.get('respawnTimer') or 0),
            'items': sorted(({'id': i.get('itemID'), 'name': i.get('displayName', ''), 'slot': i.get('slot', 0),
                              'count': i.get('count', 1)} for i in p.get('items') or []),
                            key=lambda i: i['slot']),
            'spells': [_spell(s, static) for s in (p.get('summonerSpells') or {}).values()],
            'gold': sum(item_value(i, st_items) for i in p.get('items') or []),
            'full': full,
            'rec': recommend(champ['id'], [i.get('itemID') for i in p.get('items') or []],
                             (builds or {}).get(str(champ['id'])), prof, static, map_id)
            if p.get('team') == my_team else [],
            'next': [] if full else predict_next(champ['id'], [i.get('itemID') for i in p.get('items') or []],
                                                 static, map_id, build=(builds or {}).get(str(champ['id']))),
        })
    # 我方在前；同隊依原順序
    out_players.sort(key=lambda p: not p['ally'])

    self_info = None
    if ap.get('championStats'):
        cs = ap['championStats']
        self_info = {
            'hp': round(cs.get('currentHealth', 0)), 'maxHp': round(cs.get('maxHealth', 0)),
            'res': round(cs.get('resourceValue', 0)), 'maxRes': round(cs.get('resourceMax', 0)),
            'resType': cs.get('resourceType', ''), 'gold': round(ap.get('currentGold', 0)),
            'level': ap.get('level', 0),
        }

    evs = []
    for e in (d.get('events') or {}).get('Events') or []:
        x = _event_text(e, names, my_team)
        if x:
            evs.append(x)
    gd = d.get('gameData') or {}
    return {
        'time': round(gd.get('gameTime', 0)),
        'mode': gd.get('gameMode', ''),
        'players': out_players,
        'self': self_info,
        'kills': {'ally': sum(p['k'] for p in out_players if p['ally']),
                  'enemy': sum(p['k'] for p in out_players if not p['ally'])},
        'enemyProfile': prof,
        'gold': {'ally': sum(p['gold'] for p in out_players if p['ally']),
                 'enemy': sum(p['gold'] for p in out_players if not p['ally'])},
        'events': list(reversed(evs[-40:])),
    }


# ── 結算 ──
# 結算的戰鬥數據：欄位 → (結算資料的 key, 對戰紀錄詳情的 key)。拿不到的是 None(畫面顯示 —)。
# 護盾只有結算資料可能有(對戰紀錄詳情沒有)；控場沒有「次數」，只有秒數。
EOG_EXTRA = {
    'dmg': ('TOTAL_DAMAGE_DEALT_TO_CHAMPIONS', 'totalDamageDealtToChampions'),
    'dmgP': ('PHYSICAL_DAMAGE_DEALT_TO_CHAMPIONS', 'physicalDamageDealtToChampions'),
    'dmgM': ('MAGIC_DAMAGE_DEALT_TO_CHAMPIONS', 'magicDamageDealtToChampions'),
    'dmgT': ('TRUE_DAMAGE_DEALT_TO_CHAMPIONS', 'trueDamageDealtToChampions'),
    'taken': ('TOTAL_DAMAGE_TAKEN', 'totalDamageTaken'),
    'mitigated': ('TOTAL_DAMAGE_SELF_MITIGATED', 'damageSelfMitigated'),
    'heal': ('TOTAL_HEAL', 'totalHeal'),
    'healAlly': ('TOTAL_HEAL_ON_TEAMMATES', 'totalHealsOnTeammates'),
    'shield': ('TOTAL_DAMAGE_SHIELDED_ON_TEAMMATES', 'totalDamageShieldedOnTeammates'),
    'cc': ('TIME_CCING_OTHERS', 'timeCCingOthers'),
    'ccTotal': ('TOTAL_TIME_CROWD_CONTROL_DEALT', 'totalTimeCrowdControlDealt'),
}


def eog_missing(eog: dict) -> bool:
    return any(p.get(k) is None for t in eog.get('teams') or [] for p in t['players']
               for k in EOG_EXTRA if k != 'shield' and k != 'healAlly')


def enrich_eog(eog: dict, game: dict) -> dict:
    """結算資料缺的欄位，用 /lol-match-history/v1/games/{gameId} 的詳情補(依 puuid 對人)。"""
    ids = {pi.get('participantId'): (pi.get('player') or {}).get('puuid')
           for pi in (game or {}).get('participantIdentities') or []}
    parts = (game or {}).get('participants') or []
    by_puuid = {ids.get(p.get('participantId')): p.get('stats') or {} for p in parts}
    by_champ = {p.get('championId'): p.get('stats') or {} for p in parts}   # puuid 對不到時用英雄(同場不重複)
    for t in eog.get('teams') or []:
        for p in t['players']:
            st = by_puuid.get(p.get('puuid')) or by_champ.get((p.get('champ') or {}).get('id'))
            if not st:
                continue
            for k, (_, hk) in EOG_EXTRA.items():
                if p.get(k) is None and st.get(hk) is not None:
                    p[k] = st[hk]
    return eog


def parse_eog(d: dict, static: dict) -> dict:
    """結算畫面。隱藏名字的玩家在結算時客戶端會顯示真名，這裡照客戶端顯示。"""
    teams = []
    me_win = None
    for t in d.get('teams') or []:
        ps = []
        for p in t.get('players') or []:
            st = p.get('stats') or {}
            cid = p.get('championId', 0)
            augs = [st.get(f'PLAYER_AUGMENT_{i}') for i in range(1, 7)]
            ps.append({
                'puuid': p.get('puuid'), 'name': p.get('riotIdGameName', ''), 'tag': p.get('riotIdTagLine', ''),
                'me': bool(p.get('isLocalPlayer')),
                'champ': {'id': cid, 'key': static.get('champIdToKey', {}).get(str(cid), ''),
                          'name': p.get('championName', '')},
                'level': st.get('LEVEL', 0),
                'k': st.get('CHAMPIONS_KILLED', 0), 'd': st.get('NUM_DEATHS', 0), 'a': st.get('ASSISTS', 0),
                'cs': st.get('MINIONS_KILLED', 0) + st.get('NEUTRAL_MINIONS_KILLED', 0),
                'gold': st.get('GOLD_EARNED', 0),
                **{k: st.get(src) for k, (src, _) in EOG_EXTRA.items()},
                'items': [i for i in p.get('items') or [] if i],
                'augments': [a for a in augs if a],
                'spells': [p.get('spell1Id'), p.get('spell2Id')],
            })
            if p.get('isLocalPlayer'):
                me_win = bool(t.get('isWinningTeam'))
        teams.append({
            'ally': bool(t.get('isPlayerTeam')),
            'win': bool(t.get('isWinningTeam')),
            'kills': sum(p['k'] for p in ps),
            'players': ps,
        })
    teams.sort(key=lambda t: not t['ally'])
    return {
        'gameId': d.get('gameId'),
        'win': me_win,
        'length': d.get('gameLength', 0),
        'mode': d.get('gameMode', ''),   # 結算資料沒有 queueId，佇列由呼叫端用 gameflow session 補
        'teams': teams,
    }


# ── 玩家資料(等級、牌位、近期勝率) ──
def rank(q: Optional[dict], division_key: str = 'division') -> Optional[dict]:
    """客戶端(division)與 Riot API(rank)的牌位都整理成同一格式。"""
    if not q or not q.get('tier') or q.get('tier') in ('NONE', ''):
        return None
    return {'tier': q['tier'], 'division': '' if q['tier'] in APEX else q.get(division_key, ''),
            'lp': q.get('leaguePoints', 0), 'w': q.get('wins', 0), 'l': q.get('losses', 0)}


def parse_profile(summoner: Optional[dict], ranked: Optional[dict], history: Optional[dict],
                  queue_id: Optional[int]) -> dict:
    s = summoner or {}
    queues = {q.get('queueType'): q for q in (ranked or {}).get('queues') or []}
    games = ((history or {}).get('games') or {}).get('games') or []
    same = [g for g in games if queue_id and g.get('queueId') == queue_id]
    w = sum(1 for g in same if (g.get('participants') or [{}])[0].get('stats', {}).get('win'))
    recent_all = len(games)
    w_all = sum(1 for g in games if (g.get('participants') or [{}])[0].get('stats', {}).get('win'))
    return {
        'name': s.get('gameName', ''), 'tag': s.get('tagLine', ''),
        'level': s.get('summonerLevel', 0), 'icon': s.get('profileIconId', 0),
        'solo': rank(queues.get('RANKED_SOLO_5x5')),
        'flex': rank(queues.get('RANKED_FLEX_SR')),
        'recent': {'queueId': queue_id, 'w': w, 'l': len(same) - w} if same else None,
        'recentAll': {'w': w_all, 'l': recent_all - w_all} if recent_all else None,
        'private': s.get('privacy') == 'PRIVATE',
        'games': recent_games(games),
    }


def recent_games(games: list) -> list[dict]:
    """客戶端對戰紀錄 → 最近 N 場的精簡資料(新到舊)。participants[0] 就是這位玩家本人。"""
    out = []
    for g in sorted(games, key=lambda g: -(g.get('gameCreation') or 0))[:RECENT_GAMES]:
        p = (g.get('participants') or [{}])[0]
        st = p.get('stats') or {}
        qid = g.get('queueId')
        out.append({
            'c': p.get('championId', 0), 'w': bool(st.get('win')),
            'k': st.get('kills', 0), 'd': st.get('deaths', 0), 'a': st.get('assists', 0),
            'q': QUEUE_SHORT.get(qid) or g.get('gameMode', '') or '',
            't': (g.get('gameCreation') or 0) // 1000, 'dur': g.get('gameDuration', 0),
            'remake': (g.get('gameDuration') or 0) < 300,   # 5 分鐘內結束 = 重開
        })
    return out
