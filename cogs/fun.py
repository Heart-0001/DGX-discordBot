"""活動核心：綁 Riot 帳號、偵測打完的對局換券、錢包(券 + 代幣)、商店、個人資料。

玩家不用打指令：所有活動都在 Activity 的「🎮 活動」面板裡(前端 activity/src/fun.js，操作走 cogs/fun_web.py)。
指令只剩管理用(預設只有管理員看得到)：
  /fun channel   換券 / 開獎 / 賽季結算公告發到這個頻道
  /fun give      (bot 擁有者)發券或代幣
打完一場 → 依表現給 1–4 張券(games/fun_score.py)，在各伺服器的活動頻道貼出來，附「開啟活動」按鈕。
券拿去玩轉蛋、釣魚、大富翁、小遊戲、樂透；每張券機率都一樣，打得好只是券比較多。

錢包是全域的(跟著 Discord 帳號走，不分伺服器)；公告頻道每個伺服器各一個。
其他 cog 的服務方法遇到「使用者能理解的失敗」(券不夠、格式錯…)就 raise FunError(訊息)。
"""
import asyncio
import json
import logging
import os
import time
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from cogs.valo import parse_rid
from cogs.valo_parse import summarize
from games import fun_shop
from games.fun_score import (lcu_to_v5, lol_game_id, lol_score, parts_text, riot_key, today,
                             valo_score)

log = logging.getLogger(__name__)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WALLET_FILE = os.path.join(ROOT, 'data', 'fun_wallet.json')
MAX_PER_GAME = 3
LOL_ROUND = 60            # 秒：每個 LoL 帳號大約多久查一次
VALO_GAP = 20             # 秒：兩次 HenrikDev 查詢至少隔多久(額度 30/分鐘，valo-track 也在用)
LCU_EVERY = 60            # 秒：PC 客戶端對戰紀錄多久看一次(補 API 查不到的大混戰)
LCU_RECENT = 5            # 每次看最近幾場
LCU_BURST = 90            # 秒：對局一結束就連續看這麼久(打完馬上關客戶端也抓得到)
LCU_BURST_GAP = 5         # 秒：連續看的間隔(對戰紀錄比結算畫面晚幾秒才有)
SEEN_KEEP = 40            # 每個帳號記住最近幾場處理過的 gameId(API 和客戶端兩條路去重)
DAILY_TICKETS = 1
RECENT_GRANTS = 10
GAMES = {'lol': 'LoL', 'valo': 'Valorant'}
LAUNCH_ID = 'fun:launch'


class FunError(Exception):
    """給玩家看的失敗原因(券不夠、格式錯…)。"""


def _esc(s) -> str:
    return discord.utils.escape_markdown(str(s or ''))


def launch_view() -> discord.ui.View:
    """「🎮 開啟活動」按鈕：按了直接打開 Activity(custom_id 固定，重啟後照樣能按)。"""
    v = discord.ui.View(timeout=None)
    v.add_item(discord.ui.Button(label='開啟活動', emoji='🎮', style=discord.ButtonStyle.primary, custom_id=LAUNCH_ID))
    return v


class FunCog(commands.Cog):
    fun = app_commands.Group(name='fun', description='活動管理', default_permissions=discord.Permissions(manage_guild=True))

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.data = self._load()
        self._tasks: list[asyncio.Task] = []
        self._lcu_kick = asyncio.Event()

    async def cog_load(self):
        self._tasks = [asyncio.ensure_future(self._lol_loop()), asyncio.ensure_future(self._valo_loop()),
                       asyncio.ensure_future(self._lcu_loop())]
        n = sum(len(u.get(g, [])) for u in self.data['users'].values() for g in GAMES)
        log.info(f'🎟️ 活動核心：{len(self.data["users"])} 位玩家、綁定 {n} 個帳號')

    async def cog_unload(self):
        for t in self._tasks:
            t.cancel()

    # ── 存檔 ──
    @staticmethod
    def _load() -> dict:
        try:
            with open(WALLET_FILE, encoding='utf-8') as f:
                d = json.load(f)
        except (OSError, ValueError):
            d = {}
        d.setdefault('users', {})
        d.setdefault('channels', {})
        d.setdefault('lcu_seen', [])          # 客戶端對戰紀錄看過的 gameId
        now = time.time()
        for u in d['users'].values():         # 舊資料補欄位：since = 從什麼時候開始算(綁定時間)
            for a in u.get('lol', []):
                a.setdefault('since', now)
                a.setdefault('seen', [])
        return d

    def save(self):
        with open(WALLET_FILE + '.tmp', 'w', encoding='utf-8') as f:
            json.dump(self.data, f, ensure_ascii=False, indent=1)
        os.replace(WALLET_FILE + '.tmp', WALLET_FILE)

    def user(self, uid: int) -> dict:
        u = self.data['users'].setdefault(str(uid), {})
        for k, v in (('tickets', 0), ('coins', 0), ('day', ''), ('earned', 0), ('daily', ''),
                     ('lol', []), ('valo', []), ('guilds', []), ('grants', []), ('name', ''), ('total', 0),
                     ('owned', []), ('equip', {}), ('lottery_wins', 0)):
            u.setdefault(k, v)
        if u['day'] != today():
            u['day'], u['earned'] = today(), 0
        return u

    def seen(self, uid: int, gid: Optional[int], name: str = ''):
        """記住這人在哪些伺服器玩過活動(公告發到這些伺服器)和顯示名稱。"""
        u = self.user(uid)
        if gid and gid not in u['guilds']:
            u['guilds'].append(gid)
        if name and u['name'] != name:
            u['name'] = name

    def name_of(self, uid: int) -> str:
        n = self.data['users'].get(str(uid), {}).get('name')
        if n:
            return n
        u = self.bot.get_user(int(uid))
        return u.display_name if u else f'玩家{str(uid)[-4:]}'

    def in_guild(self, uid, gid: int) -> bool:
        return gid in self.data['users'].get(str(uid), {}).get('guilds', [])

    # ── 錢包(給其他活動用) ──
    def tickets(self, uid: int) -> int:
        return self.user(uid)['tickets']

    def coins(self, uid: int) -> int:
        return self.user(uid)['coins']

    def spend_tickets(self, uid: int, n: int = 1) -> bool:
        u = self.user(uid)
        if n < 0 or u['tickets'] < n:
            return False
        u['tickets'] -= n
        self.save()
        return True

    def need_tickets(self, uid: int, n: int = 1):
        """扣 n 張券；不夠就 raise FunError。"""
        if not self.spend_tickets(uid, n):
            raise FunError(f'券不夠：你有 {self.tickets(uid)} 張，這次要 {n} 張。'
                           f'打一場 LoL / Valorant，或每天簽到領 {DAILY_TICKETS} 張')

    def add_tickets(self, uid: int, n: int):
        u = self.user(uid)
        u['tickets'] = max(0, u['tickets'] + n)
        self.save()

    def add_coins(self, uid: int, n: int) -> int:
        """加(或減)代幣，不會變負的；回傳新餘額。"""
        u = self.user(uid)
        u['coins'] = max(0, u['coins'] + int(n))
        self.save()
        return u['coins']

    def spend_coins(self, uid: int, n: int) -> bool:
        u = self.user(uid)
        if n < 0 or u['coins'] < n:
            return False
        u['coins'] -= n
        self.save()
        return True

    def note(self, uid: int, key: str, n: int = 1):
        """累計型統計(例如樂透中獎次數)。"""
        u = self.user(uid)
        u[key] = u.get(key, 0) + n
        self.save()

    def channel_for(self, guild_id: int) -> Optional[discord.abc.Messageable]:
        cid = self.data['channels'].get(str(guild_id))
        return self.bot.get_channel(cid) if cid else None

    def wallet(self, uid: int) -> dict:
        u = self.user(uid)
        title = fun_shop.BY_ID.get(u['equip'].get('title') or '')
        return {'tickets': u['tickets'], 'coins': u['coins'], 'earned': u['earned'],
                'daily': u['daily'] == today(),
                'accounts': {g: [{'name': a['name'], 'tag': a['tag']} for a in u[g]] for g in GAMES},
                'grants': u['grants'][-RECENT_GRANTS:][::-1],
                'title': title['name'] if title else None, 'frame': u['equip'].get('frame'), 'bg': u['equip'].get('bg'),
                'owned': list(u['owned'])}

    # ── 簽到 / 綁帳號 ──
    def daily(self, uid: int) -> int:
        u = self.user(uid)
        if u['daily'] == today():
            raise FunError('今天已經簽到過了，明天再來（台灣時間 0 點重置）')
        u['daily'] = today()
        u['tickets'] += DAILY_TICKETS
        u['total'] += DAILY_TICKETS
        self.save()
        return DAILY_TICKETS

    async def link_add(self, uid: int, game: str, player: str) -> dict:
        if game not in GAMES:
            raise FunError('不支援的遊戲')
        rid = parse_rid(player)
        if not rid:
            raise FunError('格式是 名字#tag，例如 名字#1234')
        u = self.user(uid)
        if len(u[game]) >= MAX_PER_GAME:
            raise FunError(f'{GAMES[game]} 最多綁 {MAX_PER_GAME} 個，先移除一個')
        acc = await (self._lol_account(rid) if game == 'lol' else self._valo_account(rid))
        if not acc:
            raise FunError(f'找不到 {GAMES[game]} 帳號 {player}（或 API 暫時不能用，等一下再試）')
        owner = self._owner_of(game, acc['puuid'])
        if owner == uid:
            raise FunError('這個帳號你已經綁過了')
        if owner:
            raise FunError(f'這個帳號已經被 {self.name_of(owner)} 綁走了；如果那不是他的帳號，請管理員處理')
        acc['last'] = None          # 第一次查只記下最新一場，不補發綁定前的對局
        if game == 'lol':
            acc.update(since=time.time(), seen=[])   # 客戶端那條路：只算綁定之後結束的對局
        u[game].append(acc)
        self.save()
        log.info(f"🎟️ {self.name_of(uid)} 綁定 {GAMES[game]} {acc['name']}#{acc['tag']}")
        return acc

    def link_remove(self, uid: int, game: str, player: str):
        u = self.user(uid)
        if game not in GAMES:
            raise FunError('不支援的遊戲')
        rid = parse_rid(player) or (player.strip(), '')
        keep = [a for a in u[game]
                if not (a['name'].lower() == rid[0].lower() and (not rid[1] or a['tag'].lower() == rid[1].lower()))]
        if len(keep) == len(u[game]):
            raise FunError('你沒有綁這個帳號')
        u[game] = keep
        self.save()

    async def _lol_account(self, rid) -> Optional[dict]:
        api = getattr(self.bot.get_cog('LolCog'), 'api', None)
        if not (api and api.enabled):
            return None
        a = await api.account(*rid)
        if not a or not a.get('puuid'):
            return None
        return {'name': a.get('gameName') or rid[0], 'tag': a.get('tagLine') or rid[1], 'puuid': a['puuid']}

    async def _valo_account(self, rid) -> Optional[dict]:
        api = getattr(self.bot.get_cog('ValoCog'), 'api', None)
        if not api:
            return None
        a = await api.account(*rid)
        if not a or not a.get('puuid'):
            return None
        return {'name': a['name'], 'tag': a['tag'], 'puuid': a['puuid'], 'region': a.get('region') or 'ap'}

    def _owner_of(self, game: str, puuid: str) -> Optional[int]:
        for uid, u in self.data['users'].items():
            if any(a['puuid'] == puuid for a in u.get(game, [])):
                return int(uid)
        return None

    # ── 商店 ──
    def shop(self, uid: int) -> list[dict]:
        u = self.user(uid)
        return fun_shop.catalog(u['owned'], u['equip'])

    def shop_buy(self, uid: int, item: str):
        it = fun_shop.BY_ID.get(item)
        if not it:
            raise FunError('沒有這個商品')
        u = self.user(uid)
        if item in u['owned']:
            raise FunError('你已經有了')
        if not self.spend_coins(uid, it['price']):
            raise FunError(f"代幣不夠：要 {it['price']:,}，你有 {u['coins']:,}")
        u['owned'].append(item)
        u['equip'][it['kind']] = item        # 買了直接裝上
        self.save()
        log.info(f"🎟️ {self.name_of(uid)} 買了 {it['name']}（{it['price']}）")

    def equip(self, uid: int, item: Optional[str], kind: Optional[str] = None):
        u = self.user(uid)
        if item:
            it = fun_shop.BY_ID.get(item)
            if not it or item not in u['owned']:
                raise FunError('你還沒有這個東西')
            u['equip'][it['kind']] = item
        elif kind in fun_shop.SLOTS:
            u['equip'].pop(kind, None)
        else:
            raise FunError('要卸下哪一種？')
        self.save()

    # ── 管理指令 ──
    @fun.command(name='channel', description='把活動公告(打完對局換券、開獎、賽季結算)發到這個頻道')
    async def fun_channel(self, interaction: discord.Interaction):
        self.data['channels'][str(interaction.guild_id)] = interaction.channel_id
        self.save()
        await interaction.response.send_message(f'✅ 活動公告之後會發到 <#{interaction.channel_id}>。'
                                                '玩家在 Activity(/act)右上角的「🎮 活動」裡玩')

    @fun.command(name='give', description='(bot 擁有者)發券或代幣給某人')
    @app_commands.describe(member='給誰', tickets='券(可負數)', coins='代幣(可負數)')
    async def fun_give(self, interaction: discord.Interaction, member: discord.Member, tickets: int = 0, coins: int = 0):
        if not await self.bot.is_owner(interaction.user):
            await interaction.response.send_message('❌ 只有 bot 擁有者可以用', ephemeral=True)
            return
        if tickets:
            self.add_tickets(member.id, tickets)
        if coins:
            self.add_coins(member.id, coins)
        u = self.user(member.id)
        await interaction.response.send_message(
            f"✅ {member.mention} 現在有 {u['tickets']} 張券、{u['coins']:,} 代幣", ephemeral=True)
        await self._push_wallet(member.id)

    # ── 「開啟活動」按鈕 ──
    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        if interaction.type != discord.InteractionType.component:
            return
        if (interaction.data or {}).get('custom_id') != LAUNCH_ID:
            return
        try:
            await interaction.response.launch_activity()
        except discord.HTTPException as e:
            # 不在語音頻道等情況 Discord 會拒絕：退回文字說明
            log.info(f'🎟️ 開啟 Activity 失敗: {e!r}')
            if not interaction.response.is_done():
                await interaction.response.send_message('先進語音頻道，再用 `/act` 開啟 Activity，右上角「🎮 活動」就能玩',
                                                        ephemeral=True)

    # ── 背景：偵測打完的對局 ──
    def _accounts(self, game: str) -> list[tuple[int, dict]]:
        return [(int(uid), a) for uid, u in self.data['users'].items() for a in u.get(game, [])]

    async def _lol_loop(self):
        await self.bot.wait_until_ready()
        while True:
            accs = self._accounts('lol')
            if not accs:
                await asyncio.sleep(LOL_ROUND)
                continue
            for uid, acc in accs:
                start = time.monotonic()
                try:
                    await self._check_lol(uid, acc)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    log.warning(f"🎟️ LoL 對局檢查例外 {acc.get('name')}: {e!r}")
                await asyncio.sleep(max(3.0, LOL_ROUND / len(accs) - (time.monotonic() - start)))

    async def _valo_loop(self):
        await self.bot.wait_until_ready()
        while True:
            accs = self._accounts('valo')
            if not accs:
                await asyncio.sleep(60)
                continue
            for uid, acc in accs:
                try:
                    await self._check_valo(uid, acc)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    log.warning(f"🎟️ Valorant 對局檢查例外 {acc.get('name')}: {e!r}")
                await asyncio.sleep(max(VALO_GAP, 60 / len(accs)))

    @staticmethod
    def _new_ids(ids_old_to_new: list[str], last: Optional[str]) -> list[str]:
        if not ids_old_to_new or ids_old_to_new[-1] == last:
            return []
        return ids_old_to_new[ids_old_to_new.index(last) + 1:] if last in ids_old_to_new else ids_old_to_new[-1:]

    async def _check_lol(self, uid: int, acc: dict):
        lol = self.bot.get_cog('LolCog')
        api = getattr(lol, 'api', None)
        if not (api and api.enabled):
            return
        from cogs.riot_api import REGIONAL
        ids = await api.get(f"https://{REGIONAL}.api.riotgames.com/lol/match/v5/matches/by-puuid/{acc['puuid']}/ids"
                            f"?count=3", 45)
        if ids is None:
            return
        ids = list(reversed(ids))           # API 新的在前 → 改成舊到新
        if acc.get('last') is None:
            acc['last'] = ids[-1] if ids else ''
            self.save()
            return
        names = lol.static.data.get('champNames', {})
        for mid in self._new_ids(ids, acc['last']):
            if lol_game_id(mid) not in acc.get('seen', []):   # 客戶端那條路先處理過了就不再發
                m = await api.match(mid)
                if not m:
                    return                      # 還查不到詳細資料，下輪再試(last 不動)
                r = lol_score(m, acc['puuid'], names)
                if r:
                    await self._grant(uid, acc, r)
                self._mark_seen(acc, lol_game_id(mid))
            acc['last'] = mid
            self.save()

    @staticmethod
    def _mark_seen(acc: dict, gid: str):
        seen = acc.setdefault('seen', [])
        seen.append(gid)
        del seen[:-SEEN_KEEP]

    # ── PC 客戶端對戰紀錄(補 API 查不到的模式) ──
    def lcu_game_ended(self):
        """LolCog 看到 gameflow 進結算 → 馬上開始看對戰紀錄，不等下一分鐘。"""
        self._lcu_kick.set()

    async def _lcu_loop(self):
        """台服 API 查不到大混戰(2400)、競技場(1700)：PC 的 LoL 客戶端開著時，每分鐘看一次登入帳號的對戰紀錄；
        對局一結束(LolCog 通知)就每 5 秒看一次，看到新的一場或 90 秒後停。
        同一場裡其他有綁定的人(用 名字#tag 對)也一起算；只看得到這台 PC 打的對局。"""
        await self.bot.wait_until_ready()
        while True:
            try:
                await asyncio.wait_for(self._lcu_kick.wait(), LCU_EVERY)
            except asyncio.TimeoutError:
                pass
            until = time.monotonic() + LCU_BURST if self._lcu_kick.is_set() else 0
            self._lcu_kick.clear()
            while True:
                found = 0
                try:
                    if self._accounts('lol'):
                        found = await self._check_lcu()
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    log.warning(f'🎟️ 客戶端對戰紀錄檢查例外: {e!r}')
                if found or time.monotonic() >= until:
                    break
                await asyncio.sleep(LCU_BURST_GAP)

    async def _check_lcu(self) -> int:
        """回傳這次處理掉幾場新的對局。"""
        lol = self.bot.get_cog('LolCog')
        pc = getattr(lol, 'pc', None)
        if not pc:
            return 0
        h = await pc.lcu(f'/lol-match-history/v1/products/lol/current-summoner/matches'
                         f'?begIndex=0&endIndex={LCU_RECENT}', timeout=20)
        games = ((h or {}).get('games') or {}).get('games') or []
        seen = self.data['lcu_seen']
        new = sorted((g for g in games if g.get('gameId') and str(g['gameId']) not in seen),
                     key=lambda g: g.get('gameCreation') or 0)
        if not new:
            return 0
        done = 0
        accs = {riot_key(a['name'], a['tag']): (uid, a) for uid, a in self._accounts('lol')}
        names = lol.static.data.get('champNames', {})
        for g in new:
            gid = str(g['gameId'])
            end = (g.get('gameCreation') or 0) / 1000 + (g.get('gameDuration') or 0)
            if any(end > a.get('since', 0) for _, a in accs.values()):   # 綁定前就結束的不用細看
                d = await pc.lcu(f'/lol-match-history/v1/games/{int(gid)}', timeout=30)
                if not isinstance(d, dict) or not d.get('participants'):
                    return done                                             # 下輪再試
                m = lcu_to_v5(d)
                for p in (m or {}).get('info', {}).get('participants', []):
                    uid, a = accs.get(p['puuid'], (None, None))
                    if not a or end <= a.get('since', 0) or gid in a.get('seen', []):
                        continue
                    r = lol_score(m, p['puuid'], names)
                    if r:
                        await self._grant(uid, a, r)
                    self._mark_seen(a, gid)
            seen.append(gid)
            del seen[:-200]
            self.save()
            done += 1
        return done

    async def _check_valo(self, uid: int, acc: dict):
        valo = self.bot.get_cog('ValoCog')
        api = getattr(valo, 'api', None)
        if not api:
            return
        ms = await api.matches(acc['region'], acc['name'], acc['tag'], 3, background=True, ttl=30)
        if ms is None:
            return
        done = [m for m in ms if (m.get('metadata') or {}).get('is_completed', True)
                and (m.get('metadata') or {}).get('match_id')]
        done.sort(key=lambda m: (m.get('metadata') or {}).get('started_at') or '')
        ids = [m['metadata']['match_id'] for m in done]
        if acc.get('last') is None:
            acc['last'] = ids[-1] if ids else ''
            self.save()
            return
        if done:   # 改過名 → 用對戰裡的新名字(HenrikDev 用名字查)
            me = next((p for p in done[-1].get('players') or [] if p.get('puuid') == acc['puuid']), None)
            if me and (me.get('name'), me.get('tag')) != (acc['name'], acc['tag']) and me.get('name'):
                acc['name'], acc['tag'] = me['name'], me.get('tag', acc['tag'])
        by_id = {m['metadata']['match_id']: m for m in done}
        for mid in self._new_ids(ids, acc['last']):
            api.remember_match(acc['region'], by_id[mid])
            s = summarize(by_id[mid], acc['puuid'], valo.content)
            if s:
                await self._grant(uid, acc, valo_score(s))
            acc['last'] = mid
            self.save()

    async def _grant(self, uid: int, acc: dict, r: dict):
        u = self.user(uid)
        game = GAMES[r['game']]
        if r['skip']:
            log.info(f"🎟️ {acc['name']} {game} {r['matchId'][:12]}：{r['skip']}")
            return
        got = r['tickets']
        u['tickets'] += got
        u['earned'] += got
        u['total'] += got
        u['grants'] = (u['grants'] + [{'day': today(), 'game': game, 'who': r['who'], 'kda': r['kda'],
                                       'score': r['score'], 'got': got}])[-RECENT_GRANTS:]
        self.save()
        log.info(f"🎟️ {acc['name']} {game} {r['matchId'][:12]}：{r['score']}/8 分 → +{got} 張")
        web = self.bot.get_cog('FunWebCog')
        if web:
            try:
                await web.on_grant(uid, {'game': game, 'queue': r['queue'], 'won': r['won'], 'who': r['who'],
                                         'kda': r['kda'], 'score': r['score'], 'parts': r['parts'], 'got': got,
                                         'tickets': u['tickets'], 'earned': u['earned']})
            except Exception as e:
                log.warning(f'🎟️ 換券推播失敗: {e!r}')
        res = '勝利' if r['won'] else '落敗'
        text = (f"🎟️ <@{uid}> 打完 {game} {r['queue']}（{res}）{_esc(r['who'])} **{r['kda']}**\n"
                f"表現 **{r['score']}/8**：{parts_text(r['parts'])} → **+{got} 張券**"
                f"・現在 {u['tickets']} 張")
        for gid in u['guilds']:
            ch = self.channel_for(gid)
            if not ch:
                continue
            try:
                await ch.send(text, view=launch_view(), allowed_mentions=discord.AllowedMentions.none())
            except discord.HTTPException as e:
                log.warning(f'🎟️ 換券公告發送失敗 <#{ch.id}>: {e!r}')

    async def _push_wallet(self, uid: int):
        web = self.bot.get_cog('FunWebCog')
        if web:
            await web.push_wallet(uid)


async def setup(bot: commands.Bot):
    await bot.add_cog(FunCog(bot))
