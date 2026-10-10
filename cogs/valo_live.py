"""Valorant 進行中對局：借用玩家 PC 上 Riot Client 的登入，直接查 Riot 內部伺服器。

流程(跟市面上的 rank 工具一樣)：
  1. SSH 到 PC 讀 Riot Client 的 lockfile → 本機 API 的 port/密碼(只讀，跟 LoL 的 LCU 同一套)
  2. GET 127.0.0.1:{port}/entitlements/v1/token → access token、entitlement token、自己的 puuid
  3. 拿 token 從 DGX 直接打 GLZ(對局)和 PD(玩家資料)：
       glz /pregame/v1/players/{me}  → 選角中的對局 id(只有我方 5 人)
       glz /core-game/v1/players/{me} → 讀取畫面之後的對局 id(雙方 10 人)
       pd  /mmr/v1/players/{puuid}    → 牌位、RR、本季勝場
       pd  /name-service/v3/players   → puuid 換名字
這些端點不在 Riot 官方 API 裡，只讀、不改遊戲、不碰記憶體。
"""
import asyncio
import base64
import json
import logging
import os
import time
from typing import Optional

import aiohttp

from cogs.lol import PcLink
from cogs.valo_live_parse import match_summaries

log = logging.getLogger(__name__)

RC_LOCKFILE = r'%LOCALAPPDATA%\Riot Games\Riot Client\Config\lockfile'
VAPI_VERSION = 'https://valorant-api.com/v1/version'
PLATFORM = base64.b64encode(json.dumps({
    'platformType': 'PC', 'platformOS': 'Windows', 'platformOSVersion': '10.0.19042.1.256.64bit',
    'platformChipset': 'Unknown'}).encode()).decode()
TOKEN_TTL = 50 * 60      # access token 約 1 小時有效，提早換
VERSION_TTL = 6 * 3600
CONTENT_TTL = 3600
MMR_TTL = 300
RECENT = 5               # 每人抓最近幾場(每場一次請求、約 270KB，所以不多抓)
MODE_RECENT = 10         # 「當前模式」戰績看最近幾場(mmr 裡非競技模式的本季勝場實測是壞的，只能自己算)
MATCH_TTL = 3600
PD_GAP = 0.25            # 秒：match-details 連發太快會 429(實測 50 個一起發就中)，每個請求間隔
RETRY_429 = 3


class RiotLink:
    def __init__(self, host: str, shard: str, http: aiohttp.ClientSession):
        self.pc = PcLink(host, '')
        self.shard = shard
        self.http = http
        self.puuid = ''
        self._tok: Optional[tuple[str, str]] = None   # (access, entitlement)
        self._tok_at = 0.0
        self._ver = ('', 0.0)
        self._content = (None, 0.0)
        self._mmr: dict[str, tuple[float, Optional[dict]]] = {}
        self._matches: dict[str, tuple[float, dict]] = {}     # match id → 每人摘要(match_summaries)
        self._sem = asyncio.Semaphore(2)

    # ── 從 PC 拿 token ──
    async def _read_lockfile(self) -> Optional[tuple[str, str]]:
        out = await self.pc._run(f'type "{RC_LOCKFILE}"')
        parts = out.decode(errors='replace').strip().split(':') if out else []
        if len(parts) >= 5 and parts[2].isdigit() and parts[3].replace('-', '').replace('_', '').isalnum():
            return parts[2], parts[3]
        return None

    async def token(self, force: bool = False) -> bool:
        if self._tok and not force and time.time() - self._tok_at < TOKEN_TTL:
            return True
        lf = await self._read_lockfile()
        if not lf:
            return False
        port, pw = lf
        d = await self.pc._json(f'https://127.0.0.1:{port}/entitlements/v1/token', f'-u riot:{pw} ')
        if not isinstance(d, dict) or not d.get('accessToken') or not d.get('token'):
            return False
        self._tok, self._tok_at, self.puuid = (d['accessToken'], d['token']), time.time(), d.get('subject', '')
        return True

    async def version(self) -> str:
        if self._ver[0] and time.time() - self._ver[1] < VERSION_TTL:
            return self._ver[0]
        try:
            async with self.http.get(VAPI_VERSION, timeout=aiohttp.ClientTimeout(total=15)) as r:
                v = (await r.json())['data']['riotClientVersion']
                self._ver = (v, time.time())
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, KeyError) as e:
            log.warning(f'🔫 取客戶端版本失敗: {e!r}')
        return self._ver[0]

    # ── 打 Riot 伺服器 ──
    async def _req(self, method: str, url: str, body=None, retry: bool = True, tries: int = RETRY_429):
        if not await self.token():
            return None
        headers = {'Authorization': f'Bearer {self._tok[0]}', 'X-Riot-Entitlements-JWT': self._tok[1],
                   'X-Riot-ClientPlatform': PLATFORM, 'X-Riot-ClientVersion': await self.version()}
        try:
            async with self.http.request(method, url, json=body, headers=headers,
                                         timeout=aiohttp.ClientTimeout(total=15)) as r:
                if r.status in (400, 401) and retry:      # token 過期或客戶端重開過
                    self._tok = None
                    return await self._req(method, url, body, retry=False)
                if r.status == 404:
                    return None
                if r.status == 429 and tries > 0:
                    ra = r.headers.get('Retry-After')
                    await asyncio.sleep(min(10, float(ra)) if ra and ra.replace('.', '').isdigit() else 2.0)
                    return await self._req(method, url, body, retry, tries - 1)
                if r.status != 200:
                    log.warning(f'🔫 Riot {r.status} {url.split("pvp.net")[-1].split("/players/")[0]}')
                    return None
                return await r.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as e:
            log.warning(f'🔫 Riot 請求失敗: {e!r}')
            return None

    def glz(self, path: str):
        return self._req('GET', f'https://glz-{self.shard}-1.{self.shard}.a.pvp.net{path}')

    def pd(self, path: str, method: str = 'GET', body=None):
        return self._req(method, f'https://pd.{self.shard}.a.pvp.net{path}', body)

    async def score(self) -> Optional[dict]:
        """回合比數：Riot Client 本機 /chat/v4/presences 裡自己的 presence(進行中的個人數據 Riot 不給，只有這個)。"""
        lf = await self._read_lockfile()
        if not lf:
            return None
        d = await self.pc._json(f'https://127.0.0.1:{lf[0]}/chat/v4/presences', f'-u riot:{lf[1]} ')
        for p in (d or {}).get('presences') or []:
            if p.get('product') == 'valorant' and p.get('puuid') == self.puuid and p.get('private'):
                try:
                    pr = json.loads(base64.b64decode(p['private']))
                except (ValueError, TypeError):
                    return None
                m = pr.get('matchPresenceData') or {}
                return {'ally': pr.get('partyOwnerMatchScoreAllyTeam'), 'enemy': pr.get('partyOwnerMatchScoreEnemyTeam'),
                        'type': m.get('gameScoreType'), 'state': m.get('sessionLoopState')}
        return None

    # ── 對局 ──
    async def current_match(self) -> Optional[tuple[str, dict]]:
        """回 ('pregame'|'coregame', 對局資料)，不在對局中回 None。"""
        if not await self.token():
            return None
        me = self.puuid
        core = await self.glz(f'/core-game/v1/players/{me}')
        if core and core.get('MatchID'):
            m = await self.glz(f'/core-game/v1/matches/{core["MatchID"]}')
            if m:
                return 'coregame', m
        pre = await self.glz(f'/pregame/v1/players/{me}')
        if pre and pre.get('MatchID'):
            m = await self.glz(f'/pregame/v1/matches/{pre["MatchID"]}')
            if m:
                return 'pregame', m
        return None

    async def active_season(self) -> str:
        if self._content[0] and time.time() - self._content[1] < CONTENT_TTL:
            return self._content[0]
        c = await self._req('GET', f'https://shared.{self.shard}.a.pvp.net/content-service/v3/content')
        sid = ''
        for s in (c or {}).get('Seasons', []):
            if s.get('Type') == 'act' and s.get('IsActive'):
                sid = s.get('ID', '')
        if sid:
            self._content = (sid, time.time())
        return sid

    async def mmr(self, puuid: str) -> Optional[dict]:
        hit = self._mmr.get(puuid)
        if hit and time.time() - hit[0] < MMR_TTL:
            return hit[1]
        d = await self.pd(f'/mmr/v1/players/{puuid}')
        self._mmr[puuid] = (time.time(), d)
        if len(self._mmr) > 200:
            now = time.time()
            for k in [k for k, (t, _) in self._mmr.items() if now - t > MMR_TTL]:
                self._mmr.pop(k, None)
        return d

    async def names(self, puuids: list[str]) -> dict[str, dict]:
        d = await self.pd('/name-service/v2/players', 'PUT', puuids)
        return {p['Subject']: p for p in d if p.get('Subject')} if isinstance(d, list) else {}

    async def _paced(self, path: str):
        """pd 的歷史類請求：限同時 2 個、每個之間空一下，避免 429。"""
        async with self._sem:
            d = await self.pd(path)
            await asyncio.sleep(PD_GAP)
        return d

    async def match_summary(self, mid: str, content: dict) -> Optional[dict]:
        """查不到(429 用完重試、或還沒結算)回 None，讓呼叫端知道不完整。"""
        hit = self._matches.get(mid)
        if hit and time.time() - hit[0] < MATCH_TTL:
            return hit[1]
        d = await self._paced(f'/match-details/v1/matches/{mid}')
        summ = match_summaries(d, content) if d else None
        if d:
            self._matches[mid] = (time.time(), summ)
            if len(self._matches) > 300:
                for k in sorted(self._matches, key=lambda k: self._matches[k][0])[:100]:
                    self._matches.pop(k, None)
        return summ

    async def recent(self, puuid: str, content: dict, n: int = RECENT, queue: str = '') -> Optional[list[dict]]:
        """最近 n 場的 {w,k,d,a,...}，新的在前；進行中的那場不算；queue 指定只看某模式。
        有任何一場查不到就回 None(呼叫端之後再補)。"""
        q = f'&queue={queue}' if queue else ''
        h = await self._paced(f'/match-history/v1/history/{puuid}?startIndex=0&endIndex={n + 1}{q}')
        if h is None:
            return None
        mids = [x['MatchID'] for x in h.get('History') or [] if x.get('MatchID')]
        summs = await asyncio.gather(*[self.match_summary(m, content) for m in mids])
        if any(s is None for s in summs):
            return None
        return [s[puuid] for s in summs if s.get(puuid)][:n]

    async def lookup_basic(self, puuids: list[str]) -> tuple[dict, dict, str]:
        """名字、mmr、本季 id：都很快(一兩秒)，先推給畫面。"""
        names, season, mmrs = await asyncio.gather(
            self.names(puuids), self.active_season(), asyncio.gather(*[self.mmr(p) for p in puuids]))
        return names, dict(zip(puuids, mmrs)), season

    async def lookup_recent(self, puuids: list[str], content: dict, n: int = RECENT, queue: str = '') -> dict:
        """近幾場(每人 n+1 次請求、有限速，10 人 5 場約 20 秒)，慢慢補。"""
        recents = await asyncio.gather(*[self.recent(p, content, n, queue) for p in puuids])
        return dict(zip(puuids, recents))

    async def lookup(self, puuids: list[str], content: dict) -> tuple[dict, dict, str, dict]:
        names, mmrs, season = await self.lookup_basic(puuids)
        return names, mmrs, season, await self.lookup_recent(puuids, content)


def shard_from_env() -> str:
    return os.getenv('VALO_SHARD', 'ap').strip().lower() or 'ap'
