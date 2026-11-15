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
    ward["Ward"] = 0.0
    assert Assumptions.ward_state_share(me, "full")[0] == 0  # (no pool: not counted as full, UNVERIFIED)
