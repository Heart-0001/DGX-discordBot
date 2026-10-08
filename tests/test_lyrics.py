"""Tests for cogs/lyrics.py 的純函式：LRC 解析、歌名清理、LRCLIB 結果挑選。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from cogs.lyrics_match import (clean_artist, names_match, parse_lrc, pick_candidate,  # noqa: E402
                               pick_result, title_candidates, usable_synced)


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

    def test_lrclib_title_must_match(self):
        # 同歌手、長度相近的別首歌不能收(My Jinji → Travel Agency)
        r = [{'artistName': '落日飛車 Sunset Rollercoaster', 'trackName': 'Travel Agency',
              'duration': 288, 'syncedLyrics': 'x'}]
        self.assertIsNone(pick_result(r, 290, 'Sunset Rollercoaster', ['My Jinji']))
        self.assertIsNotNone(pick_result(r, 290, 'Sunset Rollercoaster', ['Travel Agency']))


class TestNamesMatch(unittest.TestCase):
    def test_simplified_vs_traditional(self):
        self.assertTrue(names_match('美秀集团', '美秀集團'))
        self.assertTrue(names_match('卷烟', '捲菸'))

    def test_bilingual_and_featuring(self):
        self.assertTrue(names_match('告五人', '告五人 Accusefive'))
        self.assertTrue(names_match('美秀集团,林汉庭', '美秀集團'))

    def test_different(self):
        self.assertFalse(names_match('热心市民66', '告五人 Accusefive'))
        self.assertFalse(names_match('', '告五人'))


class TestPickCandidate(unittest.TestCase):
    def test_rejects_preview_cover_and_other_song(self):
        cands = [
            {'artist': '美秀集团', 'title': '卷烟', 'duration': 30},          # 試聽片段
            {'artist': '琳誼 Ring', 'title': '捲菸', 'duration': 233},        # 同名別人的歌
            {'artist': '美秀集团', 'title': '电火王', 'duration': 232},        # 同歌手別首
            {'artist': '美秀集团', 'title': '卷烟', 'duration': 232},
        ]
        got = pick_candidate(cands, 233, '美秀集團', ['捲菸'])
        self.assertEqual([(c['title'], c['duration']) for c in got], [('卷烟', 232)])

    def test_artist_from_video_title(self):
        # 上傳者是 JVR Music，歌手名在標題裡
        cands = [{'artist': '周杰倫', 'title': '晴天', 'duration': 269}]
        who = 'JVR Music 周杰倫 Jay Chou【晴天 Sunny Day】Official MV'
        self.assertEqual(len(pick_candidate(cands, 271, who, ['晴天 Sunny Day', '晴天'])), 1)

    def test_usable_synced_needs_enough_lines(self):
        self.assertFalse(usable_synced([{'t': 0, 'text': '純音樂，請欣賞'}]))
        self.assertTrue(usable_synced([{'t': i, 'text': f'l{i}'} for i in range(5)]))


if __name__ == '__main__':
    unittest.main()
