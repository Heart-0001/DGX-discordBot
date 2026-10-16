"""進行中 Valorant 對局(GLZ pregame/core-game + PD mmr)的資料整理。純函式，方便測試。"""
from datetime import datetime
from typing import Optional

QUEUE_ZH = {'competitive': '競技', 'unrated': '一般', 'swiftplay': '快速', 'spikerush': '搶攻',
            'deathmatch': '死鬥', 'ggteam': '升級戰', 'hurm': '團隊死鬥', 'premier': '頂級',
            'newmap': '新地圖', 'replication': '複製', 'snowball': '雪球', 'skirmish2v2': '2v2',
            'abilitydraftarena': '技能競技場', '': '自訂'}


def map_name(path: str, content: dict) -> str:
    """'/Game/Maps/Ascent/Ascent' → 中文名；content['mapPaths'] 由 valorant-api 的 mapUrl 建。"""
    return ((content.get('mapPaths') or {}).get(path) or (content.get('maps') or {}).get(path)
            or (path or '?').rsplit('/', 1)[-1])


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


def _hits_enemy(d: dict) -> tuple[dict, dict]:
    """roundResults 的傷害事件 → ({puuid: 對敵人的總傷害}, {puuid: [頭, 身, 腳]})。
    打到自己或隊友的不算：被自己隊的包炸死時 Riot 會記一筆 ~1000 的傷害給被炸的人自己，會把 ADR 灌爆。
    回傳的 dict 為空代表這份資料沒有每回合傷害事件(舊格式/測試)，呼叫端改用 roundDamage。"""
    ffa = ((d.get('matchInfo') or {}).get('queueID') or '').lower() == 'deathmatch'
    team_of = {p.get('subject'): p.get('teamId') for p in d.get('players') or []}
    dmg: dict[str, int] = {}
    shots: dict[str, list] = {}
    for r in d.get('roundResults') or []:
        for ps in r.get('playerStats') or []:
            pu = ps.get('subject', '')
            for dm in ps.get('damage') or []:
                rc = dm.get('receiver')
                if rc == pu or (not ffa and rc in team_of and team_of.get(rc) == team_of.get(pu)):
                    continue
                dmg[pu] = dmg.get(pu, 0) + dm.get('damage', 0)
                acc = shots.setdefault(pu, [0, 0, 0])
                acc[0] += dm.get('headshots', 0)
                acc[1] += dm.get('bodyshots', 0)
                acc[2] += dm.get('legshots', 0)
    return dmg, shots


def _dmg_of(p: dict, enemy_dmg: dict, have_events: bool) -> int:
    if have_events:
        return enemy_dmg.get(p.get('subject', ''), 0)
    return sum(r.get('damage', 0) for r in p.get('roundDamage') or [])


def match_summaries(d: dict, content: dict) -> dict[str, dict]:
    """pd /match-details 一場 → 每位玩家的 {w,k,d,a,agent,queue,t}。w: True/False，死鬥等無隊伍的為 None。"""
    info = d.get('matchInfo') or {}
    agents = content.get('agents', {})
    won = {t.get('teamId'): bool(t.get('won')) for t in d.get('teams') or []}
    queue = (info.get('queueID') or '').lower()
    # 每回合的命中部位與首殺(roundResults) → 算 HS% 與首殺數
    enemy_dmg, shots = _hits_enemy(d)
    have_events = any(ps.get('damage') for r in d.get('roundResults') or [] for ps in r.get('playerStats') or [])
    fb: dict[str, int] = {}
    for r in d.get('roundResults') or []:
        if r.get('firstBloodPlayer'):
            fb[r['firstBloodPlayer']] = fb.get(r['firstBloodPlayer'], 0) + 1
    out = {}
    for p in d.get('players') or []:
        st = p.get('stats') or {}
        pu = p.get('subject', '')
        hs, bs, ls = shots.get(pu, [0, 0, 0])
        out[pu] = {
            'w': won.get(p.get('teamId')) if queue != 'deathmatch' else None,
            'k': st.get('kills', 0), 'd': st.get('deaths', 0), 'a': st.get('assists', 0),
            'score': st.get('score', 0), 'rounds': st.get('roundsPlayed', 0),
            'dmg': _dmg_of(p, enemy_dmg, have_events),
            'hs': hs, 'bs': bs, 'ls': ls, 'fb': fb.get(pu, 0),
            'agent': agents.get((p.get('characterId') or '').lower(), {}).get('name', '?'),
            'agentId': (p.get('characterId') or '').lower(),
            'queue': QUEUE_ZH.get(queue, queue or '?'), 't': info.get('gameStartMillis', 0),
            'season': info.get('seasonId') or '',
        }
    return out


def stored_comp(data: Optional[list], season: str, content: dict) -> Optional[list[dict]]:
    """HenrikDev /v1/by-puuid/stored-matches(競技，新的在前) → 本 act 每場的 {w,k,d,a,...}，格式同 match_summaries。
    它沒有首殺(fb 給 None，畫面顯示 —)；碰到不是本 act 的就停。data 是 None(沒查到)就回 None。"""
    if data is None:
        return None
    agents = content.get('agents', {})
    out = []
    for m in data:
        meta, st = m.get('meta') or {}, m.get('stats') or {}
        sid = (meta.get('season') or {}).get('id') or ''
        if season and sid and sid != season:
            break
        teams = {k.lower(): v or 0 for k, v in (m.get('teams') or {}).items()}
        mine = (st.get('team') or '').lower()
        other = next((v for k, v in teams.items() if k != mine), 0)
        sh, dmg = st.get('shots') or {}, st.get('damage') or {}
        aid = ((st.get('character') or {}).get('id') or '').lower()
        t = meta.get('started_at') or ''
        out.append({
            'w': (teams[mine] > other) if mine in teams and teams[mine] != other else None,
            'k': st.get('kills', 0), 'd': st.get('deaths', 0), 'a': st.get('assists', 0),
            'score': st.get('score', 0), 'rounds': sum(teams.values()), 'dmg': dmg.get('made', 0),
            'hs': sh.get('head', 0), 'bs': sh.get('body', 0), 'ls': sh.get('leg', 0), 'fb': None,
            'agent': agents.get(aid, {}).get('name') or (st.get('character') or {}).get('name') or '?',
            'agentId': aid, 'queue': QUEUE_ZH['competitive'], 't': _iso_ms(t), 'season': sid,
        })
    return out


def _iso_ms(t: str) -> int:
    try:
        return int(datetime.fromisoformat(t.replace('Z', '+00:00')).timestamp() * 1000)
    except ValueError:
        return 0


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


def eog_table(d: dict, me: str, hidden: set, content: dict, names: Optional[dict] = None,
              rr: Optional[dict] = None, tiers: Optional[dict] = None) -> dict:
    """pd /match-details 賽後 → {map, queue, teams:[{mine, won, rounds, players:[...]}]}。
    hidden: 對局中開隱藏名字的 puuid；names: name-service 查到的名字(match-details 現在不給名字)；
    rr: {puuid: competitiveupdates 的這場 RR 變化}(只有競技模式有)；
    tiers: {puuid: 牌位 id}(對局中用 mmr 查到的；match-details 的 competitiveTier 非競技模式一律 0)。"""
    names = names or {}
    info = d.get('matchInfo') or {}
    agents = content.get('agents', {})
    queue = (info.get('queueID') or '').lower()
    tinfo = {t.get('teamId'): t for t in d.get('teams') or []}
    my_team = next((p.get('teamId') for p in d.get('players') or [] if p.get('subject') == me), None)
    teams: dict[str, list] = {}
    extras = eog_extras(d, content)
    enemy_dmg, _ = _hits_enemy(d)
    have_events = any(ps.get('damage') for r in d.get('roundResults') or [] for ps in r.get('playerStats') or [])
    parties = party_tags(d.get('players') or [])
    for p in d.get('players') or []:
        st = p.get('stats') or {}
        pu = p.get('subject', '')
        hid = pu in hidden and pu != me
        casts = st.get('abilityCasts') or {}
        bf = p.get('behaviorFactors') or {}
        teams.setdefault(p.get('teamId'), []).append({
            **extras['players'].get(pu, {}),
            'party': parties.get(pu, ''),
            'platform': (p.get('platformInfo') or {}).get('platformType', ''),
            'casts': [casts.get(k) or 0 for k in ('grenadeCasts', 'ability1Casts', 'ability2Casts', 'ultimateCasts')],
            'afk': bf.get('afkRounds') or 0,
            'rr': (rr or {}).get(pu),
            'puuid': pu, 'me': pu == me, 'hidden': hid,
            'name': '' if hid else (names.get(pu) or {}).get('GameName') or p.get('gameName', ''),
            'tag': '' if hid else (names.get(pu) or {}).get('TagLine') or p.get('tagLine', ''),
            'agentId': (p.get('characterId') or '').lower(),
            'agent': agents.get((p.get('characterId') or '').lower(), {}).get('name', '?'),
            'k': st.get('kills', 0), 'd': st.get('deaths', 0), 'a': st.get('assists', 0),
            'score': st.get('score', 0), 'rounds': st.get('roundsPlayed', 0),
            'dmg': _dmg_of(p, enemy_dmg, have_events),
            'tier': p.get('competitiveTier') or (tiers or {}).get(pu) or 0, 'level': p.get('accountLevel'),
            'tierName': (content.get('tiers', {}).get(str(p.get('competitiveTier') or (tiers or {}).get(pu) or 0)) or {}).get('name', ''),
            'mvp': pu == (tinfo.get(p.get('teamId')) or {}).get('mvp'),
        })
    out = []
    for tid, pl in teams.items():
        t = tinfo.get(tid) or {}
        pl.sort(key=lambda x: -x['score'])
        out.append({'mine': tid == my_team, 'won': t.get('won'), 'rounds': t.get('roundsWon', 0),
                    'points': t.get('numPoints'), 'players': pl})   # points: 團隊死鬥等單回合模式的總分
    out.sort(key=lambda t: not t['mine'])
    if queue == 'deathmatch':          # 死鬥每人一隊 → 併成一張表
        allp = sorted([p for t in out for p in t['players']], key=lambda x: -x['score'])
        out = [{'mine': True, 'won': None, 'rounds': 0, 'players': allp}]
    pod = info.get('gamePodId') or ''
    return {'matchId': info.get('matchId') or '', 'queue': QUEUE_ZH.get(queue, queue or '?'), 'map': map_name(info.get('mapId') or '', content),
            'length': (info.get('gameLengthMillis') or 0) // 1000, 'start': (info.get('gameStartMillis') or 0) // 1000,
            'teams': out, 'rounds': extras['rounds'], 'myTeam': my_team,
            'ranked': bool(info.get('isRanked')), 'early': bool(info.get('isEarlyCompletion')),
            'version': (info.get('gameVersion') or '').replace('release-', '').split('-shipping')[0],
            'server': server_name(pod), 'matchMvp': d.get('matchMvp') or ''}


SERVER_ZH = {'ape1': '香港', 'apne1': '東京', 'apse1': '新加坡', 'apse2': '雪梨', 'aps1': '孟買', 'apne2': '首爾',
             'use1': '美東', 'usw2': '美西', 'euc1': '法蘭克福', 'euw2': '倫敦', 'sae1': '聖保羅'}


def server_name(pod: str) -> str:
    """'aresriot.aws-ape1-prod.ap-gp-hongkong-1' → '香港'。"""
    for key, name in SERVER_ZH.items():
        if f'-{key}-' in pod:
            return name
    return pod.split('.')[-1] if pod else ''


def party_tags(players: list) -> dict[str, str]:
    """同 partyId 的人(≥2)給同一個字母，方便看誰是組隊來的。"""
    groups: dict[str, list] = {}
    for p in players:
        if p.get('partyId'):
            groups.setdefault(p['partyId'], []).append(p.get('subject', ''))
    out = {}
    letters = iter('ABCDEFGHIJ')
    for pid, members in groups.items():
        if len(members) >= 2:
            tag = next(letters, '?')
            for pu in members:
                out[pu] = tag
    return out


def eog_extras(d: dict, content: dict) -> dict:
    """用 roundResults + kills 算每人的進階數據與每回合摘要。
    players[pu] = {hs, kast, fb, fd, mk:[3K,4K,5K+ 次數], clutch:[...'1v2'], econ(平均裝備值), weapon(最多回合拿的槍), plants, defuses}
    rounds = [{n, win(teamId), code, site, planter, defuser, ceremony, fb, score:[紅,藍]...}]"""
    weapons = content.get('weapons') or {}
    ceremonies = content.get('ceremonies') or {}
    players = d.get('players') or []
    team_of = {p.get('subject'): p.get('teamId') for p in players}
    members: dict[str, list] = {}
    for pu, t in team_of.items():
        members.setdefault(t, []).append(pu)
    queue = ((d.get('matchInfo') or {}).get('queueID') or '').lower()
    ffa = queue == 'deathmatch'
    stats = {pu: {'hs': [0, 0, 0], 'kast': 0, 'fb': 0, 'fd': 0, 'mk': [0, 0, 0], 'clutch': [],
                  'loadout': [], 'weapons': {}, 'killw': {}, 'plants': 0, 'defuses': 0} for pu in team_of}
    kills_by_round: dict[int, list] = {}
    for k in d.get('kills') or []:
        kills_by_round.setdefault(k.get('round', 0), []).append(k)
    rounds = []
    score: dict[str, int] = {}
    team_ids = sorted(members)
    for r in d.get('roundResults') or []:
        n = r.get('roundNum', 0)
        rk = sorted(kills_by_round.get(n, []), key=lambda k: k.get('roundTime', 0))
        # 真的殺到敵人的(排除包炸死自己、teamkill)；死亡順序(KAST/Clutch)還是用全部的 rk
        rk_enemy = [k for k in rk if k.get('killer') != k.get('victim')
                    and (ffa or team_of.get(k.get('killer')) != team_of.get(k.get('victim')))]
        winner = r.get('winningTeam')
        if winner:
            score[winner] = score.get(winner, 0) + 1
        # 每人這回合：命中部位、裝備值、殺敵數
        kills_in_round: dict[str, int] = {}
        for ps in r.get('playerStats') or []:
            pu = ps.get('subject', '')
            st = stats.get(pu)
            if not st:
                continue
            eco = ps.get('economy') or {}
            if eco.get('loadoutValue') is not None:
                st['loadout'].append(eco['loadoutValue'])
            held = (eco.get('weapon') or '').lower()    # 這回合手上的槍(最常用槍用這個算，不看擊殺)
            if held:
                st['weapons'][held] = st['weapons'].get(held, 0) + 1
            # 被自己隊的包炸死 Riot 也記成 killer=victim 的一筆 kill；它和 teamkill 都不算殺敵
            kills_in_round[pu] = sum(1 for k in ps.get('kills') or []
                                     if k.get('victim') != pu and (ffa or team_of.get(k.get('victim')) != team_of.get(pu)))
            for k in ps.get('kills') or []:
                item = ((k.get('finishingDamage') or {}).get('damageItem') or '').lower()
                if (k.get('finishingDamage') or {}).get('damageType') == 'Weapon' and item:
                    st['killw'][item] = st['killw'].get(item, 0) + 1
        for pu, c in kills_in_round.items():
            if c >= 3:
                stats[pu]['mk'][min(c, 5) - 3] += 1
        if r.get('bombPlanter') in stats:
            stats[r['bombPlanter']]['plants'] += 1
        if r.get('bombDefuser') in stats:
            stats[r['bombDefuser']]['defuses'] += 1
        # 首殺 / 被首殺
        fb = r.get('firstBloodPlayer') or (rk_enemy[0].get('killer') if rk_enemy else None)
        if fb in stats:
            stats[fb]['fb'] += 1
        if rk_enemy and rk_enemy[0].get('victim') in stats:
            stats[rk_enemy[0]['victim']]['fd'] += 1
        # KAST 與 Clutch：照擊殺順序算誰還活著
        alive = {t: set(m) for t, m in members.items()}
        dead_at: dict[str, int] = {}
        killer_of: dict[str, str] = {}
        candidate: dict[str, tuple[str, int]] = {}   # team → (最後一個活人, 當時敵方人數)
        for k in rk:
            v, kl = k.get('victim'), k.get('killer')
            t = team_of.get(v)
            if t and v in alive[t]:
                alive[t].discard(v)
                dead_at[v] = k.get('roundTime', 0)
                killer_of[v] = kl
            if not ffa:
                for t2, al in alive.items():
                    if len(al) == 1 and t2 not in candidate:
                        others = sum(len(a) for t3, a in alive.items() if t3 != t2)
                        if others >= 1:
                            candidate[t2] = (next(iter(al)), others)
        if winner in candidate and not ffa:
            pu, vs = candidate[winner]
            stats[pu]['clutch'].append(f'1v{vs}')
        assisted = {a for k in rk for a in (k.get('assistants') or [])}
        for pu in team_of:
            survived = pu not in dead_at
            kl = killer_of.get(pu)
            # 被換掉：殺我的敵人 5 秒內也死了。被包炸死(killer=自己)或被隊友殺不算，不然自爆會自動算成被換
            traded = (pu in dead_at and kl and kl != pu and (ffa or team_of.get(kl) != team_of.get(pu))
                      and any(kv == kl and 0 <= t2 - dead_at[pu] <= 5000 for kv, t2 in dead_at.items()))
            if kills_in_round.get(pu) or pu in assisted or survived or traded:
                stats[pu]['kast'] += 1
        rounds.append({
            'n': n, 'win': winner, 'code': r.get('roundResultCode') or '', 'site': r.get('plantSite') or '',
            'planter': r.get('bombPlanter') or '', 'defuser': r.get('bombDefuser') or '',
            'ceremony': ceremony_name(r.get('roundCeremony'), ceremonies),
            'fb': fb or '', 'score': [score.get(t, 0) for t in team_ids],
        })
    _, shots = _hits_enemy(d)
    for pu, acc in shots.items():
        if pu in stats:
            stats[pu]['hs'] = acc
    nrounds = max(1, len(rounds))
    roundbased = len(rounds) > 1 and not ffa    # 團隊死鬥/死鬥只有 1 回合，KAST/多殺/Clutch 沒意義
    out = {}
    for pu, st in stats.items():
        tot = sum(st['hs'])
        wsrc = st['weapons'] or st['killw']    # 沒有每回合裝備資料(舊格式)才退回用擊殺武器
        top = max(wsrc.items(), key=lambda kv: kv[1])[0] if wsrc else ''
        loadout = [v for v in st['loadout'] if v]
        out[pu] = {
            'hs': round(st['hs'][0] * 100 / tot) if tot else 0,
            'kast': round(st['kast'] * 100 / nrounds) if roundbased else None,
            'fb': st['fb'] if roundbased else 0, 'fd': st['fd'] if roundbased else 0,
            'mk': st['mk'] if roundbased else None, 'clutch': st['clutch'] if roundbased else [],
            'econ': round(sum(loadout) / len(loadout)) if loadout else None,
            'weapon': weapons.get(top, '') if top else '',
            'plants': st['plants'], 'defuses': st['defuses'],
        }
    return {'players': out, 'rounds': rounds, 'teamIds': team_ids}


def ceremony_name(raw, ceremonies: dict) -> str:
    """'CeremonyClutch' / 'Clutch'(HenrikDev) → 中文名；Default / 空 → ''。"""
    key = 'ceremony' + (raw or '').replace('Ceremony', '').replace(' ', '').lower()
    if key in ('ceremony', 'ceremonydefault'):
        return ''
    return ceremonies.get(key) or (raw or '').replace('Ceremony', '')


def henrik_to_riot(m: dict) -> dict:
    """HenrikDev v4 的一場 → Riot match-details 的形狀，讓 eog_table / eog_extras 兩邊共用。"""
    meta = m.get('metadata') or {}
    rounds = m.get('rounds') or []
    kills = m.get('kills') or []
    started = 0
    if meta.get('started_at'):
        from datetime import datetime
        try:
            started = int(datetime.fromisoformat(meta['started_at'].replace('Z', '+00:00')).timestamp() * 1000)
        except ValueError:
            started = 0
    q = (meta.get('queue') or {}).get('id') or ''
    # 每人每回合傷害(整場 roundDamage 用)
    dmg_rounds: dict[str, list] = {}
    for r in rounds:
        for st in r.get('stats') or []:
            pu = (st.get('player') or {}).get('puuid', '')
            dmg_rounds.setdefault(pu, []).append({'round': r.get('id', 0),
                                                 'damage': sum(e.get('damage', 0) for e in st.get('damage_events') or [])})
    players = []
    for p in m.get('players') or []:
        ac = p.get('ability_casts') or {}
        players.append({
            'subject': p.get('puuid', ''), 'gameName': p.get('name', ''), 'tagLine': p.get('tag', ''),
            'teamId': p.get('team_id'), 'partyId': p.get('party_id'), 'characterId': (p.get('agent') or {}).get('id', ''),
            'stats': {**{k: (p.get('stats') or {}).get(k, 0) for k in ('score', 'kills', 'deaths', 'assists')},
                      'roundsPlayed': len(rounds),
                      'abilityCasts': {'grenadeCasts': ac.get('grenade') or 0, 'ability1Casts': ac.get('ability1') or 0,
                                       'ability2Casts': ac.get('ability2') or 0, 'ultimateCasts': ac.get('ultimate') or 0}},
            'roundDamage': dmg_rounds.get(p.get('puuid', ''), []),
            'competitiveTier': (p.get('tier') or {}).get('id') or 0, 'accountLevel': p.get('account_level'),
            'platformInfo': {'platformType': p.get('platform') or ''},
            'behaviorFactors': {'afkRounds': (p.get('behavior') or {}).get('afk_rounds') or 0},
        })
    single = len(rounds) <= 1   # HenrikDev 單回合模式把總分塞在 rounds.won
    teams = [{'teamId': t.get('team_id'), 'won': t.get('won'),
              'roundsWon': 0 if single else (t.get('rounds') or {}).get('won', 0),
              'numPoints': (t.get('rounds') or {}).get('won', 0) if single else None,
              'mvp': (t.get('mvp') or {}).get('puuid')} for t in m.get('teams') or []]
    rkills = []
    for k in kills:
        w = k.get('weapon') or {}
        rkills.append({'round': k.get('round', 0), 'roundTime': k.get('time_in_round_in_ms', 0),
                       'killer': (k.get('killer') or {}).get('puuid'), 'victim': (k.get('victim') or {}).get('puuid'),
                       'assistants': [a.get('puuid') for a in k.get('assistants') or [] if a.get('puuid')],
                       'finishingDamage': {'damageType': w.get('type') or '', 'damageItem': w.get('id') or ''}})
    by_round: dict[int, list] = {}
    for k in rkills:
        by_round.setdefault(k['round'], []).append(k)
    rr = []
    for r in rounds:
        n = r.get('id', 0)
        ps = []
        for st in r.get('stats') or []:
            pu = (st.get('player') or {}).get('puuid', '')
            eco = st.get('economy') or {}
            ps.append({'subject': pu,
                       'damage': [{'receiver': (e.get('player') or {}).get('puuid', ''), 'damage': e.get('damage', 0),
                                   'headshots': e.get('headshots', 0), 'bodyshots': e.get('bodyshots', 0),
                                   'legshots': e.get('legshots', 0)} for e in st.get('damage_events') or []],
                       'economy': {'loadoutValue': eco.get('loadout_value'), 'weapon': ((eco.get('weapon') or {}).get('id') if isinstance(eco.get('weapon'), dict) else eco.get('weapon'))},
                       'kills': [k for k in by_round.get(n, []) if k['killer'] == pu]})
        plant, defuse = r.get('plant') or {}, r.get('defuse') or {}
        rr.append({'roundNum': n, 'winningTeam': r.get('winning_team'), 'roundResultCode': r.get('result') or '',
                   'roundCeremony': r.get('ceremony') or '', 'plantSite': plant.get('site') or '',
                   'bombPlanter': (plant.get('player') or {}).get('puuid') or '',
                   'bombDefuser': (defuse.get('player') or {}).get('puuid') or '',
                   'firstBloodPlayer': (r.get('first_blood') or {}).get('puuid') or '', 'playerStats': ps})
    return {
        'matchInfo': {'matchId': meta.get('match_id'), 'queueID': q, 'mapId': (meta.get('map') or {}).get('id', ''),
                      'gameLengthMillis': meta.get('game_length_in_ms') or 0, 'gameStartMillis': started,
                      'isRanked': q == 'competitive', 'isCompleted': meta.get('is_completed', True),
                      'gameVersion': meta.get('game_version') or '', 'gamePodId': meta.get('cluster') or ''},
        'players': players, 'teams': teams, 'roundResults': rr, 'kills': rkills,
        'matchMvp': (meta.get('mvp') or {}).get('puuid') or '',
    }


def riot_summarize(e: dict, puuid: str) -> Optional[dict]:
    """eog_table 的輸出 → 跟 valo_parse.summarize 同形狀的單場摘要(/valo 清單與選單用)。"""
    me = next((p for t in e.get('teams') or [] for p in t['players'] if p['puuid'] == puuid), None)
    if not me:
        return None
    mine = next((t for t in e['teams'] if t['mine']), None)
    teams = e['teams']
    ffa = e.get('queue') == QUEUE_ZH['deathmatch']
    roundbased = me['rounds'] > 1
    if ffa:
        order = sorted((p for t in teams for p in t['players']), key=lambda p: -p['score'])
        place = next((i + 1 for i, p in enumerate(order) if p['puuid'] == puuid), None)
        won, draw, score = place == 1, False, f'第 {place} 名' if place else ''
    else:
        won = mine['won'] if mine else None
        other = next((t for t in teams if t is not mine), None)
        if roundbased:
            score = f"{mine['rounds']}:{other['rounds']}" if mine and other else ''
            draw = bool(mine and other) and mine['rounds'] == other['rounds']
        else:
            score = f"{mine.get('points')}:{other.get('points')}" if mine and other and mine.get('points') is not None else ''
            draw = False
    from datetime import datetime, timezone
    return {
        'matchId': e.get('matchId', ''), 'won': won, 'draw': draw, 'ffa': ffa, 'score': score,
        'map': e.get('map', '?'), 'queue': e.get('queue', '?'),
        'started': datetime.fromtimestamp(e['start'], tz=timezone.utc) if e.get('start') else None,
        'lengthMin': round((e.get('length') or 0) / 60), 'mvp': me['mvp'] and not ffa,   # 死鬥每人一隊，mvp 沒意義
        'agent': me['agent'], 'agentId': me['agentId'], 'k': me['k'], 'd': me['d'], 'a': me['a'],
        'acs': round(me['score'] / me['rounds']) if roundbased and not ffa else None,
        'adr': round(me['dmg'] / me['rounds']) if roundbased else None,
        'hs': me['hs'], 'fb': me['fb'], 'tier': me['tier'], 'level': me.get('level'),
    }
