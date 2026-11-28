"""COV64: item / passive lines a model already reads, marked (ledger/entries/items-cov64.yaml; NINJA1's gap list on the
fresh audit). Each reader is called with and without its line: the line moves the model's output."""
from builder.ledger import coverage, model_entry
from builder.search import models
from builder.search.models import Skill

MARKED = {
    "model-read-cov64-archon-of-undeath-offering": ["49% to gain Archon of Undeath when you create an Offering",
                                                    "(35-50)% to gain Archon of Undeath when you create an Offering"],
    "model-read-cov64-primal-bounty-dodge": ["Expend an Owl Feather when you Dodge to trigger Primal Bounty"],
}


def test_cov64_read_lines_are_marked():
    for entry, lines in MARKED.items():
        for line in lines:
            assert model_entry(line).id == entry, line
            assert coverage.ignored_line_status(line, True)[0] == "model", line


def test_cov64_archon_of_undeath_reads_the_offering_line():
    line = "49% to gain Archon of Undeath when you create an Offering"
    assert models.UNDEATH_ON_OFFERING.match(line)
    # without the line (and no Command line) the model gives nothing at all
    assert models.archon_of_undeath_model([], []) == ([], [])
    # with it the model runs: a note naming the Offering share (0 here: no Offering skill, never a flat guess)
    mods, notes = models.archon_of_undeath_model([], [line])
    assert mods == [] and notes and "Offering" in notes[0]["basis"]


def test_cov64_gem_studded_header_is_no_effect():
    line = "For each colour of Socketed Support Gem that is most numerous, gain:"
    assert coverage.ignored_line_status(line, True) == ("ledger", "no-effect-cov64-gem-studded-header",
                                                       "no effect on what's calculated")
    # (the colour lines under it are PoB's own: ModCache parses each on its MostNumerous condition)
    from pathlib import Path
    cache = (Path(__file__).resolve().parents[1] / "vendor" / "PathOfBuilding-PoE2" / "src" / "Data" / "ModCache.lua"
             ).read_text(encoding="utf-8")
    for colour, text in (("Red", "Hits against you have no Critical Damage Bonus"), ("Blue", "Skills have 30% less cost"),
                         ("Green", "40% less Movement Speed Penalty from using Skills while Moving")):
        row = next(l for l in cache.splitlines() if l.startswith(f'c["{colour}: {text}"]'))
        assert f"MostNumerous{colour}SocketedSupports" in row


def test_cov64_the_unread_stay_open():
    for line in ("+20% to Maximum Quality", "+25% to Maximum Quality", "being Shapeshifted for at least 8 seconds"):
        assert coverage.ignored_line_status(line, True)[0] != "model", line
