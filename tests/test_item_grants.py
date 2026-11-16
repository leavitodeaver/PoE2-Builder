"""What items grant, through a real engine (COV30). Boots one real PoB engine for the module and needs the game-data dump.

- A Voltaic Staff grants the castable Lightning Bolt, Choir of the Storm the triggered one (ledger entry
  granted-voltaic-staff-lightning-bolt, engine/bridge.lua installLedgerSkill). PoB maps both lines' text to Choir's
  UniqueBreachLightningBoltPlayer (ModParser gemIdLookup); the ledger tags the staff's line and the bridge grants
  LightningBoltPlayer from it.
- A keystone an item grants (Flesh Crucible's variants) reaches the models as the keystone's passive lines
  (bridge.sourceLines; AUDIT35 A35-1)."""
from __future__ import annotations

import faulthandler

import pytest

from builder.data import GameData
from builder.ledger.items import corrected_item
from builder.request.schema import BuildRequest
from builder.search import granted_rolls, models
from builder.search.context import Context, make_payload
from builder.search.inner import Search
from builder.search.skeletons import enumerate_skeletons

PLAIN, TRIGGERED = "LightningBoltPlayer", "UniqueBreachLightningBoltPlayer"


@pytest.fixture(scope="module")
def setup():
    try:
        GameData()
    except FileNotFoundError:
        pytest.skip("no game-data dump; run `uv run python -m builder.data dump`")
    from builder.pob.engine import PobEngine

    enabled = faulthandler.is_enabled()
    faulthandler.disable()  # LuaJIT's Lua errors are Windows exceptions PoB's pcall catches
    engine = PobEngine()
    payload = make_payload(BuildRequest(main_skill="Spark"))
    ctx = Context(payload)
    skeleton = enumerate_skeletons(ctx, ["Spark"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    yield ctx, search.build
    if enabled:
        faulthandler.enable()


def granted_ids(build, slot: str, raw: str) -> list[str]:
    """The skill ids of the item-granted groups' gems with this item in `slot` (bridge describeGroup's skillId)"""
    build.call("equip", slot=slot, raw=raw, fields=[])
    ids = [gem.get("skillId") for group in build.call("groups")["groups"] if group.get("fromItem")
           for gem in group.get("gems") or [] if gem.get("name") == "Lightning Bolt"]
    build.call("equip", slot=slot, fields=[])
    return ids


def unique_raw(ctx, name: str) -> str:
    raw = ctx.corrected_raw(name, next(u["raw"] for u in ctx.data.uniques if u["name"] == name))
    return granted_rolls.with_level(raw, 18)


def test_a_voltaic_staff_grants_the_castable_bolt_and_choir_the_triggered_one(setup):
    ctx, build = setup
    staff = "\n".join(["Rarity: Rare", "Test Staff", "Voltaic Staff", "Item Level: 82", "Implicits: 1",
                       "Grants Skill: Level 20 Lightning Bolt", "100% increased Spell Damage"])
    assert granted_ids(build, "Weapon 1", staff) == [TRIGGERED]  # PoB's own mapping (the line uncorrected)
    assert granted_ids(build, "Weapon 1", corrected_item(staff)) == [PLAIN]
    assert granted_ids(build, "Weapon 1", unique_raw(ctx, "Earthbound")) == [PLAIN]
    assert granted_ids(build, "Amulet", unique_raw(ctx, "Choir of the Storm")) == [TRIGGERED]


def test_a_keystone_an_item_grants_reaches_the_models_as_its_passive_lines(setup):
    """Ritual Cadence on an item (Flesh Crucible's variant line is the bare name; ModCache: Keystone LIST + the
    HaveRitualCadence flag). Before COV30 its node's text reached no model: read_inputs saw only the item's line."""
    ctx, build = setup
    crucible = next(u["raw"] for u in ctx.data.uniques if u["name"] == "Flesh Crucible")
    assert any(line.endswith("}Ritual Cadence") for line in crucible.split("\n"))
    node = next(n for n in ctx.data.tree["nodes"] if n.get("name") == "Ritual Cadence")
    amulet = "\n".join(["Rarity: Rare", "Test Amulet", "Gold Amulet", "Item Level: 82", "Implicits: 0", "Ritual Cadence"])

    def inputs():
        lines = build.call("sourceLines")["lines"]
        return [l for l in lines if l["source"] == "passive:Ritual Cadence"], models.read_inputs(lines)

    lines, before = inputs()
    assert not lines and before.total("InvocationCadence") == 0 and not before.has("InvocationNoGainTriggering")
    build.call("equip", slot="Amulet", raw=amulet, fields=[])
    lines, after = inputs()
    build.call("equip", slot="Amulet", fields=[])
    assert [l["text"] for l in lines] == list(node["stats"])
    assert after.total("InvocationCadence") > 0 and after.total("InvocationLess") == 50
    assert after.has("InvocationNoGainTriggering")
    assert not inputs()[0]  # gone with the item


def test_a_keystone_an_item_grants_carries_the_ledgers_translations(setup):
    """PoB's side (a probe, not a reading): mergeKeystones adds the tree node's modList, which carries the ledger's
    corrections (applyPassiveCorrections). Scarred Faith's "You cannot Recover Energy Shield from Regeneration" (PoB
    ignores it; ledger scarred-faith-no-es-regeneration: "You cannot Regenerate Energy Shield") stops ES regeneration
    from an item too."""
    _, build = setup
    lines = ["Rarity: Rare", "Test Amulet", "Gold Amulet", "Item Level: 82", "Implicits: 0", "+200 to maximum Energy Shield",
             "Regenerate 2% of maximum Energy Shield per second"]
    fields = ["EnergyShieldRegenRecovery", "EnergyShield"]
    build.call("equip", slot="Amulet", raw="\n".join(lines), fields=[])
    before = build.call("stats", fields=fields)
    build.call("equip", slot="Amulet", raw="\n".join(lines + ["Scarred Faith"]), fields=[])
    after = build.call("stats", fields=fields)
    build.call("equip", slot="Amulet", fields=[])
    assert before["EnergyShield"] > 0 and before["EnergyShieldRegenRecovery"] > 0
    assert after["EnergyShieldRegenRecovery"] == 0


def test_an_item_granted_skill_scores_at_the_level_the_character_meets(setup):
    """COV31 (COV30's level lead): PoB scores an item-granted skill at the highest level up to the line's whose level
    and attribute requirements the character meets (CalcSetup 2170, getGrantedSkillLevel with env.modDB), the game's
    rule since 0.5.4 ("Unique Items that grant Skills now use the lowest level of the Skill that you meet the
    requirements of when equipped, scaling up to the maximum described level on the item as you meet the Skill
    requirements"). The note names Unique Items only; a RARE Voltaic Staff's granted Lightning Bolt follows the same
    rule (the user, 14 Nov 2026, in-game: "Yes, same as uniques"; AUDIT36 A36-13), as PoB applies it to every item.
    COV30's level-3 Lightning Bolt was the test character's 15 Intelligence, not a lost level:
    probe POE2T2504 (scratch/cov31/probe_level.py), character 90: Int 15 -> 3, 75 -> 11, 165 -> 20; character 60,
    Int 165 -> 14 (level 15 needs character level 61 or more)."""
    ctx, build = setup

    def level(character: int, intelligence: int) -> int:
        build.call("setLevel", level=character)
        raw = "\n".join(["Rarity: Rare", "Test Staff", "Voltaic Staff", "Item Level: 82", "Implicits: 1",
                         "Grants Skill: Level 20 Lightning Bolt", f"+{intelligence} to Intelligence"])
        build.call("equip", slot="Weapon 1", raw=raw, fields=[])
        found = [gem.get("level") for group in build.call("groups")["groups"] if group.get("fromItem")
                 for gem in group.get("gems") or [] if gem.get("name") == "Lightning Bolt"]
        build.call("equip", slot="Weapon 1", fields=[])
        assert len(found) == 1, found
        return found[0]

    try:
        assert level(90, 150) == 20  # the line's level once the character meets it (Int 157 at level 20)
        assert level(90, 60) == 11  # Int short: the highest level the Int meets
        assert level(60, 150) == 14  # character level short: the highest level the character meets
    finally:
        build.call("setLevel", level=ctx.level)


def test_the_masters_reach_grants_untether_from_the_games_rows(setup):
    """COV34 (H5-3 parts 1-2): PoB has no Untether skill, so "Grants Skill: Level 15 Untether" parsed to nothing and
    the gloves granted no skill. The bridge adds AbyssalLivingBomb from the game's rows (GrantedEffectStatSets 7538,
    GrantedEffectStatSetsPerLevel: level 15 = 284-426 spell Physical, 13% crit) with its gem row (Int 100%), and The
    Master's Reach comes from builder/data/tool_uniques.json (PoB's unique list lacks it)."""
    ctx, build = setup
    parsed = build.call("parseLines", lines=["Grants Skill: Level 15 Untether"])["results"][0]
    assert parsed["supported"] and [m["name"] for m in parsed["parsed"]] == ["ExtraSkill"]
    name = "The Master's Reach"
    raw = ctx.corrected_raw(name, next(u["raw"] for u in ctx.data.uniques if u["name"] == name))
    assert ctx.granted_by.get("Untether") == [("unique", "The Master's Reach", None)]
    main = build.call("groups")["mainSocketGroup"]
    build.call("setLevel", level=90)
    try:
        def untether() -> list[dict]:
            return [g for g in build.call("groups")["groups"] if g.get("fromItem")
                    and any(gem.get("skillId") == "AbyssalLivingBomb" for gem in g.get("gems") or [])]

        # The gem row's Int requirement applies 0.5.4's level rule: the test character's 15 Int + the gloves' 20 meets
        # level 6 only (probe POE2T2557, scratch/cov34/probe_level.py: Int 35 -> 6, Int 185 -> 15)
        build.call("equip", slot="Gloves", raw=raw, fields=[])
        assert 1 <= untether()[0]["gems"][0]["level"] < 15
        build.call("equip", slot="Amulet", raw="\n".join(["Rarity: Rare", "Test Amulet", "Gold Amulet", "Item Level: 82",
                                                         "Implicits: 0", "+150 to Intelligence"]), fields=[])
        groups = untether()
        assert len(groups) == 1, groups
        gem = groups[0]["gems"][0]
        assert gem["name"] == "Untether" and gem["level"] == 15
        stats = build.call("setGroup", index=groups[0]["index"], main=True,
                           fields=["PhysicalMinBase", "PhysicalMaxBase", "FireMaxBase", "CritChance", "AverageHit"])["stats"]
        # (probe POE2T2559: these, and AverageHit 137.6 after the boss preset's armour)
        assert (stats["PhysicalMinBase"], stats["PhysicalMaxBase"], stats["FireMaxBase"]) == (284, 426, 0)
        assert stats["CritChance"] == 13 and stats["AverageHit"] > 0
    finally:
        build.call("setGroup", index=main, main=True, fields=[])
        build.call("equip", slot="Gloves", fields=[])
        build.call("equip", slot="Amulet", fields=[])
        build.call("setLevel", level=ctx.level)


def test_a_bought_rares_inherent_skill_is_at_its_item_levels_skill_level():
    """COV35 (PATCH_CHECK K3-2): a base's "Grants Skill: Level (1-20) X" is set by the item's item level, not rolled, so a
    generated rare carries the item level's skill level (rares.INHERENT_SKILL_LEVELS: 82 -> 18, 68 -> 16), not the
    middle of the range (10 at every gear tier before); a range is kept within its bounds, and a line below the table is
    rolled as before."""
    from builder.search import rares

    wand = {"name": "Withered Wand", "type": "Wand", "implicit": "Grants Skill: Level (1-20) Chaos Bolt"}
    assert "Grants Skill: Level 18 Chaos Bolt" in rares.rare_text("Weapon 1", wand, 82, []).splitlines()
    assert "Grants Skill: Level 16 Chaos Bolt" in rares.rare_text("Weapon 1", wand, 68, []).splitlines()
    assert "Grants Skill: Level 20 Chaos Bolt" in rares.rare_text("Weapon 1", wand, 90, []).splitlines()
    assert rares.implicit_lines(wand) == ["Grants Skill: Level 10 Chaos Bolt"]  # (no item level: the middle roll)
    assert rares.inherent_skill_line("Grants Skill: Level (5-10) Lightning Bolt", 86) == "Grants Skill: Level 10 Lightning Bolt"
    assert rares.inherent_skill_line("Grants Skill: Level (1-20) Chaos Bolt", 40) is None
    assert rares.inherent_skill_line("Adds (1-3) to (5-8) Physical Damage", 82) is None
