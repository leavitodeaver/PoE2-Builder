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


def test_lycosidae_poison_runs_beside_the_builds_own_with_room_to_stack():
    """The manager, 11:45: with PoB's PoisonStacksMax above 1 Lycosidae's Poison takes a free slot instead of having to
    beat the build's own: its average count on the boss (blocks x 2 s), up to the slots left."""
    from builder.search import boss_hits, bosses, models

    line = ["Blocking Damage Poisons the Enemy as though dealing 200 Base Chaos Damage"]
    stats = {"EffectiveBlockChance": 50.0, "EvadeChance": 0.0, "enemySkillTime": 1.0}
    n = 2 * boss_hits.block_share(stats) / bosses.benefit_skill_time(stats)
    # 3 slots, the build's own stronger Poison in one: two free, n < 2 on average
    assert models.block_poison_model(line, stats, 0.0, 100.0, 3.0)[0] == pytest.approx(40.0 * min(n, 2.0))
    # 2 slots and no Poison of its own: both free
    assert models.block_poison_model(line, stats, 0.0, 0.0, 2.0)[0] == pytest.approx(40.0 * min(n, 2.0))
    # 1 slot: the strongest-only reading
    assert models.block_poison_model(line, stats, 0.0, 100.0, 1.0)[0] == 0.0


def test_engineered_blaze_counts_the_ignited_boss():
    """MECH90 (an UNDERCOUNT): PoB parses Engineered Blaze only in part (never applied); a lone boss Ignited is 1 enemy:
    +4% Attack Area of Effect at its Ignited share."""
    from builder.search import models

    line = ["4% increased Area of Effect for Attacks per Enemy you've Ignited in the last 8 seconds, up to 40%"]
    mods, notes = models.engineered_blaze_mods(line, 0.5)
    assert mods[0]["line"] == models.ATTACK_AREA_LINE and mods[0]["scale"] == pytest.approx(2.0)
    assert models.engineered_blaze_mods(line, 0.0)[0] == [] and models.engineered_blaze_mods([], 1.0) == ([], [])


def test_inner_turmoil_plays_the_volatility_cycle_only_when_it_pays():
    """MECH90 (an UNDERCOUNT: PoB reads no Volatility): attack T s, stop 4 s so it explodes, 10 s of Volatile Power."""
    from builder.search import models

    lines = ["Gain 1 Volatility on inflicting an Elemental Ailment", "Take no Damage from Volatility"]
    fast = {"Speed": 10.0, "HitChance": 100.0, "IgniteChancePerHit": 100.0}
    mods, notes = models.volatility_model(lines, fast)
    # 10 Volatility a second: attack 20 s, 200 (the cap) explode, stop 4 s: (10 x 3.0 + 10) / 24 = 1.67, the best T
    # (10 s: 20 / 14 = 1.43)
    assert [m["line"] for m in mods] == ["1% less Damage", "Gain 1% of Damage as Extra Chaos Damage"]
    assert mods[0]["scale"] == pytest.approx(400 / 24, abs=0.01) and mods[1]["scale"] == pytest.approx(100.0)
    assert "attack 20 s" in notes[0]["basis"]
    # Maximum Volatility 30: at best (3 x 1.3 + 0) / 7 or (10 x 1.3) / 14, under 1 at every T: not played
    capped = models.volatility_model(lines + ["Maximum Volatility is 30"], fast)
    assert capped[0] == [] and "not played" in capped[1][0]["value"]
    # no ailment: nothing gained, not played; no line: nothing
    assert models.volatility_model(lines, {"Speed": 2.0, "HitChance": 100.0})[0] == []
    assert models.volatility_model([], fast) == ([], [])


def test_saitha_aggravates_the_ignite_at_the_builds_own_rates():
    """RESEARCH33 R33-8: lambda = h / (1/c + 1/p) Aggravations a second; the Ignite Aggravated a = 1 - (1 - e^-x) / x
    of its life (x = lambda D); 0 when every Bleed is already Aggravated (the line never fires: an OVERCOUNT trap)."""
    import math

    from builder.search import models

    lines = ["Aggravating any Bleeding with this Weapon also Aggravates all Ignites on the target",
             "30% chance to Aggravate Bleeding on Hit"]
    stats = {"Speed": 3.0, "HitChance": 100.0, "BleedChancePerHit": 20.0, "IgniteDuration": 4.0, "IgniteDPS": 100.0,
             "IgniteChancePerHit": 10.0}
    mods, notes = models.saitha_ignite_mods(lines, stats, "Spear Throw", True)
    rate = 3.0 / (1 / 0.2 + 1 / 0.3)  # 0.36 / s, the research's example
    a = 1 - (1 - math.exp(-rate * 4)) / (rate * 4)  # 0.47
    assert mods[0]["line"] == models.COMPOUND_LINE and mods[0]["scale"] == pytest.approx(a * 100, abs=0.01)
    assert notes[0]["value"] == f"x{1 + a:.2f} Ignite damage"
    assert models.saitha_ignite_mods(lines + ["Bleeding you inflict is Aggravated"], stats, "Spear Throw", True)[0] == []
    assert models.saitha_ignite_mods(lines, stats, "Spark", False)[0] == []
    # (the manager, 16:20) a conditional line takes its condition's share: Bleeds on a Cursed boss (60% of the fight) are
    # already Aggravated, so only the Bleed chance of the other 40% feeds the spear
    cursed = models.saitha_ignite_mods(lines + ["Bleeding you inflict on Cursed targets is Aggravated"], stats,
                                       "Spear Throw", True, {"Cursed": 0.6})
    rate_c = 3.0 / (1 / (0.2 * 0.4) + 1 / 0.3)
    a_c = 1 - (1 - math.exp(-rate_c * 4)) / (rate_c * 4)
    assert cursed[0][0]["scale"] == pytest.approx(a_c * 100, abs=0.01) and "Cursed 60%" in cursed[1][0]["basis"]
    # Cursed all fight: never fires
    assert models.saitha_ignite_mods(lines + ["Bleeding you inflict on Cursed targets is Aggravated"], stats,
                                     "Spear Throw", True, {"Cursed": 1.0})[0] == []
    assert models.saitha_ignite_mods(lines[1:], stats, "Spear Throw", True) == ([], [])


def test_idol_of_egrin_curse_zone_delay_is_curse_delay(engine):
    """RESEARCH33 R33-9: "Curse zones erupt after 20% reduced delay" is base_curse_delay_+%, PoB's CurseDelay INC."""
    got = engine.call("parseLines", lines=["Curse zones erupt after 20% reduced delay"])["results"][0]
    assert got["supported"] and got["parsed"][0]["name"] == "CurseDelay" and got["parsed"][0]["value"] == -20


def test_growing_peril_and_precise_volatility_on_the_cycle():
    """The manager, 16:20: Growing Peril on the Volatility held (the cap all fight when not played), Precise Volatility
    on the powered share."""
    from builder.search import models

    lines = ["Gain 1 Volatility on inflicting an Elemental Ailment", "1% increased Chaos Damage over Time per Volatility",
             "Volatile Power also grants 1% increased Critical Hit chance per Volatility exploded"]
    fast = {"Speed": 10.0, "HitChance": 100.0, "IgniteChancePerHit": 100.0}
    mods = {m["line"]: m["scale"] for m in models.volatility_model(lines, fast)[0]}
    # attack 20 s at 10 a second: the cap 200 reached at 20 s, so 100 held on average; 100% crit (200 x half powered)
    assert mods["1% increased Chaos Damage over Time"] == pytest.approx(100.0)
    assert mods["1% increased Critical Hit Chance"] == pytest.approx(100.0)
    # not played (Maximum 30): 30 held all fight, no crit
    capped = {m["line"]: m["scale"] for m in models.volatility_model(lines + ["Maximum Volatility is 30"], fast)[0]}
    assert capped == {"1% increased Chaos Damage over Time": 30.0}
