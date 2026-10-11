"""Riot 小帳 cookie 登入：不用帳密、不用驗證碼。

流程(跟市面 rank 工具一樣)：
  1. 第一次：經 ssh 讀 PC 上 Riot Client 的 RiotGamesPrivateSettings.yaml 裡的 ssid cookie(只讀)
  2. 之後：GET auth.riotgames.com/authorize 帶 ssid → 302 到 playvalorant.com/opt_in#access_token=…
     回應會給新的 ssid → 存回 data/riot_alt_cookie.json(權限 600)，這樣 cookie 一直續命
  3. access token → entitlements token(entitlements.auth.riotgames.com)、puuid(userinfo)、分區(riot-geo)
cookie 檔只放小帳的 ssid；主帳的 lockfile/token 流程不變。
"""
import asyncio
import json
import logging
import os
import re
import time
from typing import Optional
from urllib.parse import parse_qs, urlparse

import aiohttp

log = logging.getLogger(__name__)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COOKIE_FILE = os.path.join(ROOT, 'data', 'riot_alt_cookie.json')
RGPS = r'%LOCALAPPDATA%\Riot Games\Riot Client\Data\RiotGamesPrivateSettings.yaml'
AUTHORIZE = ('https://auth.riotgames.com/authorize?redirect_uri=https%3A%2F%2Fplayvalorant.com%2Fopt_in'
             '&client_id=play-valorant-web-prod&response_type=token%20id_token&nonce=1&scope=account%20openid')
UA = 'RiotClient/91.0.2.1870.3774 rso-auth (Windows;10;;Professional, x64)'
TOKEN_URL = 'https://auth.riotgames.com/token'
TOKEN_TTL = 50 * 60


def _load() -> dict:
    try:
        with open(COOKIE_FILE, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save(d: dict):
    os.makedirs(os.path.dirname(COOKIE_FILE), exist_ok=True)
    tmp = COOKIE_FILE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(d, f)
    os.chmod(tmp, 0o600)
    os.replace(tmp, COOKIE_FILE)


class CookieAuth:
    """小帳的 token；用法跟 RiotLink.token() 一樣，拿 access/entitlement/puuid/shard。"""

    def __init__(self, http: aiohttp.ClientSession, pc_host: str = 'pc'):
        self.http = http
        self.pc_host = pc_host
        self.access = ''
        self.entitlement = ''
        self.puuid = ''
        self.shard = ''
        self._at = 0.0
        self.fail_reason = ''
        self._retry_at = 0.0   # 失敗後 10 分鐘內不再打 auth 伺服器(每個查詢都會問一次)

    @property
    def ok(self) -> bool:
        return bool(self.access) and time.time() - self._at < TOKEN_TTL

    async def import_from_pc(self) -> bool:
        """從 PC 的 Riot Client 設定檔讀 ssid(只讀)。要在小帳登入 Riot Client 的時候做。"""
        proc = await asyncio.create_subprocess_exec(
            'ssh', '-o', 'BatchMode=yes', self.pc_host, f'type "{RGPS}"',
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), 15)
        except asyncio.TimeoutError:
            proc.kill()
            return False
        text = out.decode('utf-8', errors='replace')
        # yaml 裡是 cookies 清單：- domain: … name: ssid … value: …
        ssid = ''
        for block in re.split(r'\n\s*-\s+', text):
            if re.search(r'name:\s*ssid\b', block):
                m = re.search(r'value:\s*"?([^"\n]+)"?', block)
                if m:
                    ssid = m.group(1).strip()
        if not ssid:
            self.fail_reason = '設定檔裡沒有 ssid（Riot Client 沒登入？）'
            return False
        _save({'ssid': ssid, 'imported_at': time.time()})
        self.access = ''
        return True

    async def login(self, force: bool = False) -> bool:
        if self.ok and not force:
            return True
        if time.time() < self._retry_at and not force:
            return False
        self._retry_at = time.time() + 600
        d = _load()
        if not d.get('ssid') and not d.get('refresh_token'):
            self.fail_reason = '沒有 cookie 檔'
            return False
        try:
            id_token = ''
            if d.get('refresh_token'):
                # 新版 Riot Client(2025+)：OAuth refresh token；回應會給新的 refresh token → 存回去(舊的作廢)
                async with self.http.post(TOKEN_URL, headers={'User-Agent': UA},
                                          data={'grant_type': 'refresh_token', 'refresh_token': d['refresh_token'],
                                                'client_id': 'riot-client'},
                                          timeout=aiohttp.ClientTimeout(total=20)) as r:
                    body = await r.json(content_type=None)
                    if r.status != 200 or not body.get('access_token'):
                        self.fail_reason = f"refresh token 失效（HTTP {r.status} {body.get('error', '')}）"
                        log.warning(f'🔫 小帳登入失敗: {self.fail_reason}')
                        return False
                self.access = body['access_token']
                id_token = body.get('id_token') or d.get('id_token', '')
                if body.get('refresh_token'):
                    d['refresh_token'], d['refreshed_at'] = body['refresh_token'], time.time()
                    _save(d)
            else:
                async with self.http.get(AUTHORIZE, headers={'User-Agent': UA}, cookies={'ssid': d['ssid']},
                                         allow_redirects=False, timeout=aiohttp.ClientTimeout(total=20)) as r:
                    loc = r.headers.get('Location', '')
                    new_ssid = r.cookies.get('ssid')
                    if r.status not in (302, 303) or 'access_token' not in loc:
                        self.fail_reason = f'cookie 失效或被擋（HTTP {r.status}）'
                        log.warning(f'🔫 小帳 cookie 登入失敗: {self.fail_reason}')
                        return False
                frag = parse_qs(urlparse(loc).fragment)
                self.access = frag['access_token'][0]
                id_token = (frag.get('id_token') or [''])[0]
                if new_ssid:   # 續命：存新的 ssid
                    d['ssid'] = new_ssid.value
                    d['refreshed_at'] = time.time()
                    _save(d)
            hdr = {'Authorization': f'Bearer {self.access}', 'User-Agent': UA}
            async with self.http.post('https://entitlements.auth.riotgames.com/api/token/v1', headers=hdr, json={},
                                      timeout=aiohttp.ClientTimeout(total=20)) as r:
                self.entitlement = (await r.json(content_type=None)).get('entitlements_token', '')
            async with self.http.post('https://auth.riotgames.com/userinfo', headers=hdr, json={},
                                      timeout=aiohttp.ClientTimeout(total=20)) as r:
                self.puuid = (await r.json(content_type=None)).get('sub', '')
            async with self.http.put('https://riot-geo.pas.si.riotgames.com/pas/v1/product/valorant', headers=hdr,
                                     json={'id_token': id_token}, timeout=aiohttp.ClientTimeout(total=20)) as r:
                geo = await r.json(content_type=None) if r.status == 200 else {}
                self.shard = ((geo.get('affinities') or {}).get('live') or '').lower()
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, KeyError) as e:
            self.fail_reason = f'{e!r}'
            log.warning(f'🔫 小帳登入例外: {e!r}')
            return False
        if not (self.access and self.entitlement and self.puuid):
            self.fail_reason = 'token 不完整'
            return False
        self._at = time.time()
        self.fail_reason = ''
        return True
