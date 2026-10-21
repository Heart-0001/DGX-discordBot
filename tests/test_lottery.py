import random
from datetime import datetime

from games.fun_score import TW
from games.lottery_logic import (BET_POOL, draw, new_state, next_draw_after, pick_winners, prize_amounts,
                                 pull_in_draw, settle_period)


def tw(y, m, d, h=0, mi=0, s=0):
    return datetime(y, m, d, h, mi, s, tzinfo=TW)


# ── 開獎時間(每天 21:00)──
def test_next_draw_every_day():
    for d in range(19, 26):
        assert next_draw_after(tw(2026, 10, d, 12)) == tw(2026, 10, d, 21)
    assert next_draw_after(tw(2026, 10, 25, 0, 0)) == tw(2026, 10, 25, 21)


def test_next_draw_around_21():
    assert next_draw_after(tw(2026, 10, 25, 20, 59, 59)) == tw(2026, 10, 25, 21)
    # 剛好 21:00 整算已過 → 明天
    assert next_draw_after(tw(2026, 10, 25, 21, 0, 0)) == tw(2026, 10, 26, 21)
    assert next_draw_after(tw(2026, 10, 25, 21, 0, 1)) == tw(2026, 10, 26, 21)
    assert next_draw_after(tw(2026, 10, 25, 23, 59, 59)) == tw(2026, 10, 26, 21)


def test_next_draw_across_year_and_month():
    assert next_draw_after(tw(2026, 10, 31, 22)) == tw(2026, 11, 1, 21)
    assert next_draw_after(tw(2026, 12, 31, 23, 59)) == tw(2027, 1, 1, 21)
    assert next_draw_after(tw(2028, 2, 28, 22)) == tw(2028, 2, 29, 21)    # 閏年


def test_next_draw_other_timezone_input():
    from datetime import timezone
    utc = datetime(2026, 10, 25, 13, 0, 0, tzinfo=timezone.utc)          # = 台灣 21:00 整
    assert next_draw_after(utc) == tw(2026, 10, 26, 21)
    utc = datetime(2026, 10, 25, 12, 59, 59, tzinfo=timezone.utc)
    assert next_draw_after(utc) == tw(2026, 10, 25, 21)


def test_old_weekly_draw_pulled_in_to_next_21():
    st = new_state(tw(2026, 10, 21, 12))
    st['next_draw'] = int(tw(2026, 10, 25, 21).timestamp())             # 舊版排的週日
    st['bets'], st['pot'] = {'a': 1}, BET_POOL
    assert pull_in_draw(st, tw(2026, 10, 21, 23, 10))
    assert st['next_draw'] == int(tw(2026, 10, 22, 21).timestamp())
    assert st['bets'] == {'a': 1} and st['pot'] == BET_POOL             # 已經買的注留著
    assert not pull_in_draw(st, tw(2026, 10, 21, 23, 20))               # 已經是下一個 21:00 → 不動


# ── 抽獎 ──
def test_each_bet_equal_probability_first_prize():
    bets = {'a': 1, 'b': 3, 'c': 6}
    rng = random.Random(2024)
    n = 60_000
    cnt = {k: 0 for k in bets}
    for _ in range(n):
        cnt[pick_winners(bets, rng)[0]] += 1
    for k, v in bets.items():
        assert abs(cnt[k] / n - v / 10) < 0.01, (k, cnt[k] / n)


def test_second_prize_probability_matches_theory():
    # a:1 b:1 c:2 → 頭獎 a=1/4；c 先中後 a 得二獎 = 1/2 * 1/2 ，b 同理 ...
    bets = {'a': 1, 'b': 1, 'c': 2}
    rng = random.Random(5)
    n = 60_000
    second_a = 0
    for _ in range(n):
        w = pick_winners(bets, rng)
        second_a += w[1] == 'a'
    # P(2nd=a) = P(b first)*1/3 + P(c first)*1/2 = 1/4*1/3 + 1/2*1/2 = 1/3
    assert abs(second_a / n - 1 / 3) < 0.01


def test_no_one_wins_twice_and_seeded_reproducible():
    bets = {'a': 20, 'b': 1, 'c': 1, 'd': 5}
    for seed in range(200):
        w = pick_winners(bets, random.Random(seed))
        assert len(w) == 3 and len(set(w)) == 3
    assert pick_winners(bets, random.Random(1)) == pick_winners(bets, random.Random(1))


def test_fewer_than_three_players():
    rng = random.Random(0)
    r = draw({'a': 5}, 500, rng)
    assert [w['uid'] for w in r['winners']] == ['a']
    assert r['winners'][0]['prize'] == 300 and r['carry'] == 200
    r = draw({'a': 1, 'b': 9}, 1000, rng)
    assert len(r['winners']) == 2 and {w['uid'] for w in r['winners']} == {'a', 'b'}
    assert r['paid'] == 600 + 250 and r['carry'] == 150


def test_prize_split_floor_and_conservation():
    assert prize_amounts(1000) == [600, 250, 150]
    assert prize_amounts(101) == [60, 25, 15]
    rng = random.Random(3)
    for pot in (0, 1, 99, 100, 101, 12345):
        r = draw({'a': 1, 'b': 2, 'c': 3, 'd': 4}, pot, rng)
        assert r['paid'] + r['carry'] == pot
        assert sum(w['prize'] for w in r['winners']) == r['paid']
    r = draw({'a': 1, 'b': 1, 'c': 1}, 101, rng)
    assert r['paid'] == 100 and r['carry'] == 1


def test_zero_bets_rolls_everything():
    r = draw({}, 700, random.Random(0))
    assert r['winners'] == [] and r['carry'] == 700 and r['paid'] == 0
    r = draw({'a': 0}, 0, random.Random(0))
    assert r['winners'] == [] and r['carry'] == 0


# ── 整期結算 / 重啟不重複開獎 ──
def test_settle_period_flow_and_idempotent():
    now0 = tw(2026, 10, 25, 12)
    st = new_state(now0)
    assert st['next_draw'] == int(tw(2026, 10, 25, 21).timestamp())
    st['bets'] = {'a': 3, 'b': 2}
    st['pot'] = 5 * BET_POOL
    st['prize'] = '頭獎送身分組'
    rng = random.Random(8)
    assert not settle_period(st, tw(2026, 10, 25, 20, 59, 59), rng)
    assert st['bets'] and st['last'] is None
    assert settle_period(st, tw(2026, 10, 25, 21, 0, 0), rng)
    assert st['next_draw'] == int(tw(2026, 10, 26, 21).timestamp())
    assert st['bets'] == {} and st['prize'] == '' and st['period'] == 2
    assert st['last']['prize'] == '頭獎送身分組'
    assert len(st['pay']) == 2 and st['announce'] is True
    assert st['pot'] == 500 - 300 - 125 == 75        # 兩人：只發頭獎 + 二獎，15% 滾存 (75)
    pay_before = list(st['pay'])
    # 重啟後再檢查：不會重開
    assert not settle_period(st, tw(2026, 10, 25, 21, 0, 30), rng)
    assert st['pay'] == pay_before and st['period'] == 2


def test_settle_after_long_downtime_draws_once():
    st = new_state(tw(2026, 10, 1, 12))
    st['bets'] = {'a': 1}
    st['pot'] = 100
    rng = random.Random(1)
    now = tw(2026, 12, 25, 12)                       # 停機好幾天
    assert settle_period(st, now, rng)
    assert not settle_period(st, now, rng)
    assert st['next_draw'] > now.timestamp()


def test_settle_zero_bets_carries_pot():
    st = new_state(tw(2026, 10, 21, 12))
    st['pot'] = 250
    assert settle_period(st, tw(2026, 10, 26), random.Random(0))
    assert st['pot'] == 250 and st['pay'] == [] and st['period'] == 2
    # 完全空的期不留紀錄也不公告
    st2 = new_state(tw(2026, 10, 21, 12))
    assert settle_period(st2, tw(2026, 10, 26), random.Random(0))
    assert st2['last'] is None and st2['announce'] is False


def test_long_run_expected_value_per_bet_is_pool():
    """多期模擬：發出去的彩金總和 + 最後剩的滾存 = 系統投入的總額 (每注 100)。"""
    rng = random.Random(77)
    st = new_state(tw(2026, 1, 1))
    injected = paid = 0
    now = tw(2026, 1, 1)
    for week in range(200):
        n_players = rng.randint(0, 6)
        bets = {f'u{i}': rng.randint(1, 5) for i in range(n_players)}
        st['bets'] = bets
        st['pot'] += sum(bets.values()) * BET_POOL
        injected += sum(bets.values()) * BET_POOL
        now = datetime.fromtimestamp(st['next_draw'], TW)
        settle_period(st, now, rng)
        paid += sum(w['prize'] for w in st['pay'])
        st['pay'] = []
    assert paid + st['pot'] == injected
