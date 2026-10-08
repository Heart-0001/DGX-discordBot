"""起播即死重試（_died_on_start/_start_died）與 /come 的測試。"""
import time
import unittest
from unittest.mock import AsyncMock, MagicMock

from tests.test_repeat import make_cog, make_song, make_vc  # 共用 discord stub
from cogs.music import GuildMusicState


def dead_state(retried: bool) -> GuildMusicState:
    st = GuildMusicState()
    st.current = make_song('Dead')
    st.current_started_mono = time.monotonic() - 0.05   # 0.05s 就結束 → 403 特徵
    st.start_retried = retried
    return st


class TestStartDeath(unittest.TestCase):
    def test_first_death_triggers_retry(self):
        cog = make_cog()
        self.assertTrue(cog._died_on_start(dead_state(retried=False)))

    def test_second_death_no_retry_but_detected(self):
        cog = make_cog()
        st = dead_state(retried=True)
        self.assertFalse(cog._died_on_start(st))
        self.assertTrue(cog._start_died(st))

    def test_normal_end_not_death(self):
        cog = make_cog()
        st = dead_state(retried=True)
        st.current_started_mono = time.monotonic() - 200
        self.assertFalse(cog._start_died(st))

    def test_skip_not_death(self):
        cog = make_cog()
        st = dead_state(retried=True)
        st.repeat_skip = True
        self.assertFalse(cog._start_died(st))


def make_come_interaction(user_channel, vc):
    it = MagicMock()
    it.guild_id = 1
    it.guild.voice_client = vc
    it.user.voice.channel = user_channel
    it.response.send_message = AsyncMock()
    it.response.defer = AsyncMock()
    it.followup.send = AsyncMock()
    return it


class TestCome(unittest.IsolatedAsyncioTestCase):
    async def test_moves_to_user_channel(self):
        cog = make_cog()
        cog._manual_discard = {1}
        target = MagicMock(id=42)
        target.name = 'B'
        vc = make_vc()
        vc.channel = MagicMock(id=7)
        vc.move_to = AsyncMock()
        it = make_come_interaction(target, vc)
        await cog.come(it)
        vc.move_to.assert_awaited_once_with(target)
        self.assertEqual(cog.get_state(1).voice_channel_id, 42)
        self.assertNotIn(1, cog._manual_discard)

    async def test_connects_when_not_in_voice(self):
        cog = make_cog()
        cog._manual_discard = set()
        target = MagicMock(id=42)
        target.connect = AsyncMock()
        it = make_come_interaction(target, None)
        await cog.come(it)
        target.connect.assert_awaited_once()

    async def test_already_there(self):
        cog = make_cog()
        cog._manual_discard = set()
        target = MagicMock(id=42)
        vc = make_vc()
        vc.channel = target
        vc.move_to = AsyncMock()
        it = make_come_interaction(target, vc)
        await cog.come(it)
        vc.move_to.assert_not_awaited()
        it.response.send_message.assert_awaited_once()

    async def test_user_not_in_voice(self):
        cog = make_cog()
        it = make_come_interaction(None, None)
        it.user.voice = None
        await cog.come(it)
        it.response.send_message.assert_awaited_once()
        it.response.defer.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
