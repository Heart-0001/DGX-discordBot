"""賽季制大富翁：每個伺服器一張長駐棋盤，14 天一季；1 張券 = 擲 1 次骰(2d6，每次機率一樣)。

沒有指令：Activity 的活動面板透過 cogs/fun_web.py 呼叫這裡的服務方法。
玩家不用同時在線，誰有券誰就擲。待決定的狀態(買地 / 升級)存在 data/mono.json，5 分鐘沒決定當作不要。
賽季到期自動結算、發代幣獎勵、開新的一季(名單保留)。
"""
import asyncio
import json
import logging
import os
import random
import re
import time
from typing import Optional

import discord
from discord.ext import commands

from cogs.fun import FunError, launch_view
from games import mono_logic as ml
from games.mono_image import GROUP_COLORS, PLAYER_COLORS, SPECIAL, render_board

log = logging.getLogger(__name__)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_FILE = os.path.join(ROOT, 'data', 'mono.json')
CHECK_MIN, CHECK_MAX = 300, 600      # 秒：背景檢查賽季到期的間隔
NO_PING = discord.AllowedMentions.none()
KIND = {'start': 'go', 'jail': 'jail', 'rest': 'free', 'report': 'gotojail', 'land': 'prop', 'chance': 'chance',
        'tax': 'tax', 'portal': 'portal'}


def _hex(rgb) -> str:
    return '#%02x%02x%02x' % tuple(rgb)


def _plain(s: str) -> str:
    """事件文字給網頁用：拿掉 Markdown 粗體。"""
    return re.sub(r'\*\*(.+?)\*\*', r'\1', s)


class MonoCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.data = self._load()
        self.rng = random.Random()
        self._task: Optional[asyncio.Task] = None

    async def cog_load(self):
        self._task = asyncio.ensure_future(self._season_loop())
        n = sum(1 for g in self.data['guilds'].values() if g.get('season'))
        log.info(f'🎲 大富翁：{n} 個伺服器有進行中的賽季')

    async def cog_unload(self):
        if self._task:
            self._task.cancel()

    # ── 存檔 ──
    @staticmethod
    def _load() -> dict:
        try:
            with open(DATA_FILE, encoding='utf-8') as f:
                d = json.load(f)
        except (OSError, ValueError):
            d = {}
        d.setdefault('guilds', {})
        return d

    def save(self):
        os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
        with open(DATA_FILE + '.tmp', 'w', encoding='utf-8') as f:
            json.dump(self.data, f, ensure_ascii=False, indent=1)
        os.replace(DATA_FILE + '.tmp', DATA_FILE)

    def _g(self, gid: int) -> dict:
        g = self.data['guilds'].setdefault(str(gid), ml.new_guild())
        for k, v in ml.new_guild().items():
            g.setdefault(k, v)
        return g

    def _fun(self):
        return self.bot.get_cog('FunCog')

    def names(self, season: dict) -> dict:
        fun = self._fun()
        return {k: fun.name_of(int(k)) for k in season['players']}

    # ── 服務 ──
    def view(self, gid: int, uid: int) -> dict:
        """給網頁的完整狀態。"""
        g = self._g(gid)
        s = g.get('season')
        fun = self._fun()
        champs = [{'season': h['id'], 'name': fun.name_of(h['champion']), 'nw': h['champion_nw']}
                  for h in g['history'] if h.get('champion')][-5:][::-1]
        if not s:
            return {'joined': False, 'season': None, 'board': self._board(None, {}), 'players': [], 'me': None,
                    'pending': None, 'standings': [], 'champions': champs, 'board_png': ''}
        names = self.names(s)
        order = list(s['players'])
        colors = {k: _hex(PLAYER_COLORS[i % len(PLAYER_COLORS)]) for i, k in enumerate(order)}
        ranks = [u for u, _ in ml.standings(s)]
        players = [{'uid': k, 'name': names[k], 'initial': (names[k] or '?')[0], 'pos': p['pos'], 'cash': p['cash'],
                    'nw': ml.net_worth(s, int(k)), 'jail': p['jail'], 'color': colors[k], 'rolled': p['rolled']}
                   for k, p in s['players'].items()]
        me = s['players'].get(str(uid))
        pending = None
        if me and me.get('pending') and time.time() - me['pending']['at'] <= ml.DECISION_TIMEOUT:
            pd = me['pending']
            t = ml.TILES[pd['tile']]
            pending = {'type': pd['type'], 'tile': pd['tile'], 'name': t['name'],
                       'cost': t['price'] if pd['type'] == 'buy' else t['upgrade']}
        return {
            'joined': me is not None,
            'season': {'id': s['id'], 'name': ml.season_name(s), 'end': int(s['end']),
                       'days_left': round(max(0, s['end'] - time.time()) / 86400, 1)},
            'board': self._board(s, {k: (names[k], colors[k]) for k in order}),
            'players': players,
            'me': {'uid': str(uid), 'cash': me['cash'], 'nw': ml.net_worth(s, uid), 'pos': me['pos'], 'jail': me['jail'],
                   'rank': ranks.index(uid) + 1, 'rolled': me['rolled']} if me else None,
            'pending': pending,
            'standings': [{'uid': str(u), 'name': names[str(u)], 'nw': nw} for u, nw in ml.standings(s)],
            'champions': champs,
            'board_png': f"api/fun/mono.png?g={gid}&v={s['seq']}",
        }

    @staticmethod
    def _board(s: Optional[dict], who: dict) -> list[dict]:
        out = []
        for t in ml.TILES:
            o = (s or {}).get('owners', {}).get(str(t['i'])) if s else None
            owner = who.get(str(o['owner'])) if o else None
            color = _hex(GROUP_COLORS[t['group']]) if t['type'] == 'land' else _hex(SPECIAL[t['type']][0])
            out.append({'i': t['i'], 'name': t['name'], 'kind': KIND[t['type']], 'color': color,
                        'price': t['price'], 'rent': ml.rent_for(s, t['i']) if o else t.get('rent', 0),
                        'owner': owner[0] if owner else None, 'owner_color': owner[1] if owner else None,
                        'level': o['level'] if o else 0})
        return out

    def join(self, gid: int, uid: int) -> dict:
        ml.join(self._g(gid), uid, time.time())
        self.save()
        return self.view(gid, uid)

    def _season(self, gid: int, uid: int) -> dict:
        s = self._g(gid).get('season')
        if not s or str(uid) not in s['players']:
            raise FunError('你還沒加入本季')
        if time.time() >= s['end']:
            raise FunError('這一季剛結束，正在結算，等一下再來')
        return s

    def roll(self, gid: int, uid: int) -> dict:
        s = self._season(gid, uid)
        self._fun().need_tickets(uid, 1)
        res = ml.roll(s, uid, self.rng, time.time())
        self.save()
        return {**self.view(gid, uid), 'roll': {'dice': list(res['dice']), 'from': res['from'], 'to': res['to'],
                                                'events': [_plain(e) for e in res['events']]}}

    def decide(self, gid: int, uid: int, action: str) -> dict:
        s = self._season(gid, uid)
        pd = s['players'][str(uid)].get('pending')
        if not pd:
            raise FunError('現在沒有要決定的事')
        if time.time() - pd['at'] > ml.DECISION_TIMEOUT:
            ml.decline(s, uid)
            self.save()
            raise FunError('超過 5 分鐘沒決定，當作不要了')
        if action == 'skip':
            ml.decline(s, uid)
            msg = '決定不用了'
        elif action == 'buy' and pd['type'] == 'buy':
            ok, msg = ml.buy(s, uid, pd['tile'])
        elif action == 'up' and pd['type'] == 'up':
            ok, msg = ml.upgrade(s, uid, pd['tile'])
        else:
            raise FunError('這個決定已經失效了')
        self.save()
        return {**self.view(gid, uid), 'msg': _plain(msg)}

    def bail(self, gid: int, uid: int) -> dict:
        s = self._season(gid, uid)
        ok, msg = ml.bail(s, uid)
        if not ok:
            raise FunError(msg)
        self.save()
        return {**self.view(gid, uid), 'msg': _plain(msg)}

    def board_png(self, gid: int) -> Optional[bytes]:
        s = self._g(gid).get('season')
        if not s:
            return None
        return render_board(s, self.names(s), (s['end'] - time.time()) / 86400)

    def titles(self, uid: int) -> int:
        """當過幾次賽季冠軍(所有伺服器)。"""
        return sum(1 for g in self.data['guilds'].values() for h in g.get('history', []) if h.get('champion') == uid)

    def ranking(self, gid: int) -> list[tuple[int, int]]:
        s = self._g(gid).get('season')
        return ml.standings(s) if s else []

    # ── 背景：賽季結算 ──
    async def _season_loop(self):
        await self.bot.wait_until_ready()
        while True:
            try:
                await self._settle_all()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning(f'🎲 賽季檢查例外: {e!r}')
            await asyncio.sleep(random.randint(CHECK_MIN, CHECK_MAX))

    async def _settle_all(self):
        now = time.time()
        fun = self._fun()
        for gid, g in list(self.data['guilds'].items()):
            rec = ml.settle_if_due(g, now)
            if rec:
                # 封存、開新季、待發獎勵、待公告一起寫檔；之後每發一筆存一次 → 重啟不漏發也不重發
                g.setdefault('pay', []).extend({'uid': r['uid'], 'reward': r['reward']}
                                               for r in rec['ranking'] if r['reward'])
                g['announce'] = rec['id']
                self.save()
                log.info(f"🎲 伺服器 {gid} 第 {rec['id']} 季結算，冠軍 {rec['champion']}")
            if not fun:
                continue     # 錢包還沒載入：下一輪再發
            while g.get('pay'):
                r = g['pay'][0]
                fun.add_coins(r['uid'], r['reward'])
                g['pay'].pop(0)
                self.save()
            if g.get('announce'):
                rec = next((h for h in g['history'] if h.get('id') == g['announce']), None)
                g['announce'] = None
                self.save()      # 公告不管成功與否都不重發
                if rec:
                    await self._announce(int(gid), rec, g['season'])

    async def _announce(self, gid: int, rec: dict, new_season: dict):
        fun = self._fun()
        web = self.bot.get_cog('FunWebCog')
        if web and rec.get('champion'):
            await web.celebrate(gid, 'mono_champion', rec['champion'],
                                f"大富翁第 {rec['id']} 季冠軍！淨值 {rec['champion_nw']:,}", big=True)
        ch = fun.channel_for(gid) if fun else None
        if not ch:
            return
        medal = ['🥇', '🥈', '🥉']
        lines = []
        for i, r in enumerate(rec['ranking'][:10]):
            tag = f"+{r['reward']} 🪙" if r['reward'] else '（沒擲過骰，沒有獎勵）'
            lines.append(f"{medal[i] if i < 3 else f'`{i + 1}.`'} <@{r['uid']}> 淨值 {r['nw']:,} {tag}")
        emb = discord.Embed(title=f"🏆 大富翁 第 {rec['id']} 季結算", color=0xF1C40F,
                            description='\n'.join(lines) or '這一季沒有人參加')
        emb.set_footer(text=f"第 {new_season['id']} 季已自動開始，名單保留、現金重置")
        try:
            await ch.send(embed=emb, view=launch_view(), allowed_mentions=NO_PING)
        except discord.HTTPException as e:
            log.warning(f'🎲 結算公告發送失敗: {e!r}')


async def setup(bot: commands.Bot):
    await bot.add_cog(MonoCog(bot))
