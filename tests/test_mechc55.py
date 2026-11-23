"""MECHC55's models (MECH-C): Archon of Undeath; the needs-a-target note for a corpse main skill held to 0 uses."""
import pytest

from builder.search import models

NOTABLE = "15% chance to gain Archon of Undeath when you use a Command skill"
BELT = "50% chance to gain Archon of Undeath when you create an Offering"


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


def reaver() -> models.Skill:
    return models.Skill(name="Raise Zombie: Skeletal Reaver", group=1, summon={"limit": 2, "persistent": True})


def test_archon_of_undeath_needs_its_line_and_a_source():
    assert models.archon_of_undeath_model([reaver()], []) == ([], [])
    mods, notes = models.archon_of_undeath_model([models.Skill(name="Spark", group=1)], [NOTABLE])
    assert mods == [] and notes[0]["value"] == 0 and "never a flat guess" in notes[0]["basis"]


def test_archon_of_undeath_from_commands_on_their_cooldowns():
    """Enrage every 4 s x 15% = 0.0375 gains/s: a mean 26.7 s after the 20 s recovery, 10 s up: 17.6%; 25% x that."""
    mods, notes = models.archon_of_undeath_model([reaver()], [NOTABLE])
    up = 10 / (10 + 20 + 1 / (0.15 / 4))
    assert notes[0]["value"] == pytest.approx(round(25 * up, 2))
    assert mods[0]["line"] == "Minions deal 1% more Damage" and mods[0]["scale"] == pytest.approx(25 * up, rel=1e-3)
    assert "Enrage every 4 s" in notes[0]["basis"] and "UNDERCOUNTS" in notes[0]["basis"]


def test_archon_of_undeath_from_offerings_kept_up_with_the_shared_archon_lines():
    """Bone Offering re-cast every 8 s x 50% = 0.0625/s (wait 16 s); +100% duration, no recovery (Dominion), 50% effect."""
    skills = [reaver(), models.Skill(name="Bone Offering", group=2)]
    _, notes = models.archon_of_undeath_model(skills, [BELT])
    assert notes[0]["value"] == pytest.approx(round(25 * 10 / (10 + 20 + 16), 2))
    _, notes = models.archon_of_undeath_model(skills, [BELT], {"ArchonDuration": 100, "ArchonRecovery": 0}, 50,
                                              no_recovery=True)
    assert notes[0]["value"] == pytest.approx(round(25 * 1.5 * 20 / (20 + 16), 2))
    _, notes = models.archon_of_undeath_model([reaver()], [BELT])  # (no Offering: nothing gains it)
    assert notes[0]["value"] == 0


def test_archon_of_undeath_races_another_archon_kind_for_the_one_slot():
    """Another kind gaining at the same rate takes half the cycles, and the joint wait is halved."""
    rate = 0.15 / 4
    _, notes = models.archon_of_undeath_model([reaver()], [NOTABLE], others=(rate, "Elemental Archon"))
    assert notes[0]["value"] == pytest.approx(round(25 * 0.5 * 10 / (10 + 20 + 1 / (2 * rate)), 2))
    assert "racing Elemental Archon" in notes[0]["basis"]
    assert models.archon_race(1.0, 0.0) == 1.0 and models.archon_race(0.0, 1.0) == 0.0


def test_archon_of_undeath_without_minions_adds_no_damage():
    skills = [models.Skill(name="Bone Offering", group=2)]
    mods, notes = models.archon_of_undeath_model(skills, [BELT])
    assert mods == [] and notes[0]["value"] == 0 and "no minions" in notes[0]["basis"]


def test_needs_a_target_note_only_for_a_corpse_main_skill_held_to_zero():
    devour = models.Skill(name="Devour", group=1, corpses=True)
    held = [{"kind": "SkillData", "skill": "Devour", "key": "triggerRate", "value": 0.0}]
    note = models.needs_a_target_note(devour, held)
    assert note and note[0]["label"] == "Devour: nothing to target*" and note[0]["value"] == "0 as the main skill"
    assert "Hollow Focus" in note[0]["play"]
    fed = [{"kind": "SkillData", "skill": "Devour", "key": "triggerRate", "value": 0.488}]
    assert models.needs_a_target_note(devour, fed) == []
    assert models.needs_a_target_note(models.Skill(name="Spark", group=1), held) == []
    assert models.needs_a_target_note(None, held) == []


def test_probe_archon_of_undeath_reaches_a_skeletal_brute_build(engine):
    """Real PoB: a Skeletal Brute build given the notable's line (its Shattering Roar Command pressed on cooldown) gets
    Archon of Undeath's note from Assumptions.models, and its "Minions deal N% more Damage" raises the Brutes' DPS by
    that share; without the line, no note."""
    from builder.request.schema import BuildRequest
    from builder.search.context import Context, make_payload
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    payload = make_payload(BuildRequest(main_skill="Skeletal Brute", level=90))
    skeleton = enumerate_skeletons(Context(payload), ["Skeletal Brute"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    label = "Archon of Undeath: 25% more Minion Damage at its share of the fight*"
    search.settle()
    assert not any(n["label"] == label for n in search.assumptions.active)
    real = build.call

    def with_notable(op, *args, **kwargs):
        got = real(op, *args, **kwargs)
        if op == "sourceLines":
            got = {**got, "lines": list(got["lines"]) + [{"text": NOTABLE, "source": "tree:Archon of Undeath"}]}
        return got

    build.call = with_notable
    try:
        search.settle()
        note = next((n for n in search.assumptions.active if n["label"] == label), None)
        print("PROBE undeath", note)
        assert note is not None and note["value"] > 0 and "Shattering Roar" in note["basis"]
        build.call = real
        build.call("setAssumptions", mods=[], fields=[])
        def dps() -> float:  # (a minion main skill's damage is in Full DPS: probe POE2T7056 read TotalDPS 0)
            got = build.call("stats", fields=["TotalDPS", "FullDPS"])
            return models.number(got.get("FullDPS")) or models.number(got.get("TotalDPS"))

        before = dps()
        found = [models.Skill.of(s) for s in build.call("activeSkills")["skills"] if not s["disabled"]]
        mods, _ = models.archon_of_undeath_model(found, [NOTABLE])
        build.call("setAssumptions", mods=mods, fields=[])
        after = dps()
        print("PROBE undeath dps", before, after, mods)
        # (at least the line's share; more where the Brutes' Physical hits meet the boss's armour, which takes less of a
        # bigger hit: POE2T7057, 3% more -> 83.8 to 88.2 Full DPS)
        assert before > 0 and before * (1 + mods[0]["scale"] / 100) * 0.995 <= after
        assert after <= before * (1 + 2 * mods[0]["scale"] / 100)
    finally:
        build.call = real
