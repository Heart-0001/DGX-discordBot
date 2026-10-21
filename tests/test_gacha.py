import io
import random

from PIL import Image

from games import gacha_logic as g
from games.gacha_image import render_pulls


def test_rarity_distribution():
    rng = random.Random(12345)
    n = 200000
    cnt = {r: 0 for r in g.RARITIES}
    for _ in range(n):
        cnt[g.roll_rarity(rng)] += 1
    for r, w in g.WEIGHTS.items():
        assert abs(cnt[r] / n - w / 100) < 0.005, (r, cnt[r] / n)


def test_independent_of_character():
    """稀有度與角色獨立：每個角色的 SSR 比例都接近 2%。"""
    pool = [{'id': f'lol:{i}', 'name': str(i)} for i in range(3)]
    res = g.pull(random.Random(7), pool, 60000)
    for ch in pool:
        mine = [r for c, r in res if c is ch]
        assert abs(mine.count('SSR') / len(mine) - 0.02) < 0.006


def test_expected_value():
    ev = g.expected_sell_value()
    assert abs(ev - 68.8) < 1e-9
    assert 60 <= ev <= 110
    pool = [{'id': 'lol:1', 'name': 'a'}]
    sim = sum(g.SELL_PRICE[r] for _, r in g.pull(random.Random(1), pool, 200000)) / 200000
    assert abs(sim - ev) < 3


def test_build_pool_dedupe():
    agents = {'u2': {'name': 'KAY/O', 'icon': None}, 'u1': {'name': 'KAY/O', 'icon': 'x'},
              'u3': {'name': 'Jett', 'icon': 'y'}, 'u4': {'name': '', 'icon': 'z'}}
    pool = g.build_pool({'266': '厄薩斯', '1': '安妮'}, agents)
    assert [p['id'] for p in pool] == ['lol:1', 'lol:266', 'valo:u1', 'valo:u3']
    assert len({p['name'] for p in pool}) == len(pool)


def test_build_pool_one_side_empty():
    assert g.build_pool({}, {}) == []
    assert [p['id'] for p in g.build_pool({'1': 'A'}, {})] == ['lol:1']


def test_add_pulls_new_flags_and_sell():
    cards = {}
    a, b = {'id': 'lol:1'}, {'id': 'valo:x'}
    flags = g.add_pulls(cards, [(a, 'N'), (a, 'N'), (a, 'N'), (b, 'SSR'), (a, 'R')])
    assert flags == [True, False, False, True, True]
    assert cards == {'lol:1:N': 3, 'valo:x:SSR': 1, 'lol:1:R': 1}
    sold, total = g.sell_duplicates(cards)
    assert sold == [('lol:1:N', 2, 40)] and total == 40
    assert cards == {'lol:1:N': 1, 'valo:x:SSR': 1, 'lol:1:R': 1}
    assert g.sell_duplicates(cards) == ([], 0)


def test_sell_ssr_price():
    cards = {'valo:u:SSR': 3, 'lol:2:SR': 2}
    _, total = g.sell_duplicates(cards)
    assert total == 2 * 1000 + 200


def test_progress_and_best():
    cards = {'lol:1:N': 2, 'lol:1:SSR': 1, 'valo:a:R': 1, 'lol:999:N': 1}   # 999 不在卡池
    p = g.progress(cards, {'lol:1', 'lol:2', 'valo:a'})
    assert p == {'owned': 2, 'total': 3, 'by_rarity': {'N': 1, 'R': 1, 'SR': 0, 'SSR': 1}}
    assert g.best_cards(cards, 2)[0] == ('lol:1:SSR', 1)
    assert g.rank_score(cards, {'lol:1', 'valo:a'}) == (2, 1)


def test_render_png():
    cards = [{'name': '安妮', 'rarity': r, 'icon': None, 'new': i % 2 == 0}
             for i, r in enumerate(['N', 'R', 'SR', 'SSR', 'SSR', 'N', 'R'])]
    assert render_pulls(cards)[:8] == b'\x89PNG\r\n\x1a\n'
    assert render_pulls(cards[:1])[:4] == b'\x89PNG'


def test_render_with_icon_bytes():
    buf = io.BytesIO()
    Image.new('RGBA', (64, 64), (200, 30, 30, 255)).save(buf, 'PNG')
    png = render_pulls([{'name': 'X' * 30, 'rarity': 'SSR', 'icon': buf.getvalue(), 'new': True}])
    assert png[:4] == b'\x89PNG'
