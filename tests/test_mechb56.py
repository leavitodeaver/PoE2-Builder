"""MECHB56 (MECH-B): the uses-raise tags (MECH100's models.USES_RAISE) on Explosive Rhythm, the Last Lament and Forge
Hammer's loop; see each model's note."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from builder.search import assumptions, models
from builder.search.models import Skill

LAMENT = "Sacrifice 300 Life to not consume the last bolt when firing"
BOW = {"BoltCount": 6, "FiringRate": 3.0, "Speed": 2.0, "EffectiveReloadTime": 1.0, "ChanceToNotConsumeAmmo": 0,
       "Life": 2000}


def _raise(mods: list[dict], name: str) -> float | None:
    build = SimpleNamespace()
    assumptions.hit_cut(build, mods, name)
    assert build.main_hit_cut is None  # (never a time cut: the raise isn't clamped to 1)
    return build.main_uses_raise


def test_forge_hammers_loop_raises_its_uses_for_the_readers():
    hammer = Skill("Forge Hammer", 1, attack=True, melee=True)
    mods, _ = models.forge_hammer_loop_model(hammer, 0.125, 1.0, 8.0, 0.0)
    factor = 1 + mods[0]["value"] / 100
    assert mods[0][models.USES_RAISE] == pytest.approx(factor, rel=1e-3) and factor > 5
    assert _raise(mods, "Forge Hammer") == pytest.approx(factor, rel=1e-3)
    assert not models.forge_hammer_loop_model(hammer, 0.125, 1.0, 8.0, 0.2)[0]  # (a Warcry: no mod, no raise)


def test_the_last_laments_skipped_reloads_raise_its_uses():
    shot = Skill("Galvanic Shards", 1, attack=True)
    mods, _ = models.last_bolt_sacrifice_model(shot, [LAMENT], BOW, (5000.0, 0.0, 0.0, 0.0, []))
    assert mods[0][models.USES_RAISE] == pytest.approx(1 + mods[0]["value"] / 100, rel=1e-3) == pytest.approx(1.5)
    assert _raise(mods, "Galvanic Shards") == pytest.approx(1.5)


def test_explosive_rhythms_fervour_uses_are_tagged_without_its_extra_projectile():
    main = Skill("Frag Grenade", group=1)
    texts = ["Gain 1 Explosive Rhythm every 3 times you use a Grenade Skill",
             "Remove all Explosive Rhythm on reaching 10 to gain Explosive Fervour for 10 Seconds"]
    row = {"speed": 0.5, "cooldown": 2.0, "castRate": 1.5, "repeats": 1}
    mods, _ = models.explosive_rhythm_model(main, texts, row, 1.0, 1, 1)
    build = sum(3 / min(1.5, 0.5 * (1 + 0.1 * s)) for s in range(10))
    uses = (30 + 10 * 1.5) / ((build + 10) * 0.5)
    assert 1 < uses < 1 + mods[0]["value"] / 100  # (the extra projectile is hits a use: in the DPS value only)
    assert mods[0][models.USES_RAISE] == pytest.approx(uses, rel=1e-3)
    assert _raise(mods, "Frag Grenade") == pytest.approx(uses, rel=1e-3)
    slow = {"speed": 1.5, "cooldown": 0.0, "castRate": 1.5, "repeats": 1}  # (no cooldown: Fervour adds no uses)
    mods, _ = models.explosive_rhythm_model(main, texts, slow, 1.0, 1, 1)
    assert models.USES_RAISE not in mods[0]


AVATAR = ["When you Consume a Charge, Trigger Elemental Surge to gain 3 Lightning Surges",
          "When you Consume a Charge, Trigger Elemental Surge to gain 2 Fire Surges",
          "When you Consume a Charge, Trigger Elemental Surge to gain 2 Cold Surges",
          "Gain 1 fewer Lightning Surge from Triggering Elemental Surge"]
SPIRE = "When you Consume a Charge Trigger Chaotic Surge to gain 2 Chaos Surges"
HITS = {"Cold": 100.0, "Fire": 200.0, "Lightning": 300.0, "Chaos": 400.0}
BOTH = {models.ELEMENTAL_SURGE, models.CHAOTIC_SURGE}


def test_surge_lines_read_with_surging_avatars_one_fewer_lightning():
    assert models.surge_gains(AVATAR[:1]) == {"Lightning": 3.0}
    assert models.surge_gains(AVATAR) == {"Lightning": 2.0, "Fire": 2.0, "Cold": 2.0}
    assert models.surge_gains([SPIRE]) == {"Chaos": 2.0}
    assert models.surge_gains(["Gain 1 fewer Lightning Surge from Triggering Elemental Surge"]) == {}


def test_charge_surges_blast_at_the_uses_the_supply_allows():
    gains = models.surge_gains(AVATAR[:1])
    # 0.1 charges/s x 3 Lightning = 0.3 Surges/s against 2 uses/s: 0.3 blasts/s x 300
    mods, notes = models.charge_surge_model(gains, 0.1, "Frenzy", 2.0, "2 uses", 1, HITS, {}, False, BOTH)
    assert models.added_total(mods) == pytest.approx(90.0, abs=0.01)
    assert notes[0]["full_dps"] == {models.ELEMENTAL_SURGE: 0.0} and notes[0]["entry"] == models.SURGE_ENTRY
    # supply above the uses: one a use (the headline), Lightning before Chaos
    mods, _ = models.charge_surge_model({"Lightning": 3.0, "Chaos": 2.0}, 1.0, "Frenzy", 2.0, "2 uses", 1, HITS, {},
                                        False, BOTH)
    assert models.added_total(mods) == pytest.approx(2.0 * 300.0, abs=0.01)
    mods, _ = models.charge_surge_model({"Lightning": 3.0, "Chaos": 2.0}, 0.5, "Frenzy", 2.0, "2 uses", 1, HITS, {},
                                        False, BOTH)
    assert models.added_total(mods) == pytest.approx(1.5 * 300.0 + 0.5 * 400.0, abs=0.01)
    # a batch above the cap: at most 6 a type an event
    mods, _ = models.charge_surge_model({"Cold": 9.0}, 0.1, "x", 10.0, "x", 1, HITS, {}, False, BOTH)
    assert models.added_total(mods) == pytest.approx(0.6 * 100.0, abs=0.01)


def test_charge_surges_need_a_spender_a_qualifying_use_and_a_stopping_projectile():
    gains = models.surge_gains([SPIRE])
    for charges, uses, pierce in ((0.0, 2.0, False), (1.0, 0.0, False), (1.0, 2.0, True)):
        mods, notes = models.charge_surge_model(gains, charges, "x", uses, "x", 1, HITS, {}, pierce, BOTH)
        assert not mods and notes[0]["value"] == 0 and notes[0]["full_dps"] == {models.CHAOTIC_SURGE: 0.0}
    assert models.charge_surge_model(gains, 1.0, "x", 2.0, "x", 1, HITS, {}, False, {models.ELEMENTAL_SURGE}) == ([], [])
    assert models.charge_surge_model(gains, 1.0, "x", 2.0, "x", 1, HITS, {}, False, BOTH,
                                     {models.SURGE_ENTRY}) == ([], [])


def test_midnight_crystal_life_reads_the_builds_increases_and_the_amulets_cold_resistance_line():
    """RESEARCH48 R48-1: PoB's Crystal-set Life (the build's increases in it) plus Eventide Petals' unparsed line, added
    to the same increases."""
    line = "Ice Crystals have 3% reduced maximum Life per 5% Cold Resistance you have"
    assert models.midnight_crystal_life(0.0, 4768.0, [], 75.0)[0] == 4768.0
    assert models.midnight_crystal_life(4768.0 * 0.4, 4768.0, [], 75.0)[0] == pytest.approx(4768.0 * 0.4)
    life, how = models.midnight_crystal_life(0.0, 4768.0, [line], 75.0)
    assert life == pytest.approx(4768.0 * 0.55) and "x 15" in how
    up = "Ice Crystals have 3% increased maximum Life per 5% Cold Resistance you have"
    assert models.midnight_crystal_life(4768.0 * 3, 4768.0, [up], 74.0)[0] == pytest.approx(4768.0 * (3 + 0.42))
    neg = "Ice Crystals have -3% reduced maximum Life per 5% Cold Resistance you have"
    assert models.midnight_crystal_life(0.0, 4768.0, [neg], 75.0)[0] == pytest.approx(4768.0 * 1.45)
