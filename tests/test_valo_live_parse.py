from cogs.valo_live_parse import parse_match, rank_of

CONTENT = {
    'agents': {'a1': {'name': '傑特', 'icon': 'i'}, 'a2': {'name': '菲尼克斯', 'icon': 'i'}},
    'mapPaths': {'/Game/Maps/Ascent/Ascent': '遺落境地'},
    'tiers': {'7': {'name': '銀牌 2'}, '12': {'name': '金牌 1'}, '0': {'name': '未定級'}},
}
SEASON = 's-now'


def mmr(tier, rr, wins, games, old_peak=0):
    by = {SEASON: {'CompetitiveTier': tier, 'RankedRating': rr, 'NumberOfWins': wins, 'NumberOfGames': games}}
    if old_peak:
        by['s-old'] = {'CompetitiveTier': old_peak, 'RankedRating': 1, 'NumberOfWins': 1, 'NumberOfGames': 1}
    return {'QueueSkills': {'competitive': {'SeasonalInfoBySeasonID': by}}}


def test_rank_of_current_and_peak():
    r = rank_of(mmr(7, 50, 11, 24, old_peak=12), SEASON, CONTENT)
    assert (r['name'], r['rr'], r['wins'], r['games'], r['wr']) == ('銀牌 2', 50, 11, 24, 46)
    assert r['peak'] == 12 and r['peakName'] == '金牌 1'


def test_rank_of_no_games_this_season():
    r = rank_of({'QueueSkills': {'competitive': {}}}, SEASON, CONTENT)
    assert r['tier'] == 0 and r['name'] == '未定級' and r['wr'] is None
    assert rank_of(None, SEASON, CONTENT)['tier'] == 0


def test_coregame_two_teams_and_incognito():
    m = {'MapID': '/Game/Maps/Ascent/Ascent', 'MatchmakingData': {'QueueID': 'competitive'},
         'Players': [
             {'Subject': 'me', 'TeamID': 'Blue', 'CharacterID': 'A1',
              'PlayerIdentity': {'AccountLevel': 120, 'Incognito': True}},
             {'Subject': 'p2', 'TeamID': 'Red', 'CharacterID': 'a2',
              'PlayerIdentity': {'AccountLevel': 30, 'Incognito': True, 'HideAccountLevel': True}},
         ]}
    names = {'me': {'GameName': 'Me', 'TagLine': '0001'}, 'p2': {'GameName': 'X', 'TagLine': '1'}}
    d = parse_match('coregame', m, names, {'me': mmr(7, 50, 1, 2)}, SEASON, CONTENT, 'me')
    assert d['queue'] == '競技' and d['ranked'] and d['map'] == '遺落境地'
    assert [t['mine'] for t in d['teams']] == [True, False]
    me, p2 = d['teams'][0]['players'][0], d['teams'][1]['players'][0]
    assert me['me'] and not me['hidden'] and me['name'] == 'Me' and me['agent'] == '傑特' and me['level'] == 120 and me['agentId'] == 'a1'
    assert me['rank']['name'] == '銀牌 2'
    assert p2['hidden'] and p2['name'] == '' and p2['level'] is None and p2['agent'] == '菲尼克斯'
    assert p2['rank']['tier'] == 0


def test_pregame_ally_only():
    m = {'MapID': '/Game/Maps/Foo/Foo', 'QueueID': 'unrated', 'Mode': '/Game/GameModes/Bomb/BombGameMode',
         'AllyTeam': {'Players': [
             {'Subject': 'me', 'CharacterID': '', 'CharacterSelectionState': '', 'PlayerIdentity': {}},
             {'Subject': 't', 'CharacterID': 'a1', 'CharacterSelectionState': 'locked', 'PlayerIdentity': {}}]}}
    d = parse_match('pregame', m, {}, {}, SEASON, CONTENT, 'me')
    assert d['queue'] == '一般' and not d['ranked'] and d['map'] == 'Foo' and len(d['teams']) == 1
    a, b = d['teams'][0]['players']
    assert a['agent'] == '選角中' and not a['locked'] and a['name'] == ''
    assert b['agent'] == '傑特' and b['locked']


def test_match_summaries_and_recent_line():
    from cogs.valo_live_parse import match_summaries, recent_line
    d = {'matchInfo': {'queueID': 'hurm', 'gameStartMillis': 5, 'seasonId': 'act9'},
         'teams': [{'teamId': 'Red', 'won': True}, {'teamId': 'Blue', 'won': False}],
         'roundResults': [{'firstBloodPlayer': 'me', 'playerStats': [
             {'subject': 'me', 'damage': [{'damage': 50, 'headshots': 2, 'bodyshots': 1, 'legshots': 0},
                                          {'damage': 20, 'headshots': 0, 'bodyshots': 2, 'legshots': 1}]}]}],
         'players': [{'subject': 'me', 'teamId': 'Red', 'characterId': 'A1', 'roundDamage': [{'damage': 70}],
                      'stats': {'kills': 10, 'deaths': 5, 'assists': 2, 'score': 300, 'roundsPlayed': 1}},
                     {'subject': 'x', 'teamId': 'Blue', 'characterId': 'a2', 'stats': {'kills': 1, 'deaths': 9, 'assists': 0}}]}
    s = match_summaries(d, CONTENT)
    assert s['me'] == {'w': True, 'k': 10, 'd': 5, 'a': 2, 'score': 300, 'rounds': 1, 'dmg': 70, 'hs': 2, 'bs': 3, 'ls': 1,
                       'fb': 1, 'agent': '傑特', 'agentId': 'a1', 'queue': '團隊死鬥', 't': 5, 'season': 'act9'}
    assert s['x']['hs'] == 0 and s['x']['fb'] == 0
    assert s['x']['w'] is False
    dm = match_summaries({'matchInfo': {'queueID': 'deathmatch'}, 'teams': [{'teamId': 'me', 'won': True}],
                          'players': [{'subject': 'me', 'teamId': 'me', 'stats': {}}]}, CONTENT)
    assert dm['me']['w'] is None
    assert recent_line([s['me'], s['x'], dm['me']]) == '近3 1勝1敗 KDA 0.9'
    assert recent_line([dm['me']]) == '近1 KDA 0.0'
    assert recent_line([]) == ''


def test_eog_table():
    from cogs.valo_live_parse import eog_table
    d = {'matchInfo': {'queueID': 'hurm', 'mapId': '/Game/Maps/Ascent/Ascent', 'gameLengthMillis': 60000},
         'teams': [{'teamId': 'Red', 'won': True, 'roundsWon': 1, 'mvp': 'me'}, {'teamId': 'Blue', 'won': False, 'roundsWon': 0}],
         'players': [
             {'subject': 'x', 'teamId': 'Blue', 'gameName': 'X', 'tagLine': '1', 'characterId': 'a2',
              'stats': {'kills': 1, 'deaths': 9, 'assists': 0, 'score': 300}, 'roundDamage': [{'damage': 100}]},
             {'subject': 'me', 'teamId': 'Red', 'gameName': 'Me', 'tagLine': '0001', 'characterId': 'A1',
              'stats': {'kills': 10, 'deaths': 5, 'assists': 2, 'score': 2000, 'roundsPlayed': 20}, 'roundDamage': [{'damage': 50}, {'damage': 70}]}]}
    e = eog_table(d, 'me', {'x'}, CONTENT, {'me': {'GameName': 'Me2', 'TagLine': '1'}})
    assert e['map'] == '遺落境地' and e['queue'] == '團隊死鬥' and e['length'] == 60
    assert [t['mine'] for t in e['teams']] == [True, False] and e['teams'][0]['won'] is True
    me = e['teams'][0]['players'][0]
    assert me['rounds'] == 20
    assert me['mvp'] and me['dmg'] == 120 and me['name'] == 'Me2' and me['agent'] == '傑特' and me['agentId'] == 'a1'
    x = e['teams'][1]['players'][0]
    assert x['hidden'] and x['name'] == ''


def test_mode_record():
    from cogs.valo_live_parse import mode_record
    gs = [{'w': True, 'k': 10, 'd': 5, 'a': 2}, {'w': False, 'k': 2, 'd': 10, 'a': 1}, {'w': None, 'k': 1, 'd': 1, 'a': 1}]
    assert mode_record(gs, 'unrated') == {'q': '一般', 'n': 3, 'w': 1, 'l': 1, 'kda': 1.1}
    assert mode_record([], 'hurm') == {'q': '團隊死鬥', 'n': 0, 'w': 0, 'l': 0, 'kda': 0.0}


def _std_match():
    """兩回合的小對局：R0 me 殺兩人後 1v1 贏(Clutch)；R1 x 下包爆炸、me 被首殺。"""
    players = [
        {'subject': 'me', 'teamId': 'Red', 'gameName': 'Me', 'tagLine': '1', 'characterId': 'a1', 'partyId': 'p1',
         'stats': {'kills': 2, 'deaths': 1, 'assists': 0, 'score': 600, 'roundsPlayed': 2,
                   'abilityCasts': {'grenadeCasts': 1, 'ability1Casts': 2, 'ability2Casts': 3, 'ultimateCasts': 0}},
         'roundDamage': [{'damage': 300}], 'competitiveTier': 7, 'accountLevel': 10,
         'platformInfo': {'platformType': 'pc'}, 'behaviorFactors': {'afkRounds': 0}},
        {'subject': 'r2', 'teamId': 'Red', 'characterId': 'a2', 'partyId': 'p1', 'stats': {'kills': 0, 'deaths': 2, 'assists': 1, 'score': 50, 'roundsPlayed': 2}},
        {'subject': 'x', 'teamId': 'Blue', 'characterId': 'a2', 'partyId': 'p9', 'stats': {'kills': 2, 'deaths': 1, 'assists': 0, 'score': 500, 'roundsPlayed': 2}},
        {'subject': 'y', 'teamId': 'Blue', 'characterId': 'a1', 'partyId': 'p8', 'stats': {'kills': 1, 'deaths': 1, 'assists': 0, 'score': 200, 'roundsPlayed': 2}},
    ]
    kills = [
        {'round': 0, 'roundTime': 1000, 'killer': 'x', 'victim': 'r2', 'assistants': [], 'finishingDamage': {'damageType': 'Weapon', 'damageItem': 'W1'}},
        {'round': 0, 'roundTime': 2000, 'killer': 'me', 'victim': 'y', 'assistants': [], 'finishingDamage': {'damageType': 'Weapon', 'damageItem': 'W1'}},
        {'round': 0, 'roundTime': 3000, 'killer': 'me', 'victim': 'x', 'assistants': ['r2'], 'finishingDamage': {'damageType': 'Weapon', 'damageItem': 'W2'}},
        {'round': 1, 'roundTime': 500, 'killer': 'x', 'victim': 'me', 'assistants': [], 'finishingDamage': {'damageType': 'Ability', 'damageItem': ''}},
        {'round': 1, 'roundTime': 900, 'killer': 'y', 'victim': 'r2', 'assistants': [], 'finishingDamage': {'damageType': 'Weapon', 'damageItem': 'W1'}},
    ]
    def ps(pu, rnd, loadout):
        return {'subject': pu, 'kills': [k for k in kills if k['round'] == rnd and k['killer'] == pu],
                'damage': [{'receiver': 'z', 'headshots': 1, 'bodyshots': 1, 'legshots': 0, 'damage': 100}] if pu == 'me' else [],
                'economy': {'loadoutValue': loadout}}
    rounds = [
        {'roundNum': 0, 'winningTeam': 'Red', 'roundResultCode': 'Elimination', 'roundCeremony': 'CeremonyClutch',
         'firstBloodPlayer': 'x', 'plantSite': '', 'playerStats': [ps(p, 0, 3900) for p in ('me', 'r2', 'x', 'y')]},
        {'roundNum': 1, 'winningTeam': 'Blue', 'roundResultCode': 'Detonate', 'roundCeremony': 'CeremonyDefault',
         'firstBloodPlayer': 'x', 'plantSite': 'A', 'bombPlanter': 'x', 'playerStats': [ps(p, 1, 4100) for p in ('me', 'r2', 'x', 'y')]},
    ]
    return {'matchInfo': {'queueID': 'competitive', 'mapId': '/Game/Maps/Ascent/Ascent', 'gameLengthMillis': 120000,
                          'isRanked': True, 'gameVersion': 'release-13.06-shipping-18-5590001',
                          'gamePodId': 'aresriot.aws-ape1-prod.ap-gp-hongkong-1'},
            'teams': [{'teamId': 'Red', 'won': False, 'roundsWon': 1, 'mvp': 'me'}, {'teamId': 'Blue', 'won': True, 'roundsWon': 1, 'mvp': 'x'}],
            'players': players, 'roundResults': rounds, 'kills': kills, 'matchMvp': 'me'}


def test_eog_extras_full():
    from cogs.valo_live_parse import eog_table
    content = dict(CONTENT, weapons={'w1': '幻象', 'w2': '暴徒'}, ceremonies={'ceremonyclutch': 'Clutch 翻盤手'})
    e = eog_table(_std_match(), 'me', set(), content, None, {'me': {'earned': 17, 'after': 7, 'rrAfter': 55}}, {'r2': 12})
    me = next(p for t in e['teams'] for p in t['players'] if p['me'])
    r2 = next(p for t in e['teams'] for p in t['players'] if p['puuid'] == 'r2')
    assert me['tier'] == 7 and r2['tier'] == 12   # match-details 給的優先，0 才用對局中查到的
    assert me['hs'] == 50 and me['kast'] == 50 and me['fb'] == 0 and me['fd'] == 1
    assert me['clutch'] == ['1v2'] and me['mk'] == [0, 0, 0] and me['econ'] == 4000 and me['weapon'] == '幻象'
    assert me['casts'] == [1, 2, 3, 0] and me['party'] == 'A' and me['rr']['earned'] == 17 and me['level'] == 10
    x = next(p for t in e['teams'] for p in t['players'] if p['puuid'] == 'x')
    assert x['fb'] == 2 and x['plants'] == 1 and x['kast'] == 100 and x['party'] == ''
    assert e['server'] == '香港' and e['version'] == '13.06' and e['ranked'] and e['matchMvp'] == 'me' and e['myTeam'] == 'Red'
    r0, r1 = e['rounds']
    assert r0['ceremony'] == 'Clutch 翻盤手' and r0['score'] == [0, 1] and r0['fb'] == 'x'
    assert r1['code'] == 'Detonate' and r1['site'] == 'A' and r1['planter'] == 'x' and r1['ceremony'] == '' and r1['score'] == [1, 1]


def test_eog_single_round_mode_skips_round_stats():
    from cogs.valo_live_parse import eog_table
    d = _std_match()
    d['matchInfo']['queueID'] = 'hurm'
    d['roundResults'] = d['roundResults'][:1]
    d['teams'][0]['numPoints'] = 100
    e = eog_table(d, 'me', set(), CONTENT)
    me = next(p for t in e['teams'] for p in t['players'] if p['me'])
    assert me['kast'] is None and me['mk'] is None and me['clutch'] == [] and me['fb'] == 0
    assert e['teams'][0]['points'] == 100


def test_henrik_to_riot_roundtrip():
    from cogs.valo_live_parse import eog_table, henrik_to_riot
    hv = {
        'metadata': {'match_id': 'm1', 'map': {'id': 'map1', 'name': 'Ascent'}, 'game_version': 'release-13.06-shipping-18-1',
                     'game_length_in_ms': 60000, 'started_at': '2026-10-11T09:59:25.055Z', 'is_completed': True,
                     'queue': {'id': 'competitive'}, 'cluster': 'Hong Kong', 'mvp': {'puuid': 'me'}},
        'players': [
            {'puuid': 'me', 'name': 'Me', 'tag': '1', 'team_id': 'Red', 'party_id': 'p', 'agent': {'id': 'a1'},
             'stats': {'score': 300, 'kills': 1, 'deaths': 0, 'assists': 0}, 'ability_casts': {'grenade': 1, 'ability1': 0, 'ability2': 2, 'ultimate': 0},
             'tier': {'id': 7}, 'account_level': 5, 'platform': 'pc', 'behavior': {'afk_rounds': 0}},
            {'puuid': 'x', 'name': 'X', 'tag': '2', 'team_id': 'Blue', 'party_id': 'q', 'agent': {'id': 'a2'},
             'stats': {'score': 0, 'kills': 0, 'deaths': 1, 'assists': 0}, 'tier': {'id': 0}}],
        'teams': [{'team_id': 'Red', 'won': True, 'rounds': {'won': 1, 'lost': 0}, 'mvp': {'puuid': 'me'}},
                  {'team_id': 'Blue', 'won': False, 'rounds': {'won': 0, 'lost': 1}, 'mvp': {'puuid': 'x'}}],
        'rounds': [{'id': 0, 'result': 'Elimination', 'ceremony': 'Flawless', 'winning_team': 'Red',
                    'first_blood': {'puuid': 'me'}, 'plant': {}, 'defuse': None,
                    'stats': [{'player': {'puuid': 'me'}, 'economy': {'loadout_value': 3900, 'weapon': {'id': 'W1'}},
                               'damage_events': [{'player': {'puuid': 'x'}, 'headshots': 1, 'bodyshots': 0, 'legshots': 0, 'damage': 150}]},
                              {'player': {'puuid': 'x'}, 'economy': {'loadout_value': 800}, 'damage_events': []}]}],
        'kills': [{'round': 0, 'time_in_round_in_ms': 1000, 'killer': {'puuid': 'me'}, 'victim': {'puuid': 'x'}, 'assistants': [],
                   'weapon': {'id': 'W1', 'type': 'Weapon'}}],
    }
    content = dict(CONTENT, maps={'map1': '遺落境地'}, weapons={'w1': '幻象'}, ceremonies={'ceremonyflawless': '無懈可擊'})
    e = eog_table(henrik_to_riot(hv), 'me', set(), content)
    assert e['map'] == '遺落境地' and e['queue'] == '競技' and e['start'] == 1791712765 and e['server'] == 'Hong Kong' and e['ranked']
    me = e['teams'][0]['players'][0]
    assert me['name'] == 'Me' and me['k'] == 1 and me['dmg'] == 150 and me['hs'] == 100 and me['weapon'] == '幻象'
    assert me['econ'] == 3900 and me['casts'] == [1, 0, 2, 0] and me['tier'] == 7 and me['level'] == 5 and me['party'] == ''
    assert e['rounds'][0]['ceremony'] == '無懈可擊' and e['rounds'][0]['fb'] == 'me' and e['teams'][1]['players'][0]['agent'] == '菲尼克斯'


def test_competitive_stops_at_previous_act():
    """本 act 競技：對戰紀錄新的在前，碰到上個 act 的就停、不往後看。"""
    import asyncio
    from cogs.valo_live import RiotLink
    link = RiotLink.__new__(RiotLink)
    link._sem = asyncio.Semaphore(2)
    calls = []

    async def pd(path):
        calls.append(path)
        return {'History': [{'MatchID': 'm1'}, {'MatchID': 'm2'}, {'MatchID': 'm3'}]}
    link.pd = pd
    summs = {'m1': {'me': {'season': 'cur', 'k': 1}}, 'm2': {'me': {'season': 'old', 'k': 2}},
             'm3': {'me': {'season': 'cur', 'k': 3}}}

    async def ms(mid, content):
        return summs[mid]
    link.match_summary = ms
    out = asyncio.run(link.competitive('me', {}, 'cur', n=5))
    assert out == [{'season': 'cur', 'k': 1}]
    assert 'queue=competitive' in calls[0] and 'endIndex=6' in calls[0]
    summs['m2'] = None
    assert asyncio.run(link.competitive('me', {}, 'cur', n=5)) is None


def test_eog_extras_spike_suicide_and_teamkill_not_multikill():
    """Riot 把被自己隊的包炸死記成 killer=victim 的 kill；它和 teamkill 都不能算進多殺/KAST，最常用槍看每回合手上的槍。"""
    from cogs.valo_live_parse import eog_extras
    content = dict(CONTENT, weapons={'w1': '制式手槍', 'w2': '暴徒'}, ceremonies={})
    players = [{'subject': p, 'teamId': t} for p, t in (('me', 'Red'), ('r2', 'Red'), ('x', 'Blue'), ('y', 'Blue'), ('z', 'Blue'))]
    kills = [
        {'round': 0, 'roundTime': 1000, 'killer': 'me', 'victim': 'x', 'assistants': [], 'finishingDamage': {'damageType': 'Weapon', 'damageItem': 'W1'}},
        {'round': 0, 'roundTime': 2000, 'killer': 'me', 'victim': 'y', 'assistants': [], 'finishingDamage': {'damageType': 'Weapon', 'damageItem': 'W1'}},
        {'round': 0, 'roundTime': 3000, 'killer': 'me', 'victim': 'r2', 'assistants': [], 'finishingDamage': {'damageType': 'Weapon', 'damageItem': 'W1'}},
        {'round': 0, 'roundTime': 9000, 'killer': 'me', 'victim': 'me', 'assistants': [], 'finishingDamage': {'damageType': 'Bomb', 'damageItem': ''}},
        {'round': 1, 'roundTime': 1000, 'killer': 'z', 'victim': 'me', 'assistants': [], 'finishingDamage': {'damageType': 'Weapon', 'damageItem': 'W2'}},
    ]
    def ps(pu, rnd, weapon):
        return {'subject': pu, 'kills': [k for k in kills if k['round'] == rnd and k['killer'] == pu], 'damage': [],
                'economy': {'loadoutValue': 3000, 'weapon': weapon}}
    rounds = [{'roundNum': n, 'winningTeam': 'Blue', 'roundResultCode': 'Detonate', 'roundCeremony': '', 'firstBloodPlayer': 'me',
               'playerStats': [ps(p, n, 'W2' if n else 'W1') for p in ('me', 'r2', 'x', 'y', 'z')]} for n in (0, 1, 2)]
    e = eog_extras({'matchInfo': {'queueID': 'competitive'}, 'players': players, 'roundResults': rounds, 'kills': kills}, content)
    me = e['players']['me']
    assert me['mk'] == [0, 0, 0]          # 2 殺 + teamkill + 自爆 → 不是 3K
    assert me['kast'] == 67               # R1 有殺、R2 死掉沒人換、R3 存活
    assert me['weapon'] == '暴徒'          # 2 回合拿暴徒 > 1 回合制式手槍，雖然殺人全用制式手槍


def test_spike_self_damage_not_in_adr_hs_kast():
    """被自己隊的包炸死：Riot 記 killer=victim + 一筆 ~1000 打自己的傷害。不能灌 ADR/HS%，也不能算成「被換掉」拿 KAST、不能算被首殺。"""
    from cogs.valo_live_parse import eog_table, match_summaries
    players = [{'subject': p, 'teamId': t, 'stats': {'kills': 0, 'deaths': 0, 'assists': 0, 'score': 0, 'roundsPlayed': 2},
                'roundDamage': [{'damage': 9999}]}
               for p, t in (('me', 'Red'), ('r2', 'Red'), ('x', 'Blue'))]
    kills = [
        {'round': 0, 'roundTime': 5000, 'killer': 'me', 'victim': 'me', 'assistants': [], 'finishingDamage': {'damageType': 'Bomb', 'damageItem': ''}},
        {'round': 1, 'roundTime': 1000, 'killer': 'x', 'victim': 'r2', 'assistants': [], 'finishingDamage': {'damageType': 'Weapon', 'damageItem': 'W1'}},
    ]
    def ps(pu, rnd):
        dmg = []
        if pu == 'me' and rnd == 0:
            dmg = [{'receiver': 'x', 'damage': 150, 'headshots': 1, 'bodyshots': 0, 'legshots': 0},
                   {'receiver': 'me', 'damage': 999, 'headshots': 0, 'bodyshots': 0, 'legshots': 0},
                   {'receiver': 'r2', 'damage': 20, 'headshots': 0, 'bodyshots': 3, 'legshots': 0}]
        return {'subject': pu, 'kills': [k for k in kills if k['round'] == rnd and k['killer'] == pu], 'damage': dmg,
                'economy': {'loadoutValue': 1000}}
    rounds = [{'roundNum': n, 'winningTeam': 'Blue', 'roundResultCode': 'Detonate', 'roundCeremony': '',
               'playerStats': [ps(p, n) for p in ('me', 'r2', 'x')]} for n in (0, 1)]
    d = {'matchInfo': {'queueID': 'competitive'}, 'players': players, 'roundResults': rounds, 'kills': kills,
         'teams': [{'teamId': 'Red', 'won': False}, {'teamId': 'Blue', 'won': True}]}
    e = eog_table(d, 'me', set(), CONTENT)
    me = next(p for t in e['teams'] for p in t['players'] if p['me'])
    assert me['dmg'] == 150 and me['hs'] == 100       # 打自己 999、打隊友 20(3 發身體)都不算
    assert me['kast'] == 50                            # R1 被包炸死不算被換；R2 存活
    assert me['fd'] == 0                               # R1 唯一的「擊殺」是自爆，不算被首殺
    assert match_summaries(d, CONTENT)['me']['dmg'] == 150


def test_stored_comp_from_henrik():
    """HenrikDev stored-matches → 跟 match_summaries 同格式；沒有首殺(None)；碰到上個 act 就停；None 原樣回。"""
    from cogs.valo_live_parse import stored_comp

    def m(season, team, red, blue, k=10):
        return {'meta': {'season': {'id': season}, 'started_at': '2026-10-15T13:41:43.199Z'},
                'stats': {'team': team, 'character': {'id': 'A1', 'name': 'Jett'}, 'score': 3000, 'kills': k,
                          'deaths': 8, 'assists': 3, 'shots': {'head': 6, 'body': 30, 'leg': 4},
                          'damage': {'made': 2000, 'received': 1500}},
                'teams': {'red': red, 'blue': blue}}
    out = stored_comp([m(SEASON, 'Blue', 11, 13), m(SEASON, 'Red', 11, 13, k=5), m('s-old', 'Red', 13, 2),
                       m(SEASON, 'Red', 13, 0)], SEASON, CONTENT)
    assert [g['w'] for g in out] == [True, False]
    g = out[0]
    assert (g['k'], g['d'], g['a'], g['rounds'], g['dmg'], g['score']) == (10, 8, 3, 24, 2000, 3000)
    assert (g['hs'], g['bs'], g['ls'], g['fb']) == (6, 30, 4, None)
    assert g['agent'] == '傑特' and g['queue'] == '競技' and g['t'] == 1792071703199
    assert stored_comp(None, SEASON, CONTENT) is None
    assert stored_comp([], SEASON, CONTENT) == []
