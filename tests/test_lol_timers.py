"""Tests for cogs/lol_timers.py：大型野怪 / 兵營計時、Porofessor log 的補包與聊天室。
fixture 是 2026-10-18 幾場隨機單中的 gate(`poro 0`)輸出，玩家名字已換成 P0…(英雄名保留)。"""
import gzip
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from cogs.lol_timers import (BARON_FIRST, BARON_RESPAWN, DRAGON_FIRST, DRAGON_RESPAWN, ELDER_RESPAWN,  # noqa: E402
                             INHIB_RESPAWN, RELIC_RESPAWN, PoroFeed, objective_timers)

FIXTURE = os.path.join(os.path.dirname(__file__), 'fixtures', 'poro_aram.txt.gz')


def game(t, events=(), mp=11):
    players = [{'riotId': 'Me#1', 'riotIdGameName': 'Me', 'team': 'ORDER', 'championName': '阿璃'},
               {'riotId': 'Foe#1', 'riotIdGameName': 'Foe', 'team': 'CHAOS', 'championName': '犽宿'}]
    return {'gameData': {'gameTime': t, 'mapNumber': mp}, 'activePlayer': {'riotId': 'Me#1'}, 'allPlayers': players,
            'events': {'Events': list(events)}}


def by_key(ts):
    return {t['key']: t for t in ts}


class TestRiftTimers(unittest.TestCase):
    def test_first_spawns(self):
        t = by_key(objective_timers(game(60)))
        self.assertEqual(t['dragon']['at'], DRAGON_FIRST)
        self.assertEqual(t['baron']['at'], BARON_FIRST)
        self.assertIn('grubs', t)
        self.assertIn('herald', t)
        self.assertFalse(t['dragon']['up'])

    def test_dragon_respawn_and_side(self):
        evs = [{'EventName': 'DragonKill', 'EventTime': 400.4, 'DragonType': 'Fire', 'KillerName': 'Foe', 'Stolen': 'False'}]
        d = by_key(objective_timers(game(410, evs)))['dragon']
        self.assertEqual(d['at'], round(400.4 + DRAGON_RESPAWN))
        self.assertEqual(d['side'], 'enemy')
        self.assertIn('敵方 1', d['note'])
        self.assertIn('炎龍', d['note'])

    def test_soul_then_elder(self):
        evs = [{'EventName': 'DragonKill', 'EventTime': 300 + i * 310, 'DragonType': 'Water', 'KillerName': 'Me'} for i in range(4)]
        d = by_key(objective_timers(game(1300, evs)))['dragon']
        self.assertEqual(d['label'], '遠古龍')
        self.assertEqual(d['at'], round(evs[-1]['EventTime'] + ELDER_RESPAWN))

    def test_baron_respawn_and_herald_gone(self):
        evs = [{'EventName': 'BaronKill', 'EventTime': 1250, 'KillerName': 'Me'},
               {'EventName': 'HeraldKill', 'EventTime': 920, 'KillerName': 'Me'}]
        t = by_key(objective_timers(game(1300, evs)))
        self.assertEqual(t['baron']['at'], 1250 + BARON_RESPAWN)
        self.assertEqual(t['baron']['side'], 'ally')
        self.assertNotIn('herald', t)
        self.assertNotIn('grubs', t)          # 先鋒出現之後巢蟲就不列

    def test_up_when_time_passed(self):
        self.assertTrue(by_key(objective_timers(game(DRAGON_FIRST + 5)))['dragon']['up'])


class TestInhib(unittest.TestCase):
    def test_inhib_down_then_respawned(self):
        evs = [{'EventName': 'InhibKilled', 'EventTime': 600, 'InhibKilled': 'Barracks_T2_C1', 'KillerName': 'Me'}]
        t = by_key(objective_timers(game(610, evs, mp=12)))
        i = t['inhib:Barracks_T2_C1']
        self.assertEqual((i['label'], i['side'], i['at']), ('敵方兵營', 'enemy', 600 + INHIB_RESPAWN[12]))
        evs.append({'EventName': 'InhibRespawned', 'EventTime': 850, 'InhibRespawned': 'Barracks_T2_C1'})
        self.assertNotIn('inhib:Barracks_T2_C1', by_key(objective_timers(game(860, evs, mp=12))))

    def test_live_client_names(self):
        # Live Client 真實格式(2026-10-18 隨機單中)：TOrder = 藍方(ORDER)
        evs = [{'EventName': 'InhibKilled', 'EventTime': 801, 'InhibKilled': 'Inhib_TOrder_L1_P1_196971597_0'},
               {'EventName': 'InhibKilled', 'EventTime': 883, 'InhibKilled': 'Inhib_TChaos_L1_P1_2432143522_0'}]
        t = objective_timers(game(900, evs, mp=12))
        self.assertEqual([(x['label'], x['side']) for x in t], [('我方兵營', 'ally'), ('敵方兵營', 'enemy')])

    def test_rift_lane_name(self):
        evs = [{'EventName': 'InhibKilled', 'EventTime': 1500, 'InhibKilled': 'Barracks_T1_L1'}]
        self.assertEqual(by_key(objective_timers(game(1510, evs)))['inhib:Barracks_T1_L1']['label'], '我方上路兵營')

    def test_aram_has_no_rift_objectives(self):
        self.assertEqual(objective_timers(game(100, mp=12)), [])


class TestPoroFeed(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with gzip.open(FIXTURE, 'rt', encoding='utf-8') as f:
            cls.lines = f.read().splitlines()

    def test_game_window(self):
        # 10/18 00:37 開的那場：之前場次的行都不能混進來
        f = PoroFeed('20261018003700000')
        f.feed_text('\n'.join(self.lines))
        self.assertTrue(f.chat)
        self.assertTrue(all(c['t'] < 40 * 60 for c in f.chat))
        self.assertLessEqual(len(f.chat), 40)

    def test_chat_parse(self):
        f = PoroFeed('20261018000000000')
        f.feed_text('\n'.join(self.lines))
        f2 = PoroFeed('20261018000000000')
        f2.feed_text('20261018003803756\tC\t00:30 Heart (科加斯): 亞菲利歐\n20261018002540246\tC\t14:52 敵隊 消滅了我方！')
        self.assertEqual(f2.chat, [{'t': 30, 'who': 'Heart', 'champ': '科加斯', 'text': '亞菲利歐'}])  # 早於起點的那行被丟掉
        f3 = PoroFeed('20261018000000000')
        f3.feed_text('20261018002540246\tC\t14:52 敵隊 消滅了我方！\n20261018002518531\tC\t14:30 Heart (吶兒) 擊殺了 P0 (伊澤瑞爾)')
        self.assertEqual([c.get('sys') for c in f3.chat], [True])   # 第二行時間更早(同一批裡倒退)→ 丟掉
        self.assertTrue(any(c.get('sys') for c in f.chat))
        self.assertTrue(any(not c.get('sys') for c in f.chat))

    def test_relics_and_timer(self):
        f = PoroFeed('20261018000000000')
        f.feed_text('20261018002528557\tJ\t14:40\t{"jungle_camps":{"jungle_camp_3":"{\\"name\\":\\"Relic Health\\",\\"alive\\":false,\\"vision\\":false,\\"icon_status\\":\\"0\\"}"}}\n'
                    '20261018002529000\tJ\t14:41\t{"jungle_camps":{"jungle_camp_0":"{\\"name\\":\\"Relic Health\\",\\"alive\\":true}"}}')
        self.assertEqual(f.relics, {3: {'alive': False, 't': 880}, 0: {'alive': True, 't': 881}})
        t = by_key(objective_timers(game(900, mp=12), f))
        self.assertEqual(t['relic:3']['at'], 880 + RELIC_RESPAWN)
        self.assertFalse(t['relic:3']['up'])
        self.assertTrue(t['relic:0']['up'])
        self.assertEqual(t['relic:0']['label'], '補包 1')

    def test_incremental_same_ms(self):
        a = '20261018003803756\tC\t00:30 Heart (科加斯): 一'
        b = '20261018003803756\tC\t00:30 Heart (科加斯): 二'
        f = PoroFeed('20261018000000000')
        f.feed_text(a)
        self.assertEqual(f.since, '20261018003803755')
        f.feed_text(a + '\n' + b)                 # 下一次 gate 從 since 之後給，含同一毫秒
        self.assertEqual([c['text'] for c in f.chat], ['一', '二'])

    def test_bad_lines_ignored(self):
        f = PoroFeed('20261018000000000')
        self.assertFalse(f.feed_text('garbage\n20261018003803756\tJ\t1:00\tnot json\n20261018003803757\tC\tno clock'))


if __name__ == '__main__':
    unittest.main()
