"""活動面板後端(cogs/fun_web.py)的整合測試：假 bot + 假 WebSocket，從協定這層跑過每個操作。"""
import asyncio
import sys
import types

import pytest


def _import_with_real_discord():
    """test_act_music / test_repeat 會在 sys.modules 塞假的 discord(setdefault)；整套一起跑時它們先被收集。
    這裡暫時換回真的 discord.py 載入要測的 cog，載完把假的放回去，兩邊互不影響。"""
    keys = [k for k in sys.modules if k == 'discord' or k.startswith('discord.')]
    fake = {k: sys.modules[k] for k in keys} if keys and not getattr(sys.modules['discord'], '__file__', None) else {}
    for k in fake:
        del sys.modules[k]
    try:
        import cogs.fishing, cogs.fun, cogs.fun_web, cogs.gacha, cogs.lottery, cogs.minigames, cogs.mono  # noqa: E401
    finally:
        if fake:
            for k in [k for k in sys.modules if k == 'discord' or k.startswith('discord.')]:
                del sys.modules[k]
            sys.modules.update(fake)
    return cogs


cogs = _import_with_real_discord()
FunWebCog = cogs.fun_web.FunWebCog

UID, GID = 111, 999
SESS = {'id': UID, 'name': '測試玩家'}


class FakeWS:
    closed = False

    def __init__(self):
        self.sent = []

    async def send_json(self, m):
        self.sent.append(m)


class FakeAct:
    def __init__(self):
        self.user_msgs, self.guild_msgs = [], []

    async def send_user(self, uid, m):
        self.user_msgs.append((uid, m))

    async def send_guild(self, gid, m):
        self.guild_msgs.append((gid, m))


class FakeBot:
    def __init__(self):
        self.cogs = {}

    def get_cog(self, name):
        return self.cogs.get(name)

    def get_user(self, uid):
        return None

    def get_channel(self, cid):
        return None


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(cogs.fun, 'WALLET_FILE', str(tmp_path / 'w.json'))
    monkeypatch.setattr(cogs.gacha, 'GACHA_FILE', str(tmp_path / 'g.json'))
    monkeypatch.setattr(cogs.fishing, 'FILE', str(tmp_path / 'f.json'))
    monkeypatch.setattr(cogs.lottery, 'FILE', str(tmp_path / 'l.json'))
    monkeypatch.setattr(cogs.minigames, 'FILE', str(tmp_path / 'm.json'))
    monkeypatch.setattr(cogs.mono, 'DATA_FILE', str(tmp_path / 'mono.json'))
    bot = FakeBot()
    bot.cogs = {
        'FunCog': cogs.fun.FunCog(bot), 'GachaCog': cogs.gacha.GachaCog(bot), 'FishingCog': cogs.fishing.FishingCog(bot),
        'LotteryCog': cogs.lottery.LotteryCog(bot), 'MiniGamesCog': cogs.minigames.MiniGamesCog(bot),
        'MonoCog': cogs.mono.MonoCog(bot), 'ActLiveCog': FakeAct(),
        # 轉蛋卡池的來源
        'LolCog': types.SimpleNamespace(static=types.SimpleNamespace(data={'champNames': {'103': '阿璃', '1': '安妮'}})),
        'ValoCog': types.SimpleNamespace(content={'agents': {'add6443a-41bd-e414-f6ad-e58d267f4e95': {'name': '捷風', 'icon': 'x'}}}),
    }
    web = FunWebCog(bot)
    bot.cogs['FunWebCog'] = web
    return bot, web


def call(web, op, sess=SESS, **kw):
    ws = FakeWS()
    asyncio.run(web.op(ws, GID, sess, {'type': 'fun', 'id': 7, 'op': op, **kw}))
    (msg,) = ws.sent
    assert msg['type'] == 'fun' and msg['id'] == 7
    return msg


def ok(web, op, **kw):
    msg = call(web, op, **kw)
    assert msg['ok'], msg.get('error')
    return msg['data']


def give(bot, tickets=0, coins=0):
    fun = bot.cogs['FunCog']
    fun.add_tickets(UID, tickets)
    fun.add_coins(UID, coins)


def test_requires_login(env):
    _, web = env
    m = call(web, 'home', sess=None)
    assert not m['ok'] and '登入' in m['error']


def test_unknown_op(env):
    _, web = env
    assert not call(web, 'nope')['ok']


def test_home_and_daily(env):
    bot, web = env
    w = ok(web, 'home')['wallet']
    assert w['tickets'] == 0 and w['daily'] is False and w['cap'] == 12
    d = ok(web, 'daily')
    assert d['got'] == 1 and d['wallet']['tickets'] == 1 and d['wallet']['daily'] is True
    assert '簽到過' in call(web, 'daily')['error']
    # 用過活動 → 記住伺服器和名字
    assert GID in bot.cogs['FunCog'].user(UID)['guilds'] and bot.cogs['FunCog'].name_of(UID) == '測試玩家'


def test_gacha_needs_tickets_then_pulls(env):
    bot, web = env
    assert '券不夠' in call(web, 'gacha_pull', count=3)['error']
    give(bot, tickets=5)
    d = ok(web, 'gacha_pull', count=3)
    assert len(d['results']) == 3 and d['wallet']['tickets'] == 2
    assert all(r['icon'].startswith('api/fun/icon/') for r in d['results'])
    dex = ok(web, 'gacha_dex')
    assert dex['total'] == 3 and dex['owned'] >= 1


def test_fish_cast_and_sell(env):
    bot, web = env
    give(bot, tickets=4)
    d = ok(web, 'fish_cast', count=4)
    assert len(d['catches']) == 4 and d['wallet']['tickets'] == 0
    bag = ok(web, 'fish_bag')
    assert bag['total'] == d['total']
    s = ok(web, 'fish_sell')
    assert s['total'] == d['total'] and s['wallet']['coins'] == d['total']
    assert '空' in call(web, 'fish_sell')['error']
    assert ok(web, 'fish_dex')['caught'] >= 1
    assert ok(web, 'fish_records')['rows']


def test_blackjack_flow_charges_once(env):
    bot, web = env
    give(bot, tickets=1)
    g = ok(web, 'bj_start')['game']
    if not g['over']:
        g2 = ok(web, 'bj_start')['game']           # 重複開局 → 接著同一局，不再扣券
        assert g2['player'] == g['player']
        assert g['dealer'][1] is None and g['dv'] is None    # 莊家暗牌
        g = ok(web, 'bj_stand')['game']
    assert g['over'] and g['result']
    assert bot.cogs['FunCog'].tickets(UID) == 0
    assert bot.cogs['FunCog'].coins(UID) == g['payout']
    assert '結束' in call(web, 'bj_hit')['error']


def test_mines_reveal_and_cash(env):
    bot, web = env
    give(bot, tickets=1)
    ok(web, 'mines_start')
    mines = bot.cogs['MiniGamesCog'].games[('mines', UID)]['mines']
    safe = next(i for i in range(20) if i not in mines)
    g = ok(web, 'mines_reveal', i=safe)['game']
    assert g['k'] == 1 and not g['over'] and g['mine_cells'] == []
    g = ok(web, 'mines_cash')['game']
    assert g['over'] and not g['exploded'] and g['payout'] == g['cash'] > 100
    assert bot.cogs['FunCog'].coins(UID) == g['payout']


def test_mines_explode_shows_mines(env):
    bot, web = env
    give(bot, tickets=1)
    ok(web, 'mines_start')
    mine = next(iter(bot.cogs['MiniGamesCog'].games[('mines', UID)]['mines']))
    g = ok(web, 'mines_reveal', i=mine)['game']
    assert g['over'] and g['exploded'] and g['payout'] == 0 and len(g['mine_cells']) == 3


def test_slots_and_flip(env):
    bot, web = env
    give(bot, tickets=2)
    s = ok(web, 'slots_spin')
    assert len(s['reels']) == 3 and s['table']
    f = ok(web, 'flip', guess='heads')
    assert f['flip'] in ('heads', 'tails') and f['payout'] in (0, 196)
    assert f['wallet']['tickets'] == 0
    assert not call(web, 'flip', guess='edge')['ok']


def test_lottery_buy(env):
    bot, web = env
    give(bot, tickets=3)
    d = ok(web, 'lottery_buy', count=3)
    assert d['pot'] == 300 and d['mine'] == 3 and d['chance'] == 1.0 and d['wallet']['tickets'] == 0
    assert ok(web, 'lottery')['bets'] == 3


def test_mono_join_roll_decide(env):
    bot, web = env
    d = ok(web, 'mono')
    assert d['joined'] is False and len(d['board']) == 32
    d = ok(web, 'mono_join')
    assert d['joined'] and d['me']['cash'] == 1500
    assert '券不夠' in call(web, 'mono_roll')['error']
    give(bot, tickets=1)
    d = ok(web, 'mono_roll')
    assert len(d['roll']['dice']) == 2 and all('**' not in e for e in d['roll']['events'])
    if d['pending']:
        d = ok(web, 'mono_decide', action='skip')
        assert d['pending'] is None
    else:
        assert not call(web, 'mono_decide', action='buy')['ok']


def test_shop_buy_and_equip(env):
    bot, web = env
    assert '代幣不夠' in call(web, 'shop_buy', item='frame_gold')['error']
    give(bot, coins=2500)
    d = ok(web, 'shop_buy', item='frame_gold')
    item = next(i for i in d['items'] if i['id'] == 'frame_gold')
    assert item['owned'] and item['equipped'] and d['wallet']['coins'] == 500 and d['wallet']['frame'] == 'frame_gold'
    assert '已經有' in call(web, 'shop_buy', item='frame_gold')['error']
    d = ok(web, 'equip', item=None, kind='frame')
    assert d['wallet']['frame'] is None
    assert not call(web, 'equip', item='bg_lava')['ok']        # 沒買過


def test_profile_and_top(env):
    bot, web = env
    give(bot, coins=123)
    p = ok(web, 'profile')
    assert p['name'] == '測試玩家' and p['avatar'] == f'api/fun/avatar/{UID}.png'
    assert set(p['stats']) >= {'tickets_total', 'coins', 'gacha', 'fish', 'lottery_wins', 'mono_titles', 'mini_best'}
    rows = ok(web, 'top', kind='coins')['rows']
    assert rows[0]['uid'] == str(UID) and rows[0]['value'] == 123
    for k in ('gacha', 'fish', 'mono'):
        assert 'rows' in ok(web, 'top', kind=k)


def test_ssr_pull_celebrates(env, monkeypatch):
    bot, web = env
    monkeypatch.setattr(cogs.gacha, 'pull', lambda rng, pool, n: [(pool[0], 'SSR')] * n)
    give(bot, tickets=1)
    ok(web, 'gacha_pull', count=1)
    (gid, msg), = bot.cogs['ActLiveCog'].guild_msgs
    assert gid == GID and msg['type'] == 'celebrate' and msg['kind'] == 'ssr' and msg['big']


def test_grant_push_and_max_ticket_celebration(env):
    bot, web = env
    bot.cogs['FunCog'].seen(UID, GID)
    grant = {'game': 'LoL', 'queue': '單/雙積分', 'won': True, 'who': '阿璃', 'kda': '12/1/8', 'score': 8,
             'parts': [['勝利', 2]], 'got': 4, 'tickets': 4, 'earned': 4, 'cap': 12}
    asyncio.run(web.on_grant(UID, grant))
    act = bot.cogs['ActLiveCog']
    assert [m['what'] for _, m in act.user_msgs] == ['grant', 'wallet']
    assert act.guild_msgs[0][1]['kind'] == 'max_ticket'


# ── PC 客戶端對戰紀錄(大混戰) ──
class FakePc:
    def __init__(self, games, details):
        self.games, self.details, self.calls = games, details, []

    async def lcu(self, path, timeout=8):
        self.calls.append(path)
        if 'current-summoner/matches' in path:
            return {'games': {'games': self.games}} if self.games is not None else None
        gid = int(path.rsplit('/', 1)[-1])
        return self.details.get(gid)


def _game(gid, created_ms, players):
    parts, ids = [], []
    for i, (name, team, k, d, a, dmg, win) in enumerate(players, start=1):
        parts.append({'participantId': i, 'teamId': team, 'championId': 103,
                      'stats': {'kills': k, 'deaths': d, 'assists': a, 'win': win, 'totalDamageDealtToChampions': dmg}})
        ids.append({'participantId': i, 'player': {'gameName': name, 'tagLine': 'TW2'}})
    return {'gameId': gid, 'queueId': 2400, 'gameMode': 'KIWI', 'gameCreation': created_ms, 'gameDuration': 1200,
            'participants': parts, 'participantIdentities': ids}


PLAYERS = [('Heart', 100, 15, 3, 20, 45000, True), ('朋友', 100, 2, 8, 6, 9000, True),
           ('路人', 100, 3, 6, 10, 15000, True), ('對手', 200, 8, 9, 5, 30000, False)]


def _link(bot, uid, name, since):
    u = bot.cogs['FunCog'].user(uid)
    u['lol'].append({'name': name, 'tag': 'TW2', 'puuid': f'api-{uid}', 'last': 'x', 'since': since, 'seen': []})
    u['guilds'] = [GID]


def test_lcu_grants_mayhem_to_everyone_linked_in_the_game(env):
    bot, web = env
    fun = bot.cogs['FunCog']
    _link(bot, UID, 'Heart', since=10_000)
    _link(bot, 222, '朋友', since=10_000)
    old = _game(1, 5_000_000, PLAYERS)          # 5000 秒開始 + 1200 秒 → 綁定(10000)前就結束 → 不發
    new = _game(2, 20_000_000, PLAYERS)
    pc = FakePc([new, old], {1: old, 2: new})
    bot.cogs['LolCog'].pc = pc
    asyncio.run(fun._check_lcu())
    assert fun.tickets(UID) == 4 and fun.tickets(222) == 1        # Heart 滿分 4 張、朋友保底 1 張
    assert all('/games/1' not in c for c in pc.calls)              # 綁定前的那場連細節都不用抓
    assert fun.data['lcu_seen'] == ['1', '2']
    # 再跑一次不重複發；API 那條路之後查到同一場(TW2_2)也不會再發
    asyncio.run(fun._check_lcu())
    assert fun.tickets(UID) == 4 and '2' in fun.user(UID)['lol'][0]['seen']
    grants = [m for u, m in bot.cogs['ActLiveCog'].user_msgs if u == UID and m['what'] == 'grant']
    assert len(grants) == 1 and grants[0]['data']['queue'] == '隨機單中：大混戰'


def test_lcu_offline_or_detail_missing_retries_later(env):
    bot, web = env
    fun = bot.cogs['FunCog']
    _link(bot, UID, 'Heart', since=10_000)
    bot.cogs['LolCog'].pc = FakePc(None, {})                       # 客戶端沒開
    asyncio.run(fun._check_lcu())
    g = _game(3, 20_000_000, PLAYERS)
    bot.cogs['LolCog'].pc = FakePc([g], {})                        # 列表有、細節還抓不到
    asyncio.run(fun._check_lcu())
    assert fun.tickets(UID) == 0 and fun.data['lcu_seen'] == []
    bot.cogs['LolCog'].pc = FakePc([g], {3: g})
    asyncio.run(fun._check_lcu())
    assert fun.tickets(UID) == 4


class SlowPc(FakePc):
    """對戰紀錄比結算畫面晚幾次才出現這場。"""
    def __init__(self, game, late):
        super().__init__([], {game['gameId']: game})
        self.game, self.late = game, late

    async def lcu(self, path, timeout=8):
        if 'current-summoner/matches' in path:
            self.late -= 1
            self.games = [self.game] if self.late < 0 else []
        return await super().lcu(path, timeout)


def test_lcu_game_end_kick_checks_right_away_until_found(env, monkeypatch):
    bot, web = env
    fun = bot.cogs['FunCog']
    _link(bot, UID, 'Heart', since=10_000)
    monkeypatch.setattr(cogs.fun, 'LCU_EVERY', 3600)        # 沒有通知就要等一小時 → 有發券一定是通知觸發的
    monkeypatch.setattr(cogs.fun, 'LCU_BURST_GAP', 0.01)
    pc = SlowPc(_game(5, 20_000_000, PLAYERS), late=3)
    bot.cogs['LolCog'].pc = pc

    async def ready():
        pass
    bot.wait_until_ready = ready

    async def run():
        fun._lcu_kick = asyncio.Event()
        task = asyncio.ensure_future(fun._lcu_loop())
        await asyncio.sleep(0.05)
        assert fun.tickets(UID) == 0                          # 還沒通知
        fun.lcu_game_ended()
        for _ in range(100):
            await asyncio.sleep(0.01)
            if fun.tickets(UID):
                break
        n = sum('current-summoner/matches' in c for c in pc.calls)
        await asyncio.sleep(0.1)
        task.cancel()
        return n, sum('current-summoner/matches' in c for c in pc.calls)

    n, after = asyncio.run(run())
    assert fun.tickets(UID) == 4 and n == 4                   # 前 3 次列表沒有，第 4 次看到就發
    assert after == n                                         # 找到之後就停，回到每分鐘(這裡是一小時)


def test_lcu_arena_scored_by_placement(env):
    bot, web = env
    fun = bot.cogs['FunCog']
    _link(bot, UID, 'Heart', since=10_000)
    g = _game(6, 20_000_000, PLAYERS)
    g.update(queueId=1700, gameMode='CHERRY')
    for p, place in zip(g['participants'], (1, 1, 5, 8)):
        p['stats']['subteamPlacement'] = place
    bot.cogs['LolCog'].pc = FakePc([g], {6: g})
    assert asyncio.run(fun._check_lcu()) == 1
    (grant,) = [m for u, m in bot.cogs['ActLiveCog'].user_msgs if u == UID and m['what'] == 'grant']
    assert grant['data']['queue'] == '鬥魂競技場' and grant['data']['score'] == 8 and fun.tickets(UID) == 4
