from cogs.lol_live_parse import (fmt_length, live_text, match_live_players, parse_spectator, rank_text,
                                 recent_text)

STATIC = {
    'champNames': {'58': '雷尼克頓', '267': '娜米', '99': '拉克絲'},
    'spellKeys': {'SummonerFlash': '閃現', 'SummonerDot': '點燃', 'SummonerTeleport': '傳送'},
    'spellIdToKey': {'4': 'SummonerFlash', '14': 'SummonerDot', '12': 'SummonerTeleport'},
    'perkNames': {'8010': '征服者', '8112': '死亡電刑'},
    'perkStyles': {'8000': '精密', '8400': '堅決'},
}


def participant(team, cid, rid, puuid, s1=14, s2=4, perk=8010, bot=False):
    return {'puuid': puuid, 'teamId': team, 'spell1Id': s1, 'spell2Id': s2, 'championId': cid, 'riotId': rid,
            'bot': bot, 'profileIconId': 1, 'perks': {'perkIds': [perk, 1, 2], 'perkStyle': 8000, 'perkSubStyle': 8400}}


GAME = {
    'gameId': 1, 'mapId': 11, 'gameMode': 'CLASSIC', 'gameType': 'MATCHED', 'gameQueueConfigId': 420,
    'platformId': 'TW2', 'gameStartTime': 1791688240936, 'gameLength': 350,
    'bannedChampions': [{'championId': -1, 'teamId': 100, 'pickTurn': 1}, {'championId': 99, 'teamId': 200, 'pickTurn': 6}],
    'participants': [participant(100, 58, 'A#1', 'p1'), participant(100, 267, 'B#2', 'p2', perk=8112),
                     participant(200, 99, 'C#3', 'p3', s1=12), participant(200, 58, '', 'p4', bot=True)],
}


def test_parse_spectator_teams_bans_and_names():
    d = parse_spectator(GAME, STATIC)
    assert d['queue'] and d['map'] == '召喚峽谷' and d['length'] == 350 and d['started'] is not None
    assert d['bans'] == {100: ['無'], 200: ['拉克絲']}
    assert [t['id'] for t in d['teams']] == [100, 200]
    p1 = d['teams'][0]['players'][0]
    assert p1['champion'] == '雷尼克頓' and p1['spells'] == ['點燃', '閃現'] and p1['keystone'] == '征服者'
    assert p1['style'] == '精密' and p1['subStyle'] == '堅決'
    assert d['teams'][1]['players'][1]['bot'] is True


def test_parse_spectator_unknown_ids_and_zero_start():
    g = dict(GAME, gameStartTime=0, participants=[participant(100, 9999, 'X#1', 'p', s1=777, perk=5)])
    d = parse_spectator(g, STATIC)
    p = d['teams'][0]['players'][0]
    assert d['started'] is None and p['champion'] == '9999' and p['spells'] == ['技能777', '閃現'] and p['keystone'] == ''
    assert parse_spectator({}, {})['teams'] == []


def test_rank_text():
    assert rank_text(None) == '未排名'
    assert rank_text({'tier': 'GOLD', 'division': 'II', 'lp': 40, 'w': 30, 'l': 20}) == '金牌 II 40LP（30勝20敗 60%）'
    assert rank_text({'tier': 'MASTER', 'division': '', 'lp': 120, 'w': 1, 'l': 0}).startswith('大師 120LP')


def match(puuid, win, k, d, a, cid, dur=1500):
    return {'info': {'gameDuration': dur, 'participants': [
        {'puuid': puuid, 'win': win, 'kills': k, 'deaths': d, 'assists': a, 'championId': cid}]}}


def test_recent_text_skips_remakes_and_missing():
    ms = [match('p', True, 5, 2, 10, 58), match('p', False, 1, 4, 2, 267), match('p', True, 0, 0, 0, 99, dur=200), None]
    assert recent_text(ms, 'p', STATIC['champNames']) == '近2 1勝1敗 KDA 3.0（雷尼克頓, 娜米）'
    assert recent_text([], 'p', {}) == ''


def test_live_text_and_matching():
    live = {'allPlayers': [
        {'riotIdGameName': 'a', 'riotIdTagLine': '1', 'level': 7, 'isDead': True, 'respawnTimer': 12.4,
         'scores': {'kills': 2, 'deaths': 1, 'assists': 3, 'creepScore': 40},
         'items': [{'itemID': 1055}, {'itemID': 3340}]},
        {'summonerName': 'B', 'level': 3, 'scores': {}, 'items': []},
    ]}
    players = [{'puuid': 'p1', 'riotId': 'A#1'}, {'puuid': 'p2', 'riotId': 'B#2'}, {'puuid': 'p3', 'riotId': 'C#3'}]
    m = match_live_players(live, players)
    assert set(m) == {'p1', 'p2'}
    assert live_text(m['p1'], {'1055': {'name': '多蘭之刃'}}) == 'Lv7 2/1/3 CS 40（死亡，12 秒）　多蘭之刃'
    assert live_text(m['p2'], {}) == 'Lv3 0/0/0 CS 0'
    assert live_text(None, {}) == '' and match_live_players(None, players) == {}


def test_fmt_length():
    assert fmt_length(350) == '5 分 50 秒'
