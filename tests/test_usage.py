"""Usage requirements (builder/search/usage.py): skills and supports that can't be used at will are
allowed only with a real, sustainable source. The engine tests boot one PoB engine for the module
and need the game-data dump."""

from __future__ import annotations

import faulthandler

import pytest

from builder.data import GameData
from builder.request.schema import BuildRequest
from builder.search.context import Context, make_payload
from builder.search.usage import Usage, requirements

ZERPHI = {"stats": {"active_skill_requires_X_glory": 50, "active_skill_generates_%_of_life_spent_as_glory": 5}}


def kinds(rule: dict) -> list[str]:
    return [r.kind for r in requirements("gem", rule)]


def test_requirements_come_from_pobs_usage_markers():
    glory = requirements("Zerphi's Infamy", ZERPHI)
    assert [(r.kind, r.amount, r.rate) for r in glory] == [("glory from life", 50, 0.05)]
    assert kinds({"addsUsageCondition": True, "stats": {}}) == ["never"]  # Hit and Run: distance moved
    assert kinds({"addsUsageCondition": True,
                  "stats": {"support_combo_finisher_required_number_of_combo_stacks": 3}}) == ["never"]
    assert kinds({"usageCondition": True, "stats": {"active_skill_requires_X_glory": 200}}) == ["never"]  # a banner
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
    assert rules["Blink"].get("talismanExempt") and rules["Tame Beast"].get("talismanExempt")  # usable_with_talisman
    assert not rules["Blink"].get("stats")  # (an exemption, never a requirement)
    assert rules["War Banner"].get("usageCondition")  # on the placed banner, the gem's second effect
    assert "Rapid Attacks II" not in rules


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
        support = {"Feral Support"}
        self.ctx = type("Ctx", (), {"gems": {n: {"skillTypes": t, "support": n in support} for n, t in types.items()}})()
        self.group_list = groups

    def groups(self) -> list[dict]:
        return self.group_list


def group(*names: str, set1: bool = True, set2: bool = True) -> dict:
    return {"enabled": True, "set1": set1, "set2": set2,
            "gems": [{"name": n, "support": n == "Feral Support"} for n in names]}


TYPES = {"Ancestral Cry": ["Warcry", "Area", "Cooldown"], "Fireball": ["Spell", "Projectile", "Triggerable"],
         "Maul": ["Attack", "Melee", "Bear", "Shapeshift"], "Herald of Ash": ["Persistent", "HasReservation", "Buff"],
         "Cast on Shock": ["Meta", "Persistent", "Buff"], "Spell Totem": ["Meta", "SummonsTotem", "HasUsageCondition"],
         "Blink": ["Spell", "Travel"], "Frost Bomb": ["Spell", "Area"], "Feral Support": [],
         "Hollow Form": ["Meta", "Channel", "UsableWhileShapeshifted"]}


def test_a_talisman_shuts_out_skills_that_cant_be_used_in_a_shapeshift_form(fresh_rules):
    rules = {"Blink": {"stats": {}, "talismanExempt": True}, "Feral Support": {"stats": {}, "talismanExempt": True}}
    groups = [group("Maul"), group("Herald of Ash"), group("Cast on Shock", "Fireball"), group("Ancestral Cry"),
              group("Frost Bomb", "Feral Support"), group("Spell Totem", "Fireball")]
    build = TalismanBuild(rules, TYPES, groups)
    usage = Usage(build, FakeConditions(), weapons={1: "Talisman"})
    # The game: "Skills that cannot be used in a Shapeshift form cannot be used with Talismans"
    assert usage.blockers("Ancestral Cry") and not usage.usable("Ancestral Cry")
    assert not usage.usable("Spell Totem")  # (its Fireball: assumption 2, follows the totem)
    assert usage.usable("Hollow Form")  # UsableWhileShapeshifted (not in the build yet)
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
