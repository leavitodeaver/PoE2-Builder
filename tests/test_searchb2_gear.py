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


def test_quick_fill_gives_each_empty_slot_its_best_option_that_beats_empty():
    """The manager's 19:08 item (SEARCH35: 4-6 slots empty on 2b75b when a full round ran out): each empty gear slot
    takes its best option by one what-if batch if it beats the slot empty; a filled, locked, flask or charm slot is left
    alone, and an option worse than empty isn't put in."""
    from builder.search.gear import Gear

    class Option:
        def __init__(self, name):
            self.name, self.raw, self.price = name, name, 0.0

    class Build:
        def __init__(self):
            self.score, self.locked_slots = 10.0, {"Belt"}
            self.equipped = {"Helmet": Option("worn helmet")}

        def worse(self, before):
            return self.score < before

    build = Build()
    gear = Gear.__new__(Gear)
    gear.build, gear.lam = build, 0.0
    gear.slots = {s: None for s in ("Helmet", "Gloves", "Boots", "Belt", "Flask 1", "Charm 1")}
    gear.settle = lambda: None
    gear.allowed = lambda slot, option: True
    options = {"Gloves": [Option("g1"), Option("g2")], "Boots": [Option("b1")]}
    gear.candidates = lambda slot: options.get(slot, [])
    values = {"g1": 10.2, "g2": 10.5, "b1": 9.0}
    gear.deltas = lambda slot, raws: [(values[r], {}) for r in raws]

    def put(slot, option):
        build.equipped[slot] = option
        build.score = values[option.name] if option else 10.0

    gear.put = put
    assert gear.quick_fill(float("inf"))
    assert build.equipped["Gloves"].name == "g2" and "Boots" not in build.equipped and "Belt" not in build.equipped
    assert build.equipped["Helmet"].name == "worn helmet"


# Shrine Sceptres (Gear.purity_trials; SEARCHB3, the manager 03:05, RESEARCH46 R46-1 / R46-2) -------------------------

def test_the_free_support_skills_are_the_four_shrine_sceptre_ones_only():
    from builder.search.gear import FREE_SUPPORT_LINE

    for line in ("Grants Skill: Level 10 Purity of Fire", "Grants Skill: Level (1-20) Purity of Ice",
                 "Grants Skill: Level 20 Purity of Lightning", "Grants Skill: Level (1-20) Impurity"):
        assert FREE_SUPPORT_LINE.search(line)
    for line in ("Grants Skill: Level 10 Malice", "Grants Skill: Level 10 Discipline", "Grants Skill: Level 10 Heart of Ice",
                 "Grants Skill: Level 10 Fulmination", "Grants Skill: Level (1-20) Skeletal Warrior Minion"):
        assert not FREE_SUPPORT_LINE.search(line)


def test_purity_trials_try_each_shrine_option_for_real_with_its_group_filled_and_keep_a_gain(monkeypatch):
    from builder.search import gear as gear_module
    from builder.search.gear import Gear

    class Option:
        def __init__(self, name, raw, unique=False, base=None):
            self.name, self.raw, self.unique, self.base, self.price, self.rare = name, raw, unique, base or name, 0.0, False

    shrine = Option("Shrine Sceptre (Purity of Fire)", "Rarity: Normal\nShrine Sceptre (Purity of Fire)\nGrants Skill: Level 10 Purity of Fire")
    omen = Option("Omen Sceptre", "Rarity: Normal\nOmen Sceptre\nGrants Skill: Level 10 Malice")
    palm = Option("Sacred Flame", "Rarity: Unique\nSacred Flame\nGrants Skill: Level (1-20) Purity of Fire", unique=True,
                  base="Shrine Sceptre")
    rare = Option("rare shrine", shrine.raw.replace("Normal", "Rare"), base=shrine.base)

    class Build:
        locked_slots: set = set()

        def __init__(self):
            self.score, self.equipped, self.filled = 10.0, {"Weapon 2": omen}, []

        def snapshot(self):
            return (self.score, dict(self.equipped))

        def restore(self, snap):
            self.score, self.equipped = snap[0], dict(snap[1])

        def groups(self):
            held = self.equipped.get("Weapon 2")
            name = gear_module.FREE_SUPPORT_LINE.search(held.raw).group(1) if held and gear_module.FREE_SUPPORT_LINE.search(held.raw) else "Malice"
            return [{"index": 3, "fromItem": True, "enabled": True, "gems": [{"name": name}]}]

    build = Build()
    gear = Gear.__new__(Gear)
    gear.build, gear.lam, gear.fit_requirements = build, 0.0, None
    gear.slots = {"Weapon 1": gear_module.SlotSpec("Wand"), "Weapon 2": gear_module.SlotSpec("Sceptre")}
    gear.settle = lambda: None
    gear.allowed = lambda slot, option: True
    gear.left_for = lambda slot: 100.0
    gear.candidates = lambda slot: [palm] if slot == "Weapon 2" else []
    gear.options = lambda slot: [shrine, omen] if slot == "Weapon 2" else []
    gear.build_rare = lambda slot, deadline, base_name=None, variant=None: rare if base_name == shrine.base else None
    tried = []
    # The bare sceptres score under the Omen; with the Purity group filled, the rare scores higher, the unique doesn't
    after_fill = {"rare shrine": 11.0, "Sacred Flame": 9.5}

    def put(slot, option):
        build.equipped[slot] = option
        tried.append(option.name)
        build.score = 9.0

    def fill(index, deadline):
        build.filled.append((index, build.equipped["Weapon 2"].name))
        build.score = after_fill[build.equipped["Weapon 2"].name]
        return True

    gear.put, gear.fill_group = put, fill
    assert gear.purity_trials(float("inf"))
    assert tried == ["Sacred Flame", "rare shrine"] and build.filled == [(3, "Sacred Flame"), (3, "rare shrine")]
    assert build.equipped["Weapon 2"].name == "rare shrine" and build.score == 11.0
