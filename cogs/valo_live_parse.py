"""進行中 Valorant 對局(GLZ pregame/core-game + PD mmr)的資料整理。純函式，方便測試。"""
from typing import Optional

QUEUE_ZH = {'competitive': '競技', 'unrated': '一般', 'swiftplay': '快速', 'spikerush': '搶攻',
            'deathmatch': '死鬥', 'ggteam': '升級戰', 'hurm': '團隊死鬥', 'premier': '頂級',
            'newmap': '新地圖', 'replication': '複製', 'snowball': '雪球', 'skirmish2v2': '2v2',
            'abilitydraftarena': '技能競技場', '': '自訂'}


def map_name(path: str, content: dict) -> str:
    """'/Game/Maps/Ascent/Ascent' → 中文名；content['mapPaths'] 由 valorant-api 的 mapUrl 建。"""
    return (content.get('mapPaths') or {}).get(path) or (path or '?').rsplit('/', 1)[-1]


def rank_of(mmr: Optional[dict], season: str, content: dict) -> dict:
    """pd /mmr 的 QueueSkills.competitive → 目前牌位、RR、本季勝場、歷史最高。"""
    tiers = content.get('tiers', {})

    def name(t):
        return tiers.get(str(t), {}).get('name') or ('未定級' if not t else f'Tier {t}')
    comp = ((mmr or {}).get('QueueSkills') or {}).get('competitive') or {}
    by = comp.get('SeasonalInfoBySeasonID') or {}
    cur = by.get(season) or {}
    latest = (mmr or {}).get('LatestCompetitiveUpdate') or {}
    tier = cur.get('CompetitiveTier') or 0
    rr = cur.get('RankedRating') or 0
    if not cur and latest.get('SeasonID') == season:
        tier, rr = latest.get('TierAfterUpdate') or 0, latest.get('RankedRatingAfterUpdate') or 0
    peak = max([v.get('CompetitiveTier') or 0 for v in by.values()] + [0])
    wins, games = cur.get('NumberOfWins') or 0, cur.get('NumberOfGames') or 0
    return {'tier': tier, 'name': name(tier), 'rr': rr, 'wins': wins, 'games': games,
            'wr': round(wins * 100 / games) if games else None, 'peak': peak, 'peakName': name(peak),
            'total': comp.get('TotalGamesNeededForRating') or 0}


def parse_match(kind: str, m: dict, names: dict, mmrs: dict, season: str, content: dict, me: str) -> dict:
    """kind='pregame'(只有我方)或 'coregame'(雙方)。回 {queue, map, teams:[{mine, players:[...]}]}。"""
    agents = content.get('agents', {})
    if kind == 'pregame':
        ally = m.get('AllyTeam') or {}
        raw = [(p, True) for p in ally.get('Players') or []]
        queue = m.get('QueueID') or ''
        mode = m.get('Mode') or ''
    else:
        my_team = next((p.get('TeamID') for p in m.get('Players') or [] if p.get('Subject') == me), None)
        raw = [(p, p.get('TeamID') == my_team) for p in m.get('Players') or []]
        queue = (m.get('MatchmakingData') or {}).get('QueueID') or ''
        mode = m.get('ModeID') or ''
    teams: dict[bool, list] = {True: [], False: []}
    for p, mine in raw:
        pu = p.get('Subject', '')
        ident = p.get('PlayerIdentity') or {}
        nm = names.get(pu) or {}
        hidden = bool(ident.get('Incognito')) and pu != me
        agent = agents.get((p.get('CharacterID') or '').lower(), {})
        teams[mine].append({
            'puuid': pu, 'me': pu == me, 'hidden': hidden,
            'name': '' if hidden else nm.get('GameName', ''), 'tag': '' if hidden else nm.get('TagLine', ''),
            'agent': agent.get('name') or ('選角中' if kind == 'pregame' else '?'),
            'agentId': (p.get('CharacterID') or '').lower(),
            'locked': (p.get('CharacterSelectionState') == 'locked') if kind == 'pregame' else True,
            'level': None if ident.get('HideAccountLevel') and pu != me else ident.get('AccountLevel'),
            'rank': rank_of(mmrs.get(pu), season, content),
        })
    out = [{'mine': True, 'players': teams[True]}]
    if teams[False]:
        out.append({'mine': False, 'players': teams[False]})
    return {'kind': kind, 'queueId': queue.lower(), 'queue': QUEUE_ZH.get(queue.lower(), queue or mode.rsplit('/', 1)[-1] or '?'),
            'ranked': queue.lower() == 'competitive', 'map': map_name(m.get('MapID') or '', content),
            'teams': out}


def match_summaries(d: dict, content: dict) -> dict[str, dict]:
    """pd /match-details 一場 → 每位玩家的 {w,k,d,a,agent,queue,t}。w: True/False，死鬥等無隊伍的為 None。"""
    info = d.get('matchInfo') or {}
    agents = content.get('agents', {})
    won = {t.get('teamId'): bool(t.get('won')) for t in d.get('teams') or []}
    queue = (info.get('queueID') or '').lower()
    # 每回合的命中部位與首殺(roundResults) → 算 HS% 與首殺數
    shots: dict[str, list] = {}
    fb: dict[str, int] = {}
    for r in d.get('roundResults') or []:
        if r.get('firstBloodPlayer'):
            fb[r['firstBloodPlayer']] = fb.get(r['firstBloodPlayer'], 0) + 1
        for ps in r.get('playerStats') or []:
            acc = shots.setdefault(ps.get('subject', ''), [0, 0, 0])
            for dm in ps.get('damage') or []:
                acc[0] += dm.get('headshots', 0)
                acc[1] += dm.get('bodyshots', 0)
                acc[2] += dm.get('legshots', 0)
    out = {}
    for p in d.get('players') or []:
        st = p.get('stats') or {}
        pu = p.get('subject', '')
        hs, bs, ls = shots.get(pu, [0, 0, 0])
        out[pu] = {
            'w': won.get(p.get('teamId')) if queue != 'deathmatch' else None,
            'k': st.get('kills', 0), 'd': st.get('deaths', 0), 'a': st.get('assists', 0),
            'score': st.get('score', 0), 'rounds': st.get('roundsPlayed', 0),
            'dmg': sum(r.get('damage', 0) for r in p.get('roundDamage') or []),
            'hs': hs, 'bs': bs, 'ls': ls, 'fb': fb.get(pu, 0),
            'agent': agents.get((p.get('characterId') or '').lower(), {}).get('name', '?'),
            'agentId': (p.get('characterId') or '').lower(),
            'queue': QUEUE_ZH.get(queue, queue or '?'), 't': info.get('gameStartMillis', 0),
        }
    return out


def mode_record(games: list[dict], queue_id: str) -> dict:
    """某模式最近 N 場 → {q, n, w, l, kda}(死鬥類沒勝負 → w/l 都 0)。"""
    w = sum(1 for g in games if g['w'] is True)
    l = sum(1 for g in games if g['w'] is False)
    k, d, a = (sum(g[x] for g in games) for x in 'kda')
    return {'q': QUEUE_ZH.get(queue_id, queue_id or '?'), 'n': len(games), 'w': w, 'l': l,
            'kda': round((k + a) / max(1, d), 1)}


def recent_line(games: list[dict]) -> str:
    """近 N 場 → '近5 3勝1敗 KDA 1.4'(勝負只算有隊伍的場)。"""
    if not games:
        return ''
    w = sum(1 for g in games if g['w'] is True)
    l = sum(1 for g in games if g['w'] is False)
    k, d, a = (sum(g[x] for g in games) for x in 'kda')
    kda = f'{(k + a) / max(1, d):.1f}'
    rec = f'{w}勝{l}敗 ' if w + l else ''
    return f'近{len(games)} {rec}KDA {kda}'


def eog_table(d: dict, me: str, hidden: set, content: dict, names: Optional[dict] = None) -> dict:
    """pd /match-details 賽後 → {map, queue, teams:[{mine, won, rounds, players:[...]}]}。
    hidden: 對局中開隱藏名字的 puuid；names: name-service 查到的名字(match-details 現在不給名字)。"""
    names = names or {}
    info = d.get('matchInfo') or {}
    agents = content.get('agents', {})
    queue = (info.get('queueID') or '').lower()
    tinfo = {t.get('teamId'): t for t in d.get('teams') or []}
    my_team = next((p.get('teamId') for p in d.get('players') or [] if p.get('subject') == me), None)
    teams: dict[str, list] = {}
    for p in d.get('players') or []:
        st = p.get('stats') or {}
        pu = p.get('subject', '')
        hid = pu in hidden and pu != me
        teams.setdefault(p.get('teamId'), []).append({
            'puuid': pu, 'me': pu == me, 'hidden': hid,
            'name': '' if hid else (names.get(pu) or {}).get('GameName') or p.get('gameName', ''),
            'tag': '' if hid else (names.get(pu) or {}).get('TagLine') or p.get('tagLine', ''),
            'agentId': (p.get('characterId') or '').lower(),
            'agent': agents.get((p.get('characterId') or '').lower(), {}).get('name', '?'),
            'k': st.get('kills', 0), 'd': st.get('deaths', 0), 'a': st.get('assists', 0),
            'score': st.get('score', 0), 'rounds': st.get('roundsPlayed', 0),
            'dmg': sum(r.get('damage', 0) for r in p.get('roundDamage') or []),
            'tier': p.get('competitiveTier') or 0, 'level': p.get('accountLevel'),
            'mvp': pu == (tinfo.get(p.get('teamId')) or {}).get('mvp'),
        })
    out = []
    for tid, pl in teams.items():
        t = tinfo.get(tid) or {}
        pl.sort(key=lambda x: -x['score'])
        out.append({'mine': tid == my_team, 'won': t.get('won'), 'rounds': t.get('roundsWon', 0), 'players': pl})
    out.sort(key=lambda t: not t['mine'])
    if queue == 'deathmatch':          # 死鬥每人一隊 → 併成一張表
        allp = sorted([p for t in out for p in t['players']], key=lambda x: -x['score'])
        out = [{'mine': True, 'won': None, 'rounds': 0, 'players': allp}]
    return {'queue': QUEUE_ZH.get(queue, queue or '?'), 'map': map_name(info.get('mapId') or '', content),
            'length': (info.get('gameLengthMillis') or 0) // 1000, 'start': (info.get('gameStartMillis') or 0) // 1000,
            'teams': out}
