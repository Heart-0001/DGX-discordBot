"""Tests for cogs/valo_gep.py：Overwolf GEP log(riot-gate owgep 輸出)→ 進階 TAB 資料。
fixture 是 2026-10-18 一場競技(Infinity，11:13 輸)的完整 gate 輸出，其他玩家的名字 / puuid 已換成假的。"""
import gzip
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from cogs.valo_gep import GepMatch, parse_lines, weapon_name  # noqa: E402

FIXTURE = os.path.join(os.path.dirname(__file__), 'fixtures', 'gep_valo_match.txt.gz')
MID = '73d70b70-3aab-4a1e-8161-be0fa127c535'
ME = 'e674ef05-d281-51b9-83bb-6ef07fd9330f'


def load() -> str:
    with gzip.open(FIXTURE, 'rt', encoding='utf-8') as f:
        return f.read()


def line(ts: str, key: str, value) -> str:
    v = json.dumps(value) if isinstance(value, dict) else value
    return f"{ts}\t{json.dumps({'featureName': 'match_info', 'key': key, 'value': v})}"


class TestParseLines(unittest.TestCase):
    def test_skips_bad_lines(self):
        txt = '\n'.join(['garbage', '123\t{}', line('20261018220000000', 'round_number', '3'), '20261018220000001\tnot json'])
        self.assertEqual(parse_lines(txt), [('20261018220000000', 'round_number', '3')])

    def test_nested_json_value(self):
        (_, _, v), = parse_lines(line('20261018220000000', 'match_score', {'team_0': 1, 'team_1': 2}))
        self.assertEqual(v, {'team_0': 1, 'team_1': 2})


class TestWeaponName(unittest.TestCase):
    def test_codenames(self):
        self.assertEqual(weapon_name('TX_Hud_Rifles_Volcano'), 'Vandal')
        self.assertEqual(weapon_name('TX_Hud_Volcano'), 'Vandal')
        self.assertEqual(weapon_name('TX_Hud_Rifles_Ghost'), 'Phantom')
        self.assertEqual(weapon_name('TX_Hud_Pistol_Luger_S'), 'Ghost')
        self.assertEqual(weapon_name('TX_Hud_Sniper_Operater'), 'Operator')
        self.assertEqual(weapon_name('tx_hud_dmr'), 'Guardian')
        self.assertEqual(weapon_name('knife'), '近戰')
        self.assertEqual(weapon_name('TX_Hud_Wushu_X_Dagger'), '大招')
        self.assertEqual(weapon_name(''), '')

    def test_unknown_kept_readable(self):
        self.assertEqual(weapon_name('TX_Hud_Pistol_Compact'), 'Compact')


class TestFixtureMatch(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.g = GepMatch(MID)
        cls.g.feed_text(load())
        cls.p = cls.g.public()

    def test_match_state(self):
        self.assertTrue(self.p['started'])
        self.assertEqual(self.p['round'], 24)
        self.assertEqual(self.p['outcome'], 'defeat')
        self.assertEqual(self.g.since, '20261018230337186')   # 最後一行的時間 − 1 毫秒

    def test_round_reports(self):
        rs = self.p['rounds']
        self.assertEqual([r['n'] for r in rs], list(range(1, 25)))
        # hit = 頭(含擊殺爆頭) + 身 + 腿
        for r in rs:
            self.assertEqual(r['hit'], r['hs'] + r['bs'] + r['ls'], r)
        t = self.p['tot']
        self.assertEqual((t['dmg'], t['hit'], t['hs'], t['bs'], t['ls']), (3531, 71, 12, 54, 5))
        self.assertEqual((t['adr'], t['hsp'], t['bsp'], t['lsp']), (147, 17, 76, 7))

    def test_board_keeps_last_real_values(self):
        b = self.p['board']
        self.assertEqual(len(b), 10)                           # 打完 scoreboard 被清成 null，不能蓋掉
        me = b[ME]
        self.assertTrue(me['me'])
        self.assertEqual((me['k'], me['d'], me['a']), (19, 21, 4))
        self.assertEqual(sum(p['ally'] for p in b.values()), 5)

    def test_enemy_hidden_info_dropped(self):
        # 遊戲內 TAB 看不到敵方的錢和誰帶包 → 不往畫面送
        for p in self.p['board'].values():
            if not p['ally']:
                self.assertIsNone(p['money'])
                self.assertFalse(p['spike'])
        self.assertIsNotNone(self.p['board'][ME]['money'])

    def test_headshot_kills_by_name(self):
        # 有名字的人用 scoreboard 名字(去掉 #tag)對 kill_feed；自己 19 殺裡有 11 個爆頭
        self.assertEqual(self.p['board'][ME]['hsk'], 11)
        self.assertLessEqual(self.p['board'][ME]['hsk'], self.p['board'][ME]['k'])

    def test_hidden_players_matched_by_agent(self):
        # 隱藏名字的人 kill_feed 寫特務中文名 → 用 GLZ 那份的特務名對；同名特務用敵我分開
        hidden = [pu for pu, p in self.g.board.items() if p['agent'] == 'Vampire']   # 蕾娜，敵方
        match = {'teams': [{'mine': False, 'players': [{'puuid': hidden[0], 'agent': '蕾娜'}]}]}
        p = self.g.public(match)
        hs = sum(1 for f in self.g.feed if f['a'] == '蕾娜' and not f['at'] and f['hs'])
        self.assertGreater(hs, 0)
        self.assertEqual(p['board'][hidden[0]]['hsk'], hs)

    def test_feed_trimmed(self):
        self.assertEqual(len(self.p['feed']), 12)
        self.assertEqual(self.p['feed'][-1]['n'], 24)


class TestIncremental(unittest.TestCase):
    def test_split_feed_same_result(self):
        ls = load().splitlines()
        a = GepMatch(MID)
        a.feed_text('\n'.join(ls))
        b = GepMatch(MID)
        half = len(ls) // 2
        b.feed_text('\n'.join(ls[:half]))
        since = b.since
        b.feed_text('\n'.join(l for l in ls[half:] if l.split('\t', 1)[0] > since))
        self.assertEqual(a.public(), b.public())

    def test_same_millisecond_split_across_polls(self):
        # 第一次只拿到同一毫秒的第一行；第二次 gate 回 since 之後(含那一毫秒)的全部 → 不重複、不漏
        k1 = line('20261018220000005', 'kill_feed', {'attacker': 'A', 'victim': 'B', 'headshot': True})
        k2 = line('20261018220000005', 'kill_feed', {'attacker': 'C', 'victim': 'D', 'headshot': False})
        g = GepMatch(MID)
        g.feed_text(line('20261018220000000', 'round_number', '1') + '\n' + k1)
        self.assertEqual(g.since, '20261018220000004')
        g.feed_text(k1 + '\n' + k2)
        self.assertEqual([f['a'] for f in g.feed], ['A', 'C'])

    def test_no_change_no_update(self):
        g = GepMatch(MID)
        self.assertTrue(g.feed_text(line('20261018220000000', 'round_number', '1')))
        self.assertFalse(g.feed_text(line('20261018220000001', 'round_number', '1')))
        self.assertIsNone(g.public()['tot'])


if __name__ == '__main__':
    unittest.main()
