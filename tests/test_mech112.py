"""MECH112: Ignite chance over the hit's roll (bridge ailmentChancePerHit "Ignite", conditions.ignite_roll_chance)."""
import pytest

from builder.search import conditions


def test_the_roll_points_with_live_flammability():
    # one point: exactly the old min(1, chance + live)
    assert conditions.ignite_roll_chance({"IgniteChanceRoll": [[40.0, 1.0]]}, 0.3) == pytest.approx(0.7)
    assert conditions.ignite_roll_chance({"IgniteChanceRoll": [[40.0, 1.0]]}, 0.8) == pytest.approx(1.0)
    # a spread crossing 1 - live: E[min(1, X + L)] below min(1, E[min(1, X)] + L)
    stats = {"IgniteChanceRoll": [[20.0, 0.5], [120.0, 0.5]]}
    assert conditions.ignite_roll_chance(stats, 0.3) == pytest.approx(0.5 * 0.5 + 0.5 * 1.0)
    assert conditions.ignite_roll_chance(stats, 0.3) < min(1.0, (0.5 * 0.2 + 0.5 * 1.0) + 0.3)
    # several hits a use land together: each point raised before the live Flammability is added
    assert conditions.ignite_roll_chance({"IgniteChanceRoll": [[50.0, 1.0]]}, 0.1, together=2.0) == pytest.approx(0.85)
    # no points: the fallback, else PoB's chance + live
    assert conditions.ignite_roll_chance({}, 0.2, fallback=0.42) == 0.42
    assert conditions.ignite_roll_chance({"IgniteChancePerHit": 30.0}, 0.2) == pytest.approx(0.5)


def uniform_mean(k: float, lo: float, hi: float) -> float:
    """The mean of min(100, k x D) for D rolled evenly between lo and hi."""
    if hi <= lo:
        return min(100.0, k * lo)
    full = 100.0 / k
    b = min(hi, max(lo, full))
    return (k * (b * b - lo * lo) / 2 + 100.0 * (hi - b)) / (hi - lo)


def test_ignite_chance_is_the_mean_of_the_capped_chance_over_the_roll():
    pytest.importorskip("builder.pob.engine")
    from builder.data import GameData

    try:
        GameData()
    except FileNotFoundError:
        pytest.skip("no game-data dump")
    from builder.pob.engine import PobEngine
    from builder.request.schema import BuildRequest
    from builder.search.context import Context, make_payload
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    fields = ["IgniteChanceOnHit", "IgniteChanceOnCrit", "IgniteChancePerHit", "IgniteChanceRoll", "CritChance",
              "IgniteFireMin", "IgniteFireMax", "FireStoredHitMax", "FireStoredCritMin", "FireStoredCritMax"]
    engine = PobEngine()
    payload = make_payload(BuildRequest(main_skill="Fireball", ascendancies=["Infernalist"]))
    skeleton = enumerate_skeletons(Context(payload), ["Fireball"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    checked = crossed = 0
    for level in (84, 40, 20, 12, 9, 7, 5, 3, 1):
        s = build.call("setConfig", vars={"enemyLevel": level}, fields=fields)["stats"]
        lo, hi = s.get("IgniteFireMin") or 0, s.get("IgniteFireMax") or 0
        if not 0 < (s.get("IgniteChanceOnHit") or 0) < 100 or hi <= lo:
            continue
        k = s["IgniteChanceOnHit"] / ((lo + hi) / 2)  # (Fireball: no base chance, the formula through 0)
        scale = hi / s["FireStoredHitMax"]
        clo, chi = s["FireStoredCritMin"] * scale, s["FireStoredCritMax"] * scale
        c = s["CritChance"] / 100
        want = uniform_mean(k, lo, hi) * (1 - c) + uniform_mean(k, clo, chi) * c
        assert s["IgniteChancePerHit"] == pytest.approx(want, rel=1e-6)
        pob = s["IgniteChanceOnHit"] * (1 - c) + s["IgniteChanceOnCrit"] * c
        assert s["IgniteChancePerHit"] <= pob + 1e-9
        crossed += s["IgniteChancePerHit"] < pob - 1e-6
        points = s["IgniteChanceRoll"]
        assert 0 < len(points) <= 32
        weight = sum(w for _, w in points)
        assert weight == pytest.approx(1.0, rel=1e-9)
        capped = sum(min(100.0, x) * w for x, w in points) / weight
        assert capped == pytest.approx(want, rel=2e-2)  # (32 buckets at their mean: a hair above)
        checked += 1
    build.call("setConfig", vars={"enemyLevel": 84}, fields=[])
    assert checked and crossed

