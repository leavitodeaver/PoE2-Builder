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

    def spy(minions, resist, own, gate=None):
        seen.append(minions)
        return real(minions, resist, own, gate)

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
    assert "R60-14" in note["basis"] and "+129.0 DPS" in note["basis"]  # (the row: the boss 2 levels up, x1.075)
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


def test_a_nova_spells_cascades_land_about_you_and_reach_the_boss_part_of_the_time():
    """The manager's build 4 walk: a disc Nova centred on you cascades on a random axis through you, so a side area
    reaches the boss only part of the time (a disc since R61-3: a secondary radius isn't an inner edge); a targeted
    spell keeps the old geometry."""
    nova = models.Skill("Ice Nova", group=2, spell=True, support_stats={models.CASCADES_PER_SIDE: 1})
    stats = {"AreaOfEffectRadius": 13.0, "AreaOfEffectRadiusSecondary": 7.2}
    mods, notes = models.spell_cascade_model(nova, stats, 0.3, "a 0.3 m boss", nova=True)
    disc = models.nova_side_share(1.3, 0.0, 0.3, 0.8 ** 0.5, 1.3)
    assert 0.4 < disc < 0.7 and mods[0]["value"] == pytest.approx(100 * 2 * disc, abs=0.01)
    assert "Nova" in notes[0]["basis"] and "disc" in notes[0]["basis"] and notes[0]["confidence"] == "MEDIUM-LOW"
    mods, _ = models.spell_cascade_model(nova, stats, 0.3, "a 0.3 m boss")  # (targeted: the boss on the target)
    assert mods[0]["value"] == pytest.approx(200.0)


def test_a_nova_landing_at_the_target_keeps_the_targeted_cascade_rule():
    """THE USER, 25 Nov ~14:05 (Astral Projection): a Nova that lands at the target (its group's support stat, the
    item / passive line, Ice Nova on a Frostbolt) takes the targeted rule (x3); a triggered one shows the about-you
    reading as a row (RESEARCH61 R61-6)."""
    stats = {"AreaOfEffectRadius": 13.0}
    nova = models.Skill("Ice Nova", group=2, spell=True, triggered=True, support_stats={models.CASCADES_PER_SIDE: 1})
    why = models.nova_lands_at_target(nova, {models.NOVA_AT_TARGET_STAT: 1}, [], set())
    assert "targeted location" in why
    mods, notes = models.spell_cascade_model(nova, stats, 0.3, "a 0.3 m boss", nova=True, at_target=why)
    assert mods[0]["value"] == pytest.approx(200.0)
    assert "R61-6" in notes[0]["basis"]
    assert models.nova_lands_at_target(nova, {}, [], set()) == ""
    line = ["Nova Spells Cast at the targeted location instead of around you"]
    assert models.nova_lands_at_target(nova, {}, line, set())
    assert models.nova_lands_at_target(nova, {}, [], {"Frostbolt"})
    cage = models.Skill("Bone Cage", group=2, spell=True)
    assert not models.nova_lands_at_target(cage, {}, [], {"Frostbolt"})
    burst = models.Skill("Voltaxic Burst", group=1, spell=True)
    named = ["Discharge and Voltaxic Burst are Cast at the targeted location instead of around you"]
    assert models.nova_lands_at_target(burst, {}, named, set()) and not models.nova_lands_at_target(cage, {}, named, set())


def test_bone_cages_spike_rings_cross_the_boss_by_geometry():
    """RESEARCH61 R61-3 (HIGH on the shape): Bone Cage's spikes stand on its radius; with Astral Projection the main ring
    circles a small boss and misses it, the Cascade's two side rings cross it (x2 against PoB's one); with no Cascade
    0; around you the main ring (you standing its radius away) plus the side rings' share; rows: around you for a
    triggered cast, 0 if a spike must be walked into."""
    stats = {"AreaOfEffectRadius": 13.0, "AreaOfEffectRadiusSecondary": 7.2}
    cage = models.Skill("Bone Cage", group=2, spell=True, triggered=True, support_stats={models.CASCADES_PER_SIDE: 1})
    mods, notes = models.spell_cascade_model(cage, stats, 0.3, "a 0.3 m boss", nova=True, at_target="AP")
    assert mods[0]["value"] == pytest.approx(100.0) and notes[0]["value"] == "x2.00"
    assert "R61-3" in notes[0]["basis"] and "x1.34" in notes[0]["basis"] and "walked into" in notes[0]["basis"]
    alone = models.Skill("Bone Cage", group=2, spell=True)
    mods, notes = models.spell_cascade_model(alone, stats, 0.3, "a 0.3 m boss", nova=True, at_target="AP")
    assert mods[0]["value"] == pytest.approx(-100.0)
    mods, notes = models.spell_cascade_model(alone, stats, 0.3, "a 0.3 m boss", nova=True)
    assert mods == [] and notes[0]["value"] == "x1.00"
    hits, _ = models.ring_nova_hits(1.3, 0.3, 1, 0.8 ** 0.5, False)
    assert 1.2 < hits < 1.5
    assert models.ring_nova_hits(1.3, 1.4, 0, 0.8 ** 0.5, True)[0] == 1.0  # (a boss bigger than the ring)


def test_infernal_legion_ignites_only_while_the_boss_holds_half_flammability():
    """RESEARCH61 R61-2 (a possible OVERCOUNT): the minions' "as though dealing" burn doesn't Hit, so it Ignites only at
    50% Flammability: the headline x the gate's share, the ungated reading the row; no gate, as before."""
    brute = ("Skeletal Brute Minion", 5000.0, 0.1, {"melee": True, "range": 16.0, "radius": 20, "minion": "Brute"})
    full, _ = models.infernal_legion_model([brute], 30.0, 0.0)
    seen = []
    half, note = models.infernal_legion_model([brute], 30.0, 0.0, lambda d: (seen.append(d) or 0.25, "test gate"))
    assert seen == [500.0] and half == pytest.approx(full * 0.25)
    assert "50% Flammability" in note["basis"] and "R61-2" in note["basis"] and f"+{full:,.1f}" in note["basis"]
    assert models.infernal_legion_model([brute], 30.0, 0.0, lambda d: (0.0, "none"))[0] == 0.0


def test_mirror_copies_returning_show_a_low_half_row():
    """RESEARCH60 R60-9 (GEM44's note): with Mirror of Refraction in the build, the return note carries a LOW row with
    about half of the copies' returns re-crossing the boss; the headline is unchanged."""
    frost = models.Skill("Frostbolt", group=1, spell=True)
    types = {"Spell", "Projectile", "Cold"}
    gems = [{"name": "Frostbolt"}, {"name": "Breachlord's Amalgam"}]
    plain, notes = models.return_model([], {"PierceCount": 1}, frost, types, gems, set())
    mirrored, mnotes = models.return_model([], {"PierceCount": 1}, frost, types, gems, set(), mirror=True)
    assert plain == mirrored and "R60-9" not in notes[0]["basis"]
    assert "R60-9" in mnotes[0]["basis"] and "+50.0% on the copies" in mnotes[0]["basis"]
