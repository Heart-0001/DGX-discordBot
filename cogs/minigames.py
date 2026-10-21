"""小遊戲：21 點、拉霸、踩地雷、猜正反面。每局 1 張券，贏的拿代幣(期望值見 games/minigames_logic.py)。

沒有指令：Activity 的活動面板透過 cogs/fun_web.py 呼叫這裡的服務方法。
進行中的局放記憶體(每人每種遊戲一局)；閒置太久自動收尾：21 點自動停牌、踩地雷自動收手。
bot 重啟時進行中的局會消失(那張券不退)。
"""
import asyncio
import json
import logging
import os
import random
import time
from typing import Optional

from discord.ext import commands

from cogs.fun import FunError
from games import minigames_logic as L

log = logging.getLogger(__name__)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FILE = os.path.join(ROOT, 'data', 'minigames.json')
IDLE = {'blackjack': 120, 'mines': 180}     # 秒：閒置多久自動收尾
SWEEP_EVERY = 15


def _card(c) -> dict:
    return {'rank': c[0], 'suit': c[1]} if isinstance(c, (tuple, list)) else {'rank': str(c), 'suit': ''}


class MiniGamesCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.rng = random.SystemRandom()
        self.stats = self._load()
        self.games: dict[tuple[str, int], dict] = {}     # (遊戲, uid) → {'g': 物件, 'at': 最後動作時間, ...}
        self._task: Optional[asyncio.Task] = None

    async def cog_load(self):
        self._task = asyncio.ensure_future(self._sweep_loop())

    async def cog_unload(self):
        if self._task:
            self._task.cancel()

    @staticmethod
    def _load() -> dict:
        try:
            with open(FILE, encoding='utf-8') as f:
                d = json.load(f)
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    def save(self):
        os.makedirs(os.path.dirname(FILE), exist_ok=True)
        with open(FILE + '.tmp', 'w', encoding='utf-8') as f:
            json.dump(self.stats, f, ensure_ascii=False, indent=1)
        os.replace(FILE + '.tmp', FILE)

    @property
    def fun(self):
        return self.bot.get_cog('FunCog')

    def _end(self, game: str, uid: int, payout: int):
        """結算：發代幣、記錄戰績、結束這局。"""
        self.games.pop((game, uid), None)
        self.fun.add_coins(uid, payout)
        s = self.stats.setdefault(str(uid), {}).setdefault(game, {'plays': 0, 'won': 0, 'best': 0})
        s['plays'] += 1
        s['won'] += payout
        s['best'] = max(s.get('best', 0), payout)
        self.save()
        log.info(f'🕹️ {game} {self.fun.name_of(uid)} +{payout} 代幣')
        web = self.bot.get_cog('FunWebCog')
        if web and ((game == 'mines' and payout >= 1000) or (game == 'slots' and payout >= 3000)):
            asyncio.ensure_future(web.celebrate_user(uid, 'mines_big' if game == 'mines' else 'jackpot',
                                                     f"{'踩地雷收手' if game == 'mines' else '拉霸中大獎'} 🪙 {payout:,}",
                                                     big=payout >= 8000))

    def best(self, uid: int) -> int:
        return max((s.get('best', 0) for s in self.stats.get(str(uid), {}).values()), default=0)

    def _get(self, game: str, uid: int) -> Optional[dict]:
        s = self.games.get((game, uid))
        if s:
            s['at'] = time.monotonic()
        return s

    # ── 21 點 ──
    @staticmethod
    def _bj_view(g: L.Blackjack) -> dict:
        return {'player': [_card(c) for c in g.player],
                'dealer': [_card(c) for c in g.dealer] if g.over else [_card(g.dealer[0]), None],
                'pv': L.hand_value(g.player)[0], 'dv': L.hand_value(g.dealer)[0] if g.over else None,
                'over': g.over, 'result': g.result or None, 'payout': g.payout if g.over else 0}

    def bj(self, uid: int, action: str) -> dict:
        s = self._get('blackjack', uid)
        if action == 'start':
            if s:
                return self._bj_view(s['g'])           # 上一局還沒打完 → 接著打，不重複扣券
            self.fun.need_tickets(uid, 1)
            g = L.Blackjack(self.rng)
            if g.over:                                  # 開局就 Blackjack / 21 點：當場結算
                self._end('blackjack', uid, g.payout)
            else:
                self.games[('blackjack', uid)] = {'g': g, 'at': time.monotonic()}
            return self._bj_view(g)
        if not s:
            raise FunError('這局已經結束了，再開一局吧')
        g = s['g']
        g.hit() if action == 'hit' else g.stand()
        if g.over:
            self._end('blackjack', uid, g.payout)
        return self._bj_view(g)

    # ── 踩地雷 ──
    @staticmethod
    def _mines_view(s: dict) -> dict:
        k = len(s['opened'])
        over = s['over']
        safe = L.MINES_CELLS - L.MINES_COUNT
        return {'cells': L.MINES_CELLS, 'cols': 5, 'mines': L.MINES_COUNT, 'opened': sorted(s['opened']), 'k': k,
                'mult': round(L.mines_multiplier(k), 2), 'cash': L.mines_payout(k),
                'next_mult': round(L.mines_multiplier(k + 1), 2) if k < safe else None,
                'next_cash': L.mines_payout(k + 1) if k < safe else None,
                'over': over, 'exploded': s['exploded'], 'hit': s.get('hit'),
                'mine_cells': sorted(s['mines']) if over else [], 'payout': s['payout']}

    def mines(self, uid: int, action: str, i: Optional[int] = None) -> dict:
        s = self._get('mines', uid)
        if action == 'start':
            if s:
                return self._mines_view(s)
            self.fun.need_tickets(uid, 1)
            s = {'mines': L.mines_place(self.rng), 'opened': set(), 'over': False, 'exploded': False, 'payout': 0,
                 'at': time.monotonic()}
            self.games[('mines', uid)] = s
            return self._mines_view(s)
        if not s:
            raise FunError('這局已經結束了，再開一局吧')
        if action == 'reveal':
            if not isinstance(i, int) or not 0 <= i < L.MINES_CELLS:
                raise FunError('格子不對')
            if i in s['opened']:
                return self._mines_view(s)
            if i in s['mines']:
                s.update(over=True, exploded=True, hit=i, payout=0)
                self._end('mines', uid, 0)
                return self._mines_view(s)
            s['opened'].add(i)
            if len(s['opened']) >= L.MINES_CELLS - L.MINES_COUNT:     # 安全格全翻完，自動收手
                return self._mines_cash(uid, s)
            return self._mines_view(s)
        return self._mines_cash(uid, s)

    def _mines_cash(self, uid: int, s: dict) -> dict:
        s.update(over=True, payout=L.mines_payout(len(s['opened'])))
        self._end('mines', uid, s['payout'])
        return self._mines_view(s)

    # ── 拉霸 / 猜正反面(一次就結束) ──
    def slots(self, uid: int) -> dict:
        self.fun.need_tickets(uid, 1)
        reels = L.spin_slots(self.rng)
        payout = L.slots_payout(reels)
        self._end('slots', uid, payout)
        return {'reels': reels, 'payout': payout, 'table': [{'sym': s, 'pay': p} for s, _, p in L.SLOTS],
                'pair': {'sym': L.SLOTS_PAIR_SYMBOL, 'pay': L.SLOTS_PAIR}}

    def flip(self, uid: int, guess: str) -> dict:
        if guess not in ('heads', 'tails'):
            raise FunError('要猜正面或反面')
        self.fun.need_tickets(uid, 1)
        side = self.rng.choice(('heads', 'tails'))
        payout = L.coinflip_payout(side == guess)
        self._end('coinflip', uid, payout)
        return {'flip': side, 'win': side == guess, 'payout': payout}

    # ── 閒置收尾 ──
    async def _sweep_loop(self):
        await self.bot.wait_until_ready()
        while True:
            await asyncio.sleep(SWEEP_EVERY)
            try:
                now = time.monotonic()
                for (game, uid), s in list(self.games.items()):
                    if now - s['at'] < IDLE.get(game, 180):
                        continue
                    if game == 'blackjack':
                        s['g'].stand()
                        self._end(game, uid, s['g'].payout)
                    elif game == 'mines' and not s['over']:
                        self._mines_cash(uid, s)
                    else:
                        self.games.pop((game, uid), None)
                    log.info(f'🕹️ {game} {uid} 閒置太久，自動收尾')
            except Exception as e:
                log.warning(f'🕹️ 小遊戲收尾例外: {e!r}')


async def setup(bot: commands.Bot):
    await bot.add_cog(MiniGamesCog(bot))
