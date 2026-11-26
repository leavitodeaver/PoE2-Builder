"""MECHC65: the boss's forced moves as a second way it re-enters your Presence (models.presence_reentries,
PRESENCE_FORCED's note); the Acolyte of Chayula's Choice nodes' +50% Remnant pickup range read by the walking model."""
import pytest

from builder.search import assumptions, models

XESHT = "Xesht, We That Are One"


def test_her_declaration_finds_an_ascendancy_persistent_buff_through_every_known_gem():
    # build 1 (probe POE2T9602): Virtuous Barrier (a Gemling ascendancy gem) and its granted Her Declaration are missing
    # from ctx.gems, so no Persistent Buff was found and the support read 0
    from types import SimpleNamespace

    from builder.search import conditions as c

    ctx = SimpleNamespace(gems={"Clarity II": {"support": True}}, any_gems={
        "Virtuous Barrier": {"support": False, "skillTypes": ["Buff", "Persistent"]},
        "Her Declaration": {"support": True, "skillTypes": []}, "Clarity II": {"support": True}})
    groups = [{"enabled": True, "gems": [{"name": "Virtuous Barrier"}, {"name": "Her Declaration"},
                                         {"name": "Clarity II"}]}]
    stats = {"PresenceRadius": 40, "MovementSpeedMod": 1.0}
    assert c.her_declaration(groups, ctx.gems, [], stats, 2.0) is None  # (the old lookup)
    up, how = c.her_declaration(groups, c.all_gems(ctx), [], stats, 2.0)
    assert up > 0 and "Virtuous Barrier" in how
