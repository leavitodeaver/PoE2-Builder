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
