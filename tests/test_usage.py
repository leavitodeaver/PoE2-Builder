"""Usage requirements (builder/search/usage.py): skills and supports that can't be used at will are
allowed only with a real, sustainable source. The engine tests boot one PoB engine for the module
and need the game-data dump."""

from __future__ import annotations

import faulthandler

import pytest

from builder.data import GameData
from builder.request.schema import BuildRequest
from builder.search.context import Context, make_payload
from builder.search.usage import (Usage, combo_rotation, requirements, throw_cost, travel_rotation,
                                  travel_seconds)

ZERPHI = {"stats": {"active_skill_requires_X_glory": 50, "active_skill_generates_%_of_life_spent_as_glory": 5}}


def kinds(rule: dict) -> list[str]:
    return [r.kind for r in requirements("gem", rule)]


def test_requirements_come_from_pobs_usage_markers(monkeypatch):
    glory = requirements("Zerphi's Infamy", ZERPHI)
    assert [(r.kind, r.amount, r.rate) for r in glory] == [("glory from life", 50, 0.05)]
    assert kinds({"addsUsageCondition": True, "stats": {}}) == ["never"]  # a usage condition with no stat naming it
    assert kinds({"addsUsageCondition": True,
                  "stats": {"support_combo_finisher_required_number_of_combo_stacks": 3}}) == ["combo"]
    assert kinds({"usageCondition": True, "stats": {"active_skill_requires_X_glory": 200}}) == ["never"]  # a Glory skill
    # A banner (COVC8): the Glory gate is on the placed banner, so the reserved banner isn't barred
    banner = {"placedUsageCondition": True, "placed": ["active_skill_requires_X_glory"],
              "stats": {"active_skill_requires_X_glory": 200, "active_skill_generates_mp_%_glory_per_attack_hit": 100}}
    assert kinds(banner) == ["placed banner"]
    usage = Usage(FakeBuild({"War Banner": banner}, {}), FakeConditions())
    monkeypatch.setattr("builder.search.usage._RULES", None)
    monkeypatch.setattr(Usage, "talisman_blockers", lambda self, name: [])
    assert usage.blockers("War Banner") == []
    staff = requirements("Charged Staff", {"usageCondition": True, "stats": {"active_skill_requires_X_power_charges": 1}})
    assert [(r.kind, r.vars) for r in staff] == [("charges", ("usePowerCharges",))]
    totem = requirements("Spell Totem", {"stats": {"active_skill_requires_X_power_or_endurance_charges": 3}})
    assert totem[0].vars == ("usePowerCharges", "useEnduranceCharges")
    flask = requirements("Ambrosia", {"stats": {"consume_%_of_maximum_mana_flask_charges_on_skill_use": 14}})
    assert [(r.kind, r.pool, r.rate) for r in flask] == [("flask", "Mana", 0.14)]
    # Mentions of a requirement that don't gate a use
    assert kinds({"stats": {"action_required_target_facing_angle_tolerance_degrees": 90}}) == []
    assert kinds({"stats": {"support_glory_required_+%_final": -50}}) == []


class FakeBuild:
    def __init__(self, rules: dict, stats: dict) -> None:
        self.rules, self.stats = rules, stats

    def call(self, method: str, **kwargs):
        return {"gems": self.rules} if method == "usageRules" else dict(self.stats)


class FakeConditions:
    def __init__(self, *active: str) -> None:
        self.active = {var: {} for var in active}


@pytest.fixture
def fresh_rules(monkeypatch):
    monkeypatch.setattr("builder.search.usage._RULES", None)


def test_zerphis_infamy_needs_life_spending_that_pays_for_every_use(fresh_rules):
    rules = {"Zerphi's Infamy": ZERPHI}
    ci = Usage(FakeBuild(rules, {"Speed": 4.0, "LifePerSecondCost": 0}), FakeConditions())
    assert not ci.usable("Zerphi's Infamy")
    assert "50 Glory per use" in ci.blockers("Zerphi's Infamy")[0]
    # 5% of 4,000 Life a second is 200 Glory a second: four uses of 50
    assert ci.usable("Zerphi's Infamy", {"Speed": 4.0, "LifePerSecondCost": 4000})
    assert not ci.usable("Zerphi's Infamy", {"Speed": 4.0, "LifePerSecondCost": 1000})


def test_charges_follow_the_resolver_and_flasks_their_generation(fresh_rules):
    rules = {"Charged Staff": {"usageCondition": True, "stats": {"active_skill_requires_X_power_charges": 1}},
             "Ambrosia": {"stats": {"consume_%_of_maximum_mana_flask_charges_on_skill_use": 14}},
             "Hit and Run": {"addsUsageCondition": True, "stats": {}}}
    build = FakeBuild(rules, {"Speed": 2.0})
    assert not Usage(build, FakeConditions()).usable("Charged Staff")
    assert Usage(build, FakeConditions("usePowerCharges")).usable("Charged Staff")
    assert not Usage(build, FakeConditions(), lambda pool: (0.0, 60.0)).usable("Ambrosia")
    assert Usage(build, FakeConditions(), lambda pool: (20.0, 60.0)).usable("Ambrosia")  # 2 × 14% × 60 = 16.8/s
    assert not Usage(build, FakeConditions("usePowerCharges")).usable("Hit and Run")
    assert Usage(build, FakeConditions()).usable("Rapid Attacks II")  # no requirement at all
    # A13-1 (COVC8, RESEARCH18 #64): Ancestral Bond lifts a totem's charge requirement, not Charged Staff's
    rules.update({"Spell Totem": {"usageCondition": True, "stats": {"active_skill_requires_X_power_or_endurance_charges": 3}},
                  "Ancestral Warrior Totem": {"usageCondition": True,
                                              "stats": {"active_skill_requires_X_endurance_charges": 3}}})
    types = {"Spell Totem": ["SummonsTotem", "Meta"], "Ancestral Warrior Totem": ["SummonsTotem", "Attack"],
             "Charged Staff": ["Attack"]}
    totems = ComboBuild(rules, types, [], {})
    assert not Usage(totems, FakeConditions()).usable("Spell Totem")
    totems.stats = {"passives": [{"name": "Ancestral Bond"}]}
    bond = Usage(totems, FakeConditions())
    assert bond.usable("Spell Totem") and bond.usable("Ancestral Warrior Totem") and not bond.usable("Charged Staff")


def test_distance_travelled_and_combo_bar_a_support_with_their_own_amounts():
    """AUDIT6 A6-2 / A6-5, AUDIT9 A9-4: Echoing Cry's "move 10 metres before each use" is its own kind (barred until
    its damage is scaled, labelled with the walk); each Combo Finisher carries its own Combo count."""
    echo = requirements("Echoing Cry", {"addsUsageCondition": True,
                                        "stats": {"skill_unusable_until_moved_X_distance": 100}})
    assert [(r.kind, r.amount) for r in echo] == [("travel", 100)] and echo[0].label == "move 10 m before each use"
    two = requirements("Combo Finisher II", {"addsUsageCondition": True,
                                             "stats": {"support_combo_finisher_required_number_of_combo_stacks": 5}})
    assert [(r.kind, r.amount) for r in two] == [("combo", 5)] and two[0].label == "5 Combo per use"
    bell = requirements("Tempest Bell", {"usageCondition": True,
                                         "stats": {"active_skill_required_number_of_combo_stacks": 6}})
    assert [(r.kind, r.amount) for r in bell] == [("combo", 6)]


class ComboBuild(FakeBuild):
    """Groups with indices and PoB skill types, and useRates speeds."""

    def __init__(self, rules: dict, types: dict[str, list[str]], groups: list[dict], speeds: dict[str, float]) -> None:
        super().__init__(rules, {})
        self.ctx = type("Ctx", (), {"gems": {n: {"skillTypes": t, "support": not t} for n, t in types.items()}})()
        self.group_list, self.speeds = groups, speeds

    def groups(self) -> list[dict]:
        return self.group_list

    def call(self, name, **kwargs):
        if name == "useRates":
            return {"rates": [{"index": i, "name": g["gems"][0]["name"], "speed": self.speeds[g["gems"][0]["name"]]}
                              for i, g in enumerate(self.group_list) if i in kwargs["indices"]]}
        return super().call(name, **kwargs)


def combo_group(index: int, *names: str, set1: bool = True, set2: bool = True) -> dict:
    return {"index": index, "enabled": True, "set1": set1, "set2": set2,
            "gems": [{"name": n, "support": n.startswith("Combo Finisher")} for n in names]}


def test_combo_finishers_name_the_builds_strikes_and_their_rate(fresh_rules):
    """RULE_AUDIT row 2b: Combo comes from the build's Strikes (KeywordPopups 'Combo', 'Strike'). Barred either way
    until the Full DPS is scaled, but the label says which, and combo_rate gives the rotation's rate."""
    rules = {"Combo Finisher II": {"addsUsageCondition": True,
                                   "stats": {"support_combo_finisher_required_number_of_combo_stacks": 5}}}
    types = {"Combo Finisher II": [], "Falling Thunder": ["Attack", "Melee", "Area"],
             "Quarterstaff Strike": ["Attack", "Melee"], "Boneshatter": ["Attack", "Melee", "Slam"],
             "Fireball": ["Spell", "Projectile"]}
    groups = [combo_group(0, "Falling Thunder", "Combo Finisher II"), combo_group(1, "Fireball"),
              combo_group(2, "Boneshatter")]
    build = ComboBuild(rules, types, groups, {"Falling Thunder": 1.0, "Quarterstaff Strike": 2.5, "Boneshatter": 3.0})
    usage = Usage(build, FakeConditions())
    # A Slam and a spell don't Strike: no builder
    assert usage.combo_builders("Combo Finisher II") == []
    assert "no skill of the build Strikes" in usage.blockers("Combo Finisher II")[0]
    assert usage.combo_rate("Combo Finisher II", 5) is None
    # A Strike on the same weapon set builds it: 5 Strikes at 2.5/s (2 s), then one use (1 s) -> 1/3 use a second
    build.group_list = groups + [combo_group(3, "Quarterstaff Strike")]
    assert usage.combo_builders("Combo Finisher II") == [(3, "Quarterstaff Strike")]
    assert "built by Quarterstaff Strike" in usage.blockers("Combo Finisher II")[0]  # (still barred: no scaling yet)
    uses, share, builder = usage.combo_rate("Combo Finisher II", 5)
    assert builder == "Quarterstaff Strike" and abs(uses - 1 / 3) < 1e-9 and abs(share - 1 / 3) < 1e-9
    # Combo is lost on a weapon swap: a Strike held only on the other set builds nothing
    build.group_list = [combo_group(0, "Falling Thunder", "Combo Finisher II", set2=False),
                        combo_group(3, "Quarterstaff Strike", set1=False)]
    assert usage.combo_builders("Combo Finisher II") == []
    assert combo_rotation(3, 2.0, 1.0, gain=1.5) == (0.5, 0.5)
    # Martial Master (A11-4): any Attack Hit builds Combo, on any weapon set; Martial Adept: 2 Combo per gain
    build.stats = {"passives": [{"name": "Martial Master"}]}
    build.group_list = [combo_group(0, "Falling Thunder", "Combo Finisher II", set2=False),
                        combo_group(2, "Boneshatter", set1=False), combo_group(1, "Fireball")]
    assert usage.combo_builders("Combo Finisher II") == [(2, "Boneshatter")]
    assert "(Attack Hits, Martial Master)" in usage.blockers("Combo Finisher II")[0]
    build.group_list = [combo_group(0, "Falling Thunder", "Combo Finisher II"), combo_group(1, "Boneshatter")]
    build.stats = {"passives": [{"name": "Martial Master"}, {"name": "Martial Adept"}]}
    uses, share, builder = usage.combo_rate("Combo Finisher II", 5)  # 2.5 Boneshatters at 3/s, then 1 s: 1/(5/6 + 1)
    assert builder == "Boneshatter" and abs(uses - 6 / 11) < 1e-9
    # A13-2 (COVC8): "N% chance to build an additional Combo on Hit" from passives and items (each gain), Practiced
    # Combo (40%, the builder in its group): 1 + 1 (Adept) + 0.1 + 0.4 = 2.5 a gain: 2 Boneshatters (2/3 s), then 1 s
    rules["Practiced Combo"] = {"comboChance": 40, "stats": {}}
    types["Practiced Combo"] = []
    build.group_list = [combo_group(0, "Falling Thunder", "Combo Finisher II"),
                        combo_group(1, "Boneshatter", "Practiced Combo")]
    build.stats = {"passives": [{"name": "Martial Master"}, {"name": "Martial Adept"}],
                   "lines": [{"source": "passive:Combo Gain", "text": "10% chance to build an additional Combo on Hit"},
                             {"source": "gem:Practiced Combo", "text": "10% chance to build an additional Combo on Hit"},
                             {"source": "item:Belt", "text": "5% chance to build an additional Combo on Hit while x"}]}
    assert usage.combo_chance() == 10 and usage.group_combo_chance(1) == 40 and usage.group_combo_chance(0) == 0
    uses, share, builder = usage.combo_rate("Combo Finisher II", 5)
    assert builder == "Boneshatter" and abs(uses - 0.6) < 1e-9
    build.stats["lines"].append({"source": "item:Amulet", "text": "Build an additional Combo on Hit"})
    assert usage.combo_chance() == 110  # (capped at 100% in the gain: 1 + 1 + 1 = 3 a gain)
    assert abs(usage.combo_rate("Combo Finisher II", 6)[0] - 1 / (2 / 3 + 1)) < 1e-9


def test_concoctions_need_a_mana_flask_that_gains_charges(fresh_rules):
    """A8-2 / COVC4: 5 Mana Flask charges a throw (flasks.THROW_CHARGES, all five Concoctions, though PoB's data has the
    stat on three); barred only when the Mana Flask gains none in a boss fight. Its partial rate: Usage.throw_rate."""
    rules = {"Acidic Concoction": {"stats": {"flask_throw_base_charges_used": 5}}}
    need = [r for r in requirements("Fulminating Concoction", {}) if r.kind == "flask throw"]
    assert [(r.amount, r.pool) for r in need] == [(5, "Mana")] and "5 Mana Flask charges per throw" in need[0].label
    assert [r.kind for r in requirements("Acidic Concoction", rules["Acidic Concoction"])] == ["flask throw"]
    build = FakeBuild(rules, {})
    dry = Usage(build, FakeConditions(), lambda pool: (0.0, 60.0))
    assert "gains no charges in a boss fight" in dry.blockers("Fulminating Concoction")[0]
    assert not dry.usable("Acidic Concoction") and not Usage(build, FakeConditions()).usable("Acidic Concoction")
    # The main skill's throws are rated by Assumptions.flask_main once the gear is on: never barred here
    main = FakeConditions()
    main.main_skill = "Fulminating Concoction"
    assert Usage(build, main, lambda pool: (0.0, 0.0)).usable("Fulminating Concoction")
    wet = Usage(build, FakeConditions(), lambda pool: (1.5, 60.0))
    assert wet.usable("Fulminating Concoction") and wet.throw_rate(need[0]) == 0.3
    # COVC8 (RESEARCH16 #82): the build's Flask Charges used modifiers change the 5 (MEDIUM)
    assert throw_cost(5, -20) == pytest.approx(4.0) and throw_cost(5, 50) == pytest.approx(7.5)  # reduced / increased
    assert throw_cost(5, 0, 0.5) == pytest.approx(2.5) and throw_cost(5, -20, 1.0, 25) == pytest.approx(3.0)
    assert throw_cost(5, 0, 1.0, 150) == 0.0
    belt = Usage(FakeBuild(rules, {"usedInc": -20, "usedMore": 1.0, "notConsume": 25}), FakeConditions(),
                 lambda pool: (1.5, 60.0))
    assert belt.throw_cost(need[0]) == pytest.approx(3.0) and belt.throw_rate(need[0]) == pytest.approx(0.5)
    free = Usage(FakeBuild(rules, {"notConsume": 100}), FakeConditions(), lambda pool: (1.5, 60.0))
    assert free.throw_rate(need[0]) == float("inf")
    assert "before the build's Flask Charges used modifiers" in need[0].label and "snippet" not in need[0].label


def test_echoing_cry_and_hit_and_run_get_a_walking_use_rate(fresh_rules):
    """AUDIT9 A9-4 (RESEARCH15 base-movement-speed.md): 10 m at 3.8 m/s* (LOW) x the build's movement speed between
    uses, the cooldown permitting; whole dodge rolls of 4.6 m, 0.75 s each, as the with-it figure. Still barred."""
    assert abs(travel_seconds(100) - 10 / 3.8) < 1e-9
    assert abs(travel_seconds(100, 1.25) - 10 / 4.75) < 1e-9
    assert travel_seconds(100, rolls=True) == 3 * 0.75 and travel_seconds(100, 1.1, rolls=True) == 2 * 0.75
    # A 0.5 s attack: 0.5 + 2.63 s a use; walking is 84% of the fight
    uses, share, walking = travel_rotation(100, 1.0, 2.0)
    assert abs(uses - 1 / (0.5 + 10 / 3.8)) < 1e-9 and abs(share - uses / 2) < 1e-9 and abs(walking - uses * 10 / 3.8) < 1e-9
    # A warcry on a 10 s cooldown: the walk fits inside it
    assert travel_rotation(100, 1.0, 1.0, cooldown=10.0)[0] == 0.1
    rules = {"Hit and Run": {"addsUsageCondition": True, "stats": {"skill_unusable_until_moved_X_distance": 100}}}
    types = {"Hit and Run": [], "Quarterstaff Strike": ["Attack", "Melee"]}
    build = ComboBuild(rules, types, [{"index": 0, "enabled": True, "gems": [
        {"name": "Quarterstaff Strike", "support": False}, {"name": "Hit and Run", "support": True}]}],
                       {"Quarterstaff Strike": 4.0})
    build.stats = {"EffectiveMovementSpeedMod": 1.3}
    usage = Usage(build, FakeConditions())
    blocked = usage.blockers("Hit and Run")
    assert len(blocked) == 1 and "move 10 m before each use (2.6 s of walking" in blocked[0]
    uses, share, walking, rolled = usage.travel_rate("Hit and Run")
    assert abs(uses - 1 / (0.25 + 10 / (3.8 * 1.3))) < 1e-9 and abs(share - uses / 4) < 1e-9
    assert abs(rolled - 1 / (0.25 + 2 * 0.75)) < 1e-9  # (two rolls of 4.6 x 1.3 m)
    assert usage.travel_rate("Quarterstaff Strike") is None


# Through a real engine --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def engine():
    try:
        GameData()
    except FileNotFoundError:
        pytest.skip("no game-data dump; run `uv run python -m builder.data dump`")
    from builder.pob.engine import PobEngine

    enabled = faulthandler.is_enabled()
    faulthandler.disable()
    yield PobEngine()
    if enabled:
        faulthandler.enable()


def test_pob_marks_the_gems_that_cant_be_used_at_will(engine):
    rules = engine.call("usageRules")["gems"]
    assert rules["Zerphi's Infamy"]["stats"]["active_skill_requires_X_glory"] == 50
    assert rules["Hit and Run"].get("addsUsageCondition")
    assert rules["Combo Finisher I"].get("addsUsageCondition")
    assert rules["Combo Finisher II"]["stats"]["support_combo_finisher_required_number_of_combo_stacks"] == 5
    assert rules["Echoing Cry"]["stats"]["skill_unusable_until_moved_X_distance"] == 100
    assert rules["Hit and Run"]["stats"]["skill_unusable_until_moved_X_distance"] == 100
    assert rules["Blink"].get("talismanExempt") and rules["Tame Beast"].get("talismanExempt")  # usable_with_talisman
    assert not rules["Blink"].get("stats")  # (an exemption, never a requirement)
    # AUDIT7 A7-3: the socketed-Shapeshift stats are their own flag, not an exemption
    for name in ("Spirit Vessel", "Feral Invocation"):
        assert rules[name].get("socketedFormFree") and not rules[name].get("talismanExempt"), name
    # COVC8: the Glory gate and usage condition are the placed banner's (the gem's second effect), not the reservation's
    for name in ("War Banner", "Dread Banner", "Defiance Banner"):
        assert rules[name].get("placedUsageCondition") and not rules[name].get("usageCondition"), name
        assert "active_skill_requires_X_glory" in rules[name]["placed"], name
        assert rules[name]["stats"]["active_skill_requires_X_glory"] == 200, name  # (the GlorySkill condition reads it)
        assert [r.kind for r in requirements(name, rules[name])] == ["placed banner"], name
    assert "Rapid Attacks II" not in rules
    assert rules["Practiced Combo"]["comboChance"] == 40 and not requirements("Practiced Combo", rules["Practiced Combo"])
    cost = engine.call("flaskThrowCost")  # (COVC8: the Concoctions' throw cost modifiers; none on an empty build)
    assert (cost["usedInc"], cost["usedMore"], cost["notConsume"]) == (0, 1, 0)


def test_a_ci_build_never_picks_zerphis_infamy(engine, fresh_rules):
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    ctx = Context(make_payload(BuildRequest(main_skill="Explosive Spear", ascendancies=["Ritualist"], offhand="weapon",
                                            require={"passives": ["Chaos Inoculation"]})))
    skeleton = enumerate_skeletons(ctx, ["Explosive Spear"])[0][0]
    search = Search(engine, None, make_payload(ctx.request), skeleton.as_dict(), 0)
    search.setup(quests=False)
    assert not search.gems.usable("Zerphi's Infamy")  # Chaos Inoculation: no Life spent, so no Glory
    search.gems.optimize_supports(float("inf"), rounds=6)
    group = search.build.main_group()
    supports = [g["name"] for g in group["gems"][1:]]
    assert "Zerphi's Infamy" not in supports and "Hit and Run" not in supports
    # Put in by hand, it goes again on the next conditions pass
    search.gems.commit(group["index"], [group["gems"][0]["name"], "Zerphi's Infamy"])
    search.conditions_round()
    assert "Zerphi's Infamy" not in [g["name"] for g in search.build.main_group()["gems"]]


class TalismanBuild(FakeBuild):
    """A build whose gems have PoB skill types (ctx.gems) and socket groups (groups())."""

    def __init__(self, rules: dict, types: dict[str, list[str]], groups: list[dict]) -> None:
        super().__init__(rules, {})
        support = {"Feral Support", "Vessel Support"}
        self.ctx = type("Ctx", (), {"gems": {n: {"skillTypes": t, "support": n in support} for n, t in types.items()}})()
        self.group_list = groups

    def groups(self) -> list[dict]:
        return self.group_list


def group(*names: str, set1: bool = True, set2: bool = True) -> dict:
    return {"enabled": True, "set1": set1, "set2": set2,
            "gems": [{"name": n, "support": n in ("Feral Support", "Vessel Support")} for n in names]}


TYPES = {"Ancestral Cry": ["Warcry", "Area", "Cooldown"], "Fireball": ["Spell", "Projectile", "Triggerable"],
         "Maul": ["Attack", "Melee", "Bear", "Shapeshift"], "Herald of Ash": ["Persistent", "HasReservation", "Buff"],
         "Cast on Shock": ["Meta", "Persistent", "Buff"], "Spell Totem": ["Meta", "SummonsTotem", "HasUsageCondition"],
         "Blink": ["Spell", "Travel"], "Frost Bomb": ["Spell", "Area"], "Feral Support": [], "Vessel Support": [],
         "Ice Nova": ["Spell", "Area"],
         "Hollow Form": ["Meta", "Channel", "UsableWhileShapeshifted"]}


def test_a_talisman_shuts_out_skills_that_cant_be_used_in_a_shapeshift_form(fresh_rules):
    rules = {"Blink": {"stats": {}, "talismanExempt": True}, "Feral Support": {"stats": {}, "talismanExempt": True},
             "Vessel Support": {"stats": {}, "socketedFormFree": True}}
    groups = [group("Maul"), group("Herald of Ash"), group("Cast on Shock", "Fireball"), group("Ancestral Cry"),
              group("Frost Bomb", "Feral Support"), group("Spell Totem", "Fireball"), group("Ice Nova", "Vessel Support")]
    build = TalismanBuild(rules, TYPES, groups)
    usage = Usage(build, FakeConditions(), weapons={1: "Talisman"})
    # The game: "Skills that cannot be used in a Shapeshift form cannot be used with Talismans"
    assert usage.blockers("Ancestral Cry") and not usage.usable("Ancestral Cry")
    assert not usage.usable("Spell Totem")  # (its Fireball: assumption 2, follows the totem)
    assert usage.usable("Hollow Form")  # UsableWhileShapeshifted (not in the build yet)
    # AUDIT7 A7-3: Spirit Vessel's / Feral Invocation's stats free socketed Shapeshift skills only, never a spell
    assert not usage.usable("Ice Nova")
    # "Triggered Skills and Persistent Skills will continue to function"; form skills and exemptions stay
    for name in ("Maul", "Herald of Ash", "Cast on Shock", "Fireball", "Blink", "Frost Bomb", "Feral Support"):
        assert usage.usable(name), name
    # A self-cast spell outside a trigger is shut out, unless a weapon set without a Talisman holds it
    build.group_list = [group("Fireball")]
    assert not usage.usable("Fireball")
    assert not usage.usable("Frost Bomb")  # not in the build yet: a new group would sit on the Talisman set
    swap = Usage(build, FakeConditions(), weapons={1: "Talisman", 2: "Wand"})
    assert swap.usable("Fireball") and swap.usable("Frost Bomb")
    build.group_list = [group("Fireball", set2=False)]
    assert not swap.usable("Fireball")  # held on set 1 (the Talisman) only
    # No Talisman: nothing changes
    assert Usage(build, FakeConditions(), weapons={1: "Two Hand Mace"}).usable("Ancestral Cry")
    assert Usage(build, FakeConditions()).usable("Ancestral Cry")
