"""MECHB68's model checks (MECH-B): Explosive Demise's press rate chosen by the build's DPS."""
import faulthandler

import pytest

from builder.search import models

ZOMBIES = models.Skill("Raise Zombie", summon={"limit": 8, "duration": 0, "persistent": True, "undead": True})
SPARK = models.Skill("Spark", group=1, spell=True)


def arsonist(recovery: float = 1.0, press: float | None = None) -> models.Skill:
    summon = {"limit": 1, "duration": 0, "persistent": True, "undead": True}
    if press is not None:
        summon["pressRate"] = press
    return models.Skill(models.ARSONIST, cooldown_recovery=recovery, summon=summon)


@pytest.fixture(scope="module")
def engine():
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


def test_explosive_demise_never_presses_faster_than_its_cast():
    """Its 1 s cast is a floor under the cooldown (a fast Cooldown Recovery read: 3 s / 5.13 = 0.58 s, an OVERCOUNT)."""
    assert models.command_every(arsonist(5.13), models.EXPLOSIVE_DEMISE_COOLDOWN, models.EXPLOSIVE_DEMISE_CAST) == 1.0
    assert models.demise_most(arsonist(5.13)) == 1.0 and models.demise_most(arsonist(1.5)) == pytest.approx(0.5)
    rate, how = models.minion_deaths([ZOMBIES, arsonist(5.13), SPARK], SPARK, {"Speed": 2.0})
    assert rate == pytest.approx(9 * 1.0 / (9 + 7.5 * 1.0)) and "every 1.00 s" in how


def test_a_chosen_press_rate_sets_the_deaths_and_every_command_reader():
    """The chosen rate (the bridge's summon pressRate) slows the deaths, the Command uses (Puppet Master, Archon of
    Undeath, different Commands); 0 presses none and says so."""
    slow = arsonist(5.13, 0.25)
    rate, how = models.minion_deaths([ZOMBIES, slow, SPARK], SPARK, {"Speed": 2.0})
    assert rate == pytest.approx(9 * 0.25 / (9 + 7.5 * 0.25)) and "the build's DPS is highest at" in how
    assert models.command_uses([slow]) == (0.25, ["Explosive Demise every 4 s"])
    never = arsonist(5.13, 0.0)
    rate, how = models.minion_deaths([ZOMBIES, never, SPARK], SPARK, {"Speed": 2.0})
    assert rate == 0.0 and "Explosive Demise (your Skeletal Arsonists' Command) not pressed" in how
    assert models.command_uses([never]) == (0.0, ["Explosive Demise not pressed"])
    # a chosen rate faster than the cooldown allows: the cooldown wins
    assert models.command_every(arsonist(1.0, 5.0), 3.0, 1.0) == 3.0


def test_the_explosion_counts_its_life_part_and_charges_the_command_cast():
    """A press: PoB's Attack part + 8% of the detonated minion's Life through the boss's Fire resistance; the 1 s cast
    off the Arsonists' attacks and a player main skill's time; the trials' rows in the play guide."""
    trials = {0.0: 100.0, 0.25: 120.0, 1.0: 50.0}
    added, mods, notes = models.explosive_demise_model(0.25, 1.0, 100.0, 5000.0, "test", 50.0, SPARK, trials)
    assert added == pytest.approx(0.25 * (100 + 0.08 * 5000 * 0.5))
    assert {m.get("line") or m.get("name") for m in mods} == {"Minions deal 1% more Damage", "DPS"}
    assert all(number == pytest.approx(-25.0) for number in (m.get("scale", m.get("value")) for m in mods))
    assert "without it 100 DPS, with it 120 DPS" in notes[0]["play"] and "every 4.00 s" in notes[0]["play"]
    minion_main = models.Skill("Raise Zombie", group=1, summon=ZOMBIES.summon)
    _, mods, _ = models.explosive_demise_model(0.25, 1.0, 100.0, 5000.0, "test", 50.0, minion_main, trials)
    assert [m.get("line") for m in mods] == ["Minions deal 1% more Damage"]  # (a minion main loses no time)
    added, mods, notes = models.explosive_demise_model(0.0, 1.0, 100.0, 5000.0, "test", 50.0, SPARK, trials)
    assert added == 0 and mods == [] and notes[0]["value"] == "not pressed"
    assert "without it 100 DPS; with it every 1.00 s, the most tried: 50 DPS" in notes[0]["play"]


class _Objective:
    fields = ["FullDPS"]

    def __init__(self, curve):
        self.curve = curve

    def dps(self, stats):
        return stats["dps"]


class _Build:
    """setAssumptions answers the DPS the curve gives for the press rate in the mods (on cooldown: the most)."""

    def __init__(self, curve, most):
        self.objective = _Objective(curve)
        self.curve, self.most, self.stats, self.calls = curve, most, {}, 0

    def call(self, method, mods=(), fields=()):
        assert method == "setAssumptions"
        self.calls += 1
        rate = next((m["value"] for m in mods if m.get("tag") == models.DEMISE_TAG), self.most)
        return {"dps": self.curve(rate)}

    def _committed(self, result):
        self.stats = result


def _choose(curve, most=1.0):
    from builder.search.assumptions import Assumptions

    build = _Build(curve, most)
    a = Assumptions(build)
    a.demise.update(key=("k",), most=most, due=True)

    def wanted():
        rate = a.demise["rate"]
        mods = ([models.raw_mod(models.COMMAND_PRESS, "OVERRIDE", rate, tag=models.DEMISE_TAG)]
                if rate is not None else [])
        return mods, []
    a.wanted = wanted
    a.choose_demise()
    return a.demise, build


def test_choose_demise_keeps_the_rate_the_build_s_dps_is_highest_at():
    """Each share of the most is scored, then the halves beside the best; ties go to fewer presses; the most is kept
    as None (on cooldown, following the build's Cooldown Recovery)."""
    state, build = _choose(lambda r: 100 - 400 * (r - 0.4) ** 2)  # (the best near 0.4 a second)
    assert state["rate"] == pytest.approx(0.375) and not state["due"]
    assert set(state["trials"]) == {0.0, 0.25, 0.5, 0.75, 0.375, 0.625} and build.calls == 6  # (stopped at 0.75)
    assert state["memo"][("k",)] == (state["rate"], state["trials"])
    state, build = _choose(lambda r: 100 - 10 * r)  # (every press costs: never pressed, 3 trials)
    assert state["rate"] == 0.0 and build.calls == 3
    state, build = _choose(lambda r: 100 + 10 * r)  # (every press pays: on cooldown)
    assert state["rate"] is None and build.calls == 6
    state, _ = _choose(lambda r: 100.0)  # (no difference: the fewest presses)
    assert state["rate"] == 0.0


def test_the_press_override_reaches_the_bridge_s_skill_list(engine):
    """The chosen rate goes to PoB as an OVERRIDE on the Arsonist's summon skill, and activeSkills reports it as
    summon.pressRate (so the conditions and the gem ranking press it as the score does); none without it."""
    from builder.request.schema import BuildRequest
    from builder.search.context import Context, make_payload
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    payload = make_payload(BuildRequest(main_skill="Fireball", level=90))
    skeleton = enumerate_skeletons(Context(payload), ["Fireball"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    build.call("setGroup", gems=[{"name": "Skeletal Arsonist", "support": False, "level": 20, "quality": 0,
                                  "enabled": True}])

    def summon():
        return next(s["summon"] for s in build.call("activeSkills")["skills"] if s["name"] == models.ARSONIST)
    assert summon().get("pressRate") is None
    build.call("setAssumptions", mods=[models.raw_mod(models.COMMAND_PRESS, "OVERRIDE", 0.25, skill=models.ARSONIST,
                                                      tag=models.DEMISE_TAG)], fields=[])
    assert summon().get("pressRate") == pytest.approx(0.25)
    found = [models.Skill.of(s) for s in build.call("activeSkills")["skills"] if not s["disabled"]]
    ars = next(s for s in found if s.name == models.ARSONIST)
    assert models.command_every(ars, 3.0, 1.0) == pytest.approx(4.0)
    build.call("setAssumptions", mods=[models.raw_mod(models.COMMAND_PRESS, "OVERRIDE", 0, skill=models.ARSONIST,
                                                      tag=models.DEMISE_TAG)], fields=[])
    assert summon().get("pressRate") == 0
    hit = build.call("minionSkillHit", index=next(g["index"] for g in build.groups() if any(
        s["name"] == models.ARSONIST for s in g["skills"])), active=1, skill=models.EXPLOSIVE_DEMISE_ID)
    assert hit.get("name") == "Explosive Demise", hit


def test_mystic_avalanche_on_a_nova_cast_around_you_reaches_the_boss_part_of_the_time():
    """MECH111 / the manager 22:20: a Nova cast around you puts the final Echo's side areas on a random axis through you
    (nova_side_share), not both on the boss; cast at the target both land (the targeted rule)."""
    nova = models.Skill("Ice Nova", 1, spell=True, support_stats={models.ECHO_COUNT_STAT: 1})
    stats = {"Speed": 1.0, "CastRate": 1.0, "AreaOfEffectRadius": 30}
    texts = ["Final Echo of Cascadable Spells also Cascades to either side of the targeted Area along a random axis"]
    _, at = models.spell_echo_model(nova, {"Cascadable", "Nova"}, stats, texts, boss=(1.0, "test"))
    _, around = models.spell_echo_model(nova, {"Cascadable", "Nova"}, stats, texts, boss=(1.0, "test"), around_you=True)
    share = models.nova_side_share(3.0, 0.0, 1.0, 1.0, 3.0)
    gain_at = float(at[-1]["value"][1:])
    gain_around = float(around[-1]["value"][1:])
    assert gain_at == pytest.approx(1 + 2 / 2, abs=1e-3)
    assert gain_around == pytest.approx(1 + 2 * share / 2, abs=1e-3) and gain_around < gain_at
    assert "a random axis through you" in around[-1]["basis"]


class _RowsBuild:
    """groups / sourceLines / getConfig / useRates for minion_rows; counts the useRates calls."""

    def __init__(self, rows):
        self.rows, self.uses, self.lines = rows, 0, ["Minions have 28% increased Cooldown Recovery Rate"]

    def groups(self):
        return [{"index": 1, "enabled": True, "gems": [{"name": "Skeletal Arsonist", "level": 20, "quality": 0}],
                 "skills": [{"name": models.ARSONIST}]},
                {"index": 2, "enabled": True, "gems": [{"name": "Raise Zombie", "level": 20, "quality": 0}],
                 "skills": [{"name": "Raise Zombie"}]}]

    def call(self, method, **kw):
        if method == "sourceLines":
            return {"lines": [{"text": t} for t in self.lines]}
        if method == "getConfig":
            return {"input": {"enemyFireResist": 50}}
        assert method == "useRates"
        self.uses += 1
        return {"rates": [{"index": i, "active": a, **self.rows[i]} for i, a in zip(kw["indices"], kw["actives"])]}


def test_the_command_recovery_is_the_minion_s_read_once_per_build_key():
    """Item 4 (MECHB67's find): each Command-bearing summon's cooldown_recovery is its minion's Command recovery x
    Archon of Undeath's MORE (the player's own recovery replaced, by a CooldownRecovery MORE on the summon skill for
    PoB's own list); the minion groups' useRates is read again only when the build key changes."""
    from builder.search.assumptions import Assumptions

    build = _RowsBuild({1: {"commandRecovery": 2.79, "minionLife": 3600}, 2: {"minionLife": 5000}})
    a = Assumptions(build)
    zombies = models.Skill("Raise Zombie", group=2, summon={"limit": 8, "duration": 0, "persistent": True})
    found = [arsonist(1.84 * 1.2 * 1.5), zombies]  # (PoB's: Archon x1.84, the player's own x1.2, last pass's mine x1.5)
    a.mods = [models.raw_mod("CooldownRecovery", "MORE", 84.0, skill=models.ARSONIST, tag=models.UNDEATH_COMMAND_TAG),
              models.raw_mod("CooldownRecovery", "MORE", 50.0, skill=models.ARSONIST, tag=models.COMMAND_RECOVERY_TAG)]
    rows = a.minion_rows(found)
    assert set(rows) == {models.ARSONIST, "Raise Zombie"} and build.uses == 1
    out = a.command_recovery(found, rows)
    assert out[0].cooldown_recovery == pytest.approx(2.79 * 1.84) and out[1] is zombies
    assert a.command_mods == [models.raw_mod("CooldownRecovery", "MORE", 100 * (2.79 / 1.2 - 1), skill=models.ARSONIST,
                                             tag=models.COMMAND_RECOVERY_TAG)]
    assert a.minion_rows(found) is rows and build.uses == 1  # (the same key: no new read)
    build.lines = ["Minions have 40% increased Cooldown Recovery Rate"]
    a.minion_rows(found)
    assert build.uses == 2  # (an item line changed: read again)
