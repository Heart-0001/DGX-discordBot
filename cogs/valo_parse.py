"""HenrikDev Valorant API(v4 match / v3 mmr)的資料整理。純函式，方便測試。"""
from datetime import datetime, timezone
from typing import Optional

QUEUE_ZH = {
    'competitive': '競技', 'unrated': '一般', 'swiftplay': '超速衝點', 'spikerush': '輕裝上陣',
    'deathmatch': '死鬥', 'teamdeathmatch': '團隊死鬥', 'hurm': '團隊死鬥', 'premier': 'Premier',
    'ggteam': '武器升級', 'onefa': '複製大戰', 'snowball': '雪球大戰', 'newmap': '新地圖', 'custom': '自訂',
}


def queue_zh(q: dict) -> str:
    qid = (q or {}).get('id') or ''
    return QUEUE_ZH.get(qid, (q or {}).get('name') or qid or '?')


def _started(meta: dict) -> Optional[datetime]:
    s = meta.get('started_at')
    try:
        return datetime.fromisoformat(s.replace('Z', '+00:00')) if s else None
    except ValueError:
        return None


def _first_bloods(match: dict) -> dict:
    out: dict[str, int] = {}
    for r in match.get('rounds') or []:
        fb = (r.get('first_blood') or {}).get('puuid')
        if fb:
            out[fb] = out.get(fb, 0) + 1
    return out


def player_line(p: dict, rounds: int, fbs: dict, content: dict) -> dict:
    st = p.get('stats') or {}
    shots = (st.get('headshots') or 0) + (st.get('bodyshots') or 0) + (st.get('legshots') or 0)
    agent = p.get('agent') or {}
    return {
        'puuid': p.get('puuid'), 'name': p.get('name', ''), 'tag': p.get('tag', ''),
        'team': p.get('team_id'),
        'agent': content.get('agents', {}).get(agent.get('id', ''), {}).get('name') or agent.get('name', '?'),
        'agentId': agent.get('id', ''),
        'k': st.get('kills', 0), 'd': st.get('deaths', 0), 'a': st.get('assists', 0),
        # 只有一回合的模式(團隊死鬥、死鬥)ACS/ADR 沒意義
        'acs': round((st.get('score') or 0) / rounds) if rounds > 1 else None,
        'adr': round(((st.get('damage') or {}).get('dealt') or 0) / rounds) if rounds > 1 else None,
        'hs': round((st.get('headshots') or 0) * 100 / shots) if shots else 0,
        'fb': fbs.get(p.get('puuid'), 0),
        'tier': (p.get('tier') or {}).get('id', 0),
    }


def is_ffa(match: dict) -> bool:
    """死鬥：每個人自成一隊(teams 有十幾個)。"""
    meta = match.get('metadata') or {}
    return (meta.get('queue') or {}).get('mode_type') == 'Deathmatch' or len(match.get('teams') or []) > 2


def summarize(match: dict, puuid: str, content: dict) -> Optional[dict]:
    """單場摘要(以 puuid 這位玩家的角度)。找不到這位玩家回 None。"""
    meta = match.get('metadata') or {}
    me = next((p for p in match.get('players') or [] if p.get('puuid') == puuid), None)
    if not me:
        return None
    teams = {t.get('team_id'): t for t in match.get('teams') or []}
    mine = teams.get(me.get('team_id')) or {}
    rr = mine.get('rounds') or {}
    rounds = len(match.get('rounds') or []) or (rr.get('won', 0) + rr.get('lost', 0))
    line = player_line(me, rounds, _first_bloods(match), content)
    ffa = is_ffa(match)
    if ffa:
        # 死鬥：用名次，ACS/ADR 沒意義
        place = mine.get('placement')
        won, draw, score, mvp = place == 1, False, f'第 {place} 名' if place else '', False
        line['acs'] = line['adr'] = None
    else:
        won = mine.get('won')
        # 隊伍的 rounds 偶爾跟回合紀錄對不上(實測 8:4 但其實打了 17 回合)→ 有回合紀錄就以它為準
        rs = match.get('rounds') or []
        if len(rs) > 1 and any(r.get('winning_team') for r in rs):
            w = sum(1 for r in rs if r.get('winning_team') == me.get('team_id'))
            rr = {'won': w, 'lost': len(rs) - w}
        draw = bool(rr) and rr.get('won') == rr.get('lost')
        score = f"{rr.get('won', 0)}:{rr.get('lost', 0)}" if rr else ''
        mvp = (mine.get('mvp') or {}).get('puuid') == puuid
    map_ = meta.get('map') or {}
    return {
        'matchId': meta.get('match_id'), 'region': meta.get('region') or '',
        'won': won, 'draw': draw, 'ffa': ffa,
        'score': score,
        'map': content.get('maps', {}).get(map_.get('id', ''), map_.get('name', '?')),
        'queue': queue_zh(meta.get('queue')),
        'started': _started(meta),
        'lengthMin': round((meta.get('game_length_in_ms') or 0) / 60000),
        'mvp': mvp, **line,
    }


def detail(match: dict, puuid: str, content: dict) -> dict:
    """單場完整計分板 + 回合條。"""
    meta = match.get('metadata') or {}
    rounds = match.get('rounds') or []
    fbs = _first_bloods(match)
    n = len(rounds)
    me = next((p for p in match.get('players') or [] if p.get('puuid') == puuid), None)
    my_team = me.get('team_id') if me else None
    teams = []
    for t in ([] if is_ffa(match) else match.get('teams') or []):
        ps = [player_line(p, n, fbs, content) for p in match.get('players') or [] if p.get('team_id') == t.get('team_id')]
        ps.sort(key=lambda x: -(x['acs'] if x['acs'] is not None else x['k']))
        w = sum(1 for r in rounds if r.get('winning_team') == t.get('team_id'))
        trr = {'won': w, 'lost': n - w} if n > 1 and any(r.get('winning_team') for r in rounds) else (t.get('rounds') or {})
        teams.append({'id': t.get('team_id'), 'mine': t.get('team_id') == my_team, 'won': t.get('won'),
                      'rounds': trr, 'mvp': (t.get('mvp') or {}).get('puuid'), 'players': ps})
    teams.sort(key=lambda t: not t['mine'])
    if not teams:   # 死鬥：沒有隊伍，全部放一隊
        ps = [player_line(p, n, fbs, content) for p in match.get('players') or []]
        ps.sort(key=lambda x: -x['k'])
        teams = [{'id': 'all', 'mine': True, 'won': None, 'rounds': {}, 'mvp': None, 'players': ps}]
    if is_ffa(match):
        my_team = None
    strip = (''.join('🟦' if r.get('winning_team') == my_team else '🟥' for r in rounds)
             if my_team and len(rounds) > 1 else '')
    s = summarize(match, puuid, content) if me else None
    return {'summary': s, 'teams': teams, 'strip': strip, 'me': puuid,
            'map': (s or {}).get('map') or (meta.get('map') or {}).get('name', '?'),
            'queue': queue_zh(meta.get('queue'))}


def mmr_summary(m: dict, content: dict) -> dict:
    cur = (m or {}).get('current') or {}
    peak = (m or {}).get('peak') or {}
    tiers = content.get('tiers', {})

    def tier(t):
        tid = (t or {}).get('id', 0)
        return {'id': tid, 'name': tiers.get(str(tid), {}).get('name') or (t or {}).get('name', '未定級')}
    seasonal = (m or {}).get('seasonal') or []
    last = seasonal[-1] if seasonal else {}
    return {
        'current': tier(cur.get('tier')), 'rr': cur.get('rr', 0), 'lastChange': cur.get('last_change'),
        'elo': cur.get('elo'), 'needed': cur.get('games_needed_for_rating', 0),
        'peak': tier(peak.get('tier')), 'peakSeason': (peak.get('season') or {}).get('short', ''),
        'seasonWins': last.get('wins'), 'seasonGames': last.get('games'),
    }
