"""Item lines (COV-A) whose reading depends on how PoB applies them, through a real engine: a charm's "during effect"
line and Maligaro's Virtuosity's crit lines. Boots one PoB engine for the module and needs the game-data dump."""

from __future__ import annotations

import faulthandler

import pytest

from builder.data import GameData
from builder.request.schema import BuildRequest
from builder.search.context import Context, make_payload
from builder.search.inner import Search
from builder.search.skeletons import enumerate_skeletons

HIT, DOT = "LightningTakenHitMult", "LightningTakenDotMult"
BELT = "\n".join(["Rarity: Rare", "Test Belt", "Plate Belt", "Item Level: 82", "Implicits: 2", "+160 to Armour",
                  "Has 3 Charm Slot"])
FLASK = "Rarity: Normal\nUltimate Life Flask"


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


def test_the_black_cats_unlucky_line_follows_the_charm_not_a_flask_and_cuts_hits_only(engine):
    """unlucky-enemy-lightning-charm: the stand-in drops "during effect" (PoB's tag for it is UsingFlask, which only a
    Life or Mana flask sets); a charm's lines join the build only while its slot is active. So the reduction is on with
    the charm on whether or not a flask is up, off with the charm switched off, and on Lightning Hits only (A10-2)."""
    ctx = Context(make_payload(BuildRequest(main_skill="Spark")))
    cat = next(u for u in ctx.data.uniques if u["name"] == "The Black Cat")
    corrected = ctx.corrected_raw(cat["name"], cat["raw"]).split("\n")
    assert "7% less Lightning Damage taken when Hit" in corrected  # one line: a charm keeps the larger of equal mods
    assert any(r["parsed"][0]["tags"] == [] and r["parsed"][0]["name"] == "LightningDamageTakenWhenHit"
               and r["parsed"][0]["value"] == -7
               for r in engine.call("parseLines", lines=["7% less Lightning Damage taken when Hit"])["results"])

    corrected = [line for line in corrected if "Unlucky" not in line]
    engine.call("newBuild")
    engine.call("equip", slot="Belt", raw=BELT, fields=[])
    engine.call("equip", slot="Flask 1", raw=None, active=False, fields=[])
    bare = engine.call("equip", slot="Charm 1", raw=None, active=False, fields=[HIT, DOT])["stats"]
    on = engine.call("equip", slot="Charm 1", raw="\n".join(corrected), active=True, fields=[HIT, DOT])["stats"]
    assert on[HIT] == pytest.approx(bare[HIT] * 0.93, rel=1e-3)  # no flask up: still counted
    assert on[DOT] == pytest.approx(bare[DOT])  # Unlucky changes Hits only
    off = engine.call("equip", slot="Charm 1", raw="\n".join(corrected), active=False, fields=[HIT])["stats"]
    assert off[HIT] == pytest.approx(bare[HIT])  # a charm switched off adds nothing
    engine.call("equip", slot="Flask 1", raw=FLASK, active=True, fields=[])
    with_flask = engine.call("equip", slot="Charm 1", raw="\n".join(corrected), active=True, fields=[HIT])["stats"]
    assert with_flask[HIT] == pytest.approx(on[HIT], rel=1e-3)  # and not counted twice with a flask up


def test_maligaros_critical_damage_bonus_is_a_bonus_override_never_crit_chance(engine):
    """COVC13's note: PoB's mod cache holds Maligaro's two lines joined as "Critical Hit Chance set to 250" (Item.lua
    1240-1251 joins a line it can't parse with the next one), but that reading keeps leftover text, so PoB never applies
    it (Item.lua: `not modLine.extra`). Alone, "Your Critical Damage Bonus is 250%" is CritMultiplier OVERRIDE 250 (a
    x3.5 crit multiplier). Checked on the whole glove: with the line, crit chance is what it is without it."""
    parsed = engine.call("parseLines", lines=["Your Critical Damage Bonus is 250%"])["results"][0]["parsed"]
    assert [(p["name"], p["type"], p["value"]) for p in parsed] == [("CritMultiplier", "OVERRIDE", 250)]
    payload = make_payload(BuildRequest(main_skill="Sunder"))
    ctx = Context(payload)
    skeleton = enumerate_skeletons(ctx, ["Sunder"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    gloves = next(u for u in ctx.data.uniques if u["name"] == "Maligaro's Virtuosity")
    raw = ctx.corrected_raw(gloves["name"], gloves["raw"])
    bonus = "Your Critical Damage Bonus is 250%"
    lines = raw.split("\n")
    assert bonus in lines
    without = "\n".join(line for line in lines if line != bonus)
    fields = ["CritChance", "CritMultiplier"]
    alone = search.build.call("equip", slot="Gloves", raw=without, fields=fields)["stats"]
    worn = search.build.call("equip", slot="Gloves", raw=raw, fields=fields)["stats"]
    assert worn["CritMultiplier"] == pytest.approx(3.5)
    assert alone["CritMultiplier"] != pytest.approx(3.5)
    assert worn["CritChance"] == pytest.approx(alone["CritChance"], rel=1e-3) and worn["CritChance"] < 100


def test_spike_pit_is_increased_damage_taken_on_the_boss_not_more_damage(engine):
    """spike-pit: the notable's "Enemies in Jagged Ground you create take N% increased Damage" is a debuff on the enemy
    that adds to its other increased Damage taken (Shock and the like), not a separate "more" multiplier (FIX_QUEUE,
    MECH51's findings: a small overcount)."""
    from builder.ledger import entries

    spike = next(e for e in entries() if e.id == "spike-pit")
    line = spike.replace[0].split(" ", 1)[1].replace("{g1}", "8")
    parsed = engine.call("parseLines", lines=[line])["results"][0]
    assert parsed["supported"] and [(p["name"], p["type"], p["value"]) for p in parsed["parsed"]] == [
        ("EnemyModifier", "LIST", "DamageTaken")]


def test_a_share_line_counts_its_value_times_the_share_the_assumptions_set(engine):
    """The ledger's `share:` lines (COV11; spike-pit and aggravate-bleed-jagged-ground): the bridge counts a passive's
    line at value / 100 x Multiplier:LedgerShare<Name>, which the assumptions set to the share in whole percent. So the
    nested EnemyModifier line (Spike Pit's) and a plain line both scale linearly with the share: 0 adds nothing, 100
    the full value."""
    from builder.ledger import entries

    spike = next(e for e in entries() if e.id == "spike-pit")
    assert list(spike.replace) == ["share:JaggedGround Nearby Enemies have {g1}% increased Damage taken"]
    payload = make_payload(BuildRequest(main_skill="Spark"))
    skeleton = enumerate_skeletons(Context(payload), ["Spark"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    node = 3698  # Spike Pit, with its path (the search's own ledger correction, spike-pit's share line, rides along)
    build.call("allocate", add=[node], fields=[])
    lines = ["share:JaggedGround Nearby Enemies have 40% increased Damage taken", "share:JaggedGround 20% more Damage",
             "share:JaggedGround 30% increased maximum Life"]
    applied = build.call("applyPassiveCorrections", nodes=[{"id": node, "entry": "test-share", "lines": lines}])
    assert applied["applied"] == 3 and not applied["failed"]
    base = list(search.assumptions.mods)

    def stats(percent):
        share = [{"kind": "Raw", "name": "Multiplier:LedgerShareJaggedGround", "type": "BASE", "value": percent}]
        return build.call("setAssumptions", mods=base + share, fields=["TotalDPS", "Life"])["stats"]

    def dps(percent):  # hit DPS (TotalDPS): an Ignite's chance grows with the hit (Flammability), so not linear
        return stats(percent)["TotalDPS"]

    life = [stats(p)["Life"] for p in (0, 50, 100)]  # a plain INC line: exactly linear in the share
    assert life[2] > life[0] and life[1] == pytest.approx((life[0] + life[2]) / 2, abs=1)
    none, half, full = dps(0), dps(50), dps(100)
    assert build.call("setAssumptions", mods=base, fields=["TotalDPS"])["stats"]["TotalDPS"] == pytest.approx(none)
    assert full > none * 1.2  # at least the 20% more, and some of the 40% increased taken
    # (1 + a + c s) x (1 + 0.2 s) / (1 + a), c = 0.4 + Spike Pit's own 0.1: at s = 1/2 the product of the halves
    assert half == pytest.approx(none * ((full / none / 1.2 - 1) / 2 + 1) * 1.1, rel=5e-3)  # (Shock's size follows the hit too)


def test_the_coverage_audit_reads_a_two_line_stat_description_as_one_line_as_pob_does_on_an_item(engine):
    """auditCoverage (COV11; PLANS1's catalog finding): a mod whose stat description spans two lines is one line PoB
    counts on an item (Item.lua joins a line it can't parse with the next), so the audit records the joined line, not
    two halves it can't parse. Waveshaper's (UniqueManaRegenAppliesToRecharge1) is the example."""
    engine.call("newBuild")
    affixes = engine.call("auditCoverage")["affixes"]
    wave = [a for a in affixes if a["id"] == "UniqueManaRegenAppliesToRecharge1"]
    assert [(a["line"], a["supported"]) for a in wave] == [
        ("Increases and Reductions to Mana Regeneration Rate also apply to Energy Shield Recharge Rate", True)]
    assert "ManaRegenAppliesToEnergyShieldRecharge" in wave[0]["names"]
    # Rune lines aren't joined: PoB rebuilds them from the rune's name and reads neither half of Jiquani's Thesis's
    # boots pair, which items-two-line.yaml's whole-sentence stand-in needs listed as unparsed (COV9)
    boots = [a for a in affixes if a["pool"] == "Runes" and a["id"] == "Jiquani's Thesis" and a.get("slot") == "boots"]
    assert [a["line"].strip() for a in boots if not a.get("bonded")][:2] == [
        "Increases and Reductions to Movement Speed also", "apply to Energy Shield Recharge Rate"]
    assert not any(a["supported"] for a in boots[:2])


def test_a_share_line_raises_a_maximum_resistance_by_its_share_only(engine):
    """Adaptive Skin's per-element lines (COV12, AUDIT25 A25-42): 'share:BossHitsFire +5% to Maximum Fire Resistance'
    counts 5 x the share of the fight the boss has hit you with Fire Recently (Multiplier:LedgerShareBossHitsFire, set by
    the assumptions); Cold stays at its maximum without its share."""
    payload = make_payload(BuildRequest(main_skill="Spark"))
    skeleton = enumerate_skeletons(Context(payload), ["Spark"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    node = 3698
    build.call("allocate", add=[node], fields=[])
    lines = ["share:BossHitsFire +5% to Maximum Fire Resistance", "share:BossHitsCold +5% to Maximum Cold Resistance",
             "+200% to all Elemental Resistances"]  # (over the cap, so the final resistance is the maximum)
    applied = build.call("applyPassiveCorrections", nodes=[{"id": node, "entry": "test-if", "lines": lines}])
    assert applied["applied"] == 3 and not applied["failed"]
    base = [m for m in search.assumptions.mods
            if not (m.get("kind") == "Condition" and m["name"].startswith("BossHits"))
            and not m.get("name", "").startswith("Multiplier:LedgerShareBossHits")]

    def res(**shares):
        mods = base + [{"kind": "Raw", "name": f"Multiplier:LedgerShare{name}", "type": "BASE", "value": percent}
                       for name, percent in shares.items()]
        return build.call("setAssumptions", mods=mods, fields=["FireResist", "ColdResist"])["stats"]

    off, fire = res(), res(BossHitsFire=100)
    assert fire["FireResist"] == off["FireResist"] + 5 and fire["ColdResist"] == off["ColdResist"]
    assert off["FireResist"] < res(BossHitsFire=44)["FireResist"] <= off["FireResist"] + 2.2  # 5 x 0.44 at most


def test_kurgals_gaze_socketed_in_a_helmet_counts_through_pobs_own_flag(engine):
    """Kurgal's Gaze (COV12; COV11's handover): "Increases and Reductions to Life Regeneration Rate also apply to Mana
    Regeneration Rate". PoB's rune data (Data/ModRunes.lua) holds the helmet line whole, not as the two halves the stat
    description prints, so a socketed one reaches ModParser's LifeRegenAppliesToManaRegen (CalcDefence copies each Life
    Regeneration Rate INC to Mana): no stand-in needed, unlike Jiquani's Thesis."""
    payload = make_payload(BuildRequest(main_skill="Spark"))
    skeleton = enumerate_skeletons(Context(payload), ["Spark"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build

    def mana_regen(lines):
        build.call("equip", slot="Helmet", raw=chr(10).join(lines), fields=[])
        return build.call("stats", fields=["ManaRegen"])["ManaRegen"]

    head = ["Rarity: Rare", "Test Gaze", "Felt Cap"]
    plain = mana_regen(head + ["Implicits: 0", "50% increased Life Regeneration rate"])
    flat = mana_regen(head + ["Implicits: 0", "50% increased Life Regeneration rate", "50% increased Mana Regeneration Rate"])
    socketed = mana_regen(head + ["Sockets: S", "Rune: Kurgal's Gaze", "Implicits: 0", "50% increased Life Regeneration rate"])
    assert flat > plain and socketed == pytest.approx(flat, rel=1e-3)


def test_a_bonded_stand_in_counts_only_under_pobs_own_bonded_switch(engine):
    """COV13: the ledger's stand-in for a Bonded line PoB can't parse ("LedgerBonded:<type> <line>", ledger/items.py)
    becomes a Bonded rune line in the bridge, so PoB's own switch decides: here an item line that lets socketed Idols use
    their Bonded lines (the Fox Idol's) turns on an Idol's stand-in, not a Rune's; with no Bonded source neither counts.
    bridge.sourceLines lists the stand-in's line only while it applies, without "Bonded: ", and never the marker."""
    from builder.ledger.items import BONDED, _bases

    payload = make_payload(BuildRequest(main_skill="Spark"))
    skeleton = enumerate_skeletons(Context(payload), ["Spark"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    coat = next(n for n, b in _bases().items() if b["type"] == "Body Armour" and not b.get("hidden"))
    head = ["Rarity: Rare", "Test Coat", coat, "Implicits: 0", "+10 to Strength"]
    idols = "Idols socketed in this item gain the benefits of their Bonded modifiers"

    def life(*lines):
        build.call("equip", slot="Body Armour", raw=chr(10).join(head + list(lines)), fields=[])
        texts = [e["text"] for e in build.call("sourceLines")["lines"] if e["source"] == "item:Body Armour"]
        return build.call("stats", fields=["Life"])["Life"], texts

    plain, _ = life()
    flagged, _ = life(idols)
    idol_off, texts_off = life("{custom}" + BONDED + "Idol +50 to maximum Life")
    idol_on, texts_on = life(idols, "{custom}" + BONDED + "Idol +50 to maximum Life")
    rune_on, _ = life(idols, "{custom}" + BONDED + "Rune +50 to maximum Life")
    assert idol_off == plain and rune_on == flagged and idol_on > flagged
    assert not any(t.startswith(("LedgerBonded:", "Bonded:")) or t == "+50 to maximum Life" for t in texts_on + texts_off)
    # a real socketed Idol's Bonded line: in sourceLines, without "Bonded: ", only while PoB applies it
    head[3:3] = ["Sockets: S", "Rune: Idol of Eramir"]
    charms = "15% chance for Charms you use to not consume Charges"
    assert charms in life(idols)[1] and charms not in life()[1]


def test_a_bonded_share_stand_in_counts_at_its_share(engine):
    """COV32: a Bonded stand-in may be a `share:` line ("LedgerBonded:<type> LedgerShare:<Name> <line>": Warding Rune
    of Hollowing's full-Ward line, Armature's missing-Ward one): it counts its line x the share, under PoB's own Bonded
    switch, as installLedgerShare counts an item's share line."""
    from builder.ledger.items import BONDED, _bases
    from builder.search import models

    engine.call("newBuild")
    engine.call("setLevel", level=90)
    coat = next(n for n, b in _bases().items() if b["type"] == "Body Armour" and not b.get("hidden"))
    idols = "Idols socketed in this item gain the benefits of their Bonded modifiers"
    stand_in = "{custom}" + BONDED + "Idol LedgerShare:ProbeShare +50 to maximum Life"

    def life(share, *lines):
        engine.call("setAssumptions", mods=[models.raw_mod("Multiplier:LedgerShareProbeShare", "BASE", share)])
        engine.call("equip", slot="Body Armour", fields=[],
                    raw="\n".join(["Rarity: Rare", "Test Coat", coat, "Implicits: 0", *lines]))
        return engine.call("stats", fields=["Life"])["Life"]

    flagged = life(100, idols)
    assert life(100, stand_in) == life(100) < flagged + 1  # (no Bonded source: nothing)
    full, half, none = life(100, idols, stand_in), life(50, idols, stand_in), life(0, idols, stand_in)
    assert none == flagged and full > half > none
    assert half - none == pytest.approx((full - none) / 2, rel=0.05)


def test_cov16_chance_lines_parse_to_the_ailment_chance_pob_reads(engine):
    """COV16: "N% increased chance to Poison / to inflict Bleeding" left the word "chance" over in PoB's parse, so PoB
    applied nothing; the ledger's lines are PoB's INC PoisonChance / BleedChance (CalcOffence's base * (1 + inc)).
    Carved Cunning's Full Life line is recorded at 0 (PoB can't parse the flag with the Full Life tag)."""
    from builder.ledger import translate

    lines = {"15% increased chance to Poison": ("PoisonChance", "INC", 15),
             "25% increased chance to inflict Bleeding": ("BleedChance", "INC", 25)}
    for line, want in lines.items():
        out = translate(line)[1]
        parsed = engine.call("parseLines", lines=out)["results"][0]
        assert parsed["supported"] and [(p["name"], p["type"], p["value"]) for p in parsed["parsed"]] == [want], line
    # PoB doesn't parse its flag and the Full Life tag together: recorded, counted 0
    assert translate("Enemies which are on Full Life cannot Evade your Hits")[1] == []


def test_cov16_reworded_lines_parse_to_the_stat_the_game_names(engine):
    """COV16: the game's newer wordings PoB doesn't know, each as PoB's older wording of the same stat
    (non_curse_aura_effect_+%, mace / unarmed melee attack speed, local_life_and_mana_gain_per_target)."""
    from builder.ledger import translate

    want = {"12% increased Magnitudes of Non-Curse Auras from your Skills": ["AuraEffect"],
            "7% increased Attack Speed with Maces or Sceptres": ["Speed"],
            "14% increased Critical Hit Chance with Maces or Sceptres": ["CritChance"],
            "+9% to Critical Damage Bonus with Maces or Sceptres": ["CritMultiplier"],
            "7% increased Unarmed Attack Speed with Melee Skills": ["Speed"],
            "Grants 10 Life and Mana per Enemy Hit": ["LifeOnHit", "ManaOnHit"],
            "Attacks have added Chaos damage equal to 2% of maximum Life": ["ChaosMin", "ChaosMax"]}
    for line, names in want.items():
        out = translate(line)[1]
        results = engine.call("parseLines", lines=out)["results"]
        assert all(r["supported"] for r in results), (line, results)
        assert [p["name"] for r in results for p in r["parsed"]] == names, (line, results)


def test_a_share_stand_in_on_an_item_counts_its_value_times_the_share(engine):
    """COV17: a `share:` line on an item ("LedgerShare:<Name> <line>", ledger.item_lines) counts at value / 100 x
    Multiplier:LedgerShare<Name> (bridge installLedgerShare), as a passive's does; without the multiplier it adds
    nothing, and bridge.sourceLines never lists the stand-in. Seed of Cataclysm's line goes through it."""
    from builder.ledger import item_lines, translate
    from builder.ledger.items import _bases

    seed = "5% reduced Critical Spell Damage Bonus per Critical Hit you've dealt with Spells Recently"
    assert item_lines(translate(seed)[1]) == ["LedgerShare:SpellCritsRecently 5% reduced Critical Spell Damage Bonus"]
    payload = make_payload(BuildRequest(main_skill="Spark"))
    skeleton = enumerate_skeletons(Context(payload), ["Spark"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    coat = next(n for n, b in _bases().items() if b["type"] == "Body Armour" and not b.get("hidden"))
    head = ["Rarity: Rare", "Test Coat", coat, "Implicits: 0", "+10 to Strength"]
    base = list(search.assumptions.mods)

    def life(percent, *lines):
        build.call("equip", slot="Body Armour", raw=chr(10).join(head + list(lines)), fields=[])
        share = [{"kind": "Raw", "name": "Multiplier:LedgerShareTestShare", "type": "BASE", "value": percent}]
        stats = build.call("setAssumptions", mods=base + share, fields=["Life"])["stats"]
        texts = [e["text"] for e in build.call("sourceLines")["lines"] if e["source"] == "item:Body Armour"]
        return stats["Life"], texts

    plain, _ = life(0)
    line = "{custom}LedgerShare:TestShare +100 to maximum Life"
    off, texts = life(0, line)
    half, _ = life(50, line)
    twice, _ = life(200, line)
    assert off == plain and half > plain and twice - plain == pytest.approx(4 * (half - plain), rel=0.02)
    assert not any("LedgerShare" in t or t == "+100 to maximum Life" for t in texts)
    ranged, _ = life(100, "{custom}LedgerShare:TestShare +(100-100) to maximum Life")
    assert ranged - plain == pytest.approx(2 * (half - plain), rel=0.02)


def test_the_per_rune_random_element_line_counts_socketed_runes_only(engine):
    """COV42 (MECHB37 item 1): Emergent Possibility in gloves, "Gain 1% of Damage as Extra Damage of a random Element
    per" / "Rune Socketed in Equipped Items" (items-cov42.yaml + the bridge's RunesInEquipment count). PoB alone parses
    neither half (0); with the ledger the line counts. The count (read exactly through a Life line the same hook reads;
    a low-level Spark's DPS moves in rounded steps, probes POE2T3869-3872) is each socketed Rune in the equipment, the
    carrier included, and no Soul Core or Idol (PoB's NonIdolAugmentsInEquipment would count Soul Cores too)."""
    from builder.ledger.items import _bases, corrected_item

    payload = make_payload(BuildRequest(main_skill="Spark"))
    skeleton = enumerate_skeletons(Context(payload), ["Spark"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    bases = _bases()
    glove = next(n for n, b in bases.items() if b["type"] == "Gloves" and not b.get("hidden"))
    coat = next(n for n, b in bases.items() if b["type"] == "Body Armour" and not b.get("hidden"))

    def item(name, base, runes, *lines):
        sockets = ["Sockets: " + " ".join("S" for _ in runes), *("Rune: " + r for r in runes)] if runes else []
        return "\n".join(["Rarity: Rare", name, base, *sockets, "Implicits: 0", "+10 to Strength", *lines])

    def stats(gloves, coat_runes, *lines, correct=True):
        g, c = item("Test Gloves", glove, gloves, *lines), item("Test Coat", coat, coat_runes)
        if correct:
            g, c = corrected_item(g), corrected_item(c)
        build.call("equip", slot="Gloves", raw=g, fields=[])
        build.call("equip", slot="Body Armour", raw=c, fields=[])
        return build.call("stats", fields=["TotalDPS", "Life"])

    ep, rune = ["Emergent Possibility"], "Desert Rune"
    plain = stats([], [])["TotalDPS"]
    assert stats(ep, [], correct=False)["TotalDPS"] == pytest.approx(plain, rel=1e-9)  # PoB alone: nothing
    assert stats(ep, [])["TotalDPS"] > plain
    per_rune = "+10 to maximum Life per Rune Socketed in Equipped Items"
    life = {n: stats(ep, [rune] * n, per_rune)["Life"] for n in (0, 2)}
    none = stats([], [], per_rune)["Life"]
    assert life[0] > none and life[2] - life[0] == pytest.approx(2 * (life[0] - none), abs=1)  # 1 Rune, then 3
    cores = ["Hayoxi's Soul Core of Heatproofing", "Idol of Eramir"]
    assert stats(ep, cores, per_rune)["Life"] == life[0]  # a Soul Core and an Idol don't count


def test_source_lines_give_only_the_equipped_variants_lines(engine):
    """COV42 (MECHB37 item 2): a unique's text holds every variant's lines; bridge.sourceLines gives the models only the
    lines PoB applies (Item:CheckModLineVariant). Probe POE2T3867: before, Lioneye's Glare on its current variant gave
    both its old "Repeat +1 time" and its current "Repeat +2 times"."""
    payload = make_payload(BuildRequest(main_skill="Lightning Arrow"))
    skeleton = enumerate_skeletons(Context(payload), ["Lightning Arrow"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    repeat = "Repeatable Attacks with this Bow Repeat +{} if no enemies are in your Presence"

    def repeats(variant):
        raw = "\n".join(["Rarity: UNIQUE", "Lioneye's Glare", "Heavy Bow", "Variant: Pre 0.4.0", "Variant: Current",
                         f"Selected Variant: {variant}", "Implicits: 0", "(150-240)% increased Physical Damage",
                         "{variant:1}" + repeat.format("1 time"), "{variant:2}" + repeat.format("2 times")])
        build.call("equip", slot="Weapon 1", raw=raw, fields=[])
        return [e["text"] for e in build.call("sourceLines")["lines"] if "Repeat" in e["text"]]

    assert repeats(2) == [repeat.format("2 times")]
    assert repeats(1) == [repeat.format("1 time")]


def test_the_pharisee_idols_physical_gain_counts_per_ten_percent_missing_mana(engine):
    """COV42: Idol of the Pharisee's "Gain 2% of Damage as Extra Physical Damage per ten percent missing Mana" (PoB: 0)
    becomes PoB's own "Gain 2% ..." at the share PhariseeManaTens sets (the average tens of Mana missing, Force of Will's
    count): 0 tens nothing, 5 tens (500) the same as a plain "Gain 10% of Damage as Extra Physical Damage"."""
    from builder.ledger.items import _bases, corrected_item
    from builder.search import models

    payload = make_payload(BuildRequest(main_skill="Spark"))
    skeleton = enumerate_skeletons(Context(payload), ["Spark"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    staff = next(n for n, b in _bases().items() if b["type"] == "Staff" and not b.get("hidden"))
    head = ["Rarity: Rare", "Test Staff", staff]

    def dps(lines, tens):
        build.call("setAssumptions", mods=[models.raw_mod("Multiplier:LedgerSharePhariseeManaTens", "BASE", tens)])
        build.call("equip", slot="Weapon 1", raw="\n".join(head + lines), fields=[])
        return build.call("stats", fields=["TotalDPS"])["TotalDPS"]

    idol = corrected_item("\n".join(head + ["Sockets: S", "Rune: Idol of the Pharisee", "Implicits: 0"])).split("\n")[3:]
    plain = dps(["Sockets: S", "Rune: Idol of the Pharisee", "Implicits: 0"], 500)  # PoB alone: no gain
    assert dps(idol, 0) == pytest.approx(plain, rel=1e-9)
    five = dps(idol, 500)
    flat = dps(["Sockets: S", "Rune: Idol of the Pharisee", "Implicits: 1",
                "Gain 10% of Damage as Extra Physical Damage"], 0)
    assert five > plain and five == pytest.approx(flat, rel=1e-6)
