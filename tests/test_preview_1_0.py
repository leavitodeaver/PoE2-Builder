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


def test_the_installer_compiles_in_pobs_luajit():
    """PoB's own LuaJIT (no PoB loaded: only the chunk's syntax) compiles the installer: POE2T8100 caught a quote
    slip only on the real engine."""
    from builder.pob.engine import POB_DIR
    from builder.pob.lua import LuaState

    lua = LuaState(POB_DIR / "runtime")
    try:
        (ok,) = lua.run("local f, err = loadstring(...); return f and 'ok' or err", "check", installer())
    finally:
        lua.close()
    assert ok == b"ok", ok


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
    engine.call("setConfig", vars={"enemyLightningResist": 50}, fields=[])
    return engine.call("stats", fields=["TotalDPS", "MainHand.AverageHit", "Speed"])


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
    assert at20["MainHand.AverageHit"] / at13["MainHand.AverageHit"] == pytest.approx(3.43 / 2.44, rel=0.02)
    assert at13["Speed"] == pytest.approx(1.55 * 1.15, rel=0.02)
    # Elemental Penetration supports it (the slash is 75% Lightning; the enemy at 50% Lightning resistance)
    pen = _dps(engine, "Charged Slash 20/0 1\nElemental Penetration 1/0 1")
    assert pen["TotalDPS"] > at20["TotalDPS"]
    # a dagger skill needs a dagger: on a sword PoB disables it
    assert (_dps(engine, "Viper Strike 20/0 1").get("TotalDPS") or 0) == 0  # (PoB leaves it out: the skill is disabled)
    assert _dps(engine, "Viper Strike 20/0 1", "Rarity: Normal\nCinquedea\n")["TotalDPS"] > 0


def test_the_checklist_marks_preview_gems_and_pending_changes():
    from builder.report.checklist import checklist

    result = {"request": {"preview_1_0": True}, "summary": {"ascendClassName": "Acolyte of Chayula"},
              "groups": [{"gems": [{"name": "Charged Slash"}, {"name": "Elemental Penetration"}, {"name": "Brutality"}]}]}
    items = checklist(result)
    rows = [i for i in items if i["kind"] == "1.0 preview *"]
    assert [r["title"] for r in rows] == [f"Charged Slash{preview.ASTERISK}", f"Elemental Penetration{preview.ASTERISK}"]
    pending = [i["why"] for i in items if i["kind"] == "1.0 changes pending"]
    assert any("Acolyte of Chayula" in p for p in pending) and any("game balance" in p for p in pending)
    assert not any("passive tree" in p for p in pending)  # (no crit passive in this result)
    assert not [i for i in checklist({"groups": [{"gems": [{"name": "Ice Nova"}]}]}) if i["kind"].startswith("1.0")]


def _pending(result):
    from builder.report.checklist import checklist

    return [i["why"] for i in checklist(result) if i["kind"] == "1.0 changes pending"]


def test_pending_notes_show_only_where_the_change_touches_the_build_with_the_switch_off():
    from builder.data import GameData
    from builder.pob.codes import encode

    assert _pending({"summary": {"ascendClassName": "Titan"}, "groups": [{"gems": [{"name": "Ice Nova"}]}]}) == []
    assert len(_pending({"summary": {"ascendClassName": "Infernalist"}})) == 1
    supports = _pending({"groups": [{"gems": [{"name": "Brutality II"}]}]})
    assert len(supports) == 1 and "support gems" in supports[0] and "this build: Brutality II" in supports[0]
    # a unique tree jewel: the socket note, not the craft note; a rare one: both
    unique = _pending({"gear": {"Jewel 1": {"name": "Megalomaniac", "unique": True, "rare": False}}})
    assert len(unique) == 1 and "jewel sockets" in unique[0]
    rare = _pending({"gear": {"Jewel 1": {"name": "Rare Jewel", "unique": False, "rare": True}}})
    assert len(rare) == 2 and any("jewel craft" in p for p in rare)
    # crit passives, read from the build code; a passive with no crit line and a defensive crit line don't count
    nodes = GameData().tree["nodes"]
    crit = next(n for n in nodes if flags.is_crit_passive(n))
    plain = next(n for n in nodes if not n.get("ascendancy") and "ritical" not in str(n.get("stats")))
    assert not flags.is_crit_passive({"stats": ["Hits against you have 20% reduced Critical Damage Bonus"]})

    def code(ids):
        return encode(f'<PathOfBuilding2><Tree activeSpec="1"><Spec nodes="{ids}"/></Tree></PathOfBuilding2>')

    assert not _pending({"code": code(plain["id"])})
    tree = _pending({"code": code(f"{plain['id']},{crit['id']}")})
    assert len(tree) == 1 and "passive tree" in tree[0] and "1 passive(s)" in tree[0]
    assert _pending({"code": "not a build code"}) == []


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


def test_a_preview_name_without_the_switch_says_how_to_use_it():
    from builder.data import GameData
    from builder.request.constraints import compile_request

    issues = compile_request(BuildRequest(main_skill="Viper Strike"), GameData()).issues
    assert any("Include 1.0 preview content" in i.message for i in issues if i.field == "main_skill")


def test_worldcutters_hold_comes_from_its_own_data():
    from builder.preview.readings import payoff_stage, stage_rows

    assert payoff_stage(3, 200) == 3
    assert [round(share, 2) for _, share in stage_rows(3, 200)] == [0.6, 0.9, 1.0]
    assert payoff_stage(3, 50) == 1  # (a weak per-stage bonus: releasing early pays more, so 3 is never assumed)


def test_the_added_attack_time_reading_row():
    from builder.preview.readings import rows, scaled_reading_ratio

    # a 1.55 weapon with 50% more speed: base 0.645 / 1.5 + 0.25 = 0.680 s; the other reading (0.645 + 0.25) / 1.5
    speed = 1 / (1 / 1.55 / 1.5 + 0.25)
    assert scaled_reading_ratio(speed, 1.55, 0.25) == pytest.approx((1 / speed) / ((1 / 1.55 + 0.25) / 1.5))
    assert scaled_reading_ratio(speed, 1.55, 0.25) > 1
    assert scaled_reading_ratio(1 / (1 / 1.55 + 0.25), 1.55, 0.25) == pytest.approx(1.0)  # no speed bonus: no gap
    gem = next(r for r in preview.gem_rows() if r["name"] == "Sand Scour")
    result = {"summary": {"mainSkill": "Sand Scour"}, "stats": {"Speed": speed},
              "gear": {"Weapon 1": {"base": "Dark Blade", "type": "One Hand Sword"}}}
    bases = {"Dark Blade": {"weapon": {"AttackRateBase": 1.55}}}
    assert "x1.1" in rows(gem, result, bases)[0]
    dual = {**result, "gear": {**result["gear"], "Weapon 2": {"base": "Dark Blade", "type": "One Hand Sword"}}}
    assert " on this build" not in rows(gem, dual, bases)[0]  # (two weapon times: no number is claimed)


def test_the_checklist_shows_the_reading_rows_with_the_asterisk():
    from builder.report.checklist import checklist

    why = next(i["why"] for i in checklist({"groups": [{"gems": [{"name": "Worldcutter"}]}]})
               if i["kind"] == "1.0 preview *")
    assert "Held to 3 stages" in why and "if it is the whole hold, DPS is x3" in why and preview.ASTERISK in why


def test_pob_scores_the_four_sword_skills(engine):
    preview.install(engine, BuildRequest(preview_1_0=True))
    great = "Rarity: Normal\nUltra Greatsword\n"
    plain = _dps(engine, "Perforate 20/0 1")
    assert plain["TotalDPS"] > 0 and plain["Speed"] == pytest.approx(1.55 * 1.3, rel=0.02)
    scour = _dps(engine, "Sand Scour 20/0 1")  # the added time isn't shortened or scaled: 1 / (1 / 1.55 + 0.25)
    assert scour["Speed"] == pytest.approx(1 / (1 / 1.55 + 0.25), rel=0.02)
    blade = _dps(engine, "Bloodied Blade 20/0 1")
    assert blade["Speed"] == pytest.approx(1 / (1 / 1.55 + 0.5), rel=0.02)
    assert (_dps(engine, "Worldcutter 13/0 1").get("TotalDPS") or 0) == 0  # (two-handed only: off on a one-hand sword)
    cut = _dps(engine, "Worldcutter 13/0 1", great)
    assert cut["TotalDPS"] > 0 and cut["Speed"] == pytest.approx(1 / (2.67 * 3), rel=0.02)


def test_the_preview_bases_carry_their_uniques_and_stay_out_of_a_plain_request():
    ctx = _context(main_skill="Charged Slash", preview_1_0=True)
    bases = {b["name"]: b for b in ctx.data.bases}
    assert bases["Vampiric Longsword"]["hidden"] and bases["Vampiric Longsword"]["weapon"]["AttackRateBase"] == 1.25
    assert {"Oro's Sacrifice", "Agnar", "Ashcaller"} <= {u["name"] for u in ctx.data.uniques}
    plain = _context(main_skill="Boneshatter")
    assert not {"Twilight Longsword", "Vampiric Longsword", "Searing Wand"} & {b["name"] for b in plain.data.bases}
    assert not {"Oro's Sacrifice", "Agnar", "Ashcaller"} & {u["name"] for u in plain.data.uniques}


def test_a_request_naming_a_trap_skill_is_told_why_it_cant_be_scored():
    assert "Fire Trap" in preview.TRAP_SKILLS and "damage" in preview.TRAP_HOLD
    assert "Fire Trap" not in preview.names()  # (no row: nothing scores it as 0 or from a guessed damage)


def test_pob_reads_agnars_cold_per_level_and_the_card_numbers(engine):
    preview.install(engine, BuildRequest(preview_1_0=True))
    raw = next(r for r in preview.unique_rows() if r["name"] == "Agnar")["raw"]
    engine.call("newBuild")
    engine.call("setLevel", level=90)
    engine.call("addItem", raw=raw)
    engine.call("addSkillGroup", text="Charged Slash 20/0 1", main=True)
    whole = engine.call("stats", fields=["MainHand.AverageHit", "Speed"])
    assert whole["Speed"] == pytest.approx(1.25 * 1.15, rel=0.01)
    engine.call("newBuild")
    engine.call("setLevel", level=90)
    engine.call("addItem", raw=raw.replace("Hits with this Weapon have 3 to 5 Added Cold Damage per Player Level\n", ""))
    engine.call("addSkillGroup", text="Charged Slash 20/0 1", main=True)
    assert whole["MainHand.AverageHit"] > 10 * engine.call("stats", fields=["MainHand.AverageHit"])["MainHand.AverageHit"]


def test_blade_conduits_ground_goes_through_the_shocked_ground_model(engine):
    from builder.search import conditions

    preview.install(engine, BuildRequest(preview_1_0=True))
    assert _dps(engine, "Blade Conduit 13/0 1")["Speed"] == pytest.approx(1 / 6, rel=0.03)  # (its cooldown)
    got = engine.call("activeSkills", levelStats=["Blade Conduit"])["skills"]
    stats = next(s for s in got if s["name"] == "Blade Conduit")["levelStats"]
    assert stats[conditions.SHOCKED_GROUND_DURATION_STAT["Blade Conduit"]] == 4000
    share, how = conditions.shocked_ground({"Speed": 1 / 6, "HitChance": 100, "_sg_skill": ("Blade Conduit", 4000),
                                            "_sg_feed": {}}, [])
    assert share == pytest.approx(4 / 6, rel=0.01) and "Blade Conduit" in how


def test_haemorrhages_rupture_is_a_labelled_model_not_a_zero():
    from builder.preview.rupture import LIMIT, rupture_gain

    row = next(r for r in preview.gem_rows() if r["name"] == "Haemorrhage")
    assert not any("Rupture" in text for text in row["not_modelled"])
    assert any("rupture.py" in text and "UNVERIFIED" in text for text in row["assumptions"])
    # (R55-10's example: 2 hits a second, every hit Bleeds, 5 s Bleeds of 15% of a hit a second, hit 1)
    headline, adds, bleeds = rupture_gain(0.15, 5.0, 2.0, 1.0, 1.0)
    assert bleeds == 2.0 and headline == pytest.approx(0.15 * 9) and adds == pytest.approx(2 * LIMIT, rel=1e-3)
    assert rupture_gain(0.15, 5.0, 0.1, 1.0, 1.0)[0] == 0  # (Bleeds that never overlap lose nothing)
    assert rupture_gain(10.0, 5.0, 2.0, 1.0, 1.0)[0] == pytest.approx(2 * LIMIT)  # (the strikes' limit binds)
    assert rupture_gain(0.15, 5.0, 2.0, 0.0, 1.0) == (0.0, 0.0, 0.0)  # (no Bleed chance: 0, never a flat rate)


def test_sacrificial_flame_is_a_table_and_counts_zero():
    from types import SimpleNamespace

    from builder.preview.flames import flame_note, flame_table

    assert flame_table()[:3] == [(1, (12, 4)), (2, (24, 8)), (3, (36, 12))]
    oro = SimpleNamespace(equipped={"Weapon 1": SimpleNamespace(name="Oro's Sacrifice")})
    notes = flame_note(oro)
    assert len(notes) == 1 and notes[0]["value"] == 0 and "12% or 4%" not in notes[0]["label"]
    assert "3 Flames: 36% or 12%" in notes[0]["basis"] and preview.ASTERISK in notes[0]["basis"]
    assert flame_note(SimpleNamespace(equipped={})) == []


def test_the_ambush_arithmetic():
    from builder.preview.stealth import ambush_more, shades_alive

    assert shades_alive(7.5) == 1 and shades_alive(2.5) == 3 and shades_alive(1.0) == 3 and shades_alive(0) == 0
    more, each, share = ambush_more(0.125, 1.0, 10, 1)  # an Ambush every 8 s, 1 use a second
    assert each == pytest.approx(1.3 * 1.5) and share == pytest.approx(0.125) and more == pytest.approx(0.125 * 0.95)
    assert ambush_more(0.0, 1.0, 10, 1)[0] == 0  # (no Stealth source: 0, never a flat rate)
    assert ambush_more(0.1, 1.0, 99, 0)[1] == pytest.approx(1.9)  # (Darkness caps at 30)


def test_ambush_needs_the_builds_own_stealth_source(engine):
    from types import SimpleNamespace

    from builder.preview.stealth import ambush_model

    preview.install(engine, BuildRequest(preview_1_0=True))
    gems = {g["name"]: g for g in preview.gems()}

    class Build:
        ctx = SimpleNamespace(gems=gems)
        objective = None
        call = staticmethod(engine.call)

    dagger = "Rarity: Normal\nCinquedea\n"
    engine.call("newBuild")
    engine.call("setLevel", level=90)
    engine.call("addItem", raw=dagger)
    engine.call("addSkillGroup", text="Viper Strike 20/0 1", main=True)
    speed = engine.call("stats", fields=["Speed", "HitChance"])
    mods, notes = ambush_model(Build(), SimpleNamespace(name="Viper Strike"), speed)
    assert not mods and notes[0]["value"] == 0 and "no skill counted as entering Stealth" in notes[0]["basis"]
    engine.call("addSkillGroup", text="Shroud 20/0 1\nConceal 1/0 1", main=False)
    groups = {s["name"]: g["index"] for g in engine.call("groups")["groups"] for s in g["skills"]}
    # (PREVIEW3) Conceal supports only skills that enter Stealth: Shroud, not Viper Strike (POE2T8114 socketed it there)
    assert "Conceal" in engine.call("supportCandidates", group=groups["Shroud"])["names"]
    assert "Conceal" not in engine.call("supportCandidates", group=groups["Viper Strike"])["names"]
    mods, notes = ambush_model(Build(), SimpleNamespace(name="Viper Strike"), speed)
    assert len(mods) == 1 and notes[0]["value"] > 0 and "10 Darkness" in notes[0]["basis"]
    assert preview.ASTERISK in notes[0]["label"] and preview.ASTERISK in notes[0]["basis"]


def test_stance_parts_are_rows_and_never_in_the_headline():
    from builder.preview.readings import rows

    row = next(r for r in preview.gem_rows() if r["name"] == "Worldcutter")
    assert any("NOT counted" in text and "Sandstorm" in text for text in rows(row))
    assert "stance_parts" not in row["model"]


def test_molten_forges_anvil_falls_once_a_cooldown(engine):
    preview.install(engine, BuildRequest(preview_1_0=True))
    got = _dps(engine, "Molten Forge 1/0 1", sword="Rarity: Normal\nWooden Club\n")
    assert got["TotalDPS"] > 0 and got["Speed"] == pytest.approx(1 / 6, rel=0.03)  # (its 6 s cooldown)
    row = next(r for r in preview.gem_rows() if r["name"] == "Molten Forge")
    assert any("UNDERCOUNT" in text for text in row["not_modelled"])


def test_the_knife_counts_and_the_boss_share():
    from builder.preview.knives import knife_count, knife_row, knives_on_boss

    ek, wb = knife_row("Ethereal Knives"), knife_row("Whirling Blades")
    assert knife_row("Viper Strike") is None
    assert knife_count(12, False, ek) == 12 and knife_count(12, True, ek) == 16  # (x1.35 dual wielding, rounded down)
    assert knife_count(20, True, wb) == 27
    fan = knives_on_boss(12, "fan", 1.5, 2.0)
    assert fan["headline"] == 12 and fan["one"] == 1  # (point-blank: the whole fan; the group rule's one a row)
    spiral = knives_on_boss(20, "spiral", 1.5, 2.0)
    assert 1 < spiral["headline"] < 20 and spiral["all"] == 20  # (the boss's share of the circle, never a flat count)
    assert knives_on_boss(20, "spiral", 3.0, 2.0)["headline"] == pytest.approx(10)  # (inside the boss: half the circle)


DAGGER = "Rarity: Normal\nCinquedea\n"


def _knife_build(engine, skill: str, dual: bool = False, extra: str = "") -> dict:
    from builder.preview.knives import FIELDS

    engine.call("newBuild")
    engine.call("setLevel", level=90)
    engine.call("equip", slot="Weapon 1", fields=[], raw=DAGGER)
    if dual:
        engine.call("equip", slot="Weapon 2", fields=[], raw=DAGGER)
    engine.call("addSkillGroup", text=skill, main=True)
    if extra:
        engine.call("addSkillGroup", text=extra, main=False)
    return engine.call("stats", fields=[*FIELDS, "TotalDPS", "LedgerImpaleChance", "Speed", "HitChance"])


def test_the_knife_skills_hit_impale_and_count_their_knives(engine):
    from types import SimpleNamespace

    from builder.preview.knives import knife_model

    preview.install(engine, BuildRequest(preview_1_0=True))
    one = _knife_build(engine, "Ethereal Knives 20/0 1")
    assert one["TotalDPS"] > 0 and one["ProjectileCount"] == pytest.approx(12)
    assert one["LedgerImpaleChance"] == 100 and one["LedgerImpaleEffect"] == pytest.approx(3.5)  # (250% more)
    mods, notes = knife_model(None, SimpleNamespace(name="Ethereal Knives"), one)
    assert mods[0]["value"] == pytest.approx(1100) and notes[0]["value"] == "x12.00"
    assert preview.ASTERISK in notes[0]["label"] and "UNVERIFIED" not in notes[0]["value"]
    two = _knife_build(engine, "Ethereal Knives 20/0 1", dual=True)
    assert two["OffHand.AverageHit"] > 0
    assert knife_model(None, SimpleNamespace(name="Ethereal Knives"), two)[1][0]["value"] == "x16.00"
    wb = _knife_build(engine, "Whirling Blades 13/0 1")
    assert wb["TotalDPS"] > 0 and wb["ProjectileCount"] == pytest.approx(20) and wb["LedgerImpaleChance"] == 100
    mods, notes = knife_model(None, SimpleNamespace(name="Whirling Blades"), wb)
    # (the spiral: the boss's share of the circle at 2 m; the worst-case boss's 0.3 m takes ~1 knife, the 1 m Fell
    # Serpent ~3.3: never a flat count; every knife is a row)
    assert float(notes[0]["value"][1:]) == pytest.approx(1.0) and "every knife, 20" in notes[0]["basis"]
    mods, notes = knife_model(None, SimpleNamespace(name="Whirling Blades"), {**wb, "_boss": "Atziri, the Fell Serpent"})
    assert float(notes[0]["value"][1:]) == pytest.approx(20 / 6, rel=0.01) and mods[0]["value"] > 0
    # a dagger skill: on a sword PoB disables it
    engine.call("equip", slot="Weapon 1", fields=[], raw=SWORD)
    assert (engine.call("stats", fields=["TotalDPS"]).get("TotalDPS") or 0) == 0
    # not a knife skill: nothing
    assert knife_model(None, SimpleNamespace(name="Viper Strike"), one) == ([], [])


def test_the_knife_skills_from_stealth(engine):
    from types import SimpleNamespace

    from builder.preview.stealth import ambush_model

    preview.install(engine, BuildRequest(preview_1_0=True))
    gems = {g["name"]: g for g in preview.gems()}

    class Build:
        ctx = SimpleNamespace(gems=gems, request=BuildRequest(preview_1_0=True))
        objective = None
        call = staticmethod(engine.call)

    # Ethereal Knives from Stealth doesn't hit: the Stealth uses cost damage, and its Spectral Impales are a row only
    stats = _knife_build(engine, "Ethereal Knives 20/0 1", extra="Shroud 20/0 1\nConceal 1/0 1")
    mods, notes = ambush_model(Build(), SimpleNamespace(name="Ethereal Knives"), stats)
    assert notes[0]["value"] < 0 and mods[0]["scale"] < 0 and "Spectral" in notes[0]["basis"]
    assert "UNVERIFIED" in notes[0]["basis"]
    # Whirling Blades' Ambush throws a knife more for each Darkness spent (Conceal's 10)
    stats = _knife_build(engine, "Whirling Blades 13/0 1", extra="Shroud 20/0 1\nConceal 1/0 1")
    mods, notes = ambush_model(Build(), SimpleNamespace(name="Whirling Blades"), stats)
    assert notes[0]["value"] > 0 and "+10 knives" in notes[0]["basis"]
    without = _knife_build(engine, "Whirling Blades 13/0 1", extra="Shroud 20/0 1")
    _, plain = ambush_model(Build(), SimpleNamespace(name="Whirling Blades"), without)
    assert "knives for the Darkness" not in plain[0]["basis"]


# (PREVIEW4) each 1.0 support offered exactly where its "Supports ..." text allows and refused elsewhere (the user, 24 Nov:
# "be careful with what mods are allowed with others"), on real PoB's own support check (bridge.supportCandidates)
ALLOWED = {
    "Overchannel": ({"Sand Scour", "Worldcutter", "Incinerate", "Snipe"}, {"Viper Strike", "Boneshatter", "Ethereal Knives"}),
    "Impaling Blade II": ({"Viper Strike", "Perforate", "Boneshatter", "Ice Strike"},
                          {"Ethereal Knives", "Shroud", "Incinerate", "Frost Bomb"}),
    "Rending Blade": ({"Viper Strike", "Boneshatter"}, {"Ethereal Knives", "Whirling Blades", "Incinerate"}),
    "Sandstorm": ({"Sand Scour", "Perforate"}, {"Viper Strike", "Worldcutter", "Boneshatter"}),
    "Volatile Shades": ({"Shroud", "Shadow Assault"}, {"Viper Strike", "Ethereal Knives", "Boneshatter"}),
    "Abundant Remnants I": ({"Frost Bomb"}, {"Viper Strike", "Shroud", "Boneshatter"}),
    "Galvanising Blades": (set(), {"Charged Slash", "Viper Strike", "Sand Scour", "Boneshatter", "Perforate"}),
}


def test_the_preview_supports_go_exactly_where_their_text_allows(engine):
    preview.install(engine, BuildRequest(preview_1_0=True))
    skills = sorted({name for yes, no in ALLOWED.values() for name in yes | no})
    engine.call("newBuild")
    engine.call("setLevel", level=90)
    engine.call("equip", slot="Weapon 1", fields=[], raw=DAGGER)
    for name in skills:
        engine.call("addSkillGroup", text=f"{name} 20/0 1", main=name == skills[0])
    groups = {s["name"]: g["index"] for g in engine.call("groups")["groups"] for s in g["skills"]}
    offered = {name: set(engine.call("supportCandidates", group=groups[name])["names"]) for name in skills}
    assert all(offered[name] for name in skills)  # (each group's skill is there for PoB's check)
    for support, (yes, no) in ALLOWED.items():
        for name in yes:
            assert support in offered[name], (support, name)
        for name in no:
            assert support not in offered[name], (support, name)
    rows = {r["name"]: r for r in preview.gem_rows()}
    assert rows["Impaling Blade II"]["families"] == rows["Rending Blade"]["families"]  # (the lower reading: not together)


def test_the_preview_supports_models():
    from builder.preview.supports import overchannel_hold

    hold, why = overchannel_hold("Worldcutter", {"Channel"})
    assert hold == pytest.approx(8.01) and "payoff" in why  # (its 2.67 s x 3 stages: past 1.5 s)
    assert overchannel_hold("Snipe", {"Channel", "PerfectTiming"})[0] == 0  # (Perfect Timing not read: 0, labelled)
    assert overchannel_hold("Incinerate", {"Channel"})[0] is None  # (kept up: counted, LOW)


def test_the_giant_sword_and_the_shade_blasts(engine):
    from types import SimpleNamespace

    from builder.preview.supports import FIELDS, support_model

    preview.install(engine, BuildRequest(preview_1_0=True))
    gems = {g["name"]: g for g in preview.gems()}

    class Build:
        ctx = SimpleNamespace(gems=gems, request=BuildRequest(preview_1_0=True))
        objective = None
        call = staticmethod(engine.call)

    engine.call("newBuild")
    engine.call("setLevel", level=90)
    engine.call("equip", slot="Weapon 1", fields=[], raw=DAGGER)
    engine.call("addSkillGroup", text="Viper Strike 13/0 1\nImpaling Blade II 1/0 1", main=True)
    stats = engine.call("stats", fields=FIELDS)
    mods, notes, added = support_model(Build(), SimpleNamespace(name="Viper Strike"), stats)
    sword = next(n for n in notes if "giant sword" in n["label"])
    # one sword a 5 s on hitting, 343% against Viper Strike's 164%, plus its Impale Extracted by the next strike
    hit = (stats.get("AverageHit") or stats["MainHand.AverageHit"]) * 343 / 164
    physical = stats.get("PhysicalHitAverage") or stats.get("MainHand.PhysicalHitAverage") or 0  # (an attack's: per hand)
    assert physical > 0 and added == pytest.approx(0.2 * hit + 0.2 * 0.3 * physical * 343 / 164, rel=0.01)
    assert added > 0 and preview.ASTERISK in sword["label"] and mods[-1]["fn"] == "added"
    engine.call("addSkillGroup", text="Shroud 20/0 1\nVolatile Shades 1/0 1", main=False)
    _, notes, more = support_model(Build(), SimpleNamespace(name="Viper Strike"), stats)
    blast = next(n for n in notes if n["label"].startswith("Volatile Shades on Shroud"))
    assert more > added and "Row: 0" in blast["basis"], blast


def test_the_knights_nodes_are_held_ready_with_their_source(engine):
    """(PREVIEW4) The Knight's nodes as data: not allocatable until the 1.0 tree has the Duelist; each line labelled, and
    the lines written for PoB's parser are read by it (the rest need a model, said per node)."""
    knight = json.loads((preview.HERE / "knight.json").read_text(encoding="utf-8"))
    assert "poe2db.tw/us/Knight" in knight["source"] and "NOT allocatable" in knight["about"]
    nodes = knight["nodes"]
    assert len(nodes) == 14 and sum(n["kind"] == "notable" for n in nodes) == 7
    names = {n["name"] for n in nodes}
    assert all(n.get("after") in names for n in nodes if n["kind"] == "notable")
    assert all(n["lines"] and n["confidence"] and n["dps"] for n in nodes)
    texts = [t for n in nodes for t in n.get("pob_text") or []]
    parsed = engine.call("parseLines", lines=texts)["results"]
    assert all(r["supported"] for r in parsed), [r["line"] for r in parsed if not r["supported"]]
