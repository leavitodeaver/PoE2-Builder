"""MECH106: aggravate_model's ledger guard goes by the source line, not by one wording (COV55's Handover part 7)."""
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
