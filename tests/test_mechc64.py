"""MECHC64: Into the Breach's flames and Wildwood's Gifts' Blooms collected only as the build's own movement allows
(RESEARCH61 R61-1; builder/search/remnant_walk.py, assumptions.breach_collection; an OVERCOUNT before: every flame
counted as collected with no walking)."""
import pytest

from builder.search import assumptions, remnant_walk
from builder.search.assumptions import BREACH, Assumptions


def test_pickup_lines_add_and_subtract_and_skip_conditions():
    texts = ["Remnants can be collected from 50% further away", "Remnants can be collected from 20% further away",
             "Remnants must be collected from 10% closer",
             "Remnants can be collected from 30% further away if you have at least 100 Tribute", "Unrelated"]
    pct, used, skipped = remnant_walk.pickup_percent(texts, support=25.0)
    assert pct == pytest.approx(85.0) and len(used) == 3 and len(skipped) == 1


def test_standing_still_collects_only_what_lands_in_reach():
    """No moves: only flames spawning within the pickup radius, (r / 8.5)^2 of them (simulated: within noise)."""
    for r in (1.0, 2.25):
        got = remnant_walk.incidental_share(0.0, 5.8, r, 5.0, 1.21)
        assert got == pytest.approx((r / 8.5) ** 2, abs=0.02)


def test_more_moves_bigger_reach_and_aimed_rolls_collect_more():
    base = remnant_walk.incidental_share(0.3, 5.8, 1.5, 5.0, 1.21)
    assert remnant_walk.incidental_share(0.6, 5.8, 1.5, 5.0, 1.21) > base
    assert remnant_walk.incidental_share(0.3, 5.8, 2.25, 7.5, 1.21) > base
    assert remnant_walk.incidental_share(0.3, 5.8, 1.5, 5.0, 1.21, steer=False) < base
    assert remnant_walk.incidental_share(0.3, 5.8, 1.5, 5.0, 1.21, ring=True) < base
    assert 0.1 < base < 0.8


def test_a_collection_walk_collects_most_for_a_large_share_of_the_fight():
    """R61-1's table: a nearest-flame walk every 20 s and back, 82-88% collected for 35-40% of the fight walking."""
    got, walk = remnant_walk.route_share(1.5, 3.7 * 1.25, 1.21)
    assert 0.75 < got < 0.95 and 0.3 < walk < 0.45
    assert assumptions.collected_share("none", 1.21, 0.3, {"metres": 5.8, "seconds": 0.75}, 4.6, 1.5, 5.0, 25.0) \
        == (0.0, 0.0)


class FakeBuild:
    """A stand-in answering the calls breach_collection makes (the default worst-case boss)."""

    def __init__(self, texts=(), speed=1.25):
        self.texts = list(texts)
        self.speed = speed

    def call(self, method, **kw):
        return {"sourceLines": {"lines": [{"text": t} for t in self.texts]},
                "stats": {"MovementSpeedMod": self.speed, "enemySkillTime": 0},
                "getConfig": {"input": {"purpleFlameStacks": 10}},
                "activeSkills": {"skills": [{"name": BREACH, "disabled": False,
                                             "levelStats": {"breach_flame_spawn_rate_ms": 1210}, "supportStats": {}}]}}[method]


def hook_for(texts=()):
    hook = Assumptions.__new__(Assumptions)
    hook.build = FakeBuild(texts)
    hook.mods = []
    return hook


def test_purple_stacks_count_only_the_flames_collected_on_the_fights_moves():
    """A third Purple of one flame per 1.21 s: 2.2 stacks if every flame were collected (the old reading); now that x the
    simulated share on the fight's own moves (no walking: the collection walk loses on DPS)."""
    hook = hook_for()
    skills = hook.build.call("activeSkills")["skills"]
    walk = hook.breach_collection(skills)
    assert walk["strategy"] == "moves" and walk["walk"] == 0.0 and 0.05 < walk["share"] < 0.9
    mods, notes = hook.purple_flames(skills)
    stacks = assumptions.purple_stacks(1 / 1.21 / 3 * walk["share"])
    assert notes[0]["value"].startswith(f"{stacks:.2f} of 10") and stacks < assumptions.purple_stacks(1 / 1.21 / 3)
    assert not any(m.get("tag") == assumptions.BREACH_WALK_TAG for m in mods)
    assert notes[1]["label"] == "Into the Breach: flames collected*" and "Rows:" in notes[1]["basis"]
    assert "without it" in notes[0]["play"]


def test_pickup_range_lines_raise_the_share_collected():
    skills = FakeBuild().call("activeSkills")["skills"]
    plain = hook_for().breach_collection(skills)["share"]
    wider = hook_for(["Remnants can be collected from 50% further away"]).breach_collection(skills)["share"]
    assert wider > plain


def test_remnant_supply_counts_only_the_flames_collected():
    hook = hook_for()
    hook.main_skill = lambda: None
    hook.build.main_group = lambda: None
    skills = hook.build.call("activeSkills")["skills"]
    share = hook.breach_collection(skills)["share"]
    supply, used = hook.remnant_supply(skills, chance=0.0)
    assert supply == pytest.approx(1 / 1.21 * share) and "collected" in used[0]


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


def test_real_pob_into_the_breach_collects_a_share_and_the_stacks_follow(engine):
    """Real PoB: a bare Into the Breach build collects a share of its flames (0-1, on the default boss's moves), the Purple
    stacks follow it, and a "Remnants can be collected from 50% further away" line raises it."""
    from test_engine_models import spark_with  # (tests/ is on sys.path under pytest)

    search = spark_with(engine, [])
    build = search.build
    build.call("addSkillGroup", text="Into the Breach 20/0 1")
    search.settle()
    first = build.remnant_collection
    note = next(n for n in search.assumptions.active if n["label"] == "Into the Breach: flames collected*")
    print("PROBE collected", first["strategy"], first["share"], note["basis"])
    assert first["strategy"] == "moves" and 0 < first["share"] < 1
    ring = chr(10).join(["Rarity: Rare", "Test Loop", "Iron Ring", "Item Level: 82", "Implicits: 0",
                         "Remnants can be collected from 50% further away"])
    build.call("equip", slot="Ring 1", raw=ring, fields=[])
    build._model_texts = (None, [])
    search.settle()
    print("PROBE wider", build.remnant_collection["share"])
    assert build.remnant_collection["share"] > first["share"]


# A totem main skill's hits aren't the player's (MECHC64; RESEARCH61 R61-9: conditions.TOTEM_TYPES' note)
TOTEM_STATS = {"Speed": 2.0, "HitChance": 100, "CritChance": 10, "_main_types": {"Melee", "Attack"}, "enemySkillTime": 1.0}


def test_a_totem_main_skills_hits_give_you_no_rage():
    from builder.search import conditions as c

    for text in ("Gain 3 Rage on Melee Hit", "Gain 2 Rage on Critical Hit"):
        own = c._rage_gain(text, TOTEM_STATS, True)
        totem = c._rage_gain(text, {**TOTEM_STATS, "TotemLife": 1500.0}, True)
        assert own[0] > 0 and totem[0] == 0.0 and "its totem's, not yours" in totem[2]


def test_a_totem_main_skills_hits_give_you_no_charges():
    from builder.search import conditions as c

    stats = {**TOTEM_STATS, "_main_types": {"Attack", "SummonsTotem"}}
    for text, kind in (("20% chance to gain a Power Charge on Hit", "Power"),
                       ("Gain a Frenzy Charge on Critical Hit", "Frenzy")):
        assert c._charge_rate(text, kind, TOTEM_STATS)[0] > 0
        rate, how = c._charge_rate(text, kind, stats)
        assert rate == 0.0 and "R61-9" in how


def test_real_pob_shockwave_totem_main_gains_no_rage_from_its_hits(engine):
    """Real PoB: a Shockwave Totem main skill (PoB's TotemLife > 0) with "Gain 5 Rage on Melee Hit" and a Rage maximum:
    the reader gives no Rage from the totem's hits (before: the totem's slams at its Speed)."""
    from builder.request.schema import BuildRequest
    from builder.search.context import Context, make_payload
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons
    from builder.search import conditions as c

    payload = make_payload(BuildRequest(main_skill="Shockwave Totem", level=90))
    skeleton = enumerate_skeletons(Context(payload), ["Shockwave Totem"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    ring = chr(10).join(["Rarity: Rare", "Test Loop", "Iron Ring", "Item Level: 82", "Implicits: 0",
                         "Gain 5 Rage on Melee Hit", "+20 to Maximum Rage"])
    build.call("equip", slot="Ring 1", raw=ring, fields=[])
    stats = build.call("stats", fields=c.EVIDENCE_FIELDS)
    print("PROBE totem", stats.get("TotemLife"), stats.get("Speed"), stats.get("MaximumRage"))
    assert c._totem_main({**stats, "_main_types": set()})
    got = c._rage_gain("Gain 5 Rage on Melee Hit", {**stats, "_main_types": {"Melee", "Attack"}}, True)
    print("PROBE rage", got)
    assert got[0] == 0.0 and "its totem's" in got[2]


# R61-4: a Rage-cost use waits for its Rage, then pays in full (conditions.RAGE_COST_FREE's note)
def test_a_rage_cost_use_waits_for_its_rage_and_pays_in_full():
    from builder.search import conditions as c

    out: dict = {}
    # 2 Rage/s gained, a 5-Rage cost asked 1 use/s (5/s): the uses wait most of the time
    mean, how = c.rage_sawtooth(2.0, {"Strike (Rage cost)": 5.0}, 40.0, each={"Strike (Rage cost)": 5.0}, out=out)
    assert out["wait"] > 0.4 and "waits until" in how and mean < 5.0
    out = {}
    c.rage_sawtooth(10.0, {"Strike (Rage cost)": 5.0}, 40.0, each={"Strike (Rage cost)": 5.0}, out=out)
    assert out["wait"] == 0.0  # (gains cover the cost: no waiting)


def test_rage_cost_wait_charges_the_use_rate_scale():
    class Build:
        rage_paid = {"share": 0.0, "asked": 5.0, "paid": 2.0, "cost_share": 0.4}

    hook = Assumptions.__new__(Assumptions)
    hook.build = Build()
    mods, notes = hook.rage_cost_wait()
    assert mods[0]["name"] == "Speed" and mods[0]["value"] == pytest.approx(-60.0) and "R61-4" in notes[0]["basis"]
    Build.rage_paid = {"share": 1.0, "asked": 1.5, "paid": 1.5, "cost_share": 1.0}
    assert hook.rage_cost_wait() == ([], [])
