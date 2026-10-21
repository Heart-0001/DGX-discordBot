"""釣魚：1 張券甩 1 竿，釣到的魚放背包，賣掉換代幣。

沒有指令：Activity 的活動面板透過 cogs/fun_web.py 呼叫這裡的服務方法。
機率與價值在 games/fishing_logic.py；每張券機率完全一樣。
"""
import json
import logging
import os
import random

from discord.ext import commands

from cogs.fun import FunError
from games.fishing_logic import BY_ID, RARITY_NAME, SPECIES, cast_once

log = logging.getLogger(__name__)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FILE = os.path.join(ROOT, 'data', 'fishing.json')
MAX_CAST = 10


class FishingCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.rng = random.Random()
        self.data = self._load()

    async def cog_load(self):
        log.info(f'🎣 釣魚：{len(self.data["bags"])} 個背包、{len(self.data["dex"])} 位玩家有圖鑑')

    @staticmethod
    def _load() -> dict:
        try:
            with open(FILE, encoding='utf-8') as f:
                d = json.load(f)
        except (OSError, ValueError):
            d = {}
        for k in ('bags', 'dex', 'records'):
            d.setdefault(k, {})
        return d

    def save(self):
        os.makedirs(os.path.dirname(FILE), exist_ok=True)
        with open(FILE + '.tmp', 'w', encoding='utf-8') as f:
            json.dump(self.data, f, ensure_ascii=False, indent=1)
        os.replace(FILE + '.tmp', FILE)

    # ── 服務 ──
    def cast(self, uid: int, gid: int, count: int) -> dict:
        fun = self.bot.get_cog('FunCog')
        count = max(1, min(MAX_CAST, int(count)))
        fun.need_tickets(uid, count)
        suid = str(uid)
        bag = self.data['bags'].setdefault(suid, [])
        dex = self.data['dex'].setdefault(suid, {})
        recs = self.data['records'].setdefault(str(gid), {})
        catches, total = [], 0
        for _ in range(count):
            c = cast_once(self.rng)
            sp = BY_ID[c['id']]
            bag.append([c['id'], c['weight'], c['value']])
            total += c['value']
            d = dex.setdefault(c['id'], {'n': 0, 'best': 0.0})
            d['n'] += 1
            d['best'] = max(d['best'], c['weight'])
            cur = recs.get(c['id'])
            record = cur is None or c['weight'] > cur['w']
            if record:
                recs[c['id']] = {'uid': suid, 'w': c['weight']}
            catches.append({'id': sp.id, 'emoji': sp.emoji, 'name': sp.name, 'desc': sp.desc, 'rarity': sp.rarity,
                            'rarity_name': RARITY_NAME[sp.rarity], 'weight': c['weight'], 'value': c['value'],
                            'record': record})
        self.save()
        return {'catches': catches, 'total': total}

    def bag(self, uid: int) -> dict:
        agg: dict[str, list] = {}
        for sid, w, v in self.data['bags'].get(str(uid), []):
            a = agg.setdefault(sid, [0, 0.0, 0])
            a[0] += 1
            a[1] += w
            a[2] += v
        order = {s.id: i for i, s in enumerate(SPECIES)}
        items = [{'emoji': BY_ID[s].emoji, 'name': BY_ID[s].name, 'rarity': BY_ID[s].rarity,
                  'rarity_name': RARITY_NAME[BY_ID[s].rarity], 'count': n, 'weight': round(w, 2), 'value': v}
                 for s, (n, w, v) in sorted(agg.items(), key=lambda kv: -order[kv[0]])]
        return {'items': items, 'total': sum(i['value'] for i in items)}

    def sell(self, uid: int) -> dict:
        fun = self.bot.get_cog('FunCog')
        bag = self.data['bags'].get(str(uid), [])
        if not bag:
            raise FunError('背包是空的，沒東西可賣')
        total, n = sum(v for _, _, v in bag), len(bag)
        self.data['bags'][str(uid)] = []
        self.save()                  # 先清背包再給錢，不會重複賣
        fun.add_coins(uid, total)
        log.info(f'🎣 {fun.name_of(uid)} 賣出 {n} 條魚 +{total}')
        return {'count': n, 'total': total}

    def dex(self, uid: int) -> dict:
        dex = self.data['dex'].get(str(uid), {})
        species = []
        for sp in SPECIES:
            d = dex.get(sp.id)
            species.append({'emoji': sp.emoji if d else '❓', 'name': sp.name if d else '???', 'rarity': sp.rarity,
                            'rarity_name': RARITY_NAME[sp.rarity], 'caught': bool(d), 'count': d['n'] if d else 0,
                            'best': d['best'] if d else None, 'desc': sp.desc if d else ''})
        return {'caught': len(dex), 'total': len(SPECIES), 'species': species}

    def records(self, gid: int) -> dict:
        fun = self.bot.get_cog('FunCog')
        recs = self.data['records'].get(str(gid), {})
        rows = []
        for sp in SPECIES:
            r = recs.get(sp.id)
            if r and fun.in_guild(r['uid'], gid):     # 只算在這個伺服器玩過活動的人
                rows.append({'emoji': sp.emoji, 'name': sp.name, 'rarity': sp.rarity, 'weight': r['w'],
                             'uid': r['uid'], 'user': fun.name_of(int(r['uid']))})
        return {'rows': rows}

    def summary(self, uid: int) -> dict:
        dex = self.data['dex'].get(str(uid), {})
        best = None
        rank = {r: i for i, r in enumerate(('trash', 'common', 'rare', 'epic', 'legend'))}
        for sid, d in dex.items():
            sp = BY_ID.get(sid)
            if sp and (best is None or (rank[sp.rarity], d['best']) > (rank[best[0].rarity], best[1])):
                best = (sp, d['best'])
        return {'caught': len(dex), 'total': len(SPECIES),
                'best': {'emoji': best[0].emoji, 'name': best[0].name, 'weight': best[1]} if best else None}

    def ranking(self, gid: int) -> list[tuple[int, int]]:
        """(uid, 圖鑑收集數)。"""
        fun = self.bot.get_cog('FunCog')
        rows = [(int(u), len(d)) for u, d in self.data['dex'].items() if d and fun.in_guild(u, gid)]
        rows.sort(key=lambda x: -x[1])
        return rows


async def setup(bot: commands.Bot):
    await bot.add_cog(FishingCog(bot))
