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
