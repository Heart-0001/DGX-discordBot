"""/valo：Valorant 戰績(HenrikDev 非官方 API，HENRIK_API_KEY)。

  /valo player:名字#tag       牌位、RR、最近 5 場；下拉選單看單場完整計分板
  /valo-track add/remove/list  追蹤玩家，打完一場自動把結果發到指定頻道
只用賽後的公開資料，不碰遊戲中的即時資訊。
額度：key 是 30 次/分鐘，這裡用九成，背景追蹤只在剩餘額度夠多時才查(互動指令優先)。
中文名稱與圖示來自 valorant-api.com(免費、不需要 key)。
"""
import asyncio
import json
import logging
import os
import re
import time
from typing import Optional
from urllib.parse import quote

import aiohttp
import discord
from aiohttp import web
from discord import app_commands
from discord.ext import commands

from cogs.riot_api import RateLimiter
from cogs.valo_live import MODE_RECENT, RiotLink, shard_from_env
from cogs.valo_live_parse import eog_table, mode_record, parse_match, recent_line
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


async def load_content(http: aiohttp.ClientSession) -> dict:
    """特務、地圖、牌位的中文名稱與圖示。下載失敗用上次的快取。"""
    try:
        async def j(path):
            async with http.get(f'{VAPI}/{path}', timeout=aiohttp.ClientTimeout(total=20)) as r:
                r.raise_for_status()
                return (await r.json())['data']
        agents, maps, tiers = await asyncio.gather(
            j('agents?language=zh-TW&isPlayableCharacter=true'), j('maps?language=zh-TW'),
            j('competitivetiers?language=zh-TW'))
        c = {
            'agents': {a['uuid']: {'name': a['displayName'], 'icon': a.get('displayIcon')} for a in agents},
            'maps': {m['uuid']: m['displayName'] for m in maps},
            'mapPaths': {m['mapUrl']: m['displayName'] for m in maps if m.get('mapUrl')},
            'mapIcons': {m['uuid']: m.get('listViewIcon') for m in maps},
            'tiers': {str(t['tier']): {'name': t['tierName'], 'icon': t.get('largeIcon') or t.get('smallIcon')}
                      for t in tiers[-1]['tiers']},
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
                    self.state['_eog_at'] = time.time()
                    self._set(eog=eog_table(d, me, self._live_hidden, self.content, names))
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
    @app_commands.command(name='valo-live', description='開啟 Valorant 對局畫面（隊友／對手牌位、近況）；text=True 改成貼一則文字')
    @app_commands.describe(text='只貼一則文字版，不開 Activity')
    async def valo_live(self, interaction: discord.Interaction, text: bool = False):
        if not text:
            if not interaction.user.voice:
                await interaction.response.send_message('❌ 請先加入語音頻道再開 Valorant 畫面（或用 text:True）', ephemeral=True)
                return
            set_mode = getattr(self.bot, 'set_activity_mode', None)
            if set_mode:
                set_mode(interaction, 'valo')
            if getattr(self.bot, 'activity_open', lambda i: False)(interaction):
                await interaction.response.send_message('✅ 已切換成 Valorant 畫面', ephemeral=True)
                return
            try:
                await interaction.response.launch_activity()
            except discord.HTTPException as e:
                log.error(f'launch_activity 失敗: {e!r}')
                await interaction.response.send_message('❌ 無法開啟 Activity', ephemeral=True)
            return
        await interaction.response.defer(thinking=True)
        cur = await self.link.current_match() if self.link else None
        if not cur:
            await interaction.followup.send('目前沒有進行中的 Valorant 對局（或電腦沒開 Riot Client）')
            return
        kind, m = cur
        pl = m.get('Players') if kind == 'coregame' else (m.get('AllyTeam') or {}).get('Players')
        puuids = [p['Subject'] for p in pl or [] if p.get('Subject')]
        names, mmrs, season, recents = await self.link.lookup(puuids, self.content)
        await interaction.followup.send(embed=self._live_embed(
            parse_match(kind, m, names, mmrs, season, self.content, self.link.puuid), recents))

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
        region, name, tag = acc.get('region') or 'ap', acc['name'], acc['tag']
        mmr, matches = await asyncio.gather(self.api.mmr(region, name, tag), self.api.matches(region, name, tag, 5))
        sums = []
        for m in matches or []:
            self.api.remember_match(region, m)
            s = summarize(m, acc['puuid'], self.content)
            if s:
                sums.append(s)
        await interaction.followup.send(embed=self._profile_embed(acc, mmr, sums),
                                        view=self._select(region, acc['puuid'], sums))

    @track.command(name='add', description='追蹤玩家：打完一場自動把戰績發到這個頻道')
    @app_commands.describe(player='玩家，例如 名字#1234')
    async def track_add(self, interaction: discord.Interaction, player: str):
        await interaction.response.defer(thinking=True)
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
                                      'channel': interaction.channel_id, 'last': last_id,
                                      'by': interaction.user.id}
        self._save_tracked()
        await interaction.followup.send(f"✅ 開始追蹤 **{_esc(acc['name'])}#{_esc(acc['tag'])}**，"
                                        f"之後每打完一場會發到 <#{interaction.channel_id}>"
                                        f"（大約 {self._cycle_minutes()} 分鐘內）")

    @track.command(name='remove', description='取消追蹤玩家')
    @app_commands.describe(player='玩家，例如 名字#1234')
    async def track_remove(self, interaction: discord.Interaction, player: str):
        rid = parse_rid(player)
        key = next((pu for pu, t in self.tracked.items()
                    if rid and t['name'].lower() == rid[0].lower() and t['tag'].lower() == rid[1].lower()), None)
        if not key:
            await interaction.response.send_message('❌ 名單裡沒有這個人，用 `/valo-track list` 看名單', ephemeral=True)
            return
        t = self.tracked.pop(key)
        self._save_tracked()
        await interaction.response.send_message(f"✅ 已取消追蹤 **{_esc(t['name'])}#{_esc(t['tag'])}**")

    @track.command(name='list', description='目前追蹤中的玩家')
    async def track_list(self, interaction: discord.Interaction):
        if not self.tracked:
            await interaction.response.send_message('目前沒有追蹤任何人，用 `/valo-track add` 加入', ephemeral=True)
            return
        lines = [f"・{_esc(t['name'])}#{_esc(t['tag'])} → <#{t['channel']}>" for t in self.tracked.values()]
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
        await interaction.followup.send(embed=self._detail_embed(detail(m, puuid, self.content)), ephemeral=True)

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
        t['last'] = ids[-1]
        # 改過名 → 用對戰裡的新名字
        me = next((p for p in done[-1].get('players') or [] if p.get('puuid') == puuid), None)
        if me:
            t['name'], t['tag'] = me.get('name', t['name']), me.get('tag', t['tag'])
        self._save_tracked()
        for m in new:
            self.api.remember_match(t['region'], m)
            await self._post_match(t, puuid, m)

    async def _post_match(self, t: dict, puuid: str, m: dict):
        s = summarize(m, puuid, self.content)
        ch = self.bot.get_channel(t['channel'])
        if not s or not ch:
            return
        color = COLOR_DRAW if s['draw'] or s['won'] is None else (COLOR_WIN if s['won'] else COLOR_LOSS)
        if s.get('ffa'):
            res = s['score'] or '結束'
        else:
            res = '平手' if s['draw'] else ('勝利' if s['won'] else ('落敗' if s['won'] is False else '結束'))
        emb = discord.Embed(title=f"🔫 {_esc(t['name'])} 剛打完一場：{res}", color=color,
                            description=self._match_line(s))
        icon = (self.content['agents'].get(s['agentId']) or {}).get('icon')
        if icon:
            emb.set_thumbnail(url=icon)
        mmr = await self.api.mmr(t['region'], t['name'], t['tag'], background=True) if s['queue'] == '競技' else None
        if mmr:
            ms_ = mmr_summary(mmr, self.content)
            ch_ = ms_['lastChange']
            emb.add_field(name='牌位', value=f"{_esc(ms_['current']['name'])} {ms_['rr']} RR"
                                             f"{f'（{ch_:+d}）' if isinstance(ch_, int) else ''}")
        try:
            await ch.send(embed=emb, view=self._select(t['region'], puuid, [s]))
        except discord.HTTPException as e:
            log.warning(f'🔫 追蹤通知發送失敗: {e!r}')


async def setup(bot: commands.Bot):
    await bot.add_cog(ValoCog(bot))
