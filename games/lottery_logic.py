"""樂透的純邏輯：開獎時間、抽獎、彩金分配。不 import discord。

規則：
- 每張券 = 1 注；每注讓彩金 +BET_POOL 代幣(系統出錢)。
- 每週日 21:00(台灣時間)開獎。
- 抽頭獎時每一注機率完全相同(買越多注，機率越高，但每注一樣)；
  頭獎得主的其他注會被移除後再抽二獎，依此抽三獎，所以同一人不會中兩個獎。
- 彩金 60% / 25% / 15%(各自無條件捨去)；人數不足 3 人時，沒分出去的彩金(含捨去的零頭)滾到下期。
"""
import random
from datetime import datetime, timedelta

from games.fun_score import TW

BET_POOL = 100                  # 每注讓彩金增加的代幣
SHARES = (60, 25, 15)           # 頭獎 / 二獎 / 三獎 百分比
DRAW_WEEKDAY = 6                # 週日(Monday=0)
DRAW_HOUR = 21
RANK_NAME = ('頭獎', '二獎', '三獎')


def next_draw_after(now: datetime) -> datetime:
    """嚴格晚於 now 的下一個「週日 21:00 台灣時間」；剛好 21:00 整算已經過了，回傳下週。"""
    now = now.astimezone(TW)
    days = (DRAW_WEEKDAY - now.weekday()) % 7
    cand = (now + timedelta(days=days)).replace(hour=DRAW_HOUR, minute=0, second=0, microsecond=0)
    if cand <= now:
        cand += timedelta(days=7)
    return cand


def prize_amounts(pot: int) -> list[int]:
    return [pot * s // 100 for s in SHARES]


def pick_winners(bets: dict[str, int], rng: random.Random, max_winners: int = 3) -> list[str]:
    """依序抽出不重複的得主。每注機率相同；抽中者的所有注移除後再抽下一個。"""
    pool = {u: n for u, n in sorted(bets.items()) if n > 0}
    winners: list[str] = []
    while pool and len(winners) < max_winners:
        total = sum(pool.values())
        r = rng.randrange(total)
        for u, n in pool.items():
            if r < n:
                winners.append(u)
                del pool[u]
                break
            r -= n
    return winners


def draw(bets: dict[str, int], pot: int, rng: random.Random) -> dict:
    """開獎：回傳 {'winners': [{'uid','rank','prize'}...], 'paid', 'carry', 'bets', 'players', 'pot'}。
    0 注 → 不開獎，整個彩金滾存。"""
    total = sum(bets.values())
    out = {'pot': pot, 'bets': total, 'players': sum(1 for n in bets.values() if n > 0),
           'winners': [], 'paid': 0, 'carry': pot}
    if total <= 0:
        return out
    amounts = prize_amounts(pot)
    for rank, uid in enumerate(pick_winners(bets, rng, len(SHARES))):
        out['winners'].append({'uid': uid, 'rank': rank, 'prize': amounts[rank]})
    out['paid'] = sum(w['prize'] for w in out['winners'])
    out['carry'] = pot - out['paid']
    return out


def first_prize_chance(mine: int, total: int) -> float:
    """頭獎機率(0–1)：我的注數 / 總注數。"""
    return mine / total if total > 0 else 0.0


# ── 每個伺服器一期的狀態(會存檔)──
def new_state(now: datetime) -> dict:
    return {'pot': 0, 'bets': {}, 'next_draw': int(next_draw_after(now).timestamp()), 'prize': '',
            'period': 1, 'last': None, 'pay': [], 'announce': False}


def is_due(st: dict, now: datetime) -> bool:
    return now.timestamp() >= st['next_draw']


def settle_period(st: dict, now: datetime, rng: random.Random) -> bool:
    """到期就開獎並直接把狀態推進到下一期(同一個 dict 修改)；沒到期回 False。
    推進後 next_draw 一定晚於 now，所以重複呼叫不會重開。
    得獎的代幣放在 st['pay']，announce 旗標表示還沒公告；由 cog 先存檔再執行。"""
    if not is_due(st, now):
        return False
    res = draw(st['bets'], st['pot'], rng)
    if res['bets'] > 0 or res['pot'] > 0:
        st['last'] = {**res, 'period': st['period'], 'time': st['next_draw'], 'prize': st['prize']}
        st['pay'] = list(st['pay']) + [dict(w) for w in res['winners']]
        st['announce'] = True
    st['pot'] = res['carry']
    st['bets'] = {}
    st['prize'] = ''
    st['period'] += 1
    st['next_draw'] = int(next_draw_after(now).timestamp())
    return True
