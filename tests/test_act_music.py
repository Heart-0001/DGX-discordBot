"""Tests for MusicCog 給 Activity 點歌用的方法(queue_view / web_control / web_enqueue)：權限、移除比對、加入 vs 插入。"""
import asyncio
import os
import sys
import types
import unittest
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# 跟 test_repeat 一樣：沒有 bot token 也能 import MusicCog
discord_stub = types.ModuleType('discord')
discord_stub.PCMVolumeTransformer = MagicMock
discord_stub.FFmpegPCMAudio = MagicMock
discord_stub.Color = MagicMock()
discord_stub.Embed = MagicMock
discord_stub.VoiceClient = MagicMock
discord_stub.Interaction = MagicMock
ext_stub = types.ModuleType('discord.ext')
commands_stub = types.ModuleType('discord.ext.commands')
commands_stub.Cog = object
commands_stub.Bot = MagicMock
app_commands_stub = types.ModuleType('discord.app_commands')
app_commands_stub.command = lambda *a, **k: (lambda fn: fn)
app_commands_stub.describe = lambda *a, **k: (lambda fn: fn)
discord_stub.app_commands = app_commands_stub
ext_stub.commands = commands_stub
sys.modules.setdefault('discord', discord_stub)
sys.modules.setdefault('discord.ext', ext_stub)
sys.modules.setdefault('discord.ext.commands', commands_stub)
sys.modules.setdefault('discord.app_commands', app_commands_stub)
sys.modules.setdefault('ytmusicapi', MagicMock())

from cogs.music import MusicCog  # noqa: E402


CH = {n: types.SimpleNamespace(id=i, name=n) for i, n in enumerate(('vc1', 'vc2'), 1)}


def song(n: int) -> dict:
    return {'title': f'S{n}', 'webpage_url': f'https://www.youtube.com/watch?v=000000000{n:02d}', 'duration': 100 + n,
            'url': 'http://cdn.test/a.opus'}


def setup_cog(playing=True, paused=False, bot_channel='vc1'):
    bot = MagicMock()
    cog = MusicCog(bot)
    guild = MagicMock()
    guild.id = 1
    vc = MagicMock()
    vc.is_playing.return_value = playing and not paused
    vc.is_paused.return_value = paused
    vc.channel = CH.get(bot_channel)
    guild.voice_client = vc if bot_channel else None
    bot.get_guild.return_value = guild
    return cog, guild, vc


def member(channel='vc1', name='Amy'):
    m = MagicMock()
    m.display_name = name
    m.voice = MagicMock(channel=CH[channel]) if channel else None
    return m


class TestPermission(unittest.TestCase):
    def test_not_in_voice(self):
        cog, guild, _ = setup_cog()
        with self.assertRaisesRegex(ValueError, '語音頻道'):
            cog.web_control(guild, member(channel=None), 'skip')

    def test_other_channel(self):
        cog, guild, vc = setup_cog()
        with self.assertRaisesRegex(ValueError, 'bot 所在'):
            cog.web_control(guild, member(channel='vc2'), 'skip')
        vc.stop.assert_not_called()


class TestControl(unittest.TestCase):
    def test_pause_resume_skip(self):
        cog, guild, vc = setup_cog()
        self.assertIn('暫停', cog.web_control(guild, member(), 'pause'))
        vc.pause.assert_called_once()
        cog.web_control(guild, member(), 'skip')
        vc.stop.assert_called_once()
        self.assertTrue(cog.get_state(1).repeat_skip)
        with self.assertRaises(ValueError):
            cog.web_control(guild, member(), 'resume')          # 沒暫停

    def test_remove_checks_url(self):
        cog, guild, _ = setup_cog()
        st = cog.get_state(1)
        st.queue = [song(1), song(2), song(3)]
        with self.assertRaisesRegex(ValueError, '佇列已經變了'):
            cog.web_control(guild, member(), 'remove', index=1, url=song(3)['webpage_url'])
        self.assertEqual(len(st.queue), 3)
        cog.web_control(guild, member(), 'remove', index=1, url=song(2)['webpage_url'])
        self.assertEqual([s['title'] for s in st.queue], ['S1', 'S3'])
        with self.assertRaises(ValueError):
            cog.web_control(guild, member(), 'remove', index=5)

    def test_unknown_action(self):
        cog, guild, _ = setup_cog()
        with self.assertRaises(ValueError):
            cog.web_control(guild, member(), 'nuke')


class TestEnqueue(unittest.TestCase):
    def run_(self, coro):
        return asyncio.run(coro)

    def test_append_playlist_and_insert_next(self):
        cog, guild, _ = setup_cog()
        st = cog.get_state(1)
        st.queue = [song(1)]
        cog.fetch_info = AsyncMock(return_value=[song(2), song(3)])
        self.run_(cog.web_enqueue(guild, member(name='Bob'), 'list', next_=False))
        self.assertEqual([s['title'] for s in st.queue], ['S1', 'S2', 'S3'])
        self.assertEqual(st.queue[1]['requested_by'], 'Bob')
        cog.fetch_info = AsyncMock(return_value=[song(4), song(5)])
        self.run_(cog.web_enqueue(guild, member(), 'x', next_=True))     # 插入只取第一首
        self.assertEqual([s['title'] for s in st.queue], ['S4', 'S1', 'S2', 'S3'])

    def test_idle_starts_playing(self):
        cog, guild, vc = setup_cog(playing=False)
        cog.fetch_info = AsyncMock(return_value=[song(1), song(2)])
        cog._start_song = MagicMock()
        text = self.run_(cog.web_enqueue(guild, member(), 'x', next_=False))
        self.assertIn('正在播放', text)
        cog._start_song.assert_called_once()
        self.assertEqual([s['title'] for s in cog.get_state(1).queue], ['S2'])

    def test_empty_query_and_not_found(self):
        cog, guild, _ = setup_cog()
        with self.assertRaisesRegex(ValueError, '輸入'):
            self.run_(cog.web_enqueue(guild, member(), '  ', next_=False))
        cog.fetch_info = AsyncMock(return_value=[])
        with self.assertRaisesRegex(ValueError, '找不到'):
            self.run_(cog.web_enqueue(guild, member(), 'zzz', next_=False))


class TestQueueView(unittest.TestCase):
    def test_view(self):
        cog, _, _ = setup_cog(paused=True)
        st = cog.get_state(1)
        st.current = song(0)
        st.queue = [song(i) for i in range(1, 61)]
        v = cog.queue_view(1)
        self.assertEqual(v['current']['title'], 'S0')
        self.assertEqual((len(v['queue']), v['total']), (50, 60))
        self.assertTrue(v['paused'])
        self.assertIsNone(cog.queue_view(999)['current'])


if __name__ == '__main__':
    unittest.main()
