"""SEARCHB10: POE2_SEARCH_MINION_MACES (OFF by default; the manager, 26 Nov 01:33). Unset, a minion skill's open search
holds caster weapons only (as before); "1": also a One Hand Mace and a Two Hand Mace (Trenchtimbre, Chober Chaber:
"+N to Level of all Minion Skills"), with the Sceptre beside a Two Hand Mace still under Giant's Blood's own switch.
Non-minion spells and attacks never change. Data only, no PoB."""
from __future__ import annotations

import pytest

from builder.data.prices import PriceFeed
from builder.request.schema import BuildRequest
from builder.search.context import Context, make_payload
from builder.search.skeletons import (
    CASTER_WEAPONS, GIANTS_BLOOD_SWITCH, MINION_MACES_SWITCH, enumerate_skeletons, main_hands)


def context(**request) -> Context:
    return Context(make_payload(BuildRequest(name="t", level=90, **request), PriceFeed().table(None, refresh=False)))


def weapons(ctx: Context, skill: str) -> set[str | None]:
    return {m[0] for m in main_hands(ctx, skill)[0]}


@pytest.mark.parametrize("value", [None, "", "0"])
def test_off_by_default_a_minion_skill_holds_caster_weapons_only(monkeypatch, value):
    if value is None:
        monkeypatch.delenv(MINION_MACES_SWITCH, raising=False)
    else:
        monkeypatch.setenv(MINION_MACES_SWITCH, value)
    assert weapons(context(main_skill="Supporting Fire"), "Supporting Fire") == {w for w, _ in CASTER_WEAPONS}


def test_on_a_minion_skill_also_holds_the_maces(monkeypatch):
    monkeypatch.setenv(MINION_MACES_SWITCH, "1")
    ctx = context(main_skill="Supporting Fire")
    assert weapons(ctx, "Supporting Fire") == {w for w, _ in CASTER_WEAPONS} | {"One Hand Mace", "Two Hand Mace"}
    assert weapons(ctx, "Skeletal Warrior") >= {"One Hand Mace", "Two Hand Mace"}


def test_on_other_skills_never_change(monkeypatch):
    ctx = context(main_skill="Frostbolt")
    monkeypatch.delenv(MINION_MACES_SWITCH, raising=False)
    spell, attack = weapons(ctx, "Frostbolt"), weapons(ctx, "Earthshatter")
    monkeypatch.setenv(MINION_MACES_SWITCH, "1")
    assert weapons(ctx, "Frostbolt") == spell  # (a spell that creates no minion)
    assert weapons(ctx, "Earthshatter") == attack


def test_the_sceptre_beside_a_two_hand_mace_stays_giants_bloods(monkeypatch):
    monkeypatch.setenv(MINION_MACES_SWITCH, "1")
    ctx = context(main_skill="Supporting Fire")
    pairs = lambda: {(s.weapon, s.offhand, s.requires_passive) for s in enumerate_skeletons(ctx, ["Supporting Fire"])[0]
                     if s.weapon == "Two Hand Mace"}
    monkeypatch.delenv(GIANTS_BLOOD_SWITCH, raising=False)
    assert pairs() and all(p[1] is None and p[2] is None for p in pairs())  # (a bare two-hander)
    monkeypatch.setenv(GIANTS_BLOOD_SWITCH, "all")
    assert ("Two Hand Mace", "Sceptre", "Giant's Blood") in pairs()
