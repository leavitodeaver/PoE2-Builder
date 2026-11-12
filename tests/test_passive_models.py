"""COV-B's passive-line models (builder/search/passive_models.py): No Mercy from the build's Hit share, Rhythm of Ice
from the main spell's echoed casts."""

from __future__ import annotations

import pytest

from builder.ledger import model_entry, translate
from builder.search import passive_models as pm
from builder.search.models import UNIT, Inputs, Skill

NO_MERCY = "Deal up to 40% more Damage to Enemies based on their missing Concentration"
WITCHBANE = ["Enemies have Maximum Concentration equal to 30% of their Maximum Life",
             "Break enemy Concentration on Hit equal to 100% of Damage Dealt"]
RHYTHM = "6% increased Cast Speed per Spell Echoed Recently, up to 30%"


def test_no_mercy_average_follows_the_hit_share():
    """RESEARCH8's numbers: Hits only x1.329 (82% of 40%, not the old flat 90%); half Hits x1.265; a fifth x1.128."""
    assert pm.no_mercy_more(40, 1.0) == pytest.approx(32.9, abs=0.1)
    assert pm.no_mercy_more(40, 0.5) == pytest.approx(26.5, abs=0.1)
    assert pm.no_mercy_more(40, 0.2) == pytest.approx(12.8, abs=0.1)
    assert pm.no_mercy_more(40, 0.0) == 0.0
    assert pm.no_mercy_more(40, 1.0) < 36.0  # below the retired entry's 90% of the maximum
    assert pm.hit_share({"TotalDPS": 500, "CombinedDPS": 1000}) == 0.5
    assert pm.hit_share({"TotalDPS": 500}) == 0.0


def test_no_mercy_model_needs_witchbane_and_derives_from_the_build():
    inputs = Inputs(flags={"NoMercy"})
    stats = {"TotalDPS": 1000, "CombinedDPS": 2000}
    mods, notes = pm.no_mercy_model(inputs, stats, [NO_MERCY, *WITCHBANE])
    derived = next(m for m in mods if m["kind"] == "Derived")
    assert derived["fn"] == "noMercy" and derived["value"] == round(pm.no_mercy_more(40, 0.5) * UNIT)
    assert derived["args"] == {"maximum": 40.0, "concentration": 30.0, "broken": 100.0}
    lines = [m for m in mods if m["kind"] == "Line"]
    assert lines[0]["per"] == "NoMercyMore" and {"var": "Witchbane"} in lines[0]["when"]
    assert notes[0]["value"] == "26.5%" and notes[0]["confidence"] == "theory"
    mods, notes = pm.no_mercy_model(inputs, stats, [NO_MERCY])  # no Witchbane: no Concentration to miss
    assert mods == [] and notes[0]["value"] == "not counted"
    assert pm.no_mercy_model(Inputs(), stats, [NO_MERCY, *WITCHBANE]) == ([], [])


def test_rhythm_of_ice_counts_the_main_spells_echoes():
    spell = Skill("Frost Bomb", group=1, spell=True)
    cascadable = {"Spell", "Cascadable", "Area"}
    # Spell Echo on the main spell: every use Echoes; 1.25 uses/s reach the 30% cap, 1/s gives 24%
    assert pm.echo_chance(spell, cascadable, {"Spell Echo"}, [])[0] == 1.0
    assert pm.rhythm_speed(6, 30, 1.0, 1.0) == pytest.approx(24.0)
    assert pm.rhythm_speed(6, 30, 2.0, 1.0) == 30.0
    # Zarokh's Refrain: its 5 s cooldown caps the uses: 0.2/s x 4 s x 6% = 4.8%
    assert pm.echo_chance(spell, cascadable, {"Zarokh's Refrain"}, [])[1] == "Zarokh's Refrain"
    assert pm.rhythm_speed(6, 30, 2.0, 1.0, cooldown=5.0) == pytest.approx(4.8)
    # Now and Again's 20% chance without an Echo support: 2 uses/s x 0.2 x 4 s x 6% = 9.6%
    chance, _ = pm.echo_chance(spell, cascadable, set(), ["Cascadable Spells have 20% chance to Echo"])
    assert chance == pytest.approx(0.2) and pm.rhythm_speed(6, 30, 2.0, chance) == pytest.approx(9.6)
    # nothing Echoes: a spell that isn't Cascadable, a triggered one, one cast by a totem, or no Echo source
    assert pm.echo_chance(spell, {"Spell"}, {"Spell Echo"}, [])[0] == 0.0
    assert pm.echo_chance(Skill("Frost Bomb", group=1, spell=True, triggered=True), cascadable, {"Spell Echo"}, [])[0] == 0.0
    assert pm.echo_chance(spell, cascadable, {"Spell Echo"}, [], by_totem=True)[0] == 0.0
    assert pm.echo_chance(spell, cascadable, set(), [])[0] == 0.0


def test_rhythm_of_ice_model_lines():
    spell = Skill("Frost Bomb", group=1, spell=True)
    inputs = Inputs(flags={"RhythmOfIce"})
    mods, notes = pm.rhythm_model(inputs, {"Speed": 1.0}, [RHYTHM], spell, {"Spell", "Cascadable"}, {"Spell Echo"})
    derived = next(m for m in mods if m["kind"] == "Derived")
    assert derived["fn"] == "rhythmOfIce" and derived["value"] == 24 * UNIT
    assert derived["args"] == {"per": 6.0, "cap": 30.0, "chance": 1.0, "seals": 0.0}
    assert notes[0]["value"] == "24.0%"
    mods, notes = pm.rhythm_model(inputs, {"Speed": 3.0}, [RHYTHM], spell, {"Spell", "Cascadable"}, set())
    assert next(m for m in mods if m["kind"] == "Derived")["value"] == 0 and notes[0]["value"] == "not counted"


def test_the_ledger_routes_both_lines_to_the_models():
    for text, model, flag in ((NO_MERCY, "no_mercy", "flag:NoMercy"), (RHYTHM, "rhythm_of_ice", "flag:RhythmOfIce")):
        entry = model_entry(text)
        assert entry is not None and entry.raw["model"] == model
        assert translate(text)[1] == [flag]  # the old flat entries are retired


def test_slow_magnitude_is_chill_magnitude_on_passives_and_uniques_alike():
    """AUDIT6 A6-1: one entry owns the line, PoB's unique range and a rolled value included."""
    for text, value in (("Debuffs you inflict have (12-30)% increased Slow Magnitude", "(12-30)"),
                        ("Debuffs you inflict have 22% increased Slow Magnitude", "22")):
        entry, lines = translate(text)
        assert entry.id == "passives-slow-magnitude-chill"
        assert lines == [f"{value}% increased Magnitude of Chill you inflict"]


def test_engine_applies_the_derived_values(tmp_path):
    """On a real build (Stormweaver Spark, all Hits), No Mercy's derived more Damage and Rhythm of Ice's cast speed reach
    PoB's numbers through the bridge (DERIVED.noMercy / DERIVED.rhythmOfIce), with their conditions on."""
    import faulthandler

    from builder.data import GameData
    try:
        GameData()
    except FileNotFoundError:
        pytest.skip("no game-data dump")
    from builder.pob.engine import PobEngine
    from builder.request.schema import BuildRequest
    from builder.search.context import Context, make_payload
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    enabled = faulthandler.is_enabled()
    faulthandler.disable()
    try:
        engine = PobEngine()
        payload = make_payload(BuildRequest(main_skill="Spark", ascendancies=["Stormweaver"]))
        skeleton = enumerate_skeletons(Context(payload), ["Spark"])[0][0]
        search = Search(engine, None, payload, skeleton.as_dict(), 0)
        search.setup(quests=False)
        build = search.build
        fields = ["CombinedDPS", "TotalDPS", "Speed"]
        base = list(search.assumptions.mods)
        before = build.call("setAssumptions", mods=base, fields=fields)["stats"]
        share = pm.hit_share(before)
        assert share > 0.9
        conditions = [{"kind": "Condition", "name": n} for n in ("NoMercy", "Witchbane", "RhythmOfIce")]
        mercy, _ = pm.no_mercy_model(Inputs(flags={"NoMercy"}), before, [NO_MERCY, *WITCHBANE])
        spell = Skill("Spark", group=1, spell=True)
        rhythm, _ = pm.rhythm_model(Inputs(flags={"RhythmOfIce"}), before, [RHYTHM], spell, {"Spell", "Cascadable"},
                                    {"Spell Echo"})
        after = build.call("setAssumptions", mods=base + conditions + mercy, fields=fields)["stats"]
        more = pm.no_mercy_more(40, share)
        assert after["CombinedDPS"] / before["CombinedDPS"] == pytest.approx(1 + more / 100, rel=0.01)
        faster = build.call("setAssumptions", mods=base + conditions + rhythm, fields=fields)["stats"]
        assert faster["Speed"] > before["Speed"] * 1.05
        build.call("setAssumptions", mods=base, fields=fields)
    finally:
        if enabled:
            faulthandler.enable()


def test_emboldening_casts_and_enhancing_attacks_read_the_pending_counts():
    emboldening = "12% increased Attack Damage for each different Non-Instant Spell you've used in the past 8 seconds"
    enhancing = "12% increased Spell Damage for each different Non-Instant Attack you've used in the past 8 seconds"
    assert translate(emboldening)[1] == ["flag:EmboldeningCasts"] and model_entry(enhancing).raw["model"] == "different_skills"
    mods, notes = pm.different_skills_model(Inputs(flags={"EmboldeningCasts", "EnhancingAttacks"}), [emboldening, enhancing])
    assert [(m["line"], m["scale"], m["per"]) for m in mods] == [
        ("1% increased Attack Damage", 12.0, "DifferentNonInstantSpells8s"),
        ("1% increased Spell Damage", 12.0, "DifferentNonInstantAttacks8s")]
    assert all(n["value"] == "0 until the count lands" for n in notes)
    assert pm.different_skills_model(Inputs(), [emboldening]) == ([], [])


def test_rhythm_of_ice_counts_comet_and_skyfall_quality_and_tuls_avalanche():
    """A9-3 (COVB5): Comet's and Skyfall's own quality gives 0.5% chance to Echo per quality (GrantedEffectQualityStats),
    summed with Now and Again; Tul's Avalanche makes one Echo per Seal, a Seal every 2 Blocks of the boss's hits."""
    cascadable = {"Spell", "Cascadable", "Area"}
    comet = Skill("Comet", group=1, spell=True)
    assert pm.echo_chance(comet, cascadable, set(), [], quality=20)[0] == pytest.approx(0.10)
    assert pm.echo_chance(comet, cascadable, set(), ["Cascadable Spells have 20% chance to Echo"], quality=20)[0] \
        == pytest.approx(0.30)
    assert pm.echo_chance(Skill("Skyfall", group=1, spell=True), cascadable, set(), [], quality=10)[0] == pytest.approx(0.05)
    assert pm.echo_chance(Skill("Frost Bomb", group=1, spell=True), cascadable, set(), [], quality=20)[0] == 0.0
    # 1.5 casts/s at 10%: 0.15 Spells Echoed a second, 0.6 in 4 s x 6% = 3.6%
    assert pm.rhythm_speed(6, 30, 1.5, 0.10) == pytest.approx(3.6)
    # Tul's Avalanche: boss hit every 0.7 s, none evaded, 35% blocked: 0.5 Blocks/s, 0.25 Seals/s
    stats = {"enemySkillTime": 0.7, "EvadeChance": 0, "EffectiveBlockChance": 35, "Speed": 2.0}
    spell = Skill("Frost Bomb", group=1, spell=True)
    seals = pm.seal_rate(spell, cascadable, {"Tul's Avalanche"}, stats)
    assert seals == pytest.approx(0.25)
    assert pm.seal_rate(spell, cascadable, set(), stats) == 0.0
    assert pm.seal_rate(spell, cascadable, {"Tul's Avalanche"}, {**stats, "Cooldown": 2.0}) == 0.0  # can't support it
    assert pm.seal_rate(spell, cascadable, {"Tul's Avalanche"}, stats, by_totem=True) == 0.0
    # 2 casts/s, 0.25 Seals/s: 1 Spell Echoed in 4 s, 6%; with a 10% chance: 0.2 + 0.9 x 0.25 = 0.425/s, 10.2%
    assert pm.rhythm_speed(6, 30, 2.0, 0.0, seals=seals) == pytest.approx(6.0)
    assert pm.rhythm_speed(6, 30, 2.0, 0.1, seals=seals) == pytest.approx(10.2)
    assert pm.echoed_per_second(0.1, 0.0, 0.25) == pytest.approx(0.1)  # never more Echoes than uses
    mods, notes = pm.rhythm_model(Inputs(flags={"RhythmOfIce"}), stats, [RHYTHM], spell, cascadable, {"Tul's Avalanche"})
    derived = next(m for m in mods if m["kind"] == "Derived")
    assert derived["args"]["seals"] == pytest.approx(0.25) and derived["value"] == round(6.0 * UNIT)
    assert "Tul's Avalanche" in notes[0]["basis"]
    # A13-4: no Seals from Tul's Avalanche on a spell that already gains Seals (a Seal-family support, or HasSeals)
    families = {"Unleash": {"Seal"}, "Tul's Avalanche": {"SpellEcho", "Seal"}, "Controlled Destruction": set()}.get
    assert pm.seal_rate(spell, cascadable, {"Tul's Avalanche", "Controlled Destruction"}, stats,
                        families=families) == pytest.approx(0.25)
    assert pm.seal_rate(spell, cascadable, {"Tul's Avalanche", "Unleash"}, stats, families=families) == 0.0
    assert pm.seal_rate(spell, cascadable | {"HasSeals"}, {"Tul's Avalanche"}, stats, families=families) == 0.0
    mods, notes = pm.rhythm_model(Inputs(flags={"RhythmOfIce"}), stats, [RHYTHM], spell, cascadable,
                                  {"Tul's Avalanche", "Unleash"}, families=families)
    derived = next(m for m in mods if m["kind"] == "Derived")
    assert derived["args"]["seals"] == 0.0 and derived["value"] == 0
    assert "already gains Seals from Unleash" in notes[0]["basis"]


def test_arcane_surge_on_crit_tree_lines_drop_pobs_parse():
    """MECHC3 / COVB5: PoB turns "N% chance to Gain Arcane Surge when you deal a Critical Hit" into Arcane Surge held
    whenever you've crit recently. A `pob_wrong` entry with nothing in its place has the bridge drop the node's parsed
    modifier (the Arcane Surge uptime model rates the line); its tree nodes are the 5% smalls and Aspiring Genius."""
    from builder.data import GameData
    try:
        data = GameData()
    except FileNotFoundError:
        pytest.skip("no game-data dump")
    found = pm.wrong_tree_lines(data.tree["nodes"])
    surge = [f for f in found if f["entry"] == "passives-wrong-arcane-surge-on-crit"]
    assert {f["id"] for f in surge} >= {2244, 54067, 27388}
    assert all(f["lines"] == [] and "Arcane Surge" in f["drop"] for f in surge)
    from builder.ledger import wrong
    assert wrong("10% chance to Gain Arcane Surge when you deal a Critical Hit") is None  # item text: unchanged here


def test_engine_drops_the_arcane_surge_on_crit_parse(tmp_path):
    """With Aspiring Genius allocated and Crit Recently on, PoB's Arcane Surge comes only from the uptime box: turning
    the box on still raises cast speed (PoB's own parse would have held it on already)."""
    import faulthandler

    from builder.data import GameData
    try:
        GameData()
    except FileNotFoundError:
        pytest.skip("no game-data dump")
    from builder.pob.engine import PobEngine
    from builder.request.schema import BuildRequest
    from builder.search.context import Context, make_payload
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    enabled = faulthandler.is_enabled()
    faulthandler.disable()
    try:
        engine = PobEngine()
        payload = make_payload(BuildRequest(main_skill="Spark", ascendancies=["Stormweaver"]))
        skeleton = enumerate_skeletons(Context(payload), ["Spark"])[0][0]
        search = Search(engine, None, payload, skeleton.as_dict(), 0)
        search.setup(quests=False)
        build = search.build
        assert any(c.get("drop") and c["id"] == 27388 for c in search.ctx.passive_corrections)
        assert engine.call("parseLines", lines=["10% chance to Gain Arcane Surge when you deal a Critical Hit"]
                           )["results"][0]["supported"]  # PoB parses it: without the drop it would count
        build.allocate(add=[27388])
        assert any(n["id"] == 27388 for n in build.call("allocatedNodes")["nodes"])
        build.set_config({"conditionCritRecently": True, "buffArcaneSurge": False})
        off = build.call("stats", fields=["Speed"])["Speed"]
        build.set_config({"buffArcaneSurge": True})
        on = build.call("stats", fields=["Speed"])["Speed"]
        assert on > off * 1.03
    finally:
        if enabled:
            faulthandler.enable()
