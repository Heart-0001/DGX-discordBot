"""Activity 的互動：知道是誰在看(Discord OAuth)、點歌控制、表情反應。

登入(Embedded App SDK 的標準流程)：
  前端 sdk.commands.authorize({scope: ['identify']}) 拿到 code → POST api/token {code}
  → 這裡用 client secret 跟 Discord 換 access_token、查 /users/@me → 回 {access_token, sid, user}
  → 前端再 sdk.commands.authenticate({access_token})，之後 WebSocket 帶 sid 表明身分。
  sid 只放記憶體，bot 重啟就失效(前端斷線重連時會重新登入)。沒設 DISCORD_CLIENT_SECRET → 只能看不能操作。
WebSocket api/live?g=伺服器&c=頻道&sid=
  下行 {type:'me', user, canAuth}；{type:'queue', ...MusicCog.queue_view}(變了才送)；
       {type:'react', emoji, name}(同頻道全部人)；{type:'toast', text, ok}
  上行 {type:'react', emoji}；{type:'music', action:'pause'|'resume'|'skip'|'remove'|'volume', index, url, level, quiet}；
       {type:'add', query, next}
權限：在場的人都能按表情；音樂操作要在語音頻道裡(bot 已在別的頻道就要同一個)，規則在 MusicCog._check_member。
"""
import asyncio
import json
import logging
import os
import re
import secrets
import time
from typing import Optional

import aiohttp
import discord
from aiohttp import web
from discord.ext import commands

log = logging.getLogger(__name__)

API = 'https://discord.com/api/v10'
EMOJIS = ('👍', '😂', '🔥', '😭', '❤️', '🎉', '👏', '💀', '🤯', '😎')
REACT_GAP = 0.35          # 秒：同一個人兩次表情至少間隔
QUEUE_TICK = 1.0          # 秒：佇列變了沒的檢查間隔
SESSION_TTL = 12 * 3600
QUERY_MAX = 300
YT_ID = re.compile(r'^[\w-]{11}$')


class ActLiveCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.secret = os.getenv('DISCORD_CLIENT_SECRET', '').strip()
        self._http: Optional[aiohttp.ClientSession] = None
        self._sessions: dict[str, dict] = {}            # sid → {id, name, at}
        self._socks: dict[int, set] = {}                # 頻道 → 開著的 WebSocket(廣播表情 / 操作結果)
        self._adding: set[int] = set()                  # 正在點歌(搜尋中)的人，一次一首

    async def cog_load(self):
        self._http = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))
        if not self.secret:
            log.warning('🎛️ 沒設 DISCORD_CLIENT_SECRET：Activity 只能看，點歌 / 表情反應停用')

    async def cog_unload(self):
        if self._http:
            await self._http.close()

    # ── HTTP(由 LyricsCog 的伺服器轉進來) ──
    async def handle(self, request: web.Request):
        path = request.path
        if path == '/api/token' and request.method == 'POST':
            return await self._h_token(request)
        if path == '/api/live':
            return await self._h_live(request)
        m = re.match(r'^/api/ytthumb/([\w-]{11})\.jpg$', path)
        if m:
            return await self._h_ytthumb(m.group(1))
        raise web.HTTPNotFound()

    async def _h_token(self, request: web.Request):
        if not self.secret:
            return web.json_response({'error': '尚未設定 DISCORD_CLIENT_SECRET'}, status=503)
        try:
            code = (await request.json()).get('code')
        except (ValueError, AttributeError):
            code = None
        if not isinstance(code, str) or not 0 < len(code) <= 200:
            return web.json_response({'error': 'bad code'}, status=400)
        form = {'client_id': str(self.bot.application_id), 'client_secret': self.secret,
                'grant_type': 'authorization_code', 'code': code}
        try:
            async with self._http.post(f'{API}/oauth2/token', data=form) as r:
                tok = await r.json(content_type=None)
                if r.status != 200 or not tok.get('access_token'):
                    log.warning(f'🎛️ OAuth 換 token 失敗: {r.status} {tok.get("error")}')
                    return web.json_response({'error': 'oauth failed'}, status=401)
            async with self._http.get(f'{API}/users/@me', headers={'Authorization': f'Bearer {tok["access_token"]}'}) as r:
                me = await r.json(content_type=None)
                if r.status != 200 or not me.get('id'):
                    return web.json_response({'error': 'user lookup failed'}, status=401)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as e:
            log.warning(f'🎛️ OAuth 例外: {e!r}')
            return web.json_response({'error': 'oauth error'}, status=502)
        now = time.time()
        self._sessions = {k: v for k, v in self._sessions.items() if now - v['at'] < SESSION_TTL}
        sid = secrets.token_urlsafe(24)
        user = {'id': str(me['id']), 'name': me.get('global_name') or me.get('username') or ''}
        self._sessions[sid] = {'id': int(me['id']), 'name': user['name'], 'at': now}
        log.info(f'🎛️ Activity 登入: {user["name"]} ({user["id"]})')
        return web.json_response({'access_token': tok['access_token'], 'sid': sid, 'user': user})

    async def _h_ytthumb(self, vid: str):
        """佇列的小封面(Activity 的 CSP 不給載外站圖)：只代抓 i.ytimg.com 的固定尺寸。"""
        if not YT_ID.match(vid):
            raise web.HTTPNotFound()
        try:
            async with self._http.get(f'https://i.ytimg.com/vi/{vid}/mqdefault.jpg') as r:
                if r.status != 200:
                    raise web.HTTPNotFound()
                body = await r.read()
        except (aiohttp.ClientError, asyncio.TimeoutError):
            raise web.HTTPNotFound()
        return web.Response(body=body, content_type='image/jpeg', headers={'Cache-Control': 'max-age=86400'})

    # ── WebSocket ──
    async def _h_live(self, request: web.Request):
        ws = web.WebSocketResponse(heartbeat=20)
        await ws.prepare(request)
        try:
            gid = int(request.query.get('g') or 0)
            cid = int(request.query.get('c') or 0)
        except ValueError:
            await ws.close()
            return ws
        sess = self._sessions.get(request.query.get('sid') or '')
        self._socks.setdefault(cid, set()).add(ws)
        pusher = asyncio.ensure_future(self._push_queue(ws, gid))
        last_react = 0.0
        try:
            await ws.send_json({'type': 'me', 'user': sess['name'] if sess else None, 'canAuth': bool(self.secret)})
            async for msg in ws:
                if msg.type != aiohttp.WSMsgType.TEXT:
                    continue
                try:
                    m = json.loads(msg.data)
                except ValueError:
                    continue
                if not isinstance(m, dict):
                    continue
                if not sess:
                    await self._toast(ws, '要先授權登入才能操作' if self.secret else '機器人還沒設定登入(client secret)', False)
                    continue
                kind = m.get('type')
                if kind == 'react':
                    if m.get('emoji') in EMOJIS and time.monotonic() - last_react >= REACT_GAP:
                        last_react = time.monotonic()
                        await self._broadcast(cid, {'type': 'react', 'emoji': m['emoji'], 'name': sess['name']})
                elif kind in ('music', 'add'):
                    guild = self.bot.get_guild(gid)
                    music = self.bot.get_cog('MusicCog')
                    # 預設沒開 members intent：只有在語音頻道裡的人會在快取裡
                    member = guild.get_member(sess['id']) if guild else None
                    if not guild or not music:
                        await self._toast(ws, '音樂模組沒有載入', False)
                    elif member is None:
                        await self._toast(ws, '請先加入語音頻道', False)
                    elif kind == 'music':
                        await self._music(ws, cid, guild, member, music, m)
                    else:
                        asyncio.ensure_future(self._add(ws, cid, guild, member, music, m))
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        except Exception as e:
            log.warning(f'🎛️ Activity WebSocket 例外: {e!r}')
        finally:
            pusher.cancel()
            self._socks.get(cid, set()).discard(ws)
        return ws

    async def _push_queue(self, ws: web.WebSocketResponse, gid: int):
        music = self.bot.get_cog('MusicCog')
        last = None
        try:
            while not ws.closed and music:
                view = music.queue_view(gid)
                if view != last:
                    last = view
                    await ws.send_json({'type': 'queue', **view})
                await asyncio.sleep(QUEUE_TICK)
        except (ConnectionResetError, RuntimeError):
            pass

    async def _music(self, ws, cid: int, guild: discord.Guild, member: discord.Member, music, m: dict):
        idx, level = m.get('index'), m.get('level')
        try:
            text = music.web_control(guild, member, str(m.get('action') or ''),
                                     index=idx if isinstance(idx, int) else None, url=str(m.get('url') or ''),
                                     level=level if isinstance(level, int) else None)
        except ValueError as e:
            await self._toast(ws, str(e), False)
            return
        if m.get('quiet'):   # 拖音量條途中：只套用，放開時(不帶 quiet)才廣播
            return
        log.info(f'🎛️ Activity {member.display_name}: {text}')
        await self._broadcast(cid, {'type': 'toast', 'text': f'{member.display_name}　{text}', 'ok': True})

    async def _add(self, ws, cid: int, guild: discord.Guild, member: discord.Member, music, m: dict):
        if member.id in self._adding:
            await self._toast(ws, '上一首還在搜尋中', False)
            return
        query = str(m.get('query') or '')[:QUERY_MAX]
        self._adding.add(member.id)
        try:
            await self._toast(ws, f'搜尋中：{query}', True)
            text = await music.web_enqueue(guild, member, query, next_=bool(m.get('next')))
        except ValueError as e:
            await self._toast(ws, str(e), False)
            return
        except Exception as e:
            log.exception(f'🎛️ Activity 點歌失敗: {e!r}')
            await self._toast(ws, '點歌失敗', False)
            return
        finally:
            self._adding.discard(member.id)
        log.info(f'🎛️ Activity {member.display_name}: {text}')
        await self._broadcast(cid, {'type': 'toast', 'text': f'{member.display_name}　{text}', 'ok': True})

    @staticmethod
    async def _toast(ws, text: str, ok: bool):
        if not ws.closed:
            await ws.send_json({'type': 'toast', 'text': text, 'ok': ok})

    async def _broadcast(self, cid: int, msg: dict):
        for w in list(self._socks.get(cid, ())):
            if not w.closed:
                try:
                    await w.send_json(msg)
                except (ConnectionResetError, RuntimeError):
                    pass


async def setup(bot: commands.Bot):
    await bot.add_cog(ActLiveCog(bot))
