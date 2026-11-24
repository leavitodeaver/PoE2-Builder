"""MECH106: aggravate_model's ledger guard goes by the source line, not by one wording (COV55's Handover part 7)."""
import math
import re

from builder import ledger
from builder.ledger import Entry
from builder.search import models

PERFORATION_LINE = "20% chance for Bleeding to be Aggravated when Inflicted against Enemies on Jagged Ground"
ON_HIT_LINE = "25% chance to Aggravate Bleeding on Hit"
BLEEDER = {"Speed": 2.0, "HitChance": 100, "CritChance": 0, "BleedDuration": 5.0, "BleedChancePerHit": 100}


def entry(name: str, match: str, replace: tuple[str, ...] = (), kind: str = "pob_gap", **raw) -> Entry:
    data = {"id": name, "kind": kind, "match": match, "replace": list(replace), **raw}
    return Entry(name, name, kind, re.compile(match, re.I), replace, "theory", "proposed", (), "", data)


def counted(monkeypatch, *found: Entry) -> tuple[bool, bool]:
    """(Perforation counted by the model, the on-Hit line counted by the model) with only these ledger entries."""
    monkeypatch.setattr(ledger, "entries", lambda *a, **k: found)
    perforation = models.aggravate_model([PERFORATION_LINE], BLEEDER, {"Attack"}, jagged=(1.0, "Earthquake"))[0]
    on_hit = models.aggravate_model([ON_HIT_LINE], BLEEDER, {"Attack"})[0]
    return bool(perforation), bool(on_hit)


def test_the_model_counts_both_lines_with_no_ledger_entry(monkeypatch):
    assert counted(monkeypatch) == (True, True)


def test_a_model_link_of_its_own_does_not_stop_it(monkeypatch):
    links = (entry("link-perforation", r"Aggravated when Inflicted against Enemies on Jagged Ground",
                   model=models.AGGRAVATE_MODEL),
             entry("link-hit", r"chance to Aggravate Bleeding on Hit$", model=models.AGGRAVATE_MODEL))
    assert counted(monkeypatch, *links) == (True, True)


def test_a_live_ledger_line_in_any_wording_stops_it(monkeypatch):
    # (the retired aggravate-bleed-jagged-ground's own form, which the old guard missed: counted twice on main)
    old_jagged = entry("old-jagged", r"(\d+)% chance for Bleeding to be Aggravated when Inflicted against Enemies on "
                                     r"Jagged Ground", ("share:JaggedGround {1}% more Damage with Bleeding",))
    other = entry("other-wording", r"(\d+)% chance to Aggravate Bleeding on Hit$", ("{1}% increased Bleeding Damage",))
    assert counted(monkeypatch, old_jagged, other) == (False, False)
    magnitude = entry("magnitude", r"Aggravated when Inflicted", ("20% more Magnitude of Bleeding you inflict",))
    assert counted(monkeypatch, magnitude)[0] is False  # (the one wording the old guard knew: still stopped)


def test_a_live_line_behind_a_model_link_is_still_seen(monkeypatch):
    # (ledger.translate returns only the first entry; the guard reads every entry that matches the line)
    link = entry("link-perforation", r"Aggravated when Inflicted", model=models.AGGRAVATE_MODEL)
    live = entry("live-perforation", r"Aggravated when Inflicted", ("share:JaggedGround 20% more Damage with Bleeding",))
    assert counted(monkeypatch, link, live)[0] is False


def test_a_pob_wrong_line_counts_and_an_item_off_entry_does_not(monkeypatch):
    wrong = entry("wrong-hit", r"chance to Aggravate Bleeding on Hit$", ("10% more Magnitude of Bleeding you inflict",),
                  kind="pob_wrong")
    assert counted(monkeypatch, wrong)[1] is False
    off = entry("off-hit", r"chance to Aggravate Bleeding on Hit$", kind="pob_wrong", item_off=True)
    assert counted(monkeypatch, off)[1] is True  # (it only switches PoB's reading off: nothing else counts the line)


def test_another_model_reading_the_line_stops_it(monkeypatch):
    other = entry("other-model", r"chance to Aggravate Bleeding on Hit$", model="saitha")
    assert counted(monkeypatch, other)[1] is False


def test_main_ledger_leaves_every_aggravate_line_to_the_model():
    for line in (PERFORATION_LINE, ON_HIT_LINE, "Aggravate Bleeding on targets you Critically Hit with Attacks",
                 "Attack Hits Aggravate any Bleeding on targets which is older than 2 seconds",
                 "20% chance to Aggravate Bleeding on targets you Hit with Empowered Attacks",
                 "Aggravate Bleeding on Enemies when they Enter your Presence"):
        assert not models.aggravate_ledger_counts(line), line


def test_a_consumer_finds_the_debuff_only_past_the_consume_immunity():
    from builder.search import conditions

    assert conditions.consume_ready(0.8)[0] == 1.0 and conditions.consume_ready(1.0)[0] == 1.0  # (one a second or slower)
    assert conditions.consume_ready(1.2)[0] == 0.5 and conditions.consume_ready(2.5)[0] == 1 / 3  # (evenly spaced)
    assert "83%" in conditions.consume_ready(1.2)[1]  # (RESEARCH58's min(1, gap / 1 s) shown beside it)
    assert conditions.consume_ready(0.0) == (1.0, "")


def test_an_icestorm_main_skill_leaves_the_boss_unchilled_after_each_consume():
    from builder.search import conditions

    aura = {"_heart_of_ice": (1.0, "")}
    assert conditions.chilled_share(aura) == 1.0  # (no consumer: Chilled all the fight)
    assert abs(conditions.chilled_share({**aura, conditions.CHILL_CONSUMES: 0.8}) - 0.2) < 1e-9  # (1 s un-Chilled each)
    assert conditions.chilled_share({**aura, conditions.CHILL_CONSUMES: 1.5}) == 0.0
    ground = {"_chilled_ground": (0.5, ""), conditions.CHILL_CONSUMES: 0.5}
    assert abs(conditions.chilled_share(ground) - 0.25) < 1e-9


def test_malice_holds_duration_over_interval_stacks_inside_the_presence():
    inside = models.critical_weakness_stacks([], [], {"PresenceRadius": 40}, None, 2.0, malice=(510, 4.0))
    assert abs(inside[0] - 4.0 / 0.51) < 1e-9 and "Malice" in inside[1] and "refreshed" in inside[1]
    assert models.critical_weakness_stacks([], [], {"PresenceRadius": 40}, None, 6.0, malice=(510, 4.0))[0] == 0.0
    assert models.critical_weakness_stacks([], [], {"PresenceRadius": 40}, None, 2.0, malice=(100, 4.0))[0] == 20.0
    assert models.critical_weakness_stacks([], [], {"PresenceRadius": 40}, None, 2.0, malice=(500, 0.0))[0] == 8.0


def test_pursuit_counts_its_more_at_the_builds_projectile_attack_share():
    eight = "support_advancing_assault_melee_damage_+%_final_if_projectile_attack_damage_hit_in_past_8_seconds"
    two = "support_advancing_assault_melee_damage_+%_final_if_projectile_attack_damage_hit_in_past_2_seconds"
    mods, notes = models.pursuit_model({"Rapid Assault": {eight: 20}}, {"Lightning Spear": (0.25, "pressed")})
    share = 1 - math.exp(-0.25 * 8)
    assert mods == [models.line("1% more Melee Damage", 20 * share, skill="Rapid Assault")] and "86%" in notes[0]["value"]
    assert models.pursuit_model({"Rapid Assault": {eight: 20}}, {})[0] == []  # (no Projectile Attack: no source, 0)
    three = models.pursuit_model({"Rapid Assault": {two: 30}}, {"Lightning Spear": (0.25, "pressed")})[0]
    assert three[0]["scale"] == round(30 * (1 - math.exp(-0.5)), 3) or abs(three[0]["scale"] - 30 * (1 - math.exp(-0.5))) < 1e-3
