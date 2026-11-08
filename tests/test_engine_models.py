"""Custom models through a real engine (PoB alone vs PoB with the model). Boots one PoB engine for
the module (a few seconds) and needs the game-data dump."""

from __future__ import annotations

import faulthandler

import pytest

from builder.data import GameData
from builder.request.schema import BuildRequest
from builder.search.context import Context, make_payload
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
