"""THE USER (14 Nov, "Count it if used"): an item-granted skill the build presses counts toward the button cap; one it
never uses (an idle shield's Raise Shield, a weapon's own attack it never makes) doesn't (AUDIT31 A31-4, AUDIT32 A32-1).
SEARCH19."""
from __future__ import annotations

from types import SimpleNamespace

from builder.request.constraints import player_buttons
from builder.search import gems as gems_module
from builder.search.gems import Gems
from builder.search.inner import Search


def gem(name, *types, support=False):
    return {"name": name, "support": support, "enabled": True, "skillTypes": list(types), "tags": []}


def group(index, *gems, from_item=False, main=False, full=False, enabled=True):
    return {"index": index, "enabled": enabled, "fromItem": from_item, "main": main, "fullDPS": full,
            "gems": list(gems), "label": ""}


def test_an_item_granted_skill_counts_as_a_button_when_the_build_presses_it():
    facts = {"skillGroups": [
        group(1, gem("Spark", "Spell", "Damage"), main=True, full=True),
        group(2, gem("Raise Shield", "Attack", "Channel"), from_item=True),  # idle: free
        group(3, gem("Mace Strike", "Attack"), from_item=True),  # a weapon's own attack the build never makes: free
        group(4, gem("Solar Orb", "Spell", "Damage"), from_item=True, full=True),  # its damage is counted: pressed
        group(5, gem("Unleash", "Spell", "Buff", "Cooldown"), from_item=True),
        group(6, gem("Cast on Block", "Meta", "Persistent", "Buff"), gem("Spark", "Spell", "Triggerable"), from_item=True),
    ]}
    assert player_buttons(facts) == ["Spark", "Solar Orb"]
    # Bastion of Light's taps (build.item_presses) and an item buff the build's score uses (Search.fit_buttons' probe)
    assert player_buttons({**facts, "pressedSkills": ["Raise Shield", "Unleash"]}) == [
        "Spark", "Raise Shield", "Solar Orb", "Unleash"]
    # the main skill from an item (a wand's Chaos Bolt) is pressed
    wand = {"skillGroups": [group(1, gem("Chaos Bolt", "Spell", "Damage"), from_item=True, main=True)]}
    assert player_buttons(wand) == ["Chaos Bolt"]


class FakeBuild:
    """Spark (main) + a staff's Unleash (worth 20 to the score) + a shield's Raise Shield (its taps worth 5 while
    objective.raised_taps is on, as MECHC21's Assumptions puts them on item_presses) + a mace's idle Mace Strike."""

    def __init__(self, cap):
        self.objective = SimpleNamespace(raised_taps=True, press_choices={})
        self.ctx = SimpleNamespace(request=SimpleNamespace(max_buttons=cap), any_gems={
            "Spark": gem("Spark", "Spell", "Damage"), "Unleash": gem("Unleash", "Spell", "Buff", "Cooldown"),
            "Raise Shield": gem("Raise Shield", "Attack", "Channel"), "Mace Strike": gem("Mace Strike", "Attack")})
        self._groups = [
            group(1, gem("Spark", "Spell", "Damage"), main=True, full=True),
            group(2, gem("Unleash", "Spell", "Buff", "Cooldown"), from_item=True),
            group(3, gem("Raise Shield", "Attack", "Channel"), from_item=True),
            group(4, gem("Mace Strike", "Attack"), from_item=True),
        ]
        self.score = 0.0
        self.stats = {}
        self.settle()
        self.objective.score = lambda stats: self.score

    def groups(self):
        return [dict(g) for g in self._groups]

    def set_group(self, index, **changes):
        for g in self._groups:
            if g["index"] == index:
                g.update(changes)

    def settle(self):
        on = {g["gems"][0]["name"] for g in self._groups if g["enabled"]}
        taps = self.objective.raised_taps is not False and "Raise Shield" in on
        setattr(self, gems_module.ITEM_PRESSES, {"Raise Shield": (1.0, "taps")} if taps else {})
        self.score = 100.0 + (20.0 if "Unleash" in on else 0.0) + (5.0 if taps else 0.0)


def fake_search(cap):
    build = FakeBuild(cap)
    search = SimpleNamespace(build=build, settle=build.settle)
    search.gems = SimpleNamespace(buttons=lambda: Gems.buttons(SimpleNamespace(build=build, ctx=build.ctx)))
    search._rescore = lambda: Search._rescore(search)
    return search


def test_at_the_cap_the_search_drops_the_cheapest_item_press():
    search = fake_search(cap=2)
    Search.fit_buttons(search)
    build = search.build
    assert getattr(build, gems_module.USED_ITEM_SKILLS) == {"Unleash"}  # the probe: Unleash moves the score, Mace Strike doesn't
    assert build.objective.raised_taps is False  # taps off costs 5, Unleash off 20
    assert search.gems.buttons() == ["Spark", "Unleash"]
    assert build.score == 120.0


def test_under_the_cap_every_used_item_press_stays_and_counts():
    search = fake_search(cap=3)
    Search.fit_buttons(search)
    assert search.build.objective.raised_taps is True
    assert search.gems.buttons() == ["Spark", "Unleash", "Raise Shield"]
    assert search.build.score == 125.0


def test_taps_come_back_on_when_a_button_is_spare():
    search = fake_search(cap=3)
    search.build.objective.raised_taps = False
    search.build.settle()
    Search.fit_buttons(search)
    assert search.build.objective.raised_taps is True
    assert search.build.score == 125.0


def test_the_names_match_mechc21s():
    """fit_buttons and pressed_item_skills read MECHC21's build.item_presses and objective.raised_taps by name."""
    from builder.search import models

    assert gems_module.ITEM_PRESSES == models.ITEM_PRESSES and models.RAISED_TAPS == "raised_taps"
