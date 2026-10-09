"""Riot 官方 API(RIOT_API_KEY)：帳號、牌位、英雄熟練度、對戰紀錄。

台服實測(2026-10)：牌位、熟練度、一般/積分對戰紀錄可用；大混戰(2400)對戰紀錄查不到、TFT/Valorant 403。
速率限制：照回應的 X-App-Rate-Limit(個人 key 是 20/1s、100/120s)用九成，429 時照 Retry-After 等。
注意：API 的 puuid 跟客戶端(LCU)的不同，要用 名字#tag 換。
"""
import asyncio
import logging
import os
import time
from collections import deque
from typing import Optional
from urllib.parse import quote

import aiohttp

from cogs.lol_parse import rank

log = logging.getLogger(__name__)

PLATFORM = os.getenv('RIOT_PLATFORM', 'tw2')
REGIONAL = os.getenv('RIOT_REGIONAL', 'sea')     # match-v5
ACCOUNT_REGION = os.getenv('RIOT_ACCOUNT_REGION', 'asia')
SAFETY = 0.9
CACHE_MAX = 2000


class RateLimiter:
    def __init__(self, limits: list[tuple[int, float]]):
        self._lock = asyncio.Lock()
        self.set_limits(limits)

    def set_limits(self, limits: list[tuple[int, float]]):
        self.limits = [(max(1, int(n * SAFETY)), w) for n, w in limits]
        self.hits = [deque() for _ in self.limits]
        self.blocked_until = 0.0

    async def acquire(self):
        async with self._lock:
            while True:
                now = time.monotonic()
                wait = self.blocked_until - now
                for (n, w), q in zip(self.limits, self.hits):
                    while q and now - q[0] >= w:
                        q.popleft()
                    if len(q) >= n:
                        wait = max(wait, w - (now - q[0]))
                if wait <= 0:
                    for q in self.hits:
                        q.append(now)
                    return
                await asyncio.sleep(wait + 0.05)


def _parse_limits(header: str) -> list[tuple[int, float]]:
    out = []
    for part in (header or '').split(','):
        n, _, w = part.partition(':')
        if n.strip().isdigit() and w.strip().isdigit():
            out.append((int(n), float(w)))
    return out


class RiotApi:
    def __init__(self, key: str, http: aiohttp.ClientSession):
        self.key = key
        self.http = http
        self.rl = RateLimiter([(20, 1), (100, 120)])
        self._limits_hdr = ''
        self._cache: dict[str, tuple[float, object]] = {}
        self.disabled_reason = ''

    @property
    def enabled(self) -> bool:
        return bool(self.key) and not self.disabled_reason

    async def get(self, url: str, ttl: float):
        """回 JSON；404 回 None(也快取)；其他錯誤回 None 不快取。"""
        if not self.enabled:
            return None
        hit = self._cache.get(url)
        if hit and time.time() < hit[0]:
            return hit[1]
        for attempt in range(2):
            await self.rl.acquire()
            try:
                async with self.http.get(url, headers={'X-Riot-Token': self.key},
                                         timeout=aiohttp.ClientTimeout(total=15)) as r:
                    hdr = r.headers.get('X-App-Rate-Limit', '')
                    if hdr and hdr != self._limits_hdr and _parse_limits(hdr):
                        self._limits_hdr = hdr
                        self.rl.set_limits(_parse_limits(hdr))
                    if r.status == 429:
                        retry = float(r.headers.get('Retry-After', '10'))
                        self.rl.blocked_until = time.monotonic() + retry
                        log.warning(f'🎮 Riot API 429，等 {retry:.0f} 秒')
                        continue
                    if r.status in (401, 403) and '/riot/account/' in url:
                        # 帳號 API 一定有權限 → 401/403 代表 key 失效或過期
                        self.disabled_reason = f'key 失效({r.status})'
                        log.error(f'🎮 Riot API key 失效({r.status})，停用 API')
                        return None
                    if r.status == 404:
                        self._store(url, None, min(ttl, 300))
                        return None
                    if r.status != 200:
                        return None
                    data = await r.json(content_type=None)
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                log.warning(f'🎮 Riot API 失敗 {url.split("?")[0][-60:]}: {e!r}')
                return None
            self._store(url, data, ttl)
            return data
        return None

    def _store(self, url, data, ttl):
        self._cache[url] = (time.time() + ttl, data)
        if len(self._cache) > CACHE_MAX:
            now = time.time()
            for k in [k for k, (exp, _) in self._cache.items() if exp < now]:
                self._cache.pop(k, None)
            while len(self._cache) > CACHE_MAX:
                self._cache.pop(next(iter(self._cache)))

    # ── 端點 ──
    async def account(self, name: str, tag: str) -> Optional[dict]:
        if not name or not tag:
            return None
        return await self.get(f'https://{ACCOUNT_REGION}.api.riotgames.com/riot/account/v1/accounts/by-riot-id/'
                              f'{quote(name, safe="")}/{quote(tag, safe="")}', 86400)

    async def league(self, puuid: str) -> Optional[list]:
        return await self.get(f'https://{PLATFORM}.api.riotgames.com/lol/league/v4/entries/by-puuid/{puuid}', 600)

    async def summoner(self, puuid: str) -> Optional[dict]:
        return await self.get(f'https://{PLATFORM}.api.riotgames.com/lol/summoner/v4/summoners/by-puuid/{puuid}', 3600)

    async def mastery(self, puuid: str, champ_id: int) -> Optional[dict]:
        return await self.get(f'https://{PLATFORM}.api.riotgames.com/lol/champion-mastery/v4/champion-masteries/'
                              f'by-puuid/{puuid}/by-champion/{int(champ_id)}', 3600)

    async def top_mastery(self, puuid: str, count: int = 3) -> Optional[list]:
        return await self.get(f'https://{PLATFORM}.api.riotgames.com/lol/champion-mastery/v4/champion-masteries/'
                              f'by-puuid/{puuid}/top?count={int(count)}', 3600)

    async def match_ids(self, puuid: str, count: int = 5) -> Optional[list]:
        return await self.get(f'https://{REGIONAL}.api.riotgames.com/lol/match/v5/matches/by-puuid/{puuid}/ids'
                              f'?count={int(count)}', 300)

    async def match(self, match_id: str) -> Optional[dict]:
        return await self.get(f'https://{REGIONAL}.api.riotgames.com/lol/match/v5/matches/{quote(match_id)}', 86400)


def league_to_ranks(entries: Optional[list]) -> dict:
    """league-v4 → {'solo': {...}|None, 'flex': {...}|None}，格式跟 lol_parse._rank 一樣。"""
    out = {'solo': None, 'flex': None}
    for e in entries or []:
        slot = {'RANKED_SOLO_5x5': 'solo', 'RANKED_FLEX_SR': 'flex'}.get(e.get('queueType'))
        if slot:
            out[slot] = rank(e, 'rank')
    return out
