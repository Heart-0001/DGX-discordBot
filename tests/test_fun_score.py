from datetime import datetime, timezone

from games.fun_score import cap_grant, lol_score, parts_text, tickets_for, today, valo_score


def _lol(me: dict, mates=None, enemies=None, queue=420, dur=1800, mode='CLASSIC'):
    me = {'puuid': 'ME', 'teamId': 100, 'championId': 103, 'championName': 'Ahri', **me}
    ps = [me] + [{'puuid': f'm{i}', 'teamId': 100, **p} for i, p in enumerate(mates or [])] \
        + [{'puuid': f'e{i}', 'teamId': 200, **p} for i, p in enumerate(enemies or [])]
    return {'metadata': {'matchId': 'TW2_1'},
            'info': {'queueId': queue, 'gameDuration': dur, 'gameEndTimestamp': 1, 'gameMode': mode,
                     'participants': ps}}


def test_tickets_for_floor_and_steps():
    assert [tickets_for(s) for s in range(9)] == [1, 1, 1, 2, 2, 2, 3, 3, 4]


def test_cap_grant():
    assert cap_grant(3, 0) == 3
    assert cap_grant(3, 10) == 2
    assert cap_grant(3, 12) == 0
    assert cap_grant(4, 20) == 0


def test_today_uses_taiwan_time():
    # UTC 16:30 = 台灣隔天 00:30
    assert today(datetime(2026, 10, 21, 16, 30, tzinfo=timezone.utc)) == '2026-10-22'


def test_lol_carry_gets_max():
    m = _lol({'win': True, 'kills': 12, 'deaths': 1, 'assists': 8, 'totalDamageDealtToChampions': 40000},
             mates=[{'kills': 3, 'totalDamageDealtToChampions': 20000}] * 4)
    r = lol_score(m, 'ME', {'103': '阿璃'})
    assert r['score'] == 8 and r['tickets'] == 4 and r['who'] == '阿璃' and r['kda'] == '12/1/8'
    assert r['skip'] is None and r['queue'] == '單/雙積分'


def test_lol_feeder_still_gets_one_ticket():
    m = _lol({'win': False, 'kills': 0, 'deaths': 10, 'assists': 2, 'totalDamageDealtToChampions': 5000},
             mates=[{'kills': 5, 'totalDamageDealtToChampions': 20000}] * 4)
    r = lol_score(m, 'ME')
    assert r['score'] == 0 and r['tickets'] == 1
    assert parts_text(r['parts']) == '沒有加分項(保底 1 張)'


def test_lol_support_scores_on_kill_participation():
    # 輸了、KDA (1+14)/6=2.5 → 1、參團 15/17=88% → 2、傷害 5% → 0 = 3 → 2 張
    m = _lol({'win': False, 'kills': 1, 'deaths': 6, 'assists': 14, 'totalDamageDealtToChampions': 3000},
             mates=[{'kills': 4, 'totalDamageDealtToChampions': 15000}] * 4)
    r = lol_score(m, 'ME')
    assert r['score'] == 3 and r['tickets'] == 2


def test_lol_middle_breakdown():
    # 勝利 2 + KDA (4+6)/2=5 → 2 + 參團 10/20=50% → 0 + 傷害 25% → 1 = 5
    m = _lol({'win': True, 'kills': 4, 'deaths': 2, 'assists': 6, 'totalDamageDealtToChampions': 25000},
             mates=[{'kills': 4, 'totalDamageDealtToChampions': 18750}] * 4)
    r = lol_score(m, 'ME')
    assert r['score'] == 5 and r['tickets'] == 2
    assert parts_text(r['parts']) == '勝利 +2・KDA 5.0 +2・傷害佔比 25% +1'


def test_lol_remake_and_custom_skipped():
    assert lol_score(_lol({'win': True}, dur=200), 'ME')['skip']
    assert lol_score(_lol({'win': True, 'gameEndedInEarlySurrender': True}), 'ME')['skip']
    assert lol_score(_lol({'win': True}, queue=0), 'ME')['skip']
    # 舊版 gameDuration 是毫秒(沒有 gameEndTimestamp)
    m = _lol({'win': True})
    m['info'].pop('gameEndTimestamp')
    m['info']['gameDuration'] = 1_800_000
    assert lol_score(m, 'ME')['skip'] is None


def test_lol_arena_by_placement():
    assert lol_score(_lol({'placement': 1}, queue=1700, mode='CHERRY'), 'ME')['score'] == 8
    assert lol_score(_lol({'placement': 4}, queue=1700, mode='CHERRY'), 'ME')['score'] == 5
    assert lol_score(_lol({'placement': 8}, queue=1700, mode='CHERRY'), 'ME')['tickets'] == 1


def test_lol_player_missing():
    assert lol_score(_lol({}), 'NOPE') is None


def _valo(**kw):
    s = {'matchId': 'v1', 'queue': '競技', 'won': True, 'draw': False, 'ffa': False, 'score': '13:7',
         'agent': '捷風', 'k': 20, 'd': 10, 'a': 5, 'acs': 280, 'mvp': False}
    s.update(kw)
    return s


def test_valo_normal():
    # 勝利 2 + ACS 280 → 2 + K/D 2.0 → 2 = 6
    r = valo_score(_valo())
    assert r['score'] == 6 and r['tickets'] == 3 and r['who'] == '捷風'


def test_valo_mvp_capped_at_8():
    r = valo_score(_valo(acs=320, mvp=True))
    assert r['score'] == 8 and r['tickets'] == 4


def test_valo_draw_and_single_round_mode():
    assert valo_score(_valo(won=False, draw=True, acs=150, k=10, d=10))['score'] == 1
    # 團隊死鬥沒 ACS：改看 K/D(2.0 → 3) + K/D 一般門檻(→ 2) + 勝利 2
    assert valo_score(_valo(acs=None, queue='團隊死鬥'))['score'] == 7


def test_valo_deathmatch_place_and_custom():
    assert valo_score(_valo(ffa=True, score='第 1 名'))['score'] == 8
    assert valo_score(_valo(ffa=True, score='第 12 名'))['score'] == 0
    assert valo_score(_valo(queue='自訂'))['skip']


def _lcu_game(queue=2400, dur=1200):
    parts, ids = [], []
    for i, (name, team, k, d, a, dmg, win) in enumerate([
            ('Heart', 100, 15, 3, 20, 45000, True), ('朋友', 100, 5, 5, 25, 20000, True),
            ('隊友A', 100, 3, 6, 10, 15000, True), ('對手', 200, 8, 9, 5, 30000, False)], start=1):
        parts.append({'participantId': i, 'teamId': team, 'championId': 103,
                      'stats': {'kills': k, 'deaths': d, 'assists': a, 'win': win, 'totalDamageDealtToChampions': dmg}})
        ids.append({'participantId': i, 'player': {'gameName': name, 'tagLine': 'TW2', 'puuid': f'lcu-{i}'}})
    return {'gameId': 402400001, 'queueId': queue, 'gameMode': 'KIWI', 'gameDuration': dur,
            'participants': parts, 'participantIdentities': ids}


def test_lcu_mayhem_scored_like_api():
    from games.fun_score import lcu_to_v5, lol_game_id, riot_key
    m = lcu_to_v5(_lcu_game())
    r = lol_score(m, riot_key('Heart', 'tw2'), {'103': '阿璃'})
    # 勝利 2 + KDA 35/3=11.7 → 3 + 參團 35/23 → 2 + 傷害 45000/80000=56% → 2 = 9 → 封頂 8
    assert r['score'] == 8 and r['queue'] == '隨機單中：大混戰' and r['who'] == '阿璃' and r['matchId'] == '402400001'
    assert lol_score(m, riot_key('朋友', 'TW2'))['skip'] is None
    assert lol_score(lcu_to_v5(_lcu_game(dur=150)), riot_key('Heart', 'TW2'))['skip']   # 重開
    assert lol_game_id('TW2_402379706') == '402379706'
