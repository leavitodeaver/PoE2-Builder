"""The objective's damage computed outside PoB holds several sources (SEARCH14; MECHB20 sums Melting Maelstrom's pulses and
Eternal Hunt in the bridge's LedgerAddedDPS): set_added keeps added_dps their sum, which every score reads, and
added_report lists each one for the result. No engine needed."""

from __future__ import annotations

from builder.search.inner import Search
from builder.search.objective import Objective


def bare() -> Objective:
    objective = Objective.__new__(Objective)
    objective.added_dps, objective.added_parts = 0.0, {}
    return objective


def test_the_added_slot_sums_its_sources_and_reports_each():
    objective = bare()
    assert objective.added_report() == {}
    objective.set_added({"Melting Maelstrom": 1200.0, "Eternal Hunt": 300.0, "nothing": 0.0})
    assert objective.added_dps == 1500.0
    assert objective.added_report() == {"Melting Maelstrom": 1200.0, "Eternal Hunt": 300.0}
    # A sum set the old way (one float, no parts) still shows, as one entry
    objective = bare()
    objective.added_dps = 250.0
    assert objective.added_report() == {"computed outside PoB": 250.0}
    # Snapshots keep the parts with the sum
    assert {"added_dps", "added_parts"} <= set(Search.SCORING_FIELDS)
