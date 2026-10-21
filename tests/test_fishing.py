import random

from games.fishing_logic import (BY_RARITY, RARITIES, RARITY_ODDS, SPECIES, avg_weight, cast_once,
                                 expected_value_per_cast, roll_rarity, value_of)


def test_species_table_sane():
    assert 25 <= len(SPECIES) <= 35
    assert len({s.id for s in SPECIES}) == len(SPECIES)
    assert all(BY_RARITY[r] for r in RARITIES)
    assert all(0 < s.wmin < s.wmax and s.base > 0 and s.emoji and s.name and s.desc for s in SPECIES)
    assert abs(sum(RARITY_ODDS.values()) - 1) < 1e-9


def test_value_scales_with_weight():
    sp = BY_RARITY['rare'][0]
    assert value_of(sp, avg_weight(sp)) == sp.base
    assert value_of(sp, sp.wmax) > value_of(sp, sp.wmin) >= 1


def test_expected_value_exact():
    ev = expected_value_per_cast()
    assert 90 <= ev <= 110, ev


def test_expected_value_simulated():
    rng = random.Random(12345)
    n = 200_000
    avg = sum(cast_once(rng)['value'] for _ in range(n)) / n
    assert 90 <= avg <= 110, avg


def test_rarity_distribution():
    rng = random.Random(7)
    n = 200_000
    cnt = {r: 0 for r in RARITIES}
    for _ in range(n):
        cnt[roll_rarity(rng)] += 1
    for r in RARITIES:
        assert abs(cnt[r] / n - RARITY_ODDS[r]) < 0.005, (r, cnt[r] / n)


def test_species_uniform_within_rarity_and_weight_in_range():
    rng = random.Random(99)
    n = 100_000
    seen: dict[str, int] = {}
    for _ in range(n):
        c = cast_once(rng)
        sp = next(s for s in SPECIES if s.id == c['id'])
        assert sp.wmin <= c['weight'] <= sp.wmax
        seen[c['id']] = seen.get(c['id'], 0) + 1
    common = BY_RARITY['common']
    expect = n * RARITY_ODDS['common'] / len(common)
    for s in common:
        assert abs(seen[s.id] - expect) < expect * 0.1, (s.id, seen[s.id], expect)
