"""Custom models through a real engine (PoB alone vs PoB with the model). Boots one PoB engine for
the module (a few seconds) and needs the game-data dump."""

from __future__ import annotations

import faulthandler
from dataclasses import replace

import pytest

from builder.data import GameData
from builder.request.schema import BuildRequest
from builder.search.context import Context, make_payload
from builder.search import gems as gems_module
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


def test_flasks_do_not_recover_life_blocks_life_flask_recovery(engine):
    """PoB ignores "Flasks do not recover Life" (Sanguine Tides, UniqueLifeFlaskNoRecovery1); the bridge flags it, so the
    sustain floor's Life flask share is 0 (an overcount before: a Blood Mage passed its Life floor on flasks)."""
    from builder.search.flasks import Charges

    build = spark_with(engine, []).build
    assert Charges.from_bridge(build.call("chargeGain")).life_flask_blocked is False
    amulet = "\n".join(["Rarity: Rare", "Test Amulet", "Gold Amulet", "Item Level: 82", "Implicits: 0", "Flasks do not recover Life"])
    build.call("equip", slot="Amulet", raw=amulet, fields=[])
    assert Charges.from_bridge(build.call("chargeGain")).life_flask_blocked is True


def test_a_switched_off_charm_gives_the_models_no_lines(engine):
    """MECHC4 (COVD5): a flask or charm slot switched off (under flasks.ACTIVE_UPTIME) gives no lines to sourceLines;
    before, The Fall of the Axe's "Grants Onslaught during effect" reached the Onslaught model from a switched-off charm
    and counted full-time. chargeGain also carries what fills and drains Life and Mana (flasks.full_pool_cap)."""
    from builder.search.flasks import Charges

    build = spark_with(engine, []).build
    charm = chr(10).join(["Rarity: Magic", "Thawing Charm", "Item Level: 82", "Implicits: 0", "Grants Onslaught during effect"])
    texts = lambda: [line["text"] for line in build.call("sourceLines")["lines"] if line["source"] == "item:Charm 1"]
    build.call("equip", slot="Charm 1", raw=charm, active=True, fields=[])
    assert "Grants Onslaught during effect" in texts()
    build.call("equip", slot="Charm 1", raw=charm, active=False, fields=[])
    assert texts() == []
    gain = Charges.from_bridge(build.call("chargeGain"))
    assert gain.taken_hit > 0 and gain.mana_cost > 0 and gain.life_effect_kept is False


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


def test_electrocuting_arrows_rod_stats_scale_with_a_raw_effect_mod(engine):
    """MECH40: PoB counts Electrocuting Arrow's Rod quality stats always on (a Buff: extra Lightning on debuffed targets);
    models.rod_model scales them with a Raw BuffEffect / DebuffEffect MORE on the skill, which must reach PoB."""
    payload = make_payload(BuildRequest(main_skill="Electrocuting Arrow", ascendancies=["Deadeye"]))
    skeleton = enumerate_skeletons(Context(payload), ["Electrocuting Arrow"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    build.set_group(gems=[{"name": "Electrocuting Arrow", "level": 20, "quality": 20}], label="rod")
    cut = [models.raw_mod("BuffEffect", "MORE", -100, "Electrocuting Arrow"),
           models.raw_mod("DebuffEffect", "MORE", -100, "Electrocuting Arrow")]
    build.call("setAssumptions", mods=cut, fields=build.objective.fields)
    without = build.call("stats", fields=["CombinedDPS"])["CombinedDPS"]
    half = [{**m, "value": -50} for m in cut]
    build.call("setAssumptions", mods=half, fields=build.objective.fields)
    halfway = build.call("stats", fields=["CombinedDPS"])["CombinedDPS"]
    build.call("setAssumptions", mods=[], fields=build.objective.fields)
    full = build.call("stats", fields=["CombinedDPS"])["CombinedDPS"]
    assert full > halfway > without > 0  # the quality Buff, always on in PoB, scaled by the Raw mods


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
    # MECH32: an EnemyModifier line ("Enemies you Mark take ...", a LIST holding one nested mod) is scaled too, so the
    # Marked share's correction line cancels the line it scales (the model's -X x (1 - marked))
    taken = "Enemies you Mark take 1% increased Damage"
    def dps(*scales):
        build.call("setAssumptions", mods=[models.line(taken, k) for k in scales], fields=build.objective.fields)
        return build.call("stats", fields=["FullDPS"])["FullDPS"]
    assert dps(50) > dps(1) > raw
    assert dps(50, -50) == pytest.approx(raw, rel=1e-6)


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
    # MECH33: the Aura reaches the boss only inside your Presence, so a caster at range gains once it stands close
    search.choose_distance()
    assert search.conditions.distance and search.conditions.distance[0] <= 2.0
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


def test_a_variant_base_keeps_its_affix_limits_and_its_non_meta_variants_are_ranked(engine, monkeypatch):
    """GEAR3: an Absent Amulet's rare holds 2 prefixes and 2 suffixes (its "-1 ... Modifier allowed" lines; the rare
    builder had made 3 and 3), the three variant amulets don't take the amulet places of the plain bases (Solar Amulet's
    Spirit was never offered; every amulet base is now kept), and with the variant trials on, the non-meta variants (Archmage, heralds, banners, ...)
    are ranked by what their bare item adds and the best VARIANT_TRIALS tried for real."""
    from builder.search import gear as gear_module
    from builder.search.gear import GRANTS_META, affix_limits

    assert affix_limits({"implicit": "-1 Prefix Modifier allowed\n-1 Suffix Modifier allowed"}) == {"Prefix": 2, "Suffix": 2}
    assert affix_limits({"implicit": "+1 Prefix Modifier allowed\n-1 Suffix Modifier allowed"}) == {"Prefix": 4, "Suffix": 2}
    assert affix_limits({"implicit": "+(10-15) to Spirit"}) == {"Prefix": 3, "Suffix": 3}
    payload = make_payload(BuildRequest(main_skill="Boneshatter"))
    skeleton = enumerate_skeletons(Context(payload), ["Boneshatter"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup()
    build, gear = search.build, search.gear
    bases = {o.base for o in gear.options("Amulet") if not o.unique}
    assert {"Absent Amulet", "Lament Amulet", "Portent Amulet", "Solar Amulet"} <= bases
    # The bare build is short of its floors (score about -63, probe POE2T611), so no skill moves it: the what-if is
    # made to pay for Herald of Ash alone, and Lament Amulet's variant for it is the one built
    real = build.gem_deltas
    asked = []

    def deltas(cases, skip_ehp=True):
        asked.extend(c["newGroup"][0] for c in cases)
        return [(build.score + (1.0 if c["newGroup"][0] == "Herald of Ash" else 0.0), {}) for c in cases]

    monkeypatch.setattr(build, "gem_deltas", deltas)
    found = gear.variant_options("Amulet", build.equipped.get("Amulet"), float("inf"))
    monkeypatch.setattr(build, "gem_deltas", real)
    assert "Archmage" in asked and "Blink" in asked and "Cast on Dodge" not in asked and "Boneshatter" not in asked
    assert len(found) == 1
    option, key = found[0]
    assert option.base == "Lament Amulet" and key[1] == "Lament Amulet" and "Herald of Ash" in key[2]
    assert option.raw.count("Grants Skill") == 1 and "Herald of Ash" in option.raw and not GRANTS_META.search(option.raw)
    monkeypatch.setattr(gear_module, "VARIANT_META_TRIALS", True)
    before = build.score
    gear.granted_meta_trials(float("inf"))
    assert build.score >= before - 1e-9


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
    # (COVC5, the manager's ruling) the moved Attack gets support rounds: the granted group is the one a round fills,
    # and the Attack counts against Hollow Form's sockets like a totem's spell (UNVERIFIED, the cautious reading)
    gems = search.gems
    assert gems.supports_group(build.groups(), False)["index"] == hollow["index"]
    assert gems.supports_round() and len(build.main_group()["gems"]) == 3
    assert build.main_group()["gems"][2]["support"]
    build.restore(snap)
    gems.resolve()
    assert not gems.hollow_round(float("inf"))
    assert build.score == pytest.approx(before) and build.main_group()["index"] == main["index"]
    assert gems.left_out["Hollow Form"].startswith("tried")
    # (COVC5) after the move the round chooses the Attack's part and stat set again, in the Hollow Form group, before it
    # compares scores (with the resolver off, so only that call can choose)
    resolve, gems.resolve, calls = gems.resolve, None, []
    choose = gems.choose_active_skill
    gems.choose_active_skill = lambda *a, **k: (calls.append(build.main_group()["index"]), choose(*a, **k))[1]
    try:
        gems.hollow_round(float("inf"))
    finally:
        gems.resolve = resolve
        del gems.choose_active_skill
        build.restore(snap)
        gems.resolve()
    assert calls == [hollow["index"]]


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
    search.passes = search.line_passes = 1  # not a sweep pass (M1 counts the line's passes)
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
    gems.failed.clear()  # (as if the build had changed: a move that failed on the same build isn't tried again)
    gems.supports_round(full_sweep=True)
    assert gem in names(False) and gem not in names(True)
    assert "Fireball" in names(True)


def test_a_support_whose_trial_fails_lets_the_next_one_be_tried_and_is_not_tried_again(engine, monkeypatch):
    """The what-if doesn't resolve conditions, so a support can rank first and lose in its trial every round (Exploit
    Weakness took away the armour break it counted, 2b75d, POE2T351). The next moves get a trial (MAIN_TRIALS), and a move
    that failed on the same build isn't tried again."""
    payload = make_payload(BuildRequest(main_skill="Boneshatter", ascendancies=["Titan"]))
    skeleton = enumerate_skeletons(Context(payload), ["Boneshatter"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build, gems = search.build, search.gems
    main = gems.supports_group(build.groups(), meta=False)
    held = {g["name"] for g in main["gems"]}
    offered = [n for n in gems.candidates(main["index"], build.groups()) if n not in held and gems.usable(n)]
    taken = set().union(*(gems.families(n) for n in held))
    bad, good = [n for n in offered if not gems.families(n) & taken][:2]
    gains = {bad: 1.0, good: 0.5}
    monkeypatch.setattr(build, "gem_deltas", lambda cases, **kw: [(build.score + gains.get(c["gem"], 0.0), {}) for c in cases])
    tried = []
    monkeypatch.setattr(gems, "commit", lambda index, names, group=None: tried.append(
        next((n for n in names if n not in held), None)))
    judged = []  # the trials (discover's own measuring commits aren't judged)
    monkeypatch.setattr(build, "worse", lambda before: judged.append(tried[-1]) or tried[-1] == bad)
    monkeypatch.setattr(gems, "take_back", lambda *a, **kw: None)
    gems.supports_round(full_sweep=True)
    assert judged == [bad, good]  # the failed first move didn't use up the round
    gains.pop(good)
    judged.clear()
    gems.supports_round(full_sweep=True)
    assert bad not in judged  # nothing changed since it failed
    build.score += gems_module.RETRY_MOVE / 2
    gems.supports_round(full_sweep=True)
    assert bad not in judged  # other steps moved the score a little: still remembered (Deliberation, POE2T358)
    build.score += gems_module.RETRY_MOVE
    gems.supports_round(full_sweep=True)
    assert bad in judged  # the build grew enough since: tried again


def test_a_kept_support_move_is_where_the_hard_stop_goes_back_to(engine, monkeypatch):
    """A probe's supports step cut by the hard stop went back to the step's start: gate 4's 2b75d had two of five probe
    lines at 897 DPS with no support in the main group (POE2T358). Each kept round marks a checkpoint, as the tree's do."""
    payload = make_payload(BuildRequest(main_skill="Boneshatter", ascendancies=["Titan"]))
    skeleton = enumerate_skeletons(Context(payload), ["Boneshatter"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    gems = search.gems
    assert gems.checkpoint == search.checkpoint
    rounds = iter([True, True, False])
    monkeypatch.setattr(gems, "supports_round", lambda **kw: next(rounds, False))
    marks = []
    gems.checkpoint = lambda: marks.append(1)
    gems.optimize_supports(float("inf"), rounds=3)
    assert len(marks) == 2


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


def test_the_kalguuran_supports_are_the_game_files_runeforged_ones():
    """KALGUURAN_SUPPORTS against the supports whose base item carries the "runeforged" tag and that aren't Lineage
    (Olroth's Conviction and Hubris are), when the tables are here: the 7 poe2db and the user list."""
    import glob
    import json
    from pathlib import Path

    from builder.search.gems import KALGUURAN_SUPPORTS

    found = sorted(glob.glob(str(Path(__file__).resolve().parents[1] / "data" / "game-*" / "tables" / "SupportGems.json")))
    if not found:
        pytest.skip("no exported game tables")
    folder = found[-1].rsplit("SupportGems.json", 1)[0]
    load = lambda name: json.load(open(folder + name, encoding="utf-8"))
    support, skill, base, tags = load("SupportGems.json"), load("SkillGems.json"), load("BaseItemTypes.json"), load("Tags.json")
    tag = next(i for i, t in enumerate(tags) if t["Id"] == "runeforged")
    tagged = {base[b]["Name"]: row["IsLineage"] for row in support
              for b in (skill[row["SkillGem"]]["BaseItemType"],) if tag in base[b]["Tags"]}
    assert {name for name, lineage in tagged.items() if lineage} == {"Olroth's Conviction", "Olroth's Hubris"}
    assert {name for name, lineage in tagged.items() if not lineage} == set(KALGUURAN_SUPPORTS)
    assert len(KALGUURAN_SUPPORTS) == 7


def test_kalguuran_supports_are_limited_by_character_level():
    """KeywordPopups KalguuranSupportGemLimit: 1 from level 1, 2 from 17, ... 6 from 80, over every group together."""
    from types import SimpleNamespace

    from builder.search.gems import Gems, kalguuran_limit

    assert [kalguuran_limit(level) for level in (1, 16, 17, 33, 45, 64, 65, 79, 80, 100)] == [1, 1, 2, 3, 4, 4, 5, 5, 6, 6]
    gems = Gems.__new__(Gems)
    gems.build = SimpleNamespace(equipped={})
    gems.ctx = SimpleNamespace(level=20, gems={})
    group = lambda index, *names: {"index": index, "gems": [{"name": n, "support": True} for n in names]}
    groups = [group(0, "Fist Of Kalguur"), group(1, "Healing Runes"), group(2)]
    blocked = gems.used_supports(groups, 2)  # two held at level 20 (limit 2): no third, in any group
    assert {"Runic Infusion", "Scouring Flame", "Fist Of Kalguur", "Healing Runes"} <= blocked
    assert "Runic Infusion" in gems.used_supports(groups, 0)  # nor a swap within a holder (the cautious side)
    gems.ctx.level = 33
    assert gems.used_supports(groups, 2) == set()  # a normal support or a third Kalguuran one is fine at 33


def test_a_glory_skill_as_the_main_skill_is_used_as_often_as_the_build_makes_its_glory(engine):
    """PoB calculates a Glory skill at will; Assumptions.glory_main rates its uses from the Glory another skill's hits
    build (models.glory_uses), or from its own hits when there's no other skill (at will or never), as its trigger rate."""
    payload = make_payload(BuildRequest(main_skill="Hammer of the Gods"))
    skeleton = enumerate_skeletons(Context(payload), ["Hammer of the Gods"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    assert search.assumptions.main_skill() == "Hammer of the Gods"
    mods, notes = search.assumptions.wanted()
    note = next(n for n in notes if n["label"] == "Hammer of the Gods uses per second (Glory)")
    print(note["value"], "|", note["basis"])
    assert "no other skill" in note["basis"]
    assert any(m.get("skill") == "Hammer of the Gods" and m.get("key") == "triggerRate" for m in mods)
    assert not any("cooldown allows" in n.get("basis", "") for n in notes)
    main = build.main_group()
    other = build.set_group(gems=[{"name": "Fireball", "level": 20, "quality": 0}], fullDPS=True)
    mods, notes = search.assumptions.wanted()
    note = next(n for n in notes if n["label"] == "Hammer of the Gods uses per second (Glory)")
    print(note["value"], "|", note["basis"])
    assert "Fireball" in note["basis"] and note["value"] >= 0.0
    assert build.main_group()["index"] == main["index"] and search.assumptions.main_skill() == "Hammer of the Gods"
    build.remove_group(other["index"])


def test_every_cursed_line_template_parses_in_pob(engine):
    """The bridge stops a search on an assumption line PoB can't parse: "Enemies you Curse have 1% to Chaos
    Resistance" without its sign stopped every search task of gate 2b75b (GEAR4, POE2T619)."""
    texts = [text for _, text in models.CURSED_LINES]
    results = engine.call("parseLines", lines=texts)["results"]
    assert [r["line"] for r in results if not r["supported"]] == []


def test_a_curse_is_scaled_to_its_share_and_cast_by_hand_costs_time(engine):
    """MECH33: PoB counts a curse always up and the enemy Cursed with any curse. A skill-scoped "more Effect of your
    Curses" line reaches a curse gem, "Enemies you Curse take ..." is scaled like the Marked lines, and a curse cast by
    hand takes its cast time from the main skill."""
    payload = make_payload(BuildRequest(main_skill="Lightning Arrow", ascendancies=["Deadeye"]))
    skeleton = enumerate_skeletons(Context(payload), ["Lightning Arrow"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    without = build.call("stats", fields=["FullDPS"])["FullDPS"]
    build.set_group(gems=[{"name": "Elemental Weakness", "level": 20, "quality": 0}], label="curse")
    build.call("setAssumptions", mods=[], fields=build.objective.fields)
    raw = build.call("stats", fields=["FullDPS"])["FullDPS"]
    assert raw > without  # PoB's always-up curse
    build.call("setAssumptions", mods=[models.line("1% more Effect of your Curses", -100, skill="Elemental Weakness")],
               fields=build.objective.fields)
    assert build.call("stats", fields=["FullDPS"])["FullDPS"] == pytest.approx(without, rel=1e-6)
    taken = "Enemies you Curse take 1% increased Damage"
    def dps(*scales):
        build.call("setAssumptions", mods=[models.line(taken, k) for k in scales], fields=build.objective.fields)
        return build.call("stats", fields=["FullDPS"])["FullDPS"]
    assert dps(50) > dps(1) > raw
    assert dps(50, -50) == pytest.approx(raw, rel=1e-6)
    search.assumptions.mods = None
    search.assumptions.apply()
    note = next(n for n in search.assumptions.active if n["label"].startswith("Elemental Weakness"))
    assert note["value"] == 1.0 and "takes" in note["basis"]
    assert build.call("stats", fields=["FullDPS"])["FullDPS"] < raw


def test_pounce_s_slam_and_its_carried_wolves_are_scaled_in_full_dps(engine):
    """MECH36 (the user, 11 Nov: "Yes, count it labelled", "Yes, they follow"): in a Pounce group in Full DPS, PoB counts
    Pounce's slam at its cooldown and one Wolf Companion (Summon Wolf), as SkillDPS entries. The Mark model counts those
    entries x its scales (Objective.entry_scales: the slam at one per Mark, the wolf x the carried count), linearly:
    PoB's own more-Damage lines weren't (POE2T450: +500% on the wolf read x12.7, -37.5% on the slam x0.595)."""
    payload = make_payload(BuildRequest(main_skill="Cross Slash"))
    skeletons = [s for s in enumerate_skeletons(Context(payload), ["Cross Slash"])[0] if s.weapon == "Talisman"]
    assert skeletons
    search = Search(engine, None, payload, skeletons[0].as_dict(), 0, full=True)
    search.setup(quests=False)
    build = search.build
    group = build.set_group(gems=[{"name": "Pounce", "level": 20, "quality": 0}], label="pounce")
    build.set_group(index=group["index"], fullDPS=True)
    search.assumptions.mods = None
    search.assumptions.apply()
    note = next(n for n in search.assumptions.active if n["label"].startswith("Predator's Mark"))
    scales = build.objective.entry_scales
    assert note.get("full_dps") == scales and scales["Summon Wolf"] == 6 and scales["Pounce"] == pytest.approx(1.0)  # (Cross Slash consumes it: Pounce on its cooldown)
    stats = build.call("stats", fields=build.objective.fields)
    entries = {e["name"]: e["dps"] * (e.get("count") or 1) for e in stats["SkillDPS"]}
    print("PROBE", entries, scales)
    assert entries["Pounce"] > 0 and entries["Summon Wolf"] > 0
    counted = build.objective.dps(stats)
    build.objective.entry_scales = {}
    plain = build.objective.dps(stats)
    assert counted - plain == pytest.approx(
        (entries["Summon Wolf"] * 5 + entries["Pounce"] * (scales["Pounce"] - 1)) * build.objective.life_factor
        * build.objective.uptime_factor, rel=1e-6)


def test_a_pressed_non_main_skill_counts_at_its_real_use_in_full_dps(engine):
    """MECH37: PoB's Full DPS counts a pressed non-main skill as used nonstop beside the nonstop main skill. It counts at
    its cooldown (Frost Bomb), and the main skill loses the time its presses take (Objective.press_scales)."""
    payload = make_payload(BuildRequest(main_skill="Cross Slash"))
    skeletons = enumerate_skeletons(Context(payload), ["Cross Slash"])[0]
    search = Search(engine, None, payload, skeletons[0].as_dict(), 0, full=True)
    search.setup(quests=False)
    build = search.build
    group = build.set_group(gems=[{"name": "Frost Bomb", "level": 20, "quality": 0}], label="bomb")
    build.set_group(index=group["index"], fullDPS=True)
    search.assumptions.mods = None
    search.assumptions.apply()
    scales = build.objective.press_scales
    print("PROBE", scales, [n["basis"] for n in search.assumptions.active if "pressed" in n["label"] or "time left" in n["label"]])
    assert 0 < scales["Cross Slash"] < 1 and 0 < scales["Frost Bomb"] <= 1
    assert any(n["label"].startswith("Frost Bomb: pressed beside") for n in search.assumptions.active)
    stats = build.call("stats", fields=build.objective.fields)
    entries = {models.press_key(e["name"], e.get("source")): e["dps"] * (e.get("count") or 1) for e in stats["SkillDPS"]}
    print("ENTRIES", entries)
    assert "Cross Slash" in entries and "Frost Bomb" in entries
    counted = build.objective.dps(stats)
    build.objective.press_scales = {}
    plain = build.objective.dps(stats)
    assert counted < plain
    # (every scaled entry, the skills' and their ailments' where they're the best source)
    assert plain - counted == pytest.approx(
        sum(dps * (1 - scales[key]) for key, dps in entries.items() if key in scales)
        * build.objective.life_factor * build.objective.uptime_factor, rel=1e-6)


def test_withering_presence_withers_the_boss_in_your_presence(engine):
    """RULE_AUDIT #1 (MECH42): Withered from Withering Presence (the gem's frequency and 6 s duration, game files), read
    through the bridge's levelStats; before, only "chance to inflict Withered" lines counted."""
    payload = make_payload(BuildRequest(main_skill="Boneshatter", ascendancies=["Titan"]))
    skeleton = enumerate_skeletons(Context(payload), ["Boneshatter"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    search.build.set_group(gems=[{"name": "Withering Presence", "level": 20, "quality": 0}], label="presence")
    found = search.conditions.evidence()
    print("PROBE", found.get("multiplierWitheredStackCount"))
    value, basis = found["multiplierWitheredStackCount"][:2]
    assert 1 <= value <= 10 and "Withering Presence" in basis


def test_shocked_ground_reads_the_builds_shock_magnitude_but_not_crit_only_lines(engine):
    """MECH45 (FIX_QUEUE, the user's expectation): the bridge's shockMagnitude sums the main skill's Shock-magnitude
    modifiers for your Shocked Ground's Shock; a crit-only line (hits only) isn't counted."""
    build = spark_with(engine, []).build
    before = build.call("shockMagnitude")
    amulet = "\n".join(["Rarity: Rare", "Test Amulet", "Gold Amulet", "Item Level: 82", "Implicits: 0",
                        "30% increased Magnitude of Shock you inflict",
                        "40% increased Magnitude of Shock you inflict with Critical Hits"])
    build.call("equip", slot="Amulet", raw=amulet, fields=[])
    after = build.call("shockMagnitude")
    assert after["inc"] - before["inc"] == 30 and after["max"] >= 20, (before, after)



def test_return_model_lines_parse_in_pob(engine):
    """MECHB1: the bridge stops a search on an assumption line PoB can't parse; the return model's two lines."""
    texts = ["1% more Damage", "1% more Damage against Enemies with Fully Broken Armour"]
    results = engine.call("parseLines", lines=texts)["results"]
    assert [r["line"] for r in results if not r["supported"]] == []


def test_the_bridge_reports_a_hazards_first_half_second_penalty(engine):
    """MECHB2: the bridge reads "-80% damage if destroyed within 0.5 s by something other than you" from the scored stat
    set (PoB maps it to nothing) and models.hazard_early_model counts 80% less for Spearfield placed on the boss."""
    payload = make_payload(BuildRequest(main_skill="Spearfield"))
    skeleton = enumerate_skeletons(Context(payload), ["Spearfield"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    skills = search.build.call("activeSkills")["skills"]
    early = next(s for s in skills if s["name"] == "Spearfield").get("earlyHazard")
    assert early and early["kind"] == "hazard" and early["more"] == -80
    note = next(n for n in search.assumptions.active if n["label"].startswith("Spearfield: 80% less"))
    assert 0.15 < note["value"] < 0.5  # x0.2, or more with a rearm chance


def test_the_bridge_reports_a_skills_own_final_energy_penalty(engine):
    """MECHB4 (COVC6): Incinerate's active_skill_energy_generated_+%_final -98 (game files; PoB maps it to nothing) comes
    through activeSkills as energyFinal for models.energy_gain."""
    payload = make_payload(BuildRequest(main_skill="Incinerate"))
    skeleton = enumerate_skeletons(Context(payload), ["Incinerate"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    skills = search.build.call("activeSkills")["skills"]
    assert next(s for s in skills if s["name"] == "Incinerate").get("energyFinal") == -98


def test_the_boss_is_scored_with_its_own_resistances_not_pob_s_50(engine):
    """MECH50 (FIX_QUEUE RESEARCH8/9): resetConfig writes the boss preset's resistances (default the Arbiter of Ash,
    Cold -15%) instead of PoB's Pinnacle placeholder 50/50/50; a Cold hit build gains about (1 + 0.15) / 0.5 = x2.3
    less any Cold Penetration (PoB floors it at 0%, as the game does)."""
    from builder.search import bosses

    payload = make_payload(BuildRequest(main_skill="Ice Nova"))
    skeleton = enumerate_skeletons(Context(payload), ["Ice Nova"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    pob = {"enemyFireResist": 50, "enemyColdResist": 50, "enemyLightningResist": 50, "enemyChaosResist": 0}
    build.call("resetConfig", boss=pob)
    at_50 = build.call("stats", fields=FIELDS)["CombinedDPS"]
    pob_time = build.call("stats", fields=["enemySkillTime"])["enemySkillTime"]
    config = build.call("resetConfig")["input"]
    assert {k: config[k] for k in bosses.config_vars()} == bosses.config_vars()
    at_ash = build.call("stats", fields=FIELDS)["CombinedDPS"]
    assert at_50 > 0 and 1.5 < at_ash / at_50 <= 2.31
    # MECHD4: the preset's defence cadence reaches PoB's enemySkillTime (the Arbiter: PoB's 0.7 s, faster than its upper
    # bound on hits; the Bodach 0.6 s; the same boss Slow on all)
    ash_time = build.call("stats", fields=["enemySkillTime"])["enemySkillTime"]
    assert pob_time > 0 and ash_time == pytest.approx(pob_time, rel=1e-3)
    build.call("resetConfig", boss=bosses.config_vars("The Bodach"))
    bodach_time = build.call("stats", fields=["enemySkillTime"])["enemySkillTime"]
    assert bodach_time / pob_time == pytest.approx(600 / 700, rel=1e-3)
    build.call("resetConfig")
    # MECHD4: the boss's Culling threshold reaches PoB's game constant (5% x Life / (Life + ES)); every reset puts the
    # preset's back (the Arbiter has no ES: PoB's 5)
    def cull():
        return float(engine.lua.run('return tostring(data.gameConstants["CullingStrikeUniqueThreshold"])', "cull")[0])
    assert cull() == 5
    build.call("resetConfig", boss=bosses.config_vars("Atziri, the Red Queen"))
    assert cull() == pytest.approx(bosses.cull_threshold("Atziri, the Red Queen")) and cull() < 4.7
    build.call("resetConfig", boss=pob)
    assert cull() == 5
    build.call("resetConfig")


def test_a_unique_granting_a_remnant_source_is_tried_for_real_in_the_gear_round(engine):
    """SEARCH3: every Shaman of the Explosive Transmutation request scored 0 in POE2T733. The explosion's trigger rate
    comes from the build's Remnant sources, and the gear what-if can see neither The Hollow Mask's granted Wildwood's
    Gifts nor the rate it brings: it scored the mask below the bare build (probe POE2T738). Gear.assumption_trials equips
    it for real, settles (the main group then calculates the Transmutation Explosion) and keeps it."""
    import json

    from builder import ROOT

    body = json.loads((ROOT / "benchmarks/user_requests/et_acolyte_shaman.json").read_text(encoding="utf-8"))
    payload = make_payload(BuildRequest.model_validate(body))
    payload["unique_prices"] = {"The Hollow Mask": [0.01, 50]}
    skeleton = next(s for s in enumerate_skeletons(Context(payload), ["Explosive Transmutation"])[0]
                    if s.ascendancy == "Shaman" and "Wand + Focus" in s.label)
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    before = build.score
    assert build.objective.dps(build.stats) == 0  # no Remnant source yet
    assert search.gear.assumption_trials(float("inf"))
    assert build.equipped["Helmet"].name == "The Hollow Mask" and build.score > before
    assert build.objective.dps(build.stats) > 0
    group = build.main_group()
    assert group["skills"][group["mainActiveSkill"] - 1]["name"] == "Transmutation Explosion"


def test_the_bridge_reports_a_skills_total_cast_time_addition(engine):
    """MECHB6 (AUDIT17 A17-1): Comet's "+1 second to Total Cast Time" (total_cast_time_+_ms 1000, game files; PoB's
    TotalCastTime) comes through activeSkills as totalTimeAdded, which the Energy cost counts double (models.energy_cost)."""
    payload = make_payload(BuildRequest(main_skill="Comet"))
    skeleton = enumerate_skeletons(Context(payload), ["Comet"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    skills = search.build.call("activeSkills")["skills"]
    comet = next(s for s in skills if s["name"] == "Comet")
    assert comet.get("totalTimeAdded") == pytest.approx(1.0)
    assert models.energy_cost(models.Skill.of(comet)) == pytest.approx(100 * comet["castTime"] + 200)


def test_the_bridge_reports_the_calculated_stat_set_and_a_groups_hit_fields(engine):
    """MECHB7 (COVE8's bridge requests): activeSkills gives each skill the label of the stat set PoB calculates
    (models.Skill.stat_set), and useRates gives a group's HitChance, CritChance, HeavyStunBuildupAvg and per-type
    averages (models.HEAVY_STUN_GROUP_FIELDS) for a side skill's own output."""
    payload = make_payload(BuildRequest(main_skill="Comet"))
    skeleton = enumerate_skeletons(Context(payload), ["Comet"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    skills = search.build.call("activeSkills")["skills"]
    comet = next(s for s in skills if s["name"] == "Comet")
    assert comet.get("statSet") == "Comet" and models.Skill.of(comet).stat_set == "Comet"
    index = search.build.main_group()["index"]
    rate = search.build.call("useRates", indices=[index])["rates"][0]
    assert rate["HitChance"] > 0 and "CritChance" in rate
    assert rate.get("ColdHitAverage", 0) > 0 or rate.get("FireHitAverage", 0) > 0


def test_apocalypses_disasters_count_through_full_dps(engine):
    """MECHB8 (engine probe POE2T1012-1014): Apocalypse's Hailstorm, Meteor and Crash Lightning sit in the granted skill's
    own group, which PoB leaves out of Full DPS: their trigger rates alone moved nothing until the group was added."""
    from builder.request.schema import Choices

    payload = make_payload(BuildRequest(main_skill="Volcano", ascendancies=["Shaman"],
                                        require=Choices(skills=["Apocalypse"])))
    skeleton = enumerate_skeletons(Context(payload), ["Volcano"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    build.call("allocate", add=[28745], fields=[])  # Bringer of the Apocalypse: "Grants Skill: Apocalypse"
    search.assumptions.apply()
    skills = build.call("activeSkills")["skills"]
    group = next(s["group"] for s in skills if s["name"] == "Meteor")
    assert {s["name"] for s in skills if s["group"] == group} >= {"Apocalypse", "Hailstorm", "Meteor", "Crash Lightning"}
    assert group in search.assumptions.extra_groups
    stats = build.call("stats", fields=["CombinedDPS", "FullDPS"])
    assert stats["FullDPS"] > 2 * stats["CombinedDPS"]


def test_doedres_witchtoads_count_in_full_dps_at_their_curse_areas_share(engine):
    """MECHB8 (engine probes POE2T1015-1016): PoB rates Dark Consequences at castTime x 0.25 (4 a second) in the curse's
    group, outside Full DPS; the toad model puts the group in it, and a trigger rate r gives 4r explosions a second."""
    payload = make_payload(BuildRequest(main_skill="Spark", ascendancies=["Stormweaver"]))
    skeleton = enumerate_skeletons(Context(payload), ["Spark"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    build.call("addSkillGroup", text="Despair 20/0 1\nDoedre's Undoing 1/0 1")
    group = build.groups()[-1]["index"]
    search.assumptions.apply()
    assert group in search.assumptions.extra_groups
    assert build.minion_died[0] == pytest.approx(4.0)  # a hand-cast curse kept up: a toad every 0.25 s
    stats = build.call("stats", fields=["CombinedDPS", "FullDPS"])
    assert stats["FullDPS"] > 2 * stats["CombinedDPS"]
    build.call("setAssumptions", mods=models.trigger_rate(models.DARK_CONSEQUENCES, 0.5), fields=[])
    names = [s["name"] for s in build.groups()[-1]["skills"]]
    half = build.call("setGroup", index=group, main=True, mainActiveSkill=names.index(models.DARK_CONSEQUENCES) + 1,
                      fields=["HitTime"])["stats"]
    assert half["HitTime"] == pytest.approx(0.5)  # 2 explosions a second at half the share


def test_a_second_dark_effigy_doubles_vile_effusions_damage(engine):
    """MECHB8 (engine probe POE2T1018): Vile Effusion carries no ActiveTotemLimit; the bridge reports Dark Effigy's, and
    the totem count model counts each totem placed."""
    payload = make_payload(BuildRequest(main_skill="Dark Effigy", ascendancies=["Lich"]))
    skeleton = enumerate_skeletons(Context(payload), ["Dark Effigy"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    effigy = next(s for s in build.call("activeSkills")["skills"] if s["name"] == "Dark Effigy")
    assert effigy["totemLimit"] == 1
    search.assumptions.apply()
    one = build.call("stats", fields=["TotalDPS"])["TotalDPS"]
    found = [models.Skill.of(s) for s in build.call("activeSkills")["skills"] if not s["disabled"]]
    main = next(s for s in found if s.name == "Vile Effusion")
    mods = [models.line("+1 to maximum number of Summoned Totems", 1.0)] + models.totem_count_model(
        [replace(s, totem_limit=2) if s.name == "Dark Effigy" else s for s in found], main)[0]
    build.call("setAssumptions", mods=search.assumptions.mods + mods, fields=[])
    assert next(s for s in build.call("activeSkills")["skills"] if s["name"] == "Dark Effigy")["totemLimit"] == 2
    assert build.call("stats", fields=["TotalDPS"])["TotalDPS"] == pytest.approx(2 * one, rel=0.02)


def test_a_boss_takes_half_the_slow(engine):
    """Unique bosses take 50% less Slow (KeywordPopups "Slow"; MonsterUnique11). PoB adds SlowEffectOnSelf MORE -50 to
    its boss presets but never reads it, and adds a configured Chill twice: a 30% Chill made the boss hit 60% less often
    (survival overstated); the bridge's actionSpeedMod now counts it once, at 15%. Slows multiply (MECHD2)."""
    from builder.search import bosses

    engine.call("newBuild")
    plain = engine.call("setConfig", vars={"conditionEnemyChilled": False}, fields=["enemySkillTime"])["stats"]
    chilled = engine.call("setConfig", vars={"conditionEnemyChilled": True, "conditionEnemyChilledEffect": 30},
                          fields=["enemySkillTime"])["stats"]
    slow = 0.30 * bosses.PRESETS[bosses.DEFAULT].slow_taken
    assert chilled["enemySkillTime"] == pytest.approx(plain["enemySkillTime"] / (1 - slow), rel=1e-3)
    engine.call("setConfig", vars={"conditionEnemyChilled": False, "conditionEnemyChilledEffect": False})

def test_cat_o_nine_tails_takes_life_above_low_life_out_of_max_hit(engine):
    """MECHC8: PoB parses "Life Recovery other than Flasks cannot Recover Life to above Low Life" to nothing; the bridge
    flags it (LowLifeRecoveryCap) and objective.prepare counts only Low Life's 35% of Life in max hit taken."""
    from builder.search.objective import Objective

    search = spark_with(engine, [])
    build = search.build
    fields = ["LowLifeRecoveryCap", "Life", "Darkness", "PhysicalMaximumHitTaken", "PhysicalTotalHitPool"]
    assert not build.call("stats", fields=fields).get("LowLifeRecoveryCap")
    belt = "\n".join(["Rarity: Unique", "Cat O' Nine Tails", "Utility Belt", "Implicits: 1", "Has 2 Charm Slot",
                      "+200 to maximum Life", "Life Recovery other than Flasks cannot Recover Life to above Low Life"])
    build.call("equip", slot="Belt", raw=belt, fields=[])
    stats = build.call("stats", fields=fields)
    assert stats["LowLifeRecoveryCap"] == 1
    raw, pool, life = stats["PhysicalMaximumHitTaken"], stats["PhysicalTotalHitPool"], stats["Life"]
    adjusted = Objective.prepare(dict(stats))
    assert adjusted["PoBPhysicalMaximumHitTaken"] == raw
    assert adjusted["PhysicalMaximumHitTaken"] == pytest.approx(raw * (pool + stats.get("Darkness", 0) - 0.65 * life) / pool)


def test_heartbound_damage_reaches_pobs_degen_recovery_and_the_life_flask(engine):
    """MECHC9: Heartbound Loop's damage on Minion Death goes to PoB as "N Physical Damage taken per second"; PoB's
    TotalBuildDegen carries it to objective.boss_recovery, and the bridge's chargeGain sends its Life share (lifeDegen)
    to flasks.full_pool_cap."""
    from builder.search import models

    search = spark_with(engine, [])
    build = search.build
    before = build.call("stats", fields=["TotalBuildDegen"]).get("TotalBuildDegen") or 0
    build.call("setAssumptions", mods=search.assumptions.mods + [models.line("1 Physical Damage taken per second", 300.0)],
               fields=[])
    after = build.call("stats", fields=["TotalBuildDegen"])["TotalBuildDegen"]
    assert after - before == pytest.approx(300.0, rel=0.05)
    assert build.call("chargeGain")["lifeDegen"] > 0
    build.call("setAssumptions", mods=search.assumptions.mods, fields=[])


def test_jiquanis_thesis_line_reaches_pobs_own_flag_and_its_socketed_halves_dont(engine):
    """MECHC9, A12-2: "Increases and Reductions to Movement Speed also apply to Energy Shield Recharge Rate" (Jiquani's
    Thesis, boots Soul Core). The pinned PoB parses the whole line (ModParser: MovementSpeedAppliesToEnergyShieldRecharge;
    CalcDefence copies each Movement Speed INC to Energy Shield Recharge), so the full line counts a +30% Movement Speed
    as +30% Recharge Rate. Socketed, PoB's UpdateRunes parses the Soul Core's two halves apart, and neither parses: the
    model (models.jiquani_model) hands PoB the whole line."""
    search = spark_with(engine, [])
    build = search.build
    base = ["Rarity: Rare", "Test Stride", "Straw Sandals", "Implicits: 0", "+60 to maximum Energy Shield",
            "30% increased Movement Speed"]

    def recharge(lines):
        build.call("equip", slot="Boots", raw=chr(10).join(lines), fields=[])
        return build.call("stats", fields=["EnergyShieldRecharge"])["EnergyShieldRecharge"]

    plain = recharge(base)
    flat = recharge(base + ["30% increased Energy Shield Recharge Rate"])
    whole = recharge(base + ["Increases and Reductions to Movement Speed also apply to Energy Shield Recharge Rate"])
    assert plain > 0 and whole == pytest.approx(flat, abs=0.11)
    socketed = recharge(["Rarity: Rare", "Test Stride", "Straw Sandals", "Sockets: S", "Rune: Jiquani's Thesis",
                         "Implicits: 0", "+60 to maximum Energy Shield", "30% increased Movement Speed"])
    assert socketed == pytest.approx(plain)  # (PoB alone: the two halves parse to nothing)
    from builder.search import models

    texts = [e["text"] for e in build.call("sourceLines")["lines"]]
    mods, notes = models.jiquani_model(texts)
    assert mods and "Recharge" in notes[0]["label"]
    build.call("setAssumptions", mods=search.assumptions.mods + mods, fields=[])
    assert build.call("stats", fields=["EnergyShieldRecharge"])["EnergyShieldRecharge"] == pytest.approx(flat, abs=0.11)
    build.call("setAssumptions", mods=search.assumptions.mods, fields=[])



def test_critical_hit_chance_that_cannot_be_rerolled_drops_lucky_crit(engine):
    """MECH53 (an OVERCOUNT): "Your Critical Hit Chance cannot be Rerolled" (Maligaro's Virtuosity) stops anything making
    Critical Hit Chance Lucky, Unlucky, Bifurcated or Inevitable (KeywordPopups RerollCrit). PoB leaves the line unparsed and
    keeps its Lucky crit; the bridge answers false for the three flags on the player's mod stores while it's equipped."""
    payload = make_payload(BuildRequest(main_skill="Lightning Arrow", ascendancies=["Deadeye"]))
    skeleton = enumerate_skeletons(Context(payload), ["Lightning Arrow"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    fields = ["CritChance"]

    def wear(slot: str, base: str, lines: list[str]) -> float:
        raw = "\n".join(["Rarity: Rare", "Test " + slot, base, "Implicits: 0", *lines])
        return build.call("equip", slot=slot, raw=raw, fields=fields)["stats"]["CritChance"]

    plain = wear("Gloves", "Fine Bracers", [])
    lucky = wear("Amulet", "Stellar Amulet", ["Your Critical Hit Chance is Lucky"])
    assert 0 < plain < lucky  # PoB's Lucky crit
    p = plain / 100
    assert lucky == pytest.approx(100 * (1 - (1 - p) ** 2), rel=1e-3)
    held = wear("Gloves", "Fine Bracers", ["Your Critical Hit Chance cannot be Rerolled"])
    assert held == pytest.approx(plain)
    # off again with the line gone
    assert wear("Gloves", "Fine Bracers", []) == pytest.approx(lucky)


def test_either_flask_keeps_pobs_using_flask_lines_on(engine):
    """MECHC10 (task 1): PoB sets UsingFlask while ANY flask slot is active, so a "during any Flask Effect" line survives
    one of two part-time flasks switched off; weigh_uptimes, switching one slot off at a time, never sees it drop.
    flasks.shared_flag_factor takes the both-off case back in (here each flask adds nothing of its own)."""
    from builder.search import flasks

    build = spark_with(engine, []).build
    amulet = chr(10).join(["Rarity: Rare", "Test Amulet", "Gold Amulet", "Item Level: 82", "Implicits: 0",
                           "40% increased Damage during any Flask Effect"])
    life = chr(10).join(["Rarity: Normal", "Ultimate Life Flask", "Item Level: 82", "Implicits: 0"])
    mana = chr(10).join(["Rarity: Normal", "Ultimate Mana Flask", "Item Level: 82", "Implicits: 0"])
    build.call("equip", slot="Amulet", raw=amulet, fields=[])
    dps = lambda: build.call("stats", fields=["CombinedDPS"])["CombinedDPS"]

    def flasks_on(one: bool, two: bool) -> float:
        build.call("equip", slot="Flask 1", raw=life, active=one, fields=[])
        build.call("equip", slot="Flask 2", raw=mana, active=two, fields=[])
        return dps()

    both = flasks_on(True, True)
    assert both > 0
    one_off, two_off, none = flasks_on(False, True) / both, flasks_on(True, False) / both, flasks_on(False, False) / both
    assert one_off == pytest.approx(1.0) and two_off == pytest.approx(1.0)  # the other flask keeps the line
    assert none < 0.95  # the line goes only with both off
    factor, note = flasks.shared_flag_factor([0.5, 0.5], [one_off, two_off], none)
    assert factor == pytest.approx(0.75 + 0.25 * none) and factor < 1.0 and "75%" in note


def test_unbound_buff_lines_parse_and_count_only_while_unbound(engine):
    """MECHC10: Unbound Avatar's three buff stats (PoB maps none) reach PoB as lines under Condition:Unbound (the
    bridge's `pob` condition): 40% more Elemental Damage and 80% more Shock chance while the "Are you Unbound?" box is
    on, nothing while it's off."""
    from builder.search import models

    search = spark_with(engine, [])
    build = search.build
    mods, notes = models.unbound_model(["Grants Skill: Unbound Avatar"])
    assert len(mods) == 4 and "while Unbound" in notes[0]["value"]
    fields = ["CombinedDPS", "ShockChancePerHit"]
    plain = build.call("stats", fields=fields)
    build.call("setAssumptions", mods=search.assumptions.mods + mods, fields=[])
    off = build.call("stats", fields=fields)
    assert off["CombinedDPS"] == pytest.approx(plain["CombinedDPS"])
    build.set_config({"Unbound": True})
    on = build.call("stats", fields=fields)
    assert on["CombinedDPS"] > off["CombinedDPS"] * 1.3
    if 0 < off.get("ShockChancePerHit", 0) < 50:
        assert on["ShockChancePerHit"] == pytest.approx(off["ShockChancePerHit"] * 1.8, rel=0.02)
    build.set_config({"Unbound": False})
    build.call("setAssumptions", mods=search.assumptions.mods, fields=[])



def test_a_one_totem_support_holds_pobs_ailment_stacks_to_one_totem(engine):
    """AUDIT24 A24-3 (MECHB11): PoB multiplies a totem skill's ailment stacks by its ActiveTotemLimit (CalcOffence 5264).
    With Cirel's Cultivation ("Supported Skills can only have one active Totem") and +1 totem, the model's
    ActiveTotemLimit line holds the calculated skill's count to 1, and the bridge's read leaves the line out: the model
    reads 2 on the next pass too (no loop)."""
    payload = make_payload(BuildRequest(main_skill="Dark Effigy", ascendancies=["Lich"]))
    skeleton = enumerate_skeletons(Context(payload), ["Dark Effigy"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    build.call("addSkillGroup", text="Dark Effigy 20/0 1\nCirel's Cultivation 1/0 1")
    group = build.groups()[-1]["index"]
    plus = [models.line("+1 to maximum number of Summoned Totems", 1.0)]
    build.call("setAssumptions", mods=plus, fields=[])
    names = [s["name"] for s in build.groups()[-1]["skills"]]
    flagged = None
    for k, name in enumerate(names):
        build.call("setGroup", index=group, main=True, mainActiveSkill=k + 1, fields=["ActiveTotemLimit"])
        found = [models.Skill.of(s) for s in build.call("activeSkills")["skills"] if not s["disabled"]]
        skill = next(s for s in found if s.group == group and s.name == name)
        print(name, skill.ailment_totems, skill.support_stats.get(models.TOTEM_ONE_STAT))
        if skill.ailment_totems and models.number(skill.ailment_totems.get("limit")) > 1:
            flagged = (k, skill, found)
    assert flagged, "no totem-flagged skill with a limit above 1"
    k, skill, found = flagged
    build.call("setGroup", index=group, main=True, mainActiveSkill=k + 1, fields=[])
    before = build.call("stats", fields=["ActiveTotemLimit"])["ActiveTotemLimit"]
    assert before == 2
    mods, notes = models.totem_count_model(found, skill)
    assert mods and mods[0]["value"] == -1 and notes[0]["value"] == 1
    build.call("setAssumptions", mods=plus + mods, fields=[])
    assert build.call("stats", fields=["ActiveTotemLimit"])["ActiveTotemLimit"] == 1
    again = next(s for s in build.call("activeSkills")["skills"] if s.get("group") == group and s["name"] == skill.name)
    assert again["ailmentTotems"]["limit"] == 2  # (its own line left out: the same line next pass)
    assert next(s for s in build.call("activeSkills")["skills"] if s["name"] == "Dark Effigy"
                and s.get("group") == group)["totemLimit"] == 2


def test_the_kings_adds_supply_detonate_dead_its_corpses(engine, monkeypatch):
    """AUDIT24 A24-1 and the add-kill probe (MECHB11): against the King in the Mists (MECHD1's Kills: one add per 25 s
    for 43% of the fight), the adds' kill rate reaches the models (build.kills) and supplies corpses (LOW, UNVERIFIED
    whether totems leave one); against the default lone boss, none."""
    from builder.search import bosses

    payload = make_payload(BuildRequest(main_skill="Detonate Dead"))
    skeleton = enumerate_skeletons(Context(payload), ["Detonate Dead"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    search.assumptions.apply()
    lone = next(n for n in search.assumptions.active if n["label"].startswith("Corpses per second"))
    assert lone["value"] == 0 and build.kills[0] == 0
    monkeypatch.setattr(bosses, "DEFAULT", "The King in the Mists")
    search.assumptions.apply()
    rate = bosses.kills_per_second("The King in the Mists")[0]
    assert build.kills[0] == pytest.approx(rate) and rate == pytest.approx(0.43 / 25)
    king = next(n for n in search.assumptions.active if n["label"].startswith("Corpses per second"))
    assert king["value"] == pytest.approx(rate, rel=1e-3) and "UNVERIFIED" in king["basis"]
    print("King corpses", king, build.call("stats", fields=["TotalDPS", "FullDPS"]))
def test_icestorm_cannot_chill(engine):
    """MECH54 (an OVERCOUNT): Icestorm's cold_damage_cannot_chill (its GrantedEffectStatSets implicit stat) has no PoB
    SkillStatMap entry, so PoB Chilled with it. The bridge maps it to ColdCannotChill (CalcOffence canDoAilment)."""
    payload = make_payload(BuildRequest(main_skill="Icestorm", ascendancies=["Stormweaver"]))
    skeleton = enumerate_skeletons(Context(payload), ["Icestorm"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    raw = chr(10).join(["Rarity: Rare", "Test Amulet", "Stellar Amulet", "Implicits: 0", "5000% increased Cold Damage"])
    stats = search.build.call("equip", slot="Amulet", raw=raw,
                              fields=["ChillChanceOnHit", "ColdHitAverage", "EnemyAilmentThreshold", "ChillHitDamage"])["stats"]
    assert stats["ColdHitAverage"] > stats["EnemyAilmentThreshold"] / 100  # PoB would Chill this hit
    assert stats["ChillChanceOnHit"] == 0
    assert stats["ChillHitDamage"] == 0  # (the bridge's Chill-able total skips a type that can't Chill)


def test_the_chill_able_hit_counts_every_type_allowed_to_chill(engine):
    """MECH54, AUDIT25 A25: the bridge's ChillHitDamage is PoB's sum for Chill: the cold hit, plus another type once a
    line lets it Chill ("Your Physical Damage can Chill" sets PhysicalCanChill, ModParser 3946)."""
    payload = make_payload(BuildRequest(main_skill="Ice Nova", ascendancies=["Stormweaver"]))
    skeleton = enumerate_skeletons(Context(payload), ["Ice Nova"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    fields = ["ChillHitDamage", "ColdHitAverage", "PhysicalHitAverage"]

    def wear(lines):
        raw = chr(10).join(["Rarity: Rare", "Test Amulet", "Stellar Amulet", "Implicits: 0", *lines])
        return search.build.call("equip", slot="Amulet", raw=raw, fields=fields)["stats"]

    added = "Gain 50% of Damage as Extra Physical Damage"
    cold = wear([added])
    assert cold["PhysicalHitAverage"] > 0 and cold["ChillHitDamage"] == pytest.approx(cold["ColdHitAverage"])
    both = wear([added, "Your Physical Damage can Chill"])
    assert both["ChillHitDamage"] == pytest.approx(both["ColdHitAverage"] + both["PhysicalHitAverage"])




def test_explosive_transmutations_trigger_assumption_keeps_the_explosions_runic_ward_cost(engine):
    """MECHB12 (SEARCH7's defect A): the game charges the triggered explosion Runic Ward (GrantedEffectsPerLevel
    CostAmounts 7 / 16 / 30, poe2db "Cost: (7-30) Ward"), and PoB has no rule that makes a triggered skill free. MECHC8's
    delivered 2b75b build (Runic Ward 0) must warn the same with the trigger assumption on as on finalize's plain
    reload, so the live search sees what the delivered build can't cast."""
    import json
    from pathlib import Path
    from builder.pob import codes

    row = json.loads((Path(__file__).parents[1] / "benchmarks/results/gate4-20261112-152004.json")
                     .read_text(encoding="utf-8"))["rows"][0]
    request = BuildRequest.model_validate(row["request"])
    payload = make_payload(request)
    skeleton = enumerate_skeletons(Context(payload), [request.main_skill])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    build.call("loadXml", xml=codes.decode(row["code"]))
    plain = build.call("facts", fields=["WardCostWarningList"])
    search.assumptions.apply()
    assumed = build.call("facts", fields=["WardCostWarningList"])
    assert any(m["skill"] == "Transmutation Explosion" and m["key"] == "triggered" for m in search.assumptions.mods)
    warning = "You do not have enough Runic Ward to use: Transmutation Explosion"
    assert warning in plain["warnings"] and warning in assumed["warnings"]
    assert plain["stats"].get("WardCostWarningList") == assumed["stats"].get("WardCostWarningList") == 1
