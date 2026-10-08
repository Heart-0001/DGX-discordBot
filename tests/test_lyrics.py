"""Tests for cogs/lyrics.py 的純函式：LRC 解析、歌名清理、LRCLIB 結果挑選。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from cogs.lyrics_match import clean_artist, parse_lrc, pick_result, title_candidates  # noqa: E402


class TestParseLrc(unittest.TestCase):
    def test_basic_and_sorted(self):
        lines = parse_lrc('[00:21.58]Second\n[00:05.10]First\nno stamp\n[01:02.5]Third')
        self.assertEqual([l['t'] for l in lines], [5.1, 21.58, 62.5])
        self.assertEqual(lines[0]['text'], 'First')

    def test_multiple_stamps_one_line(self):
        lines = parse_lrc('[00:10.00][00:50.00]Chorus')
        self.assertEqual([(l['t'], l['text']) for l in lines], [(10.0, 'Chorus'), (50.0, 'Chorus')])

    def test_empty_line_kept_for_gap(self):
        self.assertEqual(parse_lrc('[00:30.00]')[0]['text'], '')


class TestTitleCandidates(unittest.TestCase):
    def test_bracket_title_first(self):
        c = title_candidates('周杰倫 Jay Chou【晴天 Sunny Day】Official MV', 'JVR Music')
        self.assertEqual(c[0], '晴天 Sunny Day')
        self.assertIn('晴天', c)

    def test_noise_removed(self):
        c = title_candidates('告五人 Accusefive [ 愛人錯過 Mistakes ] Official Music Video', '告五人 Accusefive')
        self.assertIn('愛人錯過', c)
        self.assertFalse(any('Official' in x for x in c))

    def test_artist_dash_title(self):
        self.assertIn('Numb', title_candidates('Linkin Park - Numb (Official Video)', ''))

    def test_topic_channel(self):
        self.assertEqual(clean_artist('Ed Sheeran - Topic'), 'Ed Sheeran')


class TestPickResult(unittest.TestCase):
    R = [
        {'artistName': 'Someone Else', 'duration': 270, 'syncedLyrics': '[00:01.00]x'},
        {'artistName': '告五人', 'duration': 268, 'plainLyrics': 'y'},
        {'artistName': '告五人', 'duration': 271, 'syncedLyrics': '[00:01.00]z'},
    ]

    def test_prefers_artist_then_synced(self):
        self.assertEqual(pick_result(self.R, 270, '告五人 Accusefive')['syncedLyrics'], '[00:01.00]z')

    def test_rejects_wrong_duration(self):
        self.assertIsNone(pick_result(self.R, 120, '告五人'))

    def test_unknown_artist_needs_tight_duration(self):
        r = [{'artistName': 'X', 'duration': 274, 'syncedLyrics': 'a'}]
        self.assertIsNone(pick_result(r, 270, 'Nobody'))
        self.assertIsNotNone(pick_result(r, 273, 'Nobody'))


if __name__ == '__main__':
    unittest.main()
