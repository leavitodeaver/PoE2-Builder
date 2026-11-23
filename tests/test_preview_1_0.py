"""PREVIEW1: the PoE2 1.0 preview layer (builder/preview), OFF by default (the user, 23 Nov 2026). With the request's
preview_1_0 switch off nothing changes; with it on, the 1.0 gems join the gem lists, the 1.0 weapon classes count as
released, and PoB scores the preview skills from their reveal numbers."""
from __future__ import annotations

import json

import pytest

from builder import preview
from builder.data.rules import rules
from builder.preview import flags
from builder.preview.lua import installer, lua_value
from builder.request.schema import BuildRequest
from builder.search.context import Context, make_payload


def _context(**request) -> Context:
    return Context(make_payload(BuildRequest(**request)))


def test_preview_is_off_by_default_and_changes_nothing():
    assert BuildRequest().preview_1_0 is False
    ctx = _context(main_skill="Boneshatter")
    assert not preview.names() & set(ctx.gems) and not preview.names() & set(ctx.all_gems)
    assert not rules().weapon_in_game("Dagger") and not rules().weapon_in_game("One Hand Sword")
    assert rules().patch == "0.5.5"


def test_preview_on_offers_the_gems_and_weapons_then_off_puts_them_back():
    ctx = _context(main_skill="Charged Slash", preview_1_0=True)
    assert {"Charged Slash", "Viper Strike", "Elemental Penetration"} <= set(ctx.gems)
    assert ctx.gems["Charged Slash"]["preview"] is True and ctx.gems["Charged Slash"]["weaponTypes"] == [
        "One Hand Sword", "Two Hand Sword"]
    assert rules().weapon_in_game("Dagger") and rules().weapon_in_game("Two Hand Sword")
    assert "One Hand Sword" in rules().off_hands("One Hand Sword")
    assert "1.0 preview" in rules().patch
    _context(main_skill="Boneshatter")  # a later request without the switch: 0.5.5 again
    assert not rules().weapon_in_game("Dagger")


def test_every_preview_row_carries_its_source_confidence_and_what_is_left_out():
    for row in preview.gem_rows():
        assert row["sources"] and row["confidence"] and "not_modelled" in row, row["name"]
        assert row["id"].startswith("Metadata/Items/Gems/Preview/") and row["grantedEffectId"].startswith("Preview")
        note = preview.note(row["name"])
        assert preview.ASTERISK in note
    # Elemental Penetration shares the single-element supports' families: never stacked with one of them
    pen = next(r for r in preview.gem_rows() if r["name"] == "Elemental Penetration")
    assert set(pen["families"]) == {"FirePenetration", "ColdPenetration", "LightningPenetration"}


def test_lua_literals():
    assert lua_value({"a": [1, "x"], "b": True}) == '{ ["a"] = { 1, "x", }, ["b"] = true, }'
    assert "PREVIEW_1_0_INSTALLED" in installer() and "PreviewChargedSlashPlayer" in installer()


def test_the_pending_flags_name_their_sources():
    for flag in flags.FLAGS:
        assert flag["what"] and flag["sources"] and flag["confidence"]
    assert flags.for_ascendancy("Acolyte of Chayula") and not flags.for_ascendancy("Titan")


def test_install_does_nothing_when_the_switch_is_off():
    class Engine:
        class lua:  # noqa: N801
            @staticmethod
            def run(*a):
                raise AssertionError("no Lua may run with the switch off")

    assert preview.install(Engine(), BuildRequest()) is False


@pytest.fixture(scope="module")
def engine():
    from builder.pob.engine import PobEngine

    made = PobEngine()
    yield made
    made.close()


SWORD = "Rarity: Normal\nDark Blade\n"


def _dps(engine, skill: str, sword: str = SWORD) -> dict:
    engine.call("newBuild")
    engine.call("setLevel", level=90)
    engine.call("addItem", raw=sword)
    engine.call("addSkillGroup", text=skill, main=True)
    return engine.call("stats", fields=["TotalDPS", "AverageHit", "Speed"])


def test_pob_scores_the_preview_skills_only_once_installed(engine):
    found = engine.call("findGems", names=["Charged Slash"])
    assert found["Charged Slash"] != "Charged Slash"  # PoB alone has no such gem
    preview.install(engine, BuildRequest(preview_1_0=True))
    preview.install(engine, BuildRequest(preview_1_0=True))  # a repeat is a no-op
    assert engine.call("findGems", names=["Charged Slash"])["Charged Slash"] == "Charged Slash"
    at13 = _dps(engine, "Charged Slash 13/0 1")
    at20 = _dps(engine, "Charged Slash 20/0 1")
    assert at13["TotalDPS"] > 0
    # the level curve is Lightning Spear's (3.43 / 2.44 at 20 / 13), the speed 115% of base on a 1.55 sword
    assert at20["AverageHit"] / at13["AverageHit"] == pytest.approx(3.43 / 2.44, rel=0.02)
    assert at13["Speed"] == pytest.approx(1.55 * 1.15, rel=0.02)
    # Elemental Penetration supports it (the slash is 75% Lightning)
    pen = _dps(engine, "Charged Slash 20/0 1\nElemental Penetration 1/0 1")
    assert pen["TotalDPS"] > at20["TotalDPS"]
    # a dagger skill needs a dagger: on a sword PoB disables it
    assert _dps(engine, "Viper Strike 20/0 1")["TotalDPS"] == 0
    assert _dps(engine, "Viper Strike 20/0 1", "Rarity: Normal\nCinquedea\n")["TotalDPS"] > 0


def test_the_checklist_marks_preview_gems_and_pending_changes():
    from builder.report.checklist import checklist

    result = {"request": {"preview_1_0": True}, "summary": {"ascendClassName": "Acolyte of Chayula"},
              "groups": [{"gems": [{"name": "Charged Slash"}, {"name": "Elemental Penetration"}, {"name": "Brutality"}]}]}
    items = checklist(result)
    rows = [i for i in items if i["kind"] == "1.0 preview *"]
    assert [r["title"] for r in rows] == [f"Charged Slash{preview.ASTERISK}", f"Elemental Penetration{preview.ASTERISK}"]
    pending = [i["why"] for i in items if i["kind"] == "1.0 changes pending"]
    assert any("Acolyte of Chayula" in p for p in pending) and any("passive tree" in p for p in pending)
    # the switch off: no pending row; a 0.5.5 build gets no 1.0 row at all (its checklist as before)
    assert not [i for i in checklist({"groups": result["groups"]}) if i["kind"] == "1.0 changes pending"]
    assert not [i for i in checklist({"groups": [{"gems": [{"name": "Brutality"}]}]}) if i["kind"].startswith("1.0")]


def test_preview_uniques_join_only_with_the_switch():
    off = _context()
    assert "Windripper" not in {u["name"] for u in off.data.uniques}
    blue_off = next(u for u in off.data.uniques if u["name"] == "Blueflame Bracers")
    assert "Flammability" not in blue_off["raw"]
    ctx = _context(preview_1_0=True)
    by_name = {u["name"]: u for u in ctx.data.uniques}
    assert by_name["Windripper"]["preview"] and "Greatbow" == by_name["Windripper"]["base"]
    assert "Flammability" in by_name["Blueflame Bracers"]["raw"]  # the 1.0 text replaces 0.5.5's
    # no market: offered unpriced without a cap, never as free under one
    assert ctx.unique_price("Windripper") == (None, f"no market yet ({preview.ASTERISK})")
    capped = _context(preview_1_0=True, budget={"divines": 50})
    assert capped.unique_price("Windripper") is None
    names_offered = {o.name for o in ctx.item_options.get("Bow", [])}
    assert "Windripper" in names_offered
    assert "Windripper" not in {o.name for o in capped.item_options.get("Bow", [])}


def test_the_checklist_marks_a_preview_unique():
    from builder.report.checklist import checklist

    result = {"request": {"preview_1_0": True}, "gear": {"Weapon 1": {"name": "Windripper", "unique": True}}}
    rows = [i for i in checklist(result) if i["kind"] == "1.0 preview *"]
    assert rows and "new in 1.0" in rows[0]["why"] and preview.ASTERISK in rows[0]["title"]


def test_pob_reads_the_preview_uniques_text(engine):
    raw = next(u for u in preview.unique_rows() if u["name"] == "Windripper")["raw"]
    plain = _dps(engine, "Lightning Arrow 20/0 1", "Rarity: Normal\nGreatbow\n")
    ripper = _dps(engine, "Lightning Arrow 20/0 1", "Rarity: UNIQUE\n" + raw)
    assert ripper["TotalDPS"] > 1.5 * plain["TotalDPS"]  # 100% increased Physical, 30% + 30% extra, 12% speed
