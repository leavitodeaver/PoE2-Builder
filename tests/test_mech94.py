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
    assert notes[0]["confidence"] == "MEDIUM" and "UNVERIFIED" in notes[0]["basis"]
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
    22.5% slower; +100%: 60%, held at the Chill's 50% maximum (UNVERIFIED past it), 25% slower."""
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
    assert notes[0]["full_dps"][models.BLOOD_EXPLOSION] == pytest.approx(stuns * 2.0, rel=1e-4)
    # (recast once consumed: one press per Heavy Stun, not every 0.5 s cooldown: 100% of the main skill's time before)
    speed = next(m for m in mods if m["line"] == "1% more Attack and Cast Speed")
    assert speed["scale"] == pytest.approx(-100 * 0.5 * stuns, rel=1e-3)
    (_, notes), _, _ = _bloodhound(0.0)  # (no Bleed: the hits' Heavy Stuns only, the Mark expiring after 8 s)
    assert 0 < notes[0]["full_dps"][models.BLOOD_EXPLOSION] < stuns * 2.0


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
    # (the press model doesn't charge the Mark's group as a skill pressed nonstop: probe POE2T5070, Boneshatter 0%)
    assert "Bloodhound's Mark" not in (build.objective.press_scales or {})
    assert (build.objective.press_scales or {}).get("Boneshatter", 1.0) > 0.5
