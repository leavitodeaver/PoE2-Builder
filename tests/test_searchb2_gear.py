"""SEARCHB2 (SEARCH-B lane, SEARCHB1's G5): resistances moved off an offence slot (Gear.resist_swap_round): the
resistance lines it reads, and build_rare's `required` taking several rules."""
from __future__ import annotations

from builder.search.gear import explicit_lines, is_rare_text, is_unique_text, requires, resist_kinds, resist_lines, resist_rule
from builder.search.rares import Affix

GLOVES = """Rarity: RARE
Gale Grip
Advanced Gauntlets
Item Level: 82
Implicits: 1
+20% to Cold Resistance
{rune}+12% to Fire Resistance
+35% to Fire Resistance
+28% to Lightning Resistance
+13% to Chaos Resistance
+12% to Fire and Cold Resistances
+8% to all Elemental Resistances
+2% to Maximum Fire Resistance
Adds 5 to 9 Physical Damage to Attacks
Damage Penetrates 10% Lightning Resistance"""


def test_resist_kinds_reads_single_hybrid_and_all_elemental_lines_only():
    assert resist_kinds("+35% to Fire Resistance") == {"Fire"}
    assert resist_kinds("+12% to Fire and Cold Resistances") == {"Fire", "Cold"}
    assert resist_kinds("+8% to all Elemental Resistances") == {"Fire", "Cold", "Lightning"}
    assert resist_kinds("+13% to Chaos Resistance") == {"Chaos"}
    assert not resist_kinds("+2% to Maximum Fire Resistance")
    assert not resist_kinds("Damage Penetrates 10% Lightning Resistance")
    assert not resist_kinds("Adds 5 to 9 Physical Damage to Attacks")


def test_resist_lines_skip_the_implicit_and_rune_lines():
    """The implicit (+20% Cold) and the rune's line stay: resist_swap_round moves only the affixes a rebuild can drop."""
    assert explicit_lines(GLOVES)[0] == "+35% to Fire Resistance"
    assert resist_lines(GLOVES) == [{"Fire"}, {"Lightning"}, {"Chaos"}, {"Fire", "Cold"}, {"Fire", "Cold", "Lightning"}]
    assert is_rare_text(GLOVES) and not is_unique_text(GLOVES)


def test_a_resist_rule_takes_an_affix_covering_its_resistances():
    def affix(line):
        return Affix(id=line, group=line, type="Suffix", tier=1, lines=(line,))

    fire = resist_rule(frozenset({"Fire"}))
    assert fire(affix("+40% to Fire Resistance")) and fire(affix("+10% to all Elemental Resistances"))
    assert not fire(affix("+40% to Cold Resistance"))
    assert not resist_rule(frozenset({"Fire", "Cold"}))(affix("+40% to Fire Resistance"))
    assert requires(None) == [] and requires(fire) == [fire] and requires([fire, fire]) == [fire, fire]


def test_a_resist_swap_is_kept_only_when_it_scores_higher_with_the_floors_met(monkeypatch):
    """POE2T6234 (2b75d): the gloves' Lightning / Chaos / Fire onto Ring 2, the gloves rebuilt for damage, 25,072 ->
    33,595 with every floor met. Here: the boots pair loses (put back), the ring pair gains (kept, the round ends for
    the gloves); the boots already hold Lightning, so the gloves' other kinds are asked of them first."""
    from builder.search import gear as gear_module
    from builder.search.gear import Gear

    class Objective:
        def deficits(self, stats):
            return []

    class Build:
        def __init__(self):
            self.score, self.stats, self.objective, self.locked_slots = 10.0, {}, Objective(), set()
            self.items = {"Gloves": GLOVES, "Boots": "Rarity: RARE\nB\nBoots\nImplicits: 0\n+30% to Lightning Resistance",
                          "Ring 2": "Rarity: RARE\nR\nRing\nImplicits: 0\n+10% increased Attack Speed"}
            self.restores = 0

        def call(self, method, **kwargs):
            return {"slots": dict(self.items)}

        def snapshot(self):
            return {"score": self.score}

        def restore(self, snap):
            self.restores += 1
            self.score = snap["score"]

    build = Build()
    gear = Gear.__new__(Gear)
    gear.build, gear.slots, gear.rares = build, {"Gloves": None, "Boots": None, "Ring 2": None}, {}
    gear.restriction = lambda slot: None
    gear.settle = lambda: None
    asked = []

    def build_rare(slot, deadline, required=None):
        asked.append((slot, len(required or [])))
        if slot == "Boots" and required and len(required) > 2:
            return None  # (the boots' suffixes hold their own Lightning and two of the gloves')
        return f"rare {slot}"

    def put(slot, option):
        build.score += {"rare Boots": -0.5, "rare Ring 2": 0.4}.get(option, 0.0)

    gear.build_rare, gear.put = build_rare, put
    monkeypatch.setattr(gear_module, "RESIST_RECEIVERS", ("Boots", "Ring 2", "Gloves"))
    rows = gear.resist_swap_round(float("inf"), 4)
    first = [r for r in rows if r["from"] == "Gloves"]
    assert [r["to"] for r in first] == ["Boots", "Ring 2"] and [r["kept"] for r in first] == [False, True]
    assert gear.rares == {"Ring 2": "rare Ring 2", "Gloves": "rare Gloves"} and build.restores >= 1
    assert asked[0] == ("Boots", 1 + 5)  # (the boots' own Lightning + all five of the gloves' resistance lines)
