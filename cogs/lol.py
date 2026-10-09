"""/lol：在語音頻道開 Discord Activity，顯示 LoL 選角 / 遊戲中 / 結算資訊。

架構：
  bot 背景一直輪詢玩家 PC(經 SSH，`LOL_SSH_HOST`，預設 ~/.ssh/config 的 `pc`)：
    - LCU(客戶端 API，lockfile 給 port/密碼)：gameflow 階段、選角、結算、玩家等級/牌位/對戰紀錄
    - Live Client Data API(127.0.0.1:2999)：遊戲中 10 人的英雄、裝備、KDA、事件
  只送 curl GET，不會在 PC 上寫任何東西。
  網頁與 /lyrics 共用 LyricsCog 的 aiohttp 伺服器(/lol/* 轉給這裡)；圖示從 Data Dragon /
  CommunityDragon 抓下來快取在 data/lol_assets/。
畫面：選角 → 讀取/遊戲中 → 結算，結算畫面一直留著直到下一場進選角。
"""
import asyncio
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import Optional

import aiohttp
import discord
from aiohttp import web
from discord import app_commands
from discord.ext import commands

from cogs.riot_api import RiotApi, league_to_ranks
from cogs.lol_parse import (EOG_PHASES, INGAME_PHASES, parse_champselect, parse_eog, parse_live,
                            parse_profile, queue_name, riot_id, session_puuids)

log = logging.getLogger(__name__)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSET_DIR = os.path.join(ROOT, 'data', 'lol_assets')
LAST_FILE = os.path.join(ROOT, 'data', 'lol_last.json')

SSH_HOST = os.getenv('LOL_SSH_HOST', 'pc')
LOL_DIR = os.getenv('LOL_INSTALL_DIR', r'D:\Riot Games\League of Legends')

POLL_IDLE = 5        # 秒：沒在遊戲時看 gameflow 的間隔
POLL_ACTIVE = 2      # 秒：選角 / 遊戲中
POLL_OFFLINE = 15    # 秒：PC 連不上或客戶端沒開
PROFILE_TTL = 600    # 秒：玩家資料快取
HISTORY_COUNT = 20   # 近期勝率看幾場

DDRAGON = 'https://ddragon.leagueoflegends.com'
CDRAGON = 'https://raw.communitydragon.org/latest'
CD_GAME_DATA = f'{CDRAGON}/plugins/rcp-be-lol-game-data/global'
SAFE_PATH = re.compile(r'^/[A-Za-z0-9/_\-?=&.]+$')   # 送到 Windows cmd 的路徑只允許這些字元
SAFE_FILE = re.compile(r'^[A-Za-z0-9_\-]+$')


class PcLink:
    """經 SSH 在玩家 PC 上跑 curl GET。所有指令都是固定格式，只讀不寫。"""

    def __init__(self, host: str, lol_dir: str):
        self.host = host
        self.lol_dir = lol_dir
        self.creds: Optional[tuple[str, str]] = None   # (port, password)

    async def _run(self, remote: str, timeout: float = 8) -> Optional[bytes]:
        proc = await asyncio.create_subprocess_exec(
            'ssh', '-o', 'BatchMode=yes', self.host, remote,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return None
        return out if proc.returncode == 0 else None

    async def read_lockfile(self) -> bool:
        out = await self._run(f'type "{self.lol_dir}\\lockfile"')
        parts = out.decode(errors='replace').strip().split(':') if out else []
        if len(parts) >= 5 and parts[2].isdigit():
            self.creds = (parts[2], parts[3])
            return True
        self.creds = None
        return False

    async def _json(self, url: str, auth: str = '', timeout: float = 8):
        out = await self._run(f'curl -s -k -m {max(1, int(timeout) - 2)} {auth}"{url}"', timeout)
        if not out:
            return None
        try:
            return json.loads(out)
        except ValueError:
            return None

    async def lcu(self, path: str, timeout: float = 8):
        if not SAFE_PATH.match(path):
            raise ValueError(f'不安全的 LCU 路徑: {path}')
        if not self.creds and not await self.read_lockfile():
            return None
        port, pw = self.creds
        if not re.match(r'^[\w\-]+$', pw):
            return None
        d = await self._json(f'https://127.0.0.1:{port}{path}', f'-u riot:{pw} ', timeout)
        if isinstance(d, dict) and d.get('httpStatus') == 401:   # 客戶端重開過，密碼換了
            self.creds = None
            return None
        return d

    async def live(self):
        return await self._json('https://127.0.0.1:2999/liveclientdata/allgamedata', timeout=6)


class Static:
    """Data Dragon / CommunityDragon 的靜態資料與圖示(下載後存在 data/lol_assets/)。"""

    def __init__(self):
        self.version = ''
        self.data = {'champIdToKey': {}, 'champKeyToId': {}, 'champNames': {}, 'spellKeys': {},
                     'spellIdToKey': {}, 'augments': {}}
        self._http: Optional[aiohttp.ClientSession] = None
        self._locks: dict[str, asyncio.Lock] = {}

    async def start(self, http: aiohttp.ClientSession):
        self._http = http
        os.makedirs(ASSET_DIR, exist_ok=True)
        try:
            await asyncio.wait_for(self.refresh(), 30)
        except Exception as e:
            log.warning(f'🎮 LoL 靜態資料下載失敗，改用快取: {e!r}')
            self._load_cache()

    def _load_cache(self):
        p = os.path.join(ASSET_DIR, 'static.json')
        try:
            with open(p, encoding='utf-8') as f:
                c = json.load(f)
            self.version, self.data = c['version'], c['data']
        except (OSError, ValueError, KeyError) as e:
            log.warning(f'🎮 LoL 靜態資料快取不能用，圖示/名稱會缺: {e!r}')

    async def _get_json(self, url: str):
        async with self._http.get(url) as r:
            r.raise_for_status()
            return await r.json(content_type=None)

    async def refresh(self):
        versions = await self._get_json(f'{DDRAGON}/api/versions.json')
        ver = versions[0]
        champs = (await self._get_json(f'{DDRAGON}/cdn/{ver}/data/zh_TW/champion.json'))['data']
        spells = (await self._get_json(f'{DDRAGON}/cdn/{ver}/data/zh_TW/summoner.json'))['data']
        augs = await self._get_json(f'{CD_GAME_DATA}/zh_tw/v1/cherry-augments.json')
        d = {
            'champIdToKey': {c['key']: c['id'] for c in champs.values()},
            'champKeyToId': {c['id'].lower(): int(c['key']) for c in champs.values()},
            'champNames': {c['key']: c['name'] for c in champs.values()},
            'spellKeys': {s['id']: s['name'] for s in spells.values()},
            'spellIdToKey': {s['key']: s['id'] for s in spells.values()},
            'augments': {str(a['id']): {'name': a.get('nameTRA', ''), 'rarity': a.get('rarity', ''),
                                        'icon': a.get('augmentSmallIconPath', '')} for a in augs},
        }
        self.version, self.data = ver, d
        path = os.path.join(ASSET_DIR, 'static.json')
        with open(path + '.tmp', 'w', encoding='utf-8') as f:
            json.dump({'version': ver, 'data': d}, f, ensure_ascii=False)
        os.replace(path + '.tmp', path)
        log.info(f'🎮 LoL 靜態資料 {ver}：{len(champs)} 隻英雄、{len(augs)} 個增幅')

    def public(self) -> dict:
        d = self.data
        return {'version': self.version, 'champions': d['champNames'], 'champKeys': d['champIdToKey'],
                'spells': d['spellIdToKey'],
                'augments': {k: {'name': v['name'], 'rarity': v['rarity']} for k, v in d['augments'].items()}}

    def source_url(self, kind: str, ident: str) -> Optional[str]:
        v, d = self.version, self.data
        if kind == 'champ':
            key = d['champIdToKey'].get(ident) or ident
            return f'{DDRAGON}/cdn/{v}/img/champion/{key}.png'
        if kind == 'item':
            return f'{DDRAGON}/cdn/{v}/img/item/{ident}.png'
        if kind == 'spell':
            key = d['spellIdToKey'].get(ident, ident)
            return f'{DDRAGON}/cdn/{v}/img/spell/{key}.png'
        if kind == 'icon':
            return f'{DDRAGON}/cdn/{v}/img/profileicon/{ident}.png'
        if kind == 'aug':
            path = (d['augments'].get(ident) or {}).get('icon', '')
            if not path:
                return None
            # /lol-game-data/assets/ASSETS/... → CommunityDragon 的小寫路徑
            return f'{CD_GAME_DATA}/default/' + path.replace('/lol-game-data/assets/', '').lower()
        if kind == 'tier':
            return f'{CDRAGON}/plugins/rcp-fe-lol-static-assets/global/default/images/ranked-mini-crests/{ident}.svg'
        return None

    async def asset(self, kind: str, ident: str) -> Optional[str]:
        """回傳本機快取檔路徑，沒有就下載。"""
        if not SAFE_FILE.match(ident):
            return None
        ext = 'svg' if kind == 'tier' else 'png'
        ver_dir = 'common' if kind in ('aug', 'tier', 'icon') else self.version or 'unknown'
        path = os.path.join(ASSET_DIR, kind, ver_dir, f'{ident}.{ext}')
        if os.path.exists(path):
            return path
        miss = path + '.404'
        if os.path.exists(miss) and time.time() - os.path.getmtime(miss) < 86400:
            return None
        url = self.source_url(kind, ident.lower() if kind == 'tier' else ident)
        if not url:
            return None
        lock = self._locks.setdefault(path, asyncio.Lock())
        async with lock:
            if os.path.exists(path):
                return path
            os.makedirs(os.path.dirname(path), exist_ok=True)
            try:
                async with self._http.get(url) as r:
                    if r.status == 404:
                        open(miss, 'w').close()
                    if r.status != 200:
                        return None
                    body = await r.read()
            except (aiohttp.ClientError, asyncio.TimeoutError):
                return None
            with open(path + '.tmp', 'wb') as f:
                f.write(body)
            os.replace(path + '.tmp', path)
            return path

    async def prefetch(self):
        """先把英雄、召喚師技能、裝備圖示抓下來(第一次開畫面就不用等)。"""
        try:
            items = (await self._get_json(f'{DDRAGON}/cdn/{self.version}/data/zh_TW/item.json'))['data']
        except Exception as e:
            log.warning(f'🎮 裝備清單下載失敗: {e!r}')
            items = {}
        jobs = ([('champ', k) for k in self.data['champIdToKey']] +
                [('spell', k) for k in self.data['spellIdToKey']] +
                [('item', k) for k in items] +
                [('aug', k) for k in self.data['augments']] +
                [('tier', t.lower()) for t in ('IRON', 'BRONZE', 'SILVER', 'GOLD', 'PLATINUM', 'EMERALD',
                                               'DIAMOND', 'MASTER', 'GRANDMASTER', 'CHALLENGER')])
        sem = asyncio.Semaphore(8)

        async def one(kind, ident):
            async with sem:
                await self.asset(kind, ident)

        await asyncio.gather(*(one(k, i) for k, i in jobs), return_exceptions=True)
        log.info(f'🎮 LoL 圖示預載完成：{len(jobs)} 個')


class LolCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.pc = PcLink(SSH_HOST, LOL_DIR)
        self.static = Static()
        self._http: Optional[aiohttp.ClientSession] = None
        self._task: Optional[asyncio.Task] = None
        self._prefetch: Optional[asyncio.Task] = None
        self.version = 0                 # state 每變一次 +1，WebSocket 用來判斷要不要推
        self._changed = asyncio.Event()
        self.state: dict = {'phase': 'offline', 'sub': '啟動中', 'queue': None, 'me': None,
                            'champselect': None, 'live': None, 'eog': None, 'profiles': {}}
        self._profiles: dict[str, tuple[float, dict]] = {}
        self._profile_jobs: dict[str, asyncio.Task] = {}
        self._name_to_puuid: dict[str, str] = {}
        self.api: Optional[RiotApi] = None
        self._api_puuid: dict[str, str] = {}            # LCU puuid → API puuid
        self._mastery: dict[tuple[str, int], tuple[float, Optional[dict]]] = {}
        self._mastery_jobs: dict[tuple[str, int], asyncio.Task] = {}
        self._load_last()

    # ── lifecycle ──
    async def cog_load(self):
        self._http = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20),
                                           headers={'User-Agent': 'heart-discordbot-lol/1.0'})
        await self.static.start(self._http)
        key = os.getenv('RIOT_API_KEY', '')
        if key:
            self.api = RiotApi(key, self._http)
            log.info('🎮 Riot API 已啟用(牌位、熟練度、/lol 查人)')
        self._prefetch = asyncio.ensure_future(self.static.prefetch())
        self._task = asyncio.ensure_future(self._loop())

    async def cog_unload(self):
        for t in (self._task, self._prefetch, *self._profile_jobs.values(), *self._mastery_jobs.values()):
            if t:
                t.cancel()
        if self._http:
            await self._http.close()

    def _load_last(self):
        try:
            with open(LAST_FILE, encoding='utf-8') as f:
                self.state['eog'] = json.load(f)
        except (OSError, ValueError):
            pass

    def _save_last(self, eog: dict):
        try:
            with open(LAST_FILE + '.tmp', 'w', encoding='utf-8') as f:
                json.dump(eog, f, ensure_ascii=False)
            os.replace(LAST_FILE + '.tmp', LAST_FILE)
        except OSError as e:
            log.warning(f'🎮 結算存檔失敗: {e!r}')

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

    # ── 玩家資料 ──
    def _want_profiles(self, puuids: list[str], queue_id: Optional[int]):
        now = time.time()
        for pu in puuids:
            if not pu:
                continue
            hit = self._profiles.get(pu)
            if hit and now - hit[0] < PROFILE_TTL and hit[1].get('_q') == queue_id:
                continue
            if pu in self._profile_jobs:
                continue
            self._profile_jobs[pu] = asyncio.ensure_future(self._fetch_profile(pu, queue_id))

    async def _fetch_profile(self, puuid: str, queue_id: Optional[int]):
        try:
            if not re.match(r'^[0-9a-f\-]{36,}$', puuid):
                return
            # 別人的對戰紀錄第一次查客戶端要向伺服器拿，常要好幾秒 → 給長一點
            summ, ranked, hist = await asyncio.gather(
                self.pc.lcu(f'/lol-summoner/v2/summoners/puuid/{puuid}'),
                self.pc.lcu(f'/lol-ranked/v1/ranked-stats/{puuid}'),
                self.pc.lcu(f'/lol-match-history/v1/products/lol/{puuid}/matches'
                            f'?begIndex=0&endIndex={HISTORY_COUNT - 1}', timeout=25))
            prof = parse_profile(summ, ranked, hist, queue_id)
            prof['_q'] = queue_id
            prof['rankSrc'] = 'client'
            # 牌位以 Riot API 為準(客戶端給別人的敗場數常是 0)；查不到才用客戶端的
            if self.api and self.api.enabled and prof['name']:
                api_pu = await self._api_puuid_for(puuid, prof['name'], prof['tag'])
                entries = await self.api.league(api_pu) if api_pu else None
                if entries is not None:
                    # 逐欄比：API 有就用 API；API 空的(例如定位賽中)保留客戶端的
                    for slot, r in league_to_ranks(entries).items():
                        if r:
                            prof[slot] = r
                    prof['rankSrc'] = 'api'
            # 對戰紀錄沒拿到 → 快取時間設短，下一輪重試
            self._profiles[puuid] = (time.time() - (PROFILE_TTL - 30 if hist is None else 0), prof)
            if prof['name']:
                self._name_to_puuid[riot_id(prof['name'], prof['tag']).lower()] = puuid
            self._publish_profiles()
        except Exception as e:
            log.warning(f'🎮 玩家資料失敗 {puuid[:8]}: {e!r}')
        finally:
            self._profile_jobs.pop(puuid, None)

    async def _api_puuid_for(self, lcu_puuid: str, name: str, tag: str) -> Optional[str]:
        if lcu_puuid in self._api_puuid:
            return self._api_puuid[lcu_puuid]
        acc = await self.api.account(name, tag)
        if acc and acc.get('puuid'):
            self._api_puuid[lcu_puuid] = acc['puuid']
            return acc['puuid']
        return None

    def _want_mastery(self, pairs: list[tuple[Optional[str], int]]):
        if not (self.api and self.api.enabled):
            return
        now = time.time()
        for pu, cid in pairs:
            if not pu or not cid:
                continue
            key = (pu, int(cid))
            hit = self._mastery.get(key)
            if (hit and now < hit[0]) or key in self._mastery_jobs:
                continue
            prof = self._profiles.get(pu, (0, {}))[1]
            if not prof.get('name'):
                continue   # 還沒拿到名字 → 下一輪再來
            self._mastery_jobs[key] = asyncio.ensure_future(self._fetch_mastery(key, prof['name'], prof['tag']))

    async def _fetch_mastery(self, key: tuple[str, int], name: str, tag: str):
        try:
            api_pu = await self._api_puuid_for(key[0], name, tag)
            m = await self.api.mastery(api_pu, key[1]) if api_pu else None
            val = {'level': m.get('championLevel', 0), 'points': m.get('championPoints', 0)} if m else None
            # 沒練過(404)也記下來，一小時內不再查
            self._mastery[key] = (time.time() + (3600 if api_pu else 120), val)
            self._publish_profiles()
        except Exception as e:
            log.warning(f'🎮 熟練度失敗: {e!r}')
        finally:
            self._mastery_jobs.pop(key, None)

    def _publish_profiles(self):
        want = set()
        st = self.state
        if st.get('champselect'):
            want |= {p['puuid'] for p in st['champselect']['team'] if p.get('puuid')}
        if st.get('live'):
            want |= {p['puuid'] for p in st['live']['players'] if p.get('puuid')}
        want |= set(st.get('_session_puuids') or [])
        profs = {pu: {k: v for k, v in self._profiles[pu][1].items() if not k.startswith('_')}
                 for pu in want if pu in self._profiles}
        for (pu, cid), (_, val) in self._mastery.items():
            if pu in profs and val:
                profs[pu].setdefault('mastery', {})[str(cid)] = val
        self._set(profiles=profs)

    # ── 輪詢 ──
    async def _loop(self):
        await self.bot.wait_until_ready()
        last_phase = None
        while True:
            try:
                delay = await self._tick(last_phase)
                last_phase = self.state.get('_raw_phase')
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning(f'🎮 LoL 輪詢例外: {e!r}')
                delay = POLL_OFFLINE
            await asyncio.sleep(delay)

    async def _tick(self, last_phase: Optional[str]) -> float:
        phase = await self.pc.lcu('/lol-gameflow/v1/gameflow-phase')
        if not isinstance(phase, str):
            # lockfile 讀不到 = 客戶端沒開；ssh 失敗 = PC 離線
            ok = await self.pc._run('echo ok', timeout=6)
            self._set(phase='eog' if self.state.get('eog') else 'offline',
                      sub='PC 離線' if not ok else '客戶端未開啟', live=None, champselect=None)
            self.state['_raw_phase'] = None
            return POLL_OFFLINE
        self.state['_raw_phase'] = phase

        if phase != last_phase:
            sess = await self.pc.lcu('/lol-gameflow/v1/session') or {}
            q = (sess.get('gameData') or {}).get('queue') or {}
            qid = q.get('id') or None
            self._set(queue={'id': qid, 'name': queue_name(qid, q.get('description', ''))} if qid else None)
            if phase in INGAME_PHASES:
                self.state['_session_puuids'] = session_puuids(sess)
                self._want_profiles(self.state['_session_puuids'], qid)
            if self.state.get('me') is None:
                me = await self.pc.lcu('/lol-summoner/v1/current-summoner') or {}
                self._set(me=me.get('puuid'))
        qid = (self.state.get('queue') or {}).get('id')

        if phase == 'ChampSelect':
            sess = await self.pc.lcu('/lol-champ-select/v1/session')
            if isinstance(sess, dict) and sess.get('myTeam') is not None:
                cs = parse_champselect(sess, self.state.get('me') or '')
                self._set(phase='champselect', sub='選角中', champselect=cs, live=None)
                self._want_profiles([p['puuid'] for p in cs['team']], qid)
                self._want_mastery([(p['puuid'], p['championId']) for p in cs['team']])
                self._publish_profiles()
            return POLL_ACTIVE

        if phase in INGAME_PHASES:
            d = await self.pc.live()
            if isinstance(d, dict) and d.get('allPlayers'):
                live = parse_live(d, self.static.data, self._name_to_puuid)
                self._set(phase='ingame', sub='遊戲中', live=live, champselect=None)
                self._want_mastery([(p['puuid'], p['champ']['id']) for p in live['players']])
            else:
                self._set(phase='ingame', sub='讀取中', champselect=None,
                          live=self.state.get('live') if phase == 'Reconnect' else None)
            self._publish_profiles()
            return POLL_ACTIVE

        if phase in EOG_PHASES:
            raw = await self.pc.lcu('/lol-end-of-game/v1/eog-stats-block')
            if isinstance(raw, dict) and raw.get('teams'):
                eog = parse_eog(raw, self.static.data)
                eog['queue'] = self.state.get('queue')
                if (self.state.get('eog') or {}).get('gameId') != eog['gameId']:
                    self._save_last(eog)
                self._set(phase='eog', sub='結算', eog=eog, live=None, champselect=None)
            return POLL_ACTIVE

        # Lobby / Matchmaking / ReadyCheck / None：有上一場結算就繼續顯示
        sub = {'Lobby': '房間中', 'Matchmaking': '配對中', 'ReadyCheck': '找到對戰',
               'None': '待機中'}.get(phase, phase)
        self._set(phase='eog' if self.state.get('eog') else 'idle', sub=sub, live=None, champselect=None)
        self.state.pop('_session_puuids', None)
        return POLL_ACTIVE if phase in ('Matchmaking', 'ReadyCheck') else POLL_IDLE

    def public_state(self) -> dict:
        return {k: v for k, v in self.state.items() if not k.startswith('_')}

    # ── HTTP(由 LyricsCog 的伺服器轉進來：/lol/...) ──
    async def handle(self, request: web.Request, tail: str):
        if tail == 'ws':
            return await self._h_ws(request)
        if tail == 'state':
            return web.json_response(self.public_state())
        if tail == 'static.json':
            return web.json_response(self.static.public())
        m = re.match(r'^img/(champ|item|spell|icon|aug|tier)/([A-Za-z0-9_\-]+)\.(png|svg)$', tail)
        if m:
            path = await self.static.asset(m.group(1), m.group(2))
            if not path:
                raise web.HTTPNotFound()
            return web.FileResponse(path, headers={'Cache-Control': 'max-age=86400'})
        raise web.HTTPNotFound()

    async def _h_ws(self, request):
        ws = web.WebSocketResponse(heartbeat=20)
        await ws.prepare(request)
        reader = asyncio.ensure_future(self._drain(ws))
        sent = -1
        try:
            while not ws.closed:
                ev = self._changed          # 先拿 Event 再比版本：送出期間的變動不會漏
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
            log.warning(f'🎮 LoL WebSocket 例外: {e!r}')
        finally:
            reader.cancel()
        return ws

    @staticmethod
    async def _drain(ws: web.WebSocketResponse):
        async for _ in ws:
            pass

    # ── slash command ──
    @app_commands.command(name='lol', description='開啟 LoL 對戰資訊畫面；填玩家（名字#tag）則改成查這個人的戰績')
    @app_commands.describe(player='要查的玩家，例如 名字#1234（不填 = 在語音頻道開即時畫面）')
    async def lol(self, interaction: discord.Interaction, player: Optional[str] = None):
        if player:
            await self._lookup(interaction, player)
            return
        if not interaction.user.voice:
            await interaction.response.send_message('❌ 請先加入語音頻道再開 LoL 畫面', ephemeral=True)
            return
        set_mode = getattr(self.bot, 'set_activity_mode', None)
        if set_mode:
            set_mode(interaction, 'lol')
        # Activity 已經開著 → 不再叫 Discord 開(會留下一行空的「使用了 /lol」)，畫面 3 秒內自己切
        if getattr(self.bot, 'activity_open', lambda i: False)(interaction):
            await interaction.response.send_message('✅ 已切換成 LoL 畫面', ephemeral=True)
            return
        try:
            await interaction.response.launch_activity()
        except discord.HTTPException as e:
            log.error(f'launch_activity 失敗: {e!r}')
            await interaction.response.send_message(
                '❌ 無法開啟 Activity。請確認 Developer Portal 已開啟 Activities 並設好 URL Mapping。',
                ephemeral=True)

    async def _lookup(self, interaction: discord.Interaction, player: str):
        """用 Riot API 查任何人(不需要 PC)：牌位、常用英雄、最近 5 場一般/積分。"""
        name, _, tag = player.strip().partition('#')
        if not name or not tag:
            await interaction.response.send_message('❌ 格式是 `名字#tag`，例如 `名字#1234`', ephemeral=True)
            return
        if not (self.api and self.api.enabled):
            await interaction.response.send_message('❌ 沒有設定可用的 Riot API key', ephemeral=True)
            return
        await interaction.response.defer(thinking=True)
        acc = await self.api.account(name.strip(), tag.strip())
        if not acc:
            await interaction.followup.send(f'❌ 找不到玩家 `{discord.utils.escape_markdown(player)}`')
            return
        pu = acc['puuid']
        summ, entries, top, ids = await asyncio.gather(
            self.api.summoner(pu), self.api.league(pu), self.api.top_mastery(pu, 3), self.api.match_ids(pu, 5))
        matches = await asyncio.gather(*(self.api.match(m) for m in ids or []))
        champ_names = self.static.data['champNames']

        def rank_text(r):
            if not r:
                return '未排名'
            wr = r['w'] * 100 // max(1, r['w'] + r['l'])
            return f"{TIER_ZH.get(r['tier'], r['tier'])} {r['division']} {r['lp']} LP（{r['w']}勝 {r['l']}敗，{wr}%）"

        ranks = league_to_ranks(entries)
        emb = discord.Embed(title=f"{acc.get('gameName', name)}#{acc.get('tagLine', tag)}",
                            color=0xC8AA6E,
                            description=f"等級 {(summ or {}).get('summonerLevel', '?')}")
        if summ and summ.get('profileIconId') is not None:
            emb.set_thumbnail(url=f'{DDRAGON}/cdn/{self.static.version}/img/profileicon/{summ["profileIconId"]}.png')
        emb.add_field(name='單/雙積分', value=rank_text(ranks['solo']), inline=False)
        emb.add_field(name='彈性積分', value=rank_text(ranks['flex']), inline=False)
        if top:
            emb.add_field(name='熟練度前三', inline=False, value='\n'.join(
                f"{champ_names.get(str(m['championId']), m['championId'])}　Lv{m.get('championLevel', 0)}"
                f"　{m.get('championPoints', 0):,} 點" for m in top))
        lines = []
        for m in matches:
            if not m:
                continue
            info = m.get('info') or {}
            me = next((x for x in info.get('participants') or [] if x.get('puuid') == pu), None)
            if not me:
                continue
            when = discord.utils.format_dt(
                datetime.fromtimestamp(info.get('gameCreation', 0) / 1000, tz=timezone.utc), 'R')
            lines.append(f"{'🟦 勝' if me.get('win') else '🟥 敗'}　{champ_names.get(str(me.get('championId')), me.get('championName'))}"
                         f"　{me.get('kills')}/{me.get('deaths')}/{me.get('assists')}"
                         f"　{queue_name(info.get('queueId'))}　{when}")
        emb.add_field(name='最近對戰（大混戰不在 Riot API 裡）', value='\n'.join(lines) or '沒有資料', inline=False)
        await interaction.followup.send(embed=emb)


TIER_ZH = {'IRON': '鐵牌', 'BRONZE': '銅牌', 'SILVER': '銀牌', 'GOLD': '金牌', 'PLATINUM': '白金',
           'EMERALD': '翡翠', 'DIAMOND': '鑽石', 'MASTER': '大師', 'GRANDMASTER': '宗師', 'CHALLENGER': '菁英'}


async def setup(bot: commands.Bot):
    await bot.add_cog(LolCog(bot))
