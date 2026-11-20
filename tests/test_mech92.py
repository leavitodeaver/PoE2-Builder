"""MECH92 (MECH-A): Instability's "regain an equivalent amount of Volatility" and Volatility on Kill at the build's own
kills (PoB reads neither line)."""
import pytest

TURMOIL = ["Gain 1 Volatility on inflicting an Elemental Ailment", "Take no Damage from Volatility"]
INSTABILITY = "25% chance that when Volatility on you explodes, you regain an equivalent amount of Volatility"
FOUR = {"Speed": 4.0, "HitChance": 100.0, "IgniteChancePerHit": 100.0}
CHAOS = "Gain 1% of Damage as Extra Chaos Damage"


def test_volatility_refreshed_expected_count():
    from builder.search import models

    # no cap reached: 10 a window, regained 25%: 10 / (1 - 0.25)
    assert models.volatility_refreshed(10.0, 200.0, 0.25) == pytest.approx(10.0 / 0.75)
    # the cap after one regain: half the time 150, half the time (a regain first) 200
    assert models.volatility_refreshed(150.0, 200.0, 0.5) == pytest.approx(175.0)
    # no Instability: as before, min(cap, per window)
    assert models.volatility_refreshed(250.0, 200.0, 0.0) == pytest.approx(200.0)


def test_instability_raises_the_cycles_exploded_count():
    """4 Volatility a second: attack 30 s, 120 explode (x1.235); with Instability 25% a cycle explodes 0.75 x 120 +
    0.25 x 200 = 140 on average (x1.294), so +140 x 10 / 30 % of Damage as Chaos while attacking (40 before)."""
    from builder.search import models

    plain = {m["line"]: m["scale"] for m in models.volatility_model(TURMOIL, FOUR)[0]}
    mods, notes = models.volatility_model(TURMOIL + [INSTABILITY], FOUR)
    got = {m["line"]: m["scale"] for m in mods}
    assert plain[CHAOS] == pytest.approx(40.0)
    assert got[CHAOS] == pytest.approx(140.0 / 3, rel=1e-3)
    assert "Instability: 25%" in notes[0]["basis"] and "attack 30 s" in notes[0]["basis"]
    # Ancient Rune of Decay's 15% sums with it (40%)
    rune = "15% chance that when Volatility on you explodes, you regain an equivalent amount of Volatility"
    assert "Instability: 40%" in models.volatility_model(TURMOIL + [INSTABILITY, rune], FOUR)[1][0]["basis"]


def test_volatility_on_kill_alone_at_the_builds_kills():
    """Volatility on Kill without Inner Turmoil or crits: each kill's Volatility explodes by itself for 10 s of Volatile
    Power (1% of Damage as Chaos); 0 with no kills (a lone boss), said in a note."""
    from builder.search import models

    kills = (0.04, 0.04, "the King's adds")
    mods, notes = models.volatility_model(["10% chance to gain Volatility on Kill"], {}, kills=kills)
    assert {m["line"]: m["scale"] for m in mods} == {CHAOS: pytest.approx(0.04)}  # (0.004 a second x 10 s)
    assert notes[0]["label"] == "Volatility on Kill: Volatile Power*" and "the King's adds" in notes[0]["basis"]
    # Instability: 1 / 0.75 explosions a gain, each its own 10 s of Volatile Power, adding (R35-8)
    mods = models.volatility_model(["10% chance to gain Volatility on Kill", INSTABILITY], {}, kills=kills)[0]
    assert mods[0]["scale"] == pytest.approx(0.004 * 10 / 0.75, rel=1e-3)
    # no kills: nothing, with a note
    mods, notes = models.volatility_model(["10% chance to gain Volatility on Kill"], {},
                                          kills=(0.0, 0.0, "a lone boss"))
    assert mods == [] and notes[0]["value"] == "0 (no kills)"
    # no Volatility line at all: nothing
    assert models.volatility_model([INSTABILITY], {}, kills=kills) == ([], [])


def test_volatility_on_kill_joins_the_cycles_rate():
    from builder.search import models

    kills = (0.5, 0.5, "test adds")
    notes = models.volatility_model(TURMOIL + ["10% chance to gain Volatility on Kill"], FOUR, kills=kills)[1]
    assert "4.05 Volatility a second" in notes[0]["basis"] and "Volatility on Kill (10% x 0.500 Kills/s" in notes[0][
        "basis"]


def test_debuffs_on_immobilise_and_after_unpinned(monkeypatch):
    """MECH92: Fearful Paralysis' Intimidate "when you Immobilise them" at the main skill's Freezes, Electrocutes, Pins
    and Heavy Stuns, and Lockdown's Maim "after becoming Unpinned" at its Pins (both 0 before)."""
    from builder.search import conditions, models

    asked = []

    def rate(stats, events):
        asked.append(set(events))
        return (0.1 if events == {"pin"} else 0.2), "test"

    monkeypatch.setattr(models, "immobilise_rate", rate)
    up, how = conditions._debuff_source("Enemies are Intimidated for 4 seconds when you Immobilise them",
                                        "conditionEnemyIntimidated", [], {})
    assert up == pytest.approx(0.8) and asked[-1] == {"freeze", "electrocute", "pin", "stun"} and "Immobilised" in how
    up, how = conditions._debuff_source("Enemies are Maimed for 4 seconds after becoming Unpinned",
                                        "conditionEnemyMaimed", [], {})
    assert up == pytest.approx(0.4) and asked[-1] == {"pin"} and "Pins ending" in how


def test_the_survival_cap_with_armour_on_one_hit(  # (R35-2, the manager's ruling 22:40)
):
    """10000 Armour, 5000 Life: one hit of 100 x k keeps 100k x k / (10 + k), so 58 survive (the headline); unreduced 50
    and a hit per stack (9.1 each) 200, the rows."""
    from builder.search import models

    crits = {"Speed": 10.0, "HitChance": 100.0, "CritChance": 50.0, "Life": 5001.0, "Armour": 10000.0,
             "PhysicalDamageReductionMax": 90.0}
    basis = models.volatility_model([], crits, 100.0)[1][0]["basis"]
    assert "up to 58 " in basis and "10000 Armour" in basis
    assert "the cap 50 with unreduced" in basis and "the cap 200 with a hit per stack" in basis
    # 'Take no Damage from Volatility': no cap rows
    assert "the cap" not in models.volatility_model(["Take no Damage from Volatility"], crits, 100.0)[1][0]["basis"]


def test_volatile_power_from_each_explosion_adds():
    """R35-8: each explosion's Volatile Power is its own 10 s. 10 a second up to 60, a 2 s delay: attack 6 s (an 8 s
    cycle) holds the earlier one's last 2 s too, (6 + 0.6 x 8) / 8 = x1.35; if it replaced, attack 10 s, x1.333 (a row)."""
    from builder.search import models

    lines = TURMOIL + ["Maximum Volatility is 60", "50% reduced Volatility Explosion delay"]
    mods, notes = models.volatility_model(lines, {"Speed": 10.0, "HitChance": 100.0, "IgniteChancePerHit": 100.0})
    assert notes[0]["value"].startswith("x1.350") and "attack 6 s" in notes[0]["basis"]
    assert {m["line"]: m["scale"] for m in mods}[CHAOS] == pytest.approx(80.0)
    assert "replaces the earlier one's (R35-8's other reading): x1.333 (attack 10 s" in notes[0]["basis"]


def test_unstable_bond_joins_inner_turmoils_cycle():
    """MECH92 (MECHB44's routing): with Inner Turmoil, Unstable Bond's 3 Volatility a Persistent Reviving minion Killed
    adds to the cycle's rate at the build's death rate; without it, unstable_bond_model counts it alone (not here)."""
    from builder.search import models

    bond = "Gain 3 Volatility when an Allied Persistent Reviving Minion is Killed"
    notes = models.volatility_model(TURMOIL + [bond], FOUR, deaths=(0.5, "test deaths"))[1]
    assert "5.50 Volatility a second" in notes[0]["basis"] and "Unstable Bond (3 x 0.500" in notes[0]["basis"]
    crits = {"Speed": 10.0, "HitChance": 100.0, "CritChance": 50.0, "Life": 5001.0}
    notes = models.volatility_model(["Take no Damage from Volatility", bond], crits, 100.0, deaths=(0.5, "x"))[1]
    assert "Unstable Bond" not in notes[0]["basis"] and "5.00 Volatility a second" in notes[0]["basis"]


def test_the_crafted_jewels_two_jewel_condition():
    """MECH92: "Blind / Debilitate Enemies on Hit" and "Inflict Elemental Exposure on Hit" "while you have a <X> and a
    <Y> socketed in your tree" count only with both jewels in the tree's sockets (Blind / Debilitate read 0 before; the
    Exposure line counted on every hit before: an OVERCOUNT)."""
    from builder.search import conditions

    hits = {"Speed": 2.0, "HitChance": 100.0}
    blind = "Blind Enemies on Hit while you have a Ruby and a Sapphire socketed in your tree"
    up, how = conditions._debuff_source(blind, "conditionEnemyBlinded", [], {**hits, "_tree_jewels": ["Ruby"]})
    assert up == 0.0 and "Sapphire missing" in how
    up, how = conditions._debuff_source(blind, "conditionEnemyBlinded", [],
                                        {**hits, "_tree_jewels": ["Ruby", "Sapphire", "Emerald"]})
    assert up == pytest.approx(1.0) and "both are" in how
    debilitate = "Debilitate Enemies on Hit while you have an Emerald and a Sapphire socketed in your tree"
    up, how = conditions._debuff_source(debilitate, "conditionEnemyDebilitated", [],
                                        {**hits, "_tree_jewels": ["Emerald", "Sapphire"]})
    assert up == pytest.approx(1.0)  # (2 hits a second x Debilitate's 1 s)
    exposure = "Inflict Elemental Exposure on Hit while you have a Ruby and an Emerald socketed in your tree"
    assert conditions._exposure_source(exposure, "Fire", {**hits, "_tree_jewels": ["Ruby", "Time-Lost Emerald"]})[0] == 0
    assert conditions._exposure_source(exposure, "Cold", {**hits, "_tree_jewels": ["Emerald", "Ruby"]})[0] == 1.0
    # a line without the clause is untouched
    assert conditions.socketed_pair("Blind Enemies on Hit", {}) is None


def test_frightening_shields_debilitate_rides_bastions_taps():
    """MECH92: "Apply Debilitate to Enemies 30 Metres in front of you while your Shield is raised" at Bastion of Light's
    shield taps (each Debilitating for the 0.55 s tap and Debilitate's 1 s after); no taps or the boss out of reach: 0."""
    from builder.search import conditions, models

    line = "Apply Debilitate to Enemies 30 Metres in front of you while your Shield is raised"
    var = "conditionEnemyDebilitated"
    plan = models.RaisedPlan(1.0, 0.14, "test", 0.25)
    up, how = conditions._debuff_source(line, var, [], {"_raised_plan": plan, "_distance_choice": (2.0, "")})
    assert up == pytest.approx(0.25 * (models.RAISE_TAP + 1.0)) and "Bastion of Light" in how
    assert conditions._debuff_source(line, var, [], {"_distance_choice": (2.0, "")})[0] == 0.0
    far = conditions._debuff_source(line, var, [], {"_raised_plan": plan, "_distance_choice": (40.0, "")})
    assert far[0] == 0.0 and "beyond" in far[1]


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


@pytest.mark.parametrize("skill, kind, field", [("Spark", "Shock", "TotalDPS"), ("Frostbolt", "Chill", "enemySkillTime")])
def test_the_stack_boxes_placeholder_reads_as_one_stack(engine, skill, kind, field):
    """MECH92 probe (the manager's placeholder check, MECHC44's note): PoB applies a 'count' box's placeholder (1 for
    ShockStacks / ChillStacks) when the input is unset or 0, so conditions.ailment_stacks' 0 (no stacking line) and its
    explicit 1 read the same as the box left unset: one stack, no double count; 2 adds the second."""
    from builder.request.schema import BuildRequest
    from builder.search.context import Context, make_payload
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    payload = make_payload(BuildRequest(main_skill=skill))
    skeleton = enumerate_skeletons(Context(payload), [skill])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    line = f"Targets can be affected by two of your {kind}s at the same time"
    build.call("equip", slot="Amulet", raw="\n".join(["Rarity: Rare", "Test Amulet", "Gold Amulet", "Item Level: 82",
                                                      "Implicits: 0", line]), fields=[])
    box = {"Shock": {"conditionEnemyShocked": True},
           "Chill": {"conditionEnemyChilled": True, "conditionEnemyChilledEffect": 30}}[kind]
    build.call("setConfig", vars=box, fields=[])
    unset = build.call("stats", fields=[field])[field]
    got = {}
    for n in (0, 1, 2):
        build.call("setConfig", vars={**box, f"{kind}Stacks": n}, fields=[])
        got[n] = build.call("stats", fields=[field])[field]
    print("PROBE placeholder", kind, unset, got)
    assert got[0] == pytest.approx(unset) and got[1] == pytest.approx(unset)
    assert abs(got[2] - unset) > 0.05 * abs(unset)


def test_the_tree_jewels_bases_are_read_from_the_allocated_sockets(engine):
    """MECH92 probe: Conditions._tree_jewels' source, the bridge's facts items, lists a jewel in an allocated tree socket
    with its base and type Jewel (the two-jewel condition of the crafted jewel suffixes reads these)."""
    from builder import ROOT
    from builder.pob import codes

    xml = codes.decode((ROOT / "benchmarks" / "reference" / "2b75b.txt").read_text(encoding="utf-8").strip())
    engine.call("loadXml", xml=xml)
    sockets = [n["id"] for n in engine.call("allocatedNodes")["nodes"] if n["type"] == "Socket"]
    assert len(sockets) >= 2
    for socket, base in zip(sockets, ("Ruby", "Sapphire")):
        engine.call("equip", slot=f"Jewel {socket}", raw="\n".join([
            "Rarity: Rare", f"Test {base}", base, "Item Level: 82", "Implicits: 0",
            "Blind Enemies on Hit while you have a Ruby and a Sapphire socketed in your tree"]))
    jewels = sorted(str(i.get("base")) for i in engine.call("facts", fields=[])["items"] if i.get("type") == "Jewel")
    print("PROBE tree jewels", jewels)
    assert "Ruby" in jewels and "Sapphire" in jewels
