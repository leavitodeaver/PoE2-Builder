"""MECH113 (MECH-A): Lightning-only and all-hit Lucky chances read in the Shock roll (RESEARCH64 R64-5: the Lightning Rod
anoint's "30% chance for Lightning Damage with Hits to be Lucky", parsed by PoB itself); a Nova's reach reads the SCORED
boss's hit radius (R64-10)."""
import pytest

from builder.search import bosses, conditions


class _NovaBuild:
    """A stub for conditions.nova_reach: one main group holding a self-centred Nova with a 2.0 m radius."""

    class ctx:  # noqa: N801
        request = None

    def main_group(self):
        return {"index": 1, "mainActiveSkill": 1, "skills": [{"name": "Ice Nova"}]}

    def call(self, method, **kw):
        if method == "activeSkills":
            return {"skills": [{"name": "Ice Nova", "group": 1, "levelStats": {}, "supportStats": {}}]}
        if method == "stats":
            return {"AreaOfEffectRadius": 20}
        raise AssertionError(method)


def test_a_novas_reach_reads_the_scored_bosss_hit_radius(monkeypatch):
    import builder.search.context as context

    monkeypatch.setattr(context, "skill_types_of", lambda ctx, name: ["Spell", "Nova", "Area"])
    build = _NovaBuild()
    seen = {}
    for name in (None, "Xesht, We That Are One", "Atziri, the Fell Serpent", "Vessel of Kulemak"):
        reach, how = conditions.nova_reach(build, name)
        radius = bosses.hit_radius({"_boss": name} if name else None)[0]
        assert reach == pytest.approx(2.0 + radius) and f"{radius:g} m hit radius" in how
        seen[name] = reach
    assert seen["Atziri, the Fell Serpent"] - seen["Xesht, We That Are One"] == pytest.approx(0.7)
    assert seen[None] == pytest.approx(2.0 + bosses.HIT_RADIUS[bosses.DEFAULT])


def uniform_mean(k: float, lo: float, hi: float) -> float:
    if hi <= lo:
        return min(100.0, k * lo)
    full = 100.0 / k
    b = min(hi, max(lo, full))
    return (k * (b * b - lo * lo) / 2 + 100.0 * (hi - b)) / (hi - lo)


def test_lightning_rods_lucky_chance_weights_the_shock_roll():
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
    from tests.test_mech111 import lucky_mean

    fields = ["ShockChanceOnHit", "ShockChancePerHit", "CritChance", "ShockLightningMin", "ShockLightningMax",
              "LightningStoredHitMax", "LightningStoredCritMin", "LightningStoredCritMax"]
    engine = PobEngine()
    payload = make_payload(BuildRequest(main_skill="Spark", ascendancies=["Stormweaver"]))
    skeleton = enumerate_skeletons(Context(payload), ["Spark"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    group = next(g for g in build.call("groups")["groups"] if g["gems"] and g["gems"][0]["name"] == "Spark")
    gems = [{"name": n["name"], "level": n.get("level") or 20, "quality": n.get("quality") or 0} for n in group["gems"]]
    build.set_group(index=group["index"], gems=gems, main=True)
    checked = 0
    try:
        for level in (20, 12, 9, 7, 5):
            plain = build.call("setConfig", vars={"enemyLevel": level, "customMods": "nil"}, fields=fields)["stats"]
            # the Lightning Rod notable's own text, parsed by PoB (LightningLuckyHitsChance BASE 30; R64-5)
            rod = build.call("setConfig", vars={"customMods": "30% chance for Lightning Damage with Hits to be Lucky"},
                             fields=fields)["stats"]
            lo, hi = plain["ShockLightningMin"], plain["ShockLightningMax"]
            if not 0 < plain["ShockChanceOnHit"] < 100 or hi <= lo:
                continue
            assert rod["ShockChanceOnHit"] == pytest.approx(plain["ShockChanceOnHit"])  # PoB's own never reads Lucky
            k = plain["ShockChanceOnHit"] / ((lo + hi) / 2)
            scale = hi / plain["LightningStoredHitMax"]
            clo, chi = plain["LightningStoredCritMin"] * scale, plain["LightningStoredCritMax"] * scale
            c = plain["CritChance"] / 100
            even = uniform_mean(k, lo, hi) * (1 - c) + uniform_mean(k, clo, chi) * c
            lucky = lucky_mean(k, lo, hi) * (1 - c) + lucky_mean(k, clo, chi) * c
            assert plain["ShockChancePerHit"] == pytest.approx(even, rel=1e-6)
            assert rod["ShockChancePerHit"] == pytest.approx(0.7 * even + 0.3 * lucky, rel=1e-6)
            assert rod["ShockChancePerHit"] > plain["ShockChancePerHit"]
            checked += 1
    finally:
        build.call("setConfig", vars={"enemyLevel": 84, "customMods": "nil"}, fields=[])
    assert checked


def test_staggering_palms_projectiles_blind_at_the_buffs_rate(monkeypatch):
    """(A39-16) The Projectile's hits a second for the Blind reader: Heavy Stuns x the buff's duration (its uptime) x the
    hitting Quarterstaff attacks (MECHB51's reading); 0 Heavy Stuns: None."""
    import builder.search.context as context
    from builder.search import models

    class Build:
        class ctx:  # noqa: N801
            gems = {"Quarterstaff Strike": {"weaponTypes": ["Staff"]}, "Spark": {}}

        def call(self, method, **kw):
            if method == "groups":
                return {"groups": [{"index": 3, "skills": [{"name": "Staggering Palm"}, {"name": "Projectile"}]}]}
            if method == "useRates":
                assert kw == {"indices": [3], "actives": [1]}
                return {"rates": [{"duration": 7.5, "speed": 0.5, "HitChance": 100}]}
            raise AssertionError(method)

    class Self:
        build = Build()
        main_skill = "Quarterstaff Strike"

    monkeypatch.setattr(context, "skill_types_of", lambda ctx, name: ["Attack", "Melee"] if name != "Spark" else ["Spell"])
    monkeypatch.setattr(models, "heavy_stun_rate", lambda stats: (0.05, "test"))
    stats = {"Speed": 2.0, "HitChance": 90.0}
    rate, how = conditions.Conditions._palm_projectiles(Self(), stats)
    assert rate == pytest.approx(0.05 * 7.5 * 2.0 * 0.9) and "38%" in how  # the main Quarterstaff attack's hits
    Self.main_skill = "Spark"  # not a Quarterstaff attack: the Palm's own uses
    rate, how = conditions.Conditions._palm_projectiles(Self(), stats)
    assert rate == pytest.approx(0.05 * 7.5 * 0.5) and "Staggering Palm's own" in how
    monkeypatch.setattr(models, "heavy_stun_rate", lambda stats: (0.0, "no stuns"))
    assert conditions.Conditions._palm_projectiles(Self(), stats) is None
