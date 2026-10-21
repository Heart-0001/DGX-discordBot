"""大富翁純邏輯 + 棋盤圖測試(固定骰子 / 固定 seed，不需要 discord)。"""
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from games import mono_logic as ml  # noqa: E402
from games.mono_image import render_board  # noqa: E402

A, B, C = 1, 2, 3


def mk(n=3, now=1000.0):
    return ml.new_season(list(range(1, n + 1)), now, 1)


def go(s, uid, pos, dice, now=1000.0, card=None):
    s['players'][str(uid)]['pos'] = pos
    return ml.roll(s, uid, random.Random(1), now, dice=dice, card=card)


def own(s, tile, uid, level=0):
    s['owners'][str(tile)] = {'owner': uid, 'level': level}


def test_board_shape():
    assert len(ml.TILES) == 32
    kinds = [t['type'] for t in ml.TILES]
    assert kinds.count('land') == 20 and kinds.count('chance') == 4
    assert kinds.count('tax') == 2 and kinds.count('portal') == 2
    for i in (0, 8, 16, 24):
        assert ml.TILES[i]['type'] in ('start', 'jail', 'rest', 'report')
    assert sorted(len(x) for x in ml.GROUP_TILES) == [2, 3, 3, 3, 3, 3, 3]
    assert len(ml.CARDS) >= 12
    prices = [t['price'] for t in ml.TILES if t['type'] == 'land']
    assert min(prices) == 60 and max(prices) == 400


def test_pass_start_collects_200():
    s = mk()
    r = go(s, 1, 30, (1, 2))             # 30+3 → 第 1 格
    assert s['players']['1']['pos'] == 1
    assert s['players']['1']['cash'] == ml.START_CASH + 200
    assert any('起點' in e for e in r['events'])


def test_land_exactly_on_start():
    s = mk()
    go(s, 1, 25, (3, 4))                 # 25+7=32 → 起點
    assert s['players']['1']['pos'] == 0 and s['players']['1']['cash'] == 1700


def test_buy_flow():
    s = mk()
    r = go(s, 1, 0, (3, 4))              # 第 7 格 天漠之峽 100
    pd = r['pending']
    assert pd['type'] == 'buy' and pd['tile'] == 7
    assert ml.pending_error(s, 1, 'buy', 7, r['turn'], 1010) is None
    ok, _ = ml.buy(s, 1, 7)
    assert ok and s['players']['1']['cash'] == 1400
    assert s['owners']['7'] == {'owner': 1, 'level': 0}
    assert s['players']['1']['pending'] is None
    assert ml.net_worth(s, 1) == 1500


def test_pending_expires_and_mismatch():
    s = mk()
    r = go(s, 1, 0, (3, 4), now=1000)
    assert ml.pending_error(s, 1, 'buy', 7, r['turn'] + 1, 1001)          # 回合序號不符
    assert ml.pending_error(s, 1, 'up', 7, r['turn'], 1001)               # 類型不符
    assert ml.pending_error(s, 1, 'buy', 7, r['turn'], 1000 + 301) is not None   # 5 分鐘過期
    assert s['players']['1']['pending'] is None


def test_decline_and_new_roll_clears_pending():
    s = mk()
    go(s, 1, 0, (3, 4))
    ml.decline(s, 1)
    assert s['players']['1']['pending'] is None and '7' not in s['owners']
    go(s, 1, 0, (3, 4))
    r = go(s, 1, 0, (2, 4))              # 再擲一次 → 舊的待決定被清掉
    assert s['players']['1']['pending']['tile'] == 6 and r['pending']['tile'] == 6


def test_cannot_buy_without_cash():
    s = mk()
    s['players']['1']['cash'] = 50
    r = go(s, 1, 0, (3, 4))
    assert r['pending'] is None


def test_rent_levels_and_group_double():
    s = mk()
    # 第 6、7、9 格是同一組(劫境之地/天漠之峽/日落之城)，基本租 10/10/12
    own(s, 7, 2)
    assert ml.rent_for(s, 7) == 10                       # 沒全擁有
    own(s, 6, 2)
    own(s, 9, 2)
    assert ml.rent_for(s, 7) == 20                       # 同組全擁有 ×2
    for lv, mult in enumerate(ml.RENT_MULT):
        own(s, 7, 2, lv)
        want = 10 * mult * (2 if lv == 0 else 1)         # 升級後不再加倍
        assert ml.rent_for(s, 7) == want
    assert ml.RENT_MULT == (1, 3, 6, 10)


def test_pay_rent_to_owner_even_offline():
    s = mk()
    own(s, 7, 2, 1)                                      # 3 倍 = 30
    r = go(s, 1, 0, (3, 4))
    assert s['players']['1']['cash'] == 1470
    assert s['players']['2']['cash'] == 1530
    assert r['pending'] is None
    assert any('<@2>' in e for e in r['events'])


def test_upgrade_flow():
    s = mk()
    own(s, 7, 1, 0)
    r = go(s, 1, 0, (3, 4))
    assert r['pending']['type'] == 'up'
    ok, _ = ml.upgrade(s, 1, 7)
    assert ok and s['owners']['7']['level'] == 1 and s['players']['1']['cash'] == 1450   # 100 × 0.5
    for _ in range(2):
        go(s, 1, 0, (3, 4))
        assert ml.upgrade(s, 1, 7)[0]
    assert s['owners']['7']['level'] == 3
    r = go(s, 1, 0, (3, 4))
    assert r['pending'] is None                          # 滿級不再問
    assert ml.net_worth(s, 1) == 1600                    # 免費給的地(100) 算進淨值，升級花的錢轉成地的價值


def test_tax_to_bank():
    s = mk()
    go(s, 1, 0, (2, 3))                                  # 第 5 格 買裝稅 100
    assert s['players']['1']['cash'] == 1400


def test_portal_teleports_to_next():
    s = mk()
    go(s, 1, 4, (2, 4))                                  # 第 10 格 傳送門 → 第 19 格
    assert s['players']['1']['pos'] == 19
    go(s, 1, 14, (2, 3))                                 # 第 19 格 → 下一個是第 10 格
    assert s['players']['1']['pos'] == 10


def test_rest_and_visit_jail_nothing():
    s = mk()
    go(s, 1, 4, (2, 2))                                  # 第 8 格 探監
    assert s['players']['1']['cash'] == 1500 and not s['players']['1']['jail']
    go(s, 1, 10, (2, 4))                                 # 第 16 格 泉水
    assert s['players']['1']['cash'] == 1500


def card_idx(kind, val=None):
    return next(i for i, c in enumerate(ml.CARDS) if c[0] == kind and (val is None or c[1] == val))


def test_card_each_give():
    s = mk(4)
    ev = []
    ml.apply_card(s, 1, card_idx('each_give'), ev, random.Random(0), 0)
    assert s['players']['1']['cash'] == 1500 + 150
    assert all(s['players'][k]['cash'] == 1450 for k in '234')


def test_card_gain_pay_give_each():
    s = mk(3)
    ev = []
    ml.apply_card(s, 1, card_idx('gain', 150), ev, random.Random(0), 0)
    assert s['players']['1']['cash'] == 1650
    ml.apply_card(s, 1, card_idx('pay', 150), ev, random.Random(0), 0)
    assert s['players']['1']['cash'] == 1500
    ml.apply_card(s, 1, card_idx('give_each'), ev, random.Random(0), 0)
    assert s['players']['1']['cash'] == 1440 and s['players']['2']['cash'] == 1530


def test_card_move_back_and_forward():
    s = mk()
    s['players']['1']['pos'] = 13
    ev = []
    ml.apply_card(s, 1, card_idx('rel', -3), ev, random.Random(0), 0)
    assert s['players']['1']['pos'] == 10 or s['players']['1']['pos'] == 19    # 落在傳送門會再傳
    s['players']['1']['pos'] = 3
    s['players']['1']['cash'] = 1500
    ml.apply_card(s, 1, card_idx('rel', 3), ev, random.Random(0), 0)
    assert s['players']['1']['pos'] == 6 and s['players']['1']['pending']['tile'] == 6
    ml.apply_card(s, 2, card_idx('rel', -3), ev, random.Random(0), 0)          # 第 0 格退 3 格 → 29 機會；不領起點錢
    assert s['players']['2']['cash'] >= 1500


def test_card_goto_start_collects():
    s = mk()
    s['players']['1']['pos'] = 13
    ml.apply_card(s, 1, card_idx('goto'), [], random.Random(0), 0)
    assert s['players']['1']['pos'] == 0 and s['players']['1']['cash'] == 1700


def test_card_goto_named_tile():
    s = mk()
    s['players']['1']['pos'] = 3
    ml.apply_card(s, 1, card_idx('goto_name'), [], random.Random(0), 0)
    assert s['players']['1']['pos'] == ml.NAME_TO_TILE['日落之城']
    assert s['players']['1']['cash'] == 1500


def test_card_via_roll_and_uniform_draw():
    s = mk()
    r = go(s, 1, 0, (1, 2), card=card_idx('gain', 100))            # 第 3 格 機會
    assert s['players']['1']['cash'] == 1600 and any('海克斯' in e for e in r['events'])
    rng = random.Random(7)
    cnt = [0] * len(ml.CARDS)
    for _ in range(len(ml.CARDS) * 400):
        cnt[rng.randrange(len(ml.CARDS))] += 1
    assert max(cnt) < 520 and min(cnt) > 280


def test_every_card_runs_without_error():
    for i in range(len(ml.CARDS)):
        for pos in (3, 13, 20, 29):
            s = mk(4)
            s['players']['1']['pos'] = pos
            ml.apply_card(s, 1, i, [], random.Random(i), 0)


def test_report_card_and_tile_go_to_jail():
    s = mk()
    ml.apply_card(s, 1, card_idx('jail'), [], random.Random(0), 0)
    p = s['players']['1']
    assert p['jail'] and p['pos'] == 8
    s = mk()
    go(s, 1, 17, (3, 4))                                 # 第 24 格 被檢舉
    assert s['players']['1']['jail'] and s['players']['1']['pos'] == 8
    assert s['players']['1']['cash'] == 1500             # 沒領起點錢


def test_jail_exit_with_double():
    s = mk()
    go(s, 1, 17, (3, 4))
    r = ml.roll(s, 1, random.Random(0), 0, dice=(2, 2))
    p = s['players']['1']
    assert not p['jail'] and p['pos'] == 12 and r['from'] == 8


def test_jail_stay_then_auto_pay_third_time():
    s = mk()
    go(s, 1, 17, (3, 4))
    p = s['players']['1']
    ml.roll(s, 1, random.Random(0), 0, dice=(1, 2))
    assert p['jail'] and p['pos'] == 8 and p['tries'] == 1
    ml.roll(s, 1, random.Random(0), 0, dice=(1, 2))
    assert p['jail'] and p['tries'] == 2
    cash = p['cash']
    ml.roll(s, 1, random.Random(0), 0, dice=(1, 2))      # 第 3 次：付 50 出來並移動 3 格
    assert not p['jail'] and p['pos'] == 11 and p['cash'] == cash - ml.BAIL


def test_jail_bail_button():
    s = mk()
    go(s, 1, 17, (3, 4))
    ok, _ = ml.bail(s, 1)
    p = s['players']['1']
    assert ok and not p['jail'] and p['cash'] == 1450 and p['pos'] == 8
    ml.roll(s, 1, random.Random(0), 0, dice=(1, 2))      # 下一次正常移動
    assert p['pos'] == 11
    assert ml.bail(s, 1)[0] is False


def test_bail_turn_check():
    s = mk()
    r = go(s, 1, 17, (3, 4))
    assert ml.pending_error(s, 1, 'bail', 0, r['turn'], 0) is None
    assert ml.pending_error(s, 1, 'bail', 0, r['turn'] + 5, 0)


def test_bankruptcy_sells_cheapest_first_half_price():
    s = mk()
    p = s['players']['1']
    p['cash'] = 10
    own(s, 1, 1, 0)                                      # 60 → 賣 30
    own(s, 2, 1, 2)                                      # 60+2×30=120 → 賣 60
    own(s, 31, 1, 0)                                     # 400 → 賣 200
    ev = []
    paid = ml.pay(s, 1, 80, ev, to=2)
    assert paid == 80 and p['cash'] == 10 + 30 + 60 - 80 + 0
    assert '1' not in s['owners'] and '2' not in s['owners'] and '31' in s['owners']
    assert s['players']['2']['cash'] == 1580


def test_bankruptcy_debt_forgiven_when_all_sold():
    s = mk()
    p = s['players']['1']
    p['cash'] = 10
    own(s, 1, 1, 0)
    ev = []
    paid = ml.pay(s, 1, 500, ev, to=2)
    assert paid == 40 and p['cash'] == 0 and not ml.owned(s, 1)
    assert s['players']['2']['cash'] == 1540             # 債主少收
    assert '1' in s['players']                           # 仍留在遊戲裡
    assert any('免除' in e for e in ev)


def test_bankruptcy_via_rent_roll():
    s = mk()
    s['players']['1']['cash'] = 5
    own(s, 7, 2, 3)                                      # 100 租 ×10 = 1000
    go(s, 1, 0, (3, 4))
    assert s['players']['1']['cash'] == 0
    assert s['players']['2']['cash'] == 1505


def test_roll_counts_and_dice_range():
    s = mk()
    rng = random.Random(42)
    seen = set()
    for _ in range(300):
        r = ml.roll(s, 1, rng, 0)
        a, b = r['dice']
        assert 1 <= a <= 6 and 1 <= b <= 6
        seen.add(a + b)
        ml.decline(s, 1)
    assert seen == set(range(2, 13))
    assert s['players']['1']['rolled'] == 300


def test_join_and_season_start():
    g = ml.new_guild()
    assert ml.join(g, 5, 100.0) == 'new_season'
    assert g['season']['start'] == 100.0 and g['season']['end'] == 100.0 + 14 * 86400
    assert ml.join(g, 5, 101.0) == 'already'
    assert ml.join(g, 6, 101.0) == 'joined'
    assert g['season']['players']['6']['cash'] == 1500


def test_standings_and_net_worth():
    s = mk()
    own(s, 31, 2, 1)                                     # 400 + 200
    assert ml.net_worth(s, 2) == 1500 + 600
    assert [u for u, _ in ml.standings(s)][0] == 2


def make_guild_for_settle():
    g = ml.new_guild()
    for u in (1, 2, 3, 4, 5):
        ml.join(g, u, 0.0)
    s = g['season']
    for u, cash in ((1, 500), (2, 3000), (3, 2000), (4, 1000), (5, 9999)):
        s['players'][str(u)]['cash'] = cash
        s['players'][str(u)]['rolled'] = 0 if u == 5 else 3
    own(s, 31, 4, 3)                                     # 玩家 4：1000 + 400 + 600 = 2000
    s['players']['4']['cash'] = 1000
    s['seq'] = 17
    return g


def test_settle_not_due():
    g = make_guild_for_settle()
    assert ml.settle_if_due(g, 1000.0) is None
    assert g['history'] == []


def test_settle_ranking_rewards_and_renew():
    g = make_guild_for_settle()
    old_end = g['season']['end']
    rec = ml.settle_if_due(g, old_end + 5)
    rank = [(r['uid'], r['reward']) for r in rec['ranking']]
    # 淨值：2=3000、3=2000、4=2000(現金 1000 較少排在 3 後)、1=500、5 沒擲過骰不領獎
    assert rank[0] == (5, 0) or rec['ranking'][0]['uid'] == 5
    rewards = {r['uid']: r['reward'] for r in rec['ranking']}
    assert rewards == {2: 3000, 3: 2000, 4: 1000, 1: 300, 5: 0}
    assert rec['champion'] == 2
    assert g['history'][-1]['id'] == rec['id'] == 1
    # 自動開新季、名單保留、現金地位置重置
    s2 = g['season']
    assert s2['id'] == 2 and s2['start'] == old_end + 5 and s2['end'] == s2['start'] + 14 * 86400
    assert sorted(s2['players']) == ['1', '2', '3', '4', '5']
    assert all(p['cash'] == 1500 and p['pos'] == 0 and p['rolled'] == 0 for p in s2['players'].values())
    assert s2['owners'] == {}
    assert s2['seq'] == 17                               # 回合序號不重來，舊按鈕不會對到新季
    # 重啟後再檢查一次不會重複結算
    assert ml.settle_if_due(g, old_end + 6) is None
    assert len(g['history']) == 1


def test_settle_nobody_rolled():
    g = ml.new_guild()
    ml.join(g, 1, 0.0)
    rec = ml.settle_if_due(g, 99999999.0)
    assert rec['champion'] is None and rec['ranking'][0]['reward'] == 0


def test_json_roundtrip():
    import json
    g = make_guild_for_settle()
    g2 = json.loads(json.dumps(g))
    assert ml.net_worth(g2['season'], 4) == ml.net_worth(g['season'], 4)
    r = ml.roll(g2['season'], 1, random.Random(3), 5.0)
    assert r['dice']


def test_board_image_png():
    s = mk(5)
    own(s, 7, 1, 2)
    own(s, 31, 2, 3)
    s['players']['3']['pos'] = 8
    s['players']['3']['jail'] = True
    s['players']['4']['pos'] = 8
    png = render_board(s, {'1': '小明', '2': 'Bob', '3': '阿華', '4': '喵', '5': 'Zed'}, 9.5)
    assert png[:8] == b'\x89PNG\r\n\x1a\n' and len(png) > 5000


def test_board_image_empty_season():
    png = render_board(ml.new_season([], 0, 1), {}, 14)
    assert png[:4] == b'\x89PNG'
