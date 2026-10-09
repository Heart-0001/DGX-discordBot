"""/lyrics：在語音頻道開 Discord Activity，顯示跟音樂同步的歌詞。

架構：
  bot 內嵌一個 aiohttp 伺服器(只聽 127.0.0.1)，由 cloudflared tunnel 對外，
  Developer Portal 的 Activity URL Mapping 指到那個網址。
  網頁(activity/dist)用 WebSocket 每 0.25 秒拿一次 MusicCog 的播放快照；
  播放位置來自 TrackedSource 實際送出的音框數，暫停時自動停住。
歌詞來源(依序)：
  1. YouTube Music 正在播的這首(官方 LyricFind / Musixmatch，有每句結束時間)
  2. YouTube Music 用歌名搜歌曲版本(MV 影片本身常沒歌詞)
  3. LRCLIB(https://lrclib.net)
  4. 網易雲 / QQ 音樂 / 酷狗同時查(cogs/lyrics_sources.py)
  都沒有時間軸才退回 LRCLIB 的純文字。
"""
import asyncio
import logging
import os
import re
import threading
import time
from collections import OrderedDict
from functools import partial
from typing import Optional
from urllib.parse import urlparse

import aiohttp
import discord
import requests
from aiohttp import web
from discord import app_commands
from discord.ext import commands
from ytmusicapi import YTMusic

from cogs.lyrics_match import (clean_artist, name_variants, parse_lrc, pick_candidate, pick_result,
                               title_candidates, usable_synced)
from cogs.lyrics_sources import SOURCES

log = logging.getLogger(__name__)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST_DIR = os.path.join(ROOT, 'activity', 'dist')
HOST = '127.0.0.1'
PORT = int(os.getenv('LYRICS_PORT', '8765'))

LRCLIB = 'https://lrclib.net/api'
LRCLIB_HEADERS = {'User-Agent': 'heart-discordbot-lyrics/1.0 (https://github.com/)'}
TICK_SECS = 0.25
CACHE_SIZE = 200
THUMB_HOSTS = ('ytimg.com', 'googleusercontent.com', 'ggpht.com')
YTM_TIMEOUT = (10, 20)
GAP_SECS = 4   # YTM 歌詞一句結束到下一句開始超過這麼久 → 插空白行(前端顯示間奏三個點)

def thumb_url(song: dict) -> str:
    """歌曲封面；播放清單/YTM 網址加進來的歌常沒有 thumbnail，就用影片 ID 組 YouTube 縮圖。"""
    if song.get('thumbnail'):
        return song['thumbnail']
    m = re.search(r'(?:v=|youtu\.be/)([\w-]{11})', song.get('webpage_url') or '')
    return f'https://i.ytimg.com/vi/{m.group(1)}/hqdefault.jpg' if m else ''


def song_key(song: dict) -> str:
    return song.get('webpage_url') or song.get('title') or ''


class LyricsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._cache: OrderedDict[str, dict] = OrderedDict()
        self._inflight: dict[str, asyncio.Task] = {}
        self._http: Optional[aiohttp.ClientSession] = None
        self._runner: Optional[web.AppRunner] = None
        # 自己一個 YTMusic(跟 MusicCog 分開，不搶它的 session)；ytmusicapi 是同步的 → 丟 thread + lock
        session = requests.Session()
        session.request = partial(session.request, timeout=YTM_TIMEOUT)  # type: ignore[method-assign]
        self._ytm = YTMusic(requests_session=session)
        self._ytm_lock = threading.Lock()

    # ── lifecycle ──
    async def cog_load(self):
        self._http = aiohttp.ClientSession(headers=LRCLIB_HEADERS, timeout=aiohttp.ClientTimeout(total=15))
        app = web.Application()
        app.on_response_prepare.append(self._no_cache)
        app.router.add_get('/api/config', self._h_config)
        app.router.add_get('/api/thumb', self._h_thumb)
        app.router.add_get('/ws', self._h_ws)
        app.router.add_get('/api/mode', self._h_mode)
        app.router.add_get('/lol/{tail:.*}', self._h_lol)
        app.router.add_get('/', self._h_index)
        if os.path.isdir(DIST_DIR):
            app.router.add_static('/', DIST_DIR)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        await web.TCPSite(self._runner, HOST, PORT).start()
        log.info(f'🎤 歌詞 Activity 伺服器: http://{HOST}:{PORT}')

    async def cog_unload(self):
        if self._runner:
            await self._runner.cleanup()
        if self._http:
            await self._http.close()

    # ── lyrics lookup ──
    async def get_lyrics(self, song: dict) -> dict:
        key = song_key(song)
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        task = self._inflight.get(key)
        if task is None:
            task = asyncio.ensure_future(self._lookup(song))
            self._inflight[key] = task
        try:
            result = await asyncio.shield(task)
        finally:
            self._inflight.pop(key, None)
        self._cache[key] = result
        while len(self._cache) > CACHE_SIZE:
            self._cache.popitem(last=False)
        return result

    # ── YouTube Music 歌詞 ──
    def _ytm_timed_lyrics_sync(self, video_id: str) -> Optional[list[dict]]:
        with self._ytm_lock:
            d = self._ytm._send_request('next', {'videoId': video_id, 'isAudioOnly': True})
            tabs = (d['contents']['singleColumnMusicWatchNextResultsRenderer']['tabbedRenderer']
                    ['watchNextTabbedResultsRenderer']['tabs'])
            ep = tabs[1]['tabRenderer'].get('endpoint') if len(tabs) > 1 else None
            if not ep:
                return None
            res = self._ytm.get_lyrics(ep['browseEndpoint']['browseId'], timestamps=True)
        if not res or not res.get('hasTimestamps'):
            return None
        out = []
        raw = res['lyrics']
        for i, ln in enumerate(raw):
            text = '' if ln.text.strip() in ('♪', '') else ln.text.strip()
            out.append({'t': ln.start_time / 1000, 'text': text})
            nxt = raw[i + 1].start_time if i + 1 < len(raw) else None
            if text and nxt is not None and (nxt - ln.end_time) / 1000 >= GAP_SECS:
                out.append({'t': ln.end_time / 1000, 'text': ''})   # 間奏
        return out

    async def _ytm_lyrics(self, video_id: str) -> Optional[list[dict]]:
        try:
            lines = await asyncio.to_thread(self._ytm_timed_lyrics_sync, video_id)
        except Exception as e:
            log.warning(f'YTM 歌詞失敗 {video_id}: {e!r}')
            return None
        return lines if usable_synced(lines) else None

    def _ytm_search_sync(self, q: str) -> list[dict]:
        with self._ytm_lock:
            res = self._ytm.search(q, filter='songs', limit=5)
        return [{'artist': ', '.join(a['name'] for a in r.get('artists') or []), 'title': r.get('title', ''),
                 'duration': r.get('duration_seconds') or 0, 'ref': r['videoId']}
                for r in res if r.get('videoId')]

    async def _ytm_by_search(self, cands: list[str], artists: list[str], who: str, duration: float):
        """MV 影片沒歌詞時，到 YTM 找同一首歌的「歌曲」版本(歌手、長度要對得上)。"""
        tried = set()
        for c in cands[:2]:
            for q in ([f'{artists[0]} {c}'] if artists else []) + [c]:
                try:
                    found = await asyncio.to_thread(self._ytm_search_sync, q)
                except Exception as e:
                    log.warning(f'YTM 搜尋失敗 {q}: {e!r}')
                    continue
                for cand in pick_candidate(found, duration, who, cands)[:2]:
                    if cand['ref'] in tried:
                        continue
                    tried.add(cand['ref'])
                    lines = await self._ytm_lyrics(cand['ref'])
                    if lines:
                        return cand, lines
        return None

    async def _lrclib(self, path: str, params: dict):
        try:
            async with self._http.get(f'{LRCLIB}/{path}', params=params) as r:
                if r.status != 200:
                    return None
                return await r.json()
        except Exception as e:
            log.warning(f'LRCLIB {path} 失敗: {e!r}')
            return None

    async def _lookup(self, song: dict) -> dict:
        title = song.get('title', '')
        artist = clean_artist(song.get('uploader', ''))
        duration = song.get('duration') or 0
        cands = title_candidates(title, artist)
        artists = name_variants(artist) if artist else []
        # 上傳者常不是歌手(周杰倫的 MV 是 JVR Music 上傳)→ 標題裡出現的名字也算
        who = f'{artist} {title}'

        # 1. YTM：正在播的這首
        m = re.search(r'(?:v=|youtu\.be/)([\w-]{11})', song.get('webpage_url') or '')
        if m and (lines := await self._ytm_lyrics(m.group(1))):
            log.info(f'🎤 歌詞: {title} → YouTube Music (同步)')
            return {'synced': lines, 'plain': None}
        # 2. YTM：用歌名找歌曲版本
        if hit := await self._ytm_by_search(cands, artists, who, duration):
            cand, lines = hit
            log.info(f'🎤 歌詞: {title} → YouTube Music 搜尋 {cand["artist"]} - {cand["title"]} (同步)')
            return {'synced': lines, 'plain': None}

        found = None

        def better(pick):
            return pick and (not found or (pick.get('syncedLyrics') and not found.get('syncedLyrics')))

        # 3. LRCLIB 精確查詢：歌名 × 歌手組合
        for c in cands[:3]:
            for a in artists:
                q = {'track_name': c, 'artist_name': a}
                if duration:
                    q['duration'] = int(duration)
                d = await self._lrclib('get', q)
                pick = pick_result([d] if d else [], duration, artist, cands)
                if better(pick):
                    found = pick
                if found and found.get('syncedLyrics'):
                    break
            if found and found.get('syncedLyrics'):
                break
        # 3b. LRCLIB 模糊搜尋
        if not found or not found.get('syncedLyrics'):
            for c in cands:
                queries = [{'q': f'{a} {c}'} for a in artists[:2]] + [{'q': c}]
                for q in queries:
                    pick = pick_result(await self._lrclib('search', q) or [], duration, artist, cands)
                    if better(pick):
                        found = pick
                    if found and found.get('syncedLyrics'):
                        break
                if found and found.get('syncedLyrics'):
                    break
        if found and found.get('syncedLyrics'):
            synced = parse_lrc(found['syncedLyrics'])
            log.info(f'🎤 歌詞: {title} → LRCLIB {found.get("artistName")} - {found.get("trackName")} (同步)')
            return {'synced': synced, 'plain': None}
        # 4. 其他來源(中文歌齊全)：同時查，依 SOURCES 順序取第一個有時間軸的
        results = await asyncio.gather(*(self._try_source(src, cands, artists, who, duration)
                                         for src in SOURCES))
        for (name, *_), hit in zip(SOURCES, results):
            if hit:
                cand, synced = hit
                log.info(f'🎤 歌詞: {title} → {name} {cand["artist"]} - {cand["title"]} (同步)')
                return {'synced': synced, 'plain': None}
        if not found:
            log.info(f'🎤 找不到歌詞: {title}')
            return {'synced': None, 'plain': None}
        log.info(f'🎤 歌詞: {title} → LRCLIB {found.get("artistName")} - {found.get("trackName")} (純文字)')
        return {'synced': None, 'plain': found.get('plainLyrics')}

    async def _try_source(self, src, cands: list[str], artists: list[str], who: str, duration: float):
        """在單一外部來源找同步歌詞，回 (候選, 解析後歌詞) 或 None。
        最多試 2 個歌名 ×(歌手+歌名、只有歌名)；who = 上傳者 + 原標題，用來比對歌手。"""
        name, search, fetch = src
        tried = set()
        for c in cands[:2]:
            for q in ([f'{artists[0]} {c}'] if artists else []) + [c]:
                found = pick_candidate(await search(self._http, q, duration), duration, who, cands)
                for cand in found[:2]:
                    if cand['ref'] in tried:
                        continue
                    tried.add(cand['ref'])
                    lrc = await fetch(self._http, cand['ref'])
                    synced = parse_lrc(lrc) if lrc else None
                    if usable_synced(synced):
                        return cand, synced
        return None

    # ── HTTP handlers ──
    @staticmethod
    async def _no_cache(request, response):
        """Cloudflare 預設會把 .js/.css 快取 4 小時，改版後看不到；封面除外(有自己的 max-age)。"""
        if not request.path.startswith(('/api/thumb', '/lol/img/')):
            response.headers['Cache-Control'] = 'no-cache'

    async def _h_index(self, request):
        path = os.path.join(DIST_DIR, 'index.html')
        if not os.path.exists(path):
            return web.Response(text='activity/dist 尚未 build：cd activity && npm run build', status=503)
        return web.FileResponse(path, headers={'Cache-Control': 'no-cache'})

    async def _h_config(self, request):
        return web.json_response({'client_id': str(self.bot.application_id or '')})

    async def _h_mode(self, request):
        """這個語音頻道最後用哪個指令開的 Activity(/lyrics 或 /lol)，前端據此切畫面。"""
        modes = getattr(self.bot, 'activity_modes', {})
        try:
            cid = int(request.query.get('c') or 0)
        except ValueError:
            cid = 0
        mode = modes.get(cid)
        if cid and hasattr(self.bot, 'activity_seen'):
            self.bot.activity_seen[cid] = time.monotonic()
        if request.query.get('first'):
            log.info(f"Activity 開啟: 頻道 {request.query.get('c')} / 伺服器 {request.query.get('g')} → {mode or 'lyrics'}")
        return web.json_response({'mode': mode or 'lyrics'})

    async def _h_lol(self, request):
        lol = self.bot.get_cog('LolCog')
        if not lol:
            raise web.HTTPServiceUnavailable(text='LoL 模組未載入')
        return await lol.handle(request, request.match_info['tail'])

    def _snapshot(self, guild_id: int) -> Optional[dict]:
        music = self.bot.get_cog('MusicCog')
        return music.get_playback(guild_id) if music else None

    async def _h_thumb(self, request):
        """代抓目前歌曲封面(Activity 的 CSP 不允許直接載外部圖片)。只放行 YouTube 圖床。"""
        try:
            snap = self._snapshot(int(request.query.get('g', '0')))
        except ValueError:
            snap = None
        url = thumb_url((snap or {}).get('song', {}))
        host = urlparse(url).hostname or ''
        if not url or not any(host == h or host.endswith('.' + h) for h in THUMB_HOSTS):
            raise web.HTTPNotFound()
        urls = [url]
        # hqdefault / sddefault 是 4:3 帶黑邊，先試 16:9 的 maxresdefault
        m = re.match(r'(https://i\d?\.ytimg\.com/vi(?:_webp)?/[\w-]+/)(hq|sd|mq)default', url)
        if m:
            urls.insert(0, m.group(1).replace('/vi_webp/', '/vi/') + 'maxresdefault.jpg')
        for u in urls:
            try:
                async with self._http.get(u) as r:
                    if r.status != 200:
                        continue
                    body = await r.read()
                    ctype = r.headers.get('Content-Type', 'image/jpeg')
            except aiohttp.ClientError:
                continue
            return web.Response(body=body, content_type=ctype.split(';')[0],
                                headers={'Cache-Control': 'max-age=3600'})
        raise web.HTTPNotFound()

    async def _h_ws(self, request):
        ws = web.WebSocketResponse(heartbeat=20)
        await ws.prepare(request)
        try:
            guild_id = int(request.query.get('g', '0'))
        except ValueError:
            await ws.close()
            return ws
        sent_key: Optional[str] = None
        idle_sent = False
        lyrics_task: Optional[asyncio.Task] = None
        lyrics_sent = True
        # 一定要有人讀：aiohttp 的 heartbeat pong 是在 receive() 裡處理的，
        # 只送不收的話 30 秒後會被判定逾時斷線。
        reader = asyncio.ensure_future(self._drain(ws))
        try:
            while not ws.closed:
                snap = self._snapshot(guild_id)
                if snap is None:
                    if not idle_sent:
                        await ws.send_json({'type': 'idle'})
                        idle_sent, sent_key = True, None
                else:
                    idle_sent = False
                    song = snap['song']
                    key = song_key(song)
                    if key != sent_key:
                        sent_key = key
                        await ws.send_json({
                            'type': 'track', 'key': key,
                            'title': song.get('title', ''),
                            'artist': clean_artist(song.get('uploader', '')),
                            'duration': song.get('duration') or 0,
                            'thumb': f'api/thumb?g={guild_id}&k={abs(hash(key))}' if thumb_url(song) else '',
                        })
                        lyrics_task = asyncio.ensure_future(self.get_lyrics(song))
                        lyrics_sent = False
                    if not lyrics_sent and lyrics_task.done():
                        lyr = ({'synced': None, 'plain': None} if lyrics_task.exception()
                               else lyrics_task.result())
                        await ws.send_json({'type': 'lyrics', 'key': key, **lyr})
                        lyrics_sent = True
                    await ws.send_json({'type': 'pos', 'key': key,
                                        'position': round(snap['position'], 3),
                                        'paused': snap['paused']})
                await asyncio.sleep(TICK_SECS)
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        except Exception as e:
            log.warning(f'🎤 歌詞 WebSocket 例外: {e!r}')
        finally:
            reader.cancel()
        return ws

    @staticmethod
    async def _drain(ws: web.WebSocketResponse):
        async for _ in ws:   # 前端不會傳東西上來，只是讓 ping/pong 與 close 被處理
            pass

    # ── slash command ──
    @app_commands.command(name='lyrics', description='在語音頻道開啟同步歌詞畫面（Discord Activity）')
    async def lyrics(self, interaction: discord.Interaction):
        if not interaction.user.voice:
            await interaction.response.send_message('❌ 請先加入語音頻道再開歌詞畫面', ephemeral=True)
            return
        set_mode = getattr(self.bot, 'set_activity_mode', None)
        if set_mode:
            set_mode(interaction, 'lyrics')
        # Activity 已經開著 → 不再叫 Discord 開(會留下一行空的「使用了 /lyrics」)，畫面 3 秒內自己切
        if getattr(self.bot, 'activity_open', lambda i: False)(interaction):
            await interaction.response.send_message('✅ 已切換成歌詞畫面', ephemeral=True)
            return
        try:
            await interaction.response.launch_activity()
        except discord.HTTPException as e:
            log.error(f'launch_activity 失敗: {e!r}')
            await interaction.response.send_message(
                '❌ 無法開啟 Activity。請確認 Developer Portal 已開啟 Activities 並設好 URL Mapping。',
                ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(LyricsCog(bot))
