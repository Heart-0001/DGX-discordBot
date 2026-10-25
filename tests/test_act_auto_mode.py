"""/act 自動模式：只有擁有者本人在這個頻道的 Activity 裡，才切到 LoL / Valo。"""
import asyncio
from types import SimpleNamespace

from cogs.act_live import ActLiveCog
from cogs.lyrics import LyricsCog

OWNER, OTHER, CID = 1, 2, 100


class _WS:
    closed = False


class _Bot:
    def __init__(self, cogs):
        self.cogs = cogs

    def get_cog(self, name):
        return self.cogs.get(name)

    async def is_owner(self, user):
        return user.id == OWNER


def _mode(watchers, phase='ingame'):
    act = ActLiveCog.__new__(ActLiveCog)
    act._socks, act._meta = {CID: set()}, {}
    for uid in watchers:
        ws = _WS()
        act._socks[CID].add(ws)
        act._meta[ws] = (9, uid)
    bot = _Bot({'ActLiveCog': act, 'LolCog': SimpleNamespace(state={'phase': phase})})
    lyr = LyricsCog.__new__(LyricsCog)
    lyr.bot = bot
    return asyncio.run(lyr._auto_mode(CID))


def test_owner_in_activity_switches():
    assert _mode([OWNER, OTHER]) == 'lol'


def test_others_only_stays_lyrics():
    assert _mode([OTHER]) == 'lyrics'
    assert _mode([None]) == 'lyrics'      # 還沒登入
    assert _mode([]) == 'lyrics'


def test_owner_but_no_game():
    assert _mode([OWNER], phase='lobby') == 'lyrics'
