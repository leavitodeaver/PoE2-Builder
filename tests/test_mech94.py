"""MECH94 (MECH-A): Slow Magnitude ("Debuffs you inflict have N% increased Slow Magnitude"; PoB parses none of it, so
the boss's Slow from the build's Chill / Temporal Chains ignored it: an UNDERCOUNT of the boss's Slow, defence only)."""
import faulthandler

import pytest


def test_slow_magnitude_lines_sum_to_one_pob_mod():
    from builder.search import models

    mods, notes = models.slow_magnitude_mods([
        "Debuffs you inflict have 20% increased Slow Magnitude",
        "Debuffs you inflict have 5% reduced Slow Magnitude",
        "Debuffs inflicted by Hazards have 30% increased Slow Magnitude",  # (Hazards' Debuffs only: not read)
        "Debuffs inflicted with Supported Skills have 25% increased Slow Magnitude"])  # (one skill's: not read)
    assert [(m["name"], m["type"], m["value"]) for m in mods] == [(models.SLOW_MAGNITUDE_MOD, "INC", 15.0)]
    assert notes[0]["confidence"] == "MEDIUM" and "R36-20" in notes[0]["basis"] and "R36-21" in notes[0]["basis"]
    assert models.slow_magnitude_mods(["+10% to Fire Resistance"]) == ([], [])


@pytest.fixture(scope="module")
def engine():
    from builder.data import GameData

    try:
        GameData()
    except FileNotFoundError:
        pytest.skip("no game-data dump")
    from builder.pob.engine import PobEngine

    enabled = faulthandler.is_enabled()
    faulthandler.disable()
    yield PobEngine()
    if enabled:
        faulthandler.enable()


def test_slow_magnitude_scales_the_boss_chill_up_to_its_maximum(engine):
    """A 30% Chill on the default (Unique) boss: 15% slower (MECHD2's half). +50% Slow Magnitude: a 45% Chill slow,
    22.5% slower; +100%: 60%, held at the Chill's 50% maximum (RESEARCH36 R36-20), 25% slower."""
    from builder.search import bosses, models

    slow_taken = bosses.PRESETS[bosses.DEFAULT].slow_taken
    engine.call("newBuild")
    plain = engine.call("setConfig", vars={"conditionEnemyChilled": False}, fields=["enemySkillTime"])["stats"]
    engine.call("setConfig", vars={"conditionEnemyChilled": True, "conditionEnemyChilledEffect": 30})
    got = {}
    for inc in (0, 50, 100):
        mods = models.slow_magnitude_mods([f"Debuffs you inflict have {inc}% increased Slow Magnitude"])[0]
        engine.call("setAssumptions", mods=mods, fields=[])
        got[inc] = engine.call("setConfig", vars={}, fields=["enemySkillTime"])["stats"]["enemySkillTime"]
    print("PROBE slow magnitude", plain["enemySkillTime"], got)
    for inc, chill in ((0, 0.30), (50, 0.45), (100, 0.50)):
        assert got[inc] == pytest.approx(plain["enemySkillTime"] / (1 - chill * slow_taken), rel=1e-3)
    engine.call("setAssumptions", mods=[], fields=[])
    engine.call("setConfig", vars={"conditionEnemyChilled": False, "conditionEnemyChilledEffect": False})


def test_snap_reads_the_boss_ailments_at_the_main_skills_share_of_the_fight():
    """snap_stats (Snap's chance to find an ailment to Consume, from the main skill's hits) carries the main skill's share
    of the fight (assumptions.main_time_share), so its Ignite / Shock / Freeze readers take the hits at that share (an
    OVERCOUNT for builds pressing side skills before: PoB's full Speed)."""
    from types import SimpleNamespace

    from builder.search import assumptions

    class Build:
        objective = SimpleNamespace(press_scales={"Fireball": 0.5}, busy=lambda: 0.0)

        def main_group(self):
            return {"index": 1, "mainActiveSkill": 1, "skills": [{"name": "Fireball"}]}

        def call(self, name, **_):
            return {"Speed": 0.4, "HitChance": 100.0, "IgniteChancePerHit": 20.0, "IgniteDuration": 4.0}

    stats = assumptions.snap_stats(Build())
    assert stats["_main_share"] == 0.5
    full = {k: v for k, v in stats.items() if k != "_main_share"}
    half = {**full, "Speed": 0.2}
    from builder.search.conditions import ailment_uptime

    assert ailment_uptime(stats, "Ignite") == ailment_uptime(half, "Ignite") < ailment_uptime(full, "Ignite")


def test_a_pressed_cooldown_skill_takes_its_cast_not_its_cooldown_from_the_main_skill():
    """PoB's Time is 1 / Speed after the cooldown cap, so a cooldown-capped row's `time` is the whole interval (probe
    POE2T5059: Frost Bomb, 6.006 s cooldown, 0.8 s cast, time 6.006): a press takes the cast time, not 6 s."""
    from builder.search import models

    bomb = {"index": 2, "name": "Frost Bomb", "speed": 1 / 6.006, "cooldown": 6.006, "castTime": 0.8, "time": 6.006,
            "repeats": 1}
    assert models.press_action(bomb) == pytest.approx(0.8)
    scales, _ = models.press_model([{"index": 1, "name": "Spark", "speed": 0.74}, bomb], 1, {2})
    assert scales["Spark"] == pytest.approx(1 - 0.8 / 6.006)
    assert models.press_action({**bomb, "time": 0.4}) == pytest.approx(0.4)  # (a HitTime shorter than the interval)
    assert models.press_action({**bomb, "cooldown": None}) == pytest.approx(6.006)  # (no cap: 1 / speed)


def _bloodhound(bleed):
    from builder.search import models
    from builder.search.models import Skill

    main = Skill("Boneshatter", group=1, attack=True, melee=True, cast_time=1.0)
    mark = Skill("Bloodhound's Mark", group=2, cast_time=0.5, cooldown=0.5)
    blood = Skill(models.BLOOD_EXPLOSION, group=2, attack=True, triggered=True, cast_time=1.0)
    stats = {"BleedDPS": bleed, "HeavyStunBuildupAvg": 5.0, "CritChance": 0, "PhysicalHitAverage": 2000.0,
             "Speed": 2.0, "HitChance": 100}
    return models.mark_model([main, mark, blood], stats, main), stats, (main, mark, blood)


def test_blood_explosion_counts_at_the_marks_activations_not_pobs_flat_one_a_second():
    """PoB scores Blood Explosion at a flat 1 a second (GEM26's sweep: 550 DPS); it fires when Bloodhound's Mark
    Activates on the boss's Heavy Stun, at twice PoB's hit (the full bar's "up to 100% more damage"). The Mark's Blood
    Loss buildup (300% of the Bleed DPS) adds to the stun bar, and the Mark doesn't expire while the boss Bleeds."""
    from builder.search import models

    (mods, notes), stats, skills = _bloodhound(1000.0)
    fill = models.bloodhound_fill(stats)[0]
    assert fill == pytest.approx(1000 * 3.0 * 0.05 / 3000)  # (5% of the bar per 2000 x 1.5 stun-weighted damage)
    stuns = models.heavy_stun_rate({**stats, "_blood_loss_fill": fill})[0]
    assert stuns > models.heavy_stun_rate(stats)[0]
    # (MECH95, R36-19: x(1 + Blood Loss's share of the bar), not a flat x2: the hits build 5% x 2/s = 0.10 of the bar a
    # second, Blood Loss 0.05: a third of it)
    share, _ = models.blood_loss_share(stats)
    assert share == pytest.approx(0.05 / (0.05 + 0.10))
    assert notes[0]["full_dps"][models.BLOOD_EXPLOSION] == pytest.approx(stuns * (1 + share), rel=1e-4)
    # (recast once consumed: one press per Heavy Stun, not every 0.5 s cooldown: 100% of the main skill's time before)
    speed = next(m for m in mods if m["line"] == "1% more Attack and Cast Speed")
    assert speed["scale"] == pytest.approx(-100 * 0.5 * stuns, rel=1e-3)
    (_, notes), stats0, _ = _bloodhound(0.0)  # (no Bleed: the hits' Heavy Stuns only, the Mark expiring after 8 s)
    assert models.blood_loss_share(stats0)[0] == 0  # (no Blood Loss: each explosion at x1)
    assert 0 < notes[0]["full_dps"][models.BLOOD_EXPLOSION] < stuns


def test_the_marks_blood_loss_buildup_doesnt_take_the_hits_stun_buildup_multiplier():
    """RESEARCH36 R36-18: "increased Stun Buildup" (and Melee's 50% more) scale Hits; Blood Loss isn't a Hit. The
    bridge's HeavyStunHitMult (the main skill's own INC x skill-side MORE) comes out of the bar per damage, so a build
    with x1.5 on its hits gets the same Blood Loss buildup a second as one without, and a smaller share of the bar."""
    from builder.search import models

    _, stats, _ = _bloodhound(1000.0)
    plain = models.bloodhound_fill(stats)[0]
    # (the same boss and damage, the hits' buildup x1.5 from the build's Stun Buildup lines)
    boosted = {**stats, "HeavyStunBuildupAvg": 7.5, "HeavyStunHitMult": 1.5}
    assert models.bloodhound_fill(boosted)[0] == pytest.approx(plain)
    assert models.blood_loss_share(boosted)[0] == pytest.approx(0.05 / (0.05 + 0.15))
    assert models.blood_loss_share(boosted)[0] < models.blood_loss_share(stats)[0]


def test_a_mark_never_activates_faster_than_its_event():
    from builder.search import models

    _, stats, (main, mark, blood) = _bloodhound(0.0)
    timer = models.Skill("Bloodhound's Mark", group=2, cast_time=0.5, cooldown=0.5)
    acts, _ = models.mark_activations(timer, [main, timer, blood], stats)
    assert acts <= models.heavy_stun_rate(stats)[0] + 1e-12


def test_blood_explosions_full_dps_entry_is_scaled_in_a_real_build(engine):
    """GEM26's sweep build: Boneshatter with Bloodhound's Mark in Full DPS. PoB's Blood Explosion entry (a flat 1 a
    second) gets mark_model's scale (its Activations x 2), and Boneshatter isn't charged a Mark every 0.5 s."""
    from builder.request.schema import BuildRequest
    from builder.search import models
    from builder.search.context import Context, make_payload
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    payload = make_payload(BuildRequest(main_skill="Boneshatter", ascendancies=["Titan"]))
    skeleton = enumerate_skeletons(Context(payload), ["Boneshatter"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0, full=True)
    search.setup(quests=False)
    build = search.build
    group = build.set_group(gems=[{"name": "Bloodhound's Mark", "level": 20, "quality": 0}], label="mark")
    build.set_group(index=group["index"], fullDPS=True)
    search.assumptions.mods = None
    search.assumptions.apply()
    stats = build.call("stats", fields=build.objective.fields)
    entries = {e["name"]: e["dps"] * (e.get("count") or 1) for e in stats["SkillDPS"]}
    scale = build.objective.entry_scales.get(models.BLOOD_EXPLOSION)
    note = next(n for n in search.assumptions.active if n["label"].startswith("Bloodhound's Mark"))
    print("PROBE bloodhound", entries, scale, note["basis"][:500])
    assert models.BLOOD_EXPLOSION in entries and scale is not None and 0 <= scale < 1
    assert "100% of Boneshatter" not in note["basis"]
    assert "caused by Blood Loss" in note["basis"]  # (MECH95: the share, R36-19)
    # (the press model doesn't charge the Mark's group as a skill pressed nonstop: probe POE2T5070, Boneshatter 0%)
    assert "Bloodhound's Mark" not in (build.objective.press_scales or {})
    assert (build.objective.press_scales or {}).get("Boneshatter", 1.0) > 0.5


def test_ailment_magnitude_lines_raise_chill_not_freeze_buildup(engine):
    """RESEARCH36 R36-9: "Magnitude of Non-Damaging Ailments" raises Chill / Shock Magnitude, not Freeze buildup; PoB
    (ModParser 818-820) gives it EnemyFreezeBuildup too. The probe: PoB's FreezeBuildupAvg with the line moves; after
    our cancel (freeze_magnitude_cancel_mods) it's back to the plain build's, and the Chill part stays."""
    from builder.request.schema import BuildRequest
    from builder.search.context import Context, make_payload
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    payload = make_payload(BuildRequest(main_skill="Frostbolt"))
    skeleton = enumerate_skeletons(Context(payload), ["Frostbolt"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    fields = ["FreezeBuildupAvg"]
    search.assumptions.mods = None
    search.assumptions.apply()
    plain = build.call("stats", fields=fields)
    amulet = chr(10).join(["Rarity: Rare", "Test Amulet", "Gold Amulet", "Item Level: 82", "Implicits: 0",
                           "30% increased Magnitude of Non-Damaging Ailments you inflict"])
    build.call("equip", slot="Amulet", raw=amulet, fields=[])
    build.call("setAssumptions", mods=[], fields=[])
    raw = build.call("stats", fields=fields)
    search.assumptions.mods = None
    search.assumptions.apply()
    fixed = build.call("stats", fields=fields)
    print("PROBE freeze buildup", plain, raw, fixed)
    assert raw["FreezeBuildupAvg"] > plain["FreezeBuildupAvg"] > 0  # (PoB's overcount)
    assert fixed["FreezeBuildupAvg"] == pytest.approx(plain["FreezeBuildupAvg"], rel=1e-3)
    # (the Chill / Shock part stays: the cancel touches EnemyFreezeBuildup only; PoB returns no ChillEffectMod here)
    from builder.search import models

    mods = models.freeze_magnitude_cancel_mods(["30% increased Magnitude of Non-Damaging Ailments you inflict"])[0]
    assert {m["name"] for m in mods} == {"EnemyFreezeBuildup"}
    # (MECH95) the "per 10 Devotion" form: PoB applies it at the player's own Devotion (ModStore's PerStat, actor
    # "parent" read as the player); the cancel takes it back at floor(Devotion / 10)
    amulet = chr(10).join(["Rarity: Rare", "Test Amulet", "Gold Amulet", "Item Level: 82", "Implicits: 0",
                           "+10 to Devotion", "30% increased Magnitude of Non-Damaging Ailments you inflict per 10 Devotion"])
    build.call("equip", slot="Amulet", raw=amulet, fields=[])
    build.call("setAssumptions", mods=[], fields=[])
    raw = build.call("stats", fields=fields + ["Devotion"])
    search.assumptions.mods = None
    search.assumptions.apply()
    fixed = build.call("stats", fields=fields)
    print("PROBE freeze buildup per Devotion", raw, fixed)
    assert raw["Devotion"] == pytest.approx(10)
    assert raw["FreezeBuildupAvg"] == pytest.approx(plain["FreezeBuildupAvg"] * 1.30, rel=1e-3)  # (PoB: +30% x 1)
    assert fixed["FreezeBuildupAvg"] == pytest.approx(plain["FreezeBuildupAvg"], rel=1e-3)


def test_freeze_magnitude_cancel_reads_each_form():
    from builder.search import models

    mods, notes = models.freeze_magnitude_cancel_mods([
        "30% increased Magnitude of Non-Damaging Ailments you inflict",
        "20% increased Magnitude of Non-Damaging Ailments you inflict with Critical Hits",
        "3% increased Magnitude of Non-Damaging Ailments you inflict per 10 Devotion"])  # (no Devotion given: 0)
    assert [(m["value"], m.get("condition")) for m in mods] == [(-30.0, None), (-20.0, "CriticalStrike")]
    # (MECH95: the Devotion form at PoB's floor(Devotion / 10): 57 Devotion -> 5 x 3%)
    mods, _ = models.freeze_magnitude_cancel_mods(
        ["3% increased Magnitude of Non-Damaging Ailments you inflict per 10 Devotion"], devotion=57)
    assert [(m["value"], m.get("condition")) for m in mods] == [(-15.0, None)]
    assert models.freeze_magnitude_cancel_mods(["30% increased Chill Magnitude"]) == ([], [])


def test_a_creeping_chill_ground_under_the_chill_floor_doesnt_chill_the_boss():
    """R36-4: the ground stays (on-Chilled-Ground lines), but under the 30% floor it doesn't make the boss Chilled."""
    from builder.search import conditions as c

    ground = (0.6, "Creeping Chill on Frost Wall")
    assert c.chilled_share({"_chilled_ground": ground}) == pytest.approx(0.6)
    assert c.chilled_share({"_chilled_ground": ground, "_chilled_ground_chills": (0.0, "under 30%")}) == 0.0


def test_creeping_chill_at_base_crystal_life_is_under_the_floor_on_a_pinnacle(engine):
    """R36-4's numbers through PoB: Frost Wall's crystal Life against the boss's ailment threshold, under 30% at base."""
    from builder.request.schema import BuildRequest
    from builder.search import models
    from builder.search.context import Context, make_payload
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    payload = make_payload(BuildRequest(main_skill="Frost Wall"))
    skeleton = enumerate_skeletons(Context(payload), ["Frost Wall"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    stats = search.build.call("stats", fields=models.MODEL_FIELDS)
    got = search.conditions._creeping_chill_magnitude(["Frost Wall"], stats)
    print("PROBE creeping chill magnitude", got, stats.get("EnemyAilmentThreshold"))
    magnitude, how = got["Frost Wall"]
    assert magnitude is not None and 0 < magnitude < 30 and "Life" in how


def test_repulsions_hold_counts_the_minions_hits_too():
    """RESEARCH36 R36-2: minions' hits remove Repulsion as well ("Hitting these enemies"): their hits shorten the hold."""
    import math

    from builder.search import models

    gems = {"Repulsion": {"skillTypes": ["Spell", "AppliesCurse"], "castTime": 0.5}}
    hand = [{"enabled": True, "gems": [{"name": "Repulsion"}]}]
    stats = {"Speed": 2.0, "HitChance": 100.0, "Ward": 500.0, "PresenceRadius": 40}
    own = models.curse_uses(hand, gems, stats, 2.0)[0]
    with_minions = models.curse_uses(hand, gems, {**stats, "_repulsion_minion_hits": (3.0, "3 minions x 1.00")}, 2.0)[0]
    d = models.CURSE_DURATION
    hold = (1 - math.exp(-5.0 * d)) / 5.0
    assert with_minions.share == pytest.approx(hold / (hold + 0.5 + 1.5))
    assert with_minions.share < own.share and "3 minions" in with_minions.how


def test_temporal_chains_curse_and_slow_magnitude_add(engine):
    """RESEARCH36 R36-21 (HIGH): Temporal Chains' curse magnitude and slow magnitude ADD (GGG's 3 Apr 2025 correction
    of the 0.2.0 notes). With +50% curse effect and +50% Slow Magnitude the boss's Slow from the curse is x(1 + 0.5 +
    0.5) / (1 + 0.5) of the curse-only one (MECH94 multiplied them: x1.5, an OVERCOUNT of the boss's Slow)."""
    from builder.search import models

    engine.call("newBuild")
    engine.call("setAssumptions", mods=[], fields=[])
    engine.call("setGroup", gems=[{"name": "Frostbolt", "level": 20, "quality": 0}], main=True)
    # (the boss's own speed, no curse: POE2T5079 measured "plain" with the curse on, which isn't a baseline)
    plain = engine.call("setConfig", vars={"conditionEnemyChilled": False}, fields=["enemySkillTime"])["stats"][
        "enemySkillTime"]
    engine.call("setGroup", gems=[{"name": "Temporal Chains", "level": 20, "quality": 0}])
    curse = [models.raw_mod("CurseEffect", "INC", 50)]
    slow = models.slow_magnitude_mods(["Debuffs you inflict have 50% increased Slow Magnitude"])[0]
    got = {}
    for name, mods in (("curse", curse), ("both", curse + slow)):
        engine.call("setAssumptions", mods=mods, fields=[])
        got[name] = engine.call("setConfig", vars={}, fields=["enemySkillTime"])["stats"]["enemySkillTime"]
    print("PROBE temporal chains", plain, got)
    engine.call("setAssumptions", mods=[], fields=[])
    # (no Chill or other Slow here: the speed is 1 - the curse's Slow at the boss's potency, above the 25% floor)
    slowed = {k: 1 - plain / v for k, v in got.items()}
    assert 0 < slowed["curse"] < slowed["both"] < 0.75
    assert slowed["both"] / slowed["curse"] == pytest.approx(2.0 / 1.5, rel=2e-3)
