"""Activity「🎮 活動」面板的後端：把網頁的操作轉給各活動 cog，推播錢包 / 換券 / 慶祝特效。

傳輸沿用 cogs/act_live.py 那條已登入的 WebSocket(協定見 tools/FUN_PROTOCOL.md 的同名說明)：
  上行 {type:'fun', id, op, ...參數}
  下行 {type:'fun', id, ok, data|error}；{type:'fun-push', what:'wallet'|'grant', data}；{type:'celebrate', ...}
HTTP(由 LyricsCog 的伺服器轉進來，Activity 的 CSP 不給載外站圖)：
  api/fun/icon/<lol|valo>/<ref>.png   轉蛋卡圖示
  api/fun/avatar/<uid>.png            Discord 頭像
  api/fun/mono.png?g=<guild>          大富翁棋盤圖
"""
import asyncio
import io
import logging
import re
import time
from typing import Optional

import discord
from aiohttp import web
from discord.ext import commands

from cogs.fun import FunError, launch_view

log = logging.getLogger(__name__)

AVATAR_TTL = 6 * 3600
AVATAR_MAX = 300
UUID_RE = re.compile(r'^[0-9a-fA-F-]{36}$')
CELEBRATE_FISH = {'epic', 'legend'}      # 這些稀有度的伺服器紀錄才放特效(一開始幾乎每條都是紀錄)


class FunWebCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._avatars: dict[int, tuple[float, bytes]] = {}
        self._mono_png: dict[int, tuple[str, bytes]] = {}
        self.OPS = {
            'home': self._home, 'daily': self._daily, 'link_add': self._link_add, 'link_remove': self._link_remove,
            'gacha_pull': self._gacha_pull, 'gacha_dex': self._gacha_dex, 'gacha_sell': self._gacha_sell,
            'fish_cast': self._fish_cast, 'fish_bag': self._fish_bag, 'fish_sell': self._fish_sell,
            'fish_dex': self._fish_dex, 'fish_records': self._fish_records,
            'bj_start': self._bj, 'bj_hit': self._bj, 'bj_stand': self._bj,
            'mines_start': self._mines, 'mines_reveal': self._mines, 'mines_cash': self._mines,
            'slots_spin': self._slots, 'flip': self._flip,
            'lottery': self._lottery, 'lottery_buy': self._lottery_buy,
            'mono': self._mono, 'mono_join': self._mono_join, 'mono_roll': self._mono_roll,
            'mono_decide': self._mono_decide, 'mono_bail': self._mono_bail,
            'shop': self._shop, 'shop_buy': self._shop_buy, 'equip': self._equip,
            'profile': self._profile, 'top': self._top,
        }

    def cog(self, name: str):
        c = self.bot.get_cog(name)
        if not c:
            raise FunError('這個活動還沒載入，等一下再試')
        return c

    @property
    def fun(self):
        return self.cog('FunCog')

    # ── WebSocket 操作(由 ActLiveCog 呼叫) ──
    async def op(self, ws, gid: int, sess: Optional[dict], m: dict):
        rid = m.get('id')
        name = str(m.get('op') or '')
        if not sess:
            await self._reply(ws, rid, error='要先授權登入才能玩（重開 Activity 時按「授權」）')
            return
        fn = self.OPS.get(name)
        if not fn:
            await self._reply(ws, rid, error='不支援的操作')
            return
        uid = sess['id']
        try:
            self.fun.seen(uid, gid, sess.get('name') or '')
            data = await fn(uid, gid, m)
            data.setdefault('wallet', self.fun.wallet(uid))
        except FunError as e:
            await self._reply(ws, rid, error=str(e))
            return
        except Exception as e:
            log.exception(f'🎮 活動操作 {name} 失敗: {e!r}')
            await self._reply(ws, rid, error='出了點問題，等一下再試')
            return
        await self._reply(ws, rid, data=data)

    @staticmethod
    async def _reply(ws, rid, data=None, error: str = ''):
        if ws.closed:
            return
        msg = {'type': 'fun', 'id': rid, 'ok': not error}
        if error:
            msg['error'] = error
        else:
            msg['data'] = data
        try:
            await ws.send_json(msg)
        except (ConnectionResetError, RuntimeError):
            pass

    # ── 推播 ──
    def _act(self):
        return self.bot.get_cog('ActLiveCog')

    async def push_wallet(self, uid: int):
        act = self._act()
        if act:
            await act.send_user(uid, {'type': 'fun-push', 'what': 'wallet', 'data': self.fun.wallet(uid)})

    async def on_grant(self, uid: int, grant: dict):
        """FunCog 換券後呼叫：推給本人(跳卡片)；拿滿 4 張全伺服器放特效。"""
        act = self._act()
        if act:
            await act.send_user(uid, {'type': 'fun-push', 'what': 'grant', 'data': grant})
            await self.push_wallet(uid)
        if grant['got'] >= 4:
            await self.celebrate_user(uid, 'max_ticket',
                                      f"{grant['game']} {grant['who']} {grant['kda']}，表現滿分拿 4 張券！", big=True)

    async def celebrate(self, gid: int, kind: str, uid: int, text: str, big: bool = False, icon: Optional[str] = None):
        act = self._act()
        if act:
            await act.send_guild(gid, {'type': 'celebrate', 'kind': kind, 'user': self.fun.name_of(uid),
                                       'uid': str(uid), 'text': text, 'icon': icon, 'big': big})

    async def celebrate_user(self, uid: int, kind: str, text: str, big: bool = False, icon: Optional[str] = None):
        for gid in self.fun.user(uid)['guilds']:
            await self.celebrate(gid, kind, uid, text, big, icon)

    # ── 錢包 / 帳號 / 商店 / 個人卡片 ──
    async def _home(self, uid, gid, m):
        return {}

    async def _daily(self, uid, gid, m):
        return {'got': self.fun.daily(uid)}

    async def _link_add(self, uid, gid, m):
        await self.fun.link_add(uid, str(m.get('game') or ''), str(m.get('rid') or '')[:64])
        return {}

    async def _link_remove(self, uid, gid, m):
        self.fun.link_remove(uid, str(m.get('game') or ''), str(m.get('rid') or '')[:64])
        return {}

    async def _shop(self, uid, gid, m):
        return {'items': self.fun.shop(uid)}

    async def _shop_buy(self, uid, gid, m):
        self.fun.shop_buy(uid, str(m.get('item') or ''))
        return {'items': self.fun.shop(uid)}

    async def _equip(self, uid, gid, m):
        self.fun.equip(uid, m.get('item') or None, m.get('kind'))
        return {'items': self.fun.shop(uid)}

    async def _profile(self, uid, gid, m):
        who = int(m.get('uid') or uid)
        fun = self.fun
        u = fun.user(who)
        w = fun.wallet(who)
        gacha, fish = self.bot.get_cog('GachaCog'), self.bot.get_cog('FishingCog')
        mini, mono = self.bot.get_cog('MiniGamesCog'), self.bot.get_cog('MonoCog')
        return {'uid': str(who), 'name': fun.name_of(who), 'avatar': f'api/fun/avatar/{who}.png',
                'title': w['title'], 'frame': w['frame'], 'bg': w['bg'], 'accounts': w['accounts'],
                'stats': {'tickets_total': u['total'], 'coins': u['coins'],
                          'gacha': await gacha.summary(who) if gacha else None,
                          'fish': fish.summary(who) if fish else None,
                          'lottery_wins': u['lottery_wins'],
                          'mono_titles': mono.titles(who) if mono else 0,
                          'mini_best': mini.best(who) if mini else 0},
                'wallet': fun.wallet(uid)}

    async def _top(self, uid, gid, m):
        kind = m.get('kind')
        fun = self.fun
        if kind == 'gacha':
            rows = [(u, owned, f'SSR {ssr}') for u, owned, ssr in await self.cog('GachaCog').ranking(gid)]
        elif kind == 'fish':
            rows = [(u, n, '') for u, n in self.cog('FishingCog').ranking(gid)]
        elif kind == 'mono':
            rows = [(u, nw, '') for u, nw in self.cog('MonoCog').ranking(gid)]
        else:
            rows = sorted(((int(k), v.get('coins', 0), '') for k, v in fun.data['users'].items()
                           if gid in v.get('guilds', [])), key=lambda r: -r[1])
        return {'rows': [{'rank': i + 1, 'uid': str(u), 'name': fun.name_of(u), 'avatar': f'api/fun/avatar/{u}.png',
                          'value': v, 'sub': sub} for i, (u, v, sub) in enumerate(rows[:20])]}

    # ── 轉蛋 ──
    async def _gacha_pull(self, uid, gid, m):
        results = await self.cog('GachaCog').do_pull(uid, int(m.get('count') or 1))
        for r in results:
            if r['rarity'] == 'SSR':
                await self.celebrate(gid, 'ssr', uid, f"抽到傳說卡「{r['name']}」！", big=True, icon=r['icon'])
                asyncio.ensure_future(self._post_ssr(gid, uid, r))
        return {'results': results}

    async def _post_ssr(self, gid: int, uid: int, r: dict):
        """傳說卡也貼到活動頻道(沒開 Activity 的人也看得到)。"""
        ch = self.fun.channel_for(gid)
        if not ch:
            return
        try:
            from games.gacha_image import render_pulls
            gacha = self.cog('GachaCog')
            game, _, ref = r['id'].partition(':')
            path = await gacha.icon_path(game, ref)
            icon = open(path, 'rb').read() if path else None
            png = await asyncio.to_thread(render_pulls, [{'name': r['name'], 'rarity': 'SSR', 'icon': icon, 'new': r['new']}])
            emb = discord.Embed(title='🌟 傳說降臨！', color=0xFFC828,
                                description=f"<@{uid}> 在轉蛋抽到了傳說卡 **{discord.utils.escape_markdown(r['name'])}**！")
            emb.set_image(url='attachment://ssr.png')
            await ch.send(embed=emb, file=discord.File(io.BytesIO(png), 'ssr.png'), view=launch_view(),
                          allowed_mentions=discord.AllowedMentions.none())
        except Exception as e:
            log.warning(f'🎮 傳說卡公告失敗: {e!r}')

    async def _gacha_dex(self, uid, gid, m):
        return await self.cog('GachaCog').dex(int(m.get('uid') or uid))

    async def _gacha_sell(self, uid, gid, m):
        return await self.cog('GachaCog').sell(uid)

    # ── 釣魚 ──
    async def _fish_cast(self, uid, gid, m):
        r = self.cog('FishingCog').cast(uid, gid, int(m.get('count') or 1))
        for c in r['catches']:
            if c['rarity'] == 'legend':
                await self.celebrate(gid, 'legend_fish', uid, f"釣到傳說魚 {c['emoji']} {c['name']} {c['weight']:g} 公斤！",
                                     big=True)
            elif c['record'] and c['rarity'] in CELEBRATE_FISH:
                await self.celebrate(gid, 'fish_record', uid, f"{c['emoji']} {c['name']} {c['weight']:g} 公斤，伺服器新紀錄！")
        return r

    async def _fish_bag(self, uid, gid, m):
        return self.cog('FishingCog').bag(uid)

    async def _fish_sell(self, uid, gid, m):
        return self.cog('FishingCog').sell(uid)

    async def _fish_dex(self, uid, gid, m):
        return self.cog('FishingCog').dex(int(m.get('uid') or uid))

    async def _fish_records(self, uid, gid, m):
        return self.cog('FishingCog').records(gid)

    # ── 小遊戲 ──
    async def _bj(self, uid, gid, m):
        return {'game': self.cog('MiniGamesCog').bj(uid, m['op'].split('_', 1)[1])}

    async def _mines(self, uid, gid, m):
        i = m.get('i')
        return {'game': self.cog('MiniGamesCog').mines(uid, m['op'].split('_', 1)[1], i if isinstance(i, int) else None)}

    async def _slots(self, uid, gid, m):
        return self.cog('MiniGamesCog').slots(uid)

    async def _flip(self, uid, gid, m):
        return self.cog('MiniGamesCog').flip(uid, str(m.get('guess') or ''))

    # ── 樂透 ──
    async def _lottery(self, uid, gid, m):
        return self.cog('LotteryCog').info(gid, uid)

    async def _lottery_buy(self, uid, gid, m):
        return self.cog('LotteryCog').buy(gid, uid, int(m.get('count') or 1))

    # ── 大富翁 ──
    async def _mono(self, uid, gid, m):
        return self.cog('MonoCog').view(gid, uid)

    async def _mono_join(self, uid, gid, m):
        return self.cog('MonoCog').join(gid, uid)

    async def _mono_roll(self, uid, gid, m):
        return self.cog('MonoCog').roll(gid, uid)

    async def _mono_decide(self, uid, gid, m):
        return self.cog('MonoCog').decide(gid, uid, str(m.get('action') or ''))

    async def _mono_bail(self, uid, gid, m):
        return self.cog('MonoCog').bail(gid, uid)

    # ── HTTP ──
    async def handle(self, request: web.Request, tail: str):
        m = re.match(r'^icon/(lol|valo)/([\w-]{1,40})\.png$', tail)
        if m:
            game, ref = m.groups()
            if (game == 'lol' and not ref.isdigit()) or (game == 'valo' and not UUID_RE.match(ref)):
                raise web.HTTPNotFound()
            gacha = self.bot.get_cog('GachaCog')
            path = await gacha.icon_path(game, ref) if gacha else None
            if not path:
                raise web.HTTPNotFound()
            return web.FileResponse(path, headers={'Cache-Control': 'max-age=604800'})
        m = re.match(r'^avatar/(\d{5,20})\.png$', tail)
        if m:
            return await self._avatar(int(m.group(1)))
        if tail == 'mono.png':
            return await self._mono_board(request)
        raise web.HTTPNotFound()

    async def _avatar(self, uid: int):
        hit = self._avatars.get(uid)
        if not hit or time.time() - hit[0] > AVATAR_TTL:
            user = self.bot.get_user(uid)
            if user is None:
                try:
                    user = await self.bot.fetch_user(uid)
                except discord.HTTPException:
                    raise web.HTTPNotFound()
            try:
                data = await user.display_avatar.replace(size=128, format='png').read()
            except discord.HTTPException:
                raise web.HTTPNotFound()
            if len(self._avatars) >= AVATAR_MAX:
                self._avatars.pop(next(iter(self._avatars)))
            self._avatars[uid] = hit = (time.time(), data)
        return web.Response(body=hit[1], content_type='image/png', headers={'Cache-Control': 'max-age=3600'})

    async def _mono_board(self, request: web.Request):
        try:
            gid = int(request.query.get('g') or 0)
        except ValueError:
            raise web.HTTPNotFound()
        mono = self.bot.get_cog('MonoCog')
        g = mono.data['guilds'].get(str(gid)) if mono else None
        s = g.get('season') if g else None
        if not s:
            raise web.HTTPNotFound()
        key = f"{s['id']}:{s['seq']}:{int((s['end'] - time.time()) // 3600)}"
        hit = self._mono_png.get(gid)
        if not hit or hit[0] != key:
            png = await asyncio.to_thread(mono.board_png, gid)
            self._mono_png[gid] = hit = (key, png)
        return web.Response(body=hit[1], content_type='image/png', headers={'Cache-Control': 'max-age=60'})


async def setup(bot: commands.Bot):
    await bot.add_cog(FunWebCog(bot))
