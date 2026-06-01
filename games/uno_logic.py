from __future__ import annotations

import random
import time
from enum import IntEnum
from typing import Optional, List, Dict, Tuple
from dataclasses import dataclass, field

# ── Colors & Actions ──────────────────────────────────────────────────────────

class Color(IntEnum):
    RED = 0
    YELLOW = 1
    GREEN = 2
    BLUE = 3
    WILD = 4

COLOR_NAMES = {Color.RED: '紅', Color.YELLOW: '黃', Color.GREEN: '綠', Color.BLUE: '藍', Color.WILD: '萬能'}
COLOR_EMOJI = {Color.RED: '🟥', Color.YELLOW: '🟨', Color.GREEN: '🟩', Color.BLUE: '🟦', Color.WILD: '⬛'}

class Action(IntEnum):
    NUMBER = 0
    SKIP = 9
    REVERSE = 10
    DRAW_TWO = 11
    WILD = 12
    WILD_DRAW_FOUR = 13

ACTION_NAMES = {
    Action.NUMBER: None,
    Action.SKIP: '跳轉',
    Action.REVERSE: '反轉',
    Action.DRAW_TWO: '+2',
    Action.WILD: '萬能',
    Action.WILD_DRAW_FOUR: '萬能+4',
}

@dataclass(frozen=True, order=True)
class Card:
    color: Color
    value: int
    count: int = 1

    def is_action(self) -> bool:
        return self.value >= 9

    def is_wild(self) -> bool:
        return self.color == Color.WILD

    def display(self) -> str:
        if self.is_wild():
            return '萬能+4' if self.value == Action.WILD_DRAW_FOUR else '萬能'
        val = self.value if self.value < 9 else ACTION_NAMES.get(self.value, str(self.value))
        return f'{COLOR_NAMES.get(self.color, "?")}{val}'

# ── Deck factory ──────────────────────────────────────────────────────────────

def create_deck() -> List[Card]:
    deck = []
    for color in (Color.RED, Color.YELLOW, Color.GREEN, Color.BLUE):
        deck.append(Card(color, 0))                          # one 0
        for i in range(1, 10):
            deck.extend([Card(color, i)] * 2)                # two each 1–9
        for act in (Action.SKIP, Action.REVERSE, Action.DRAW_TWO):
            deck.extend([Card(color, act)] * 2)              # two each
    for _ in range(4):
        deck.append(Card(Color.WILD, Action.WILD))
        deck.append(Card(Color.WILD, Action.WILD_DRAW_FOUR))
    random.shuffle(deck)
    return deck


def _can_play(card: Card, top_color: Optional[Color], top_value: Optional[int]) -> bool:
    """Can *card* be placed on top of the discard pile?"""
    if card.is_wild():
        return True
    return card.color == top_color or card.value == top_value


# ── Game state ────────────────────────────────────────────────────────────────

class UNOGame:
    def __init__(self, players: List[int], guild_id: Optional[int] = None, channel_id: Optional[int] = None):
        self.players = players
        self.guild_id = guild_id
        self.channel_id = channel_id
        self.hands: Dict[int, List[Card]] = {p: [] for p in players}
        self.deck = create_deck()
        self.discard: List[Card] = []
        self.drawn_cards: Dict[int, List[Card]] = {p: [] for p in players}  # cards drawn this turn
        self.selected: Dict[int, set] = {p: set() for p in players}
        self.current_idx = 0
        self.direction = 1
        self.current_color: Optional[Color] = None
        self.current_value: Optional[int] = None
        self.game_over = False
        self.winner: Optional[int] = None
        self.uno_called: Dict[int, bool] = {p: False for p in players}
        self.created_at = time.time()

        # Deal
        for _ in range(7):
            for pid in players:
                if self.deck:
                    self.hands[pid].append(self.deck.pop())

        # Flip top card — retry if Wild until we get a non-wild starter
        while True:
            top = self.deck.pop()
            self.discard.append(top)
            if not top.is_wild():
                self.current_color = top.color
                self.current_value = top.value
                break

    # ── Helpers ────────────────────────────────────────────────────────────

    @property
    def current_player(self) -> int:
        return self.players[self.current_idx]

    @property
    def is_current_cpu(self) -> bool:
        return is_cpu(self.current_player)

    def _next(self):
        self.current_idx = (self.current_idx + self.direction) % len(self.players)

    def _skip_next(self):
        self._next()
        self._next()

    def draw(self, pid: int, n: int = 1) -> List[Card]:
        drawn = []
        for _ in range(n):
            if not self.deck:
                self._reshuffle()
            if not self.deck:
                break
            drawn.append(self.deck.pop())
        self.hands[pid].extend(drawn)
        self.drawn_cards[pid].extend(drawn)
        return drawn

    def _reshuffle(self):
        if len(self.discard) <= 1:
            return
        self.deck = self.discard[:-1]
        self.discard = [self.discard[-1]]
        random.shuffle(self.deck)

    # ── Toggle / clear selection ───────────────────────────────────────────

    def toggle(self, pid: int, idx: int):
        s = self.selected[pid]
        s.discard(idx) if idx in s else s.add(idx)

    def clear_sel(self, pid: int):
        self.selected[pid].clear()

    # ── Play card ──────────────────────────────────────────────────────────

    def play_card(self, pid: int, idx: int, *, chosen_color: Optional[Color] = None) -> Tuple[bool, str]:
        if self.game_over:
            return False, '遊戲已結束'
        if pid != self.current_player:
            return False, f'不是你的回合'
        hand = self.hands[pid]
        if idx < 0 or idx >= len(hand):
            return False, '無效的牌'
        card = hand[idx]

        if not _can_play(card, self.current_color, self.current_value):
            return False, f'不能出牌（顏色/數字/萬能才可出）'

        # Remove from hand
        hand.pop(idx)
        # Recompute selected indices since cards shifted
        if card.is_wild():
            # Wild needs a color choice
            color = chosen_color or random.choice([Color.RED, Color.YELLOW, Color.GREEN, Color.BLUE])
            self.current_color = color
            self.current_value = card.value

        self.current_color = card.color if not card.is_wild() else self.current_color
        if not card.is_wild():
            self.current_value = card.value

        self.discard.append(card)
        # Clear drawn cards for next player
        self.drawn_cards = {p: [] for p in self.players}
        self.selected[pid] = set()

        # Check win
        if not hand:
            self.game_over = True
            self.winner = pid
            return True, 'win'

        # Apply action effects
        if card.value == Action.SKIP or (card.value == Action.REVERSE and len(self.players) == 2):
            self._skip_next()
            return True, f'effect_skip:{card.display()}'
        if card.value == Action.REVERSE and len(self.players) > 2:
            self.direction *= -1
            self._next()
            return True, f'effect_reverse:{card.display()}'
        if card.value == Action.DRAW_TWO:
            self._next()
            victim = self.current_player
            self.draw(victim, 2)
            self._next()  # skip victim
            return True, f'effect_draw2:{card.display()}:{victim}'
        if card.value == Action.WILD_DRAW_FOUR:
            self._next()
            victim = self.current_player
            self.draw(victim, 4)
            self._next()  # skip victim
            return True, f'effect_wild4:{card.display()}:{victim}'

        self._next()
        return True, 'ok'

    # ── Pass / skip (draw penalty) ─────────────────────────────────────────

    def pass_turn(self, pid: int):
        """Draw 1 card and pass. The turn ends, so clear this player's
        draw-tracker (otherwise their next turn would look like they'd
        already drawn)."""
        self.draw(pid, 1)
        self.selected[pid] = set()
        self.drawn_cards[pid] = []
        self._next()

    def clear_drawn_all(self, except_pid: int):
        for p in self.players:
            if p != except_pid:
                self.drawn_cards[p] = []

    # ── Uno call ───────────────────────────────────────────────────────────

    def call_uno(self, pid: int) -> bool:
        if len(self.hands[pid]) == 1:
            self.uno_called[pid] = True
            return True
        return False

    # ── Win check ──────────────────────────────────────────────────────────

    def has_won(self, pid: int) -> bool:
        return len(self.hands[pid]) == 0

    # ── Info ────————─——─——─——─——─——─——─——─——─——─——─——─——─——─——─——─——─

    def card_counts(self) -> Dict[int, int]:
        return {p: len(self.hands[p]) for p in self.players}


# ── CPU / AI helpers ────────────────────────────────────────────────────────

_CPU_BASE = -1000


def CPU_PREFIX(slot: int) -> int:
    """Generate a negative ID for a CPU player. Slot = 1-based position."""
    return _CPU_BASE - slot


def is_cpu(uid: int) -> bool:
    """Return True if uid belongs to a CPU player."""
    return uid < _CPU_BASE


def cpu_name(uid: int) -> str:
    """Human-readable CPU name."""
    slot = -uid - _CPU_BASE - 1
    return f'電腦 #{slot + 1}'


def ai_pick_card(game: UNOGame, cpu_id: int) -> Optional[int]:
    """
    AI picks the best card index from this CPU's hand.

    Strategy (score-based):
      1. Color match + high value = best (dump points)
      2. Value match = second best
      3. Action cards get strategic bonuses
      4. Wild = last resort (save for when truly stuck)
      5. Return None if nothing playable (CPU will draw)
    """
    hand = game.hands.get(cpu_id, [])
    if not hand:
        return None

    top_color = game.current_color
    top_value = game.current_value
    best_idx = None
    best_score = -1

    for i, card in enumerate(hand):
        if not _can_play(card, top_color, top_value):
            continue

        score = 0

        # Prefer wilds LAST (low base score)
        if card.is_wild():
            score = 1 if card.value == Action.WILD else 5
        else:
            # Color match bonus
            if card.color == top_color:
                score += 10
            # Value match is second-best
            elif card.value == top_value:
                score += 8

            # Higher value = more points to dump
            score += card.value

            # Action card strategic value
            if card.value == Action.SKIP:
                score += 15
            elif card.value == Action.DRAW_TWO:
                score += 20
            elif card.value == Action.REVERSE:
                score += 12

        # Tiny jitter to avoid identical behavior in replay
        score += random.random() * 3

        if score > best_score:
            best_score = score
            best_idx = i

    return best_idx


def ai_choose_color(game: UNOGame, cpu_id: int) -> Color:
    """Pick the color this CPU has most of in their remaining hand."""
    hand = game.hands.get(cpu_id, [])
    color_counts: Dict[Color, int] = {c: 0 for c in (Color.RED, Color.YELLOW, Color.GREEN, Color.BLUE)}
    for card in hand:
        if not card.is_wild():
            color_counts[card.color] = color_counts.get(card.color, 0) + 1
    return max(color_counts, key=color_counts.get)
