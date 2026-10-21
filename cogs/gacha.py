"""轉蛋收集：用券抽 LoL 英雄 / Valorant 特務卡，收集圖鑑、賣重複換代幣。

沒有指令：Activity 的活動面板透過 cogs/fun_web.py 呼叫這裡的服務方法。
卡池在第一次用到時才建立(LolCog / ValoCog 載入時可能還沒準備好)，某一邊拿不到就只用另一邊。
機率與經濟見 games/gacha_logic.py。圖示走 api/fun/icon/<lol|valo>/<ref>.png(Activity 的 CSP 不給載外站圖)。
"""
import asyncio
import json
import logging
import os
import random
from typing import Optional

from discord.ext import commands

from cogs.fun import FunError
from games.gacha_logic import (ORDER, RARITY_NAME, SELL_PRICE, add_pulls, build_pool, progress, pull,
                               rank_score, sell_duplicates, split_key)

log = logging.getLogger(__name__)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GACHA_FILE = os.path.join(ROOT, 'data', 'gacha.json')
MAX_PULL = 10


class GachaCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.data = self._load()
        self.rng = random.SystemRandom()
        self._pool: list[dict] = []
        self._pool_lock = asyncio.Lock()

    async def cog_load(self):
        log.info(f'🎰 轉蛋：{len(self.data)} 位玩家有收藏')

    # ── 存檔 ──
    @staticmethod
    def _load() -> dict:
        try:
            with open(GACHA_FILE, encoding='utf-8') as f:
                d = json.load(f)
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    def save(self):
        os.makedirs(os.path.dirname(GACHA_FILE), exist_ok=True)
        with open(GACHA_FILE + '.tmp', 'w', encoding='utf-8') as f:
            json.dump(self.data, f, ensure_ascii=False, indent=1)
        os.replace(GACHA_FILE + '.tmp', GACHA_FILE)

    def cards(self, uid: int) -> dict:
        return self.data.setdefault(str(uid), {}).setdefault('cards', {})

    # ── 卡池 ──
    async def get_pool(self) -> list[dict]:
        """卡池；兩邊的資料可能後來才載入，所以沒拿滿就下次再試。"""
        if self._pool:
            return self._pool
        async with self._pool_lock:
            lol = self.bot.get_cog('LolCog')
            valo = self.bot.get_cog('ValoCog')
            names = getattr(getattr(lol, 'static', None), 'data', {}).get('champNames') or {}
            agents = (getattr(valo, 'content', None) or {}).get('agents') or {}
            pool = build_pool(names, agents)
            if names and agents:
                self._pool = pool
            return pool

    @staticmethod
    def icon_url(cid: str) -> str:
        game, _, ref = cid.partition(':')
        return f'api/fun/icon/{game}/{ref}.png'

    async def icon_path(self, game: str, ref: str) -> Optional[str]:
        """圖示的本機快取路徑(沒有就下載)。"""
        try:
            if game == 'lol':
                lol = self.bot.get_cog('LolCog')
                return await asyncio.wait_for(lol.static.asset('champ', ref), 20) if lol else None
            valo = self.bot.get_cog('ValoCog')
            return await asyncio.wait_for(valo._asset('agent', ref), 25) if valo else None
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning(f'🎰 圖示取得失敗 {game}:{ref}: {e!r}')
            return None

    def _names(self, pool: list[dict]) -> dict:
        return {p['id']: p['name'] for p in pool}

    # ── 服務 ──
    async def do_pull(self, uid: int, count: int) -> list[dict]:
        fun = self.bot.get_cog('FunCog')
        count = max(1, min(MAX_PULL, int(count)))
        pool = await self.get_pool()
        if not pool:
            raise FunError('英雄 / 特務資料還沒準備好，等一下再試（沒有扣券）')
        fun.need_tickets(uid, count)
        results = pull(self.rng, pool, count)
        flags = add_pulls(self.cards(uid), results)
        self.save()
        log.info(f"🎰 {fun.name_of(uid)} 抽 {count}：{[r for _, r in results]}")
        return [{'id': ch['id'], 'name': ch['name'], 'rarity': r, 'icon': self.icon_url(ch['id']), 'new': nw}
                for (ch, r), nw in zip(results, flags)]

    async def dex(self, uid: int) -> dict:
        pool = await self.get_pool()
        names = self._names(pool)
        cards = self.cards(uid)
        p = progress(cards, set(names))
        rows = sorted(((k, n) for k, n in cards.items() if n > 0),
                      key=lambda kv: (-ORDER.get(split_key(kv[0])[1], 0), names.get(split_key(kv[0])[0], '')))
        out = []
        for k, n in rows:
            cid, r = split_key(k)
            if cid in names:
                out.append({'id': cid, 'name': names[cid], 'rarity': r, 'count': n, 'icon': self.icon_url(cid)})
        dupes = sum((n - 1) * SELL_PRICE.get(split_key(k)[1], 0) for k, n in cards.items() if n > 1)
        return {'owned': p['owned'], 'total': p['total'], 'by_rarity': p['by_rarity'], 'cards': out,
                'dupes_value': dupes}

    async def sell(self, uid: int) -> dict:
        fun = self.bot.get_cog('FunCog')
        names = self._names(await self.get_pool())
        sold, total = sell_duplicates(self.cards(uid))
        if not sold:
            raise FunError('沒有重複的卡可以賣（每個角色 + 稀有度會留 1 張）')
        self.save()
        fun.add_coins(uid, total)
        return {'sold': [{'name': names.get(split_key(k)[0], split_key(k)[0]), 'rarity': split_key(k)[1],
                          'rarity_name': RARITY_NAME.get(split_key(k)[1], ''), 'count': n, 'coins': got}
                         for k, n, got in sold], 'total': total}

    async def summary(self, uid: int) -> dict:
        pool = await self.get_pool()
        p = progress(self.cards(uid), {c['id'] for c in pool})
        return {'owned': p['owned'], 'total': p['total'], 'ssr': p['by_rarity']['SSR']}

    async def ranking(self, gid: int) -> list[tuple[int, int, int]]:
        """(uid, 收集角色數, SSR 種數)，只算在這個伺服器玩過活動的人。"""
        fun = self.bot.get_cog('FunCog')
        ids = {c['id'] for c in await self.get_pool()}
        rows = []
        for uid, d in self.data.items():
            if fun.in_guild(uid, gid):
                owned, ssr = rank_score(d.get('cards', {}), ids)
                if owned:
                    rows.append((int(uid), owned, ssr))
        rows.sort(key=lambda x: (-x[1], -x[2]))
        return rows


async def setup(bot: commands.Bot):
    await bot.add_cog(GachaCog(bot))
