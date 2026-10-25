"""/valo-track：同一場好幾個被追蹤的人只發一則，同場被追蹤的都標 ▶。"""
import asyncio
import sys
import types


def _import_with_real_discord():
    """跟 test_fun_web 一樣：別的測試可能在 sys.modules 塞了假 discord，暫時換回真的載入 cogs.valo。"""
    keys = [k for k in sys.modules if k == 'discord' or k.startswith('discord.')]
    fake = {k: sys.modules[k] for k in keys} if keys and not getattr(sys.modules['discord'], '__file__', None) else {}
    for k in fake:
        del sys.modules[k]
    try:
        import cogs.valo
    finally:
        if fake:
            for k in [k for k in sys.modules if k == 'discord' or k.startswith('discord.')]:
                del sys.modules[k]
            sys.modules.update(fake)
    return cogs.valo


valo = _import_with_real_discord()
A, B, C, X = 'pu-a', 'pu-b', 'pu-c', 'pu-x'
MID = 'match-1'


class Ch:
    def __init__(self, cid, gid):
        self.id, self.name, self.guild, self.sent = cid, f'ch{cid}', types.SimpleNamespace(id=gid), []

    async def send(self, embed=None, view=None):
        self.sent.append(embed)


def _player(pu, name, me):
    return {'puuid': pu, 'me': me, 'hidden': False, 'agent': '捷風', 'name': name, 'tag': 'tw', 'mvp': False,
            'k': 10, 'd': 5, 'a': 3, 'score': 3000, 'dmg': 2000, 'rounds': 13, 'hs': 20}


def _env(tmp_path, monkeypatch):
    monkeypatch.setattr(valo, 'TRACK_FILE', str(tmp_path / 't.json'))
    monkeypatch.setattr(valo, 'POSTED_FILE', str(tmp_path / 'p.json'))
    ch1, ch2 = Ch(1, 10), Ch(2, 20)
    chans = {1: ch1, 2: ch2}
    bot = types.SimpleNamespace(get_channel=chans.get)
    cog = valo.ValoCog(bot)
    cog.content = {'agents': {}, 'tiers': {}, 'maps': {}, 'mapIcons': {}}
    cog.tracked = {A: {'name': 'Alice', 'tag': 'tw', 'region': 'ap', 'channels': {'10': 1, '20': 2}},
                   B: {'name': 'Bob', 'tag': 'tw', 'region': 'ap', 'channels': {'10': 1}},
                   C: {'name': 'Cat', 'tag': 'tw', 'region': 'ap', 'channels': {'20': 2}}}

    async def eog(d, pu):
        return {'teams': [{'mine': True, 'won': True, 'rounds': 13,
                           'players': [_player(p, n, p == pu) for p, n in ((A, 'Alice'), (B, 'Bob'), (X, '路人'))]},
                          {'mine': False, 'won': False, 'rounds': 5, 'players': [_player(C, 'Cat', C == pu)]}],
                'queue': '競技', 'map': 'Ascent', 'length': 1800, 'rounds': []}
    cog._riot_eog = eog
    monkeypatch.setattr(valo, 'riot_summarize', lambda e, pu: {'draw': False, 'won': True, 'agentId': 'x', 'queue': '競技'})
    cog._select = lambda *a: None
    return cog, ch1, ch2


def test_same_match_posts_once_with_everyone_marked(tmp_path, monkeypatch):
    cog, ch1, ch2 = _env(tmp_path, monkeypatch)
    riot = {'matchInfo': {'matchId': MID}}
    assert asyncio.run(cog._post_match(cog.tracked[A], A, riot=riot))
    (e1,), (e2,) = ch1.sent, ch2.sent
    # 伺服器 10 追蹤 Alice、Bob；伺服器 20 追蹤 Alice、Cat(在敵隊)
    assert e1.title.startswith('🔫 Alice、Bob 剛打完一場') and e2.title.startswith('🔫 Alice、Cat 剛打完一場')
    body1 = ''.join(f.value for f in e1.fields)
    assert '▶ **捷風** Alice' in body1 and '▶ **捷風** Bob' in body1 and '▶ **捷風** 路人' not in body1
    assert '▶ **捷風** Cat' not in body1                               # Cat 沒在這個伺服器被追蹤
    assert '▶ **捷風** Cat' in ''.join(f.value for f in e2.fields)
    # Bob、Cat 輪到時不再發
    assert asyncio.run(cog._post_match(cog.tracked[B], B, riot=riot))
    assert asyncio.run(cog._post_match(cog.tracked[C], C, riot=riot))
    assert len(ch1.sent) == 1 and len(ch2.sent) == 1
    # 重啟後還記得
    assert valo.ValoCog._load_posted() == {MID: [1, 2]}


def test_other_match_still_posts(tmp_path, monkeypatch):
    cog, ch1, ch2 = _env(tmp_path, monkeypatch)
    asyncio.run(cog._post_match(cog.tracked[A], A, riot={'matchInfo': {'matchId': MID}}))
    asyncio.run(cog._post_match(cog.tracked[B], B, riot={'matchInfo': {'matchId': 'match-2'}}))
    assert len(ch1.sent) == 2
