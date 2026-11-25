"""MECHB65 (MECH-B): COV58 row 1, Infernal Legion / Catha's Brilliance's burning radius read from each minion's own
attack range and PoB's melee flag, not its name."""
import faulthandler

import pytest

from builder.search import models


@pytest.fixture(scope="module")
def engine():
    from builder.data import GameData

    try:
        GameData()
    except FileNotFoundError:
        pytest.skip("no game-data dump; run `uv run python -m builder.data dump`")
    from builder.pob.engine import PobEngine

    enabled = faulthandler.is_enabled()
    faulthandler.disable()  # LuaJIT's Lua errors are Windows exceptions PoB's pcall catches
    yield PobEngine()
    if enabled:
        faulthandler.enable()


def test_a_minion_stands_near_the_boss_by_its_own_attack_range_not_its_name():
    """A minion counts as within the burning radius when its attack range (10 a metre) is inside it: a ranged minion
    with a melee-looking name keeps away, a melee one with a 'ranged' name word stands at the boss; with no PoB place
    the old name list decides; Infernal Legion II's and Catha's radius is 20, I's 15."""
    assert models.LEGION_RADIUS["Infernal Legion I"] == 15 and models.LEGION_RADIUS["Infernal Legion II"] == 20
    assert models.LEGION_RADIUS["Catha's Brilliance"] == 20
    far = {"melee": False, "range": 80.0, "radius": 15, "minion": "Bone Warrior"}
    near = {"melee": True, "range": 12.0, "radius": 15, "minion": "Skeletal Sniper"}
    assert models.infernal_legion_model([("Bone Warrior", 5000.0, 0.1, far)], 30.0, 0.0)[0] == 0.0
    added, note = models.infernal_legion_model([("Skeletal Sniper", 5000.0, 0.1, near)], 30.0, 0.0)
    assert added == pytest.approx(0.2 * 0.1 * 5000 * 0.7) and "1.2 m within the 1.5 m" in note["basis"]
    bear = {"melee": True, "range": 20.0, "radius": 15, "minion": "Wild Bear Spirit"}  # (2 m: past I's 1.5 m)
    assert models.infernal_legion_model([("Bear", 5000.0, 0.1, bear)], 30.0, 0.0)[0] == 0.0
    assert models.infernal_legion_model([("Bear", 5000.0, 0.1, {**bear, "radius": 20})], 30.0, 0.0)[0] > 0
    caster = {"melee": False, "range": 6.0, "radius": 15, "minion": "Navira"}  # (a default range: UNVERIFIED, near)
    added, note = models.infernal_legion_model([("Djinn", 5000.0, 0.1, caster)], 30.0, 0.0)
    assert added > 0 and "UNVERIFIED" in note["basis"]
    assert models.infernal_legion_model([("Skeletal Sniper Minion", 5000.0)], 30.0, 0.0)[0] == 0.0  # (no place)
    mix, note = models.infernal_legion_model([("Brute", 1000.0, 0.1, {**near, "minion": "Brute"}),
                                              ("Cleric", 9000.0, 0.1, {**far, "minion": "Cleric"})], 30.0, 0.0)
    assert mix == pytest.approx(0.2 * 0.1 * 1000 * 0.7) and "kept away: Cleric" in note["basis"]
    # the Ignited box: the summon's range / melee from activeSkills
    sniper = models.Skill("Skeletal Sniper", group=2, summon={"limit": 2, "persistent": True, "range": 80, "melee": False},
                          support_stats={models.LEGION_STAT: 10, models.LEGION_RADIUS_STAT: 20})
    assert models.infernal_legion_ignited([sniper], None, {})[0] == 0.0
    brute = models.Skill("Bone Thing", group=3, summon={"limit": 2, "persistent": True, "range": 16, "melee": True},
                         support_stats={models.LEGION_STAT: 10, models.LEGION_RADIUS_STAT: 20})
    share, how = models.infernal_legion_ignited([sniper, brute], None, {})
    assert share == 1.0 and "Bone Thing" in how
    short = models.Skill("Bone Thing", group=3, summon={"limit": 2, "persistent": True, "range": 16, "melee": True},
                         support_stats={models.LEGION_STAT: 10, models.LEGION_RADIUS_STAT: 15})
    assert models.infernal_legion_ignited([short], None, {})[0] == 0.0  # (1.6 m past I's 1.5 m)


def test_infernal_legion_reads_each_minions_place_from_pob(engine):
    """On real PoB: useRates gives each minion's attackRange and melee flag (the Skeletal Brute melee within Infernal
    Legion II's 2 m; the Skeletal Sniper's Bow attack not melee, 8 m away), so the Sniper adds no Ignite and the
    Brute does; activeSkills' summon carries the same range for the Ignited box."""
    from builder.request.schema import BuildRequest
    from builder.search import assumptions
    from builder.search.context import Context, make_payload
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    payload = make_payload(BuildRequest(main_skill="Skeletal Brute", level=90))
    skeleton = enumerate_skeletons(Context(payload), ["Skeletal Brute"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    build.call("addSkillGroup", text="Skeletal Sniper 20/0 1\nInfernal Legion II 20/0 1")
    seen = []
    real = models.infernal_legion_model

    def spy(minions, resist, own):
        seen.append(minions)
        return real(minions, resist, own)

    models.infernal_legion_model = spy
    try:
        added, notes = assumptions.infernal_legion(build, {})
    finally:
        models.infernal_legion_model = real
    print(seen, added, notes)
    sniper = next(m for m in seen[0] if "Sniper" in m[0])
    assert sniper[3]["range"] == 80 and sniper[3]["melee"] is not True and sniper[3]["radius"] == 20  # (None: flags not built)
    assert "Skeletal Sniper" in notes[0]["basis"] and "past the 2 m" in notes[0]["basis"]
    found = [models.Skill.of(s) for s in build.call("activeSkills")["skills"] if not s["disabled"]]
    snipers = [s for s in found if "Sniper" in s.name and s.summon]
    assert snipers and snipers[0].summon.get("range") == 80
    assert snipers[0].support_stats.get(models.LEGION_RADIUS_STAT) == 20
    group = next(x for x in build.groups() if any(y["name"].startswith("Skeletal Brute") for y in x["gems"]))
    gems = [{k: y.get(k) for k in ("name", "support", "level", "quality", "enabled")} for y in group["gems"]]
    gems.append({"name": "Infernal Legion II", "support": True, "level": 20, "quality": 0, "enabled": True})
    build.call("setGroup", index=group["index"], gems=gems, fields=[])
    added, notes = assumptions.infernal_legion(build, {})
    print(added, notes)
    assert added > 0 and "Skeletal Brute" in notes[0]["basis"] and "melee" in notes[0]["basis"]


def test_entangles_overgrown_vines_count_at_the_builds_overgrown_share():
    """COV58 (an UNDERCOUNT: the Overgrown +3 vines were a row only): 3 + 3 x the build's Overgrown share vine hits a
    second in the headline; every fissure Overgrown stays the row; the slower expiry moves nothing (said)."""
    entangle = models.Skill("Entangle", 1, spell=True)
    sets = [{"label": "Fissures", "TotalDPS": 100.0}, {"label": "Vines", "TotalDPS": 20.0, "AverageDamage": 50.0}]
    plain = models.fissure_count_model(entangle, "Fissures", sets, {}, {}, [], 0.4)
    half = models.fissure_count_model(entangle, "Fissures", sets, {}, {}, [], 0.4, overgrown=(0.5, "Thunderstorm 50%"))
    assert plain[0][0]["value"] == pytest.approx(100 * ((150.0 + 100.0) / 100.0 - 1))
    assert half[0][0]["value"] == pytest.approx(100 * ((4.5 * 50.0 + 100.0) / 100.0 - 1))
    basis = half[1][0]["basis"]
    assert "4.5 vine hits a second" in basis and "Overgrown share 50%" in basis and "a row: x4.000" in basis
    assert "expire 25% slower" in basis and "none of its plants Overgrown" in plain[1][0]["basis"]


def test_merging_fragments_get_one_returned_fragment_not_all_of_them():
    """MECHB63's leftover (RETURN_RULE's UNVERIFIED, a possible OVERCOUNT): the return's "1% more" multiplied the Merging
    fragments' x N; R59-1's one returned hit for projectiles fired together makes it one fragment: the more / N in the
    headline, all N merged on the return a row; a plain projectile skill keeps its one return."""
    shards = models.Skill("Galvanic Shards", group=1, attack=True)
    types = {"Attack", "Projectile", "Lightning"}
    gems = [{"name": "Galvanic Shards"}, {"name": "Breachlord's Amalgam"}]
    mods, notes = models.return_model([], {"PierceCount": 1, "ProjectileCount": 8}, shards, types, gems, {"Crossbow"})
    assert mods[0]["scale"] == pytest.approx(100.0 / 8)
    assert "one returned fragment for the 8" in notes[0]["basis"] and "+100.0%" in notes[0]["basis"]
    frost = models.Skill("Frostbolt", group=1, spell=True)
    mods, _ = models.return_model([], {"PierceCount": 1, "ProjectileCount": 8}, frost, {"Spell", "Projectile", "Cold"},
                                  [{"name": "Frostbolt"}, {"name": "Breachlord's Amalgam"}], set())
    assert mods[0]["scale"] == pytest.approx(100.0)


def test_fiery_death_explodes_ignited_adds_at_the_builds_own_rate():
    """COV58 (an UNDERCOUNT where adds die; PoB's own entry is left out of Full DPS as on-death damage): adds killed a
    second x the supported skill's Ignite chance x the support's 60% x PoB's Fiery Death hit; 0 on a lone boss."""
    added, note = models.fiery_death_model(0.5, "test adds", 40.0, 60.0, 1000.0, "Fireball")
    assert added == pytest.approx(0.5 * 0.4 * 0.6 * 1000.0) and note["confidence"] == "LOW"
    assert models.fiery_death_model(0.0, "", 40.0, 60.0, 1000.0, "Fireball")[0] == 0.0
    assert "lone boss" in models.fiery_death_model(0.0, "", 40.0, 60.0, 1000.0, "Fireball")[1]["basis"]
    assert models.fiery_death_model(0.5, "", 0.0, 60.0, 1000.0, "Fireball")[0] == 0.0
    assert models.fiery_death_model(0.5, "", 40.0, 0.0, 1000.0, "Fireball")[0] == pytest.approx(0.5 * 0.4 * 0.6 * 1000.0)


def test_fiery_death_reads_the_supported_skill_and_its_explosion_from_pob(engine):
    """On real PoB: Fireball with Fiery Death; the support's 60% from its stats, Fireball's IgniteChancePerHit and the
    Fiery Death hit from useRates; with the preset's adds killed (build.kills) the explosions add DPS, without them 0."""
    from builder.request.schema import BuildRequest
    from builder.search import assumptions
    from builder.search.context import Context, make_payload
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    payload = make_payload(BuildRequest(main_skill="Fireball", level=90))
    skeleton = enumerate_skeletons(Context(payload), ["Fireball"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    assert assumptions.fiery_death(build) == (0.0, [])
    group = build.main_group()
    gems = [{k: y.get(k) for k in ("name", "support", "level", "quality", "enabled")} for y in group["gems"]]
    gems.append({"name": "Fiery Death", "support": True, "level": 20, "quality": 0, "enabled": True})
    build.call("setGroup", index=group["index"], gems=gems, fields=[])
    build.kills = (0.0, 0.0, "a lone boss")
    added, notes = assumptions.fiery_death(build)
    print(added, notes)
    assert added == 0.0 and "lone boss" in notes[0]["basis"]
    build.kills = (0.5, 0.5, "test adds")
    added, notes = assumptions.fiery_death(build)
    print(added, notes)
    assert "60%" in notes[0]["basis"] or added == 0.0
    assert added > 0 or "doesn't Ignite" in notes[0]["basis"] or "no hit" in notes[0]["basis"]
