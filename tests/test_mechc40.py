"""MECHC40's real-PoB tests (their own file, so their own small replay store: test_engine_models.py's grew past what a
4-8 GB place can load, POE2T3748-3751)."""
from __future__ import annotations

import faulthandler

import pytest

from builder.data import GameData
from builder.request.schema import BuildRequest
from builder.search.context import Context, make_payload
from builder.search.inner import Search
from builder.search.skeletons import enumerate_skeletons


@pytest.fixture(scope="module")
def engine():
    try:
        GameData()
    except FileNotFoundError:
        pytest.skip("no game-data dump; run `uv run python -m builder.data dump`")
    from builder.pob.engine import PobEngine

    enabled = faulthandler.is_enabled()
    faulthandler.disable()  # LuaJIT's Lua errors are Windows exceptions PoB's pcall catches
    yield PobEngine()
    if enabled:
        faulthandler.enable()


def test_fortifying_cry_grants_guard_for_a_bosss_power_at_its_use_rate(engine):
    """MECHC40: Fortifying Cry "Grants {0} Guard for every 5 enemy Power in range, counting up to 20 Power" for 4 s:
    a lone Unique boss's 20 Power gives 4 x the gem's value, up its uses x 4 s (on its 8 s cooldown: about half)."""
    payload = make_payload(BuildRequest(main_skill="Boneshatter", ascendancies=["Titan"]))
    skeleton = enumerate_skeletons(Context(payload), ["Boneshatter"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    build.call("addSkillGroup", text="Fortifying Cry 20/0 1")
    present = {s["name"]: s for s in build.call("activeSkills")["skills"] if not s.get("disabled")}
    stats = build.call("stats", fields=["Life", "Speed"])
    found = search.assumptions.event_guards([], stats, "Boneshatter", present, None)
    guard, up, row = found[0]
    print(row)
    assert row["label"].startswith("Guard from Fortifying Cry") and guard == pytest.approx(4 * 92)
    assert 0.3 < up <= 0.55


def test_a_chaos_inoculation_build_keeps_its_ailment_threshold(engine):
    """MECHC40 (patch 0.5.0): 2b75c is Chaos Inoculation; PoB's Ailment Threshold counts 50% of its 1 Life (plus its
    Energy Shield lines). With the assumptions it gains 50% of the Life it would have without the keystone (PoB's own
    what-if without it), x its increases."""
    from builder import ROOT
    from builder.pob import codes
    from builder.search.assumptions import Assumptions
    from builder.search.objective import Objective
    from builder.search.state import Build

    xml = codes.decode((ROOT / "benchmarks" / "reference" / "2b75c.txt").read_text(encoding="utf-8").strip())
    engine.call("loadXml", xml=xml)
    bare = engine.call("stats", fields=["AilmentThreshold", "Life"])
    ci = next(p for p in engine.call("facts", fields=[])["passives"] if p["name"] == "Chaos Inoculation")
    life = engine.call("nodeDeltas", cases=[{"remove": [ci["id"]]}], fields=["Life"], fullDPS=False)["results"][0]["Life"]
    ctx = Context(make_payload(BuildRequest(class_name="Huntress", main_skill="Explosive Spear")))
    build = Build(engine, ctx, Objective(ctx, full=True))
    build.refresh()
    Assumptions(build).apply()
    after = engine.call("stats", fields=["AilmentThreshold"])["AilmentThreshold"]
    print(bare, life, after)
    assert bare["Life"] == 1 and life > 100
    assert after - bare["AilmentThreshold"] > 0.25 * life  # (50% of it, x the build's own increases)
    # (MECHC41: and its Stun Threshold: PoB's own value without the keystone, through a StunThreshold MORE; this also
    # probes that a MORE of that name reaches PoB's threshold, CalcDefence 2767)
    stun = engine.call("stats", fields=["StunThreshold"])["StunThreshold"]
    stun_without = engine.call("nodeDeltas", cases=[{"remove": [ci["id"]]}], fields=["StunThreshold"],
                               fullDPS=False)["results"][0]["StunThreshold"]
    print(bare, stun, stun_without)
    assert stun == pytest.approx(stun_without, rel=0.01)


def test_soul_mantle_curses_you_at_the_shared_totem_death_rate(engine):
    """MECHC41: Soul Mantle's curses come at the shared totem-death rate (assumptions.totem_deaths, MECHB39): a Shockwave
    Totem build's totems over their own duration, re-placed as they expire; the "level gate blocks them" row stays."""
    payload = make_payload(BuildRequest(main_skill="Shockwave Totem", ascendancies=["Warbringer"], level=90))
    skeleton = enumerate_skeletons(Context(payload), ["Shockwave Totem"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    mantle = chr(10).join(["Rarity: UNIQUE", "Soul Mantle", "Sacrificial Mantle", "+75 to Spirit",
                           "+1 to maximum number of Summoned Totems",
                           "Inflicts a random Curse on you when your Totems die, ignoring Curse limit"])
    build.call("equip", slot="Body Armour", raw=mantle, fields=["FullDPS"])
    texts = [e["text"] for e in build.call("sourceLines")["lines"]]
    if not any("random Curse on you when your Totems die" in t for t in texts):
        pytest.skip("PoB didn't equip Soul Mantle in this skeleton")
    skills = build.call("activeSkills")["skills"]
    _, notes, _ = search.assumptions.models(skills)
    row = next(n for n in notes if str(n.get("label", "")).startswith("Soul Mantle: random Curses"))
    print(row)
    assert "totems die a second" in row["basis"] and "Shockwave Totem" in row["basis"]
    assert any(str(n.get("label", "")).startswith("Soul Mantle: if the level gate") for n in notes)


def test_vanguard_grants_its_stun_guard_on_every_stun_and_more_on_heavy_stuns(engine):
    """MECHC44 (an UNDERCOUNT before: only the Heavy Stun stat was counted, alone): Vanguard I's text "Gain 2 Guard per
    ten Strength when you Stun an Enemy / Gain an additional 4 Guard per ten Strength if Heavily Stunning": a Heavy Stun
    grants 6 per 10 Strength, a Light Stun 2, each at the main skill's own stun rates (models.heavy_stun_rate /
    light_stun_rate)."""
    from builder.search import bosses, models

    payload = make_payload(BuildRequest(main_skill="Boneshatter", ascendancies=["Titan"]))
    skeleton = enumerate_skeletons(Context(payload), ["Boneshatter"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    main = build.main_group()
    old = [{"name": g["name"], "level": g["level"], "quality": g["quality"]} for g in main["gems"]]
    build.set_group(index=main["index"], gems=[*old, {"name": "Vanguard I", "level": 1, "quality": 0}])
    present = {s["name"]: s for s in build.call("activeSkills")["skills"] if not s.get("disabled")}
    stats = {**build.call("stats", fields=list(dict.fromkeys(["Life", "Str", *models.MODEL_FIELDS]))),
             **bosses.kill_stats(bosses.DEFAULT)}
    found = [f for f in search.assumptions.event_guards([], stats, "Boneshatter", present, bosses.DEFAULT)
             if f[2]["label"].startswith("Guard from Vanguard I")]
    strength = stats["Str"]
    print(found, strength, models.heavy_stun_rate(stats), models.light_stun_rate(stats, True))
    assert found
    basis = found[0][2]["basis"]
    assert f"{6 * strength / 10:,.0f} a Heavy Stun" in basis and f"{2 * strength / 10:,.0f} a Light Stun" in basis
    assert "Light Stuns" in basis and "ROW, Heavy Stuns alone" in basis
