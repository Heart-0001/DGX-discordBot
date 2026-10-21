"""小遊戲合集的純邏輯測試：每個遊戲的期望值都要在 90–110 代幣。"""
import os
import random
import sys
from fractions import Fraction

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from games import minigames_logic as L  # noqa: E402


# ── 21 點 ──
def test_hand_value():
    assert L.hand_value([('A', '♠'), ('K', '♥')]) == (21, True)
    assert L.hand_value([('A', '♠'), ('A', '♥'), ('9', '♦')]) == (21, True)
    assert L.hand_value([('A', '♠'), ('6', '♥'), ('K', '♦')]) == (17, False)
    assert L.hand_value([('10', '♠'), ('7', '♥'), ('5', '♦')]) == (22, False)
    assert L.is_blackjack([('A', '♠'), ('10', '♥')])
    assert not L.is_blackjack([('7', '♠'), ('7', '♥'), ('7', '♦')])


def _game(player, dealer, shoe):
    g = L.Blackjack(random.Random(0))
    g.shoe = list(shoe)
    g.player, g.dealer, g.over, g.result, g.payout = list(player), list(dealer), False, '', 0
    return g


def c(r):
    return (r, '♠')


def test_dealer_stands_on_soft_17():
    g = _game([c('10'), c('9')], [c('A'), c('6')], [c('5')])
    g.stand()
    assert len(g.dealer) == 2 and g.result == 'win' and g.payout == L.BJ_WIN


def test_dealer_hits_16_and_busts():
    g = _game([c('10'), c('8')], [c('10'), c('6')], [c('9')])
    g.stand()
    assert g.result == 'dealer_bust' and g.payout == L.BJ_WIN


def test_player_bust_and_push():
    g = _game([c('10'), c('6')], [c('10'), c('7')], [c('9')])
    g.hit()
    assert g.over and g.result == 'bust' and g.payout == L.BJ_LOSE
    g = _game([c('10'), c('7')], [c('10'), c('7')], [])
    g.stand()
    assert g.result == 'push' and g.payout == L.BJ_PUSH


def test_blackjack_cases():
    # 開局 BJ 當場結算(用固定牌堆太麻煩，直接呼叫 _settle)
    g = _game([c('A'), c('K')], [c('9'), c('8')], [])
    g._settle()
    assert g.result == 'blackjack' and g.payout == L.BJ_NATURAL
    g = _game([c('A'), c('K')], [c('A'), c('Q')], [])
    g._settle()
    assert g.result == 'push' and g.payout == L.BJ_PUSH
    g = _game([c('9'), c('8')], [c('A'), c('Q')], [])
    g._settle()
    assert g.result == 'lose' and g.payout == 0


def test_blackjack_always_settles():
    rng = random.Random(5)
    for _ in range(2000):
        g = L.Blackjack(rng)
        while not g.over:
            g.hit() if rng.random() < 0.5 else g.stand()
        assert g.payout in (0, L.BJ_PUSH, L.BJ_WIN, L.BJ_NATURAL)


def test_blackjack_ev_basic_strategy():
    ev = (L.simulate_blackjack(250_000, seed=11) + L.simulate_blackjack(250_000, seed=12)) / 2
    print('blackjack EV', ev)
    assert 92 <= ev <= 108


# ── 拉霸 ──
def test_slots_ev_exact():
    ev = float(L.slots_expected())
    print('slots EV', ev)
    assert 90 <= ev <= 110


def test_slots_probabilities_sum_to_one():
    total = sum(w for _, w, _ in L.SLOTS)
    assert sum(Fraction(w, total) for _, w, _ in L.SLOTS) == 1
    assert L.slots_payout(['🍄', '🍄', '💀']) == L.SLOTS_PAIR
    assert L.slots_payout(['🍄', '💀', '🍄']) == L.SLOTS_PAIR
    assert L.slots_payout(['🍄', '🍄', '🍄']) == 600
    assert L.slots_payout(['💀', '💀', '🔫']) == 0


def test_slots_simulation_matches_exact():
    rng = random.Random(3)
    n = 200_000
    ev = sum(L.slots_payout(L.spin_slots(rng)) for _ in range(n)) / n
    assert abs(ev - float(L.slots_expected())) < 8      # 大獎很稀有，容許較大誤差


# ── 踩地雷 ──
def test_mines_ev_each_stop_point():
    for k in range(0, L.MINES_CELLS - L.MINES_COUNT + 1):
        ev = float(L.mines_safe_prob(k)) * L.mines_payout(k)
        assert 96.0 <= ev <= 98.0, (k, ev)
        assert abs(L.mines_multiplier(k) * float(L.mines_safe_prob(k)) - 0.97) < 1e-9


def test_mines_multiplier_increases():
    ms = [L.mines_multiplier(k) for k in range(18)]
    assert all(a < b for a, b in zip(ms, ms[1:]))


def test_mines_simulated_stop_strategy():
    rng = random.Random(7)
    for stop in (1, 3, 6):
        n, total = 100_000, 0
        for _ in range(n):
            mines = L.mines_place(rng)
            order = rng.sample(range(L.MINES_CELLS), L.MINES_CELLS)
            if any(cell in mines for cell in order[:stop]):
                continue
            total += L.mines_payout(stop)
        ev = total / n
        assert 90 <= ev <= 104, (stop, ev)


def test_mines_place():
    m = L.mines_place(random.Random(1))
    assert len(m) == L.MINES_COUNT and all(0 <= x < L.MINES_CELLS for x in m)


# ── 猜正反面 ──
def test_coinflip_ev():
    assert (L.coinflip_payout(True) + L.coinflip_payout(False)) / 2 == 98
