"""MECHC57's models (MECH-C): Rage regeneration from PoB's total (conditions.RAGE_REGEN_FIELD's note): Eternal Rage and
Furious Wellspring's Mana Regeneration increases were unread (UNDERCOUNTS; COV52 B1, RESEARCH53 R53-4)."""
import pytest

from builder.search import conditions as c

STATS = {"Speed": 2.0, "HitChance": 100, "MaximumRage": 40, "_main_types": {"Attack"}}
WELLSPRING = "Regenerate 6% of your maximum Rage per second"
PRIMAL = "Regenerate 1 Rage per second per 4 Rage spent Recently"


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
    faulthandler.disable()  # LuaJIT's Lua errors are Windows exceptions PoB's pcall catches
    yield PobEngine()
    if enabled:
        faulthandler.enable()


def test_regeneration_with_no_text_line_holds_the_maximum():
    """Eternal Rage has no line: PoB's total is the gain, and regeneration stops the decay (patch 0.4.0)."""
    held, how = c.rage_held([], {**STATS, "RageRegenRecovery": 4.5})
    assert held == 40 and "never decays" in how
    assert c.rage_held([], dict(STATS)) == (0.0, "")


def test_a_regeneration_line_is_not_counted_twice_beside_pobs_total():
    stats = {**STATS, "RageRegenRecovery": 3.6, "_rage_spend": {"Spender": 3.0}}
    held, how = c.rage_held([WELLSPRING], stats)
    assert held == 40 and "3.60 Rage/s gained" in how  # (2.4 from the line would be 6.0 if added)


def test_regeneration_below_the_spend_holds_none():
    assert c.rage_held([], {**STATS, "RageRegenRecovery": 3.0, "_rage_spend": {"Spender": 5.0}})[0] == 0


def test_primal_hunger_keeps_the_line_reader():
    """PoB reads "per 4 Rage spent Recently" as per 4 Rage held, so its total isn't used beside that line."""
    assert c.rage_regen_total([PRIMAL], {**STATS, "RageRegenRecovery": 9.0}) == 0
    assert c.rage_held([PRIMAL], {**STATS, "RageRegenRecovery": 9.0})[0] == 0  # (a refund with nothing gained)


def test_a_channel_counts_regeneration_only_outside_it():
    """The manager's ruling (R55-8): headline outside the channel; "works during the channel" is the row."""
    stats = {**STATS, "RageRegenRecovery": 8.0, "_main_channel_rage": True, "_rage_spend": {"Rampage": 5.0}}
    held, how = c.rage_held([], dict(stats))  # (the channel all fight: no regeneration, nothing held)
    assert held == 0 and "UNVERIFIED" in how and "8.00 Rage/s gained, 3.00/s net" in how
    held, how = c.rage_held([], {**stats, "_main_share": 0.25, "_rage_spend": {"Rampage": 1.25}})
    assert held == 40 and "6.00 Rage/s gained" in how and "8.00 Rage/s gained, 6.75/s net" in how
    assert c.rage_held([], {**stats, "_main_channel_rage": False})[0] == 40  # (no channel: regeneration counts)


def test_glory_decays_only_while_banked_past_15_seconds_without_a_gain():
    from builder.search import models as m

    assert m.glory_after_decay(2.0, 1.0, 200) == 2.0  # (gains 1 s apart: no decay)
    assert m.glory_after_decay(20 / 30, 1 / 30, 20) == pytest.approx(20 / 30)  # (one gain pays for a use: nothing banked)
    # 100 a gain, 30 s apart, 200 a use: 100, loses 30 -> 70, +100 = 170, loses 30 -> 140, +100 = 240 >= 200: 3 gains
    assert m.glory_after_decay(100 / 30, 1 / 30, 200) == pytest.approx(200 / 90)
    assert m.glory_after_decay(10 / 60, 1 / 60, 200) == 0.0  # (each gain decays away before the next)


SNIPER = ("Mark a target. The next Critical Hit the target receives Activates the Mark, Consuming it to deal extra "
          "damage and grant you a Frenzy Charge.")
FRENZY = ("While active, grants you a Frenzy Charge when you Freeze, Electrocute or Pin an enemy. This can only occur once "
          "every few seconds.")


def side(hits, crits):
    return {"name": "Arc", "hits": hits, "crits": crits, "melee": False, "flagged": False, "how": "Arc"}


def test_snipers_mark_takes_the_other_skills_crits():
    """R53-4, build 7: a triggered main skill reads Speed 0; Arc's crits Activate the Mark at its re-applied rate."""
    base = {"Speed": 0.0, "HitChance": 100, "CritChance": 100, "enemySkillTime": 1.0, "_mark_uses": 0.1665}
    assert c._charge_rate(SNIPER, "Frenzy", base)[0] == 0
    assert c._charge_rate(SNIPER, "Frenzy", {**base, "_side_rows": [side(1.8, 1.2)]})[0] == pytest.approx(0.1665)
    assert c._charge_rate(SNIPER, "Frenzy", {**base, "_side_rows": [side(0.1, 0.05)]})[0] == pytest.approx(0.05)
    assert c._charge_rate(SNIPER, "Frenzy", {**base, "_mark_uses": 0.0, "_side_rows": [side(1.8, 1.2)]})[0] == 0


def test_a_consume_that_grants_a_charge_is_a_source_of_it():
    box = next(x for x in c.CONDITIONS if x.var == "useFrenzyCharges")
    assert c.text_evidence(box, SNIPER)
    assert not c.text_evidence(box, "Consumes a Frenzy Charge to deal more damage")
    assert not c.text_evidence(box, "Gain 10% more Damage per Frenzy Charge")


def test_combat_frenzy_is_held_to_its_cooldown(monkeypatch):
    """Its description gives no number: the level stat caps it (an OVERCOUNT on a build that Freezes often)."""
    monkeypatch.setattr(c.models, "immobilise_rate", lambda stats, events: (2.0, "2 Freezes a second"))
    stats = {"Speed": 1.0, "HitChance": 100, "CritChance": 0, "enemySkillTime": 1.0}
    assert c._charge_rate(FRENZY, "Frenzy", stats)[0] == 2.0  # (no stat read: a stand-in)
    rate, how = c._charge_rate(FRENZY, "Frenzy", {**stats, "_combat_frenzy_every": 6.1})
    assert rate == pytest.approx(1 / 6.1) and "6.1 s" in how
    monkeypatch.setattr(c.models, "immobilise_rate", lambda stats, events: (0.02, "rare Freezes"))
    assert c._charge_rate(FRENZY, "Frenzy", {**stats, "_combat_frenzy_every": 6.1})[0] == 0.02


def test_probe_eternal_rage_regenerates_in_real_pob(engine):
    """Real PoB: Eternal Rage at level 20 gives RageRegenRecovery 5/s (300 a minute) and a Rage bar that stays full."""
    from builder.request.schema import BuildRequest
    from builder.search.context import Context, make_payload
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    payload = make_payload(BuildRequest(main_skill="Boneshatter", ascendancies=["Titan"]))
    skeleton = enumerate_skeletons(Context(payload), ["Boneshatter"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    before = build.call("stats", fields=["RageRegenRecovery"]).get("RageRegenRecovery") or 0
    build.set_group(gems=[{"name": "Eternal Rage", "level": 20, "quality": 0}])
    for _ in range(2):
        search.settle()
    got = build.call("stats", fields=["RageRegenRecovery", "MaximumRage"])
    print("PROBE Eternal Rage", before, got)
    assert before == 0 and got["RageRegenRecovery"] == pytest.approx(5.0, rel=0.3) and got["MaximumRage"] > 0
    held, how = c.rage_held([], {**STATS, **got})
    print("PROBE held", held, how)
    assert held == int(got["MaximumRage"])
    box = search.conditions.active.get("multiplierRage")
    print("PROBE box", box)
    assert box and float(box["value"]) == int(got["MaximumRage"])
