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
from cogs.lol_live_parse import (RECENT_N, fmt_length, live_text, mastery_text, match_live_players, parse_spectator,
                                 rank_text, recent_text)
from cogs.lol_parse import (BUILD_SLOTS, EOG_PHASES, eog_missing, enrich_eog, INGAME_PHASES, parse_champselect, parse_eog,
                            parse_history_eog, parse_live, parse_opgg_build, parse_profile, queue_name, riot_id, selection_champ_puuids,
                            session_champ_puuids, session_hidden_puuids, session_puuids)
from cogs.lol_timers import PoroFeed, objective_timers

log = logging.getLogger(__name__)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSET_DIR = os.path.join(ROOT, 'data', 'lol_assets')
LAST_FILE = os.path.join(ROOT, 'data', 'lol_last.json')
RAW_FILE = os.path.join(ROOT, 'data', 'lol_live_raw.json')
EOG_RAW_FILE = os.path.join(ROOT, 'data', 'lol_eog_raw.json')   # 最近一場結算原始 JSON(查欄位用)
BUILDS_FILE = os.path.join(ASSET_DIR, 'builds.json')

# 英雄常出裝：op.gg 公開的 MCP 端點(ARAM 統計；大混戰沒有單獨資料，出裝幾乎一樣)
OPGG_MCP = 'https://mcp-api.op.gg/mcp'
BUILD_TTL = 12 * 3600
BUILD_RETRY = 600

SSH_HOST = os.getenv('LOL_SSH_HOST', 'pc')
LOL_DIR = os.getenv('LOL_INSTALL_DIR', r'D:\Riot Games\League of Legends')

POLL_IDLE = 5        # 秒：沒在遊戲時看 gameflow 的間隔
POLL_ACTIVE = 2      # 秒：選角 / 遊戲中
POLL_OFFLINE = 15    # 秒：PC 連不上或客戶端沒開
POLL_PORO = 4        # 秒：遊戲中跟 PC 要 Porofessor log(補包、聊天室)的間隔
PORO_SLACK = 30      # 秒：這場開始前多早的 log 行就算上一場
PROFILE_TTL = 600    # 秒：玩家資料快取
HISTORY_COUNT = 100  # 近期勝率看幾場(客戶端最多只給最近 100 場，分頁參數無效)

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
        items = (await self._get_json(f'{DDRAGON}/cdn/{ver}/data/zh_TW/item.json'))['data']
        runes = await self._get_json(f'{DDRAGON}/cdn/{ver}/data/zh_TW/runesReforged.json')
        d = {
            # /lol-live 顯示主符文用
            'perkStyles': {str(r['id']): r['name'] for r in runes},
            'perkNames': {str(x['id']): x['name'] for r in runes for slot in r.get('slots', []) for x in slot.get('runes', [])},
            'champIdToKey': {c['key']: c['id'] for c in champs.values()},
            'champKeyToId': {c['id'].lower(): int(c['key']) for c in champs.values()},
            'champNames': {c['key']: c['name'] for c in champs.values()},
            'spellKeys': {s['id']: s['name'] for s in spells.values()},
            'spellIdToKey': {s['key']: s['id'] for s in spells.values()},
            # 預測下一件大裝備用：英雄類型/攻擊魔法坦度、裝備合成表與價錢
            'champInfo': {c['key']: {'tags': c.get('tags', []), 'ad': c['info']['attack'],
                                     'ap': c['info']['magic'], 'tank': c['info']['defense']} for c in champs.values()},
            'items': {k: {'name': v['name'], 'total': v['gold']['total'], 'buy': v['gold']['purchasable'],
                          'from': v.get('from', []), 'into': v.get('into', []), 'tags': v.get('tags', []),
                          'maps': [m for m, ok in v.get('maps', {}).items() if ok],
                          'gw': '重創' in v.get('description', ''),   # 重創(減治療)效果
                          'special': bool(v.get('requiredChampion') or v.get('requiredAlly')
                                          or v.get('inStore') is False or v.get('consumed'))}
                      for k, v in items.items()},
            'augments': {str(a['id']): {'name': a.get('nameTRA', ''), 'rarity': a.get('rarity', ''),
                                        'icon': a.get('augmentSmallIconPath', '')} for a in augs},
        }
        self.version, self.data = ver, d
        path = os.path.join(ASSET_DIR, 'static.json')
        with open(path + '.tmp', 'w', encoding='utf-8') as f:
            json.dump({'version': ver, 'data': d}, f, ensure_ascii=False)
        os.replace(path + '.tmp', path)
        log.info(f'🎮 LoL 靜態資料 {ver}：{len(champs)} 隻英雄、{len(items)} 件裝備、{len(augs)} 個增幅')

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
        items = self.data.get('items') or {}
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
        self._profile_sem = asyncio.Semaphore(4)       # 同時查幾個人(太多客戶端會塞住)
        self._name_to_puuid: dict[str, str] = {}
        self.api: Optional[RiotApi] = None
        self._api_puuid: dict[str, str] = {}            # LCU puuid → API puuid
        self._mastery: dict[tuple[str, int], tuple[float, Optional[dict]]] = {}
        self._mastery_jobs: dict[tuple[str, int], asyncio.Task] = {}
        self._enrich_tries: dict[int, int] = {}          # gameId → 結算補資料試過幾次
        self._builds: dict[str, dict] = {}              # 英雄 id → {'at', 'build'}
        self._build_jobs: dict[str, asyncio.Task] = {}
        self._build_sem = asyncio.Semaphore(3)
        self._poro: Optional[PoroFeed] = None            # 這場的 Porofessor log(補包、聊天室)
        self._poro_t = 0.0                               # 上一輪的遊戲時間(倒退 = 換場)
        self._poro_at = 0.0
        self._load_last()
        self._load_builds()

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
        for t in (self._task, self._prefetch, *self._profile_jobs.values(), *self._mastery_jobs.values(),
                  *self._build_jobs.values()):
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

    # ── 英雄常出裝(op.gg) ──
    def _load_builds(self):
        try:
            with open(BUILDS_FILE, encoding='utf-8') as f:
                self._builds = json.load(f)
        except (OSError, ValueError):
            pass

    def _save_builds(self):
        try:
            os.makedirs(ASSET_DIR, exist_ok=True)
            with open(BUILDS_FILE + '.tmp', 'w', encoding='utf-8') as f:
                json.dump(self._builds, f, ensure_ascii=False)
            os.replace(BUILDS_FILE + '.tmp', BUILDS_FILE)
        except OSError as e:
            log.warning(f'🎮 出裝快取存檔失敗: {e!r}')

    def _want_builds(self, champ_ids: list[int]):
        now = time.time()
        for cid in champ_ids:
            cid = str(cid or '')
            if not cid or cid == '0' or cid in self._build_jobs:
                continue
            hit = self._builds.get(cid)
            if hit and now - hit['at'] < (BUILD_TTL if hit.get('build') else BUILD_RETRY):
                continue
            self._build_jobs[cid] = asyncio.ensure_future(self._fetch_build(cid))

    async def _fetch_build(self, cid: str):
        try:
            key = self.static.data['champIdToKey'].get(cid)
            if not key:
                return
            body = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {
                'name': 'lol_get_champion_analysis',
                'arguments': {'game_mode': 'aram', 'champion': key.upper(), 'position': 'mid',
                              'desired_output_fields': [f'data.{f}' + ('.' if f == 'core_items' else '[].')
                                                        + '{ids[],pick_rate,play,win}' for f in BUILD_SLOTS]}}}
            build = None
            async with self._build_sem:
                async with self._http.post(OPGG_MCP, json=body, timeout=aiohttp.ClientTimeout(total=30),
                                           headers={'Accept': 'application/json, text/event-stream'}) as r:
                    txt = await r.text()
            if r.status == 200:
                d = json.loads(txt[txt.find('{'):])
                content = ((d.get('result') or {}).get('content') or [{}])[0].get('text', '')
                build = parse_opgg_build(content)
            if not build:
                log.warning(f'🎮 op.gg 出裝拿不到 {key}: HTTP {r.status} {txt[:120]!r}')
            self._builds[cid] = {'at': time.time(), 'build': build}
            self._save_builds()
        except Exception as e:
            log.warning(f'🎮 op.gg 出裝失敗 {cid}: {e!r}')
            self._builds[cid] = {'at': time.time(), 'build': None}
        finally:
            self._build_jobs.pop(cid, None)

    def builds(self) -> dict:
        return {k: v['build'] for k, v in self._builds.items() if v.get('build')}

    async def _enrich_eog(self, eog: dict):
        """結算資料缺的戰鬥數據從對戰紀錄詳情補；剛結束時詳情可能還沒好，下一輪再試。"""
        gid = eog.get('gameId')
        if not gid:
            return
        tries = self._enrich_tries.get(gid, 0)
        if tries >= 10:
            return
        self._enrich_tries[gid] = tries + 1
        game = await self.pc.lcu(f"/lol-match-history/v1/games/{int(eog['gameId'])}", timeout=20)
        if isinstance(game, dict) and game.get('participants'):
            enrich_eog(eog, game)

    async def _eog_from_history(self, why: str) -> bool:
        """結算畫面資料沒抓到(太快離開結算、或 eog-stats-block 沒回隊伍) → 用對戰紀錄詳情組結算，最多試 10 次。
        對戰紀錄第一次查可能要 ~22 秒，所以 timeout 給長一點。"""
        gid = self.state.get('_eog_pending')
        if not gid or (self.state.get('eog') or {}).get('gameId') == gid:
            self.state.pop('_eog_pending', None)
            return False
        key = -int(gid)   # 跟 _enrich_eog 共用次數表，用負號分開
        tries = self._enrich_tries.get(key, 0)
        if tries >= 10:
            self.state.pop('_eog_pending', None)
            log.warning(f'🎮 結算退路放棄 {gid}：對戰紀錄試了 10 次都沒資料')
            return False
        self._enrich_tries[key] = tries + 1
        game = await self.pc.lcu(f'/lol-match-history/v1/games/{int(gid)}', timeout=40)
        eog = parse_history_eog(game, self.static.data, self.state.get('me') or '') if isinstance(game, dict) else None
        if not eog or not eog.get('teams'):
            log.info(f'🎮 結算退路({why}) {gid} 第 {tries + 1} 次：對戰紀錄還沒資料')
            return False
        eog['queue'] = self.state.get('queue')
        self._save_last(eog)
        self.state.pop('_eog_pending', None)
        self.state['_eog_at'] = time.time()
        log.info(f'🎮 結算退路({why}) {gid}：用對戰紀錄組出結算畫面')
        self._set(phase='eog', sub='結算', eog=eog, live=None, champselect=None)
        return True

    def _dump_json(self, path: str, d: dict):
        try:
            with open(path + '.tmp', 'w', encoding='utf-8') as f:
                json.dump(d, f, ensure_ascii=False)
            os.replace(path + '.tmp', path)
        except OSError as e:
            log.warning(f'🎮 存檔失敗 {path}: {e!r}')

    def _dump_raw(self, d: dict):
        """每 60 秒把遊戲中的原始 JSON 存一份(data/ 不進 git)，拿來查 API 有哪些欄位(例如海克斯)。"""
        now = time.time()
        if now - getattr(self, '_raw_at', 0) < 60:
            return
        self._raw_at = now
        try:
            with open(RAW_FILE + '.tmp', 'w', encoding='utf-8') as f:
                json.dump(d, f, ensure_ascii=False)
            os.replace(RAW_FILE + '.tmp', RAW_FILE)
        except OSError as e:
            log.warning(f'🎮 原始資料存檔失敗: {e!r}')

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
            # 別人的資料客戶端要向伺服器拿：遊戲中召喚師資料實測 ~14 秒、對戰紀錄 ~22 秒 → 給長一點
            async with self._profile_sem:
                summ, ranked, hist = await asyncio.gather(
                    self.pc.lcu(f'/lol-summoner/v2/summoners/puuid/{puuid}', timeout=30),
                    self.pc.lcu(f'/lol-ranked/v1/ranked-stats/{puuid}'),
                    self.pc.lcu(f'/lol-match-history/v1/products/lol/{puuid}/matches'
                                f'?begIndex=0&endIndex={HISTORY_COUNT - 1}', timeout=45))
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
            # 對戰紀錄沒拿到(別人的第一次查常要 20 秒以上，會逾時) → 快取時間設短，30 秒後重試
            # 客戶端還在下載時會回「成功但 0 場」，也當失敗；真的 0 場的新帳號只是每 30 秒多查一次
            no_hist = not ((hist or {}).get('games') or {}).get('games') if isinstance(hist, dict) else True
            no_name = not prof['name']   # 召喚師資料逾時 → 沒名字就對不到遊戲中的人
            # 重試拿到的比上次少 → 沿用上次拿到的那部分，不要蓋成空的
            prev = (self._profiles.get(puuid) or (0, {}))[1]
            if no_hist and prev.get('games'):
                prof.update({k: prev[k] for k in ('recent', 'recentAll', 'games', 'private') if k in prev})
                no_hist = False
            if no_name and prev.get('name'):
                prof.update({k: prev[k] for k in ('name', 'tag', 'level', 'icon') if k in prev})
                no_name = False
            failed = no_hist or no_name
            if failed:
                log.info(f'🎮 玩家資料不完整(戰績{"✗" if no_hist else "✓"} 名字{"✗" if no_name else "✓"})，'
                         f'30 秒後重試 {puuid[:8]}')
            self._profiles[puuid] = (time.time() - (PROFILE_TTL - 30 if failed else 0), prof)
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
            # 客戶端重開過 port 會換，舊的 creds 連不上 → 丟掉，下一輪重讀 lockfile
            self.pc.creds = None
            ok = await self.pc._run('echo ok', timeout=6)
            self._set(phase='eog' if self.state.get('eog') else 'offline',
                      sub='PC 離線' if not ok else '客戶端未開啟', live=None, champselect=None)
            self.state['_raw_phase'] = None
            return POLL_OFFLINE
        self.state['_raw_phase'] = phase
        eog = self.state.get('eog')
        if eog and eog_missing(eog) and phase not in EOG_PHASES:
            # 上一場結算缺戰鬥數據(舊版存的、或剛結束時詳情還沒好) → 客戶端開著時補，最多試 10 次
            await self._enrich_eog(eog)
            if not eog_missing(eog):
                self._save_last(eog)
                self.state['eog'] = None   # 原地改的 → 先清掉再 _set，才會推給畫面
                self._set(eog=eog)

        if phase != last_phase:
            log.info(f'🎮 gameflow {last_phase} → {phase}')
            sess = await self.pc.lcu('/lol-gameflow/v1/session') or {}
            q = (sess.get('gameData') or {}).get('queue') or {}
            qid = q.get('id') or None
            self._set(queue={'id': qid, 'name': queue_name(qid, q.get('description', ''))} if qid else None)
            if phase in EOG_PHASES and last_phase not in EOG_PHASES:
                fun = self.bot.get_cog('FunCog')
                if fun:
                    fun.lcu_game_ended()           # 活動換券：打完馬上看對戰紀錄(競技場、大混戰 API 查不到)
            if phase in INGAME_PHASES or phase in EOG_PHASES:
                gid = (sess.get('gameData') or {}).get('gameId')
                if gid and gid != (self.state.get('eog') or {}).get('gameId'):
                    self.state['_eog_pending'] = gid   # 這場還沒有結算畫面；之後沒抓到就走對戰紀錄退路
            if phase in INGAME_PHASES:
                self.state['_session_puuids'] = session_puuids(sess)
                self.state['_session_champs'] = session_champ_puuids(sess)
                self.state['_session_hidden'] = session_hidden_puuids(sess)
                self._want_profiles(self.state['_session_puuids'], qid)
            if self.state.get('me') is None:
                me = await self.pc.lcu('/lol-summoner/v1/current-summoner') or {}
                self._set(me=me.get('puuid'))
        qid = (self.state.get('queue') or {}).get('id')

        if phase == 'ChampSelect':
            sess = await self.pc.lcu('/lol-champ-select/v1/session')
            if isinstance(sess, dict) and sess.get('myTeam') is not None:
                champ_puuid = None
                if any(p.get('nameVisibilityType') == 'HIDDEN' or not p.get('gameName') for p in sess['myTeam']):
                    # 有人隱藏名字：gameflow session 的 playerChampionSelections 可能已經帶他的 puuid
                    champ_puuid = selection_champ_puuids(await self.pc.lcu('/lol-gameflow/v1/session') or {})
                cs = parse_champselect(sess, self.state.get('me') or '', champ_puuid)
                self._set(phase='champselect', sub='選角中', champselect=cs, live=None)
                self._want_profiles([p['puuid'] for p in cs['team']], qid)
                self._want_mastery([(p['puuid'], p['championId']) for p in cs['team']])
                self._want_builds([p['championId'] for p in cs['team']])
                self._publish_profiles()
            return POLL_ACTIVE

        if phase in INGAME_PHASES:
            # 每輪都檢查一次：之前逾時的玩家資料到期就會重抓(有快取的不會重複查)
            self._want_profiles(self.state.get('_session_puuids') or [], qid)
            d = await self.pc.live()
            if isinstance(d, dict) and d.get('allPlayers'):
                self._dump_raw(d)
                live = parse_live(d, self.static.data, self._name_to_puuid, self.builds(),
                                  self.state.get('_session_champs'), self.state.get('_session_hidden'))
                await self._poro_tick(d)
                live['timers'] = objective_timers(d, self._poro)
                live['chat'] = list(self._poro.chat) if self._poro else []
                self._want_builds([p['champ']['id'] for p in live['players']])
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
                old = self.state.get('eog') or {}
                if old.get('gameId') == eog['gameId'] and not eog_missing(old):
                    eog = old   # 已經補過了，不再查
                elif eog_missing(eog):
                    await self._enrich_eog(eog)
                if old.get('gameId') != eog['gameId'] or old != eog:
                    if old.get('gameId') != eog['gameId']:
                        self._dump_json(EOG_RAW_FILE, raw)
                    self._save_last(eog)
                if self.state.get('phase') != 'eog':
                    self.state['_eog_at'] = time.time()   # /act 自動模式：結算畫面保留幾分鐘再切回歌詞
                self._set(phase='eog', sub='結算', eog=eog, live=None, champselect=None)
                self.state.pop('_eog_pending', None)
            else:
                if self.state.get('_eog_raw_note') != (phase, str(raw)[:200]):
                    self.state['_eog_raw_note'] = (phase, str(raw)[:200])
                    log.info(f'🎮 {phase}：eog-stats-block 沒有隊伍資料 → {str(raw)[:200]}')
                if phase == 'EndOfGame':   # WaitingForStats 時對戰紀錄一定還沒好，不浪費次數
                    await self._eog_from_history('結算畫面沒資料')
            return POLL_ACTIVE

        # Lobby / Matchmaking / ReadyCheck / None：有上一場結算就繼續顯示
        if self.state.get('_eog_pending'):
            await self._eog_from_history('已離開結算')
        sub = {'Lobby': '房間中', 'Matchmaking': '配對中', 'ReadyCheck': '找到對戰',
               'None': '待機中'}.get(phase, phase)
        self._set(phase='eog' if self.state.get('eog') else 'idle', sub=sub, live=None, champselect=None)
        self.state.pop('_session_puuids', None)
        self.state.pop('_session_champs', None)
        self.state.pop('_session_hidden', None)
        return POLL_ACTIVE if phase in ('Matchmaking', 'ReadyCheck') else POLL_IDLE

    async def _poro_tick(self, d: dict):
        """Porofessor 的 Overwolf log(riot-gate 的 `poro <since>`)：補包狀態、聊天室。PC 沒開 Porofessor 就一直是空的。
        遊戲時間倒退 = 換了一場 → 從「這場開始前一點」重新讀，上一場的行不會混進來。"""
        t = (d.get('gameData') or {}).get('gameTime') or 0
        if self._poro is None or t + 5 < self._poro_t:
            start = time.localtime(time.time() - t - PORO_SLACK)   # PC 的 log 是本地時間，DGX 跟 PC 同時區
            self._poro = PoroFeed(time.strftime('%Y%m%d%H%M%S', start) + '000')
            self._poro_at = 0.0
        self._poro_t = t
        if time.monotonic() - self._poro_at < POLL_PORO:
            return
        self._poro_at = time.monotonic()
        out = await self.pc._run(f'poro {self._poro.since}', timeout=10)
        if out:
            self._poro.feed_text(out.decode('utf-8', errors='replace'))

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
    # 即時畫面一律由 /act(Activity)開；這裡的指令都是純文字，API 為主、玩家 PC 的客戶端有開就多補一層
    @app_commands.command(name='lol', description='查 LoL 玩家的牌位、英雄熟練度與最近對戰')
    @app_commands.describe(player='玩家，例如 名字#1234')
    async def lol(self, interaction: discord.Interaction, player: str):
        if not (self.api and self.api.enabled):
            await interaction.response.send_message('❌ 沒有設定可用的 Riot API key', ephemeral=True)
            return
        await interaction.response.defer(thinking=True)
        acc = await self._account(interaction, player)
        if acc:
            await interaction.followup.send(embed=await self._profile_embed(acc))

    async def _account(self, interaction: discord.Interaction, player: str) -> Optional[dict]:
        name, _, tag = player.strip().partition('#')
        if not name or not tag:
            await interaction.followup.send('❌ 格式是 `名字#tag`，例如 `名字#1234`')
            return None
        acc = await self.api.account(name.strip(), tag.strip())
        if not acc:
            await interaction.followup.send(f'❌ 找不到玩家 `{discord.utils.escape_markdown(player)}`')
        return acc

    async def _profile_embed(self, acc: dict) -> discord.Embed:
        """用 Riot API 查任何人(不需要 PC)：牌位、常用英雄、最近 5 場一般/積分。"""
        name, tag = acc.get('gameName', ''), acc.get('tagLine', '')
        pu = acc['puuid']
        summ, entries, top, ids = await asyncio.gather(
            self.api.summoner(pu), self.api.league(pu), self.api.top_mastery(pu, 3), self.api.match_ids(pu, 5))
        matches = await asyncio.gather(*(self.api.match(m) for m in ids or []))
        champ_names = self.static.data['champNames']
        ranks = league_to_ranks(entries)
        emb = discord.Embed(title=f"{name}#{tag}",
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
        # 客戶端層：玩家 PC 的客戶端剛好查過這個人(同場)就補近 100 場勝率(含大混戰)
        prof = self._client_profile(name, tag)
        if prof and prof.get('recentAll'):
            ra = prof['recentAll']
            emb.add_field(name='客戶端近 100 場（含大混戰）', inline=False,
                          value=f"{ra['w']}勝{ra['l']}敗 {ra['w'] * 100 // max(1, ra['w'] + ra['l'])}%")
        emb.set_footer(text='資料來源：Riot API' + ('＋玩家電腦的客戶端' if prof else ''))
        return emb

    def _client_profile(self, name: str, tag: str) -> Optional[dict]:
        pu = self._name_to_puuid.get(riot_id(name, tag).lower())
        hit = self._profiles.get(pu) if pu else None
        return hit[1] if hit else None

    # ── /lol-live：任何玩家正在打的對局 ──
    @app_commands.command(name='lol-live', description='查玩家目前正在打的 LoL 對局：雙方英雄、牌位、熟練度、近況')
    @app_commands.describe(player='玩家，例如 名字#1234（不填 = 你電腦上登入的帳號）')
    async def lol_live(self, interaction: discord.Interaction, player: Optional[str] = None):
        if not (self.api and self.api.enabled):
            await interaction.response.send_message('❌ 沒有設定可用的 Riot API key', ephemeral=True)
            return
        await interaction.response.defer(thinking=True)
        if not player:
            # 沒填 → 看玩家 PC 上客戶端登入的是誰(客戶端沒開就沒辦法)
            me = await self.pc.lcu('/lol-summoner/v1/current-summoner', timeout=6) if self.state.get('_raw_phase') else None
            if not isinstance(me, dict) or not me.get('gameName'):
                await interaction.followup.send('❌ 沒填玩家，而且你電腦上的 LoL 客戶端沒開，不知道要查誰。請填 `名字#tag`')
                return
            player = f"{me['gameName']}#{me.get('tagLine', '')}"
        acc = await self._account(interaction, player)
        if not acc:
            return
        rid = f"{acc.get('gameName', '')}#{acc.get('tagLine', '')}"
        game = await self.api.active_game(acc['puuid'])
        if not game:
            await interaction.followup.send(
                f'**{discord.utils.escape_markdown(rid)}** 目前沒有在對局中（Riot 的即時資料大約慢 1～2 分鐘，自訂對戰查不到）。'
                f'以下是這位玩家的戰績：', embed=await self._profile_embed(acc))
            return
        data = parse_spectator(game, self.static.data)
        players = [p for t in data['teams'] for p in t['players'] if p['puuid'] and not p['bot']]
        # API 層：每人牌位 / 等級 / 這隻英雄的熟練度 / 近幾場，最多等 25 秒(額度不夠的先顯示有拿到的)
        jobs = {p['puuid']: asyncio.ensure_future(self._enrich_live(p)) for p in players}
        live_task = asyncio.ensure_future(self._live_for(players))   # 客戶端層(PC 沒開會很快回 None)
        done, pending = await asyncio.wait(list(jobs.values()) + [live_task], timeout=25)
        for t in pending:
            t.cancel()
        partial = sum(1 for t in jobs.values() if t in pending)
        live = live_task.result() if live_task in done and not live_task.exception() else {}
        await interaction.followup.send(embed=self._live_embed(data, acc['puuid'], live, partial))

    async def _enrich_live(self, p: dict):
        pu, cid = p['puuid'], p['championId']
        try:
            entries, summ, mast, ids = await asyncio.gather(
                self.api.league(pu), self.api.summoner(pu), self.api.mastery(pu, cid), self.api.match_ids(pu, RECENT_N))
            p['ranks'] = league_to_ranks(entries)
            p['level'] = (summ or {}).get('summonerLevel')
            p['mastery'] = mastery_text(mast)
            matches = await asyncio.gather(*(self.api.match(m) for m in ids or []))
            p['recent'] = recent_text(matches, pu, self.static.data['champNames'])
        except Exception as e:
            log.warning(f'🎮 /lol-live 補玩家資料失敗 {pu[:8]}: {e!r}')

    async def _live_for(self, players: list[dict]) -> dict:
        """客戶端層：玩家 PC 的客戶端正在遊戲中、而且就是這場(Riot ID 對得上一半以上) → Live Client 的每人即時數據。"""
        if self.state.get('_raw_phase') not in INGAME_PHASES:
            return {}
        d = await self.pc.live()
        if not isinstance(d, dict) or not d.get('allPlayers'):
            return {}
        m = match_live_players(d, players)
        return m if len(m) * 2 >= len(players) else {}

    def _live_embed(self, data: dict, target: str, live: dict, partial: int) -> discord.Embed:
        esc = discord.utils.escape_markdown
        items = self.static.data.get('items') or {}
        title = f"🎮 {data['queue']}・{data['map']}"
        if data['length']:
            title += f"　已進行 {fmt_length(data['length'])}"
        emb = discord.Embed(title=title, color=0xC8AA6E)
        desc = []
        if data['started']:
            desc.append(f"開局 {discord.utils.format_dt(data['started'], 'R')}")
        for t in data['teams']:
            if data['bans'].get(t['id']):
                desc.append(f"{t['name']}禁用：{'、'.join(data['bans'][t['id']])}")
        emb.description = '\n'.join(desc) or None
        for t in data['teams']:
            lines = []
            for p in t['players']:
                who = '🤖 電腦' if p['bot'] else (esc(p['riotId']) or '🙈 隱藏')
                mark = '▶ ' if p['puuid'] == target else ''
                head = f"{mark}**{esc(p['champion'])}** {who}"
                extra = '　'.join(x for x in ('/'.join(p['spells']), p['keystone']) if x)
                if extra:
                    head += f"　{extra}"
                lines.append(head)
                if 'ranks' in p:
                    r = p['ranks']
                    row = f"　　單雙 {rank_text(r['solo'])}"
                    if r['flex']:
                        row += f"　彈性 {rank_text(r['flex'])}"
                    if p.get('level'):
                        row += f"　Lv{p['level']}"
                    if p.get('mastery'):
                        row += f"　{p['mastery']}"
                    lines.append(row)
                    if p.get('recent'):
                        lines.append(f"　　{p['recent']}")
                elif not p['bot']:
                    lines.append('　　（資料還沒拿到）')
                lv = live_text(live.get(p['puuid']), items)
                if lv:
                    lines.append(f"　　⚔ {lv}")
            self._add_lines(emb, t['name'], lines)
        src = '資料來源：Riot API（spectator）'
        if live:
            src += '＋你電腦的客戶端（即時 K/D/A、裝備）'
        if partial:
            src += f'；{partial} 人資料不完整（API 額度，稍後再查）'
        emb.set_footer(text=src)
        return emb

    @staticmethod
    def _add_lines(emb: discord.Embed, name: str, lines: list[str]):
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


TIER_ZH = {'IRON': '鐵牌', 'BRONZE': '銅牌', 'SILVER': '銀牌', 'GOLD': '金牌', 'PLATINUM': '白金',
           'EMERALD': '翡翠', 'DIAMOND': '鑽石', 'MASTER': '大師', 'GRANDMASTER': '宗師', 'CHALLENGER': '菁英'}


async def setup(bot: commands.Bot):
    await bot.add_cog(LolCog(bot))
