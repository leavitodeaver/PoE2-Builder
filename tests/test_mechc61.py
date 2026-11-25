"""MECHC61: a side group's Rageforged at the paid share; each group's Rageforged spends its own Rage."""
from __future__ import annotations

import pytest

from builder.search import conditions
from builder.search.assumptions import Assumptions


def _group(index, skills, supports, main=False, enabled=True):
    gems = [{"name": n, "support": False} for n in skills] + [{"name": n, "support": True} for n in supports]
    return {"index": index, "main": main, "enabled": enabled, "gems": gems, "skills": [{"name": n} for n in skills]}


class _Build:
    def __init__(self, groups, box, share, rates=None):
        self._groups = groups
        self.rage_paid = {"share": share, "paid": 4.5, "asked": 4.5 / share}
        self.box = box
        self.rates = rates or {}

    def main_group(self):
        return next((g for g in self._groups if g.get("main")), None)

    def groups(self):
        return self._groups

    def call(self, method, **kw):
        if method == "getConfig":
            return {"input": {"multiplierRage": self.box}}
        if method == "groups":
            return {"groups": self._groups, "mainSocketGroup": 1}
        if method == "useRates":
            return {"rates": [{"index": i, "name": "x", "cooldown": 1 / self.rates[i]} for i in kw["indices"]]}
        raise KeyError(method)


def _hook(build):
    hook = Assumptions.__new__(Assumptions)
    hook.build = build
    return hook


def test_side_rageforged_moves_to_the_paid_share():
    """Spiral Volley main (no Rageforged), Snipe beside it with Rageforged II; box 30, 60% paid: Snipe's PoB 35% -> 21%."""
    groups = [_group(1, ["Spiral Volley"], ["Martial Tempo"], main=True), _group(2, ["Snipe"], ["Rageforged II"])]
    mods, notes = _hook(_Build(groups, 30, 0.6)).rageforged("Spiral Volley")
    assert len(mods) == 1 and mods[0]["skill"] == "Snipe"
    assert mods[0]["value"] == pytest.approx(((1 + 0.35 * 0.6) / 1.35 - 1) * 100, rel=1e-3)
    assert "beside the main skill" in notes[0]["label"]
    # all paid at a box of 30: PoB's reading stands, nothing
    assert _hook(_Build(groups, 30, 1.0)).rageforged("Spiral Volley") == ([], [])
    # all paid but the sawtooth holds under 10: PoB gave none, the side skill gets all 35%
    mods, _ = _hook(_Build(groups, 6, 1.0)).rageforged("Spiral Volley")
    assert mods[0]["skill"] == "Snipe" and mods[0]["value"] == pytest.approx(35.0)


def test_main_and_side_rageforged_both_moved_and_a_shared_name_kept():
    groups = [_group(1, ["Twister"], ["Rageforged II"], main=True), _group(2, ["Snipe"], ["Rageforged I"]),
              _group(3, ["Twister"], [])]
    mods, notes = _hook(_Build(groups, 30, 0.5)).rageforged("Twister")
    assert {m["skill"] for m in mods} == {"Twister", "Snipe"}
    # a side skill that also sits in a group without Rageforged keeps PoB's reading, said
    groups = [_group(1, ["Twister"], [], main=True), _group(2, ["Snipe"], ["Rageforged I"]), _group(3, ["Snipe"], [])]
    mods, notes = _hook(_Build(groups, 30, 0.5)).rageforged("Twister")
    assert mods == [] and "PoB's reading kept" in notes[-1]["label"]


def test_disabled_side_group_and_no_paid_share_add_nothing():
    groups = [_group(1, ["Twister"], [], main=True), _group(2, ["Snipe"], ["Rageforged II"], enabled=False)]
    assert _hook(_Build(groups, 30, 0.5)).rageforged("Twister") == ([], [])
    build = _Build(groups, 30, 0.5)
    build.rage_paid = None
    assert _hook(build).rageforged("Twister") == ([], [])


def test_each_groups_rageforged_spends_its_own_rage():
    """Rageforged II on the main group and on a side group (0.5 uses/s): the side copy spent nothing before."""
    groups = [_group(1, ["Twister"], ["Rageforged II"], main=True), _group(2, ["Snipe"], ["Rageforged II"]),
              _group(3, ["Earthquake"], ["Rageforged I"]), _group(4, ["Leap Slam"], ["Rageforged I"])]
    self = conditions.Conditions.__new__(conditions.Conditions)
    self.build = _Build(groups, 0, 1.0, rates={2: 0.5, 3: 0.25, 4: 0.2})
    spend = {"Rageforged II": 7.0, "Rageforged I": 2.0}
    extra = self._rageforged_copies(spend, {"Twister", "Rageforged II"})
    # Rageforged II: the main copy counted, Snipe's added; Rageforged I: the last group's counted (as _group_uses finds
    # it), Earthquake's added
    assert extra == pytest.approx({"Rageforged II (Snipe)": 0.5 * 7.0, "Rageforged I (Earthquake)": 0.25 * 10})
    assert conditions.rage_spender("Rageforged II (Snipe)") == "Rageforged II"
    assert conditions.rage_spender("Molten Crash") == "Molten Crash"
    # the sawtooth takes the keyed copies (amount and skip chance from the gem)
    assert conditions.rage_sawtooth(5.0, {**spend, **extra}, 30.0) is not None


@pytest.fixture(scope="module")
def engine():
    import faulthandler

    from builder.data import GameData

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


def test_real_pob_side_rageforged_line_reaches_the_side_skill_only(engine):
    """Real PoB: a SkillName MORE line for a side group's skill moves that group's damage and not the main skill's (the
    probe behind Assumptions.rageforged's side lines); the bridge's groups give each side skill's name; two groups'
    Rageforged II spend apart (conditions._rageforged_copies on the bridge's own groups and useRates rows)."""
    from test_engine_models import spark_with  # (tests/ is on sys.path under pytest)

    from builder.search import models

    search = spark_with(engine, [])
    build = search.build
    build.call("addSkillGroup", text="Frost Bomb 1/0 1\nRageforged II 1/0 1", fullDPS=True)
    build.call("addSkillGroup", text="Orb of Storms 1/0 1\nRageforged II 1/0 1")
    search.settle()
    groups = build.groups()
    side = next(g for g in groups if any(s["name"] == "Frost Bomb" for s in g.get("skills") or []))
    main_before = build.call("stats", fields=["TotalDPS"])["TotalDPS"]
    own = list(search.assumptions.mods)

    def side_dps(mods):
        build.call("setAssumptions", mods=mods, fields=[])
        return build.call("useRates", indices=[side["index"]])["rates"][0].get("TotalDPS")

    before = side_dps(own)
    after = side_dps(own + [models.raw_mod("Damage", "MORE", -50, skill="Frost Bomb")])
    main_after = build.call("stats", fields=["TotalDPS"])["TotalDPS"]
    build.call("setAssumptions", mods=own, fields=[])
    # (Frost Bomb's TotalDPS fell to 0.556 of itself, not 0.5, on POE2T7151: a part of it a Damage MORE doesn't scale;
    # PoB's own Rageforged line is the same Damage MORE, so the side line scales what PoB's did)
    assert before and before * 0.45 < after < before * 0.6
    assert main_after == pytest.approx(main_before, rel=1e-6)
    # the hook names the side skill from the bridge's groups (a paid share of 50%, PoB's box 30)
    build.rage_paid = {"share": 0.5, "paid": 1.0, "asked": 2.0}
    build.call("setConfig", vars={"multiplierRage": 30}, fields=[])
    mods, _ = search.assumptions.rageforged("Spark")
    assert {"Frost Bomb", "Orb of Storms"} <= {m["skill"] for m in mods}
    # two groups' Rageforged II: the last counted by name, the other added at its own group's rate
    copies = search.conditions._rageforged_copies({"Rageforged II": 1.0}, {"Spark"})
    assert list(copies) == ["Rageforged II (Frost Bomb)"] and copies["Rageforged II (Frost Bomb)"] >= 0

