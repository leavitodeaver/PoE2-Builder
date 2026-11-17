"""MECHC36 (PATCH_CHECK H2-3, an UNDERCOUNT): Zerphi's Genesis's "50% of Charges consumed by used Charms are granted to
your Life Flasks" and "50% of Charges consumed by used Life Flasks are granted to your Charms": PoB parses neither;
flasks.zerphi_transfer solves the two together (each side's spend depends on the other's grant)."""
from __future__ import annotations

from dataclasses import replace

import pytest

from builder.search import flasks


def _charm():
    return {"kind": "Charm", "chargesUsed": 40, "duration": 4.0, "gainBase": 0}


LIFE = {"kind": "Flask", "chargesUsed": 10, "chargesMax": 60, "duration": 3.0, "gainBase": 0}


def test_zerphis_genesis_moves_charges_both_ways_to_a_fixed_point():
    # charms 2 charges/s each, the Life Flask 1/s, drunk whenever it can (Enduring Elixirs: no dip cap)
    gain = replace(flasks.Charges.from_bridge({}), charm=2.0, life_flask=1.0, life_effect_kept=True)
    charms = [(_charm(), "A"), (_charm(), "B")]
    out = flasks.zerphi_transfer(gain, charms, (LIFE, "life"), 50.0, 50.0)
    # g_l = 0.5 x 2 x (2 + g_c), g_c = 0.5 x (1 + g_l) / 2  ->  g_l = 3, g_c = 1
    assert out.granted_life == pytest.approx(3.0, abs=1e-4) and out.granted_charm == pytest.approx(1.0, abs=1e-4)
    assert "split between 2" in out.granted_how
    assert flasks.charges_per_second(LIFE, out, "Life") == pytest.approx(4.0, abs=1e-4)
    # granted charges aren't scaled by charge-gain increases (the lower reading)
    assert flasks.charges_per_second(LIFE, replace(out, flask_gain_inc=100.0), "Life") == pytest.approx(5.0, abs=1e-4)
    # only the charm line (every variant): the charms keep their own 2/s, the flask gets half of their 4/s
    one = flasks.zerphi_transfer(gain, charms, (LIFE, "life"), 50.0, 0.0)
    assert one.granted_life == pytest.approx(2.0) and one.granted_charm == 0.0
    # a charm used at most once per its 4 s effect: 10 charges/s at most, so the grant stops growing
    rich = flasks.zerphi_transfer(replace(gain, charm=20.0), charms, (LIFE, "life"), 50.0, 50.0)
    assert rich.granted_life == pytest.approx(0.5 * 2 * 10.0)
    # a Life Flask that's never drunk (never below full Life, no Enduring Elixirs) grants nothing
    dry = flasks.zerphi_transfer(replace(gain, life_effect_kept=False), charms, (LIFE, "life"), 50.0, 50.0)
    assert dry.granted_charm == 0.0
