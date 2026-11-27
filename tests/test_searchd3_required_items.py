"""SEARCHD3: required items come before the skeleton's weapons (THE USER, 27 Nov 2026: "the very first rule is 'use what I
tell you I want in the build'"). Their run (data/results/20261127-134254-run2.json) delivered Shankgonne and Crown of Eyes
without the required Liminal Coil (a Wand): "fits no slot of this skeleton" beside a Sceptre + Focus. No PoB here: the
skeleton chooser, the seed's re-plan, the game-rule reasons and the item-class check read the game data only."""

from __future__ import annotations

from builder.request.constraints import compile_request
from builder.request.schema import BuildRequest
from builder.search import required_items
from builder.search.context import Context, make_payload
from builder.search.skeletons import enumerate_skeletons, seed_skeleton

THEIRS = dict(level=95, class_name="Ranger", ascendancies=["Pathfinder"], main_skill="Detonate Living",
              require={"uniques": ["Shankgonne", "Crown of Eyes", "Liminal Coil"]}, weapon_swap="may")


def ctx_for(**fields) -> Context:
    return Context(make_payload(BuildRequest(**{**THEIRS, **fields})))


def skeletons(**fields):
    ctx = ctx_for(**fields)
    return ctx, *enumerate_skeletons(ctx, [ctx.request.main_skill])


def test_the_users_three_uniques_get_a_wand_main_hand():
    ctx, found, problems = skeletons()
    labels = [s.label for s in found]
    print(labels, problems)
    assert found and all(s.weapon == "Wand" and not s.swap for s in found)  # (Sceptre + Focus, Staff: gone)
    assert not problems
    assert all(not required_items.unheld(ctx, s) for s in found)


def test_a_main_skill_that_needs_another_weapon_puts_the_wand_in_weapon_set_2():
    ctx, found, problems = skeletons(main_skill="Lightning Arrow")
    print([s.label for s in found], problems)
    assert found and all(s.weapon == "Bow" and s.swap and s.swap_weapon == "Wand" for s in found)
    assert not problems


def test_no_swap_allowed_says_which_game_rule_stops_it():
    ctx, found, problems = skeletons(main_skill="Lightning Arrow", weapon_swap="never")
    print(problems)
    assert [s.weapon for s in found] == ["Bow"]  # (the closest build is still delivered)
    text = " ".join(problems)
    assert "Liminal Coil (Wand)" in text and "a Wand goes only in the main hand" in text
    assert "forbids weapon swap" in text and "closest build is delivered" in text
    assert "fits no slot" not in text


def test_a_two_hander_and_a_shield_take_giants_blood_or_report_the_rule():
    warrior = dict(main_skill="Boneshatter", class_name="Warrior", ascendancies=["Titan"], weapon_swap="never",
                   require={"uniques": ["Chober Chaber", "Wulfsbane"]})
    ctx, found, problems = skeletons(**warrior)
    print([s.label for s in found], problems)
    assert [(s.weapon, s.offhand, s.requires_passive) for s in found] == [("Two Hand Mace", "Shield", "Giant's Blood")]
    ctx, found, problems = skeletons(**warrior, forbid={"passives": ["Giant's Blood"]})
    text = " ".join(problems)
    print(text)
    assert "a Two Hand Mace is two-handed" in text and "Giant's Blood, which would allow it, is banned" in text
    assert "weapon set 2 would hold the rest, but the request forbids weapon swap" in text
    assert found  # (the closest: one of the two held)


def test_a_required_item_class_decides_the_off_hand_and_is_checked():
    ctx, found, problems = skeletons(class_name="Sorceress", ascendancies=["Stormweaver"], main_skill="Spark",
                                     require={"item_classes": ["Shield"]})
    assert found and all(s.offhand == "Shield" for s in found)
    compiled = compile_request(BuildRequest(**{**THEIRS, "require": {"item_classes": ["Shield"]}}), ctx.data)
    facts = {"className": "Ranger", "ascendClassName": "Pathfinder", "skillGroups": [], "passives": [], "stats": {},
             "items": [{"slot": "Weapon 1", "type": "Wand", "name": "x", "rarity": "RARE", "runes": []}]}
    rows = []
    for check in compiled.checks:  # (the other checks read more of a real build's facts than this one needs)
        try:
            rows.append(check(facts))
        except (KeyError, TypeError):
            continue
    row = next(c for c in rows if c.label == "Required item classes")
    assert not row.ok
    facts["items"].append({"slot": "Weapon 2", "type": "Shield", "name": "y", "rarity": "RARE", "runes": []})
    rows = []
    for check in compiled.checks:
        try:
            rows.append(check(facts))
        except (KeyError, TypeError):
            continue
    assert next(c for c in rows if c.label == "Required item classes").ok


def test_a_third_required_ring_needs_the_ring_slot_passive():
    rings = {"uniques": ["Andvarius", "Blackflame", "Blackheart"]}
    ctx, found, problems = skeletons(require=rings)
    text = " ".join(problems)
    print(text)
    assert "3 required rings need 3 ring slots" in text and "Unfurled Finger (Ritualist)" in text
    ctx, found, problems = skeletons(require=rings, class_name="Huntress", ascendancies=["Ritualist"],
                                     main_skill="Spark")
    assert found and not problems


def seed_facts(weapon: str, base: str, offhand: str = "Focus", offbase: str = "Runeforged Elegant Focus") -> dict:
    return {"ascendClassName": "Pathfinder", "className": "Ranger", "passives": [],
            "skillGroups": [{"enabled": True, "main": True, "fromItem": False, "set1": True,
                             "gems": [{"name": "Detonate Living", "support": False}]}],
            "items": [{"slot": "Weapon 1", "type": weapon, "base": base, "rarity": "RARE", "name": "Rare"},
                      {"slot": "Weapon 2", "type": offhand, "base": offbase, "rarity": "RARE", "name": "Rare"}]}


def test_a_seed_whose_weapons_cant_hold_the_required_wand_is_replanned():
    ctx = ctx_for()
    skeleton, notes = seed_skeleton(ctx, seed_facts("Sceptre", "Stoic Sceptre"))
    print(skeleton.label, notes)
    assert (skeleton.weapon, skeleton.offhand, skeleton.seeded) == ("Wand", "Focus", True)  # (the code's Focus kept)
    assert any("can't hold the required Liminal Coil" in n and "Wand + Focus instead" in n for n in notes)
    held, notes = seed_skeleton(ctx_for(require={"uniques": ["Shankgonne"]}), seed_facts("Sceptre", "Stoic Sceptre"))
    assert (held.weapon, held.offhand) == ("Sceptre", "Focus") and not notes  # (nothing to hold: the code's own)


def test_the_no_slot_reason_names_the_rule_not_fits_no_slot():
    class Gear:  # (Gear.slot_for found no slot: what required_items.no_slot reads)
        from builder.search.gear import SlotSpec

        def __init__(self, ctx):
            self.ctx = ctx
            self.slots = {"Weapon 1": self.SlotSpec("Sceptre"), "Weapon 2": self.SlotSpec("Focus"),
                          "Ring 1": self.SlotSpec("Ring"), "Ring 2": self.SlotSpec("Ring")}
            self.build = type("B", (), {"equipped": {}, "locked_slots": set()})()

    ctx = ctx_for()
    gear = Gear(ctx)
    text = required_items.no_slot(gear, "Liminal Coil", ctx.unique_option("Liminal Coil"))
    print(text)
    assert "Liminal Coil (Wand) can't go in this skeleton's hands" in text and "a Wand goes only in the main hand" in text
    jewel = required_items.no_slot(gear, "From Nothing", ctx.unique_option("From Nothing"))
    assert "is a Jewel" in jewel and "passive-tree socket" in jewel


def test_a_seed_on_an_ascendancy_without_a_third_ring_slot_keeps_its_weapons_and_says_why():
    ctx = ctx_for(require={"uniques": ["Andvarius", "Blackflame", "Blackheart"]})
    skeleton, notes = seed_skeleton(ctx, seed_facts("Sceptre", "Stoic Sceptre"))
    print(skeleton.label, notes)
    assert (skeleton.weapon, skeleton.offhand) == ("Sceptre", "Focus")
    assert any("3 required rings need 3 ring slots" in n for n in notes)
