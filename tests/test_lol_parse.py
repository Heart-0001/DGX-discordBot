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


ITEMS = {
    '1036': {'name': '長劍', 'total': 350, 'buy': True, 'from': [], 'into': ['3134', '3071'], 'tags': ['Damage'], 'maps': ['11', '12'], 'special': False},
    '1028': {'name': '紅水晶', 'total': 400, 'buy': True, 'from': [], 'into': ['3071'], 'tags': ['Health'], 'maps': ['11', '12'], 'special': False},
    '3134': {'name': '殘暴之力', 'total': 1100, 'buy': True, 'from': ['1036', '1036'], 'into': ['3142'], 'tags': ['Damage'], 'maps': ['11', '12'], 'special': False},
    '3142': {'name': '妖夢鬼刀', 'total': 2800, 'buy': True, 'from': ['3134', '1036'], 'into': [], 'tags': ['Damage', 'ArmorPenetration'], 'maps': ['11', '12'], 'special': False},
    '3071': {'name': '黑色切割者', 'total': 3000, 'buy': True, 'from': ['1036', '1028'], 'into': [], 'tags': ['Damage', 'Health'], 'maps': ['11', '12'], 'special': False},
    '773142': {'name': '妖夢鬼刀', 'total': 2800, 'buy': True, 'from': ['3134'], 'into': [], 'tags': ['Damage'], 'maps': ['12'], 'special': False},
}


def test_predict_next_prefers_most_complete():
    from cogs.lol_parse import item_value, predict_next
    static = {'items': ITEMS, 'champInfo': {'238': {'tags': ['Assassin'], 'ad': 9, 'ap': 1, 'tank': 2}}}
    # 殘暴之力 + 長劍 → 妖夢鬼刀已經湊齊 1450/2800；黑色切割者只有長劍
    got = predict_next(238, [3134, 1036], static, 12)
    assert got[0]['id'] == 3142 and got[0]['left'] == 1350 and got[1]['id'] == 3071
    assert all(x['id'] < 10000 for x in got)            # 其他模式的複製品不算
    assert predict_next(238, [3142], static, 12) == []   # 只有大裝、沒零件 → 不猜
    assert item_value({'itemID': 1036, 'count': 2}, ITEMS) == 700
    assert item_value({'itemID': 9999, 'price': 50}, ITEMS) == 50


OPGG_TEXT = ('class LolGetChampionAnalysis: data\nclass Data: core_items,boots,last_items,fourth_items,fifth_items,counters_meta\n'
             'class CoreItems: ids,play,win,pick_rate\n\n'
             'LolGetChampionAnalysis(Data(CoreItems([3134,123071],692,351,0.11),Boots([3158],4365,0.68),'
             '[CoreItems([3071],429,237,0.19)],[CoreItems([3142],1160,590,0.23)],[CoreItems([3071],227,127,0.1)],'
             'CountersMeta("x")))')


def test_opgg_build_and_popular_prediction():
    from cogs.lol_parse import parse_opgg_build, predict_next
    b = parse_opgg_build(OPGG_TEXT)
    assert b['core'] == ['3134', '3071']             # 123071 → 3071
    assert b['pop']['3071'] > b['pop']['3142'] > 0   # 核心裝 > 第 4 件
    static = {'items': ITEMS, 'champInfo': {}}
    # 手上什麼都沒有也能猜(照常出裝)；黑色切割者是核心裝
    got = predict_next(238, [], static, 12, build=b)
    assert got[0]['id'] == 3071 and got[0]['popular']
    assert parse_opgg_build('error') is None


def test_recent_games_newest_first_and_remake():
    from cogs.lol_parse import recent_games
    g = lambda t, win, dur, q=2400: {'gameCreation': t * 1000, 'gameDuration': dur, 'queueId': q, 'gameMode': 'KIWI',
                                      'participants': [{'championId': 86, 'stats': {'win': win, 'kills': 1, 'deaths': 2, 'assists': 3}}]}
    out = recent_games([g(100, True, 900), g(300, False, 200, 420)] + [g(i, True, 900) for i in range(20)])
    assert len(out) == 10
    assert out[0]['t'] == 300 and out[0]['remake'] and out[0]['q'] == '單雙'
    assert out[1]['t'] == 100 and out[1]['w'] and out[1]['q'] == '大混戰'


def test_live_puuid_falls_back_to_session_champion():
    from cogs.lol_parse import session_champ_puuids
    sess = {'gameData': {'teamOne': [{'puuid': 'pu-garen', 'championId': 86}],
                         'teamTwo': [{'championId': 72}]}}   # 被隱藏的人沒有 puuid
    m = session_champ_puuids(sess)
    assert m == {'ORDER:86': 'pu-garen'}
    d = {'activePlayer': {'riotId': 'Me#1'},
         'allPlayers': [_player('Foe', '2', 'ORDER', 'Garen', '蓋倫'), _player('Me', '1', 'CHAOS', 'Skarner', '史加納')],
         'events': {'Events': []}, 'gameData': {'gameTime': 1}}
    live = parse_live(d, STATIC, {}, None, m)
    by = {p['champ']['key']: p['puuid'] for p in live['players']}
    assert by == {'Garen': 'pu-garen', 'Skarner': None}


def test_parts_in_hand_beat_popular_core():
    # 手上已有 A 的兩個零件，常出核心裝是 B(一個零件都沒有) → 應該猜 A
    from cogs.lol_parse import predict_next
    items = dict(ITEMS)
    items['9001'] = {'name': '火炮', 'total': 2650, 'buy': True, 'from': ['9002'], 'into': [], 'tags': ['Damage'],
                     'maps': ['12'], 'special': False}
    items['9002'] = {'name': '雙刀', 'total': 1000, 'buy': True, 'from': [], 'into': ['9001'], 'tags': [], 'maps': ['12'], 'special': False}
    build = {'core': ['9001'], 'pop': {'9001': 1.3, '3071': 0.2}}
    got = predict_next(1, [1036, 1028], {'items': items}, 12, build=build)
    assert got[0]['id'] == 3071 and got[1]['id'] == 9001


def test_eog_extra_fields_and_enrich_from_history():
    from cogs.lol_parse import enrich_eog, eog_missing, parse_eog
    d = {'gameId': 1, 'gameLength': 900, 'teams': [{'isPlayerTeam': True, 'isWinningTeam': True, 'players': [
        {'puuid': 'pu-a', 'championId': 86, 'isLocalPlayer': True,
         'stats': {'TOTAL_DAMAGE_DEALT_TO_CHAMPIONS': 100, 'TOTAL_DAMAGE_SHIELDED_ON_TEAMMATES': 30}},
        {'puuid': 'pu-b', 'championId': 72, 'stats': {}}]}]}
    e = parse_eog(d, STATIC)
    a, b = e['teams'][0]['players']
    assert a['dmg'] == 100 and a['shield'] == 30 and a['cc'] is None and eog_missing(e)
    game = {'participantIdentities': [{'participantId': 1, 'player': {'puuid': 'pu-a'}}],
            'participants': [{'participantId': 1, 'championId': 86, 'stats': {'timeCCingOthers': 12, 'totalDamageDealtToChampions': 999}},
                             {'participantId': 2, 'championId': 72, 'stats': {'timeCCingOthers': 5}}]}
    enrich_eog(e, game)
    assert a['cc'] == 12 and a['dmg'] == 100      # 已有的不蓋掉
    assert b['cc'] == 5                           # puuid 對不到 → 用英雄對


def test_recommend_only_from_build_and_explains_counters():
    from cogs.lol_parse import enemy_profile, recommend
    items = dict(ITEMS)
    items['3075'] = {'name': '荊棘之甲', 'total': 2450, 'buy': True, 'from': ['1028'], 'into': [], 'tags': ['Armor', 'Health'],
                     'maps': ['12'], 'special': False, 'gw': True}
    items['6673'] = {'name': '吸血刀', 'total': 3000, 'buy': True, 'from': ['1036'], 'into': [], 'tags': ['Damage', 'LifeSteal'],
                     'maps': ['12'], 'special': False}
    static = {'items': items, 'champInfo': {}}
    # 敵方：兩件吸血刀 + 一件黑切
    raw = [{'championName': '某A', 'items': [{'itemID': 6673}, {'itemID': 6673}]},
           {'championName': '某B', 'items': [{'itemID': 3071}]}]
    prof = enemy_profile(raw, [1, 2], static)
    assert prof['src'] == '依敵方裝備' and prof['phys'] == 100 and prof['healPct'] == 67
    build = {'pop': {'3075': .3, '3142': 1.0}, 'stats': {
        '3075': {'slot': '第 5 件', 'rate': .2, 'play': 100, 'wr': .55},
        '3142': {'slot': '核心裝第 1 件', 'rate': .1, 'play': 100, 'wr': .5}}}
    got = recommend(54, [], build, prof, static, 12)
    assert [r['id'] for r in got] == [3075, 3142]            # 荊棘：常出 + 物理 100% + 重創 → 排第一
    assert got[0]['counter'] and any('重創' in x for x in got[0]['reasons']) and any('護甲' in x for x in got[0]['reasons'])
    assert all(r['id'] != 3071 for r in got)                 # 不在常出清單的不推薦
    assert recommend(54, [], None, prof, static, 12) == []   # 沒有 op.gg 資料就不推薦(不瞎掰)
