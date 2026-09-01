"""
Tests for /repeat command and related _play_next logic in MusicCog.

Scenarios covered:
  1. /repeat cycles off → one → all → off
  2. repeat one: song ends → same song replays
  3. repeat all: song ends → song is appended to queue tail
  4. /repeat when not playing gives an ephemeral error prompt
  5. skip + repeat all: skipped song is still re-queued (regression for repeat_skip bug)
"""

import asyncio
import sys
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch, call

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# Stub discord module so we can import MusicCog without a bot token / gateway
import types

discord_stub = types.ModuleType('discord')
discord_stub.PCMVolumeTransformer = MagicMock
discord_stub.FFmpegPCMAudio = MagicMock
discord_stub.Color = MagicMock()
discord_stub.Color.blue = MagicMock(return_value='blue')
discord_stub.Color.green = MagicMock(return_value='green')
discord_stub.Color.purple = MagicMock(return_value='purple')
discord_stub.Color.orange = MagicMock(return_value='orange')
discord_stub.Color.blurple = MagicMock(return_value='blurple')
discord_stub.Embed = MagicMock
discord_stub.VoiceClient = MagicMock
discord_stub.Interaction = MagicMock

ext_stub = types.ModuleType('discord.ext')
commands_stub = types.ModuleType('discord.ext.commands')
commands_stub.Cog = object
commands_stub.Bot = MagicMock

app_commands_stub = types.ModuleType('discord.app_commands')


def _noop_decorator(*args, **kwargs):
    def decorator(fn):
        return fn
    if len(args) == 1 and callable(args[0]):
        return args[0]
    return decorator


app_commands_stub.command = _noop_decorator
app_commands_stub.describe = _noop_decorator

discord_stub.app_commands = app_commands_stub
ext_stub.commands = commands_stub

sys.modules.setdefault('discord', discord_stub)
sys.modules.setdefault('discord.ext', ext_stub)
sys.modules.setdefault('discord.ext.commands', commands_stub)
sys.modules.setdefault('discord.app_commands', app_commands_stub)
sys.modules.setdefault('ytmusicapi', MagicMock())

from cogs.music import MusicCog, GuildMusicState  # noqa: E402


# ── helpers ──────────────────────────────────────────────────────────────────

def make_song(title: str = 'Test Song') -> dict:
    return {
        'url': 'http://cdn.test/audio.opus',
        'webpage_url': 'https://www.youtube.com/watch?v=dQw4w9WgXcQ',
        'title': title,
        'duration': 213,
        'thumbnail': 'http://img.test/thumb.jpg',
        'uploader': 'TestChannel',
    }


def make_bot():
    bot = MagicMock()
    bot.loop = asyncio.new_event_loop()
    return bot


def make_cog(bot=None) -> MusicCog:
    bot = bot or make_bot()
    cog = MusicCog.__new__(MusicCog)
    cog.bot = bot
    cog._states = {}
    cog._ytm = MagicMock()
    return cog


def make_vc(*, playing: bool = True, paused: bool = False, connected: bool = True):
    vc = MagicMock()
    vc.is_connected.return_value = connected
    vc.is_playing.return_value = playing
    vc.is_paused.return_value = paused
    return vc


def make_interaction(*, vc=None, guild_id: int = 1):
    interaction = MagicMock()
    interaction.guild_id = guild_id
    interaction.guild.voice_client = vc
    interaction.response.send_message = AsyncMock()
    return interaction


# ── Test 1: toggle cycle ─────────────────────────────────────────────────────

class TestRepeatToggle(unittest.IsolatedAsyncioTestCase):
    async def test_cycle_off_one_all_off(self):
        """Each /repeat call advances the mode through off → one → all → off."""
        cog = make_cog()
        vc = make_vc()

        for expected in ('one', 'all', 'off'):
            interaction = make_interaction(vc=vc)
            await cog.repeat(interaction)
            state = cog.get_state(interaction.guild_id)
            self.assertEqual(state.repeat, expected,
                             msg=f'Expected repeat={expected!r} after toggle')

    async def test_labels_sent_correctly(self):
        """Confirmation message must contain the new mode's label."""
        cog = make_cog()
        vc = make_vc()
        label_map = {'one': '🔂 單曲循環', 'all': '🔁 全部循環', 'off': '❌ 關閉'}

        for expected_label in ('🔂 單曲循環', '🔁 全部循環', '❌ 關閉'):
            interaction = make_interaction(vc=vc)
            await cog.repeat(interaction)
            sent = interaction.response.send_message.call_args[0][0]
            # The sent message must match the mode we just switched into
            new_mode = cog.get_state(interaction.guild_id).repeat
            self.assertIn(label_map[new_mode], sent)


# ── Test 2: repeat one replays same song ─────────────────────────────────────

class TestRepeatOne(unittest.IsolatedAsyncioTestCase):
    async def test_same_song_starts_again(self):
        """When repeat='one' and a song ends, _play_next must replay it."""
        cog = make_cog()
        state = cog.get_state(1)
        song = make_song('Looping Song')
        state.current = song
        state.repeat = 'one'
        state.queue = []

        vc = make_vc()
        started: list[str] = []

        async def fake_ensure(s):
            s['url'] = 'http://refreshed.url/audio.opus'

        def fake_start(guild_id, voice_client, st, s, **kwargs):
            started.append(s['title'])

        with patch.object(cog, '_ensure_stream_url', side_effect=fake_ensure), \
             patch.object(cog, '_start_song', side_effect=fake_start):
            await cog._play_next(1, vc)

        self.assertEqual(started, ['Looping Song'],
                         'Expected the same song to be started again')
        # Queue must still be empty — repeat one does not consume it
        self.assertEqual(len(state.queue), 0)

    async def test_url_is_refreshed(self):
        """repeat one must clear the URL so a fresh stream URL is fetched."""
        cog = make_cog()
        state = cog.get_state(1)
        song = make_song()
        song['url'] = 'http://old.expired/audio.opus'
        state.current = song
        state.repeat = 'one'

        fetched_urls: list[str] = []

        async def fake_ensure(s):
            fetched_urls.append(s.get('url'))  # should be empty string
            s['url'] = 'http://new.url/audio.opus'

        with patch.object(cog, '_ensure_stream_url', side_effect=fake_ensure), \
             patch.object(cog, '_start_song'):
            await cog._play_next(1, make_vc())

        self.assertEqual(fetched_urls, [''],
                         'URL must be cleared before fetching so a fresh stream is obtained')

    async def test_fallback_on_fetch_error(self):
        """If the URL refresh fails, repeat one must fall through to the queue."""
        cog = make_cog()
        state = cog.get_state(1)
        state.current = make_song('Current')
        state.repeat = 'one'
        next_song = make_song('Next')
        next_song['url'] = 'http://ok.url/next.opus'
        state.queue = [next_song]

        started: list[str] = []

        async def fail_ensure(s):
            raise Exception('network error')

        def fake_start(guild_id, vc, st, s, **kwargs):
            started.append(s['title'])

        with patch.object(cog, '_ensure_stream_url', side_effect=fail_ensure), \
             patch.object(cog, '_start_song', side_effect=fake_start):
            await cog._play_next(1, make_vc())

        self.assertEqual(started, ['Next'],
                         'Should fall through to queue when repeat one fetch fails')


# ── Test 3: repeat all re-queues song at the tail ────────────────────────────

class TestRepeatAll(unittest.IsolatedAsyncioTestCase):
    async def test_finished_song_appended_to_tail(self):
        """When repeat='all', the finished song must be appended to the queue tail."""
        cog = make_cog()
        state = cog.get_state(1)
        song_a = make_song('Song A')
        song_b = make_song('Song B')
        state.current = song_a
        state.repeat = 'all'
        state.queue = [song_b]

        started: list[str] = []

        async def fake_ensure(s):
            s['url'] = 'http://cdn.test/audio.opus'

        def fake_start(guild_id, vc, st, s, **kwargs):
            started.append(s['title'])

        with patch.object(cog, '_ensure_stream_url', side_effect=fake_ensure), \
             patch.object(cog, '_start_song', side_effect=fake_start):
            await cog._play_next(1, make_vc())

        # Next played song must be Song B (front of queue)
        self.assertEqual(started, ['Song B'])
        # Song A must now be at the end of the queue
        self.assertEqual(len(state.queue), 1)
        self.assertEqual(state.queue[0]['title'], 'Song A')

    async def test_url_cleared_in_requeued_song(self):
        """Requeued song must have URL cleared so a fresh stream URL is fetched later."""
        cog = make_cog()
        state = cog.get_state(1)
        song = make_song()
        song['url'] = 'http://old.url/audio.opus'
        state.current = song
        state.repeat = 'all'
        state.queue = [make_song('Next')]

        async def fake_ensure(s):
            s['url'] = 'http://cdn.test/audio.opus'

        with patch.object(cog, '_ensure_stream_url', side_effect=fake_ensure), \
             patch.object(cog, '_start_song'):
            await cog._play_next(1, make_vc())

        requeued = state.queue[0]
        self.assertEqual(requeued['url'], '',
                         'Requeued song URL must be cleared to force a fresh fetch')

    async def test_single_song_loops(self):
        """With one song and repeat all, that song must loop indefinitely."""
        cog = make_cog()
        state = cog.get_state(1)
        song = make_song('Solo')
        state.current = song
        state.repeat = 'all'
        state.queue = []

        started: list[str] = []

        async def fake_ensure(s):
            s['url'] = 'http://cdn.test/audio.opus'

        def fake_start(guild_id, vc, st, s, **kwargs):
            started.append(s['title'])

        with patch.object(cog, '_ensure_stream_url', side_effect=fake_ensure), \
             patch.object(cog, '_start_song', side_effect=fake_start):
            await cog._play_next(1, make_vc())

        self.assertEqual(started, ['Solo'])
        # Queue must be empty again (song was popped to play)
        self.assertEqual(len(state.queue), 0)

    async def test_skip_with_repeat_all_still_requeues(self):
        """
        Regression: repeat_skip=True (from explicit skip) must NOT prevent
        repeat all from re-queuing the skipped song.
        """
        cog = make_cog()
        state = cog.get_state(1)
        song_a = make_song('Song A')
        song_b = make_song('Song B')
        state.current = song_a
        state.repeat = 'all'
        state.repeat_skip = True   # user pressed /skip
        state.queue = [song_b]

        started: list[str] = []

        async def fake_ensure(s):
            s['url'] = 'http://cdn.test/audio.opus'

        def fake_start(guild_id, vc, st, s, **kwargs):
            started.append(s['title'])

        with patch.object(cog, '_ensure_stream_url', side_effect=fake_ensure), \
             patch.object(cog, '_start_song', side_effect=fake_start):
            await cog._play_next(1, make_vc())

        self.assertEqual(started, ['Song B'])
        # Song A must be re-queued even though skip was pressed
        self.assertEqual(state.queue[0]['title'], 'Song A',
                         'Skipped song must be re-queued when repeat=all')
        # repeat_skip must be cleared
        self.assertFalse(state.repeat_skip)


# ── Test 4: /repeat when not playing gives a prompt ──────────────────────────

class TestRepeatNotPlaying(unittest.IsolatedAsyncioTestCase):
    async def test_no_voice_client_shows_error(self):
        """No voice client → ephemeral error message, state unchanged."""
        cog = make_cog()
        interaction = make_interaction(vc=None)

        await cog.repeat(interaction)

        interaction.response.send_message.assert_called_once()
        args, kwargs = interaction.response.send_message.call_args
        self.assertTrue(kwargs.get('ephemeral', False),
                        'Error must be ephemeral')
        self.assertIn('❌', args[0])

    async def test_vc_not_playing_shows_error(self):
        """Voice client present but idle → ephemeral error, state unchanged."""
        cog = make_cog()
        vc = make_vc(playing=False, paused=False)
        interaction = make_interaction(vc=vc)
        original_repeat = cog.get_state(interaction.guild_id).repeat

        await cog.repeat(interaction)

        interaction.response.send_message.assert_called_once()
        _, kwargs = interaction.response.send_message.call_args
        self.assertTrue(kwargs.get('ephemeral', False))
        # State must not change
        self.assertEqual(cog.get_state(interaction.guild_id).repeat, original_repeat)

    async def test_paused_is_allowed(self):
        """A paused voice client counts as 'active'; toggle must succeed."""
        cog = make_cog()
        vc = make_vc(playing=False, paused=True)
        interaction = make_interaction(vc=vc)

        await cog.repeat(interaction)

        # No error
        args, _ = interaction.response.send_message.call_args
        self.assertNotIn('❌', args[0])
        self.assertEqual(cog.get_state(interaction.guild_id).repeat, 'one')


if __name__ == '__main__':
    unittest.main()
