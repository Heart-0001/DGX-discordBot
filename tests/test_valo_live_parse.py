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
    d = {'matchInfo': {'queueID': 'hurm', 'gameStartMillis': 5},
         'teams': [{'teamId': 'Red', 'won': True}, {'teamId': 'Blue', 'won': False}],
         'roundResults': [{'firstBloodPlayer': 'me', 'playerStats': [
             {'subject': 'me', 'damage': [{'damage': 50, 'headshots': 2, 'bodyshots': 1, 'legshots': 0},
                                          {'damage': 20, 'headshots': 0, 'bodyshots': 2, 'legshots': 1}]}]}],
         'players': [{'subject': 'me', 'teamId': 'Red', 'characterId': 'A1', 'roundDamage': [{'damage': 70}],
                      'stats': {'kills': 10, 'deaths': 5, 'assists': 2, 'score': 300, 'roundsPlayed': 1}},
                     {'subject': 'x', 'teamId': 'Blue', 'characterId': 'a2', 'stats': {'kills': 1, 'deaths': 9, 'assists': 0}}]}
    s = match_summaries(d, CONTENT)
    assert s['me'] == {'w': True, 'k': 10, 'd': 5, 'a': 2, 'score': 300, 'rounds': 1, 'dmg': 70, 'hs': 2, 'bs': 3, 'ls': 1,
                       'fb': 1, 'agent': '傑特', 'agentId': 'a1', 'queue': '團隊死鬥', 't': 5}
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
