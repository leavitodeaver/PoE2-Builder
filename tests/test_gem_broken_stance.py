"""GEM5: Broken Stance (models.broken_stance_model). The player's Vaulting Impact can't Daze and doesn't consume Daze
(game files: cannot_daze; the consume stat is on a monster's set only), so it needs the boss Dazed by another source:
build.dazed_up (MECH70). A stand-in share here; the model isn't wired yet."""

from __future__ import annotations

import pytest

from builder.search import models


def test_no_other_daze_source_means_no_broken_stance():
    dps, notes = models.broken_stance_model(10_000.0, 1.0, 0.0, 2.0, 0.8)
    assert dps == 0.0 and "cannot Daze" in notes[0]["basis"]


def test_a_dazed_boss_gives_ten_hits_of_the_magnitude_per_cycle():
    # 10% of 10,000 = 1,000 on 10 hits; Vaulting Impact 1/s on a boss Dazed half the time: a 2 s wait, then 10 hits at 2/s
    dps, notes = models.broken_stance_model(10_000.0, 1.0, 0.5, 2.0, 0.8)
    assert dps == pytest.approx(1_000.0 * 10 / (2.0 + 5.0) * 0.8)
    assert notes[0]["confidence"] == "LOW" and "without it: 0" in notes[0]["play"]


def test_slow_hits_are_capped_by_the_sixty_seconds():
    # 0.1 hits/s: 6 hits gain it in 60 s, not 10
    dps, _ = models.broken_stance_model(10_000.0, 1.0, 1.0, 0.1, 1.0)
    assert dps == pytest.approx(1_000.0 * 6 / (1.0 + 60.0))
