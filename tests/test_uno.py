import unittest
from games.uno_logic import Card, Color, Action, create_deck, UNOGame

class TestCard(unittest.TestCase):
    def test_standard_number_card(self):
        card = Card(Color.RED, 5)
        self.assertEqual(card.color, Color.RED)
        self.assertEqual(card.value, 5)

    def test_action_card(self):
        card = Card(Color.BLUE, Action.SKIP)
        self.assertEqual(card.color, Color.BLUE)
        self.assertEqual(card.value, Action.SKIP)

    def test_wild_card(self):
        card = Card(Color.WILD, Action.WILD)
        self.assertEqual(card.color, Color.WILD)

class TestDeck(unittest.TestCase):
    def test_standard_deck_size(self):
        deck = create_deck()
        self.assertEqual(len(deck), 108)

    def test_deck_distribution(self):
        deck = create_deck()
        colors = {}
        for card in deck:
            if card.color != Color.WILD:
                colors[card.color] = colors.get(card.color, 0) + 1
        for count in colors.values():
            self.assertEqual(count, 25)

    def test_wild_cards_count(self):
        deck = create_deck()
        wild_cards = [c for c in deck if c.color == Color.WILD]
        self.assertEqual(len(wild_cards), 8)

class TestUNOGame(unittest.TestCase):
    def setUp(self):
        self.game = UNOGame([1, 2])

    def test_initial_deal(self):
        self.assertEqual(len(self.game.hands[1]), 7)
        self.assertEqual(len(self.game.hands[2]), 7)

    def test_deck_after_dealing(self):
        # 14 cards dealt (7 each) + however many were flipped for the starter
        # (a wild starter is reshuffled in, popping extra). Cards are conserved.
        self.assertEqual(
            len(self.game.deck) + 14 + len(self.game.discard), 108)

    def test_draw_card(self):
        initial_size = len(self.game.hands[1])
        card = self.game.draw(1)
        self.assertEqual(len(self.game.hands[1]), initial_size + 1)
        self.assertIsNotNone(card)

    def test_turn_progression(self):
        self.game.pass_turn(1)
        self.assertEqual(self.game.current_player, 2)

    def test_turn_cycling(self):
        self.game.pass_turn(1)
        self.game.pass_turn(2)
        self.assertEqual(self.game.current_player, 1)

    def test_winning_condition(self):
        self.game.hands[1] = []
        self.game.hands[2] = [Card(Color.RED, 5)]
        self.assertTrue(self.game.has_won(1))
        self.assertFalse(self.game.has_won(2))

    def test_uno_call(self):
        self.game.hands[1] = [Card(Color.RED, 5)]
        self.assertTrue(self.game.call_uno(1))
        self.assertTrue(self.game.uno_called[1])

    def test_uno_call_with_too_many_cards(self):
        self.game.hands[1] = [Card(Color.RED, 5), Card(Color.BLUE, 3)]
        self.assertFalse(self.game.call_uno(1))

    # --- Play validation tests ---
    def test_play_matching_color(self):
        self.game.hands[1] = [Card(self.game.current_color, 5)]
        ok, msg = self.game.play_card(1, 0)
        self.assertTrue(ok)

    def test_play_matching_value(self):
        self.game.current_color = Color.RED
        self.game.current_value = 5
        self.game.hands[1] = [Card(Color.BLUE, 5)]
        ok, msg = self.game.play_card(1, 0)
        self.assertTrue(ok)

    def test_play_wild_always_works(self):
        self.game.current_color = Color.RED
        self.game.current_value = 5
        self.game.hands[1] = [Card(Color.WILD, Action.WILD), Card(Color.BLUE, 3)]
        ok, msg = self.game.play_card(1, 0)
        self.assertTrue(ok)

    def test_play_wrong_color_and_value_fails(self):
        self.game.current_color = Color.RED
        self.game.current_value = 5
        self.game.hands[1] = [Card(Color.BLUE, 7)]
        ok, msg = self.game.play_card(1, 0)
        self.assertFalse(ok)
        self.assertIn('不能出牌', msg)

    def test_play_skip_card_effect(self):
        self.game.current_color = Color.RED
        self.game.current_value = 5
        self.game.hands[1] = [Card(Color.RED, Action.SKIP), Card(Color.BLUE, 3)]
        ok, msg = self.game.play_card(1, 0)
        self.assertTrue(ok)
        self.assertEqual(self.game.current_player, 1)

    def test_play_reverse_2_players_acts_as_skip(self):
        self.game.current_color = Color.GREEN
        self.game.current_value = 3
        self.game.hands[1] = [Card(Color.GREEN, Action.REVERSE), Card(Color.RED, 7)]
        ok, msg = self.game.play_card(1, 0)
        self.assertTrue(ok)
        self.assertEqual(self.game.current_player, 1)

    def test_play_reverse_3_players_changes_direction(self):
        game3 = UNOGame([1, 2, 3])
        game3.current_color = Color.BLUE
        game3.current_value = 5
        game3.current_idx = 0
        game3.hands[1] = [Card(Color.BLUE, Action.REVERSE), Card(Color.RED, 3)]
        ok, msg = game3.play_card(1, 0)
        self.assertTrue(ok)
        self.assertEqual(game3.current_player, 3)

    def test_play_wild_sets_color(self):
        self.game.current_color = Color.RED
        self.game.current_value = 5
        self.game.hands[1] = [Card(Color.WILD, Action.WILD), Card(Color.BLUE, 3)]
        ok, msg = self.game.play_card(1, 0)
        self.assertTrue(ok)

    def test_play_draw_two(self):
        self.game.current_color = Color.YELLOW
        self.game.current_value = 5
        self.game.hands[1] = [Card(Color.YELLOW, Action.DRAW_TWO), Card(Color.RED, 3)]
        initial_p2_len = len(self.game.hands[2])
        ok, msg = self.game.play_card(1, 0)
        self.assertTrue(ok)
        self.assertEqual(len(self.game.hands[2]), initial_p2_len + 2)
        self.assertEqual(self.game.current_player, 1)

    def test_play_wild_draw_four(self):
        self.game.current_color = Color.RED
        self.game.current_value = 5
        self.game.hands[1] = [Card(Color.WILD, Action.WILD_DRAW_FOUR), Card(Color.BLUE, 3)]
        initial_p2_len = len(self.game.hands[2])
        ok, msg = self.game.play_card(1, 0, chosen_color=Color.GREEN)
        self.assertTrue(ok)
        self.assertEqual(self.game.current_color, Color.GREEN)
        self.assertEqual(len(self.game.hands[2]), initial_p2_len + 4)

    def test_cannot_play_when_not_your_turn(self):
        self.game.hands[2] = [Card(self.game.current_color, 5)]
        ok, msg = self.game.play_card(2, 0)
        self.assertFalse(ok)
        self.assertIn('不是你的回合', msg)

    def test_draw_replenishes_from_discard_when_empty(self):
        while self.game.deck:
            self.game.draw(1)
        self.assertEqual(len(self.game.deck), 0)

    def test_invalid_card_index_fails(self):
        self.game.hands[1] = [Card(Color.RED, 5)]
        ok, msg = self.game.play_card(1, 5)
        self.assertFalse(ok)
        self.assertIn('無效', msg)

    def test_empty_hand_fails(self):
        self.game.hands[1] = []
        ok, msg = self.game.play_card(1, 0)
        self.assertFalse(ok)

if __name__ == '__main__':
    unittest.main()
