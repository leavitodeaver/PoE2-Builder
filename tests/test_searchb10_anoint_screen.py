"""SEARCHB10: Gear.anoint_screen scores each notable as an anoint (no passive point), not as an allocated node. A node
what-if counts its added node in PassivePointsOver, so on a tree with every point spent each notable read one point
over the level's budget (a hard deficit) and the screen kept nothing (SPEED4's POE2T7899; POE2T8476). Pure: no PoB."""
from types import SimpleNamespace

from builder.search import gear
from builder.search.objective import POINTS_OVER


def test_each_screen_what_if_keeps_the_builds_own_points_over():
    seen = []

    def node_deltas(cases):
        seen.extend(cases)
        # PoB's answer counts the added node: one point over; the "extra" puts the build's own value back
        out = []
        for case in cases:
            stats = {POINTS_OVER: 1, "gain": 1.0}
            stats.update(case.get("extra") or {})
            out.append((11.0 if stats[POINTS_OVER] == 0 else -12.0, stats))
        return out

    build = SimpleNamespace(stats={POINTS_OVER: 0}, score=10.0, node_deltas=node_deltas,
                            call=lambda name, **kw: {"nodes": [{"id": 1}]})
    g = SimpleNamespace(build=build, _anoint_screen=None, lam=0.0,
                        anoint_recipes=lambda: {"Allocated": (1, None), "Lasting Trauma": (2, None), "Wasting": (3, None)},
                        anoint_cost=lambda name: 0.0,
                        ctx=SimpleNamespace(compiled=SimpleNamespace(filters=SimpleNamespace(forbidden_passives=set()))))
    screen = gear.Gear.anoint_screen(g)
    assert sorted(screen) == ["Lasting Trauma", "Wasting"]  # (both gain once scored as anoints; the allocated one skipped)
    assert all(case["extra"] == {POINTS_OVER: 0} and case["add"] for case in seen)


def test_a_build_already_over_keeps_its_own_count():
    seen = []
    build = SimpleNamespace(stats={POINTS_OVER: 2}, score=10.0, call=lambda name, **kw: {"nodes": []},
                            node_deltas=lambda cases: seen.extend(cases) or [(9.0, {}) for _ in cases])
    g = SimpleNamespace(build=build, _anoint_screen=None, lam=0.0, anoint_recipes=lambda: {"Wasting": (3, None)},
                        anoint_cost=lambda name: 0.0,
                        ctx=SimpleNamespace(compiled=SimpleNamespace(filters=SimpleNamespace(forbidden_passives=set()))))
    assert gear.Gear.anoint_screen(g) == []
    assert seen[0]["extra"] == {POINTS_OVER: 2}
