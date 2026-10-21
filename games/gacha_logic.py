"""轉蛋純邏輯：稀有度機率、卡池、賣重複、收集進度。不 import discord。

卡 = (角色, 稀有度)。每抽「獨立」先抽稀有度，再從整個卡池均勻抽角色，沒有保底、不看玩家任何東西。

經濟：把每一張都當重複賣掉，每抽期望值 = 0.60×20 + 0.28×60 + 0.10×200 + 0.02×1000 = 68.8 代幣。
比基準的 100 低一些，是刻意的——收集(圖鑑、排行、閃亮的 SSR)本身就是另一份價值。
"""
import random

RARITIES = ['N', 'R', 'SR', 'SSR']
WEIGHTS = {'N': 60, 'R': 28, 'SR': 10, 'SSR': 2}           # 百分比
SELL_PRICE = {'N': 20, 'R': 60, 'SR': 200, 'SSR': 1000}
RARITY_NAME = {'N': '普通', 'R': '稀有', 'SR': '史詩', 'SSR': '傳說'}
STARS = {'N': 1, 'R': 2, 'SR': 3, 'SSR': 4}
ORDER = {r: i for i, r in enumerate(RARITIES)}


def expected_sell_value() -> float:
    """每抽賣掉的期望代幣(假設每張都是重複的)。"""
    total = sum(WEIGHTS.values())
    return sum(WEIGHTS[r] / total * SELL_PRICE[r] for r in RARITIES)


def roll_rarity(rng: random.Random) -> str:
    x = rng.random() * sum(WEIGHTS.values())
    for r in RARITIES:
        x -= WEIGHTS[r]
        if x < 0:
            return r
    return RARITIES[-1]


def build_pool(lol_names: dict, agents: dict) -> list[dict]:
    """合併 LoL 英雄 + Valorant 特務。回傳 [{'id','game','name','ref'}]；順序固定。

    lol_names: {數字 key: 中文名}；agents: {uuid: {'name','icon'}}。
    特務依名字去重(KAY/O 有兩個 uuid)，優先留有圖示的、再依 uuid 排序。
    """
    def num(k):
        return int(k) if str(k).isdigit() else 0

    pool = [{'id': f'lol:{k}', 'game': 'lol', 'name': n, 'ref': str(k)}
            for k, n in sorted((lol_names or {}).items(), key=lambda kv: (num(kv[0]), kv[0])) if n]
    best: dict[str, tuple] = {}
    for uuid, a in (agents or {}).items():
        name = (a or {}).get('name')
        if not name:
            continue
        rank = (0 if a.get('icon') else 1, uuid)
        if name not in best or rank < best[name]:
            best[name] = rank
    pool += [{'id': f'valo:{u}', 'game': 'valo', 'name': n, 'ref': u}
             for n, (_, u) in sorted(best.items(), key=lambda kv: kv[1][1])]
    return pool


def card_key(cid: str, rarity: str) -> str:
    return f'{cid}:{rarity}'


def split_key(key: str) -> tuple[str, str]:
    cid, _, rarity = key.rpartition(':')
    return cid, rarity


def pull(rng: random.Random, pool: list[dict], count: int) -> list[tuple[dict, str]]:
    """抽 count 次：稀有度與角色各自獨立。"""
    return [(rng.choice(pool), roll_rarity(rng)) for _ in range(count)]


def add_pulls(cards: dict, results: list[tuple[dict, str]]) -> list[bool]:
    """把結果放進收藏；回傳每一抽是不是新卡(同一批裡重複抽到，只有第一張算新)。"""
    flags = []
    for ch, r in results:
        k = card_key(ch['id'], r)
        flags.append(cards.get(k, 0) == 0)
        cards[k] = cards.get(k, 0) + 1
    return flags


def sell_duplicates(cards: dict) -> tuple[list[tuple[str, int, int]], int]:
    """每個「角色+稀有度」留 1 張，其餘賣掉。就地修改 cards，回傳 ([(key, 賣幾張, 得幾幣)], 總額)。"""
    sold, total = [], 0
    for k in sorted(cards, key=lambda k: (-ORDER.get(split_key(k)[1], 0), k)):
        extra = cards[k] - 1
        if extra <= 0:
            continue
        got = extra * SELL_PRICE.get(split_key(k)[1], 0)
        sold.append((k, extra, got))
        total += got
        cards[k] = 1
    return sold, total


def progress(cards: dict, pool_ids: set) -> dict:
    """收集進度：owned=擁有幾個不同角色(任何稀有度)、total=卡池大小、by_rarity=各稀有度擁有幾張不同的卡。"""
    owned, by = set(), {r: 0 for r in RARITIES}
    for k, n in cards.items():
        cid, r = split_key(k)
        if n > 0 and cid in pool_ids and r in by:
            owned.add(cid)
            by[r] += 1
    return {'owned': len(owned), 'total': len(pool_ids), 'by_rarity': by}


def best_cards(cards: dict, n: int = 5) -> list[tuple[str, int]]:
    """最稀有的 n 張(依稀有度高到低，同級依 key)。"""
    ks = [k for k, c in cards.items() if c > 0]
    ks.sort(key=lambda k: (-ORDER.get(split_key(k)[1], 0), k))
    return [(k, cards[k]) for k in ks[:n]]


def rank_score(cards: dict, pool_ids: set) -> tuple[int, int]:
    """排行用：(收集到的角色數, SSR 種數)，越大越前面。"""
    p = progress(cards, pool_ids)
    return p['owned'], p['by_rarity']['SSR']
