"""LoL 對局中的計時與聊天室(Activity)。純函式 + 一個增量讀取器，方便測試。

  大型野怪 / 兵營：Live Client Data API 的擊殺事件 + 遊戲時間 → 下次出現的遊戲秒數(重生時間見下面常數，2026 賽季)
  隨機單中的補包(Health Relic)：Porofessor 的 Overwolf log 裡的 jungle_camps 事件(riot-gate 的 `poro <since>`)
  聊天室：同一份 log 的 chat 事件(含系統訊息：擊殺、團滅、隨機單中的質變)
gate 輸出一行一筆：
  20261018002528557\tJ\t14:40\t{"jungle_camps":{"jungle_camp_3":"{\"name\":\"Relic Health\",\"alive\":true,...}"}}
  20261018002543999\tC\t14:56 台語艾瑪 (安比薩): 質變： 巨無霸雪球
"""
import json
import re
from typing import Optional

SR, ARAM = 11, 12
# 召喚峽谷(26.x：阿塔坎已移除、巴龍回到 20:00)。版本改了只要改這裡
DRAGON_FIRST, DRAGON_RESPAWN, ELDER_RESPAWN, SOUL_DRAGONS = 300, 300, 360, 4
GRUBS_SPAWN, HERALD_SPAWN = 480, 900
BARON_FIRST, BARON_RESPAWN = 1200, 360
INHIB_RESPAWN = {SR: 300, ARAM: 250}   # 隨機單中的 250 秒是 Porofessor 實際設的值
RELIC_RESPAWN = 90                     # 補包(Porofessor：Setting respawn for Relic Health in 90000ms)
CHAT_KEEP = 40

DRAGON_ZH = {'Fire': '炎龍', 'Earth': '山龍', 'Water': '海龍', 'Air': '風龍', 'Chemtech': '煉金龍',
             'Hextech': '海克斯龍', 'Elder': '遠古龍'}
CHAT_RE = re.compile(r'^(\d{1,3}):(\d\d) (.+?) \(([^()]+)\): (.*)$')
CLOCK_RE = re.compile(r'^(\d{1,3}):(\d\d) ?(.*)$')


def _secs(m: str, s: str) -> int:
    return int(m) * 60 + int(s)


def _teams(d: dict) -> tuple[str, dict]:
    """(我方隊伍 ORDER/CHAOS, {任何一種名字: 隊伍})。"""
    ap = d.get('activePlayer') or {}
    names, mine = {}, 'ORDER'
    for p in d.get('allPlayers') or []:
        for k in (p.get('riotIdGameName'), p.get('summonerName'), p.get('riotId'), p.get('championName')):
            if k:
                names.setdefault(k, p.get('team'))
        if p.get('riotId') and p.get('riotId') == ap.get('riotId'):
            mine = p.get('team') or mine
    return mine, names


def objective_timers(d: dict, feed: Optional['PoroFeed'] = None) -> list[dict]:
    """→ [{key, label, at, up, side, note}]。at = 下次出現的遊戲秒數(None = 不會再出現 / 不知道)，
    up = 現在場上有；side = 'ally' / 'enemy'(兵營是誰的、龍上次誰拿)。前端用 at - 現在遊戲時間倒數。"""
    gd = d.get('gameData') or {}
    now = gd.get('gameTime') or 0
    mp = gd.get('mapNumber')
    mine, team_of = _teams(d)
    evs = (d.get('events') or {}).get('Events') or []
    side = lambda name: None if not team_of.get(name) else ('ally' if team_of[name] == mine else 'enemy')  # noqa: E731
    out = []

    def timer(key, label, at, note='', who=None):
        out.append({'key': key, 'label': label, 'at': None if at is None else round(at), 'up': at is not None and at <= now,
                    'side': who, 'note': note})

    if mp == SR:
        drakes = [e for e in evs if e.get('EventName') == 'DragonKill']
        count = {'ally': 0, 'enemy': 0}
        for e in drakes:
            s = side(e.get('KillerName'))
            if s and e.get('DragonType') != 'Elder':
                count[s] += 1
        soul = max(count.values()) >= SOUL_DRAGONS
        note = f"我方 {count['ally']} · 敵方 {count['enemy']}"
        if not drakes:
            timer('dragon', '小龍', DRAGON_FIRST, note)
        else:
            last = drakes[-1]
            what = DRAGON_ZH.get(last.get('DragonType'), '小龍')
            timer('dragon', '遠古龍' if soul else '小龍', last['EventTime'] + (ELDER_RESPAWN if soul else DRAGON_RESPAWN),
                  f"{note} · 上一隻{what}{'(被搶)' if last.get('Stolen') == 'True' else ''}", side(last.get('KillerName')))
        grubs = [e for e in evs if e.get('EventName') == 'HordeKill']
        if now < HERALD_SPAWN and len(grubs) < 3:
            timer('grubs', '巢蟲', GRUBS_SPAWN, f'已打 {len(grubs)} / 3' if grubs else '')
        herald = [e for e in evs if e.get('EventName') == 'HeraldKill']
        if not herald and now < BARON_FIRST:
            timer('herald', '預示者', HERALD_SPAWN)
        barons = [e for e in evs if e.get('EventName') == 'BaronKill']
        if barons:
            timer('baron', '巴龍', barons[-1]['EventTime'] + BARON_RESPAWN, '', side(barons[-1].get('KillerName')))
        else:
            timer('baron', '巴龍', BARON_FIRST)

    # 兵營：被拆 → 倒數重生；InhibRespawned 之後就不列
    down: dict[str, float] = {}
    for e in evs:
        if e.get('EventName') == 'InhibKilled' and e.get('InhibKilled'):
            down[e['InhibKilled']] = e['EventTime']
        elif e.get('EventName') == 'InhibRespawned' and e.get('InhibRespawned'):
            down.pop(e['InhibRespawned'], None)
    # 名字兩種寫法：Live Client 給 Inhib_TOrder_L1_P1_196971597_0；Porofessor / 舊版是 Barracks_T1_L1
    lane = {'L1': '上路', 'C1': '中路', 'R1': '下路'}
    for name, t in sorted(down.items(), key=lambda x: x[1]):
        owner = 'ORDER' if ('_TOrder_' in name or '_T1_' in name) else 'CHAOS'
        old = re.match(r'^Barracks_T\d_([LCR]1)$', name)
        where = lane.get(old.group(1), '') if old and mp == SR else ''
        timer(f'inhib:{name}', f"{'我方' if owner == mine else '敵方'}{where}兵營", t + INHIB_RESPAWN.get(mp, 300), '',
              'ally' if owner == mine else 'enemy')

    if feed:
        for idx, r in sorted(feed.relics.items()):
            at = r['t'] if r['alive'] else r['t'] + RELIC_RESPAWN
            timer(f'relic:{idx}', f'補包 {idx + 1}', at)
    return out


class PoroFeed:
    """一場對局的 Porofessor log 增量讀取(跟 valo_gep.GepMatch 同一套 since / 同毫秒去重)。"""

    def __init__(self, start_ts: str):
        self._last = start_ts      # 這場開始前一點的牆上時間(17 位數)：之前的行屬於上一場
        self._edge: set[str] = set()
        self.relics: dict[int, dict] = {}   # 補包編號 → {alive, t(遊戲秒數)}
        self.chat: list[dict] = []

    @property
    def since(self) -> str:
        return str(int(self._last) - 1)

    def feed_text(self, text: str) -> bool:
        changed = False
        for ln in (text or '').splitlines():
            ts = ln[:17]
            if len(ts) != 17 or not ts.isdigit() or ts < self._last or (ts == self._last and ln in self._edge):
                continue
            if ts > self._last:
                self._last, self._edge = ts, set()
            self._edge.add(ln)
            parts = ln.split('\t', 3)
            if len(parts) == 4 and parts[1] == 'J':
                changed |= self._jungle(parts[2], parts[3])
            elif len(parts) >= 3 and parts[1] == 'C':
                changed |= self._chat(ln.split('\t', 2)[2])
        return changed

    def _jungle(self, clock: str, js: str) -> bool:
        m = re.match(r'^(\d{1,3}):(\d\d)$', clock)
        try:
            camps = (json.loads(js) or {}).get('jungle_camps') or {}
        except ValueError:
            return False
        if not m or not isinstance(camps, dict):
            return False
        changed = False
        for key, raw in camps.items():
            try:
                c = json.loads(raw) if isinstance(raw, str) else raw
            except ValueError:
                continue
            km = re.search(r'(\d+)$', key)
            if not isinstance(c, dict) or not km or 'Relic' not in (c.get('name') or ''):
                continue
            r = {'alive': bool(c.get('alive')), 't': _secs(m.group(1), m.group(2))}
            if self.relics.get(int(km.group(1))) != r:
                self.relics[int(km.group(1))] = r
                changed = True
        return changed

    def _chat(self, text: str) -> bool:
        m = CHAT_RE.match(text)
        if m:
            self.chat.append({'t': _secs(m.group(1), m.group(2)), 'who': m.group(3), 'champ': m.group(4), 'text': m.group(5)})
        else:
            c = CLOCK_RE.match(text)
            if not c:
                return False
            self.chat.append({'t': _secs(c.group(1), c.group(2)), 'sys': True, 'text': c.group(3)})
        del self.chat[:-CHAT_KEEP]
        return True
