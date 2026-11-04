"""語音 WS 掉線重連後播放卡死的回歸測試(2026-11-04)。

discord.py 2.7 AudioPlayer 在「斷線後等不到重連」時直接 return、沒呼叫 stop()，
執行緒死了 is_playing() 卻永遠 True → /play 全進隊列、沒人起播。
"""
import asyncio
import threading
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from tests.test_repeat import make_cog, make_song, make_vc  # 共用 discord stub
from cogs.music import GuildMusicState


class AbortingPlayer(threading.Thread):
    """照 discord.player.AudioPlayer 的狀態語意：Aborting playback 路徑不 stop() 就結束。"""

    def __init__(self, after):
        super().__init__(daemon=True)
        self.after = after
        self._end = threading.Event()
        self._resumed = threading.Event()
        self._resumed.set()

    def run(self):
        try:
            return  # 「not connected → Aborting playback」
        finally:
            self.after(None)

    def stop(self):
        self._end.set()
        self._resumed.set()

    def is_playing(self):
        return self._resumed.is_set() and not self._end.is_set()

    def is_paused(self):
        return not self._end.is_set() and not self._resumed.is_set()


class TestZombiePlayer(unittest.TestCase):
    def test_after_play_marks_aborted_player_finished(self):
        cog = make_cog()
        cog._play_next = MagicMock(return_value=None)
        vc = MagicMock()
        with patch('cogs.music.asyncio.run_coroutine_threadsafe'):
            player = AbortingPlayer(after=lambda e: cog._after_play(e, 1, vc))
            vc._player = player
            player.start()
            player.join(2)
        self.assertFalse(player.is_alive())
        self.assertFalse(player.is_playing())
        self.assertFalse(player.is_paused())

    def test_after_play_leaves_other_player_alone(self):
        """after 不是在 vc 目前那條播放執行緒裡呼叫(例如已換新曲) → 不能 stop 掉新的。"""
        cog = make_cog()
        cog._play_next = MagicMock(return_value=None)
        vc = MagicMock()
        newer = MagicMock()
        vc._player = newer
        with patch('cogs.music.asyncio.run_coroutine_threadsafe'):
            cog._after_play(None, 1, vc)
        newer.stop.assert_not_called()


class TestPlayNextWaitsForReconnect(unittest.IsolatedAsyncioTestCase):
    def _setup(self, connected_seq):
        cog = make_cog()
        state = GuildMusicState()
        state.queue = [make_song('Next')]
        cog._states[1] = state
        vc = make_vc(playing=False)
        vc.is_connected.side_effect = connected_seq
        cog._start_song = MagicMock()
        return cog, state, vc

    async def test_resumes_after_reconnect(self):
        cog, state, vc = self._setup([False, False, False, True] + [True] * 20)
        with patch('cogs.music.asyncio.sleep', new=AsyncMock()):
            await cog._play_next(1, vc)
        cog._start_song.assert_called_once()
        self.assertEqual(cog._start_song.call_args[0][3]['title'], 'Next')

    async def test_gives_up_when_never_reconnects(self):
        cog, state, vc = self._setup(None)
        vc.is_connected.side_effect = None
        vc.is_connected.return_value = False
        cog.PLAY_NEXT_RECONNECT_WAIT = 0.0
        await cog._play_next(1, vc)
        cog._start_song.assert_not_called()
        self.assertEqual(len(state.queue), 1)


class TestWatchdogStall(unittest.IsolatedAsyncioTestCase):
    async def _run_ticks(self, cog, ticks):
        n = {'i': 0}

        async def fake_sleep(_):
            n['i'] += 1
            if n['i'] > ticks:
                raise asyncio.CancelledError

        with patch('cogs.music.asyncio.sleep', new=fake_sleep):
            with self.assertRaises(asyncio.CancelledError):
                await cog._watchdog_loop()

    def _setup(self, *, queue, playing=False):
        cog = make_cog()
        cog._manual_discard = set()
        state = GuildMusicState()
        state.want_voice = True
        state.queue = [make_song('Q')] if queue else []
        cog._states[1] = state
        vc = make_vc(playing=playing)
        guild = MagicMock()
        guild.name = 'G'
        guild.voice_client = vc
        cog.bot.get_guild = MagicMock(return_value=guild)
        cog._play_next = AsyncMock()
        return cog, vc

    async def test_kicks_stalled_queue(self):
        cog, vc = self._setup(queue=True)
        await self._run_ticks(cog, 2)
        cog._play_next.assert_awaited_once_with(1, vc)

    async def test_single_tick_not_enough(self):
        cog, _ = self._setup(queue=True)
        await self._run_ticks(cog, 1)
        cog._play_next.assert_not_awaited()

    async def test_no_kick_while_playing(self):
        cog, _ = self._setup(queue=True, playing=True)
        await self._run_ticks(cog, 4)
        cog._play_next.assert_not_awaited()

    async def test_no_kick_when_queue_empty(self):
        cog, _ = self._setup(queue=False)
        await self._run_ticks(cog, 4)
        cog._play_next.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
