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


ANOTHER = "10% chance when a Charm is used to use another Charm without consuming Charges"


def _charm(used=40, duration=4.0):
    return {"kind": "Charm", "chargesUsed": used, "duration": duration, "gainBase": 0}


def test_another_charm_used_free_from_the_others_uses(monkeypatch):
    # (MECHC21, AUDIT31 A31-21: charms13 / charms15 / Heart of the Well's unveiled mod; read nowhere before)
    own = {"A": 0.10, "B": 0.05, "C": 0.0}
    monkeypatch.setattr(flasks, "uses_per_second",
                        lambda info, gain, raw, pool, _real=flasks.uses_per_second: (
                            (own[raw], "own") if not gain.charm_extra else _real(info, gain, raw, pool)))
    gain = replace(flasks.Charges.from_bridge({}), charm_another=10.0)
    extra = dict((r, u) for r, u, _ in flasks.charm_free_uses([(_charm(), r) for r in own], gain))
    # each use of the others: 10%, one of the two others at random
    assert abs(extra["A"] - 0.1 * 0.05 / 2) < 1e-9 and abs(extra["C"] - 0.1 * 0.15 / 2) < 1e-9
    assert flasks.charm_free_uses([(_charm(), "A")], gain) == ()  # one charm: nothing to use


def test_free_uses_fill_a_charms_down_time():
    gain = replace(flasks.Charges.from_bridge({}), charm_another=10.0, charm_extra=(("C", 0.0075, "test"),))
    import math
    up = flasks.uptime(_charm(), gain, None, "C")  # no charges of its own: only the free uses
    assert abs(up - (1 - math.exp(-0.0075 * 4))) < 1e-9
    assert flasks.uptime(_charm(), replace(gain, charm_extra=()), None, "C") == 0.0
