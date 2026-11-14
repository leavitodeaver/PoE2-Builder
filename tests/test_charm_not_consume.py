"""MECHC21 (MECHC20's Handover; an UNDERCOUNT of a charm's uses, labelled in flasks.charges_per_use): "N% chance for
Charms you use to not consume Charges" (charms_%_chance_to_not_consume_charges). PoB parses it with leftover text
(ModCache: FlaskCharges BASE + " for Charms you use to not consume "), so it never applies it; flasks reads the line."""
from __future__ import annotations

from dataclasses import replace

from builder.search import flasks


def test_charm_line_is_read_and_cuts_charges_used():
    texts = ["20% chance for Charms you use to not consume Charges", "Recover 5% of maximum Mana when a Charm is used",
             "14% chance for Charms you use to not consume Charges", "10% chance for Flasks you use to not consume Charges"]
    assert flasks.charm_not_consume(texts) == 34.0  # (the Flask line is a flask's, not a charm's)
    gain = replace(flasks.Charges.from_bridge({}), charm_not_consume=25.0, charm_used_inc=-20.0)
    assert abs(flasks.charges_per_use({"kind": "Charm", "chargesUsed": 40}, gain) - 40 * 0.8 * 0.75) < 1e-9
    # a Flask isn't touched by the Charm line
    assert flasks.charges_per_use({"kind": "Flask", "chargesUsed": 10}, gain) == 10
