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
