"""MECH115: Poisons from different sources share your Poison limit, the strongest acting (RESEARCH68 R68-7 / R68-10)."""
import pytest

from builder.search import models


def _value(mods, name):
    return next(m["value"] for m in mods if m["name"] == name) / 100


def test_poison_fill_strongest_first():
    assert models.poison_fill(1, [(10.0, 1.0), (5.0, 2.0)]) == pytest.approx(10.0)
    assert models.poison_fill(3, [(5.0, 4.0), (10.0, 1.0)]) == pytest.approx(20.0)
    # (a stronger source up half the time: the weaker fills the rest, as strongest-wins at limit 1)
    assert models.poison_fill(1, [(5.0, 1.0), (10.0, 0.5)]) == pytest.approx(7.5)
    assert models.poison_fill(0, [(5.0, 1.0)]) == 0.0


def test_cloud_poison_beside_a_full_limit_is_cut():
    mods = [models.added_input("GasArrowCloud", 40.0)]
    notes = [{"poison_pool": ("AddedGasArrowCloud", 40.0, 1.0)}]
    # limit 1 held by the headline's stronger Poison: the cloud's adds nothing
    out, rows = models.poison_pool_model(mods, notes, (100.0, 1.0, "main"), 1.0)
    assert _value(out, "AddedGasArrowCloud") == pytest.approx(0.0)
    assert rows and rows[0]["entry"] == models.POISON_POOL_ENTRY and "+40.0" in rows[0]["basis"]
    # the cloud stronger than the headline's: it replaces it, adding only the difference
    out, _ = models.poison_pool_model(mods, notes, (25.0, 1.0, "main"), 1.0)
    assert _value(out, "AddedGasArrowCloud") == pytest.approx(15.0)
    # room for both: nothing cut, no row
    out, rows = models.poison_pool_model(mods, notes, (100.0, 1.0, "main"), 2.0)
    assert _value(out, "AddedGasArrowCloud") == pytest.approx(40.0) and rows == []


def test_lone_sources_unchanged():
    # Lycosidae at limit 1 already counts strongest-wins (up x (poison - own)): unchanged
    up, poison, own = 0.6, 30.0, 10.0
    gain = up * (poison - own)
    mods = [models.added_input("BlockPoison", gain)]
    out, rows = models.poison_pool_model(mods, [{"poison_pool": ("AddedBlockPoison", poison, up)}],
                                         (own, 1.0, "main"), 1.0)
    assert _value(out, "AddedBlockPoison") == pytest.approx(gain) and rows == []
    # Herald of Plague's spread: the main skill's own Poisons, min(limit, S + A): unchanged
    one, limit, s, a = 20.0, 4.0, 2.5, 1.0
    added = one * (min(limit, s + a) - min(limit, s))
    mods = [models.added_input("HeraldOfPlague", added)]
    out, rows = models.poison_pool_model(mods, [{"poison_pool": ("AddedHeraldOfPlague", one, added / one)}],
                                         (one, s, "main"), limit)
    assert _value(out, "AddedHeraldOfPlague") == pytest.approx(added) and rows == []


def test_raised_limit_lycosidae_behind_a_stronger_own_poison():
    # limit 3, the build's own 3 stronger Poisons held: Lycosidae's (taken by its model to hold 2 free slots) adds 0
    mods = [models.added_input("BlockPoison", 2 * 30.0)]
    out, _ = models.poison_pool_model(mods, [{"poison_pool": ("AddedBlockPoison", 30.0, 2.0)}], (50.0, 3.0, "main"), 3.0)
    assert _value(out, "AddedBlockPoison") == pytest.approx(0.0)


def test_two_added_sources_share_the_room_and_a_minion_is_shared():
    mods = [models.added_input("GasArrowCloud", 30.0), models.added_input("SniperGasArrow", 50.0)]
    notes = [{"poison_pool": ("AddedGasArrowCloud", 30.0, 1.0)}, {"poison_pool": ("AddedSniperGasArrow", 20.0, 1.0)}]
    # limit 2, the headline's one Poison of 100: one slot left, the cloud's 30 keeps it; the Sniper's 20 Poison goes
    # (its 30 of hits stays)
    out, rows = models.poison_pool_model(mods, notes, (100.0, 1.0, "main"), 2.0)
    assert _value(out, "AddedGasArrowCloud") == pytest.approx(30.0)
    assert _value(out, "AddedSniperGasArrow") == pytest.approx(30.0)
    assert "UNVERIFIED" in rows[0]["basis"] and "R68-10" in rows[0]["basis"]
    assert models.poison_pool_model(mods, notes, (100.0, 1.0, "main"), 2.0,
                                    {models.POISON_POOL_ENTRY})[0] == mods


def test_minion_ignite_passes_the_legion_gate_when_set(monkeypatch):
    """MECHB69's OVERCOUNT: Conditions.minion_ignite hands MECHB70's build.legion_gate to infernal_legion_ignited."""
    from types import SimpleNamespace

    from builder.search import conditions

    seen = []
    monkeypatch.setattr(models, "infernal_legion_ignited",
                        lambda found, main, stats, gate=None: (seen.append(gate) or (0.4 if gate is None else 0.2, "t")),
                        raising=False)
    cond = conditions.Conditions.__new__(conditions.Conditions)
    cond._minion_inputs = lambda: ([], None, {})
    assert cond.minion_ignite({"Infernal Legion II"})[0] == pytest.approx(0.4)
    cond.build = SimpleNamespace(legion_gate=0.5)
    assert cond.minion_ignite({"Infernal Legion II"})[0] == pytest.approx(0.2)
    assert seen == [None, 0.5]


def test_dropped_full_dps_groups_fill_free_slots_only():
    """MECHB71's raised-limit UNDERCOUNT: PoB's Full DPS keeps one Poison group; the dropped groups fill the free slots."""
    assert models.poison_pool_label("AddedPoisonGroup7") == "Full DPS group 7's Poison"
    assert models.poison_pool_label("AddedThorns") is None
    mods = [models.added_input("PoisonGroup7", 3 * 50.0)]
    notes = [{"poison_pool": ("AddedPoisonGroup7", 50.0, 3.0)}]
    # limit 4, the headline's own 2 Poisons of 300: two slots left for the dropped group's 3 of 50
    out, rows = models.poison_pool_model(mods, notes, (300.0, 2.0, "main"), 4.0)
    assert _value(out, "AddedPoisonGroup7") == pytest.approx(100.0) and rows
    # the headline's own fill the limit (b11: 1.57 x 4 = 6.28 Poisons for 4 slots): nothing added
    out, _ = models.poison_pool_model(mods, notes, (772.0, 4.0, "main"), 4.0)
    assert _value(out, "AddedPoisonGroup7") == pytest.approx(0.0)
