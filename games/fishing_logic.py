"""釣魚的純邏輯：魚種表、稀有度機率、重量與價值、期望值。不 import discord。

每竿獨立：先抽稀有度 → 同稀有度魚種均勻抽一種 → 在重量範圍內均勻抽重量。
價值 = 魚種基礎價 × (重量 / 該魚種平均重量)，所以每種魚的期望價值剛好等於基礎價。
所有玩家、所有券的機率完全一樣，沒有任何保底或加成。
"""
import random
from typing import NamedTuple

RARITIES = ('trash', 'common', 'rare', 'epic', 'legend')
RARITY_NAME = {'trash': '垃圾', 'common': '普通', 'rare': '稀有', 'epic': '史詩', 'legend': '傳說'}
RARITY_ODDS = {'trash': 0.15, 'common': 0.50, 'rare': 0.25, 'epic': 0.08, 'legend': 0.02}


class Species(NamedTuple):
    id: str
    emoji: str
    name: str
    rarity: str
    base: int       # 基礎價(代幣)：平均重量時的價值
    wmin: float     # 重量範圍(公斤)
    wmax: float
    desc: str


SPECIES: list[Species] = [
    # 垃圾
    Species('boot', '👟', '斷線的鞋子', 'trash', 2, 0.2, 0.8, '主人打到一半斷線，只留下這隻鞋。'),
    Species('can', '🥫', '生鏽的罐頭', 'trash', 4, 0.1, 0.5, '過期很久了，千萬別打開。'),
    Species('weed', '🌿', '一團水草', 'trash', 5, 0.3, 2.0, '像極了草叢，裡面沒有敵人。'),
    Species('cable', '🔌', 'Ping 999 的網路線', 'trash', 6, 0.1, 0.6, '難怪團戰都在轉圈圈。'),
    Species('creep', '🫧', '漏掉的小兵', 'trash', 8, 0.05, 0.3, '補刀失敗的遺憾，沉在水底。'),
    # 普通
    Species('crab', '🦀', '河道蟹', 'common', 35, 0.2, 1.2, '路過河道的老朋友，踩到會被夾。'),
    Species('mackerel', '🐟', '被 gank 的鯖魚', 'common', 40, 0.3, 1.5, '上岸前還在喊「沒眼了」。'),
    Species('clown', '🐠', '補刀不及格的小丑魚', 'common', 45, 0.05, 0.4, '十分鐘只有 12 刀。'),
    Species('crayfish', '🦐', '小龍蝦', 'common', 50, 0.05, 0.5, '不是小龍，是小龍蝦，可以加辣。'),
    Species('tilapia', '🐟', '吳郭魚', 'common', 50, 0.4, 2.5, '最普通的魚，適應力卻很強。'),
    Species('squid', '🦑', '小卷', 'common', 55, 0.1, 0.8, '墨汁噴出來像大招煙霧。'),
    Species('poro', '🐡', '魄羅魚', 'common', 55, 0.3, 1.8, '圓滾滾的，好想摸一下。'),
    Species('milkfish', '🐟', '虱目魚', 'common', 60, 0.5, 3.0, '刺很多，吃的時候要專心。'),
    Species('shrimp', '🍤', '白蝦', 'common', 60, 0.03, 0.3, '看起來就很適合炸成天婦羅。'),
    # 稀有
    Species('spike', '🐡', 'Spike 形狀的河豚', 'rare', 100, 0.5, 2.5, '身上帶著炸彈，只差一個拆包。'),
    Species('baron_eel', '🐍', '巴龍鰻', 'rare', 110, 1.0, 6.0, '拿下它全隊都有加成。'),
    Species('salmon', '🐟', '鮭魚', 'rare', 120, 1.5, 7.0, '逆流而上，意志力滿分。'),
    Species('lobster', '🦞', 'Ace 龍蝦', 'rare', 130, 0.8, 4.0, '一夾五，全場都被清光。'),
    Species('fade_octo', '🐙', 'Fade 的夢魘章魚', 'rare', 140, 2.0, 12.0, '觸手伸出來，你的位置被看光光。'),
    Species('sheriff', '🦈', 'Sheriff 小鯊魚', 'rare', 160, 3.0, 20.0, '一槍頭，但這次只有牙齒。'),
    Species('rift_turtle', '🐢', '峽谷先鋒海龜', 'rare', 170, 5.0, 30.0, '殼很硬，撞牆很痛。'),
    Species('sage_dolphin', '🐬', 'Sage 治療海豚', 'rare', 190, 20.0, 120.0, '復活隊友一次，記得保護她。'),
    # 史詩
    Species('tuna', '🐟', '藍鰭鮪魚', 'epic', 250, 30.0, 250.0, '一條就能吃到滿足的頂級鮪魚。'),
    Species('jett_bonito', '🗡️', 'Jett 飛燕鰹魚', 'epic', 300, 10.0, 40.0, '一個衝刺就跳出水面，根本抓不到。'),
    Species('shark', '🦈', '峽谷大白鯊', 'epic', 330, 100.0, 600.0, '聞到血的味道就來了。'),
    Species('hextech_ray', '⚡', '海克斯魟魚', 'epic', 350, 40.0, 200.0, '身上閃著藍色電流，是科技改變了魟魚。'),
    Species('sunfish', '🌞', '曼波魚', 'epic', 380, 200.0, 900.0, '一次產下三億顆卵，卻一直在曬太陽。'),
    Species('whale_shark', '🐋', '鯨鯊', 'epic', 430, 500.0, 2000.0, '體型很大，個性卻很溫柔。'),
    # 傳說
    Species('radiant_tuna', '✨', 'Radiant 發光鮪魚', 'legend', 600, 50.0, 300.0, '段位最高的一條魚，閃亮到有點刺眼。'),
    Species('elder_koi', '🐉', '遠古龍錦鯉', 'legend', 700, 20.0, 150.0, '誰釣到誰就拿到「龍魂」，運氣爆表。'),
    Species('void_whale', '🐳', '虛空巨鯨', 'legend', 800, 3000.0, 15000.0, '從虛空裂縫游出來的巨大身影。'),
    Species('nashor', '🐲', '納什男爵本魚', 'legend', 900, 800.0, 4000.0, '你把牠釣起來，全隊都要搶。'),
]
BY_ID: dict[str, Species] = {s.id: s for s in SPECIES}
BY_RARITY: dict[str, list[Species]] = {r: [s for s in SPECIES if s.rarity == r] for r in RARITIES}


def is_bold(rarity: str) -> bool:
    """稀有以上用粗體。"""
    return rarity in ('rare', 'epic', 'legend')


def avg_weight(sp: Species) -> float:
    return (sp.wmin + sp.wmax) / 2


def value_of(sp: Species, weight: float) -> int:
    """價值 = 基礎價 × 重量係數(重量 / 平均重量)，至少 1。"""
    return max(1, round(sp.base * weight / avg_weight(sp)))


def roll_rarity(rng: random.Random) -> str:
    x = rng.random()
    acc = 0.0
    for r in RARITIES:
        acc += RARITY_ODDS[r]
        if x < acc:
            return r
    return RARITIES[-1]


def roll_weight(sp: Species, rng: random.Random) -> float:
    return round(rng.uniform(sp.wmin, sp.wmax), 2)


def cast_once(rng: random.Random) -> dict:
    """甩一竿：回傳 {id, weight, value}。"""
    sp = rng.choice(BY_RARITY[roll_rarity(rng)])
    w = roll_weight(sp, rng)
    return {'id': sp.id, 'weight': w, 'value': value_of(sp, w)}


def expected_value_per_cast() -> float:
    """每竿賣掉的期望代幣(精確計算，不含四捨五入造成的微小誤差)。"""
    return sum(RARITY_ODDS[r] * sum(s.base for s in BY_RARITY[r]) / len(BY_RARITY[r]) for r in RARITIES)
