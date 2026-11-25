"""SEARCHB9: POE2_SEARCH_GIANTS_BLOOD (OFF by default; the manager, 24 Nov 04:50: Giant's Blood pairs in the open search,
the second Two Hand Mace first). Unset, the open search never offers a Giant's Blood pair (as before); set, it offers the
listed off-hand kinds beside a Two Hand Mace, each requiring the keystone, within the request's off-hand choice and bans."""
from __future__ import annotations

import pytest

from builder.data.prices import PriceFeed
from builder.request.schema import BuildRequest
from builder.search.context import Context, make_payload
from builder.search.skeletons import GIANTS_BLOOD_SWITCH, enumerate_skeletons, open_pair


def context(**request) -> Context:
    return Context(make_payload(BuildRequest(name="t", level=90, **request), PriceFeed().table(None, refresh=False)))


def gb_pairs(ctx: Context) -> set[tuple[str, str]]:
    return {(s.weapon, s.offhand) for s in enumerate_skeletons(ctx, ["Earthshatter"])[0]
            if s.requires_passive == "Giant's Blood"}


@pytest.mark.parametrize("value", [None, "", "0", "off"])
def test_off_by_default_the_open_search_takes_no_giants_blood_pair(monkeypatch, value):
    if value is None:
        monkeypatch.delenv(GIANTS_BLOOD_SWITCH, raising=False)
    else:
        monkeypatch.setenv(GIANTS_BLOOD_SWITCH, value)
    assert not gb_pairs(context(main_skill="Earthshatter", ascendancies=["Titan"]))


def test_the_switch_opens_only_the_listed_off_hand_kinds(monkeypatch):
    monkeypatch.setenv(GIANTS_BLOOD_SWITCH, "Two Hand Mace")
    assert gb_pairs(context(main_skill="Earthshatter", ascendancies=["Titan"])) == {("Two Hand Mace", "Two Hand Mace")}
    monkeypatch.setenv(GIANTS_BLOOD_SWITCH, "Two Hand Mace, Shield")
    assert gb_pairs(context(main_skill="Earthshatter", ascendancies=["Titan"])) \
        == {("Two Hand Mace", "Two Hand Mace"), ("Two Hand Mace", "Shield")}
    monkeypatch.setenv(GIANTS_BLOOD_SWITCH, "all")
    assert gb_pairs(context(main_skill="Earthshatter", ascendancies=["Titan"])) == {
        ("Two Hand Mace", "Two Hand Mace"), ("Two Hand Mace", "Shield"), ("Two Hand Mace", "One Hand Mace"),
        ("Two Hand Mace", "Sceptre")}


def test_the_open_pairs_keep_the_requests_off_hand_choice_and_bans(monkeypatch):
    monkeypatch.setenv(GIANTS_BLOOD_SWITCH, "all")
    # an off hand the request names: only that kind
    assert gb_pairs(context(main_skill="Earthshatter", ascendancies=["Titan"], offhand="shield")) \
        == {("Two Hand Mace", "Shield")}
    assert not gb_pairs(context(main_skill="Earthshatter", ascendancies=["Titan"], offhand="focus"))  # (never a Focus)
    # a banned keystone or item class: none / that kind out
    assert not gb_pairs(context(main_skill="Earthshatter", ascendancies=["Titan"], forbid={"passives": ["Giant's Blood"]}))
    assert ("Two Hand Mace", "Shield") not in gb_pairs(
        context(main_skill="Earthshatter", ascendancies=["Titan"], forbid={"item_classes": ["Shield"]}))


def test_open_pair_reads_only_giants_blood_rows(monkeypatch):
    monkeypatch.setenv(GIANTS_BLOOD_SWITCH, "all")
    assert open_pair({"passive": "Giant's Blood", "off_hand": "Shield"})
    assert not open_pair({"passive": "Instruments of Power", "off_hand": "Focus"})
    monkeypatch.setenv(GIANTS_BLOOD_SWITCH, "Shield")
    assert not open_pair({"passive": "Giant's Blood", "off_hand": "Two Hand Mace"})
