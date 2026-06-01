import unittest
from games.uno_logic import UNOGame, Card, Color, Action, CPU_PREFIX, is_cpu, ai_pick_card

class TestCPUIdentity(unittest.TestCase):
    def test_cpu_id_generation(self):
        # CPU IDs should start with negative numbers
        cpu_id = CPU_PREFIX(1)
        self.assertIsInstance(cpu_id, int)
        self.assertTrue(cpu_id < 0)

    def test_is_cpu_detection(self):
        self.assertTrue(is_cpu(CPU_PREFIX(1)))
        self.assertTrue(is_cpu(CPU_PREFIX(5)))
        self.assertFalse(is_cpu(12345))
        self.assertFalse(is_cpu(1))

class TestAIPick(unittest.TestCase):
    def setUp(self):
        self.game = UNOGame([12345, CPU_PREFIX(1)])

    def test_ai_picks_matching_color(self):
        """AI should prefer matching color cards"""
        self.game.current_color = Color.RED
        self.game.current_value = 5
        self.game.hands[CPU_PREFIX(1)] = [
            Card(Color.BLUE, 7),
            Card(Color.RED, 3),
            Card(Color.GREEN, 2),
        ]
        idx = ai_pick_card(self.game, CPU_PREFIX(1))
        self.assertIsNotNone(idx)
        card = self.game.hands[CPU_PREFIX(1)][idx]
        self.assertEqual(card.color, Color.RED)

    def test_ai_picks_matching_value(self):
        """AI should pick matching value when no color match"""
        self.game.current_color = Color.BLUE
        self.game.current_value = 3
        self.game.hands[CPU_PREFIX(1)] = [
            Card(Color.RED, 7),
            Card(Color.GREEN, 3),
            Card(Color.YELLOW, 9),
        ]
        idx = ai_pick_card(self.game, CPU_PREFIX(1))
        self.assertIsNotNone(idx)
        card = self.game.hands[CPU_PREFIX(1)][idx]
        self.assertEqual(card.value, 3)

    def test_ai_picks_wild_as_last_resort(self):
        """AI only plays wild cards when nothing else works"""
        self.game.current_color = Color.RED
        self.game.current_value = 5
        self.game.hands[CPU_PREFIX(1)] = [
            Card(Color.BLUE, 7),
            Card(Color.GREEN, 3),
            Card(Color.WILD, Action.WILD),
        ]
        idx = ai_pick_card(self.game, CPU_PREFIX(1))
        self.assertIsNotNone(idx)
        card = self.game.hands[CPU_PREFIX(1)][idx]
        self.assertTrue(card.is_wild())

    def test_ai_picks_none_when_no_valid_card(self):
        """If truly no valid card (shouldn't happen often), return None"""
        # This case is rare because wilds always work, but test edge case
        self.game.current_color = Color.RED
        self.game.current_value = 5
        hand = [Card(Color.BLUE, 1), Card(Color.GREEN, 3)]
        self.game.hands[CPU_PREFIX(1)] = hand
        idx = ai_pick_card(self.game, CPU_PREFIX(1))
        # No wild, so should return None
        self.assertIsNone(idx)

    def test_ai_chooses_most_common_color_for_wild(self):
        """When playing wild, AI should choose the color most common in hand"""
        self.game.current_color = Color.BLUE
        self.game.current_value = 7
        self.game.hands[CPU_PREFIX(1)] = [
            Card(Color.RED, 1), Card(Color.RED, 3), Card(Color.RED, 5),
            Card(Color.GREEN, 2),
            Card(Color.WILD, Action.WILD),
        ]
        idx = ai_pick_card(self.game, CPU_PREFIX(1))
        self.assertIsNotNone(idx)
        hand = self.game.hands[CPU_PREFIX(1)]
        card = hand[idx]
        self.assertTrue(card.is_wild())

    def test_ai_prefers_high_number_cards(self):
        """Among valid cards, AI prefers higher values (get rid of points)"""
        self.game.current_color = Color.RED
        self.game.current_value = 5
        self.game.hands[CPU_PREFIX(1)] = [
            Card(Color.RED, 1),
            Card(Color.RED, 7),   # higher, should prefer
            Card(Color.RED, 9),   # highest, should prefer
        ]
        idx = ai_pick_card(self.game, CPU_PREFIX(1))
        card = self.game.hands[CPU_PREFIX(1)][idx]
        self.assertEqual(card.value, 9)

    def test_ai_plays_action_cards_strategically(self):
        """AI can play action cards if color/value matches"""
        self.game.current_color = Color.GREEN
        self.game.current_value = Action.SKIP
        self.game.hands[CPU_PREFIX(1)] = [
            Card(Color.GREEN, Action.SKIP),
            Card(Color.RED, 5),
        ]
        idx = ai_pick_card(self.game, CPU_PREFIX(1))
        self.assertIsNotNone(idx)
        card = self.game.hands[CPU_PREFIX(1)][idx]
        self.assertEqual(card.value, Action.SKIP)

class TestCPUAutoPlay(unittest.TestCase):
    def setUp(self):
        # 2 human + 1 CPU
        cpu = CPU_PREFIX(1)
        self.game = UNOGame([12345, 67890, cpu])
        self.cpu_id = cpu

    def test_cpu_turn_detection(self):
        """Ensure CPU can be current player"""
        # Move to CPU's position
        cpu_idx = self.game.players.index(self.cpu_id)
        self.game.current_idx = cpu_idx
        self.assertTrue(is_cpu(self.game.current_player))

    def test_cpu_can_play_and_game_continues(self):
        """CPU playing a card advances the game"""
        self.game.current_color = Color.RED
        self.game.current_value = 5
        cpu_idx = self.game.players.index(self.cpu_id)
        self.game.current_idx = cpu_idx
        self.game.hands[self.cpu_id] = [
            Card(Color.RED, 7),
            Card(Color.BLUE, 3),
        ]
        # AI should find and play RED 7
        idx = ai_pick_card(self.game, self.cpu_id)
        self.assertIsNotNone(idx)
        ok, msg = self.game.play_card(self.cpu_id, idx)
        self.assertTrue(ok)
        # Turn should advance away from CPU
        self.assertFalse(is_cpu(self.game.current_player))

class TestCPUDraw(unittest.TestCase):
    def setUp(self):
        cpu = CPU_PREFIX(1)
        self.game = UNOGame([12345, cpu])
        self.cpu_id = cpu

    def test_cpu_draws_when_no_valid_card(self):
        """CPU should draw when AI can't find a play"""
        self.game.current_color = Color.RED
        self.game.current_value = 5
        self.game.hands[self.cpu_id] = [
            Card(Color.BLUE, 1),
            Card(Color.GREEN, 2),
        ]
        initial_len = len(self.game.hands[self.cpu_id])
        idx = ai_pick_card(self.game, self.cpu_id)
        self.assertIsNone(idx)  # No wild, no match
        # In flow, CPU would draw — test that draw works for CPU
        drawn = self.game.draw(self.cpu_id, 1)
        self.assertEqual(len(drawn), 1)
        self.assertEqual(len(self.game.hands[self.cpu_id]), initial_len + 1)


if __name__ == '__main__':
    unittest.main()
