"""The "How to play it" guide (builder/search/play_guide.py) on a fake build: no PoB needed."""

from types import SimpleNamespace

from pytest import approx

from builder.report.checklist import checklist
from builder.search import play_guide


class FakeBuild:
    """DPS from the config: x1.25 at 2 m (Presence), x1.10 standing still, x1.50 on a Low Life boss."""

    def __init__(self, config):
        self.config = dict(config)
        self.objective = SimpleNamespace(dps=lambda stats: stats["dps"])

    @property
    def stats(self):
        dps = 1000.0
        if self.config.get("enemyDistance") == 20:
            dps *= 1.25
        if self.config.get("conditionStationary"):
            dps *= 1.10
        if self.config.get("conditionEnemyLowLife"):
            dps *= 1.50
        if self.config.get("conditionBeenHitRecently"):
            dps *= 1.001  # below MIN_CHANGE: no row
        return {"dps": dps}

    def set_config(self, values):
        self.config.update(values)

    def call(self, method, **kwargs):
        assert method == "getConfig"
        return {"input": dict(self.config)}


def search_with(config, active):
    build = FakeBuild(config)
    return SimpleNamespace(build=build, conditions=SimpleNamespace(active=active),
                           assumptions=SimpleNamespace(apply=lambda: False))


def test_rows_for_player_and_fight_conditions_and_build_left_as_it_was():
    active = {"enemyDistance": {"label": "Boss distance", "value": 20},
              "conditionBeenHitRecently": {"label": "Been hit", "value": True},
              "conditionEnemyShocked": {"label": "Shocked", "value": True}}  # build-applied: no row
    search = search_with({"enemyDistance": 20, "conditionBeenHitRecently": True, "conditionEnemyShocked": True}, active)
    rows = play_guide.guide(search)
    by_var = {r["var"]: r for r in rows}
    assert set(by_var) == {"enemyDistance", "conditionStationary", "conditionEnemyLowLife"}
    distance = by_var["enemyDistance"]
    assert distance["counted"] and distance["kind"] == "player"
    assert distance["dps_with"] == approx(1251.25) and distance["dps_without"] == approx(1001) and "at 5 m instead" in distance["label"]
    assert distance["play"] == "stay within 2 m of the boss"
    low = by_var["conditionEnemyLowLife"]
    assert not low["counted"] and low["kind"] == "fight" and low["dps_with"] == approx(1876.875) and low["dps_without"] == approx(1251.25)
    assert rows[0]["var"] == "conditionEnemyLowLife"  # largest change first
    assert search.build.config["enemyDistance"] == 20 and not search.build.config["conditionStationary"]
    items = [i for i in checklist({"play_guide": rows}) if i["kind"] == "how to play it *"]
    assert len(items) == 3 and all(i["title"].endswith("*") for i in items)


def test_every_classified_condition_is_known_and_build_is_the_default():
    assert play_guide.kind("conditionEnemyIgnited") == play_guide.BUILD
    assert play_guide.kind("enemyDistance") == play_guide.PLAYER


def test_distance_candidates_follow_the_build_lines():
    from builder.search.conditions import distance_candidates

    assert distance_candidates(["10% increased Spell Damage"], {"Spell"}) == []
    assert distance_candidates(["Enemies in your Presence have -10% to Fire Resistance"], {"Spell"}) == [5.0, 2.0]
    assert distance_candidates(["20% more Damage against Enemies further than 6m"], {"Spell"}) == [5.0, 2.0, 6.5]
    assert distance_candidates(["Enemies in your Presence are Blinded"], {"Melee", "Attack"}) == []  # already close
    # MECHC1: a build standing back also tries the edge of its Presence (Presence Area of Effect decides it), between
    # close range and its natural distance; not for melee, nor when the Presence already reaches the natural distance
    presence = ["Enemies in your Presence have -10% to Fire Resistance"]
    assert distance_candidates(presence, {"Spell"}, 4.47) == [5.0, 2.0, 4.4]
    assert distance_candidates(presence, {"Spell"}, 5.6) == [5.0, 2.0]
    assert distance_candidates(presence, {"Melee", "Attack"}, 4.4) == []
    assert distance_candidates(["20% more Damage against Enemies further than 6m"], {"Spell"}, 4.4) == [5.0, 2.0, 6.5]


class FakeObjective:
    def __init__(self):
        self.life_factor = 1.0

    def dps(self, stats):
        return stats["dps"] * self.life_factor

    def score(self, stats):
        return self.dps(stats)


class FakeSearchBuild(FakeBuild):
    def __init__(self, config, lines):
        super().__init__(config)
        self.objective = FakeObjective()
        self.lines = lines
        self.score = 0.0

    def call(self, method, **kwargs):
        if method == "sourceLines":
            return {"lines": [{"source": "tree", "text": t} for t in self.lines]}
        return super().call(method, **kwargs)


def fake_search(lines, notes=()):
    from builder.search.inner import Search

    search = SimpleNamespace(build=FakeSearchBuild({}, lines), play_notes={},
                             assumptions=SimpleNamespace(apply=lambda: False, active=list(notes)),
                             conditions=SimpleNamespace(stationary_off=None, rolling=0.0, active={}), settle=lambda: False)
    for name in ("weigh_life", "choose_stationary", "rolling_share"):
        setattr(search, name, getattr(Search, name).__get__(search))
    return search


def test_low_life_is_weighted_by_its_share_of_the_boss_life():
    search = fake_search(["30% more Damage against Enemies on Low Life"])
    search.weigh_life()
    # 1000 above 35%, 1500 below: 1 / (0.65 + 0.35 / 1.5)
    assert search.build.objective.life_factor == approx(1 / (0.65 + 0.35 / 1.5))
    assert search.build.score == approx(1000 / (0.65 + 0.35 / 1.5))
    assert not search.build.config["conditionEnemyLowLife"] and "low_life" in search.play_notes
    rows = play_guide.guide(SimpleNamespace(build=search.build, conditions=SimpleNamespace(active={}),
                                            assumptions=search.assumptions))
    low = next(r for r in rows if r["var"] == "conditionEnemyLowLife")
    assert low["counted"] and low["dps_with"] == approx(1500) and low["dps_without"] == approx(1000)
    assert search.build.objective.life_factor == approx(1 / (0.65 + 0.35 / 1.5))  # put back after the trials
    plain = fake_search(["10% increased Spell Damage"])
    plain.weigh_life()
    assert plain.build.objective.life_factor == 1.0 and not plain.play_notes


def test_dodge_rolls_keep_a_build_from_counting_standing_still():
    rolling = fake_search(["20% more Damage while Stationary"],
                          [{"label": "Time spent dodge rolling (Spark)", "value": "37.5% less Attack and Cast Speed"}])
    assert rolling.rolling_share() == approx(0.375)
    rolling.choose_stationary()
    assert rolling.conditions.stationary_off == "dodge rolls" and "dodge rolls 38%" in rolling.play_notes["stationary"]["basis"]


def test_standing_still_is_on_for_a_build_with_stationary_lines():
    from builder.search.conditions import _standing_still

    assert _standing_still(["20% more Damage while Stationary"], {})[0] is True
    assert _standing_still(["20% more Damage while Stationary"], {"_stationary_off": "dodge rolls"})[0] == 0
    assert _standing_still(["10% increased Spell Damage"], {})[0] == 0


class UptimeBuild(FakeBuild):
    """Shocked: x1.2 DPS; Intimidated: no DPS, but 500 more Life (a defence that leans on a debuff)."""

    @property
    def stats(self):
        return {"dps": 1200.0 if self.config.get("conditionEnemyShocked") else 1000.0,
                "Life": 2000.0 if self.config.get("conditionEnemyIntimidated") else 1500.0}


class UptimeObjective(FakeObjective):
    def __init__(self):
        super().__init__()
        self.uptime_factor, self.floor_deltas = 1.0, []
        self.penalties = [SimpleNamespace(stat="Life", label="Life")]

    def dps(self, stats):
        return stats["dps"] * self.life_factor * self.uptime_factor


def test_partial_uptimes_count_at_their_share_and_floors_without_them():
    """The user, 9 Nov 2026: "Count the real share". D x product of (u + (1 - u) x D(off) / D); the floors checked off."""
    from builder.search.inner import Search

    config = {"conditionEnemyShocked": True, "conditionEnemyIntimidated": True}
    build = UptimeBuild(config)
    build.objective, build.score = UptimeObjective(), 0.0
    active = {"conditionEnemyShocked": {"label": "Shocked", "value": True},
              "conditionEnemyIntimidated": {"label": "Intimidated", "value": True}}
    search = SimpleNamespace(build=build, play_notes={}, uptime_rows=[], assumptions=SimpleNamespace(apply=lambda: False),
                             conditions=SimpleNamespace(active=active, uptimes={"conditionEnemyShocked": 0.5,
                                                                                "conditionEnemyIntimidated": 0.8}))
    search.floor_without = Search.floor_without.__get__(search)
    Search.weigh_uptimes.__get__(search)()
    assert build.objective.uptime_factor == approx(0.5 + 0.5 * 1000 / 1200)
    assert build.score == approx(1100)
    assert build.config == config  # put back
    # the floors: every part-time effect off at once (the user, 9 Nov: a floor counts one only at full uptime)
    assert build.objective.floor_deltas == [{"Life": -500.0, "dps": -200.0}]
    intimidated = next(r for r in search.uptime_rows if r["var"] == "conditionEnemyIntimidated")
    assert "1,900 time-weighted" in intimidated["defence"] and "uptime:conditionEnemyShocked" in search.play_notes
    rows = {r["var"]: r for r in play_guide.guide(search)}
    shock = rows["conditionEnemyShocked"]
    assert shock["uptime"] == 0.5 and shock["dps_with"] == approx(1100) and shock["dps_without"] == approx(1000)
    assert shock["dps_share"] == approx(100 / 1100) and "conditionEnemyIntimidated" not in rows  # no DPS: no row
    item = next(i for i in checklist({"play_guide": [shock]}) if i["kind"] == "how to play it *")
    assert "50% of the fight" in item["why"] and "9% of the headline" in item["why"]


def test_another_reading_of_an_unsettled_uptime_gets_its_own_row():
    """MECHC1: the Arcane Surge support's counter is unsettled (RESEARCH1, LOW); the headline counts the lower uptime and
    the play guide gives the DPS at the other one, not counted."""
    from builder.search.inner import Search

    config = {"conditionEnemyShocked": True}
    build = UptimeBuild(config)
    build.objective, build.score = UptimeObjective(), 0.0
    active = {"conditionEnemyShocked": {"label": "Arcane Surge", "value": True}}
    search = SimpleNamespace(build=build, play_notes={}, uptime_rows=[], assumptions=SimpleNamespace(apply=lambda: False),
                             conditions=SimpleNamespace(active=active, uptimes={"conditionEnemyShocked": 0.5},
                                                        alt_uptimes={"conditionEnemyShocked": (1.0, "if it counts")}))
    search.floor_without = Search.floor_without.__get__(search)
    Search.weigh_uptimes.__get__(search)()
    rows = [r for r in play_guide.guide(search) if r["var"] == "conditionEnemyShocked"]
    counted, other = rows
    assert counted["counted"] and counted["dps_with"] == approx(1100)
    assert not other["counted"] and other["alt_uptime"] == 1.0 and other["dps_with"] == approx(1200)
    assert other["dps_without"] == approx(1100) and "if it counts" in other["play"]
    item = next(i for i in checklist({"play_guide": [other]}) if i["kind"] == "how to play it *")
    assert "doesn't count it" in item["why"]

def test_an_effect_switched_on_between_rounds_counts_at_its_share():
    """MECH31: a trial that raises an effect from 0% between conditions rounds was scored as if it were fully up (the
    factor came only from the round). follow_uptimes weighs it at once, with one recalculation of its own."""
    from builder.search.inner import Search

    class Build(UptimeBuild):
        @property
        def stats(self):
            stats = UptimeBuild.stats.fget(self)
            return {**stats, "dps": stats["dps"] * (1.5 if self.config.get("conditionEnemyWithered") else 1.0)}

    build = Build({"conditionEnemyShocked": True})
    build.objective, build.score = UptimeObjective(), 0.0
    active = {"conditionEnemyShocked": {"label": "Shocked", "value": True}}
    search = SimpleNamespace(build=build, play_notes={}, uptime_rows=[], uptime_cache={},
                             assumptions=SimpleNamespace(apply=lambda: False),
                             conditions=SimpleNamespace(active=active, uptimes={"conditionEnemyShocked": 0.5}))
    for name in ("weigh_uptimes", "follow_uptimes", "floor_without"):
        setattr(search, name, getattr(Search, name).__get__(search))
    search.weigh_uptimes()
    shocked = 0.5 + 0.5 * 1000 / 1200
    assert build.objective.uptime_factor == approx(shocked)
    # a trial switches Withered on, up 20% of the fight: x1.5 while up, so x(0.2 + 0.8 / 1.5), not x1.5
    build.config["conditionEnemyWithered"] = True
    active["conditionEnemyWithered"] = {"label": "Withered", "value": True}
    search.conditions.uptimes = {"conditionEnemyShocked": 0.5, "conditionEnemyWithered": 0.2}
    search.follow_uptimes()
    assert build.objective.uptime_factor == approx(shocked * (0.2 + 0.8 / 1.5))
    assert build.score == approx(1800 * shocked * (0.2 + 0.8 / 1.5)) and build.config["conditionEnemyWithered"] is True
    # its uptime moves (no recalculation: the cached ratio), then the trial is undone
    search.conditions.uptimes = {"conditionEnemyShocked": 0.5, "conditionEnemyWithered": 0.6}
    search.follow_uptimes()
    assert build.objective.uptime_factor == approx(shocked * (0.6 + 0.4 / 1.5))
    del build.config["conditionEnemyWithered"], active["conditionEnemyWithered"]
    search.conditions.uptimes = {"conditionEnemyShocked": 0.5}
    search.follow_uptimes()
    assert build.objective.uptime_factor == approx(shocked) and build.score == approx(1200 * shocked)


def test_floors_are_checked_with_every_part_time_effect_off_at_once():
    """The user, 9 Nov 2026: a defence floor counts a part-time effect only at full uptime. Two effects that each carry
    300 Life: off one at a time, each leaves the other's 300 standing; the floors see both gone. B is a count (2 on, 1 off,
    like Withered's chance-line stacks without Ignite: its "off" setting), so off it keeps 150."""
    from builder.search.inner import Search

    class Build(FakeBuild):
        @property
        def stats(self):
            return {"dps": 1000.0, "Life": 1000.0 + 300.0 * bool(self.config.get("a")) + 150.0 * (self.config.get("b") or 0)}

    build = Build({"a": True, "b": 2})
    build.objective, build.score = UptimeObjective(), 0.0
    active = {"a": {"label": "A", "value": True}, "b": {"label": "B", "value": 2, "off": 1}}
    search = SimpleNamespace(build=build, play_notes={}, uptime_rows=[], assumptions=SimpleNamespace(apply=lambda: False),
                             conditions=SimpleNamespace(active=active, uptimes={"a": 0.5, "b": 0.5}))
    for name in ("weigh_uptimes", "follow_uptimes", "floor_without"):
        setattr(search, name, getattr(Search, name).__get__(search))
    search.weigh_uptimes()
    assert build.objective.floor_deltas == [{"Life": -450.0}] and build.config == {"a": True, "b": 2}
    assert "1,450 time-weighted" in next(r for r in search.uptime_rows if r["var"] == "a")["defence"]
    assert "1,525 time-weighted" in next(r for r in search.uptime_rows if r["var"] == "b")["defence"]
    search.follow_uptimes()  # same effects: the cached deltas, no recalculation
    assert build.objective.floor_deltas == [{"Life": -450.0}]


def test_an_effect_switches_on_from_seven_percent_and_off_under_five():
    """MECH31: 2b75b's Ignite at 0.6-1.2% of the fight flipped with every support trial, and each flip chose the main
    skill again. Off, an effect switches on only from SWITCH_ON (7%, the user: "if something isn't active at least 7% of
    the time, it's basically worthless"); on, it stays down to MIN_UPTIME (5%)."""
    from builder.search.conditions import Conditions

    sets = []
    conditions = SimpleNamespace(active={}, found_uptimes={}, build=SimpleNamespace(set_config=sets.append))

    def evidence(up):
        def run():
            conditions.found_uptimes = {"conditionEnemyIgnited": up}
            return {"conditionEnemyIgnited": (True, "ignite")}
        return run

    resolve = Conditions.resolve.__get__(conditions)
    conditions.evidence = evidence(0.06)
    assert resolve() is False and "conditionEnemyIgnited" not in conditions.active  # off at 6%: stays off
    conditions.evidence = evidence(0.08)
    assert resolve() is True and conditions.uptimes == {"conditionEnemyIgnited": 0.08}
    conditions.evidence = evidence(0.06)
    assert resolve() is False and conditions.uptimes == {"conditionEnemyIgnited": 0.06}  # on: stays on at 6%
    assert sets == [{"conditionEnemyIgnited": True}]


def test_a_scouting_trials_condition_flip_does_not_choose_the_main_skill_again():
    """MECH31: discover's and energy_trials' trials only rank a move; a flip there doesn't re-choose the main skill."""
    from builder.search.inner import Search

    chosen = []
    search = SimpleNamespace(settle=lambda: True, gems=SimpleNamespace(scouting=True, choose_active_skill=lambda: chosen.append(1)))
    assert Search.resolve.__get__(search)() is True and chosen == []
    search.gems.scouting = False
    assert Search.resolve.__get__(search)() is True and chosen == [1]


def test_parried_row_gives_the_dps_while_parried_and_while_not():
    """The user, 11 Nov 2026: "X dps when boss is parried, Y dps when not". One row (not also the player-condition
    trial), the parries' 15% of the time taken out of both figures."""
    from builder.search import models
    from builder.search.inner import Search

    class ParryBuild(FakeBuild):
        @property
        def stats(self):
            return {"dps": 1500.0 if self.config.get("parryActive") else 1000.0, "Life": 1.0}

    build = ParryBuild({"parryActive": True})
    build.objective, build.score = UptimeObjective(), 0.0
    active = {"parryActive": {"label": "Parried", "value": True}}
    plan = models.ParryPlan(0.4, 0.15, "Parry the boss's telegraphed hits")
    search = SimpleNamespace(build=build, play_notes={}, uptime_rows=[], assumptions=SimpleNamespace(apply=lambda: False),
                             conditions=SimpleNamespace(active=active, uptimes={"parryActive": 0.4}, parry_plan=plan))
    search.floor_without = Search.floor_without.__get__(search)
    Search.weigh_uptimes.__get__(search)()
    rows = [r for r in play_guide.guide(search) if r["var"] == "parryActive"]
    assert len(rows) == 1 and rows[0]["kind"] == play_guide.PLAYER and rows[0]["dps_with"] == approx(1200)
    assert "1,765 DPS while the boss is Parried, 1,176 while it isn't" in rows[0]["play"]


def test_the_usage_play_notes_get_their_own_rows():
    """MECHC7 (COVC10-11): Usage.play_note's lines (Combo, walking) are shown as rows, counted in the headline."""
    search = search_with({}, {})
    groups = {"groups": [{"index": 1, "gems": [{"name": "Tempest Bell"}, {"name": "Combo Finisher", "support": True}]},
                         {"index": 2, "enabled": False, "gems": [{"name": "Hidden"}]}]}
    plain_call = search.build.call
    search.build.call = lambda method, **kw: groups if method == "groups" else plain_call(method, **kw)
    search.usage = SimpleNamespace(play_note=lambda name: "Strike first" if name == "Tempest Bell" else "")
    rows = [r for r in play_guide.guide(search) if r["var"].startswith("usage:")]
    assert [(r["var"], r["play"], r["counted"]) for r in rows] == [("usage:Tempest Bell", "Strike first", True)]


def test_seething_body_pause_row_holds_archon_at_the_cost_of_the_pauses():
    """MECHC8: cast from High (65%) to the top of Infernal Flame (2 x maximum Mana), pause 2 s + 1.4 s of decay back to
    High; Archon held all fight, the DPS scaled by the casting share; not counted in the headline."""
    from builder.search import models

    # 1,000 Mana, 100 Mana/s: casting 0.35 x 2,000 / 100 = 7 s, pausing 3.4 s: 7 / 10.4 of the time
    assert models.flame_pause_share(1000, 100) == approx(7 / 10.4)
    assert models.flame_pause_share(0, 100) == 0.0
    assert models.flame_uptime(20.0, 0.0) != (1.0, models.math.inf, 0.0)

    class Build:
        objective = SimpleNamespace(dps=lambda stats: stats["dps"])

        @property
        def stats(self):
            return {"dps": 2000.0 if models.FLAME_MANAGED else 1000.0}

        def call(self, method, **kwargs):
            assert method == "stats"
            return {"Mana": 1000, "ManaPerSecondCost": 100}

    row = play_guide.seething_pause(SimpleNamespace(build=Build(), assumptions=SimpleNamespace(apply=lambda: None)), 1000.0)
    assert not models.FLAME_MANAGED  # (put back)
    assert row["counted"] is False and row["dps_with"] == approx(2000 * 7 / 10.4) and row["dps_without"] == 1000.0
    assert "3.4s" in row["play"] and "stronger" in row["play"]

    class Unmoved(Build):
        @property
        def stats(self):
            return {"dps": 1000.0}

    assert play_guide.seething_pause(SimpleNamespace(build=Unmoved(), assumptions=SimpleNamespace(apply=lambda: None)),
                                     1000.0) is None


def test_a_build_at_the_edge_of_its_presence_is_compared_with_standing_back():
    """MECHC8 (#151): the search's Presence-edge distance (4 m) gets a "stand closer" row against the ranged 5 m."""
    assert play_guide._other_distance(40) == 50 and play_guide._other_distance(20) == 50
    assert play_guide._other_distance(50) == 20


def test_alignment_row_compares_stepping_round_the_boss_with_standing_still():
    """MECHC10: the headline counts Alignment's matched share; the row's "without it" leaves its lines out (standing
    still: only the first attack matches), and the build's assumptions are put back."""
    calls = []

    class Build:
        objective = SimpleNamespace(dps=lambda stats: stats["dps"], fields=[])
        stats = {"dps": 1200.0}

        def call(self, method, **kwargs):
            assert method == "setAssumptions"
            calls.append(kwargs["mods"])
            dps = 1200.0 if any(m.get("entry") == "support-alignment-damage" for m in kwargs["mods"]) else 1000.0
            return {"dps": dps}

        def _committed(self, result):
            self.stats = result

    mods = [{"line": "x", "entry": "support-alignment-damage"}, {"line": "y"}]
    notes = [{"entry": "support-alignment-damage", "play": "After each matched attack, step round the boss."}]
    build = Build()
    row = play_guide.alignment_row(SimpleNamespace(build=build, assumptions=SimpleNamespace(mods=mods, active=notes)),
                                   1200.0)
    assert row["dps_with"] == 1200.0 and row["dps_without"] == 1000.0 and row["counted"] is True
    assert "step round" in row["play"] and "standing still" in row["play"]
    assert calls[-1] == mods and build.stats["dps"] == 1200.0  # (put back)
    none = SimpleNamespace(build=build, assumptions=SimpleNamespace(mods=[{"line": "y"}], active=[]))
    assert play_guide.alignment_row(none, 1200.0) is None


def test_boss_moving_row_takes_a_melee_skills_dps_at_mech_ds_ceiling():
    """MECHC11 (MECH-D's bosses.moving_ceiling): a Melee main skill loses up to the boss's moving share (an upper bound,
    never counted); a ranged main skill or a boss with no dashes gets no row."""
    from builder.search import bosses

    def search(boss: str, types: set[str]):
        build = SimpleNamespace(ctx=SimpleNamespace(request=SimpleNamespace(boss=boss)))
        return SimpleNamespace(build=build, conditions=SimpleNamespace(main_types=types))

    olroth = "Olroth, Origin of the Fall"
    share = bosses.moving_ceiling({"_boss": olroth})[0]
    assert share > 0.1
    row = play_guide.boss_moving_row(search(olroth, {"Attack", "Melee"}), 1000.0)
    assert row["counted"] is False and row["kind"] == play_guide.FIGHT
    assert row["dps_without"] == approx(1000.0 * (1 - share)) and row["dps_with"] == 1000.0
    assert "upper bound" in row["play"] and "UNVERIFIED" in row["confidence"]
    assert play_guide.boss_moving_row(search(olroth, {"Spell", "Projectile"}), 1000.0) is None
    still = next(n for n, v in bosses.MOVING_CEILING.items() if v == 0)
    assert play_guide.boss_moving_row(search(still, {"Attack", "Melee"}), 1000.0) is None
