"""/valo：Valorant 戰績(HenrikDev 非官方 API，HENRIK_API_KEY)。

  /valo player:名字#tag       牌位、RR、最近 5 場；下拉選單看單場完整計分板
  /valo-track add/remove/list  追蹤玩家，打完一場自動把結果發到指定頻道
只用賽後的公開資料，不碰遊戲中的即時資訊。
額度：key 是 30 次/分鐘，這裡用九成，背景追蹤只在剩餘額度夠多時才查(互動指令優先)。
中文名稱與圖示來自 valorant-api.com(免費、不需要 key)。
"""
import asyncio
import io
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import quote

import aiohttp
import discord
from aiohttp import web
from discord import app_commands
from discord.ext import commands

from cogs.riot_api import RateLimiter
from cogs.valo_live import MODE_RECENT, RiotLink, shard_from_env
from cogs.valo_live_parse import eog_table, henrik_to_riot, mode_record, parse_match, rank_of, recent_line
from cogs.valo_parse import detail, mmr_summary, summarize

log = logging.getLogger(__name__)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRACK_FILE = os.path.join(ROOT, 'data', 'valo_tracked.json')
CONTENT_FILE = os.path.join(ROOT, 'data', 'valo_content.json')
HENRIK = 'https://api.henrikdev.xyz'
VAPI = 'https://valorant-api.com/v1'
TRACK_EVERY = 60          # 秒：背景每輪查一位被追蹤的玩家
BG_MIN_REMAINING = 12     # 剩餘額度少於這個就跳過背景查詢，留給互動指令
MAX_TRACKED = 15
ASSET_DIR = os.path.join(ROOT, 'data', 'valo_assets')
ICON_PX = 160             # 特務/牌位圖示快取的邊長(原圖 1024，畫面最大約 64px，留 2x 給高 DPI)
LIVE_POLL_IDLE, LIVE_POLL_MATCH, LIVE_POLL_OFFLINE = 10, 5, 60   # 秒：/valo-live 畫面輪詢 GLZ 的間隔
EOG_TRIES = 12            # 對局結束後賽後資料可能要等一下，最多試幾次
COLOR_WIN, COLOR_LOSS, COLOR_DRAW = 0x3BA55D, 0xED4245, 0x99AAB5
RID_RE = re.compile(r'^\s*([^#]{1,32})#([^#\s]{1,8})\s*$')


class HenrikApi:
    def __init__(self, key: str, http: aiohttp.ClientSession):
        self.key = key
        self.http = http
        self.rl = RateLimiter([(30, 60)])
        self.remaining = 30
        self.reset_at = 0.0
        self._cache: dict[str, tuple[float, object]] = {}

    async def get(self, path: str, ttl: float, background: bool = False):
        hit = self._cache.get(path)
        if hit and time.time() < hit[0]:
            return hit[1]
        if background and self.remaining < BG_MIN_REMAINING and time.monotonic() < self.reset_at:
            return None
        await self.rl.acquire()
        try:
            async with self.http.get(HENRIK + path, headers={'Authorization': self.key},
                                     timeout=aiohttp.ClientTimeout(total=25)) as r:
                rem, reset = r.headers.get('x-ratelimit-remaining'), r.headers.get('x-ratelimit-reset')
                if rem and rem.isdigit():
                    self.remaining = int(rem)
                if reset and reset.isdigit():
                    self.reset_at = time.monotonic() + int(reset)
                if r.status == 429 or self.remaining == 0:
                    self.rl.blocked_until = time.monotonic() + (int(reset) if reset and reset.isdigit() else 60)
                if r.status != 200:
                    if r.status == 404:
                        self._cache[path] = (time.time() + 120, None)
                    else:
                        log.warning(f'🔫 HenrikDev {r.status} {path.split("?")[0]}')
                    return None
                data = (await r.json(content_type=None)).get('data')
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as e:
            log.warning(f'🔫 HenrikDev 失敗 {path.split("?")[0]}: {e!r}')
            return None
        self._cache[path] = (time.time() + ttl, data)
        if len(self._cache) > 500:
            now = time.time()
            for k in [k for k, (exp, _) in self._cache.items() if exp < now]:
                self._cache.pop(k, None)
        return data

    async def account(self, name: str, tag: str, background=False):
        return await self.get(f'/valorant/v2/account/{quote(name, safe="")}/{quote(tag, safe="")}', 86400, background)

    async def mmr(self, region: str, name: str, tag: str, background=False):
        return await self.get(f'/valorant/v3/mmr/{region}/pc/{quote(name, safe="")}/{quote(tag, safe="")}', 120,
                              background)

    async def matches(self, region: str, name: str, tag: str, size: int = 5, background=False, ttl: float = 120):
        return await self.get(f'/valorant/v4/matches/{region}/pc/{quote(name, safe="")}/{quote(tag, safe="")}'
                              f'?size={int(size)}', ttl, background)

    async def match(self, region: str, match_id: str):
        return await self.get(f'/valorant/v4/match/{region}/{quote(match_id, safe="")}', 86400)

    def remember_match(self, region: str, m: dict):
        """查列表時拿到的完整對戰順便存起來，點詳細時不用再打 API。"""
        mid = (m.get('metadata') or {}).get('match_id')
        if mid:
            self._cache[f'/valorant/v4/match/{region}/{quote(mid, safe="")}'] = (time.time() + 86400, m)


def shrink_png(data: bytes, px: int) -> bytes:
    """把圖示縮到 px×px 以內的 PNG；壞圖或已經夠小就原樣回傳。"""
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(data))
        if max(im.size) <= px:
            return data
        im = im.convert('RGBA')
        im.thumbnail((px, px), Image.LANCZOS)
        out = io.BytesIO()
        im.save(out, 'PNG', optimize=True)
        return out.getvalue()
    except Exception as e:
        log.warning(f'🔫 圖示縮圖失敗，存原圖: {e!r}')
        return data


async def load_content(http: aiohttp.ClientSession) -> dict:
    """特務、地圖、牌位的中文名稱與圖示。下載失敗用上次的快取。"""
    try:
        async def j(path):
            async with http.get(f'{VAPI}/{path}', timeout=aiohttp.ClientTimeout(total=20)) as r:
                r.raise_for_status()
                return (await r.json())['data']
        agents, maps, tiers, weapons, ceremonies = await asyncio.gather(
            j('agents?language=zh-TW'), j('maps?language=zh-TW'),
            j('competitivetiers?language=zh-TW'), j('weapons?language=zh-TW'), j('ceremonies?language=zh-TW'))
        # 不篩 isPlayableCharacter：KAY/O 在 valorant-api 有兩個 uuid，遊戲用的那個(773f0c78…)被標成不可玩，
        # 篩掉就查不到名字跟圖。沒圖的重複項借用同名可玩特務的圖。
        icon_by_name = {a['displayName']: a['displayIcon'] for a in agents
                        if a.get('isPlayableCharacter') and a.get('displayIcon')}
        c = {
            'agents': {a['uuid']: {'name': a['displayName'],
                                   'icon': a.get('displayIcon') or icon_by_name.get(a['displayName'])}
                       for a in agents},
            'maps': {m['uuid']: m['displayName'] for m in maps},
            'mapPaths': {m['mapUrl']: m['displayName'] for m in maps if m.get('mapUrl')},
            'mapIcons': {m['uuid']: m.get('listViewIcon') for m in maps},
            'tiers': {str(t['tier']): {'name': t['tierName'], 'icon': t.get('largeIcon') or t.get('smallIcon')}
                      for t in tiers[-1]['tiers']},
            # 結算：擊殺用的武器、回合 Ceremony(Clutch/Ace/…)名稱。key 都小寫，match-details 的 uuid 大小寫不一
            'weapons': {w['uuid'].lower(): w['displayName'] for w in weapons},
            'ceremonies': {('ceremony' + c.get('assetPath', '').rsplit('/', 1)[-1].replace('Ceremony_PrimaryAsset', '')).lower():
                           c['displayName'] for c in ceremonies},
        }
        with open(CONTENT_FILE, 'w', encoding='utf-8') as f:
            json.dump(c, f, ensure_ascii=False)
        return c
    except Exception as e:
        log.warning(f'🔫 valorant-api.com 下載失敗，用快取: {e!r}')
        try:
            with open(CONTENT_FILE, encoding='utf-8') as f:
                return json.load(f)
        except (OSError, ValueError):
            return {'agents': {}, 'maps': {}, 'mapIcons': {}, 'tiers': {}}


def add_lines(emb: discord.Embed, name: str, lines: list[str]):
    """embed 欄位上限 1024 字 → 太長就拆成好幾欄(後面的欄名留空)。"""
    chunk, first = [], True
    for ln in lines + [None]:
        if ln is None or len('\n'.join(chunk + [ln])) > 1024:
            if chunk:
                emb.add_field(name=name if first else '\u200b', value='\n'.join(chunk), inline=False)
                first = False
            chunk = []
        if ln is not None:
            chunk.append(ln[:1024])


def parse_rid(text: str) -> Optional[tuple[str, str]]:
    m = RID_RE.match(text or '')
    return (m.group(1).strip(), m.group(2).strip()) if m else None


def _esc(s) -> str:
    return discord.utils.escape_markdown(str(s or ''))


class ValoCog(commands.Cog):
    track = app_commands.Group(name='valo-track', description='追蹤 Valorant 玩家，打完自動發戰績')

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.api: Optional[HenrikApi] = None
        self.content: dict = {'agents': {}, 'maps': {}, 'mapIcons': {}, 'tiers': {}}
        self._http: Optional[aiohttp.ClientSession] = None
        self._task: Optional[asyncio.Task] = None
        self.tracked: dict[str, dict] = self._load_tracked()
        self.link: Optional[RiotLink] = None
        # Activity 即時畫面(valo.js)：state 每變一次 version +1，WebSocket 據此推送
        self.version = 0
        self._changed = asyncio.Event()
        self.state: dict = {'phase': 'offline', 'sub': '啟動中', 'me': '', 'match': None,
                            'recents': {}, 'modes': {}, 'eog': None, 'score': None}
        self._live_task: Optional[asyncio.Task] = None
        self._live_mid = ''                        # 目前對局 id
        self._live_hidden: set = set()
        self._lookup_cache: dict[str, list] = {}   # puuid → [name, mmr, recent(近 5 場), mode(當前模式近 10 場)]，None = 還沒查到
        self._eog_tries = 0

    async def cog_load(self):
        self._http = aiohttp.ClientSession(headers={'User-Agent': 'heart-discordbot-valo/1.0'})
        key = os.getenv('HENRIK_API_KEY', '').strip()
        if key:
            self.api = HenrikApi(key, self._http)
        self.content = await load_content(self._http)
        self.link = RiotLink(os.getenv('LOL_SSH_HOST', 'pc'), shard_from_env(), self._http)
        self._task = asyncio.ensure_future(self._track_loop())
        self._live_task = asyncio.ensure_future(self._live_loop())
        log.info(f'🔫 Valorant 模組：API {"已啟用" if self.api else "未設定 HENRIK_API_KEY"}，'
                 f'追蹤 {len(self.tracked)} 人')

    async def cog_unload(self):
        if self._task:
            self._task.cancel()
        if self._live_task:
            self._live_task.cancel()
        if self._http:
            await self._http.close()

    # ── 追蹤名單 ──
    @staticmethod
    def _load_tracked() -> dict:
        try:
            with open(TRACK_FILE, encoding='utf-8') as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _save_tracked(self):
        with open(TRACK_FILE + '.tmp', 'w', encoding='utf-8') as f:
            json.dump(self.tracked, f, ensure_ascii=False, indent=1)
        os.replace(TRACK_FILE + '.tmp', TRACK_FILE)

    # ── Activity 即時畫面：輪詢 ──
    def _set(self, **kw):
        changed = False
        for k, v in kw.items():
            if self.state.get(k) != v:
                self.state[k] = v
                changed = True
        if changed:
            self.version += 1
            ev, self._changed = self._changed, asyncio.Event()
            ev.set()

    async def _live_loop(self):
        await self.bot.wait_until_ready()
        while True:
            try:
                delay = await self._live_tick()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning(f'🔫 對局輪詢例外: {e!r}')
                delay = LIVE_POLL_OFFLINE
            await asyncio.sleep(delay)

    def _cached(self, puuids: list[str]) -> tuple[dict, dict, dict, dict]:
        c = self._lookup_cache
        return ({p: c[p][0] for p in puuids if p in c}, {p: c[p][1] for p in puuids if p in c},
                {p: c[p][2] for p in puuids if p in c and c[p][2] is not None},
                {p: c[p][3] for p in puuids if p in c and c[p][3] is not None})

    async def _lookup_players(self, puuids: list[str], queue: str, push) -> tuple[dict, dict, dict, dict]:
        """同一場裡查過的人不重查。三階段各 push 一次：名字/牌位(1 秒) → 近 5 場(限速，約 20 秒) → 當前模式近 10 場。"""
        c = self._lookup_cache
        new = [p for p in puuids if p not in c]
        if new:
            names, mmrs, _ = await self.link.lookup_basic(new)
            for p in new:
                c[p] = [names.get(p) or {}, mmrs.get(p), None, None]
            push(*self._cached(puuids))
        todo = [p for p in puuids if c[p][2] is None]      # 還沒有、或上次沒查完整(429)
        if todo:
            recents = await self.link.lookup_recent(todo, self.content)
            for p in todo:
                c[p][2] = recents.get(p)
            push(*self._cached(puuids))
        todo = [p for p in puuids if c[p][3] is None]
        if todo and queue:
            modes = await self.link.lookup_recent(todo, self.content, MODE_RECENT, queue)
            for p in todo:
                c[p][3] = mode_record(modes[p], queue) if modes.get(p) is not None else None
        return self._cached(puuids)

    async def _live_tick(self) -> float:
        if not self.link or not await self.link.token():
            ok = await self.link.pc._run('echo ok', timeout=6) if self.link else None
            self._set(phase='offline', sub='PC 離線' if not ok else 'Riot Client 未開啟', match=None)
            return LIVE_POLL_OFFLINE
        me = self.link.puuid
        cur = await self.link.current_match()
        if not cur:
            if self._live_mid and self.state.get('phase') == 'coregame':
                # 剛打完：抓賽後計分板(資料可能要等幾秒才有)
                self._eog_tries += 1
                d = await self.link.pd(f'/match-details/v1/matches/{self._live_mid}')
                if d and (d.get('matchInfo') or {}).get('isCompleted'):
                    pu = [p['subject'] for p in d.get('players') or [] if p.get('subject')]
                    names = {p: c[0] for p, c in self._lookup_cache.items() if p in pu}
                    missing = [p for p in pu if p not in names]
                    if missing:
                        names.update(await self.link.names(missing))
                    rr = {}
                    if ((d.get('matchInfo') or {}).get('queueID') or '').lower() == 'competitive':
                        rr = await self.link.rr_changes(pu, self._live_mid)
                    # 牌位沿用對局中查到的 mmr(match-details 非競技模式的 competitiveTier 都是 0)
                    season = await self.link.active_season()
                    tiers = {p: rank_of(c[1], season, self.content)['tier']
                             for p, c in self._lookup_cache.items() if c and c[1]}
                    self.state['_eog_at'] = time.time()
                    self._set(eog=eog_table(d, me, self._live_hidden, self.content, names, rr, tiers))
                    asyncio.ensure_future(self._track_now(me, self._live_mid))   # 有被追蹤就馬上去查、不等輪到
                elif self._eog_tries < EOG_TRIES:
                    return LIVE_POLL_MATCH
            self._live_mid, self._live_hidden, self._eog_tries = '', set(), 0
            self._lookup_cache.clear()
            self._set(phase='eog' if self.state.get('eog') else 'idle', sub='待機中', me=me, match=None, recents={}, modes={}, score=None)
            return LIVE_POLL_IDLE
        kind, m = cur
        mid = m.get('MatchID') or m.get('ID') or ''
        if mid != self._live_mid:
            self._live_mid, self._live_hidden, self._eog_tries = mid, set(), 0
            self._lookup_cache.clear()
            self.state['eog'] = None
        pl = m.get('Players') if kind == 'coregame' else (m.get('AllyTeam') or {}).get('Players')
        puuids = [p['Subject'] for p in pl or [] if p.get('Subject')]
        self._live_hidden |= {p['Subject'] for p in pl or [] if (p.get('PlayerIdentity') or {}).get('Incognito')}
        season = await self.link.active_season()

        def push(names, mmrs, recents, modes):
            self._set(phase=kind, sub='選角中' if kind == 'pregame' else '對局中', me=me,
                      match=parse_match(kind, m, names, mmrs, season, self.content, me), recents=recents, modes=modes)
        push(*self._cached(puuids))
        queue = (self.state['match'] or {}).get('queueId') or ''
        push(*await self._lookup_players(puuids, queue, push))
        if kind == 'coregame':
            self._set(score=await self.link.score())
        return LIVE_POLL_MATCH

    def public_state(self) -> dict:
        return {k: v for k, v in self.state.items() if not k.startswith('_')}

    # ── HTTP(由 LyricsCog 的伺服器轉進來：/valo/...) ──
    async def handle(self, request: web.Request, tail: str):
        if tail == 'ws':
            return await self._h_ws(request)
        if tail == 'state':
            return web.json_response(self.public_state())
        m = re.match(r'^img/(agent|tier)/([a-z0-9\-]+)\.png$', tail)
        if m:
            path = await self._asset(m.group(1), m.group(2))
            if not path:
                raise web.HTTPNotFound()
            return web.FileResponse(path, headers={'Cache-Control': 'max-age=86400'})
        raise web.HTTPNotFound()

    async def _asset(self, kind: str, key: str) -> Optional[str]:
        """特務 / 牌位圖示：第一次從 valorant-api.com 抓下來存 data/valo_assets/(Discord CSP 不給載外站圖)。"""
        path = os.path.join(ASSET_DIR, kind, f'{key}.png')
        if os.path.exists(path):
            return path
        url = (self.content['agents'].get(key) or {}).get('icon') if kind == 'agent' else self._tier_icon(key)
        if not url or not url.startswith('https://media.valorant-api.com/'):
            return None
        try:
            async with self._http.get(url, timeout=aiohttp.ClientTimeout(total=20)) as r:
                if r.status != 200:
                    return None
                data = await r.read()
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return None
        os.makedirs(os.path.dirname(path), exist_ok=True)
        # 原圖 1024×1024、每張 300–460KB，10 人就 4MB，經 tunnel + Discord 代理很慢；縮到畫面用得到的大小。
        data = await asyncio.to_thread(shrink_png, data, ICON_PX)
        with open(path + '.tmp', 'wb') as f:
            f.write(data)
        os.replace(path + '.tmp', path)
        return path

    async def _h_ws(self, request):
        ws = web.WebSocketResponse(heartbeat=20)
        await ws.prepare(request)
        reader = asyncio.ensure_future(self._drain(ws))
        sent = -1
        try:
            while not ws.closed:
                ev = self._changed
                if self.version != sent:
                    sent = self.version
                    await ws.send_json({'type': 'state', **self.public_state()})
                    continue
                try:
                    await asyncio.wait_for(ev.wait(), 10)
                except asyncio.TimeoutError:
                    pass
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        except Exception as e:
            log.warning(f'🔫 WebSocket 例外: {e!r}')
        finally:
            reader.cancel()
        return ws

    @staticmethod
    async def _drain(ws: web.WebSocketResponse):
        async for _ in ws:
            pass

    # ── embed ──
    def _tier_icon(self, tid) -> Optional[str]:
        return (self.content['tiers'].get(str(tid)) or {}).get('icon')

    def _match_line(self, s: dict) -> str:
        if s.get('ffa'):
            res = '🏆' if s['won'] else '⚔️'
        else:
            res = '⬜ 平' if s['draw'] else ('🟦 勝' if s['won'] else ('🟥 敗' if s['won'] is False else '⬛'))
        when = discord.utils.format_dt(s['started'], 'R') if s['started'] else ''
        mvp = ' ⭐MVP' if s['mvp'] else ''
        acs = f"ACS {s['acs']}　" if s.get('acs') is not None else ''
        return (f"{res} **{_esc(s['agent'])}**　{s['k']}/{s['d']}/{s['a']}　{s['score']}　"
                f"{_esc(s['map'])}・{s['queue']}　{acs}爆頭 {s['hs']}%{mvp}　{when}")

    def _profile_embed(self, acc: dict, mmr: Optional[dict], sums: list[dict]) -> discord.Embed:
        ms = mmr_summary(mmr, self.content) if mmr else None
        emb = discord.Embed(title=f"{_esc(acc.get('name'))}#{_esc(acc.get('tag'))}", color=0xFF4655,
                            description=f"帳號等級 {acc.get('account_level', '?')}・伺服器 {(acc.get('region') or '?').upper()}")
        if ms:
            change = ms['lastChange']
            ch = f"（上一場 {'+' if change and change > 0 else ''}{change}）" if change is not None else ''
            cur = ms['current']['name'] if ms['current']['id'] else '未定級'
            rank = f"**{_esc(cur)}**　{ms['rr']} RR{ch}" if ms['current']['id'] else '未定級'
            if ms['needed']:
                rank += f"\n還要 {ms['needed']} 場定級"
            emb.add_field(name='目前牌位', value=rank, inline=True)
            if ms['peak']['id']:
                emb.add_field(name='歷史最高', value=f"{_esc(ms['peak']['name'])}（{ms['peakSeason']}）", inline=True)
            icon = self._tier_icon(ms['current']['id'])
            if icon:
                emb.set_thumbnail(url=icon)
        if sums:
            w = sum(1 for s in sums if s['won'] and not s.get('ffa'))
            emb.add_field(name=f'最近 {len(sums)} 場（{w} 勝）', inline=False,
                          value='\n'.join(self._match_line(s) for s in sums)[:1024])
            # 平均只算有回合的對戰(死鬥的 ACS 沒意義)
            std = [s for s in sums if s.get('acs') is not None]
            if std:
                kd = sum(s['k'] for s in std) / max(1, sum(s['d'] for s in std))
                emb.set_footer(text=f"近 {len(std)} 場(不含死鬥類)平均 ACS {sum(s['acs'] for s in std) // len(std)}・"
                                    f"K/D {kd:.2f}・爆頭 {sum(s['hs'] for s in std) // len(std)}%・資料來源 HenrikDev")
            else:
                emb.set_footer(text='資料來源 HenrikDev')
        else:
            emb.add_field(name='最近對戰', value='沒有資料', inline=False)
        return emb

    def _rich_embed(self, e: dict, title: str) -> discord.Embed:
        """賽後完整計分板(eog_table 的輸出)：valo-track 貼文與 /valo 單場詳細共用。"""
        mine = next((t for t in e['teams'] if t['mine']), None)
        won = mine['won'] if mine else None
        color = COLOR_DRAW if won is None else (COLOR_WIN if won else COLOR_LOSS)
        two = len(e['teams']) == 2
        if two and len(e.get('rounds') or []) > 1:
            score = f"　{e['teams'][0]['rounds']}:{e['teams'][1]['rounds']}"
        elif two and e['teams'][0].get('points') is not None:
            score = f"　{e['teams'][0]['points']}:{e['teams'][1]['points']}"
        else:
            score = ''
        emb = discord.Embed(title=f"{title}{score}　{e['queue']}・{_esc(e['map'])}", color=color)
        meta = [f"{e['length'] // 60} 分 {e['length'] % 60:02d} 秒"]
        if e.get('start'):
            meta.append(discord.utils.format_dt(datetime.fromtimestamp(e['start'], tz=timezone.utc), 'R'))
        if e.get('server'):
            meta.append(f"伺服器 {e['server']}")
        if e.get('version'):
            meta.append(f"版本 {e['version']}")
        if e.get('early'):
            meta.append('提前結束')
        emb.description = '・'.join(meta)
        rb = any(p['rounds'] > 1 for t in e['teams'] for p in t['players'])
        names = {p['puuid']: p for t in e['teams'] for p in t['players']}

        def who(p):
            if p['hidden']:
                return '🙈 隱藏名字'
            return f"{_esc(p['name'])}#{_esc(p['tag'])}" if p['name'] else '?'

        for t in e['teams']:
            head = ('我方' if t['mine'] else '敵方') if two else '全部玩家'
            if t['won'] is not None and two:
                head += f"　{'勝' if t['won'] else '敗'}"
            lines = []
            for p in t['players']:
                tags = ''.join(x for x in ((f" [組隊{p['party']}]" if p.get('party') else ''), (' ⭐MVP' if p['mvp'] else ''),
                                           (' 🎮' if p.get('platform') and p['platform'] != 'pc' else ''),
                                           (f" 💤AFK {p['afk']}回" if p.get('afk') else '')) if x)
                l1 = (f"{'▶ ' if p['me'] else ''}**{_esc(p['agent'])}** {who(p)}{tags}　{p['k']}/{p['d']}/{p['a']}　"
                      + (f"ACS {round(p['score'] / p['rounds'])}・ADR {round(p['dmg'] / p['rounds'])}" if rb
                         else f"分數 {p['score']}・傷害 {p['dmg']}")
                      + f"・HS {p['hs']}%" + (f"・KAST {p['kast']}%" if p.get('kast') is not None else ''))
                bits = []
                if p.get('mk') is not None:
                    bits.append(f"首殺 {p['fb']}/被首殺 {p['fd']}")
                    mk = [f"{n}{'K' if n < 5 else 'K(ACE)'}×{c}" for n, c in zip((3, 4, 5), p['mk']) if c]
                    if mk:
                        bits.append(' '.join(mk))
                    if p['clutch']:
                        bits.append('Clutch ' + ','.join(p['clutch']))
                if p.get('econ'):
                    bits.append(f"裝備均 {p['econ'] / 1000:.1f}k" + (f" {p['weapon']}" if p.get('weapon') else ''))
                elif p.get('weapon'):
                    bits.append(f"主武器 {p['weapon']}")
                if any(p.get('casts') or []):
                    bits.append('技能 ' + '/'.join(str(c) for c in p['casts']))
                if p.get('plants') or p.get('defuses'):
                    bits.append(f"下包 {p['plants']}/拆包 {p['defuses']}")
                rank = (self.content['tiers'].get(str(p['tier'])) or {}).get('name') if p.get('tier') else ''
                rr = p.get('rr') or {}
                if rank or rr:
                    r = rank or ''
                    if rr.get('earned') is not None and (rr or {}).get('rrAfter') is not None:
                        after = (self.content['tiers'].get(str(rr.get('after'))) or {}).get('name') or rank
                        r = f"{after} {rr['rrAfter']} RR（{rr['earned']:+d}）"
                    elif rr.get('earned') is not None:
                        r = f"{r}（{rr['earned']:+d} RR）"
                    bits.append(r)
                if p.get('level'):
                    bits.append(f"Lv{p['level']}")
                lines.append(l1 + (f"\n　　{'・'.join(bits)}" if bits else ''))
            add_lines(emb, head, lines)
        rounds = e.get('rounds') or []
        if len(rounds) > 1 and e.get('myTeam'):
            glyph = {'Detonate': '💥', 'Defuse': '✂️', 'Surrendered': '🏳️'}
            strip = ''.join(('🟦' if r['win'] == e['myTeam'] else '🟥') + glyph.get(r['code'], '') for r in rounds)
            notes = []
            for r in rounds:
                parts = []
                if r['ceremony']:
                    parts.append(r['ceremony'])
                if r['site']:
                    parts.append(f"{r['site']} 包" + (f"（{_esc(names[r['planter']]['agent'])}）" if r['planter'] in names else ''))
                if r['defuser'] in names:
                    parts.append(f"拆包 {_esc(names[r['defuser']]['agent'])}")
                if r['ceremony'] and parts:
                    notes.append(f"R{r['n'] + 1} {' '.join(parts)}")
            add_lines(emb, f'回合（藍 = 我方贏；💥爆炸 ✂️拆除）', [strip] + ([' / '.join(notes)] if notes else []))
        foot = f"MVP {_esc(names[e['matchMvp']]['agent'])}・" if e.get('matchMvp') in names else ''
        emb.set_footer(text=foot + 'ACS 每回合分數・ADR 每回合傷害・KAST 有殺/助攻/存活/被換的回合比例・技能 C/Q/E/X 次數')
        return emb

    def _detail_embed(self, d: dict) -> discord.Embed:
        s = d['summary'] or {}
        color = COLOR_DRAW if s.get('draw') or s.get('won') is None else (COLOR_WIN if s.get('won') else COLOR_LOSS)
        title = f"{_esc(d['map'])}・{d['queue']}"
        if s.get('score'):
            title += f"　{s['score']}"
        emb = discord.Embed(title=title, color=color)
        if s.get('started'):
            emb.description = f"{discord.utils.format_dt(s['started'], 'f')}・{s.get('lengthMin', 0)} 分鐘"
        if d['strip']:
            emb.add_field(name='回合（藍 = 這位玩家的隊伍贏）', value=d['strip'][:1024], inline=False)
        for t in d['teams'][:2]:
            rr = t['rounds']
            head = ('我方' if t['mine'] else '敵方') if len(d['teams']) > 1 else '全部玩家'
            if rr:
                head += f"　{'勝' if t['won'] else '敗'} {rr.get('won', 0)}:{rr.get('lost', 0)}"
            lines = []
            for p in t['players'][:12]:
                me = '▶ ' if p['puuid'] == d['me'] else ''
                star = '⭐' if p['puuid'] == t['mvp'] else ''
                stats = (f"爆頭 {p['hs']}%" if p['acs'] is None
                         else f"ACS {p['acs']}・ADR {p['adr']}・爆頭 {p['hs']}%・首殺 {p['fb']}")
                lines.append(f"{me}**{_esc(p['agent'])}** {_esc(p['name'])}{'#' + _esc(p['tag']) if p.get('tag') else ''}{star}　{p['k']}/{p['d']}/{p['a']}　{stats}")
            emb.add_field(name=head, value='\n'.join(lines)[:1024] or '—', inline=False)
        emb.set_footer(text='ACS = 每回合平均分數・ADR = 每回合平均傷害・資料來源 HenrikDev')
        return emb

    def _select(self, region: str, puuid: str, sums: list[dict]) -> discord.ui.View:
        view = discord.ui.View(timeout=None)
        opts = [discord.SelectOption(
            label=(f"{s['score']} " if s.get('ffa') else f"{'勝' if s['won'] else ('平' if s['draw'] else '敗')} ")
            + f"{s['agent']} {s['k']}/{s['d']}/{s['a']}"[:90],
            description=f"{s['map']}・{s['queue']}・{s['score']}"[:100], value=s['matchId'])
            for s in sums if s.get('matchId')]
        if opts:
            view.add_item(discord.ui.Select(custom_id=f'valo:sel:{region}:{puuid}', placeholder='看單場詳細計分板',
                                            options=opts[:25]))
        return view

    def _live_embed(self, d: dict, recents: dict) -> discord.Embed:
        stage = '選角中（只看得到我方）' if d['kind'] == 'pregame' else '對局中'
        emb = discord.Embed(title=f"🔫 {d['queue']}・{_esc(d['map'])}　{stage}", color=0xFF4655)
        for t in d['teams']:
            lines = []
            for p in t['players']:
                who = '🙈 隱藏名字' if p['hidden'] else f"{_esc(p['name'])}#{_esc(p['tag'])}" if p['name'] else '?'
                r = p['rank']
                rank = f"{r['name']} {r['rr']} RR" if r['tier'] else '未定級'
                season = f"　本季 {r['wins']}勝{r['games'] - r['wins']}敗 {r['wr']}%" if r['games'] else ''
                peak = f"　峰 {r['peakName']}" if r['peak'] and r['peak'] > r['tier'] else ''
                lv = f"　Lv {p['level']}" if p['level'] else ''
                lock = '' if p['locked'] else '（未鎖定）'
                rec = recent_line(recents.get(p['puuid']) or [])
                lines.append(f"{'▶ ' if p['me'] else ''}**{_esc(p['agent'])}**{lock} {who}　{rank}{season}{peak}{lv}"
                             + (f'\n　　{rec}' if rec else ''))
            emb.add_field(name='我方' if t['mine'] else '敵方', value='\n'.join(lines)[:1024] or '—', inline=False)
        emb.set_footer(text='牌位 = 競技模式本季；資料直接來自 Riot，隱藏名字的玩家不顯示名字')
        return emb

    # ── 指令 ──
    # 即時畫面一律由 /act(Activity)開；這裡是純文字版：
    # 玩家 PC 的 Riot Client 有登入 → 直接查 Riot 的對局(雙方 10 人)；沒有 → 退回 HenrikDev 的牌位與近況
    @app_commands.command(name='valo-live', description='查你目前的 Valorant 對局（雙方特務、牌位、近況）；填別人時顯示他的牌位與近況')
    @app_commands.describe(player='玩家，例如 名字#1234（不填 = 你自己；Riot 只給查自己的對局，別人只能看牌位近況）')
    async def valo_live(self, interaction: discord.Interaction, player: Optional[str] = None):
        await interaction.response.defer(thinking=True)
        rid = parse_rid(player) if player else None
        if player and not rid:
            await interaction.followup.send('❌ 格式是 `名字#tag`，例如 `名字#1234`')
            return
        linked = bool(self.link) and await self.link.token()
        acc = None
        if rid:
            acc = await self.api.account(*rid) if self.api else None
            target = (acc or {}).get('puuid')
            if not target:
                await interaction.followup.send(f'❌ 找不到 Valorant 玩家 `{_esc(player)}`'
                                                + ('' if self.api else '（沒有設定 HENRIK_API_KEY，查不了別人）'))
                return
        elif linked:
            target = self.link.puuid
        else:
            await interaction.followup.send('❌ 沒填玩家，而且你電腦上的 Riot Client 沒登入，不知道要查誰。請填 `名字#tag`')
            return

        if linked:
            # Riot 只給查登入者自己的對局(別人的 puuid 一律 403，2026-10-11 實測 273 人)
            # → 填別人時看他是不是在我這場，在就顯示這場並標記他
            cur = await self.link.current_match()
            pl = []
            if cur:
                kind, m = cur
                pl = m.get('Players') if kind == 'coregame' else (m.get('AllyTeam') or {}).get('Players')
            puuids = [p['Subject'] for p in pl or [] if p.get('Subject')]
            if target in puuids:
                names, mmrs, season, recents = await self.link.lookup(puuids, self.content)
                emb = self._live_embed(parse_match(kind, m, names, mmrs, season, self.content, target), recents)
                emb.set_footer(text='牌位 = 競技模式本季；資料直接來自 Riot（經你電腦的客戶端登入），隱藏名字的玩家不顯示名字')
                await interaction.followup.send(embed=emb)
                return
            if target == self.link.puuid:
                note = '你目前不在對局中'
            elif cur:
                note = '這位玩家不在你這場；Riot 只給查你自己的對局'
            else:
                note = 'Riot 只給查你自己的對局，而你目前不在對局中'
        else:
            note = '你電腦上的 Riot Client 沒登入，查不到進行中的對局'

        # 退路：HenrikDev 的牌位與近況(不需要 PC)
        if not self.api:
            await interaction.followup.send(f'ℹ️ {note}，而且沒有設定 HENRIK_API_KEY，沒有別的資料可以給')
            return
        if not acc:   # 查自己：puuid → 名字 → HenrikDev 帳號
            n = (await self.link.names([target])).get(target) or {}
            acc = await self.api.account(n.get('GameName', ''), n.get('TagLine', '')) if n.get('GameName') else None
        if not acc:
            await interaction.followup.send(f'ℹ️ {note}，HenrikDev 也查不到這位玩家')
            return
        emb, view = await self._profile(acc)
        await interaction.followup.send(f'ℹ️ {note}，以下是牌位與近況（HenrikDev）：', embed=emb, view=view)

    async def _profile(self, acc: dict) -> tuple[discord.Embed, discord.ui.View]:
        region, name, tag = acc.get('region') or 'ap', acc['name'], acc['tag']
        mmr, matches = await asyncio.gather(self.api.mmr(region, name, tag), self.api.matches(region, name, tag, 5))
        sums = []
        for m in matches or []:
            self.api.remember_match(region, m)
            s = summarize(m, acc['puuid'], self.content)
            if s:
                sums.append(s)
        return self._profile_embed(acc, mmr, sums), self._select(region, acc['puuid'], sums)

    async def _resolve(self, interaction: discord.Interaction, player: str) -> Optional[dict]:
        if not self.api:
            await interaction.followup.send('❌ 沒有設定 HENRIK_API_KEY')
            return None
        rid = parse_rid(player)
        if not rid:
            await interaction.followup.send('❌ 格式是 `名字#tag`，例如 `名字#1234`')
            return None
        acc = await self.api.account(*rid)
        if not acc or not acc.get('puuid'):
            await interaction.followup.send(f'❌ 找不到 Valorant 玩家 `{_esc(player)}`（或 API 額度暫時用完，等一分鐘再試）')
            return None
        return acc

    @app_commands.command(name='valo', description='查 Valorant 玩家的牌位與最近戰績')
    @app_commands.describe(player='玩家，例如 名字#1234')
    async def valo(self, interaction: discord.Interaction, player: str):
        await interaction.response.defer(thinking=True)
        acc = await self._resolve(interaction, player)
        if not acc:
            return
        emb, view = await self._profile(acc)
        await interaction.followup.send(embed=emb, view=view)

    @track.command(name='add', description='追蹤玩家：打完一場自動把戰績發到這個頻道')
    @app_commands.describe(player='玩家，例如 名字#1234')
    async def track_add(self, interaction: discord.Interaction, player: str):
        await interaction.response.defer(thinking=True)
        gid = str(interaction.guild_id or 0)
        rid = parse_rid(player)
        key = self._tracked_key(rid)
        if key:   # 已經在追蹤(別的伺服器加的)→ 這個伺服器也要收：記這裡的頻道就好，不重抓
            self._channels(self.tracked[key])[gid] = interaction.channel_id
            self._save_tracked()
            t = self.tracked[key]
            await interaction.followup.send(f"✅ **{_esc(t['name'])}#{_esc(t['tag'])}** 的戰績也會發到 <#{interaction.channel_id}>"
                                            f"（這個伺服器原本的設定已改成這個頻道）")
            return
        if len(self.tracked) >= MAX_TRACKED:
            await interaction.followup.send(f'❌ 最多追蹤 {MAX_TRACKED} 人（API 額度有限）')
            return
        acc = await self._resolve(interaction, player)
        if not acc:
            return
        region = acc.get('region') or 'ap'
        latest = await self.api.matches(region, acc['name'], acc['tag'], 1)
        last_id = ((latest or [{}])[0].get('metadata') or {}).get('match_id')
        self.tracked[acc['puuid']] = {'name': acc['name'], 'tag': acc['tag'], 'region': region,
                                      'channels': {gid: interaction.channel_id}, 'last': last_id,
                                      'by': interaction.user.id}
        self._save_tracked()
        await interaction.followup.send(f"✅ 開始追蹤 **{_esc(acc['name'])}#{_esc(acc['tag'])}**，"
                                        f"之後每打完一場會發到 <#{interaction.channel_id}>"
                                        f"（大約 {self._cycle_minutes()} 分鐘內；別的伺服器要收就在那邊再 add 一次）")

    def _tracked_key(self, rid) -> Optional[str]:
        return next((pu for pu, t in self.tracked.items()
                     if rid and t['name'].lower() == rid[0].lower() and t['tag'].lower() == rid[1].lower()), None)

    def _channels(self, t: dict) -> dict:
        """每個伺服器一個頻道：{guild_id: channel_id}。舊格式只有一個 channel → 轉過去。"""
        if 'channels' not in t:
            ch = self.bot.get_channel(t.get('channel') or 0)
            t['channels'] = {str(ch.guild.id): ch.id} if ch and getattr(ch, 'guild', None) else {}
            t.pop('channel', None)
        return t['channels']

    @track.command(name='remove', description='取消追蹤玩家')
    @app_commands.describe(player='玩家，例如 名字#1234')
    async def track_remove(self, interaction: discord.Interaction, player: str):
        key = self._tracked_key(parse_rid(player))
        gid = str(interaction.guild_id or 0)
        if not key or gid not in self._channels(self.tracked[key]):
            await interaction.response.send_message('❌ 這個伺服器沒有追蹤這個人，用 `/valo-track list` 看名單', ephemeral=True)
            return
        t = self.tracked[key]
        chs = self._channels(t)
        chs.pop(gid, None)
        if not chs:   # 沒有任何伺服器要收了才真的停止查
            self.tracked.pop(key)
        self._save_tracked()
        await interaction.response.send_message(f"✅ 這個伺服器已取消追蹤 **{_esc(t['name'])}#{_esc(t['tag'])}**"
                                                + ('' if chs else '（其他伺服器也沒在追，已完全停止）'))

    @track.command(name='list', description='目前追蹤中的玩家')
    async def track_list(self, interaction: discord.Interaction):
        gid = str(interaction.guild_id or 0)
        lines = [f"・{_esc(t['name'])}#{_esc(t['tag'])} → <#{self._channels(t)[gid]}>"
                 for t in self.tracked.values() if gid in self._channels(t)]
        if not lines:
            await interaction.response.send_message('這個伺服器目前沒有追蹤任何人，用 `/valo-track add` 加入', ephemeral=True)
            return
        await interaction.response.send_message(
            f"追蹤中（每人約 {self._cycle_minutes()} 分鐘檢查一次）：\n" + '\n'.join(lines), ephemeral=True)

    def _cycle_minutes(self) -> int:
        return max(1, len(self.tracked) * TRACK_EVERY // 60)

    # ── 選單：單場詳細 ──
    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        if interaction.type != discord.InteractionType.component:
            return
        cid = (interaction.data or {}).get('custom_id', '')
        if not cid.startswith('valo:sel:'):
            return
        _, _, region, puuid = cid.split(':', 3)
        values = (interaction.data or {}).get('values') or []
        if not values or not self.api:
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        m = await self.api.match(region, values[0])
        if not m:
            await interaction.followup.send('❌ 拿不到這場的資料（可能 API 額度暫時用完）', ephemeral=True)
            return
        e = eog_table(henrik_to_riot(m), puuid, set(), self.content)
        emb = self._rich_embed(e, '🔫 單場詳細') if e['teams'] else self._detail_embed(detail(m, puuid, self.content))
        await interaction.followup.send(embed=emb, ephemeral=True)

    # ── 背景追蹤 ──
    async def _track_loop(self):
        await self.bot.wait_until_ready()
        idx = 0
        while True:
            await asyncio.sleep(TRACK_EVERY)
            if not self.api or not self.tracked:
                continue
            try:
                keys = list(self.tracked)
                pu = keys[idx % len(keys)]
                idx += 1
                await self._check_one(pu)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning(f'🔫 追蹤例外: {e!r}')

    async def _check_one(self, puuid: str):
        t = self.tracked.get(puuid)
        if not t:
            return
        # size=1 實測會拿到舊資料(2026-10-09：最新一場只出現在 size≥3)→ 抓 3 場自己比
        ms = await self.api.matches(t['region'], t['name'], t['tag'], 3, background=True, ttl=30)
        if not ms:
            return
        done = [m for m in ms if (m.get('metadata') or {}).get('is_completed', True)
                and (m.get('metadata') or {}).get('match_id')]
        done.sort(key=lambda m: (m.get('metadata') or {}).get('started_at') or '')
        ids = [m['metadata']['match_id'] for m in done]
        if not ids or ids[-1] == t.get('last'):
            return
        # 上次記的那場之後的都是新的；記的那場不在這 3 場裡(很久沒查或第一次)就只發最新一場
        new = done[ids.index(t['last']) + 1:] if t.get('last') in ids else done[-1:]
        # 改過名 → 用對戰裡的新名字
        me = next((p for p in done[-1].get('players') or [] if p.get('puuid') == puuid), None)
        if me:
            t['name'], t['tag'] = me.get('name', t['name']), me.get('tag', t['tag'])
        log.info(f"🔫 追蹤 {t['name']}：新場次 {len(new)} 場 {[m['metadata']['match_id'][:8] for m in new]}")
        for m in new:
            self.api.remember_match(t['region'], m)
            # 發成功才記住這場；發到一半 bot 被重啟的話下次還會補發
            if await self._post_match(t, puuid, m):
                t['last'] = m['metadata']['match_id']
                self._save_tracked()

    async def _track_now(self, puuid: str, match_id: str):
        """本人(Activity 看到的對局)剛打完 → 不等輪到，每 20 秒查一次 HenrikDev，最多 10 分鐘。"""
        if puuid not in self.tracked or not self.api:
            return
        for _ in range(30):
            if self.tracked.get(puuid, {}).get('last') == match_id:
                return
            try:
                await self._check_one(puuid)
            except Exception as e:
                log.warning(f'🔫 追蹤(即時)例外: {e!r}')
            await asyncio.sleep(20)

    async def _post_match(self, t: dict, puuid: str, m: dict) -> bool:
        s = summarize(m, puuid, self.content)
        chs = [c for c in (self.bot.get_channel(cid) for cid in self._channels(t).values()) if c]
        if not s or not chs:
            log.warning(f"🔫 追蹤 {t['name']}：跳過 {m['metadata']['match_id'][:8]}"
                        f"（{'找不到這位玩家的資料' if not s else '頻道不存在 ' + str(self._channels(t))}）")
            return bool(chs)   # 玩家資料真的沒有就不要一直重發；頻道沒了則留著下次再試
        color = COLOR_DRAW if s['draw'] or s['won'] is None else (COLOR_WIN if s['won'] else COLOR_LOSS)
        if s.get('ffa'):
            res = s['score'] or '結束'
        else:
            res = '平手' if s['draw'] else ('勝利' if s['won'] else ('落敗' if s['won'] is False else '結束'))
        mmr = await self.api.mmr(t['region'], t['name'], t['tag'], background=True) if s['queue'] == '競技' else None
        rr = {}
        if mmr:
            ms_ = mmr_summary(mmr, self.content)
            if isinstance(ms_['lastChange'], int):
                rr[puuid] = {'earned': ms_['lastChange'], 'after': ms_['current']['id'], 'rrAfter': ms_['rr']}
        # 完整計分板(跟 Activity 結算同一套計算)；HenrikDev 資料不完整時退回一行摘要
        e = eog_table(henrik_to_riot(m), puuid, set(), self.content, None, rr)
        if e['teams']:
            emb = self._rich_embed(e, f"🔫 {_esc(t['name'])} 剛打完一場：{res}")
        else:
            emb = discord.Embed(title=f"🔫 {_esc(t['name'])} 剛打完一場：{res}", color=color, description=self._match_line(s))
        icon = (self.content['agents'].get(s['agentId']) or {}).get('icon')
        if icon:
            emb.set_thumbnail(url=icon)
        ok = False
        for ch in chs:   # 每個伺服器各發一份
            try:
                await ch.send(embed=emb, view=self._select(t['region'], puuid, [s]))
                ok = True
                log.info(f"🔫 追蹤 {t['name']}：{m['metadata']['match_id'][:8]} 已發到 #{ch.name}")
            except discord.HTTPException as e:
                log.warning(f'🔫 追蹤通知發送失敗 <#{ch.id}>: {e!r}')
        return ok


async def setup(bot: commands.Bot):
    await bot.add_cog(ValoCog(bot))
