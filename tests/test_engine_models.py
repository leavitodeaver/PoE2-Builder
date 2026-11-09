"""Custom models through a real engine (PoB alone vs PoB with the model). Boots one PoB engine for
the module (a few seconds) and needs the game-data dump."""

from __future__ import annotations

import faulthandler

import pytest

from builder.data import GameData
from builder.request.schema import BuildRequest
from builder.search.context import Context, make_payload
from builder.search import models
from builder.search.inner import Search
from builder.search.skeletons import enumerate_skeletons

FIELDS = ["CombinedDPS", "PhysicalMinBase", "PhysicalDamageReduction", "Armour"]


@pytest.fixture(scope="module")
def engine():
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


def iron_ward(engine, forbid: list[str]) -> Search:
    payload = make_payload(BuildRequest(main_skill="Iron Ward", ascendancies=["Titan"], forbid_ledger_entries=forbid))
    skeleton = enumerate_skeletons(Context(payload), ["Iron Ward"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    return search


def test_iron_ward_nova_deals_its_store_and_armour_speeds_it_up(engine):
    off = iron_ward(engine, ["iron-ward-store"]).build
    assert off.call("stats", fields=FIELDS)["CombinedDPS"] == 0  # PoB alone: the nova has no base damage
    search = iron_ward(engine, [])
    build = search.build
    stats = build.call("stats", fields=FIELDS)
    assert stats["PhysicalMinBase"] == 22886 and stats["CombinedDPS"] > 0  # the gem's level 20 store
    assert build.main_group()["mainActiveSkill"] == 2  # the nova, not the buff
    ctx = build.ctx
    base = next(b for b in ctx.data.bases if b["type"] == "Body Armour" and not b.get("hidden")
                and (b.get("armour") or {}).get("Armour") and not (b.get("armour") or {}).get("Evasion")
                and not (b.get("armour") or {}).get("EnergyShield") and not b["name"].startswith("Runeforged"))
    plate = f"Rarity: Rare\nTest Plate\n{base['name']}\nItem Level: 82\nImplicits: 0\n+1500 to Armour"
    (_, what_if), = build.item_deltas([{"slot": "Body Armour", "raw": plate}])
    assert what_if["CombinedDPS"] > 2 * stats["CombinedDPS"]  # the what-if sees the faster store
    build.call("equip", slot="Body Armour", raw=plate, fields=[])
    search.resolve()
    after = build.call("stats", fields=FIELDS)
    assert after["PhysicalDamageReduction"] > stats["PhysicalDamageReduction"]
    assert after["CombinedDPS"] > 2 * stats["CombinedDPS"]


def spark_with(engine, forbid: list[str]) -> Search:
    from builder.request.schema import Choices

    payload = make_payload(BuildRequest(main_skill="Spark", ascendancies=["Stormweaver"],
                                        require=Choices(skills=["Mana Tempest"]), forbid_ledger_entries=forbid))
    skeleton = enumerate_skeletons(Context(payload), ["Spark"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    return search


def test_mana_tempest_gem_stat_reaches_pob(engine):
    """PoB never reads Mana Tempest's gain-as-Lightning stat; the gem_stat entry gives it to spells, at the uptime the build's
    own Mana allows (the bare skeleton can't even pay for its spell: 0%, and the guide says why)."""
    search = spark_with(engine, [])
    build = search.build
    bare = build.call("stats", fields=FIELDS)["CombinedDPS"]
    note = next(n for n in search.assumptions.active if n.get("entry") == "gem-mana-tempest-lightning")
    assert note["value"].startswith("not counted") and "regenerated" in note["basis"]
    amulet = "\n".join(["Rarity: Rare", "Test Amulet", "Gold Amulet", "Item Level: 82", "Implicits: 0", "+1000 to maximum Mana",
                        "Regenerate 400 Mana per second"])
    build.call("equip", slot="Amulet", raw=amulet, fields=[])
    search.resolve()
    rich = build.call("stats", fields=FIELDS)["CombinedDPS"]
    note = next(n for n in search.assumptions.active if n.get("entry") == "gem-mana-tempest-lightning")
    assert "uptime 100%" in note["basis"] and rich > 1.5 * bare


def test_a_builds_coverage_blind_spots(engine):
    """What finalize_task adds to a result's not-modeled list, on a real build: the calls it makes, and gem stats PoB never reads."""
    from builder.ledger.coverage import build_blind_spots
    from builder.ledger.parse_audit import GATED_FIELDS

    search = spark_with(engine, [])
    build = search.build
    groups = build.groups()
    found = build_blind_spots(build.call("sourceLines")["lines"], [g["name"] for group in groups for g in group["gems"] if g.get("name")],
                              config_on=set(search.conditions.active), stats=build.call("stats", fields=GATED_FIELDS))
    assert isinstance(found, list) and all({"source", "line", "kind", "reason"} <= set(b) for b in found)
    assert not any(b["source"] == "gem: Mana Tempest" for b in found)  # its unread stat is counted by a gem_stat entry


def test_meta_round_keeps_no_group_whose_spell_never_fires(engine):
    """A bare Boneshatter crits too rarely to feed Cast on Critical: every trial is turned down (no trigger once per 30 s),
    so nothing PoB counts of the spell besides its damage (a buff at full) can make the group pay. Cast on Dodge, which
    any build feeds (dodge rolls), is banned here to keep the test on Cast on Critical."""
    import time

    payload = make_payload(BuildRequest(main_skill="Boneshatter", ascendancies=["Titan"],
                                        forbid={"skills": ["Cast on Dodge"]}))
    skeleton = enumerate_skeletons(Context(payload), ["Boneshatter"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    before = [[g["name"] for g in group["gems"]] for group in search.build.groups()]
    tried = []
    trial = search.gems.trial_meta
    search.gems.trial_meta = lambda gems: tried.append(trial(gems)) or tried[-1]
    assert search.gems.meta_round(time.perf_counter() + 60) is False
    assert tried and all(score is None for score in tried)  # Cast on Critical was screened in and tried for real
    assert [[g["name"] for g in group["gems"]] for group in search.build.groups()] == before


def test_a_build_that_counts_on_cast_on_dodge_loses_its_rolling_time(engine):
    """The user, 7 Nov: a build whose damage counts on Cast on Dodge loses the time its rolls take (0.75 s per roll, one
    per 2 s: 37.5%) from its main skill; PoB applies the line to that skill's speed."""
    payload = make_payload(BuildRequest(main_skill="Boneshatter", ascendancies=["Titan"]))
    skeleton = enumerate_skeletons(Context(payload), ["Boneshatter"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    before = build.call("stats", fields=["Speed"])["Speed"]
    build.set_group(gems=[{"name": "Cast on Dodge", "level": 20, "quality": 0},
                          {"name": "Fireball", "level": 20, "quality": 0}], label="meta", fullDPS=True)
    search.resolve()
    after = build.call("stats", fields=["Speed"])["Speed"]
    assert after == pytest.approx(before * 0.625, rel=0.02)
    assert any("Time spent dodge rolling" in n["label"] for n in search.assumptions.active)


def test_a_triggered_summon_counts_the_minions_alive_not_one_casting_nonstop(engine):
    """MECH15: Wardbound Minions in Cast on Dodge (2b75b's Absent Amulet) summons a Temporary caster per use, 15 s and at
    most 6 casts each; PoB counts one caster casting nonstop. The model counts the trigger rate times each one's life."""
    from builder.search import models

    payload = make_payload(BuildRequest(main_skill="Boneshatter", ascendancies=["Titan"]))
    skeleton = enumerate_skeletons(Context(payload), ["Boneshatter"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    build.set_group(gems=[{"name": "Cast on Dodge", "level": 20, "quality": 0},
                          {"name": "Wardbound Minions", "level": 20, "quality": 0}], label="meta", fullDPS=True)
    skill = next(models.Skill.of(s) for s in build.call("activeSkills")["skills"] if s["name"] == "Wardbound Minions")
    assert skill.minion["duration"] == pytest.approx(15.0) and skill.minion["maxCasts"] == 6
    assert skill.minion["castTime"] == 1 and skill.minion["limit"] >= 1
    count, basis = models.minion_count(skill, 0.1)
    assert count == pytest.approx(0.1 * 6 / skill.minion["speed"]) and "6 casts" in basis
    search.resolve()
    note = next(n for n in search.assumptions.active if "minions alive on average" in n["label"])
    assert 0 < note["value"] < 1  # one roll per 2 s pays for a use every ~10 s: well under PoB's one caster


def test_pob_gives_every_input_of_the_recovery_report(engine):
    """Recovery against a boss reads PoB's outputs by name: on a real build the boss's hits land and the build regenerates."""
    from builder.search.objective import RECOVERY_INPUTS, boss_recovery

    search = spark_with(engine, [])
    build = search.build
    amulet = "\n".join(["Rarity: Rare", "Test Amulet", "Gold Amulet", "Item Level: 82", "Implicits: 0",
                        "Regenerate 100 Life per second"])
    build.call("equip", slot="Amulet", raw=amulet, fields=[])
    stats = build.call("stats", fields=RECOVERY_INPUTS)
    r = boss_recovery(stats)
    assert r["incoming"] > 0 and r["parts"]["Life"] >= 100 * 0.99


def test_supports_round_fills_the_meta_group(engine):
    """Slice 2 of the meta plan: the supports sweep runs on the meta group too. Its candidates are PoB's for the group (they
    support the triggered spell), it keeps the levels the meta gem and spell were set at, and the main group is untouched."""
    from builder.search.gems import META_LABEL

    payload = make_payload(BuildRequest(main_skill="Boneshatter", ascendancies=["Titan"]))
    skeleton = enumerate_skeletons(Context(payload), ["Boneshatter"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build, gems = search.build, search.gems
    build.objective.base_full = True
    main_before = [[g["name"] for g in group["gems"]] for group in build.groups() if group["main"]]
    build.set_group(gems=[{"name": "Cast on Dodge", "level": 12, "quality": 0}, {"name": "Fireball", "level": 9, "quality": 0}],
                    label=META_LABEL, fullDPS=True)
    search.resolve()
    group = gems.supports_group(build.groups(), meta=True)
    candidates = set(gems.candidates(group["index"], build.groups()))
    assert candidates and gems.meta_shortlist is None
    gems.supports_round(full_sweep=True, meta=True)
    assert gems.meta_shortlist and set(gems.meta_shortlist) <= candidates
    group = gems.supports_group(build.groups(), meta=True)
    actives = {g["name"]: g["level"] for g in group["gems"] if not g["support"]}
    assert actives == {"Cast on Dodge": 12, "Fireball": 9}
    assert all(g["name"] in candidates for g in group["gems"] if g["support"])
    assert [[g["name"] for g in group["gems"]] for group in build.groups() if group["main"]] == main_before


def test_meta_spell_round_keeps_a_second_spell_only_if_it_pays(engine):
    """Slice 3: meta_spell_round tries a second spell for real (the Energy model halves the first's rate) and keeps it only
    if the build scores better; either way the group's first spell and levels stay, and the socket cap holds."""
    import time

    from builder.search.context import SUPPORT_SLOTS
    from builder.search.gems import META_LABEL

    payload = make_payload(BuildRequest(main_skill="Boneshatter", ascendancies=["Titan"]))
    skeleton = enumerate_skeletons(Context(payload), ["Boneshatter"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build, gems = search.build, search.gems
    build.objective.base_full = True
    build.set_group(gems=[{"name": "Cast on Dodge", "level": 12, "quality": 0}, {"name": "Fireball", "level": 9, "quality": 0}],
                    label=META_LABEL, fullDPS=True)
    search.resolve()
    before = build.score
    tried = []
    trial = gems.trial_meta_spell
    gems.trial_meta_spell = lambda *a: tried.append(trial(*a)) or tried[-1]
    kept = gems.meta_spell_round(time.perf_counter() + 120)
    assert tried  # spells were tried for real
    group = gems.supports_group(build.groups(), meta=True)
    actives = [(g["name"], g["level"]) for g in group["gems"] if not g["support"]]
    assert actives[:2] == [("Cast on Dodge", 12), ("Fireball", 9)] and len(group["gems"]) - 1 <= SUPPORT_SLOTS
    assert (len(actives) == 3) == kept and (build.score > before) == kept


def test_invocations_are_offered_only_while_the_button_cap_has_room(engine):
    """Slice 4: an Invocation is a button. With Boneshatter alone and a cap of 1, Elemental and Barrier Invocation are left
    out with the reason; with a cap of 2 they are meta candidates, and adding one counts as a button."""
    from builder.search.gems import META_LABEL

    def search_with(cap: int) -> Search:
        payload = make_payload(BuildRequest(main_skill="Boneshatter", ascendancies=["Titan"], max_buttons=cap))
        skeleton = enumerate_skeletons(Context(payload), ["Boneshatter"])[0][0]
        search = Search(engine, None, payload, skeleton.as_dict(), 0)
        search.setup(quests=False)
        return search

    full = search_with(1)
    assert full.gems.buttons() == ["Boneshatter"]
    candidates = full.gems.meta_candidates()
    assert "Elemental Invocation" not in candidates and "Barrier Invocation" not in candidates
    assert "buttons are all taken" in full.gems.left_out["Elemental Invocation"]
    assert "Cast on Critical" in candidates  # a meta that triggers by itself is no button

    room = search_with(2)
    candidates = room.gems.meta_candidates()
    assert {"Elemental Invocation", "Barrier Invocation"} <= set(candidates)
    assert "Elemental Invocation" not in room.gems.left_out
    room.build.set_group(gems=[{"name": "Elemental Invocation"}, {"name": "Fireball"}], label=META_LABEL, fullDPS=True)
    assert room.gems.buttons() == ["Boneshatter", "Elemental Invocation"]


def test_a_spell_totem_without_charges_deals_nothing(engine):
    """Slice 5: PoB counts one Spell Totem casting the spell at 25% less cast speed whatever charges the build makes. With
    no charges made, no totem goes up: the model takes its damage away, and the totem round doesn't propose it."""
    payload = make_payload(BuildRequest(main_skill="Fireball", ascendancies=["Stormweaver"]))
    skeleton = enumerate_skeletons(Context(payload), ["Fireball"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    assert build.call("stats", fields=["FullDPS"])["FullDPS"] > 0
    assert not search.gems.totem_round(float("inf"))
    assert "Spell Totem" in search.gems.left_out
    main = build.main_group()
    old = [{"name": g["name"], "level": g["level"], "quality": g["quality"]} for g in main["gems"]]
    build.set_group(index=main["index"], gems=[{"name": "Spell Totem", "level": 20, "quality": 0}, *old])
    search.resolve()
    assert build.call("stats", fields=["FullDPS"])["FullDPS"] == pytest.approx(0, abs=1e-6)
    assert any(n["label"].startswith("Spell Totems up") and n["value"] == 0 for n in search.assumptions.active)
    assert search.gems.buttons() == ["Spell Totem"]  # you press the totem, not the spell it casts


def test_a_snipers_marks_extra_crit_damage_is_scaled_to_the_crits_it_lands_on(engine):
    """Slice 6: PoB counts Sniper's Mark's extra crit damage on every crit. The model scales the Mark's effect to one crit
    per Mark (a skill-scoped "more Effect of your Curses" line, which PoB must apply to the Mark) and takes its casts out
    of the main skill's time."""
    payload = make_payload(BuildRequest(main_skill="Lightning Arrow", ascendancies=["Deadeye"]))
    skeleton = enumerate_skeletons(Context(payload), ["Lightning Arrow"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    parsed = build.call("parseLines", lines=["1% more Effect of your Curses"])["results"][0]
    assert parsed["supported"]
    without = build.call("stats", fields=["FullDPS", "CritChance"])
    assert without["CritChance"] > 0
    build.set_group(gems=[{"name": "Sniper's Mark", "level": 20, "quality": 0}], label="mark")
    build.call("setAssumptions", mods=[], fields=build.objective.fields)
    raw = build.call("stats", fields=["FullDPS"])["FullDPS"]
    search.assumptions.mods = None
    search.assumptions.apply()
    modelled = build.call("stats", fields=["FullDPS"])["FullDPS"]
    note = next(n for n in search.assumptions.active if n["label"].startswith("Sniper's Mark"))
    assert raw > without["FullDPS"]  # PoB's always-up Mark
    assert modelled < raw
    assert "takes" in note["basis"]
    # the scaling line reaches the Mark: at -100% its effect is gone
    build.call("setAssumptions", mods=[models.line("1% more Effect of your Curses", -100, skill="Sniper's Mark")],
               fields=build.objective.fields)
    assert build.call("stats", fields=["FullDPS"])["FullDPS"] == pytest.approx(without["FullDPS"], rel=1e-6)


def test_the_mark_round_adds_a_mark_only_when_it_pays_and_fits_the_button_cap(engine):
    """Slice 6: the search proposes a Mark (cast by you, or in Pounce / Hand of Chayula), priced by mark_model. A cold spell
    freezes the boss now and then: Freezing Mark's buff pays for its casts. A 5% crit bow gains less from Sniper's Mark
    than its casts cost. With one button allowed (the main skill), nothing pressed can come in."""
    def round_on(skill, ascendancy, cap):
        payload = make_payload(BuildRequest(main_skill=skill, ascendancies=[ascendancy], max_buttons=cap))
        skeleton = enumerate_skeletons(Context(payload), [skill])[0][0]
        search = Search(engine, None, payload, skeleton.as_dict(), 0)
        search.setup(quests=False)
        before = search.build.score
        added = search.gems.mark_round(float("inf"))
        names = {g["name"] for grp in search.build.groups() for g in grp["gems"]}
        return search, before, added, names & set(models.MARKS)

    search, before, added, marks = round_on("Ice Nova", "Stormweaver", None)
    assert added and marks == {"Freezing Mark"} and search.build.score > before
    assert any(n["label"].startswith("Freezing Mark") for n in search.assumptions.active)
    search, before, added, marks = round_on("Ice Nova", "Stormweaver", 1)
    assert not added and not marks and search.build.score == pytest.approx(before)
    assert search.gems.buttons() == ["Ice Nova"]
    search, before, added, marks = round_on("Lightning Arrow", "Deadeye", None)
    assert not added and not marks and search.build.score == pytest.approx(before)
    assert search.gems.left_out["Sniper's Mark"].startswith("tried")
    assert "Bloodhound's Mark" in search.gems.left_out


def test_the_spirit_step_puts_a_curse_in_blasphemy(engine):
    """Slice 7: the search never proposed Curses. The Spirit step now offers the best Curse in Blasphemy (an aura: no
    button, always on the boss; PoB prices its 60 Spirit and its less Curse effect, as the game files). Taking it out
    removes the whole group, never leaving the Curse as a button."""
    from builder.search.gems import BLASPHEMY_LABEL

    payload = make_payload(BuildRequest(main_skill="Ice Nova", ascendancies=["Stormweaver"]))
    skeleton = enumerate_skeletons(Context(payload), ["Ice Nova"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup()
    build = search.build
    print("PROBE", build.stats.get("Spirit"), build.stats.get("SpiritUnreserved"), build.stats.get("Int"))
    before = build.score
    present = {g["name"] for grp in build.groups() for g in grp["gems"]}
    option = search.gems.blasphemy_option(present, search.gems.budget_left(), 0)
    print("PROBE", option, before)
    assert option is not None and option[0] > before
    while search.gems.persistent_round():
        pass
    cursed = [g for g in build.groups() if g["label"] == BLASPHEMY_LABEL]
    assert len(cursed) == 1 and [g["name"] for g in cursed[0]["gems"]][0] == "Blasphemy"
    assert build.score > before
    assert search.gems.buttons() == ["Ice Nova"]


def test_the_mirage_round_gives_a_bow_build_a_mirage_archer(engine):
    """Slice 7: PoB counts an Attack in Mirage Archer as always on at 30% less damage; the mirage model scales it to one
    Mirage per 10 s (5 s + 50 ms per gem level and quality %), and its group counts in the score. Persistent: no
    button."""
    from builder.search.gems import MIRAGE_LABEL

    payload = make_payload(BuildRequest(main_skill="Lightning Arrow", ascendancies=["Deadeye"]))
    skeleton = enumerate_skeletons(Context(payload), ["Lightning Arrow"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup()
    build = search.build
    before = build.score
    assert search.gems.mirage_round(float("inf"))
    mirage = [g for g in build.groups() if g["label"] == MIRAGE_LABEL]
    assert len(mirage) == 1 and mirage[0]["gems"][0]["name"] == "Mirage Archer"
    notes = [n for n in search.assumptions.active if n["label"].startswith("Mirage Archer: share")]
    print("PROBE", before, build.score, [g["name"] for g in mirage[0]["gems"]], notes)
    archer = mirage[0]["gems"][0]
    share = models.mirage_duration("Mirage Archer", archer["level"], archer["quality"]) / models.MIRAGE_COOLDOWN
    assert build.score > before and notes and notes[0]["value"] == round(share, 2)
    assert "Mirage Archer" not in search.gems.buttons()
    assert not search.gems.mirage_round(float("inf"))  # (it has its Attack)


def test_the_roar_round_gives_an_attack_build_ferocious_roar(engine):
    """Slice 7: PoB reads none of Ferocious Roar's own stats. The roar round adds it to a Bear Attack build: its average
    Rage counts as more Attack damage (roar_model), and at gem level 20 its two Empowered hits (6147 each) Fully Break
    the boss's Armour (PoB's Pinnacle preset), switched on as a condition. It's a button."""
    from builder.search.gems import ROAR_LABEL

    payload = make_payload(BuildRequest(main_skill="Furious Slam"))
    skeleton = enumerate_skeletons(Context(payload), ["Furious Slam"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup()
    build = search.build
    before = build.score
    assert search.gems.roar_round(float("inf"))
    roar = [g for g in build.groups() if g["label"] == ROAR_LABEL]
    rage = [n for n in search.assumptions.active if n["label"] == "Ferocious Roar: average Rage"]
    assert build.score > before and len(roar) == 1 and rage and rage[0]["value"] == 25.0
    assert "Ferocious Roar" in search.gems.buttons()
    assert not search.gems.roar_round(float("inf"))
    build.set_group(index=roar[0]["index"], gems=[{"name": "Ferocious Roar", "level": 20, "quality": 0}])
    search.gems.resolve()
    broken = search.conditions.active.get("conditionEnemyArmourBroken")
    print("PROBE", before, build.score, broken)
    assert broken and "6,147" in broken["evidence"]


def test_gems_the_search_added_are_raised_as_the_attributes_allow(engine):
    """Task 0 (MECH8): every skill and meta gem the search adds goes in at the level usable then and is raised later, as
    persistent skills were: a roar left at gem level 1 comes back up to the highest level the build can use."""
    from builder.search.gems import ROAR_LABEL

    payload = make_payload(BuildRequest(main_skill="Furious Slam"))
    skeleton = enumerate_skeletons(Context(payload), ["Furious Slam"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup()
    build = search.build
    assert search.gems.roar_round(float("inf"))
    roar = next(g for g in build.groups() if g["label"] == ROAR_LABEL)
    usable = search.gems.usable_level("Ferocious Roar")
    assert usable and usable > 1
    build.set_group(index=roar["index"], gems=[{"name": "Ferocious Roar", "level": 1, "quality": 0}])
    search.gems.resolve()
    search.gems.relevel_added()
    roar = next(g for g in build.groups() if g["label"] == ROAR_LABEL)
    assert roar["gems"][0]["level"] == usable
    assert not search.gems.relevel_added()  # nothing left to raise


def test_ascendancy_round_takes_a_meta_granting_notable_with_its_group_filled(engine):
    """Slice 8 (MECH8): a notable granting a meta skill adds nothing until its group holds what it uses, so the ascendancy
    round tries it with the group filled (tree.granted_trial, gems.fill_granted). On a bare Deadeye Lightning Arrow build,
    Mirage Deadeye with a ranged Attack in it beats the best allocation without it. Called Shots' group (PoB gives it a
    source, so it reads as fromItem) takes a Mark from the marks round, priced at one per 5 s and no button (PoB doesn't
    flag it triggered: models.mark_use looks for Called Shots in the group)."""
    payload = make_payload(BuildRequest(main_skill="Lightning Arrow", ascendancies=["Deadeye"]))
    skeleton = enumerate_skeletons(Context(payload), ["Lightning Arrow"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup()
    build = search.build
    before = build.score
    assert search.tree.ascendancy_round()
    deadeye = next(g for g in build.groups() if g["gems"] and g["gems"][0]["name"] == "Mirage Deadeye")
    assert build.score > before and len(deadeye["gems"]) == 2 and deadeye["fromItem"]
    called = next(i for i, n in search.ctx.nodes.items() if n.get("name") == "Called Shots" and n.get("ascendancy"))
    build.allocate(add=[called])
    search.gems.resolve()
    assert search.gems.mark_round(float("inf"))
    group = next(g for g in build.groups() if g["gems"] and g["gems"][0]["name"] == "Called Shots")
    assert [g["name"] for g in group["gems"]][1:] == ["Sniper's Mark"]
    note = next(n for n in search.assumptions.active if n["label"].startswith("Sniper's Mark"))
    assert "Called Shots applies it every 5 s" in note["basis"] and "takes" not in note["basis"]  # (no button, no time)
    assert "Sniper's Mark" not in search.gems.buttons()


def test_heat_of_the_forge_fills_fire_spell_on_hit_and_prices_it_at_its_level(engine):
    """Slice 8 (MECH10): Smith of Kitava's Heat of the Forge grants Fire Spell on Hit (PoB: Fire Spell on Melee Hit), priced
    as Thundergod's Wrath (game files). The notable's trial fills its group with a spell through the granted-meta round
    (gems.fill_granted), and the trigger rate reads the meta's own level from the bridge (models.gem_energy)."""
    payload = make_payload(BuildRequest(main_skill="Boneshatter", ascendancies=["Smith of Kitava"]))
    skeleton = enumerate_skeletons(Context(payload), ["Boneshatter"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup()
    build = search.build
    heat = next(i for i, n in search.ctx.nodes.items() if n.get("name") == "Heat of the Forge" and n.get("ascendancy"))
    build.allocate(add=[heat])
    search.gems.resolve()
    assert search.gems.fill_granted("Fire Spell on Hit")
    group = next(g for g in build.groups() if g["gems"] and g["gems"][0]["name"] == "Fire Spell on Hit")  # (the skill is Fire Spell on Melee Hit)
    assert group["fromItem"] and not group["gems"][1]["support"]
    meta = next(s for s in build.call("activeSkills")["skills"] if s["name"] == "Fire Spell on Melee Hit")
    assert meta["level"] >= 1
    note = next(n for n in search.assumptions.active if n["label"].startswith("Fire Spell on Melee Hit triggers"))
    assert f"at level {meta['level']}" in note["basis"] and note["value"] > 0


def test_a_variant_base_gets_one_variant_per_rare(engine):
    """Absent Amulet's implicit is one of 7 granted skills. PoB grants none while the text carries every {variant:n} line,
    so a plain rare carries none (context.implicit_variant); the granted-meta trials build a rare per variant granting
    an Energy meta (gear.build_rare(variant=...)), and PoB grants that one."""
    payload = make_payload(BuildRequest(main_skill="Boneshatter"))
    skeleton = enumerate_skeletons(Context(payload), ["Boneshatter"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup()
    gear = search.gear
    base = next(o for o in gear.options("Amulet") if o.base == "Absent Amulet")
    assert "Grants Skill" not in base.raw and "{variant" not in base.raw
    rare = gear.build_rare("Amulet", float("inf"), base_name="Absent Amulet", variant=2)
    assert "Cast on Critical" in rare.raw and "Cast on Dodge" not in rare.raw and "{variant" not in rare.raw
    gear.put("Amulet", rare)
    gear.settle()
    assert [g["gems"][0]["name"] for g in search.gems.granted_meta_groups()] == ["Cast on Critical"]


def test_the_reverse_meta_trial_takes_a_meta_granting_item_off_when_the_build_does_better(engine):
    """MECH15: 2b75b kept a rare Absent Amulet's Cast on Dodge and its main group was left 2-3 supports. The reverse trial
    puts the best plain rare in its place, refills the main group and keeps that if better: on Boneshatter, Cast on Dodge's
    rolls cost 37.5% of its speed."""
    from builder.search.context import implicit_variants
    from builder.search.gear import GRANTS_META

    payload = make_payload(BuildRequest(main_skill="Boneshatter", ascendancies=["Titan"]))
    skeleton = enumerate_skeletons(Context(payload), ["Boneshatter"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build, gear = search.build, search.gear
    implicit = gear.ctx.bases_by_name["Absent Amulet"]["implicit"]
    dodge = next(v for v, text in implicit_variants(implicit) if "Cast on Dodge" in text)
    gear.put("Amulet", gear.build_rare("Amulet", float("inf"), base_name="Absent Amulet", variant=dodge))
    gear.settle()
    search.gems.granted_meta_round(float("inf"))
    assert "Cast on Dodge" in build.equipped["Amulet"].raw
    before = build.score
    assert gear.reverse_meta_trial("Amulet", float("inf"))
    assert not GRANTS_META.search(build.equipped["Amulet"].raw) and build.score > before


def test_an_item_granted_meta_gets_a_spell_and_a_unique_granting_one_is_tried(engine):
    """Slice 8 (MECH8): Mjolner's Thundergod's Wrath triggers nothing until a spell is socketed in it, so the granted metas
    step fills it (gems.granted_meta_round: PoB's what-if ranks spells only once the group counts in Full DPS). The gear
    round tries each meta-granting unique for real with its group filled, supports included (gear.granted_meta_trials); on a bare
    Boneshatter mace and shield build Svalinn's Cast on Block pays."""
    payload = make_payload(BuildRequest(main_skill="Boneshatter", weapon_unique="Mjolner"))
    skeleton = enumerate_skeletons(Context(payload), ["Boneshatter"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup()
    build = search.build
    before = build.score
    assert search.gems.granted_meta_round(float("inf"))
    group = next(g for g in build.groups() if g["gems"] and g["gems"][0]["name"] == "Thundergod's Wrath")
    assert build.score > before and len([g for g in group["gems"] if not g["support"]]) == 2 and group["fullDPS"]
    assert any(g["support"] for g in group["gems"])  # its spell's supports chosen too (fill_granted_supports)
    assert not search.gems.granted_meta_groups() and search.gems.filled_granted_groups() == [group]

    payload = make_payload(BuildRequest(main_skill="Boneshatter"))
    payload["unique_prices"] = {"Svalinn": [2.0, 50]}
    skeleton = next(s for s in enumerate_skeletons(Context(payload), ["Boneshatter"])[0]
                    if "Shield" in s.label and "One Hand Mace" in s.label)
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup()
    build = search.build
    before = build.score
    assert search.gear.granted_meta_trials(float("inf"))
    assert build.equipped["Weapon 2"].name == "Svalinn" and build.score > before
    group = next(g for g in build.groups() if g["gems"] and g["gems"][0]["name"] == "Cast on Block")
    assert len([g for g in group["gems"] if not g["support"]]) == 2


def test_a_spell_that_needs_corpses_counts_nothing_without_a_corpse_source(engine):
    """Nothing dies on a single boss, so Detonate Dead (the game types it TargetsDestructibleCorpses) has no corpse to
    explode unless the build makes its own, and no 0.5 player skill does: 0 uses per second as the main skill and in a
    meta group, said in the guide, and the meta round doesn't propose it."""
    payload = make_payload(BuildRequest(main_skill="Detonate Dead", ascendancies=["Infernalist"]))
    skeleton = enumerate_skeletons(Context(payload), ["Detonate Dead"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    assert any(s["name"] == "Detonate Dead" and s.get("corpses") for s in build.call("activeSkills")["skills"])
    assert build.call("stats", fields=["FullDPS"])["FullDPS"] < 1  # (a hundredth of a point from elsewhere)
    note = next(n for n in search.assumptions.active if n["label"].startswith("Corpses per second"))
    assert note["value"] == 0 and "no corpse source" in note["basis"]
    assert "Detonate Dead" not in search.gems.meta_spells(set(), float("inf"), None)
    assert "Fireball" in search.gems.meta_spells(set(), float("inf"), None)


def test_hollow_form_takes_the_main_melee_attack_as_a_button_and_is_undone_without_power_charges(engine):
    """Slice 8 (MECH11): Martial Artist's Hollow Form Technique grants Hollow Form (a group PoB gives a source: fromItem).
    With the main melee Attack moved into it you press Hollow Form, not the Attack, at 30% less attack speed (PoB's
    SupportHollowFormPlayer), and models.hollow_model counts the images. A bare build makes no Power Charges: one image
    per use at the penalties scores worse, so the round puts the build back as it was."""
    payload = make_payload(BuildRequest(main_skill="Falling Thunder", ascendancies=["Martial Artist"]))
    skeleton = enumerate_skeletons(Context(payload), ["Falling Thunder"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup()
    build = search.build
    node = next(i for i, n in search.ctx.nodes.items() if n.get("name") == "Hollow Form Technique" and n.get("ascendancy"))
    build.allocate(add=[node])
    search.gems.resolve()
    before, speed, snap = build.score, build.stats.get("Speed"), build.snapshot()
    main = build.main_group()
    hollow = next(g for g in build.groups() if g["gems"] and g["gems"][0]["name"] == "Hollow Form")
    assert hollow["fromItem"] and search.gems.buttons() == ["Falling Thunder"]
    build.set_group(index=main["index"], enabled=False)
    build.set_group(index=hollow["index"], gems=[{"name": "Hollow Form", "level": 20, "quality": 0},
                                                 {"name": "Falling Thunder", "level": 20, "quality": 0}],
                    main=True, mainActiveSkill=2)
    search.gems.resolve()
    assert search.gems.buttons() == ["Hollow Form"]
    assert build.stats.get("Speed") == pytest.approx(0.7 * speed, rel=1e-3)
    note = next(n for n in search.assumptions.active if n["label"] == "Hollow Form: images per use (Falling Thunder)")
    assert note["value"] == 1 and "no Power Charges" in note["basis"]
    build.restore(snap)
    search.gems.resolve()
    assert not search.gems.hollow_round(float("inf"))
    assert build.score == pytest.approx(before) and build.main_group()["index"] == main["index"]
    assert search.gems.left_out["Hollow Form"].startswith("tried")


def test_a_rare_dueling_wand_is_tried_for_its_spellslinger(engine):
    """Slice 8 (MECH11): a Dueling Wand's implicit grants Spellslinger, worth nothing until a spell is socketed in it, so
    the rare builder's base ranking never picks the base. The granted-meta trials build a rare on it and try it with its
    group filled; Spellslinger is an Invocation, so it's a button. Rares share one name ("Rare Wand"): the trial tells
    them apart by base."""
    payload = make_payload(BuildRequest(main_skill="Spark"))
    skeleton = next(s for s in enumerate_skeletons(Context(payload), ["Spark"])[0] if "Wand" in s.label)
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup()
    build, gear = search.build, search.gear
    other = next(o.base for o in gear.options("Weapon 1") if not o.unique and o.base != "Dueling Wand")
    gear.put("Weapon 1", gear.build_rare("Weapon 1", float("inf"), base_name=other))
    gear.settle()
    before = build.score
    assert search.gems.buttons() == ["Spark"]
    assert gear.granted_meta_trials(float("inf"))
    wand = build.equipped["Weapon 1"]
    assert wand.base == "Dueling Wand" and wand.rare and build.score > before
    group = next(g for g in build.groups() if g["gems"] and g["gems"][0]["name"] == "Spellslinger")
    assert group["fromItem"] and len([g for g in group["gems"] if not g["support"]]) == 2
    assert search.gems.buttons() == ["Spark", "Spellslinger"]


def test_bijouborne_is_tried_and_its_cast_on_charm_use_gets_no_spell_without_charm_uses(engine):
    """Slice 8 (MECH11): Bijouborne grants Cast on Charm Use, priced from the charm charges the build spends against a
    boss (flasks.charm_charges_used). On a bare Spark build no charm is equipped: the belt is a granted-meta trial,
    but no spell goes into its group, as nothing would trigger it. (Whether the trial keeps the belt for its other
    lines depends on what else the same pass keeps, an Absent Amulet's Cast on Critical say, so it's equipped here.)"""
    from builder.search.gear import GRANTS_META

    payload = make_payload(BuildRequest(main_skill="Spark"))
    payload["unique_prices"] = {"Bijouborne": [1.0, 50]}
    skeleton = enumerate_skeletons(Context(payload), ["Spark"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup()
    build = search.build
    belt = next(o for o in search.gear.candidates("Belt") if o.unique and o.name == "Bijouborne")
    assert GRANTS_META.search(belt.raw)
    search.gear.put("Belt", belt)
    search.gear.settle()
    search.gems.granted_meta_round(float("inf"))
    group = next(g for g in build.groups() if g["gems"] and g["gems"][0]["name"] == "Cast on Charm Use")
    assert len(group["gems"]) == 1
    from builder.search.assumptions import charm_stats
    assert charm_stats(build)["_charm_charges"] == 0


def test_a_meta_kick_takes_the_meta_group_out_and_the_meta_steps_rechoose_it_in_the_same_pass(engine):
    """Slice 9: the meta round adds a group only when the build has none, so without this kick a meta chosen early (or
    a seed's) stayed for the whole search. A required meta, or one an item grants, is never kicked."""
    import time
    from builder.search.gems import META_LABEL

    def boneshatter(**body) -> Search:
        payload = make_payload(BuildRequest(main_skill="Boneshatter", ascendancies=["Titan"], **body))
        skeleton = enumerate_skeletons(Context(payload), ["Boneshatter"])[0][0]
        search = Search(engine, None, payload, skeleton.as_dict(), 0)
        search.setup(quests=False)
        search.build.set_group(gems=[{"name": "Cast on Dodge", "level": 20, "quality": 0},
                                     {"name": "Fireball", "level": 20, "quality": 0}], label=META_LABEL, fullDPS=True)
        return search

    search = boneshatter()
    assert search.meta_kickable()
    count = len(search.build.groups())
    search.kick_meta()
    assert len(search.build.groups()) == count - 1 and not search.meta_kickable()
    assert search.build.main_group()["gems"][0]["name"] == "Boneshatter"

    phases = []
    search.step = lambda phase, fn, *args: phases.append(phase) or False
    search.passes = 1  # not a sweep pass
    search.kicked = {"meta"}
    search.blocks(time.perf_counter() + 60)
    assert "meta gems" in phases and "meta spells" in phases and "totems" not in phases
    phases.clear()
    search.blocks(time.perf_counter() + 60)  # the next pass between sweeps skips them again
    assert "meta gems" not in phases

    assert not boneshatter(require={"skills": ["Cast on Dodge"]}).meta_kickable()


def test_spirit_vessel_gets_the_best_shapeshift_skills_then_supports(engine):
    """gems.spirit_vessel_round: Forgotten Warden's empty Spirit Vessel group is a granted group the granted metas step
    fills, with Bear, Werewolf or Wyvern skills one at a time while the score rises, then supports; the group counts in
    Full DPS, and the skills its companion uses aren't buttons."""
    from builder.search.models import SPIRIT_VESSEL, SPIRIT_VESSEL_TYPES

    payload = make_payload(BuildRequest(main_skill="Boneshatter"))
    payload["unique_prices"] = {"Forgotten Warden": [2.0, 50]}
    skeleton = enumerate_skeletons(Context(payload), ["Boneshatter"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup()
    build = search.build
    option = next(o for o in search.gear.options("Body Armour") if o.unique and o.name == "Forgotten Warden")
    search.gear.put("Body Armour", option)
    search.gear.settle()
    assert [g["gems"][0]["name"] for g in search.gems.granted_meta_groups()] == [SPIRIT_VESSEL]
    before = build.score
    assert search.gems.granted_meta_round(float("inf"))
    group = next(g for g in build.groups() if g["gems"] and g["gems"][0]["name"] == SPIRIT_VESSEL)
    skills = [g["name"] for g in group["gems"][1:] if not g["support"]]
    assert build.score > before and skills and group["fullDPS"]
    assert all(SPIRIT_VESSEL_TYPES & set(search.ctx.gems[n]["skillTypes"]) for n in skills)
    assert search.gems.filled_granted_groups() == [group] and not set(skills) & set(search.gems.buttons())


def test_spirit_vessel_is_a_minion_using_the_socketed_shapeshift_skills_which_you_cant_use(engine):
    """engine/bridge.lua installSpiritVessel: Forgotten Warden's companion (game-file monster numbers), 20% more damage
    per different socketed skill, nothing when empty, and the socketed skills disabled for the player."""
    import json
    from builder import ROOT

    uniques = json.loads(next((ROOT / "data").glob("pob-*/uniques.json")).read_text(encoding="utf-8"))["uniques"]
    raw = next(u["raw"] for u in uniques if u["name"] == "Forgotten Warden")
    engine.call("newBuild")
    engine.call("setLevel", level=90)
    engine.call("equip", slot="Body Armour", raw=raw, fields=[])
    vessel = next(g for g in engine.call("groups")["groups"] if any(x["name"] == "Spirit Vessel" for x in g["gems"]))
    fields = ["Minion.TotalDPS", "Minion.AverageDamage", "FullDPS"]

    def socket(*names):
        gems = [{"name": "Spirit Vessel", "level": 16, "quality": 0}] + [{"name": n, "level": 20, "quality": 0} for n in names]
        engine.call("setGroup", index=vessel["index"], gems=gems, main=True, fullDPS=True, fields=[])
        return engine.call("stats", fields=fields)

    assert not socket()["Minion.TotalDPS"]  # PoB would otherwise give an empty vessel a melee hit
    one = socket("Cross Slash")
    assert one["Minion.TotalDPS"] > 0 and one["FullDPS"] == pytest.approx(one["Minion.TotalDPS"])
    skills = {s["name"]: s["disabled"] for s in engine.call("activeSkills")["skills"]}
    assert skills == {"Spirit Vessel": False, "Cross Slash": True}
    two = socket("Cross Slash", "Maul")  # the companion still uses Cross Slash first: 1.4 / 1.2 more (and Maul's buffs)
    assert two["Minion.AverageDamage"] / one["Minion.AverageDamage"] == pytest.approx(1.4 / 1.2, rel=0.03)
    engine.call("newBuild")


def test_presence_lines_need_the_boss_inside_the_presence_radius(engine):
    """'while a Rare or Unique Enemy is in your Presence' needs PoB's EnemyInPresence too (bridge), and the resolver sets
    the boss's distance by play style: 5 m for a bow skill, outside the default 4 m Presence; 2 m for a melee one."""
    line = "4% increased Attack Speed while a Rare or Unique Enemy is in your Presence"
    tags = engine.call("parseLines", lines=[line])["results"][0]["parsed"][0]["tags"]
    assert "Condition:EnemyInPresence" in tags
    combined = engine.call("parseLines", lines=["10% chance to Aggravate Bleeding on targets you Hit with Attacks " + line])
    gated = ["Condition:EnemyInPresence" in mod["tags"] for mod in combined["results"][0]["parsed"]]
    assert gated.count(True) == 1 or not combined["results"][0]["supported"]
    for skill, ascendancy, metres in (("Lightning Arrow", "Deadeye", 5.0), ("Boneshatter", "Titan", 2.0)):
        payload = make_payload(BuildRequest(main_skill=skill, ascendancies=[ascendancy]))
        skeleton = enumerate_skeletons(Context(payload), [skill])[0][0]
        search = Search(engine, None, payload, skeleton.as_dict(), 0)
        search.setup(quests=False)
        active = search.conditions.active["enemyDistance"]
        assert active["value"] == metres * 10
        assert ("so the boss is outside it" in active["evidence"]) == (metres > 4)
        assert engine.call("getConfig")["input"]["enemyDistance"] == metres * 10


def test_main_sweep_moves_a_support_another_group_holds_only_if_it_pays(engine, monkeypatch):
    """A lineage support is socketed once per character, so one in another group never reaches the main group's
    candidates. A full sweep of the main group offers it; the trial moves it out of the other group and stays only if
    the build's score doesn't drop."""
    from builder.search.gems import META_LABEL

    payload = make_payload(BuildRequest(main_skill="Boneshatter", ascendancies=["Titan"]))
    skeleton = enumerate_skeletons(Context(payload), ["Boneshatter"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build, gems = search.build, search.gems
    build.set_group(gems=[{"name": "Cast on Dodge", "level": 12, "quality": 0}, {"name": "Fireball", "level": 9, "quality": 0}],
                    label=META_LABEL, fullDPS=True)
    main = gems.supports_group(build.groups(), meta=False)
    meta = gems.supports_group(build.groups(), meta=True)
    in_main = {g["name"] for g in main["gems"]}
    both = sorted(set(build.call("supportCandidates", group=main["index"])["names"])
                  & set(build.call("supportCandidates", group=meta["index"])["names"]) - in_main)
    gem = next(n for n in both if gems.ctx.allowed_gem(n) and gems.usable(n) and gems.ctx.gem_price(n) is not None)
    limit = gems.copies_allowed
    monkeypatch.setattr(gems, "copies_allowed", lambda name: 1 if name == gem else limit(name))  # as a lineage support
    build.set_group(index=meta["index"], gems=[*({"name": g["name"], "level": g["level"], "quality": g["quality"]}
                                                 for g in meta["gems"]), {"name": gem}])
    assert gem not in gems.candidates(main["index"], build.groups())

    def names(meta_group: bool) -> list[str]:
        return [g["name"] for g in gems.supports_group(build.groups(), meta=meta_group)["gems"]]

    monkeypatch.setattr(build, "gem_deltas", lambda cases, **kw: [(build.score + (1.0 if c["gem"] == gem else 0.0), {})
                                                                   for c in cases])
    monkeypatch.setattr(build, "worse", lambda before: True)
    before = (names(False), names(True))
    gems.supports_round(full_sweep=True)
    assert (names(False), names(True)) == before  # not kept: both groups as they were
    monkeypatch.setattr(build, "worse", lambda before: False)
    gems.supports_round(full_sweep=True)
    assert gem in names(False) and gem not in names(True)
    assert "Fireball" in names(True)


def test_a_support_a_sweep_move_takes_out_joins_the_shortlist(engine, monkeypatch):
    """The support a sweep's move replaces was never ranked (it was in the group), so later weakest-slot rounds couldn't
    put it back in another slot: 2b75d's finalize swapped Exploit Weakness for Deadly Resolve and stopped there (POE2T285)."""
    payload = make_payload(BuildRequest(main_skill="Boneshatter", ascendancies=["Titan"]))
    skeleton = enumerate_skeletons(Context(payload), ["Boneshatter"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build, gems = search.build, search.gems
    main = gems.supports_group(build.groups(), meta=False)
    offered = [c for c in gems.candidates(main["index"], build.groups()) if gems.usable(c)]
    out, into = offered[0], next(c for c in offered[1:] if not gems.families(c) & gems.families(offered[0]))
    gems.commit(main["index"], ["Boneshatter", out])
    monkeypatch.setattr(build, "gem_deltas", lambda cases, **kw: [
        (build.score + (1.0 if (c["slot"], c["gem"]) == (2, into) else -0.001 * i), {}) for i, c in enumerate(cases)])
    monkeypatch.setattr(build, "worse", lambda before: False)
    gems.supports_round(full_sweep=True)
    names = [g["name"] for g in gems.supports_group(build.groups(), meta=False)["gems"]]
    assert names == ["Boneshatter", into]
    assert out in gems.shortlist


def test_mana_flare_rate_is_capped_by_crits_and_its_damage_by_the_mana_it_finds(engine):
    """Mana Flare fires on the supported spell's Critical Hits, once per 1 s cooldown at most, and takes 25% of current
    mana (game files). PoB gives it no rate and full mana; the cooldown fallback assumed one per second."""
    rate, share, _ = models.mana_flare({"Speed": 2.0, "HitChance": 100, "CritChance": 20, "Mana": 1000,
                                        "ManaRegenRecovery": 50, "ManaPerSecondCost": 30}, 1.0, 1.0)
    assert rate == pytest.approx(0.4) and share == pytest.approx(20 / (0.25 * 0.4) / 1000)
    rate, share, _ = models.mana_flare({"Speed": 3.0, "CritChance": 100, "Mana": 100, "ManaRegenRecovery": 50}, 1.0, 1.0)
    assert rate == pytest.approx(1.0) and share == 1.0
    assert models.mana_flare({"Speed": 3.0, "CritChance": 50, "Mana": 100, "ManaRegenRecovery": 5,
                              "ManaPerSecondCost": 9}, 1.0, 1.0)[1] == 0.0

    payload = make_payload(BuildRequest(main_skill="Fireball"))
    skeleton = enumerate_skeletons(Context(payload), ["Fireball"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build, gems = search.build, search.gems
    main = build.main_group()
    gems.commit(main["index"], ["Fireball", "Mana Flare"])
    build.set_group(index=main["index"], mainActiveSkill=2)
    assert search.assumptions.main_skill() == "Mana Flare"
    mods, notes = search.assumptions.wanted()
    note = next(n for n in notes if n["label"].startswith("Mana Flare uses per second"))
    print(note["label"], note["value"], "|", note["basis"], "|", [m for m in mods if m.get("skill") == "Mana Flare"])
    assert "Fireball" in note["label"] and 0.0 <= note["value"] <= 1.0
    assert any(m.get("skill") == "Mana Flare" and m.get("key") == "triggerRate" for m in mods)
    assert search.assumptions.main_skill() == "Mana Flare"  # (the spell was calculated for a moment, then put back)
    assert not any("cooldown allows" in n.get("basis", "") for n in notes)
    # beside the spell it supports, counted through Full DPS (2b75e: Ice Nova's group), the same model; not counted, none
    build.set_group(index=main["index"], mainActiveSkill=1, fullDPS=True)
    mods, notes = search.assumptions.wanted()
    assert [n["value"] for n in notes if n["label"].startswith("Mana Flare uses")] == [note["value"]]
    build.set_group(index=main["index"], fullDPS=False)
    mods, notes = search.assumptions.wanted()
    assert not any(n["label"].startswith("Mana Flare uses") for n in notes)
    # in another group counted through Full DPS (MECH24): the same model, that group's spell calculated for a moment
    gems.commit(main["index"], ["Fireball"])
    other = build.set_group(gems=[{"name": "Fireball", "level": 20, "quality": 0},
                                  {"name": "Mana Flare", "level": 20, "quality": 0}], fullDPS=True)
    assert other["index"] != main["index"]
    mods, notes = search.assumptions.wanted()
    flares = [n for n in notes if n["label"].startswith("Mana Flare uses")]
    assert len(flares) == 1 and f"socket group {other['index']}" in flares[0]["basis"]
    assert any(m.get("skill") == "Mana Flare" and m.get("key") == "triggerRate" for m in mods)
    assert build.main_group()["index"] == main["index"] and search.assumptions.main_skill() == "Fireball"
    build.set_group(index=other["index"], fullDPS=False)
    mods, notes = search.assumptions.wanted()
    assert not any(n["label"].startswith("Mana Flare uses") for n in notes)


def test_a_normal_support_may_sit_in_several_skills_and_a_lineage_one_once_or_three_times_with_solus_ipse(engine):
    """The 0.5 rule (game files, KeywordPopups SupportGem, SupportGemCategory, LineageSupports): supports of one category
    once per skill, a normal support in any number of skills, a lineage support (and the 21 global-benefit ones) in one
    skill, or three with Solus Ipse ("You can Socket 2 additional copies of each Lineage Support Gem, in different
    Skills"). The search had every support once per character (e4767b1)."""
    from types import SimpleNamespace

    from builder.search.gems import META_LABEL

    payload = make_payload(BuildRequest(main_skill="Boneshatter", ascendancies=["Titan"]))
    skeleton = enumerate_skeletons(Context(payload), ["Boneshatter"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build, gems = search.build, search.gems
    build.set_group(gems=[{"name": "Cast on Dodge", "level": 12, "quality": 0}, {"name": "Fireball", "level": 9, "quality": 0}],
                    label=META_LABEL, fullDPS=True)
    main = gems.supports_group(build.groups(), meta=False)
    meta = gems.supports_group(build.groups(), meta=True)
    both = sorted(set(build.call("supportCandidates", group=main["index"])["names"])
                  & set(build.call("supportCandidates", group=meta["index"])["names"]))
    normal = next(n for n in both if gems.copies_allowed(n) is None and gems.ctx.allowed_gem(n)
                  and gems.ctx.gem_price(n) is not None and n not in {g["name"] for g in main["gems"]})
    lineage = next(n for n in build.call("supportCandidates", group=meta["index"])["names"]
                   if gems.ctx.gems[n].get("lineage") and n not in {g["name"] for g in main["gems"]})
    actives = [{"name": g["name"], "level": g["level"], "quality": g["quality"]} for g in meta["gems"]]
    build.set_group(index=meta["index"], gems=[*actives, {"name": normal}, {"name": lineage}])
    assert normal in gems.candidates(main["index"], build.groups())
    assert gems.used_supports(build.groups(), main["index"]) == {lineage}
    assert gems.copies_allowed("Precision I") == 1 and gems.copies_allowed(lineage) == 1
    build.equipped = {**build.equipped, "Helmet": SimpleNamespace(
        raw="Solus Ipse\nGrand Visage\nYou can Socket 2 additional copies of each Lineage Support Gem, in different Skills")}
    assert gems.copies_allowed(lineage) == 3 and gems.copies_allowed("Precision I") == 1
    assert gems.used_supports(build.groups(), main["index"]) == set()


def test_the_global_benefit_supports_are_the_game_files_flagged_ones():
    """GLOBAL_BENEFIT_SUPPORTS against SupportGems' last column, when the exported tables are here (data/ is not in git)."""
    import glob
    import json

    from builder.search.gems import GLOBAL_BENEFIT_SUPPORTS

    from pathlib import Path

    found = sorted(glob.glob(str(Path(__file__).resolve().parents[1] / "data" / "game-*" / "tables" / "SupportGems.json")))
    if not found:
        pytest.skip("no exported game tables")
    folder = found[-1].rsplit("SupportGems.json", 1)[0]
    load = lambda name: json.load(open(folder + name, encoding="utf-8"))
    support, skill, base = load("SupportGems.json"), load("SkillGems.json"), load("BaseItemTypes.json")
    flagged = {base[skill[row["SkillGem"]]["BaseItemType"]]["Name"] for row in support if row["_unnamed8"]}
    assert flagged == set(GLOBAL_BENEFIT_SUPPORTS)
