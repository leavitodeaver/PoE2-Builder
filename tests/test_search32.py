"""SEARCH32: the finished build's re-settle check (inner.reapply_drift; the manager's 09:35 ruling, 20 Nov)."""

from types import SimpleNamespace

import pytest

from builder.search import inner


class FeedsOnItself:
    """A stand-in search whose one model reads its own last output, as 2b75b's explosion rate did with The Covenant's
    Life Remnants as its only Remnant source: r_next = 1 / (3 + 1 / r) per settle (SEARCH32's report)."""

    def __init__(self, rate: float, loop: bool):
        self.rate, self.loop = rate, loop
        objective = SimpleNamespace(dps=lambda stats: 100_000 * stats["rate"])
        self.build = SimpleNamespace(objective=objective, stats={"rate": rate})

    def settle(self) -> bool:
        if self.loop:
            self.rate = 1 / (3 + 1 / self.rate)
        self.build.stats = {"rate": self.rate}
        return self.loop


def test_a_build_that_moves_when_settled_again_is_flagged():
    drift = inner.reapply_drift(FeedsOnItself(0.224, loop=True))
    assert drift["flagged"] and len(drift["after"]) == inner.REAPPLY_ROUNDS
    assert drift["after"][0] < drift["delivered"] * 0.7  # (0.224 -> 0.134)


def test_a_settled_build_is_not_flagged():
    drift = inner.reapply_drift(FeedsOnItself(0.224, loop=False))
    assert not drift["flagged"] and drift["worst"] == 0.0


def test_the_check_never_loses_the_build():
    class Broken(FeedsOnItself):
        def settle(self) -> bool:
            raise RuntimeError("engine gone")

    drift = inner.reapply_drift(Broken(0.2, loop=False))
    assert not drift["flagged"] and "engine gone" in drift["error"]


def test_a_build_whose_code_scores_differently_from_scratch_is_flagged():
    """2b75b D1 (POE2T5408): settled, the live build stayed at 207, but its exported code scored fresh read 28,581."""
    drift = inner.reapply_drift(FeedsOnItself(0.00207, loop=False), fresh=lambda: 28_581.0)
    assert drift["flagged"] and drift["fresh"] == 28_581.0 and drift["after"] == pytest.approx([207.0, 207.0])
    assert not inner.reapply_drift(FeedsOnItself(0.2, loop=False), fresh=lambda: 20_000.0)["flagged"]
