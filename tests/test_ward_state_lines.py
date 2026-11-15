"""COV32 (PATCH_CHECK NOTES3 DEAD-1 / DEAD-2): lines that hold only while on Low / missing / full Runic Ward counted 0
(PoB's Condition:LowRunicWard / MissingRunicWard is never set; Hollowing's "full" line PoB doesn't parse). Each is now
a `share:` line at the share of the fight in that state, from MECH-C's ward_shares (ledger/entries/items-runes-cov32.yaml,
Assumptions.ward_state_share); a socketed rune's Bonded line takes its share stand-in too (builder/ledger/items.py)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from builder.ledger import translate, wrong
from builder.ledger import items as ledger_items
from builder.search import assumptions
from builder.search.assumptions import Assumptions


def test_each_ward_state_line_becomes_its_share_line():
    cases = {
        "Spell damage Penetrates 25% of enemy Elemental Resistances while on Low Runic Ward":
            "LedgerShare:RunicWardLow Spell damage Penetrates 25% of enemy Elemental Resistances",
        "25% increased Armour and Evasion Rating while on Low Runic Ward":
            "LedgerShare:RunicWardLow 25% increased Armour and Evasion Rating",
        "5% increased Attack Speed while missing Runic Ward": "LedgerShare:RunicWardMissing 5% increased Attack Speed",
        "(10-15)% increased Attack Speed while missing Runic Ward":
            "LedgerShare:RunicWardMissing (10-15)% increased Attack Speed",
        "Gain (42-52)% of Damage as Extra Cold Damage while you are missing Runic Ward":
            "LedgerShare:RunicWardMissing Gain (42-52)% of Damage as Extra Cold Damage",
        "Gain 23% of Damage as Extra Chaos Damage while you are missing Runic Ward":
            "LedgerShare:RunicWardMissing Gain 23% of Damage as Extra Chaos Damage",
    }
    for line, fixed in cases.items():
        assert wrong(line)[1] == fixed, line
    entry, lines = translate("+1% to all Maximum Elemental Resistances while on full Runic Ward")
    assert lines == ["share:RunicWardFull +1% to all Maximum Elemental Resistances"]


def _base(category: str) -> str:
    from builder.search.rares import rune_categories

    return next(n for n, b in ledger_items._bases().items() if not b.get("hidden") and category in rune_categories(b))


def test_socketed_runes_carry_their_share_stand_ins_bonded_ones_too():
    wand = ledger_items.corrected_item("\n".join(["Rarity: Rare", "Test Wand", _base("wand"), "Item Level: 80",
                                                  "Sockets: S", "Rune: Warding Rune of Desperation"]))
    assert ledger_items.ADDED + "LedgerShare:RunicWardLow Spell damage Penetrates 25% of enemy Elemental Resistances" \
        in wand.split("\n")
    assert ledger_items.SWITCHED_OFF + "Spell damage Penetrates 25% of enemy Elemental Resistances while on Low " \
        "Runic Ward" in wand.split("\n")
    # (Bonded: Armature's line PoB parses under a condition nothing sets, so the stand-in goes beside it; Hollowing's
    # PoB can't parse; Bodyguards' "Unlucky" share stand-in, COV22's, was dropped on the rune before)
    for rune, category, stand_in in (
            ("Warding Rune of Armature", "weapon", "LedgerShare:RunicWardMissing 5% increased Attack Speed"),
            ("Warding Rune of Hollowing", "caster", "LedgerShare:RunicWardFull +1% to all Maximum Elemental Resistances"),
            ("Warding Rune of Bodyguards", "sceptre", "LedgerShare:RunicWardDamaged 4% less Damage taken from Hits")):
        text = ledger_items.corrected_item("\n".join(["Rarity: Rare", "Test", _base(category), "Item Level: 80",
                                                      "Sockets: S", "Rune: " + rune]))
        assert any(l.startswith(ledger_items.ADDED + ledger_items.BONDED) and l.endswith(" " + stand_in)
                   for l in text.split("\n")), (rune, text)


def test_the_share_comes_from_mech_cs_ward_spending(monkeypatch):
    ward = {"Ward": 400.0}
    me = SimpleNamespace(build=SimpleNamespace(call=lambda name, **kw: dict(ward)))
    monkeypatch.delattr(assumptions, "ward_shares", raising=False)
    share, how = Assumptions.ward_state_share(me, "low")
    assert share == 0 and "not counted yet" in how  # (before MECH-C's function lands: 0 as before)
    monkeypatch.setattr(assumptions, "ward_shares", lambda build: (0.2, 0.6, "spends"), raising=False)
    assert Assumptions.ward_state_share(me, "low")[0] == 0.2
    assert Assumptions.ward_state_share(me, "missing")[0] == 0.6
    assert Assumptions.ward_state_share(me, "full")[0] == pytest.approx(0.4)
    monkeypatch.setattr(assumptions, "ward_shares", lambda build: (0.0, 0.0, "nothing spends Ward"), raising=False)
    assert Assumptions.ward_state_share(me, "low")[0] == 0 and Assumptions.ward_state_share(me, "full")[0] == 1
    ward["Ward"] = 0.0  # (no pool: every state 0, even if MECH-C's shares said otherwise; the other reading named)
    monkeypatch.setattr(assumptions, "ward_shares", lambda build: (1.0, 1.0, "x"), raising=False)
    for state in ("low", "missing", "full"):
        share, how = Assumptions.ward_state_share(me, state)
        assert share == 0 and "no Runic Ward pool" in how and "UNVERIFIED" in how


@pytest.fixture(scope="module")
def engine():
    import faulthandler

    from builder.pob.engine import PobEngine

    enabled = faulthandler.is_enabled()
    faulthandler.disable()  # LuaJIT's Lua errors are Windows exceptions PoB's pcall catches
    yield PobEngine()
    if enabled:
        faulthandler.enable()


def test_a_real_build_that_spends_its_ward_counts_the_missing_ward_lines(engine):
    """COV32 after train 56 (MECH-C's ward_shares on main; the manager, 15 Nov 05:37): MECHB13's delivered 2b75b
    (Explosive Transmutation, whose explosions cost Runic Ward), given a Ward pool and an Alloy ring line "Gain 25% of
    Damage as Extra Fire Damage while you are missing Runic Ward": the explosions spend the Ward, so RunicWardMissing
    switches on at MECH-C's share and the line adds damage; with no pool every state stays 0."""
    import json
    from pathlib import Path

    from builder.pob import codes
    from builder.request.schema import BuildRequest
    from builder.search.context import Context, make_payload
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    row = json.loads((Path(__file__).parents[1] / "benchmarks/results/gate4-20261112-152004.json")
                     .read_text(encoding="utf-8"))["rows"][0]
    request = BuildRequest.model_validate(row["request"])
    payload = make_payload(request)
    skeleton = enumerate_skeletons(Context(payload), [request.main_skill])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    build.call("loadXml", xml=codes.decode(row["code"]))
    line = "Gain 25% of Damage as Extra Fire Damage while you are missing Runic Ward"

    def ring(*lines):
        raw = "\n".join(["Rarity: Rare", "Ward Ring", "Gold Ring", "Item Level: 80", "Implicits: 0", *lines])
        build.call("equip", slot="Ring 2", raw=ledger_items.corrected_item(raw), fields=[])
        for _ in range(2):  # (ward_shares reads the Ward plan of the last pass: a second pass sees this item's)
            search.assumptions.apply()
        return build.call("stats", fields=["FullDPS", "Ward"]), dict(search.assumptions.ledger_shares)

    pooled, shares = ring("+400 to maximum Runic Ward", line)
    low, missing, how = assumptions.ward_shares(build)
    print("pool", pooled, "shares", shares, "ward", (low, missing, how), build.ward_sustain)
    assert pooled["Ward"] > 0 and missing > 0, how
    assert shares.get("RunicWardMissing") == round(100 * missing)
    assert any(n["label"].startswith("The share of the fight missing Runic Ward") for n in search.assumptions.active)
    without, _ = ring("+400 to maximum Runic Ward")
    assert pooled["FullDPS"] > without["FullDPS"]
    # (no pool: the line counts 0, the other reading named in the note)
    bare, shares = ring(line)
    if bare["Ward"] <= 0:
        assert "RunicWardMissing" not in shares
        note = next(n for n in search.assumptions.active if n["label"].startswith("The share of the fight missing"))
        assert "no Runic Ward pool" in note["basis"]
