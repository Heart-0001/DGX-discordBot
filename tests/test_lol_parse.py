"""cogs/lol_parse.py 的解析測試(資料是手造的，不放真實玩家資訊)。"""
from cogs.lol_parse import parse_champselect, parse_eog, parse_live, parse_profile, queue_name

STATIC = {'champKeyToId': {'skarner': 72, 'garen': 86}, 'champIdToKey': {'72': 'Skarner', '86': 'Garen'},
          'spellKeys': {'SummonerFlash': '閃現'}}


def _player(name, tag, team, champ_key, champ_name, kills=0, dead=False):
    return {'riotId': f'{name}#{tag}' if name else '#', 'riotIdGameName': name, 'riotIdTagLine': tag,
            'summonerName': name or champ_name, 'team': team, 'championName': champ_name,
            'rawChampionName': f'game_character_displayname_{champ_key}', 'level': 10,
            'isDead': dead, 'respawnTimer': 7.6 if dead else 0, 'skinName': '',
            'scores': {'kills': kills, 'deaths': 1, 'assists': 2, 'creepScore': 30, 'wardScore': 1.4},
            'items': [{'itemID': 3084, 'displayName': '雄心之鋼', 'slot': 1, 'count': 1},
                      {'itemID': 1001, 'displayName': '鞋子', 'slot': 0, 'count': 1}],
            'summonerSpells': {'summonerSpellOne': {'displayName': '閃現',
                                                    'rawDisplayName': 'GeneratedTip_SummonerSpell_SummonerFlash_DisplayName'},
                               'summonerSpellTwo': {'displayName': '某增幅',
                                                    'rawDisplayName': 'GeneratedTip_Spell_Augment_X_DisplayName'}}}


def test_live_basic():
    d = {'activePlayer': {'riotId': 'Me#1', 'currentGold': 123.4, 'level': 10,
                          'championStats': {'currentHealth': 50.6, 'maxHealth': 100, 'resourceValue': 10,
                                            'resourceMax': 20, 'resourceType': 'MANA'}},
         'allPlayers': [_player('Foe', '2', 'ORDER', 'Garen', '蓋倫', kills=3),
                        _player('', '', 'ORDER', 'Garen', '蓋倫'),
                        _player('Me', '1', 'CHAOS', 'Skarner', '史加納', kills=5, dead=True)],
         'events': {'Events': [{'EventName': 'GameStart', 'EventTime': 0.1},
                               {'EventName': 'ChampionKill', 'EventTime': 61.2, 'KillerName': 'Me',
                                'VictimName': 'Foe', 'Assisters': []},
                               {'EventName': 'TurretKilled', 'EventTime': 70, 'KillerName': 'Foe',
                                'TurretKilled': 'Turret_TChaos_L1_P2_1'}]},
         'gameData': {'gameTime': 75.4, 'gameMode': 'ARAM'}}
    live = parse_live(d, STATIC, {'me#1': 'pu-me'})
    me = live['players'][0]
    assert me['me'] and me['ally'] and me['puuid'] == 'pu-me' and me['dead'] and me['respawn'] == 8
    assert me['champ'] == {'id': 72, 'key': 'Skarner', 'name': '史加納'}
    assert [i['slot'] for i in me['items']] == [0, 1]
    assert me['spells'][0]['key'] == 'SummonerFlash' and me['spells'][1]['key'] == ''
    hidden = live['players'][2]
    assert hidden['hidden'] and hidden['name'] == '' and hidden['puuid'] is None
    assert live['kills'] == {'ally': 5, 'enemy': 3}
    assert live['self']['hp'] == 51 and live['self']['gold'] == 123
    assert live['events'][0]['kind'] == 'turret' and live['events'][0]['side'] == '我方'
    assert live['events'][1]['text'] == '史加納(我方) 擊殺 蓋倫(敵方)'


def test_champselect_hidden_and_me():
    sess = {'localPlayerCellId': 1, 'benchEnabled': True, 'benchChampions': [{'championId': 86}],
            'myTeam': [{'cellId': 0, 'puuid': 'a', 'gameName': 'X', 'tagLine': '1', 'championId': 72,
                        'nameVisibilityType': 'VISIBLE'},
                       {'cellId': 1, 'puuid': 'b', 'gameName': 'Me', 'tagLine': '2', 'championId': 0,
                        'championPickIntent': 86, 'nameVisibilityType': 'VISIBLE'},
                       {'cellId': 2, 'puuid': '', 'gameName': '', 'nameVisibilityType': 'HIDDEN'}],
            'theirTeam': [{}] * 5}
    cs = parse_champselect(sess, 'b')
    assert cs['bench'] == [86] and cs['enemyCount'] == 5
    assert cs['team'][1]['me'] and cs['team'][1]['championId'] == 86
    assert cs['team'][2]['hidden'] and cs['team'][2]['puuid'] is None


def test_eog_augments_and_order():
    def p(local, aug):
        return {'isLocalPlayer': local, 'championId': 72, 'championName': '史加納', 'riotIdGameName': 'n',
                'riotIdTagLine': 't', 'items': [3084, 0], 'spell1Id': 4, 'spell2Id': 32,
                'stats': {'CHAMPIONS_KILLED': 1, 'NUM_DEATHS': 2, 'ASSISTS': 3, 'MINIONS_KILLED': 4,
                          'NEUTRAL_MINIONS_KILLED': 1, 'PLAYER_AUGMENT_1': aug, 'PLAYER_AUGMENT_2': 0,
                          'TOTAL_DAMAGE_DEALT_TO_CHAMPIONS': 999, 'LEVEL': 18}}
    d = {'gameId': 1, 'gameLength': 600, 'gameMode': 'KIWI',
         'teams': [{'isPlayerTeam': False, 'isWinningTeam': True, 'players': [p(False, 2001)]},
                   {'isPlayerTeam': True, 'isWinningTeam': False, 'players': [p(True, 2115)]}]}
    e = parse_eog(d, STATIC)
    assert e['win'] is False and e['teams'][0]['ally']
    pl = e['teams'][0]['players'][0]
    assert pl['augments'] == [2115] and pl['items'] == [3084] and pl['cs'] == 5 and pl['champ']['key'] == 'Skarner'


def test_profile_recent_in_queue():
    hist = {'games': {'games': [{'queueId': 2400, 'participants': [{'stats': {'win': True}}]},
                                {'queueId': 2400, 'participants': [{'stats': {'win': False}}]},
                                {'queueId': 420, 'participants': [{'stats': {'win': True}}]}]}}
    ranked = {'queues': [{'queueType': 'RANKED_SOLO_5x5', 'tier': 'MASTER', 'division': 'I',
                          'leaguePoints': 50, 'wins': 1, 'losses': 2},
                         {'queueType': 'RANKED_FLEX_SR', 'tier': '', 'division': 'NA'}]}
    pr = parse_profile({'gameName': 'a', 'tagLine': 'b', 'summonerLevel': 5}, ranked, hist, 2400)
    assert pr['recent'] == {'queueId': 2400, 'w': 1, 'l': 1} and pr['recentAll'] == {'w': 2, 'l': 1}
    assert pr['solo']['division'] == '' and pr['flex'] is None
    assert parse_profile(None, None, None, 2400)['recent'] is None
    assert queue_name(2400) == '隨機單中：大混戰' and queue_name(9999, 'X') == 'X'


def test_rate_limiter_spaces_calls():
    import asyncio, time
    from cogs.riot_api import RateLimiter, league_to_ranks

    async def run():
        rl = RateLimiter([(10, 0.5)])     # 九成 → 0.5 秒內最多 9 次
        t0 = time.monotonic()
        for _ in range(12):
            await rl.acquire()
        return time.monotonic() - t0
    assert asyncio.run(run()) >= 0.45
    r = league_to_ranks([{'queueType': 'RANKED_SOLO_5x5', 'tier': 'GOLD', 'rank': 'II', 'leaguePoints': 3,
                          'wins': 4, 'losses': 5}])
    assert r['solo'] == {'tier': 'GOLD', 'division': 'II', 'lp': 3, 'w': 4, 'l': 5} and r['flex'] is None


def test_valo_summary_team_and_deathmatch():
    from cogs.valo_parse import detail, summarize

    def pl(pu, team, k, score=300, dealt=200):
        return {'puuid': pu, 'name': pu, 'tag': 't', 'team_id': team, 'agent': {'id': 'a1', 'name': 'Jett'},
                'stats': {'kills': k, 'deaths': 1, 'assists': 0, 'score': score, 'headshots': 1, 'bodyshots': 3,
                          'legshots': 0, 'damage': {'dealt': dealt}}, 'tier': {'id': 0}}
    content = {'agents': {'a1': {'name': '捷特'}}, 'maps': {}, 'tiers': {}}
    team = {'metadata': {'match_id': 'm1', 'queue': {'id': 'competitive', 'mode_type': 'Standard'},
                         'map': {'id': 'x', 'name': 'Ascent'}, 'started_at': '2026-10-08T15:14:07.996Z'},
            'players': [pl('me', 'Blue', 5), pl('foe', 'Red', 2)],
            'teams': [{'team_id': 'Blue', 'won': True, 'rounds': {'won': 2, 'lost': 0}, 'mvp': {'puuid': 'me'}},
                      {'team_id': 'Red', 'won': False, 'rounds': {'won': 0, 'lost': 2}}],
            'rounds': [{'winning_team': 'Blue', 'first_blood': {'puuid': 'me'}}, {'winning_team': 'Blue'}]}
    s = summarize(team, 'me', content)
    assert s['won'] and s['score'] == '2:0' and s['acs'] == 150 and s['adr'] == 100 and s['hs'] == 25
    assert s['agent'] == '捷特' and s['mvp'] and s['fb'] == 1 and not s['ffa']
    d = detail(team, 'me', content)
    assert d['strip'] == '🟦🟦' and d['teams'][0]['mine']
    dm = {'metadata': {'match_id': 'm2', 'queue': {'id': 'deathmatch', 'mode_type': 'Deathmatch'}, 'map': {}},
          'players': [pl('me', 'me', 16), pl('foe', 'foe', 40)],
          'teams': [{'team_id': 'me', 'placement': 3, 'won': False, 'rounds': {'won': 16, 'lost': 0}},
                    {'team_id': 'foe', 'placement': 1, 'won': True, 'rounds': {'won': 40, 'lost': 0}}],
          'rounds': [{}]}
    s = summarize(dm, 'me', content)
    assert s['ffa'] and s['score'] == '第 3 名' and s['acs'] is None and not s['won']
    d = detail(dm, 'me', content)
    assert d['strip'] == '' and len(d['teams']) == 1 and d['teams'][0]['players'][0]['k'] == 40


def test_valo_single_round_mode_has_no_acs():
    from cogs.valo_parse import detail, summarize
    pl = lambda pu, team, k: {'puuid': pu, 'name': pu, 'tag': 't', 'team_id': team, 'agent': {'id': 'a'},
                              'stats': {'kills': k, 'deaths': 1, 'assists': 0, 'score': 5000, 'headshots': 1,
                                        'bodyshots': 1, 'legshots': 0, 'damage': {'dealt': 3000}}}
    tdm = {'metadata': {'match_id': 'm', 'queue': {'id': 'hurm', 'mode_type': 'TeamDeathmatch'}, 'map': {}},
           'players': [pl('me', 'Blue', 14), pl('x', 'Red', 30)],
           'teams': [{'team_id': 'Blue', 'won': False, 'rounds': {'won': 95, 'lost': 100}},
                     {'team_id': 'Red', 'won': True, 'rounds': {'won': 100, 'lost': 95}}],
           'rounds': [{'winning_team': 'Red'}]}
    s = summarize(tdm, 'me', {})
    assert s['acs'] is None and s['adr'] is None and s['score'] == '95:100' and s['won'] is False
    d = detail(tdm, 'me', {})
    assert d['strip'] == '' and d['teams'][0]['rounds'] == {'won': 95, 'lost': 100}
