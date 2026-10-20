"""Valorant 對局中的即時數據：Overwolf GEP(Game Events Provider)→ Activity 的進階 TAB。

玩家 PC 上的 Overwolf General GameEvents Provider 會把每個 GEP info update 寫進自己的 log；
riot-gate 的 `owgep <對局 id> <since>` 只回這場(從 match_id 那行起)、時間戳 > since 的那些行：
  20261018225217951\t{"featureName":"match_info","categoryName":"match_info","key":"round_report","value":"{...}"}
value 多半是 JSON 字串再包一層。
  round_report  每回合結束送一次，只有本機玩家：damage / hit / headshot(沒打死的) / bodyshots / legshots /
                final_headshot(擊殺的爆頭) / damage_received / ability_damage；hit = 頭 + 身 + 腿 + 擊殺爆頭
  scoreboard_N  全場 10 人：player_id(= puuid)、K/D/A、錢、手上的槍、護甲、大招點數、存活；打完會被清成 null
  kill_feed     全場每次擊殺：attacker / victim(名字不含 #tag，隱藏名字的人是特務中文名)、武器、是否爆頭
"""
import json
from typing import Optional

FEED_KEEP = 12   # 擊殺紀錄留最近幾筆

# GEP 給的是 HUD 圖示名(內部代號)，對到遊戲裡的武器名；沒列到的就去掉前綴直接顯示
WEAPONS = {
    'classic': 'Classic', 'glock': 'Classic', 'luger': 'Ghost', 'sheriff': 'Sheriff', 'revolver': 'Sheriff',
    'slim': 'Shorty', 'autopistol': 'Frenzy', 'ninja': 'Stinger', 'vector': 'Stinger', 'mp5': 'Spectre',
    'pump': 'Bucky', 'persuader': 'Judge', 'autoshotgun': 'Judge', 'burst': 'Bulldog', 'dmr': 'Guardian',
    'ghost': 'Phantom', 'ar10a2': 'Phantom', 'carbine': 'Phantom', 'volcano': 'Vandal', 'ak': 'Vandal',
    'bolt': 'Marshal', 'operater': 'Operator', 'operator': 'Operator', 'doublesniper': 'Outlaw',
    'lmg': 'Ares', 'hmg': 'Odin', 'knife': '近戰', 'melee': '近戰', 'falleddamage': '摔死', 'fallendamage': '摔死',
}


def weapon_name(code: str) -> str:
    c = (code or '').strip()
    if not c:
        return ''
    low = c.lower()
    if low.startswith('tx_ui_'):     # 特務大招(TX_UI_Smonk_UltExpire)
        return '大招'
    for p in ('tx_hud_', 'tx_icon_'):
        if low.startswith(p):
            low = low[len(p):]
    for p in ('pistol_', 'rifles_', 'rifle_', 'assault_', 'smgs_', 'smg_', 'shotguns_', 'sniper_'):
        if low.startswith(p):
            low = low[len(p):]
    if low.endswith('_s'):
        low = low[:-2]
    if low in WEAPONS:
        return WEAPONS[low]
    if '_x' in low:       # 特務大招武器(TX_Hud_Wushu_X_Dagger)
        return '大招'
    return low.replace('_', ' ').title()


def _num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _value(v):
    if isinstance(v, str) and v[:1] in '{[':
        try:
            return json.loads(v)
        except ValueError:
            return None
    return v


def parse_lines(text: str) -> list[tuple[str, str, object]]:
    """gate 輸出 → [(時間戳, key, value)]；壞行略過。"""
    out = []
    for ln in (text or '').splitlines():
        ts, _, js = ln.partition('\t')
        if len(ts) != 17 or not ts.isdigit():
            continue
        try:
            d = json.loads(js)
        except ValueError:
            continue
        if isinstance(d, dict) and d.get('key'):
            out.append((ts, d['key'], _value(d.get('value'))))
    return out


class GepMatch:
    """一場對局的 GEP 狀態；feed() 吃 gate 的增量輸出，public() 給 Activity。"""

    def __init__(self, match_id: str):
        self.mid = match_id
        self._last = ''           # 看過的最後一個時間戳
        self._edge: set[str] = set()   # 那個時間戳已經吃過的行(同一毫秒常有好幾行，下次要重拿那一毫秒再去重)
        self.round = 0
        self.phase = ''
        self.side = ''            # attack / defense
        self.outcome = ''
        self.reports: dict[int, dict] = {}
        self.board: dict[str, dict] = {}     # puuid → 最後一次非空的 scoreboard
        self.feed: list[dict] = []
        self.started = False      # 進了第一回合(讀取畫面之後)

    @property
    def since(self) -> str:
        """下次跟 gate 要這個時間之後的行：退 1 毫秒，把最後那一毫秒還沒寫完的行也拿回來。"""
        return str(int(self._last) - 1) if self._last else '0'

    def feed_text(self, text: str) -> bool:
        changed = False
        for ln in (text or '').splitlines():
            ts = ln[:17]
            if ts < self._last or (ts == self._last and ln in self._edge):
                continue
            if ts > self._last:
                self._last, self._edge = ts, set()
            self._edge.add(ln)
            for _, key, v in parse_lines(ln):
                changed |= self._apply(key, v)
        return changed

    def _apply(self, key: str, v) -> bool:
        if key == 'round_number':
            n = int(_num(v))
            if n and n != self.round:
                self.round, self.started = n, True
                return True
        elif key == 'round_phase':
            if v and v != self.phase:
                self.phase = v
                return True
        elif key == 'team':
            if v and v != self.side:
                self.side = v
                return True
        elif key == 'match_outcome':
            if v:
                self.outcome = v
                return True
        elif key == 'round_report':
            if isinstance(v, dict) and self.round:
                self.reports[self.round] = {
                    'n': self.round, 'dmg': round(_num(v.get('damage'))), 'hit': int(_num(v.get('hit'))),
                    'hs': int(_num(v.get('headshot'))) + int(_num(v.get('final_headshot'))),
                    'bs': int(_num(v.get('bodyshots'))), 'ls': int(_num(v.get('legshots'))),
                    'recv': round(_num(v.get('damage_received'))), 'abil': round(_num(v.get('ability_damage'))),
                }
                return True
        elif key.startswith('scoreboard_'):
            if isinstance(v, dict) and v.get('player_id') and v.get('kills') is not None:
                pu = v['player_id'].lower()
                p = {
                    'name': v.get('name') or '', 'agent': v.get('character') or '', 'ally': bool(v.get('teammate')),
                    'k': int(_num(v.get('kills'))), 'd': int(_num(v.get('deaths'))), 'a': int(_num(v.get('assists'))),
                    'money': int(_num(v.get('money'))), 'weapon': weapon_name(v.get('weapon')),
                    'shield': int(_num(v.get('shield'))), 'ult': int(_num(v.get('ult_points'))),
                    'ultMax': int(_num(v.get('ult_max'))), 'alive': bool(v.get('alive')), 'spike': bool(v.get('spike')),
                    'me': bool(v.get('is_local')),
                }
                if self.board.get(pu) != p:
                    self.board[pu] = p
                    return True
        elif key == 'kill_feed':
            if isinstance(v, dict) and (v.get('attacker') or v.get('victim')):
                self.feed.append({
                    'n': self.round, 'a': v.get('attacker') or '', 'v': v.get('victim') or '',
                    'at': bool(v.get('is_attacker_teammate')), 'vt': bool(v.get('is_victim_teammate')),
                    'w': '大招' if v.get('ult') else weapon_name(v.get('weapon')), 'hs': bool(v.get('headshot')),
                })
                return True
        return False

    def _who(self, match: Optional[dict]) -> dict[tuple[str, bool], str]:
        """擊殺紀錄的名字 → puuid。kill_feed 名字不含 #tag；隱藏名字的人顯示特務名(中文)，用 GLZ 那份的特務名對。"""
        out: dict[tuple[str, bool], str] = {}
        for pu, p in self.board.items():
            base = p['name'].split(' #', 1)[0].strip()
            if base:
                out[(base, p['ally'])] = pu
        for t in (match or {}).get('teams') or []:
            for p in t.get('players') or []:
                if p.get('agent'):
                    out.setdefault((p['agent'], bool(t.get('mine'))), p.get('puuid', ''))
        return out

    def public(self, match: Optional[dict] = None) -> dict:
        who = self._who(match)
        hsk: dict[str, int] = {}
        for f in self.feed:
            pu = who.get((f['a'], f['at']))
            if pu and f['hs']:
                hsk[pu] = hsk.get(pu, 0) + 1
        # 敵方的錢、誰帶包遊戲內 TAB 看不到 → 不給(畫面不能比遊戲多給資訊，Riot 第三方工具規範)
        board = {pu: {**p, 'hsk': hsk.get(pu, 0)} if p['ally'] else {**p, 'hsk': hsk.get(pu, 0), 'money': None, 'spike': False}
                 for pu, p in self.board.items()}
        rounds = [self.reports[n] for n in sorted(self.reports)]
        tot = None
        if rounds:
            t = {k: sum(r[k] for r in rounds) for k in ('dmg', 'hit', 'hs', 'bs', 'ls', 'recv', 'abil')}
            t['rounds'] = len(rounds)
            t['adr'] = round(t['dmg'] / len(rounds))
            t['hsp'] = round(t['hs'] * 100 / t['hit']) if t['hit'] else None
            t['bsp'] = round(t['bs'] * 100 / t['hit']) if t['hit'] else None
            t['lsp'] = round(t['ls'] * 100 / t['hit']) if t['hit'] else None
            tot = t
        return {'mid': self.mid, 'started': self.started, 'round': self.round, 'phase': self.phase, 'side': self.side,
                'outcome': self.outcome, 'board': board, 'rounds': rounds, 'tot': tot, 'feed': self.feed[-FEED_KEEP:]}
