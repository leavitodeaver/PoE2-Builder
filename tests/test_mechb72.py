"""MECHB72: the real fight per boss phase (RESEARCH69 R69-8; AUDIT41 A41-42)."""
import pytest

from builder.search import bosses, models


def test_presets_with_phase_gates_and_their_transitions():
    """R69-8: the Arbiter of Ash (and the worst case, which has its Life and bars) stops at 55% for ~9 s and heals to
    full; the King's maze and the Fell Serpent's shield are their gates' transitions; Olroth's revive has no known
    length; presets without an established gate have none."""
    ash = bosses.gates("Arbiter of Ash")
    assert len(ash) == 1 and ash[0].share == pytest.approx(0.45 / 1.45) and ash[0].seconds == 9.0
    assert bosses.gates(bosses.WORST) == ash
    assert bosses.transitions("The King in the Mists")[0][0] == 38.0
    assert bosses.transitions("Olroth, Origin of the Fall")[0][0] is None
    for name in ("Xesht, We That Are One", "Arbiter of Divinity", "The Raven Trickster", "The Aberration",
                 "Zarokh, the Temporal", "The Bodach", "Vessel of Kulemak", "Atziri, the Red Queen"):
        assert bosses.gates(name) == () and bosses.transitions(name) == ()
    # the gates' transitions are the ABSENT events of the presets that have both (never counted twice)
    for name in set(bosses.GATES) & set(bosses.ABSENT):
        assert [t for t, _ in bosses.ABSENT[name][0]] == [g.seconds for g in bosses.gates(name)]


def test_a_burst_past_a_gate_is_the_lesser_of_its_seconds_and_its_share_of_a_bar():
    gate = bosses.gates(bosses.WORST)[0]
    assert bosses.gate_overshoot(gate, 0.05) == pytest.approx(0.05 * bosses.OVERSHOOT_SECONDS)
    assert bosses.gate_overshoot(gate, 5.0) == pytest.approx(bosses.OVERSHOOT_BAR / 1.45)
    olroth = bosses.gates("Olroth, Origin of the Fall")[0]
    assert bosses.gate_overshoot(olroth, 5.0) == 0.0  # (the bar's end: nothing past it)
    serpent = bosses.gates("Atziri, the Fell Serpent")[0]
    assert bosses.gate_overshoot(serpent, 5.0) == pytest.approx(0.04)  # (at most the bar's rest)
    assert bosses.gate_overshoot(gate, 0.0) == 0.0


def test_phase_pace_pays_each_phase_from_what_is_left_and_refills_by_regeneration():
    """One phase is cost_pace; two phases with a transition long enough to refill pay each from a full pool; with no
    refill the second pays from what the first left."""
    assert models.phase_pace(390.0, 1000.0, 2000.0, 0.25, [(1.0, None)])[0] == pytest.approx(
        models.cost_pace(390.0, 1000.0, 2000.0, 0.25))
    # b04's shape: 2,092 spent, 370.5 earned, 1,175 Mana, the pool 1.659 fights a second at the full pace
    phases = [(0.31 + 0.21, 9.0), (0.69, None)]
    pace, words = models.phase_pace(370.5, 2092.1, 1175.0, 1.659, phases, 370.5)
    assert pace == 1.0 and "9 s immune" in words[0]
    still, _ = models.phase_pace(370.5, 2092.1, 1175.0, 1.659, [(w, None) for w, _ in phases], 370.5)
    assert still < 1.0  # (no refill: the second phase runs short)
    # a slower build: the refill helps against the whole pool read as one burst
    slow = [(0.31 + 0.21, 9.0), (0.69, None)]
    assert models.phase_pace(370.5, 2092.1, 1175.0, 1.0, slow, 370.5)[0] > models.cost_pace(370.5, 2092.1, 1175.0, 1.0)
    # no regeneration in the transition: nothing refills (Leech stops with no hits)
    assert models.phase_pace(370.5, 2092.1, 1175.0, 1.0, slow, 0.0)[0] == pytest.approx(
        models.phase_pace(370.5, 2092.1, 1175.0, 1.0, [(w, None) for w, _ in slow], 0.0)[0])


def test_mana_cap_row_reads_the_phases_and_shows_its_rows():
    phases = ([(0.52, 9.0), (0.69, None)], [(0.31, 9.0), (0.69, None)], "the worst case's gates: 55%")
    scale, row = models.mana_cap_row({"Mana": (390.0, "regen", 1000.0)}, "Spark", [], True, {"Mana": 2000.0},
                                     (0.25, "a 4 s fight"), (), phases, {"Mana": 300.0})
    want, _ = models.phase_pace(390.0, 1000.0, 2000.0, 0.25, phases[0], 300.0)
    assert scale == pytest.approx(want)
    for words in ("phase by phase", "no burst past the gates", "no refill in the transitions", "as one burst",
                  "steady state only", "R69-8"):
        assert words in row["basis"], words
    # off-field breaks (no gates) refill by regeneration when it's given, the whole income when not
    one, row = models.mana_cap_row({"Mana": (390.0, "regen", 1000.0)}, "Spark", [], True, {"Mana": 2000.0},
                                   (0.25, "a 4 s fight"), (1.0,), None, {"Mana": 100.0})
    assert one == pytest.approx(models.cost_pace(390.0, 1000.0, 2100.0, 0.25)) and "regeneration alone" in row["basis"]


def test_a_pressed_group_whose_selected_skill_is_triggered_is_rated_by_its_untriggered_skill():
    """AUDIT41 A41-39: the search can select a group's triggered skill (Doom Blast beside Despair + Impending Doom);
    the press is charged for the skill the player uses, so the group is rated by its next untriggered active skill,
    and a group with none is dropped from the presses."""
    from builder.search.assumptions import untriggered_presses

    names = {(2, 1): ("Doom Blast", True), (2, 2): ("Despair", False), (3, 1): ("Lightning Bolt", True),
             (3, 2): ("Lightning Bolt", True), (1, 1): ("Spark", False)}
    calls = []

    def call(method, indices, actives):
        calls.append(list(actives))
        return {"rates": [{"index": i, "name": names[(i, a)][0], "triggered": names[(i, a)][1]}
                          for i, a in zip(indices, actives)]}

    by_index = {1: {"skills": [{}]}, 2: {"skills": [{}, {}], "mainActiveSkill": 1},
                3: {"skills": [{}, {}], "mainActiveSkill": 1}}
    indices, actives = [1, 2, 3], [1, 1, 1]
    rates = call("useRates", indices, actives)["rates"]
    rates, actives, swapped, dropped = untriggered_presses(call, rates, actives, indices, by_index, 1)
    assert swapped == {2: 2} and dropped == {3}
    assert [r["name"] for r in rates] == ["Spark", "Despair"] and actives == [1, 2, 1]
    # nothing triggered: no call, nothing changed
    calls.clear()
    plain = [{"index": 1, "name": "Spark"}, {"index": 2, "name": "Despair"}]
    assert untriggered_presses(call, plain, [1, 2], [1, 2], by_index, 1) == (plain, [1, 2], {}, set()) and not calls


def test_the_sniper_cloud_duration_reading_steps_2_02_s_at_the_base_poison():
    """GEM50 via the manager (05:37): at the base 2 s Poison both readings step R67-1's measured 2.02 s (a 6.03 s cloud:
    3 Poisons, not 4)."""
    cloud = {"PoisonDPS": 1000.0, "PoisonStackPotential": 0.25, "PoisonStacksMax": 4, "PoisonDuration": 2.0}
    fixed = models.sniper_cloud_poison(cloud, 6.03, 20.0, "fixed")[1]
    by_duration = models.sniper_cloud_poison(cloud, 6.03, 20.0, "duration")[1]
    assert fixed == pytest.approx(by_duration) == pytest.approx(3 * 2.0 / 20.0)
