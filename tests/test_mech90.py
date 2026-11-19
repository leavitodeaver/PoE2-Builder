"""MECH90 (MECH-A): Tolerant Equipment's helmet conditions (engine/bridge.lua helmetConditions); Emergent Instinct under
one condition (conditions.EMERGENT_VAR)."""
import pytest

from builder.search import conditions


@pytest.fixture(scope="module")
def engine():
    import faulthandler

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


def test_tolerant_equipment_reads_the_equipped_helmets_armour_and_evasion(engine):
    """PoB parses "Immune to Bleeding if Equipped Helmet has higher Armour than Evasion Rating" (and the Poison / Evasion
    reverse) on Condition:HelmetArmourHigherThanEvasion / HelmetEvasionHigherThanArmour, which nothing in PoB set (the
    coverage audit's never-on rows): the bridge now sets them from the equipped helmet's own Armour and Evasion."""
    from builder.request.schema import BuildRequest
    from builder.search.context import Context, make_payload
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    payload = make_payload(BuildRequest(main_skill="Boneshatter", ascendancies=["Titan"]))
    skeleton = enumerate_skeletons(Context(payload), ["Boneshatter"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    build.call("setAssumptions", mods=[], fields=[])
    lines = ["Immune to Bleeding if Equipped Helmet has higher Armour than Evasion Rating",
             "Immune to Poison if Equipped Helmet has higher Evasion Rating than Armour"]
    ring = chr(10).join(["Rarity: Rare", "Test Ring", "Ruby Ring", "Item Level: 82", "Implicits: 0", *lines])
    build.call("equip", slot="Ring 1", raw=ring, fields=[])

    def avoid(base: str) -> tuple[float, float]:
        helmet = chr(10).join(["Rarity: Normal", base, "Item Level: 82", "Implicits: 0"])
        build.call("equip", slot="Helmet", raw=helmet, fields=[])
        out = build.call("stats", fields=["BleedAvoidChance", "PoisonAvoidChance"])
        return out.get("BleedAvoidChance") or 0, out.get("PoisonAvoidChance") or 0

    armour = avoid("Rusted Greathelm")  # Armour only
    evasion = avoid("Shabby Hood")  # Evasion only
    print("PROBE Tolerant Equipment (Bleed, Poison avoid): Armour helmet", armour, "Evasion helmet", evasion)
    assert armour[0] == pytest.approx(100) and armour[1] < 100
    assert evasion[1] == pytest.approx(100) and evasion[0] < 100


def test_emergent_instinct_counts_under_one_condition(engine):
    """MECH90 (an UNDERCOUNT): PoB parses "Targets that are Blinded, Maimed, and Bleeding cannot Evade your Hits" to the
    enemy's CannotEvade under its three conditions, whose boxes the search weighs one at a time (each taking the whole
    gain off); the bridge now hands it to PoB under the one Condition:BlindedMaimedBleeding (conditions.EMERGENT_VAR,
    weighed at the share all three hold): the three boxes alone no longer switch it on."""
    from builder.request.schema import BuildRequest
    from builder.search.context import Context, make_payload
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    payload = make_payload(BuildRequest(main_skill="Boneshatter", ascendancies=["Titan"]))
    skeleton = enumerate_skeletons(Context(payload), ["Boneshatter"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    build.call("setAssumptions", mods=[], fields=[])
    boxes = ("conditionEnemyBlinded", "conditionEnemyMaimed", "conditionEnemyBleeding", conditions.EMERGENT_VAR)

    def chance(line: str, on: tuple) -> float:
        ring = chr(10).join(["Rarity: Rare", "Test Ring", "Ruby Ring", "Item Level: 82", "Implicits: 0", line])
        build.call("equip", slot="Ring 1", raw=ring, fields=[])
        build.set_config({box: (box in on) or False for box in boxes})
        return build.call("stats", fields=["HitChance"]).get("HitChance") or 0

    rune = "Targets that are Blinded, Maimed, and Bleeding cannot Evade your Hits"
    plain = chance("+10 to Strength", boxes)
    three = chance(rune, boxes[:3])
    one = chance(rune, boxes[3:])
    print("PROBE Emergent Instinct HitChance: plain (all boxes)", plain, "rune (Blinded, Maimed, Bleeding boxes)", three,
          "rune (its own condition alone)", one)
    assert one == pytest.approx(100)
    assert three < 100 and plain < 100


def test_emergent_instincts_share_is_the_product_of_the_three():
    lines = ["Targets that are Blinded, Maimed, and Bleeding cannot Evade your Hits"]
    found = {"conditionEnemyBlinded": (True, "b"), "conditionEnemyMaimed": (True, "m"),
             "conditionEnemyBleeding": (True, "PoB")}
    uptimes = {"conditionEnemyBlinded": 0.5, "conditionEnemyMaimed": 0.8}  # Bleeding found with no share: all fight
    on, basis, up = conditions._blinded_maimed_bleeding(lines, {"_found_so_far": (found, uptimes)})
    assert on is True and up == pytest.approx(0.4) and "up to 50%" in basis
    # Maimed never: off, with its reason
    got = conditions._blinded_maimed_bleeding(lines, {"_found_so_far": ({**found, "conditionEnemyMaimed": None},
                                                                       {"conditionEnemyBlinded": 0.5})})
    assert got[0] is False and "Maimed 0%" in got[1]
    # no line: nothing
    assert conditions._blinded_maimed_bleeding([], {"_found_so_far": (found, uptimes)}) == (0.0, "")


def test_crippling_toxins_maims_on_the_hits_against_a_poisoned_boss(monkeypatch):
    """MECH90 (an UNDERCOUNT, the catalog's damaging-ailments entry): "25% chance for Attacks to Maim on Hit against
    Poisoned Enemies" (Crippling Toxins) was no Maim source (DEBUFF_NOT_SOURCE's "against"; its "chance for Attacks to"
    wasn't read): now its hits on a Poisoned boss, at the Poisoned share (TARGET_AILMENT)."""
    text = "25% chance for Attacks to Maim on Hit against Poisoned Enemies"
    monkeypatch.setattr(conditions, "target_ailment_share", lambda stats, name: 0.5)
    monkeypatch.setattr(conditions.models, "hits_per_second", lambda stats: 2.0)
    monkeypatch.setattr(conditions.models, "duration_scale", lambda lines, verb: 1.0)
    monkeypatch.setattr(conditions.bosses, "benefit_skill_time", lambda stats: 1.0)
    up, how = conditions._debuff_source(text, "conditionEnemyMaimed", [text], {})
    # 25% x 2 hits/s x 50% of the fight Poisoned x 4 s = 4 tries: 1 - 0.75^4
    assert up == pytest.approx(1 - 0.75 ** 4) and "poisoned boss" in how
    # an "increased" line against Poisoned enemies is still no source
    assert conditions._debuff_source("10% increased Maim Duration against Poisoned Enemies", "conditionEnemyMaimed",
                                     [], {}) is None


def test_lycosidae_blocks_poison_the_boss():
    """MECH90 (an UNDERCOUNT, the catalog's damaging-ailments unique row): Lycosidae's "Blocking Damage Poisons the Enemy
    as though dealing 200 Base Chaos Damage" (PoB: unparsed, 0) is a 40 Chaos/s Poison per Block, 2 s, the strongest
    Poison counting."""
    import math

    from builder.search import boss_hits, bosses, models

    line = ["Blocking Damage Poisons the Enemy as though dealing 200 Base Chaos Damage"]
    stats = {"EffectiveBlockChance": 50.0, "EvadeChance": 0.0, "enemySkillTime": 1.0}
    blocks = boss_hits.block_share(stats) / bosses.benefit_skill_time(stats)
    up = 1 - math.exp(-blocks * 2)
    gain, notes = models.block_poison_model(line, stats, 0.0, 0.0)
    assert blocks > 0 and gain == pytest.approx(up * 40.0) and "Lycosidae" in notes[0]["label"]
    # the boss's 25% Chaos resistance, and a stronger Poison of the build's own: nothing over it
    assert models.block_poison_model(line, stats, 25.0, 0.0)[0] == pytest.approx(up * 30.0)
    assert models.block_poison_model(line, stats, 0.0, 100.0)[0] == 0.0
    # no Block: 0
    assert models.block_poison_model(line, {**stats, "EffectiveBlockChance": 0.0}, 0.0, 0.0)[0] == 0.0
