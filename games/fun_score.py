"""對局表現 → 表現分(0–8) → 券數。純函式，方便測試。

規則(2026-10 跟使用者定的)：打得好只會拿到「比較多張券」，每張券在各活動的機率都一樣；
打得再爛也保底 1 張。每天(台灣時間)從對局拿的券有上限，避免一天刷十幾場的人拉開太多。
"""
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from cogs.lol_parse import QUEUE_NAMES

TW = timezone(timedelta(hours=8))
MAX_SCORE = 8
DAILY_CAP = 12            # 每天從對局拿的券上限(簽到不算在內)
REMAKE_SEC = 300          # LoL 不到 5 分鐘的對局視為重開，不給券
LOL_SKIP_QUEUES = {0}     # 自訂對戰
VALO_SKIP_QUEUES = {'自訂', '訓練場', '基礎訓練'}


def today(now: Optional[datetime] = None) -> str:
    return (now or datetime.now(TW)).astimezone(TW).strftime('%Y-%m-%d')


def tickets_for(score: int) -> int:
    """0–2 → 1(保底)、3–5 → 2、6–7 → 3、8 → 4。"""
    if score >= 8:
        return 4
    if score >= 6:
        return 3
    if score >= 3:
        return 2
    return 1


def cap_grant(want: int, earned_today: int, cap: int = DAILY_CAP) -> int:
    return max(0, min(want, cap - earned_today))


def _tier(value: float, steps: list[tuple[float, int]]) -> int:
    """steps 由高到低：第一個 value >= 門檻的分數；都沒到回 0。"""
    for th, pts in steps:
        if value >= th:
            return pts
    return 0


def _place_score(place: Optional[int]) -> int:
    """名次制(競技場、死鬥)：第 1 名 8 分，之後每名少 1 分。"""
    if not place or place < 1:
        return 0
    return max(0, MAX_SCORE - (place - 1))


def _finish(parts: list[tuple[str, int]], **kw) -> dict:
    score = min(MAX_SCORE, sum(p for _, p in parts))
    return {'score': score, 'tickets': tickets_for(score), 'parts': parts, 'skip': None, **kw}


def _skip(reason: str, **kw) -> dict:
    return {'score': 0, 'tickets': 0, 'parts': [], 'skip': reason, **kw}


def lol_score(match: dict, puuid: str, champ_names: Optional[dict] = None) -> Optional[dict]:
    """Riot match-v5 → 表現分。找不到這位玩家回 None；重開、自訂回 skip。

    一般模式：勝利 +2；KDA((K+A)/D) ≥6 +3、≥4 +2、≥2.5 +1；參團率 ≥70% +2、≥55% +1；傷害佔全隊 ≥28% +2、≥22% +1。
    (加總超過 8 就算 8)
    競技場：看名次。
    """
    info = match.get('info') or {}
    ps = info.get('participants') or []
    me = next((p for p in ps if p.get('puuid') == puuid), None)
    if not me:
        return None
    qid = info.get('queueId')
    champ = (champ_names or {}).get(str(me.get('championId'))) or me.get('championName') or '?'
    k, d, a = me.get('kills', 0), me.get('deaths', 0), me.get('assists', 0)
    base = {'game': 'lol', 'matchId': (match.get('metadata') or {}).get('matchId', ''),
            'queue': QUEUE_NAMES.get(qid, f'佇列 {qid}'), 'won': bool(me.get('win')),
            'who': champ, 'kda': f'{k}/{d}/{a}'}
    if qid in LOL_SKIP_QUEUES:
        return _skip('自訂對戰不給券', **base)
    # gameDuration：有 gameEndTimestamp 的新版是秒，舊版是毫秒
    dur = info.get('gameDuration') or 0
    if not info.get('gameEndTimestamp'):
        dur //= 1000
    if dur < REMAKE_SEC or me.get('gameEndedInEarlySurrender'):
        return _skip('重開的對局不給券', **base)
    if qid == 1700 or info.get('gameMode') == 'CHERRY':
        place = me.get('placement')
        return _finish([(f'第 {place} 名', _place_score(place))], **base)
    team = [p for p in ps if p.get('teamId') == me.get('teamId')]
    team_k = sum(p.get('kills', 0) for p in team)
    team_dmg = sum(p.get('totalDamageDealtToChampions', 0) for p in team)
    kda = (k + a) / max(1, d)
    kp = (k + a) / team_k if team_k else 0
    share = me.get('totalDamageDealtToChampions', 0) / team_dmg if team_dmg else 0
    parts = []
    if me.get('win'):
        parts.append(('勝利', 2))
    parts.append((f'KDA {kda:.1f}', _tier(kda, [(6, 3), (4, 2), (2.5, 1)])))
    # 參團分兩段：輔助 KDA、傷害通常不高，靠參團拿分
    parts.append((f'參團 {kp:.0%}', _tier(kp, [(0.7, 2), (0.55, 1)])))
    parts.append((f'傷害佔比 {share:.0%}', _tier(share, [(0.28, 2), (0.22, 1)])))
    return _finish(parts, **base)


def riot_key(name: str, tag: str) -> str:
    return f'{name}#{tag}'.lower()


def lol_game_id(match_id: str) -> str:
    """API 的 matchId(TW2_402379706)→ 客戶端的 gameId(402379706)，兩邊去重用。"""
    return str(match_id).rsplit('_', 1)[-1]


def lcu_to_v5(game: dict) -> Optional[dict]:
    """客戶端對戰紀錄單場(/lol-match-history/v1/games/{id})→ match-v5 的形狀，給 lol_score 用。

    台服 API 查不到大混戰(2400)，客戶端什麼模式都有。puuid 換成 名字#tag(小寫)：客戶端的 puuid 跟 API 的不同，
    綁定的帳號是用名字對。"""
    ids = {pi.get('participantId'): pi.get('player') or {} for pi in (game or {}).get('participantIdentities') or []}
    ps = []
    for p in (game or {}).get('participants') or []:
        st = p.get('stats') or {}
        pl = ids.get(p.get('participantId')) or {}
        if not pl.get('gameName'):
            continue
        ps.append({'puuid': riot_key(pl['gameName'], pl.get('tagLine', '')), 'teamId': p.get('teamId'),
                   'championId': p.get('championId'), 'kills': st.get('kills', 0), 'deaths': st.get('deaths', 0),
                   'assists': st.get('assists', 0), 'win': bool(st.get('win')),
                   'totalDamageDealtToChampions': st.get('totalDamageDealtToChampions', 0),
                   'gameEndedInEarlySurrender': bool(st.get('gameEndedInEarlySurrender')),
                   'placement': st.get('subteamPlacement')})
    if not ps:
        return None
    return {'metadata': {'matchId': str(game.get('gameId', ''))},
            'info': {'queueId': game.get('queueId'), 'gameMode': game.get('gameMode', ''),
                     'gameDuration': game.get('gameDuration', 0), 'gameEndTimestamp': 1,   # 客戶端的長度是秒
                     'participants': ps}}


_PLACE_RE = re.compile(r'第\s*(\d+)\s*名')


def valo_score(s: dict) -> dict:
    """valo_parse.summarize / valo_live_parse.riot_summarize 的單場摘要 → 表現分。

    一般模式：勝利 +2(平手 +1)；ACS ≥300 +3、≥230 +2、≥180 +1(單回合模式改看 K/D ≥2 +3、≥1.4 +2、≥1 +1)；
    K/D ≥1.5 +2、≥1.1 +1；隊伍 MVP +1。死鬥：看名次。
    """
    k, d, a = s.get('k', 0), s.get('d', 0), s.get('a', 0)
    base = {'game': 'valo', 'matchId': s.get('matchId') or '', 'queue': s.get('queue') or '?',
            'won': bool(s.get('won')), 'who': s.get('agent') or '?', 'kda': f'{k}/{d}/{a}'}
    if s.get('queue') in VALO_SKIP_QUEUES:
        return _skip('自訂 / 練習不給券', **base)
    if s.get('ffa'):
        m = _PLACE_RE.search(s.get('score') or '')
        place = int(m.group(1)) if m else None
        return _finish([(f'第 {place} 名' if place else '名次不明', _place_score(place))], **base)
    kd = k / max(1, d)
    parts = []
    if s.get('draw'):
        parts.append(('平手', 1))
    elif s.get('won'):
        parts.append(('勝利', 2))
    if s.get('acs') is not None:
        parts.append((f"ACS {s['acs']}", _tier(s['acs'], [(300, 3), (230, 2), (180, 1)])))
    else:
        parts.append((f'K/D {kd:.2f}(單回合模式)', _tier(kd, [(2, 3), (1.4, 2), (1, 1)])))
    parts.append((f'K/D {kd:.2f}', _tier(kd, [(1.5, 2), (1.1, 1)])))
    if s.get('mvp'):
        parts.append(('隊伍 MVP', 1))
    return _finish(parts, **base)


def parts_text(parts: list[tuple[str, int]]) -> str:
    """有加到分的項目：`勝利 +2・KDA 6.5 +3`；全部 0 分回「沒有加分項」。"""
    got = [f'{t} +{p}' for t, p in parts if p > 0]
    return '・'.join(got) if got else '沒有加分項(保底 1 張)'
