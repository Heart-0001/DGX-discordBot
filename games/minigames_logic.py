"""小遊戲合集的純邏輯(不 import discord)：21 點、拉霸、踩地雷、猜正反面。

每局花 1 張券(約 100 代幣)；每個遊戲的期望值都在 90–110 代幣之間(測試裡驗證)。
"""
import random
from fractions import Fraction
from itertools import product
from math import comb

# ───────────── 21 點 ─────────────
BJ_LOSE, BJ_PUSH, BJ_WIN, BJ_NATURAL = 0, 100, 200, 250
SUITS = '♠♥♦♣'
RANKS = ['A', '2', '3', '4', '5', '6', '7', '8', '9', '10', 'J', 'Q', 'K']
DECKS = 4                      # 每局用 4 副牌洗好再發


def new_shoe(rng: random.Random, decks: int = DECKS) -> list:
    shoe = [(r, s) for _ in range(decks) for r in RANKS for s in SUITS]
    rng.shuffle(shoe)
    return shoe


def card_text(c) -> str:
    return f'{c[1]}{c[0]}'


def hand_value(cards) -> tuple[int, bool]:
    """回傳 (點數, 是否 soft)。A 盡量算 11，爆了才改成 1。"""
    total = sum(1 if r == 'A' else 10 if r in ('10', 'J', 'Q', 'K') else int(r) for r, _ in cards)
    aces = sum(1 for r, _ in cards if r == 'A')
    if aces and total + 10 <= 21:
        return total + 10, True
    return total, False


def is_blackjack(cards) -> bool:
    return len(cards) == 2 and hand_value(cards)[0] == 21


class Blackjack:
    """一局 21 點。開局就發牌；任一方 Blackjack 當場結算。"""

    def __init__(self, rng: random.Random | None = None):
        self.rng = rng or random.Random()
        self.shoe = new_shoe(self.rng)
        self.player = [self.shoe.pop(), self.shoe.pop()]
        # 莊家第一張是明牌
        self.dealer = [self.shoe.pop(), self.shoe.pop()]
        self.over = False
        self.result = ''      # blackjack / win / push / lose / bust / dealer_bust
        self.payout = 0
        if is_blackjack(self.player) or is_blackjack(self.dealer):
            self._settle()
        elif hand_value(self.player)[0] == 21:
            self.stand()

    @property
    def upcard(self):
        return self.dealer[0]

    def hit(self):
        if self.over:
            return
        self.player.append(self.shoe.pop())
        v = hand_value(self.player)[0]
        if v > 21:
            self.over, self.result, self.payout = True, 'bust', BJ_LOSE
        elif v == 21:
            self.stand()

    def stand(self):
        if self.over:
            return
        # 莊家 17 點以上停(soft 17 也停)
        while hand_value(self.dealer)[0] < 17:
            self.dealer.append(self.shoe.pop())
        self._settle()

    def _settle(self):
        p, d = hand_value(self.player)[0], hand_value(self.dealer)[0]
        pbj, dbj = is_blackjack(self.player), is_blackjack(self.dealer)
        if pbj and dbj:
            self.result, self.payout = 'push', BJ_PUSH
        elif pbj:
            self.result, self.payout = 'blackjack', BJ_NATURAL
        elif dbj:
            self.result, self.payout = 'lose', BJ_LOSE
        elif d > 21:
            self.result, self.payout = 'dealer_bust', BJ_WIN
        elif p > d:
            self.result, self.payout = 'win', BJ_WIN
        elif p == d:
            self.result, self.payout = 'push', BJ_PUSH
        else:
            self.result, self.payout = 'lose', BJ_LOSE
        self.over = True


def basic_strategy(player, up) -> str:
    """簡化基本策略：≤11 要牌；12–16 看莊家明牌(7 以上要牌)；≥17 停。"""
    v = hand_value(player)[0]
    if v <= 11:
        return 'hit'
    if v >= 17:
        return 'stand'
    uv = hand_value([up])[0] if up[0] != 'A' else 11
    return 'hit' if uv >= 7 else 'stand'


def simulate_blackjack(n: int, seed: int = 1) -> float:
    rng = random.Random(seed)
    total = 0
    for _ in range(n):
        g = Blackjack(rng)
        while not g.over:
            if basic_strategy(g.player, g.upcard) == 'hit':
                g.hit()
            else:
                g.stand()
        total += g.payout
    return total / n


# ───────────── 拉霸 ─────────────
# (圖案, 權重, 三個一樣的賠付)；兩個🍄(任意位置，剛好兩個)賠 SLOTS_PAIR
SLOTS = [
    ('🍄', 8, 600),      # 提摩蘑菇
    ('💀', 6, 1000),     # 送頭
    ('🔫', 5, 1600),     # Vandal
    ('🐉', 3, 3000),     # 大龍
    ('👑', 2, 8000),     # 超神
    ('💎', 1, 20000),    # Radiant
]
SLOTS_PAIR_SYMBOL = '🍄'
SLOTS_PAIR = 200


def spin_slots(rng: random.Random | None = None) -> list[str]:
    rng = rng or random
    return rng.choices([s[0] for s in SLOTS], weights=[s[1] for s in SLOTS], k=3)


def slots_payout(reels) -> int:
    if reels[0] == reels[1] == reels[2]:
        return next(p for s, _, p in SLOTS if s == reels[0])
    if list(reels).count(SLOTS_PAIR_SYMBOL) == 2:
        return SLOTS_PAIR
    return 0


def slots_expected() -> Fraction:
    """列舉所有 6³ 種組合，精確算期望值。"""
    total = sum(w for _, w, _ in SLOTS)
    ev = Fraction(0)
    for combo in product(SLOTS, repeat=3):
        p = Fraction(1)
        for _, w, _ in combo:
            p *= Fraction(w, total)
        ev += p * slots_payout([c[0] for c in combo])
    return ev


# ───────────── 踩地雷 ─────────────
MINES_CELLS = 20
MINES_COUNT = 3
MINES_RTP = 0.97


def mines_safe_prob(k: int, cells: int = MINES_CELLS, mines: int = MINES_COUNT) -> Fraction:
    """連續翻 k 格都安全的機率。"""
    safe = cells - mines
    return Fraction(comb(safe, k), comb(cells, k))


def mines_multiplier(k: int, cells: int = MINES_CELLS, mines: int = MINES_COUNT) -> float:
    return MINES_RTP / float(mines_safe_prob(k, cells, mines))


def mines_payout(k: int, cells: int = MINES_CELLS, mines: int = MINES_COUNT) -> int:
    """翻開 k 個安全格後收手拿到的代幣。"""
    return int(round(100 * mines_multiplier(k, cells, mines)))


def mines_place(rng: random.Random | None = None) -> set[int]:
    return set((rng or random).sample(range(MINES_CELLS), MINES_COUNT))


# ───────────── 猜正反面 ─────────────
COINFLIP_WIN = 196


def coinflip_payout(guess_correct: bool) -> int:
    return COINFLIP_WIN if guess_correct else 0
