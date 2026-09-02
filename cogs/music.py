import asyncio
import json
import logging
import random
import re
import shutil
import threading
import time
import sys
from collections.abc import Set as _AbstractSet
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import partial
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands
from ytmusicapi import YTMusic

log = logging.getLogger(__name__)

# 自動偵測系統 ffmpeg(Linux/macOS 走 PATH);找不到才退回 Windows 上寫死的路徑
FFMPEG_PATH = shutil.which('ffmpeg') or r'C:\Users\Heart\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-8.1-full_build\bin\ffmpeg.exe'

FFMPEG_BEFORE_OPTS = '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -thread_queue_size 4096 -probesize 32'
FFMPEG_OPTS = '-vn -b:a 128k'  # 限制輸出 128kbps，符合 Discord 上限

# 搜尋評分：這些關鍵字出現在標題中代表不是原版官方音源，扣分
_PENALIZED = re.compile(
    r'\b(cover|カバー|翻唱|翻cover|karaoke|lyrics?|歌詞|字幕|live|remix|piano\s*ver|'
    r'acoustic|fan.?made|instrumental|inst\.?|orchestra|orchestral|band\s*ver|tutorial)\b',
    re.IGNORECASE,
)


def make_source(url: str, volume: float, seek: float = 0.0) -> discord.PCMVolumeTransformer:
    before = FFMPEG_BEFORE_OPTS
    if seek > 0.3:
        before = f'{FFMPEG_BEFORE_OPTS} -ss {seek:.3f}'  # 斷點續播:輸入定位,從 seek 秒開始
    return discord.PCMVolumeTransformer(
        discord.FFmpegPCMAudio(
            url,
            executable=FFMPEG_PATH,
            before_options=before,
            options=FFMPEG_OPTS,
        ),
        volume=volume,
    )


@dataclass
class GuildMusicState:
    queue: list[dict] = field(default_factory=list)

    current: Optional[dict] = None
    volume: float = 0.5
    autoplay: bool = False
    history: list[str] = field(default_factory=list)
    history_titles: set[str] = field(default_factory=set)
    autoplay_prefetch: Optional[dict] = None
    autoplay_lock: Optional[asyncio.Lock] = field(default=None, repr=False)  # #3 併發保護
    repeat: str = 'off'   # 'off' | 'one' | 'all'
    repeat_skip: bool = False  # True for one cycle when user explicitly skips
    want_voice: bool = False   # watchdog: 有在播/想繼續 → True;被斷線時自動重連
    voice_channel_id: Optional[int] = None  # watchdog: 應該要連的 voice channel
    current_started_mono: float = 0.0       # 斷點續播:目前這曲开播的 monotonic 時間戳
    stop_token: int = 0                     # /stop 時 +1，讓 in-flight _play_next 知道已停


class MusicCog(commands.Cog):
    YTM_TIMEOUT = (10, 20)  # (connect, read) 秒——YT 回應慢也不該卡死播放

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._states: dict[int, GuildMusicState] = {}
        # 用公開入口 requests_session 餵一個內建 timeout 的 session（ytmusicapi 1.12
        # _prepare_session 的官方做法），避免網路卡死時 innertube / yt_dlp 無限等待。
        import requests
        session = requests.Session()
        session.request = partial(session.request, timeout=self.YTM_TIMEOUT)  # type: ignore[method-assign]
        self._ytm = YTMusic(requests_session=session)
        # 跨 guild 線程保護: innertube 呼叫跑在 thread pool，多 guild 並行時
        # 共用同一個 requests.Session(cookie jar / headers 是可變共享狀態，requests
        # 不保證 thread-safe)。用一把 cog 級 threading.Lock 串行化所有 YTM 請求。
        self._ytm_lock = threading.Lock()
        if getattr(self._ytm, '_send_request', None) is None:
            # 私有 API 若消失，autoplay 會全掛；提前告警，別等上線才炸
            log.warning('ytmusicapi 缺少 _send_request —— autoplay 推薦會不可用，'
                        '請確認 requirements.txt 的 ytmusicapi 版本')
        # ── voice watchdog ──
        self._manual_discard: set[int] = set()   # 被 /disconnect 過的 guild,別自動拉回
        self._watchdog_task = None               # asyncio.Task

    def get_state(self, guild_id: int) -> GuildMusicState:
        return self._states.setdefault(guild_id, GuildMusicState())

    @staticmethod
    def _stream_args(query: str) -> list[str]:
        return [
            sys.executable, '-m', 'yt_dlp',
            '--dump-json', '--quiet', '--no-warnings',
            '--no-playlist',
            '--format', 'bestaudio[abr<=96]/bestaudio/best',
            '--ffmpeg-location', FFMPEG_PATH,
            query,
        ]

    async def _connect_user_voice(self, interaction: discord.Interaction):
        if not interaction.user.voice:
            await interaction.response.send_message('❌ 請先加入一個語音頻道！', ephemeral=True)
            return None

        await interaction.response.defer()

        vc = interaction.guild.voice_client
        if vc is None:
            return await interaction.user.voice.channel.connect()
        if vc.channel != interaction.user.voice.channel:
            await vc.move_to(interaction.user.voice.channel)
        return vc

    async def _fetch_songs_or_reply(self, interaction: discord.Interaction, query: str) -> list[dict] | None:
        try:
            songs = await self.fetch_info(query)
        except Exception as e:
            log.error(f'fetch_info 失敗: {e}')
            await interaction.followup.send(f'❌ 無法取得音樂：{e}')
            return None

        if not songs:
            await interaction.followup.send('❌ 找不到音樂')
            return None
        return songs

    async def _ensure_stream_url(self, song: dict):
        if not song.get('url'):
            song['url'] = await self.fetch_stream_url(song['webpage_url'])

    @staticmethod
    def _remember_played(state: GuildMusicState, song: dict):
        if song.get('webpage_url'):
            state.history.append(song['webpage_url'])
            if len(state.history) > 20:
                state.history.pop(0)
        if song.get('title'):
            state.history_titles.add(song['title'].lower().strip())
            # #8: 上界，避免長播 session 把 set 撐爆 / 永遠濾掉所有候選
            if len(state.history_titles) > 400:
                keep = list(state.history_titles)[len(state.history_titles) // 2:]
                state.history_titles = set(keep)

    def _schedule_prefetch(self, guild_id: int, state: GuildMusicState, *, queued: bool = False):
        if state.autoplay and not state.queue and not state.autoplay_prefetch:
            asyncio.ensure_future(self._prefetch_autoplay(guild_id))
        elif queued and state.queue and not state.queue[0].get('url'):
            asyncio.ensure_future(self._prefetch_next(state))

    def _start_song(
        self,
        guild_id: int,
        voice_client: discord.VoiceClient,
        state: GuildMusicState,
        song: dict,
        *,
        prefetch_queued: bool = False,
        seek: float = 0.0,
    ):
        state.current = song
        if voice_client.channel is not None:
            state.want_voice = True
            state.voice_channel_id = voice_client.channel.id
        source = make_source(song['url'], state.volume, seek=seek)
        state.current_started_mono = time.monotonic()   # 記下這曲的起播時點(供斷點續播)
        voice_client.play(source, after=lambda e: self._after_play(e, guild_id, voice_client))
        log.info(f'開始播放: {song["title"]}' + (f' (續播 @ {seek:.0f}s)' if seek > 0.5 else ''))
        self._remember_played(state, song)
        self._schedule_prefetch(guild_id, state, queued=prefetch_queued)

    async def _run_ytdlp(self, args: list, timeout: int = 30) -> str:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise Exception('取得音樂超時')
        if not stdout:
            err = stderr.decode('utf-8', errors='replace')
            raise Exception(err[:300] or '無法取得音樂')
        return stdout.decode('utf-8', errors='replace')

    def _parse_ytdlp_lines(self, text: str, stream_url: bool = True) -> list[dict]:
        results = []
        for line in text.strip().split('\n'):
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                # flat-playlist entries have 'id', full entries have 'url'
                webpage = d.get('webpage_url') or (
                    f"https://www.youtube.com/watch?v={d['id']}" if d.get('id') else ''
                )
                results.append({
                    'url': d.get('url', '') if stream_url else '',
                    'webpage_url': webpage,
                    'title': d.get('title', 'Unknown'),
                    'duration': d.get('duration', 0),
                    'thumbnail': d.get('thumbnail', ''),
                    'uploader': d.get('uploader', ''),
                })
            except (json.JSONDecodeError, KeyError):
                continue
        return results

    def _ytm_track_to_dict(self, r: dict) -> dict | None:
        """把 ytmusicapi 的 track dict 轉成 bot 內部格式。"""
        vid = r.get('videoId', '')
        if not vid:
            return None
        artists = ', '.join(a['name'] for a in r.get('artists') or [])
        thumbs = r.get('thumbnails') or []
        thumb = thumbs[-1]['url'] if thumbs else ''
        duration_s = r.get('duration_seconds') or 0
        return {
            'url': '',
            'webpage_url': f'https://www.youtube.com/watch?v={vid}',
            'title': r.get('title', 'Unknown'),
            'duration': duration_s,
            'thumbnail': thumb,
            'uploader': artists,
        }

    async def _ytmusic_search(self, query: str, count: int = 5) -> list[dict]:
        """用 ytmusicapi 在 YouTube Music 搜尋歌曲，回傳前 count 個候選。"""
        try:
            loop = asyncio.get_running_loop()
            # 走同一把 _ytm_lock：search() 與 autoplay 的 innertube next 共用
            # 同一個 requests.Session，裸呼叫會跨線程併發操作 → 偶發崩。
            raw = await loop.run_in_executor(
                None,
                lambda: self._search_locked(query, count),
            )
            results = [self._ytm_track_to_dict(r) for r in (raw or [])]
            results = [r for r in results if r][:count]
            if results:
                log.info(f'YouTube Music 候選: {[r["title"] for r in results]}')
            return results
        except Exception as e:
            log.info(f'YouTube Music 搜尋失敗: {e}')
            return []

    def _search_locked(self, query: str, count: int):
        """search 的線程安全包裝（與 _send_request_locked 共用同一把鎖）。"""
        with self._ytm_lock:
            return self._ytm.search(query, filter='songs', limit=count)

    def _pick_best(self, results: list[dict], query: str) -> dict:
        """
        從 YouTube Music 多個候選中，用評分挑出最接近官方原版的結果。
        評分維度：
          +30  標題與查詢字詞的重疊比率（最高 30 分）
          +15  頻道名含 official / vevo
          +10  標題含「official」
          + 5  標題含「audio」（純音樂版）
          -20  標題含封面/卡拉OK/歌詞/Live/Remix 等不想要的標籤
        """
        query_words = set(re.findall(r'\w+', query.lower()))

        def score(r: dict) -> float:
            title = r.get('title', '').lower()
            uploader = (r.get('uploader') or '').lower()
            s = 0.0

            # 標題與查詢字詞的重疊
            title_words = set(re.findall(r'\w+', title))
            if query_words:
                s += len(query_words & title_words) / len(query_words) * 30

            # 官方頻道
            if any(k in uploader for k in ('official', 'vevo')):
                s += 15

            # 標題含 official
            if 'official' in title:
                s += 10

            # 純音樂版
            if 'audio' in title:
                s += 5

            # 扣分：封面/卡拉OK/歌詞/直播/Remix 等
            if _PENALIZED.search(r.get('title', '')):
                s -= 20

            return s

        best = max(results, key=score)
        log.info(f'挑選結果: {best["title"]} (共 {len(results)} 個候選)')
        return best

    async def fetch_info(self, query: str) -> list[dict]:
        is_url = query.startswith('http')

        # Radio Mix URL (list=RD...) → 只播 v= 那首影片，autoplay 會自己接推薦
        if is_url and re.search(r'[?&]list=RD', query):
            m = re.search(r'[?&]v=([a-zA-Z0-9_-]{11})', query)
            if m:
                query = f'https://www.youtube.com/watch?v={m.group(1)}'
                log.info(f'Radio Mix URL → 只播單首: {query}')

        is_playlist = is_url and 'list=' in query

        log.info(f'fetch_info 開始: {query[:80]}')

        if is_playlist:
            # Fast path: flat-playlist (just metadata, no stream URLs)
            out = await self._run_ytdlp([
                sys.executable, '-m', 'yt_dlp',
                '--flat-playlist', '--dump-json', '--quiet', '--no-warnings',
                query,
            ], timeout=60)
            results = self._parse_ytdlp_lines(out, stream_url=False)
        else:
            if is_url:
                # 直接抓指定 URL
                out = await self._run_ytdlp(self._stream_args(query), timeout=30)
                results = self._parse_ytdlp_lines(out, stream_url=True)
            else:
                # 優先用 YouTube Music，取多個候選並評分選最官方版本
                candidates = await self._ytmusic_search(query, count=5)
                if candidates:
                    results = [self._pick_best(candidates, query)]
                else:
                    # YouTube Music 完全失敗才退回 YouTube 搜尋
                    log.info('YouTube Music 失敗，退回 ytsearch1:')
                    out = await self._run_ytdlp(self._stream_args(f'ytsearch1:{query}'), timeout=30)
                    results = self._parse_ytdlp_lines(out, stream_url=True)

        log.info(f'fetch_info 完成: {len(results)} 首')
        return results

    async def fetch_stream_url(self, webpage_url: str) -> str:
        """播放前取得實際串流 URL。"""
        out = await self._run_ytdlp(self._stream_args(webpage_url), timeout=30)
        data = json.loads(out.strip().split('\n')[0])
        return data['url']

    def _after_play(self, error, guild_id: int, voice_client: discord.VoiceClient):
        if error:
            log.error(f'播放回呼錯誤: {error}')
        asyncio.run_coroutine_threadsafe(
            self._play_next(guild_id, voice_client), self.bot.loop
        )

    @staticmethod
    def _extract_vid(url: str) -> str:
        """從 YouTube / YouTube Music URL 取出 video ID，用於比對歷史。"""
        m = re.search(r'(?:v=|youtu\.be/)([a-zA-Z0-9_-]{11})', url or '')
        return m.group(1) if m else url

    @staticmethod
    def _runs_text(runs) -> str:
        """把 innertube 的 {'runs': [{'text': ...}]} 拼成純文字。"""
        if not runs:
            return ''
        if isinstance(runs, str):
            return runs
        return ''.join(r.get('text', '') for r in runs if isinstance(r, dict))

    def _parse_duration(self, text) -> int:
        """'m:ss' / 'h:mm:ss' → 秒。解析不出就回 0。"""
        m = re.fullmatch(r'(?:(\d+):)?(\d{1,2}):(\d{2})', (text or '').strip())
        if not m:
            return 0
        h = int(m.group(1) or 0)
        return h * 3600 + int(m.group(2)) * 60 + int(m.group(3))

    def _parse_innertube_radio(self, data: dict, video_id: str) -> list[dict]:
        """從 innertube 'next' 回應抓 Up-next radio 清單。
        確認好用的路徑 (YT Music 2026 版面):
          contents.singleColumnMusicWatchNextResultsRenderer.tabbedRenderer
            .watchNextTabbedResultsRenderer.tabs[0].tabRenderer.content
            .musicQueueRenderer.content.playlistPanelRenderer.contents
        """
        try:
            panel = (data['contents']['singleColumnMusicWatchNextResultsRenderer']
                     ['tabbedRenderer']['watchNextTabbedResultsRenderer']
                     ['tabs'][0]['tabRenderer']['content']
                     ['musicQueueRenderer']['content']['playlistPanelRenderer'])
            items = panel.get('contents', [])
        except (KeyError, IndexError, TypeError):
            return []
        out = []
        for it in items:
            try:
                if not isinstance(it, dict):
                    continue
                ppvr = it.get('playlistPanelVideoRenderer')
                if not ppvr:
                    continue
                vid = ppvr.get('videoId')
                if not vid or vid == video_id:   # 跳過目前正在播的那首
                    continue
                title_raw = ppvr.get('title')
                title = (self._runs_text(title_raw.get('runs')) or title_raw.get('simpleText', '')
                         if isinstance(title_raw, dict) else (title_raw or ''))
                if not title:
                    continue
                # 藝人 = byline 第一個 run（後面是 '• 205M views • …'）
                lbt = ppvr.get('longBylineText') or {}
                runs = lbt.get('runs') if isinstance(lbt, dict) else None
                uploader = runs[0].get('text', '') if runs and isinstance(runs[0], dict) else ''
                thumb = ''
                th = ppvr.get('thumbnail')
                if isinstance(th, dict):
                    thumbs = th.get('thumbnails') or []
                    if thumbs and isinstance(thumbs[-1], dict) and 'url' in thumbs[-1]:
                        thumb = thumbs[-1]['url']
                lt = ppvr.get('lengthText')
                dur_text = (self._runs_text(lt.get('runs')) or lt.get('simpleText', '')
                            if isinstance(lt, dict) else (lt or ''))
                out.append({
                    'url': '',
                    'webpage_url': f'https://www.youtube.com/watch?v={vid}',
                    'title': title,
                    'duration': self._parse_duration(dur_text),
                    'thumbnail': thumb,
                    'uploader': uploader,
                })
            except (KeyError, TypeError, IndexError):
                continue   # 單一畸形 item 不該拖垮整批
        return out

    def _send_request_locked(self, endpoint: str, body: dict) -> dict:
        """innertube 請求的線程安全包裝。

        跑在 thread pool，多 guild 並行時共用同一個 requests.Session
        (cookie jar / headers 是可變共享狀態，requests 不保證 thread-safe)。
        用 cog 級 threading.Lock 串行化所有 YTM 請求。
        """
        with self._ytm_lock:
            return self._ytm._send_request(endpoint, body)

    async def _get_autoplay_songs(
        self,
        webpage_url: str,
        history_titles: set[str],
        exclude: _AbstractSet[str] = frozenset(),
    ) -> list[dict]:
        """Autoplay 推薦：直接打 innertube 抓 Up-next radio 清單。
        不能用 ytmusicapi.get_watch_playlist()——YT Music 後端改版後它內部
        get_tab_browse_id 會 KeyError:'endpoint' 直接炸，那条路已經死了。

        `exclude` 是**硬排除**（/skipautoplay 剛跳的那首）——連 fallback 都不放行；
        `history_titles` 是軟過濾，fallback 時允許重放老歌。
        """
        match = re.search(r'(?:v=|youtu\.be/)([a-zA-Z0-9_-]{11})', webpage_url)
        if not match:
            return []
        video_id = match.group(1)
        excl = {t.lower().strip() for t in exclude}
        try:
            body = {
                "enablePersistentPlaylistPanel": True,
                "isAudioOnly": True,
                "tunerSettingValue": "AUTOMIX_SETTING_NORMAL",
                "videoId": video_id,
                "playlistId": "RDAMVM" + video_id,
                "params": "wAEB",  # radio
            }
            loop = asyncio.get_running_loop()
            data = await asyncio.wait_for(
                loop.run_in_executor(
                    None,
                    lambda: self._send_request_locked("next", body),
                ),
                timeout=25,
            )
            candidates = self._parse_innertube_radio(data, video_id)
            if not candidates:
                log.warning('Autoplay: innertube 回傳無可用推薦 (版式可能又變)')
                return []
            log.info(f'Autoplay 候選: {[c["title"] for c in candidates[:5]]}')
            # 硬排除：使用者明確跳過的
            candidates = [
                s for s in candidates
                if s['title'].lower().strip() not in excl
            ]
            if not candidates:
                return []   # 全是剛跳的，寧缺勿濫（寧可這次不接）
            # 軟過濾：歷史播過的，但 fallback 時允許重放（避免卡死）
            filtered = [s for s in candidates if s['title'].lower().strip() not in history_titles]
            result = filtered if filtered else candidates
            return result[:1]
        except Exception as e:
            log.error(f'Autoplay 取得推薦失敗: {e}')
            return []

    def _get_autoplay_lock(self, state: GuildMusicState) -> asyncio.Lock:
        # 單一 event loop 上 first-use 建鎖即原子(asyncio.Lock() 建構沒有 await)
        if state.autoplay_lock is None:
            state.autoplay_lock = asyncio.Lock()
        return state.autoplay_lock

    async def _prefetch_autoplay(
        self, guild_id: int, *, exclude_titles: _AbstractSet[str] = frozenset()
    ) -> Optional[dict]:
        """趁目前歌曲還在播，背景預載下一首 autoplay（含串流 URL）。
        推薦直接打 innertube 抓 Up-next radio，不需要二次搜尋。

        #3 併發保護: 整段(抓推薦→抓串流 URL→寫回 state)在 lock 下執行，
        避免 /skipautoplay 與播放結束觸發的 prefetch 同時抓兩首。
        #4 寫回前復核: await 期間使用者可能已 /stop 或切歌，避免留下幽靈曲。
        回傳成功設定的 prefetch song（讓 caller 不必再回讀 state，避開競態）。
        """
        state = self.get_state(guild_id)
        result: Optional[dict] = None
        async with self._get_autoplay_lock(state):
            if state.autoplay_prefetch:
                return  # 已有預載，不重複
            if not state.autoplay or not state.current:
                return  # 已關或由 /stop 清空
            seed_url = state.current['webpage_url']
            try:
                # 1. 從 YTMusic 取得推薦
                candidates = await self._get_autoplay_songs(
                    seed_url, state.history_titles, exclude=set(exclude_titles)
                )
                if not candidates:
                    return
                song = candidates[0]
                log.info(f'Autoplay 預載: {song["title"]}')

                # 2. 抓串流 URL
                song['url'] = await self.fetch_stream_url(song['webpage_url'])

                # 4. 寫回前復核狀態（await 期間可能已 /stop / 切歌 / 關閉）
                if (not state.autoplay or state.current is None
                        or state.current.get('webpage_url') != seed_url
                        or state.autoplay_prefetch is not None):
                    log.info('Autoplay 預載完成但狀態已變，丟棄: ' + song['title'])
                    return
                state.autoplay_prefetch = song
                result = song
                log.info(f'Autoplay 預載完成: {song["title"]}')
            except Exception as e:
                log.error(f'Autoplay 預載失敗: {e}')
        return result

    async def _play_next(self, guild_id: int, voice_client: discord.VoiceClient):
        if not voice_client.is_connected():
            return

        state = self.get_state(guild_id)
        # 捕捉 /stop token：本函式任何 await 後若它已 +1，代表 /stop 介入 → 放棄
        token = state.stop_token

        # Repeat 處理
        if state.current:
            # repeat one：用戶主動 skip 時繞過，直接播下一首
            if state.repeat == 'one' and not state.repeat_skip:
                song = dict(state.current)
                song['url'] = ''  # 重新抓串流 URL，避免過期
                try:
                    await self._ensure_stream_url(song)
                    # await 期間使用者可能 /stop（token+1）→ 別讓歌復活
                    if state.stop_token != token:
                        log.info('Repeat one: /stop 介入，放棄重播')
                        return
                    # 也可能已被 /play 起播 / 暫停中 → 別覆蓋
                    if voice_client.is_playing() or voice_client.is_paused():
                        log.info('Repeat one: voice 佔用中，跳過重播')
                        return
                    self._start_song(guild_id, voice_client, state, song)
                except Exception as e:
                    log.error(f'Repeat one 失敗，繼續下一首: {e}')
                    # fall through 到正常邏輯
                else:
                    state.repeat_skip = False
                    return
            # repeat all：不論 skip 與否都放回隊列尾部
            elif state.repeat == 'all':
                requeue = dict(state.current)
                requeue['url'] = ''  # 下次播到時再抓
                state.queue.append(requeue)

        state.repeat_skip = False

        # ── 決定下一首並播放（有上限 ≤3 輪，避免無界遞迴）──
        #
        # 把「补歌」和「起播」放同一個有界迴圈內，每輪：
        #   (a) 若 queue 空且 autoplay 開：先鎖內補一首（快路徑取 prefetch；
        #       慢路徑鎖內 fetch 後直接入隊）
        #   (b) pop 一首；stream URL 失敗 / voice 已被 /play 搶 → 記歷史+讓下一輪
        #       用新的 innertube 推薦（軟過濾避開）重來
        #   (c) 3 輪都起不來才清 current
        #
        # 這消除了先前版本「慢路徑 fetch 的 song 失敗後直接 return、但 current 仍
        # 指向舊歌」的永久靜音漏洞（Claude round-4 指出）——現在每輪都能補新推薦。
        for _attempt in range(3):
            # (a) 補歌——queue 空且 autoplay 開時
            if not state.queue and state.autoplay and state.current:
                async with self._get_autoplay_lock(state):
                    # /stop 可能在我們等鎖/上一輪 await 期間介入了 → 放棄，別寫回
                    if state.stop_token != token:
                        log.info('Autoplay: /stop 介入，放棄補歌')
                        return
                    if state.autoplay_prefetch:
                        log.info('Autoplay: 使用預載歌曲')
                        state.queue.append(state.autoplay_prefetch)
                        state.autoplay_prefetch = None
                    else:
                        # 持鎖 fetch 25s 不會卡死其它路徑（它們本就要等這把鎖來
                        # safe 地改 prefetch/queue），且 fetch 完在**同一把鎖內**
                        # 直接寫回，中間不可能被搶——沒有 check-then-act 縫隙。
                        seed_url = state.current['webpage_url']
                        log.info('Autoplay: 即時抓取推薦...')
                        new_songs = await self._get_autoplay_songs(
                            seed_url, state.history_titles
                        )
                        # 寫回前復核：fetch 期間 /stop / 切歌 / 已有 prefetch
                        if (state.stop_token == token and state.autoplay
                                and state.current is not None
                                and not state.queue and not state.autoplay_prefetch):
                            if new_songs:
                                state.queue.extend(new_songs)
                            else:
                                log.info('Autoplay: 這輪沒抓到推薦')
                        else:
                            log.info('Autoplay: fetch 期間狀態已變，這輪不接')

            # (b) 起播
            # pop 前先複核一次：若 (a) 的 fetch await 期間 /stop 介入了（token+1），
            # 此時 queue 是 stop 清空後使用者新排的——別 pop 彈掉使用者的歌。
            if state.stop_token != token:
                log.info('Autoplay: /stop 介入，放棄起播')
                return
            next_song = state.queue.pop(0) if state.queue else None
            if next_song is None:
                break  # 沒可播的（queue 空 + autoplay 也空/關 / /play 搶走）

            if not next_song.get('url'):
                try:
                    await self._ensure_stream_url(next_song)
                except Exception as e:
                    log.error(f'重新取得 URL 失敗，換一輪: {e}')
                    self._mark_failed(next_song, state)
                    continue  # 記歷史讓下輪挑到不同的，不無限自呼叫

            # /stop 可能在 _ensure_stream_url 的 await 期間介入 → 放棄
            if state.stop_token != token:
                log.info('Autoplay: /stop 介入，放棄起播')
                return

            # await 期間使用者可能 /play 搶著開播了——voice client 此刻正在放
            # → 別覆蓋它，退回隊首讓使用者的歌繼續播
            # (is_playing 在 paused 時回 False → 也檢查 is_paused)
            if voice_client.is_playing() or voice_client.is_paused():
                state.queue.insert(0, next_song)
                log.info('Autoplay: voice 已在播（/play 搶先），這首退回隊首')
                return

            try:
                self._start_song(guild_id, voice_client, state, next_song, prefetch_queued=True)
                return
            except Exception as e:
                log.error(f'播放失敗，換一輪: {e}')
                self._mark_failed(next_song, state)

        # (c) 3 輪都起不來 → 清 current。（is_playing/is_paused 保護：若 await
        # 期間 /play 已起播或使用者暫停中，就不該抹掉使用者的 current）
        if not (voice_client.is_playing() or voice_client.is_paused()):
            state.current = None
            log.info('Autoplay: 3 輪都起不來，停止')
        return

    def _mark_failed(self, song: dict, state: 'GuildMusicState') -> None:
        """失敗的候選記進歷史，避免下一輪又用 innertube 挑回同一首。"""
        t = song.get('title')
        if t:
            state.history_titles.add(t.lower().strip())
        if len(state.history_titles) > 400:
            # 保持有界（set 沒有順序，取一半砍掉）
            drop = set(list(state.history_titles)[:200])
            state.history_titles -= drop

    async def _prefetch_next(self, state: 'GuildMusicState'):
        if not state.queue:
            return
        next_song = state.queue[0]
        if next_song.get('url'):
            return
        try:
            next_song['url'] = await self.fetch_stream_url(next_song['webpage_url'])
            log.info(f'預載完成: {next_song["title"]}')
        except Exception as e:
            log.error(f'預載失敗: {e}')

    def fmt_duration(self, seconds) -> str:
        if not seconds:
            return '未知'
        m, s = divmod(int(seconds), 60)
        h, m = divmod(m, 60)
        return f'{h}:{m:02d}:{s:02d}' if h else f'{m}:{s:02d}'

    def song_embed(self, title: str, song: dict, color: discord.Color) -> discord.Embed:
        embed = discord.Embed(
            title=title,
            description=f'[{song["title"]}]({song["webpage_url"]})',
            color=color,
        )
        embed.add_field(name='長度', value=self.fmt_duration(song['duration']))
        if song['uploader']:
            embed.add_field(name='頻道', value=song['uploader'])
        if song['thumbnail']:
            embed.set_thumbnail(url=song['thumbnail'])
        return embed

    @app_commands.command(name='play', description='播放 YouTube / YouTube Music 音樂（網址或搜尋）')
    @app_commands.describe(query='YouTube 連結或歌曲名稱關鍵字')
    async def play(self, interaction: discord.Interaction, query: str):
        vc = await self._connect_user_voice(interaction)
        if vc is None:
            return
        state = self.get_state(interaction.guild_id)
        songs = await self._fetch_songs_or_reply(interaction, query)
        if songs is None:
            return

        log.info(f'取得音樂: {songs[0]["title"]} (共 {len(songs)} 首)')

        if vc.is_playing() or vc.is_paused():
            state.queue.extend(songs)
            if len(songs) > 1:
                embed = discord.Embed(
                    title='✅ 播放清單已加入隊列',
                    description=f'加入了 **{len(songs)}** 首歌曲',
                    color=discord.Color.green(),
                )
            else:
                embed = self.song_embed('✅ 已加入隊列', songs[0], discord.Color.green())
                embed.add_field(name='隊列位置', value=str(len(state.queue)))
            await interaction.followup.send(embed=embed)
        else:
            first, rest = songs[0], songs[1:]
            state.queue.extend(rest)

            # Fetch stream URL if not available (playlist flat items)
            if not first.get('url'):
                try:
                    await self._ensure_stream_url(first)
                except Exception as e:
                    await interaction.followup.send(f'❌ 無法取得串流：{e}')
                    return

            try:
                self._start_song(interaction.guild_id, vc, state, first)
            except Exception as e:
                log.error(f'播放失敗: {e}')
                await interaction.followup.send(f'❌ 播放失敗：{e}')
                return

            embed = self.song_embed('🎵 正在播放', first, discord.Color.blue())
            if rest:
                embed.set_footer(text=f'播放清單中還有 {len(rest)} 首歌曲已加入隊列')
            await interaction.followup.send(embed=embed)

    @app_commands.command(name='randomlist', description='播放清單但隨機打亂順序')
    @app_commands.describe(query='YouTube 播放清單連結或歌曲名稱')
    async def randomlist(self, interaction: discord.Interaction, query: str):
        vc = await self._connect_user_voice(interaction)
        if vc is None:
            return
        state = self.get_state(interaction.guild_id)
        songs = await self._fetch_songs_or_reply(interaction, query)
        if songs is None:
            return

        random.shuffle(songs)
        log.info(f'隨機清單: {len(songs)} 首')

        if vc.is_playing() or vc.is_paused():
            state.queue.extend(songs)
            embed = discord.Embed(
                title='🔀 已隨機加入隊列',
                description=f'打亂並加入了 **{len(songs)}** 首歌曲',
                color=discord.Color.green(),
            )
            await interaction.followup.send(embed=embed)
        else:
            first, rest = songs[0], songs[1:]
            state.queue.extend(rest)

            if not first.get('url'):
                try:
                    await self._ensure_stream_url(first)
                except Exception as e:
                    await interaction.followup.send(f'❌ 無法取得串流：{e}')
                    return

            try:
                self._start_song(interaction.guild_id, vc, state, first)
            except Exception as e:
                await interaction.followup.send(f'❌ 播放失敗：{e}')
                return

            embed = self.song_embed('🔀 隨機播放', first, discord.Color.blue())
            embed.set_footer(text=f'已隨機排列，還有 {len(rest)} 首在隊列中')
            await interaction.followup.send(embed=embed)

    @app_commands.command(name='uwu', description='隨機播放預設清單 (uwu)')
    async def uwu(self, interaction: discord.Interaction):
        UWU_PLAYLIST = 'https://music.youtube.com/playlist?list=PL73s0qFF67cncqYB3NbbfjhDH3XmlMIU_&si=vnDW4CX25FvU8o7S'
        await self.randomlist.callback(self, interaction, UWU_PLAYLIST)

    @app_commands.command(name='nextplay', description='插入歌曲到下一首播放')
    @app_commands.describe(query='YouTube 連結或歌曲名稱關鍵字')
    async def nextplay(self, interaction: discord.Interaction, query: str):
        vc = await self._connect_user_voice(interaction)
        if vc is None:
            return
        state = self.get_state(interaction.guild_id)
        songs = await self._fetch_songs_or_reply(interaction, query)
        if songs is None:
            return

        # 只取第一首插入到 queue 最前面
        song = songs[0]

        if vc.is_playing() or vc.is_paused():
            state.queue.insert(0, song)
            embed = self.song_embed('⏩ 插入為下一首', song, discord.Color.orange())
            await interaction.followup.send(embed=embed)
        else:
            # 沒在播就直接播
            if not song.get('url'):
                try:
                    await self._ensure_stream_url(song)
                except Exception as e:
                    await interaction.followup.send(f'❌ 無法取得串流：{e}')
                    return

            try:
                self._start_song(interaction.guild_id, vc, state, song)
            except Exception as e:
                await interaction.followup.send(f'❌ 播放失敗：{e}')
                return

            embed = self.song_embed('🎵 正在播放', song, discord.Color.blue())
            await interaction.followup.send(embed=embed)

    @app_commands.command(name='pause', description='暫停播放')
    async def pause(self, interaction: discord.Interaction):
        vc = interaction.guild.voice_client
        if vc and vc.is_playing():
            vc.pause()
            await interaction.response.send_message('⏸️ 已暫停')
        else:
            await interaction.response.send_message('❌ 目前沒有在播放', ephemeral=True)

    @app_commands.command(name='resume', description='繼續播放')
    async def resume(self, interaction: discord.Interaction):
        vc = interaction.guild.voice_client
        if vc and vc.is_paused():
            vc.resume()
            await interaction.response.send_message('▶️ 繼續播放')
        else:
            await interaction.response.send_message('❌ 目前沒有暫停', ephemeral=True)

    @app_commands.command(name='skip', description='跳過目前歌曲')
    async def skip(self, interaction: discord.Interaction):
        vc = interaction.guild.voice_client
        if vc and (vc.is_playing() or vc.is_paused()):
            state = self.get_state(interaction.guild_id)
            state.autoplay_prefetch = None
            state.repeat_skip = True  # 繞過 repeat one，強制播下一首
            vc.stop()
            await interaction.response.send_message('⏭️ 已跳過')
        else:
            await interaction.response.send_message('❌ 目前沒有在播放', ephemeral=True)

    @app_commands.command(name='skipautoplay', description='跳過 Autoplay 預載的下一首，重新抓一首推薦')
    async def skipautoplay(self, interaction: discord.Interaction):
        state = self.get_state(interaction.guild_id)

        if not state.autoplay:
            await interaction.response.send_message('❌ Autoplay 目前是關閉的', ephemeral=True)
            return
        if not state.current:
            await interaction.response.send_message('❌ 目前沒有在播放', ephemeral=True)
            return

        await interaction.response.defer()

        # #5: 把要跳過的那首抓出來當「硬排除」，讓新推薦不會又選回同一首
        async with self._get_autoplay_lock(state):
            old = state.autoplay_prefetch
            state.autoplay_prefetch = None
            excluded: set = set()
            if old and old.get('title'):
                excluded.add(old['title'].lower().strip())
            if old:
                if old.get('webpage_url'):
                    state.history.append(old['webpage_url'])
                    if len(state.history) > 20:
                        state.history.pop(0)
                if old.get('title'):
                    state.history_titles.add(old['title'].lower().strip())

        new = await self._prefetch_autoplay(interaction.guild_id, exclude_titles=excluded)

        if new:
            t = new['title']
            url = new.get('webpage_url', '')
            await interaction.followup.send(f'🔀 已換掉，Autoplay 下一首改為：\n**[{t}]({url})**')
        else:
            await interaction.followup.send('⚠️ 找不到新的推薦，queue 空了之後會再試一次')

    @app_commands.command(name='stop', description='停止播放並清空隊列（留在頻道）')
    async def stop(self, interaction: discord.Interaction):
        state = self.get_state(interaction.guild_id)
        vc = interaction.guild.voice_client

        state.queue.clear()
        state.current = None
        state.autoplay_prefetch = None
        state.stop_token += 1   # 讓 in-flight 的 _play_next 在 await 醒來後放棄

        if vc:
            vc.stop()

        await interaction.response.send_message('⏹️ 已停止並清空隊列')

    @app_commands.command(name='queue', description='查看播放隊列')
    async def show_queue(self, interaction: discord.Interaction):
        state = self.get_state(interaction.guild_id)
        embed = discord.Embed(title='🎵 播放隊列', color=discord.Color.purple())

        if state.current:
            t = state.current['title']
            t = t[:60] + '…' if len(t) > 60 else t
            embed.description = f'**🎶 正在播放**\n[{t}]({state.current["webpage_url"]}) `{self.fmt_duration(state.current["duration"])}`'

        if state.queue:
            lines = []
            total = 0
            for i, s in enumerate(state.queue[:20], 1):
                title = s['title'][:45] + '…' if len(s['title']) > 45 else s['title']
                line = f'`{i}.` {title} `{self.fmt_duration(s["duration"])}`'
                total += len(line) + 1
                if total > 3800:
                    lines.append(f'*... 還有更多首*')
                    break
                lines.append(line)
            if len(state.queue) > 20:
                lines.append(f'*... 還有 {len(state.queue) - 20} 首*')
            embed.description = (embed.description or '') + '\n\n**📋 待播清單**\n' + '\n'.join(lines)
        elif not state.current:
            embed.description = '隊列是空的，用 `/play` 來新增音樂！'

        # autoplay 預載提示（排在使用者 queue 之後）
        if state.autoplay_prefetch:
            t = state.autoplay_prefetch['title'][:45] + '…' if len(state.autoplay_prefetch['title']) > 45 else state.autoplay_prefetch['title']
            embed.description = (embed.description or '') + f'\n\n🔀 **Autoplay 下一首**\n{t}'

        await interaction.response.send_message(embed=embed)

    @app_commands.command(name='nowplaying', description='查看目前播放的歌曲')
    async def nowplaying(self, interaction: discord.Interaction):
        state = self.get_state(interaction.guild_id)
        if not state.current:
            await interaction.response.send_message('❌ 目前沒有在播放', ephemeral=True)
            return

        song = state.current
        embed = discord.Embed(
            title='🎵 正在播放',
            description=f'[{song["title"]}]({song["webpage_url"]})',
            color=discord.Color.blue(),
        )
        embed.add_field(name='長度', value=self.fmt_duration(song['duration']))
        if song['uploader']:
            embed.add_field(name='頻道', value=song['uploader'])
        if song['thumbnail']:
            embed.set_image(url=song['thumbnail'])

        await interaction.response.send_message(embed=embed)

    @app_commands.command(name='volume', description='調整音量 (0-100)')
    @app_commands.describe(level='音量大小 (0-100)')
    async def volume(self, interaction: discord.Interaction, level: int):
        if not 0 <= level <= 100:
            await interaction.response.send_message('❌ 音量必須在 0 到 100 之間', ephemeral=True)
            return

        state = self.get_state(interaction.guild_id)
        state.volume = level / 100

        vc = interaction.guild.voice_client
        if vc and vc.source and isinstance(vc.source, discord.PCMVolumeTransformer):
            vc.source.volume = state.volume

        await interaction.response.send_message(f'🔊 音量已設定為 **{level}%**')

    @app_commands.command(name='remove', description='從隊列移除歌曲（單首或範圍）')
    @app_commands.describe(start='要移除的位置（從 1 開始）', end='範圍結尾（不填就只移除單首）')
    async def remove(self, interaction: discord.Interaction, start: int, end: Optional[int] = None):
        state = self.get_state(interaction.guild_id)
        q = state.queue

        if not q:
            await interaction.response.send_message('❌ 隊列是空的', ephemeral=True)
            return

        end = end or start
        if start < 1 or end > len(q) or start > end:
            await interaction.response.send_message(
                f'❌ 範圍無效，隊列目前有 **{len(q)}** 首', ephemeral=True)
            return

        removed = q[start - 1:end]
        del q[start - 1:end]

        if len(removed) == 1:
            msg = f'🗑️ 已移除：**{removed[0]["title"]}**'
        else:
            msg = f'🗑️ 已移除第 {start} 到 {end} 首，共 **{len(removed)}** 首'

        await interaction.response.send_message(msg)

    @app_commands.command(name='repeat', description='切換循環模式（off → one → all → off）')
    async def repeat(self, interaction: discord.Interaction):
        vc = interaction.guild.voice_client
        state = self.get_state(interaction.guild_id)

        if not (vc and (vc.is_playing() or vc.is_paused())):
            await interaction.response.send_message(
                '❌ 目前沒有在播放音樂，請先用 `/play` 播放歌曲', ephemeral=True
            )
            return

        cycle = {'off': 'one', 'one': 'all', 'all': 'off'}
        state.repeat = cycle[state.repeat]

        labels = {'off': '❌ 關閉', 'one': '🔂 單曲循環', 'all': '🔁 全部循環'}
        await interaction.response.send_message(f'循環模式：**{labels[state.repeat]}**')

    @app_commands.command(name='autoplay', description='開啟/關閉自動播放（根據當前歌曲推薦）')
    async def autoplay(self, interaction: discord.Interaction):
        state = self.get_state(interaction.guild_id)
        state.autoplay = not state.autoplay
        status = '✅ 開啟' if state.autoplay else '❌ 關閉'
        await interaction.response.send_message(f'🔀 自動播放已 **{status}**')
        # 剛開啟時，如果歌正在播且 queue 空，立刻開始預載
        if state.autoplay and state.current and not state.queue and not state.autoplay_prefetch:
            asyncio.ensure_future(self._prefetch_autoplay(interaction.guild_id))

    @app_commands.command(name='info', description='顯示 Bot 目前的連線與播放狀態')
    async def info(self, interaction: discord.Interaction):
        state = self.get_state(interaction.guild_id)
        vc = interaction.guild.voice_client
        bot = interaction.client

        embed = discord.Embed(title='📊 Bot 狀態資訊', color=discord.Color.blurple())

        # ── Bot 本身 ──────────────────────────────────────
        ping = round(bot.latency * 1000)
        uptime_delta = datetime.now(timezone.utc) - bot.start_time
        total_s = int(uptime_delta.total_seconds())
        d, rem = divmod(total_s, 86400)
        h, rem = divmod(rem, 3600)
        m, s = divmod(rem, 60)
        uptime_str = (f'{d}d ' if d else '') + f'{h:02d}:{m:02d}:{s:02d}'

        embed.add_field(name='🏓 API 延遲', value=f'{ping} ms')
        embed.add_field(name='⏳ 上線時間', value=uptime_str)
        embed.add_field(name='🌐 連接伺服器', value=f'{len(bot.guilds)} 個')

        # ── 語音連線 ─────────────────────────────────────
        if vc and vc.is_connected():
            voice_ping = round(vc.average_latency * 1000) if vc.average_latency else '—'
            embed.add_field(name='🟢 語音頻道', value=vc.channel.name)
            embed.add_field(name='📶 語音延遲', value=f'{voice_ping} ms')
            # 伺服器支援的最高 bitrate
            max_br = interaction.guild.bitrate_limit // 1000
            embed.add_field(name='🎛️ 伺服器最高 Bitrate', value=f'{max_br} kbps')
        else:
            embed.add_field(name='🔴 語音頻道', value='未連線', inline=False)

        # ── 音訊設定 ─────────────────────────────────────
        embed.add_field(name='🎚️ 輸出 Bitrate', value='128 kbps')
        embed.add_field(name='🔊 目前音量', value=f'{round(state.volume * 100)}%')

        # ── 播放狀態 ─────────────────────────────────────
        if vc and vc.is_playing():
            play_status = f'🎵 播放中'
        elif vc and vc.is_paused():
            play_status = '⏸️ 暫停中'
        else:
            play_status = '⏹️ 閒置'
        embed.add_field(name='狀態', value=play_status)

        if state.current:
            title = state.current['title']
            title = title[:40] + '…' if len(title) > 40 else title
            embed.add_field(name='🎶 目前歌曲', value=title, inline=False)

        # ── Queue 資訊 ───────────────────────────────────
        q_count = len(state.queue)
        q_duration = sum(s.get('duration') or 0 for s in state.queue)
        q_dur_str = self.fmt_duration(q_duration) if q_duration else '—'
        embed.add_field(name='📋 Queue 歌曲數', value=f'{q_count} 首')
        embed.add_field(name='⏱️ Queue 剩餘時長', value=q_dur_str)
        embed.add_field(name='🔀 Autoplay', value='開啟 ✅' if state.autoplay else '關閉 ❌')
        repeat_labels = {'off': '關閉 ❌', 'one': '單曲循環 🔂', 'all': '全部循環 🔁'}
        embed.add_field(name='🔁 Repeat', value=repeat_labels[state.repeat])

        embed.set_footer(text=f'查詢時間：{datetime.now(timezone.utc).strftime("%H:%M:%S")} UTC')
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name='disconnect', description='讓 Bot 離開語音頻道')
    async def disconnect(self, interaction: discord.Interaction):
        vc = interaction.guild.voice_client
        if vc:
            state = self.get_state(interaction.guild_id)
            state.queue.clear()
            state.current = None
            state.autoplay_prefetch = None
            state.want_voice = False
            state.voice_channel_id = None
            if interaction.guild_id is not None:
                self._manual_discard.add(interaction.guild_id)  # 別讓 watchdog 又把她拉回來
            state.history.clear()
            state.history_titles.clear()
            vc.stop()
            await vc.disconnect()
            await interaction.response.send_message('👋 已離開語音頻道')
        else:
            await interaction.response.send_message('❌ Bot 不在語音頻道中', ephemeral=True)

    # ── Voice watchdog：斷線自動重連 ──────────────────────────
    WATCHDOG_MAX_ATTEMPTS = 10
    WATCHDOG_BASE_DELAY = 10.0     # 秒
    WATCHDOG_MAX_DELAY = 250.0     # 秒(約 4 分鐘)

    def _start_watchdog(self):
        if self._watchdog_task is None:
            self._watchdog_task = asyncio.ensure_future(self._watchdog_loop())
            log.info('🔁 Voice watchdog 已啟動')

    async def _watchdog_loop(self):
        """每 3 秒檢查一次:還想播,但語音掉線 → 指數退避重連。"""
        attempts: dict = {}
        while True:
            await asyncio.sleep(3)
            for guild_id in list(self._states.keys()):
                state = self._states.get(guild_id)
                if state is None or not state.want_voice:
                    continue
                if guild_id in self._manual_discard:
                    continue
                guild = self.bot.get_guild(guild_id)
                if guild is None:
                    continue
                if guild.voice_client is not None and guild.voice_client.is_connected():
                    attempts[guild_id] = 0
                    continue
                # 掉線且沒目標頻道 → 無從重連,放棄
                if state.voice_channel_id is None:
                    continue
                channel = self.bot.get_channel(state.voice_channel_id)
                if channel is None or not isinstance(channel, discord.VoiceChannel):
                    continue
                n = attempts.get(guild_id, 0)
                if n >= self.WATCHDOG_MAX_ATTEMPTS:
                    continue
                attempts[guild_id] = n + 1
                delay = min(self.WATCHDOG_BASE_DELAY * (2 ** n), self.WATCHDOG_MAX_DELAY)
                log.warning(
                    f'🔁 Voice 斷線 [{guild.name}] attempt {n + 1}/{self.WATCHDOG_MAX_ATTEMPTS}, '
                    f'{delay:.0f}s 後重連'
                )
                await asyncio.sleep(delay)
                try:
                    # 斷點續播:重連前記下目前這曲已播的秒數
                    resume_at = 0.0
                    cur = state.current
                    if cur is not None and state.current_started_mono > 0:
                        resume_at = max(0.0, time.monotonic() - state.current_started_mono)
                        if (cur.get('duration') and cur['duration'] > 0 and
                                resume_at >= cur['duration'] - 3):
                            resume_at = 0.0   # 已播到快結束,直接換下一首
                    old = guild.voice_client
                    if old is not None:
                        try:
                            old.stop()
                        except Exception:
                            pass
                        try:
                            await old.disconnect()
                        except Exception:
                            pass
                    client = await channel.connect()
                    log.info(f'✅ Voice 重連成功 [{guild.name}] → {channel.name}')
                    attempts[guild_id] = 0
                    # 重連後重新掛上當前/下一首歌
                    state = self.get_state(guild_id)
                    if state.current is not None and resume_at > 0.5:
                        song = dict(state.current)
                        song['url'] = ''  # 舊串流 URL 多半已失效,重新抓
                        try:
                            await self._ensure_stream_url(song)
                        except Exception as e:
                            log.error(f'重連後重取 URL 失敗,改播下一首: {e}')
                            song = None
                        if song is not None:
                            self._start_song(guild_id, client, state, song, seek=resume_at)
                            continue
                    if state.queue:
                        await self._play_next(guild_id, client)
                except Exception as e:
                    log.error(f'❌ Voice 重連失敗 [{guild.name}]: {e}')


async def setup(bot: commands.Bot):
    cog = MusicCog(bot)
    # 初始化 watchdog 專屬欄位
    cog._manual_discard = set()
    cog._watchdog_task = None
    await bot.add_cog(cog)
    cog._start_watchdog()
