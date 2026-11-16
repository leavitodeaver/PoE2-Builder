"""Every ledger replacement line through PoB's own parser: a line PoB can't parse would do nothing
(or stop an assumption from installing). Boots one PoB engine."""

from __future__ import annotations

import faulthandler
import re

import pytest

from builder.ledger import SYNTAX, entries


@pytest.fixture(scope="module")
def engine():
    from builder.pob.engine import PobEngine

    enabled = faulthandler.is_enabled()
    faulthandler.disable()  # LuaJIT's Lua errors are Windows exceptions PoB's pcall catches
    yield PobEngine()
    if enabled:
        faulthandler.enable()


def replacement_lines() -> list[tuple[str, str]]:
    found = []
    for entry in entries():
        if entry.kind not in ("pob_gap", "pob_wrong", "gem_stat"):
            continue
        for template in entry.replace:
            m = SYNTAX.match(template)
            if m and m.group(1) in ("flag", "mult"):
                continue
            text = m.group(3) if m else template
            # an item line's own tags ("{tags:LedgerSkill_<id>}", COV30): PoB's item parser takes them off before it
            # parses the line (Item.lua 982), so the parse is of the rest
            text = re.sub(r"^(?:\{(?:tags|range):[^{}]*\})+", "", text)
            # numbers from the matched line: any value parses the same, so fill templates with 10
            found.append((entry.id, re.sub(r"\{[^{}]+\}", "10", text)))
    return found


def test_every_replacement_line_parses(engine):
    lines = replacement_lines()
    results = engine.call("parseLines", lines=[text for _, text in lines])["results"]
    failed = [(entry, r["line"], r.get("extra")) for (entry, _), r in zip(lines, results) if not r["supported"]]
    assert not failed, failed


def test_the_electrocution_lines_pob_misspells_count_only_through_their_ledger_replacement(engine):
    """COV27 (AUDIT33 A33-2): PoB parses "Lightning damage from Hits Contributes to Electrocution Buildup" (Kitoko's
    Current) into LightningCanElectrocution, which no calculation reads (canDoAilment asks for ...CanElectrocute): the
    line builds no Electrocute. The ledger's pob_wrong replacement (PoB's own "Electrocute" wording) does."""
    from builder.ledger import wrong

    line = "Lightning damage from Hits Contributes to Electrocution Buildup"
    entry, fixed = wrong(line)
    assert entry.id == "wrong-lightning-hits-electrocution-buildup"

    def buildup(lines):
        engine.call("newBuild")
        engine.call("setLevel", level=90)
        engine.call("addSkillGroup", text="Spark 20/0 1", main=True)
        if lines:
            engine.call("equip", slot="Ring 1", fields=[],
                        raw="Rarity: Rare\nProbe Ring\nAmethyst Ring\n--------\n" + "\n".join(lines))
        return engine.call("stats", fields=["ElectrocuteBuildupAvg"]).get("ElectrocuteBuildupAvg") or 0

    plain = buildup([])
    assert buildup([line]) == plain  # PoB's own parse: nothing
    assert buildup([fixed]) > plain  # the replacement: Lightning hits build Electrocute
    assert wrong("All damage with this Weapon causes Electrocution buildup")[1] == \
        "All damage with this Weapon causes Electrocute buildup"



def test_a_share_line_with_a_percent_stat_counts_its_exact_share(engine):
    """COV31 (PATCH_CHECK G4-1): The Lethal Draw's line, counted at LethalDrawCharged's share, adds what PoB's own parse
    adds at share 100 and half at 50. PoB rounds a PercentStat tag's product up (ModStore m_ceil), so the share's
    Multiplier tag must come first (bridge installLedgerShare): after it, 1/100 of the value rounded up to 1 and share
    100 gave 100 added damage for PoB's 92 (probes POE2T2515 / POE2T2516, scratch/cov31/probe_lethal*.py)."""
    from builder.ledger import wrong
    from builder.search import models

    line = ("Bow Attacks consume 10% of your maximum Life Flask Charges if possible to deal added Physical damage "
            "equal to (5-10)% of Flask's Life Recovery amount")
    engine.call("newBuild")
    engine.call("setLevel", level=90)
    engine.call("equip", slot="Weapon 1", fields=[],
                raw="Rarity: Rare\nProbe Bow\nRecurve Bow\nItem Level: 80\n--------\nAdds 20 to 40 Physical Damage")
    engine.call("equip", slot="Flask 1", fields=[], raw="Rarity: Normal\nUltimate Life Flask")
    engine.call("addSkillGroup", text="Lightning Arrow 20/0 1", main=True)

    def dps(text=None, share=None):
        engine.call("equip", slot="Weapon 2", fields=[], raw=None if text is None else
                    "Rarity: Unique\nThe Lethal Draw\nSacral Quiver\n--------\n{range:1}" + text)
        engine.call("setAssumptions", mods=[] if share is None else
                    [models.raw_mod("Multiplier:LedgerShareLethalDrawCharged", "BASE", share)])
        return engine.call("stats", fields=["TotalDPS"])["TotalDPS"]  # (linear in the added damage here: probe POE2T2515)

    none = dps()
    own = dps(line) - none  # PoB's own parse: on every Bow Attack
    fixed = wrong(line)[1]
    assert own > 0
    assert abs((dps(fixed, 100) - none) - own) < 1e-6 * own
    assert abs((dps(fixed, 50) - none) - own / 2) <= 0.02 * own
    assert abs(dps(fixed, 0) - none) < 1e-9


def test_olrovasaras_power_line_adds_maximum_lightning_to_its_own_attacks(engine):
    """COV35 (PATCH_CHECK G3-3): Olrovasara's "maximum added Lightning damage equal to the enemy's Power" (PoB parses
    none of it) counts through its ledger stand-in at EnemyPowerHit20s's count: at 2000 (a lone boss's Power 20) the
    same DPS as the weapon's own "Adds 1 to 70" raised to "Adds 1 to 90", and nothing for a Spell (probe POE2T2578)."""
    from builder.ledger import translate
    from builder.search import models

    second = "the enemy's Power for 20 seconds, up to a total of 500"
    stand_in = translate(second)[1][0].replace("share:EnemyPowerHit20s ", "LedgerShare:EnemyPowerHit20s ")
    head = "Rarity: Unique\nOlrovasara\nTorment Club\nItem Level: 80\n--------\n"

    def dps(adds_max, extra=(), share=None, skill="Boneshatter 20/0 1"):
        engine.call("newBuild")
        engine.call("setLevel", level=90)
        engine.call("equip", slot="Weapon 1", fields=[],
                    raw=head + "\n".join([f"Adds 1 to {adds_max} Lightning Damage", *extra]))
        engine.call("addSkillGroup", text=skill, main=True)
        engine.call("setAssumptions", mods=[] if share is None else
                    [models.raw_mod("Multiplier:LedgerShareEnemyPowerHit20s", "BASE", share)])
        return engine.call("stats", fields=["TotalDPS"])["TotalDPS"]

    own = ["On Hitting an enemy, gains maximum added Lightning damage equal to", second]
    plain = dps(70)
    assert dps(70, own) == plain  # PoB's own parse: nothing
    assert abs(dps(70, own + [stand_in], 2000) - dps(90)) < 1e-6 * plain
    assert dps(70, own + [stand_in], 0) == plain
    assert dps(70, own + [stand_in], 2000, skill="Spark 20/0 1") == dps(70, skill="Spark 20/0 1")


RANGED_ITEM_LINES = [  # (COV37) each entry matched single rolls only, so a unique's or rune's ranged text slipped past
    "(20-30)% increased chance to inflict Bleeding", "(20-30)% increased chance to Poison",
    "(10-16)% chance for Mace Slam Skills you use yourself to cause an additional Aftershock",
    "(5-10)% chance for Slam Skills you use yourself to cause an additional Aftershock",
    "(30-40)% increased Magnitude of Elemental Ailments you inflict with Spells",
    "Minions have (35-45)% increased Magnitude of Damaging Ailments",
    "(10-15)% increased Magnitudes of Non-Curse Auras from your Skills",
    "(6-8)% increased Attack Speed with Maces or Sceptres", "(12-16)% increased Critical Hit Chance with Maces or Sceptres",
    "+(8-10)% to Critical Damage Bonus with Maces or Sceptres", "(6-8)% increased Unarmed Attack Speed with Melee Skills",
]


def test_ranged_item_lines_get_their_entrys_replacement_and_pob_reads_it(engine):
    """COV37: the entries now match a rolled range ("(20-30)%"), and the replacement keeps the range, which PoB's item
    parser rolls (Item.lua 2731, applyRange on every mod line) before ModParser reads it."""
    from builder.ledger import item_lines, translate

    added = []
    for line in RANGED_ITEM_LINES:
        found = translate(line)
        assert found is not None and found[1], line
        added += item_lines(found[1])
    engine.call("newBuild")
    engine.call("equip", slot="Ring 1", fields=[],
                raw="Rarity: Rare\nProbe Ring\nAmethyst Ring\n--------\n" + "\n".join(added))
    lines = engine.call("itemMods")["Ring 1"]["lines"]
    unread = [l["line"] for l in lines if not l["supported"]]
    assert len(lines) == len(added) and not unread, unread
