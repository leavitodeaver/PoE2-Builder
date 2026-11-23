"""MECHC56's models (MECH-C): every Glory skill takes every skill's events of its kind (models.GLORY_ROWS)."""
import pytest

from builder.search import conditions, models


@pytest.fixture(scope="module")
def engine():
    import faulthandler

    from builder.data import GameData

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


MAIN = models.Skill(name="Boneshatter", group=1, attack=True)
PHYS = {"Speed": 1.0, "HitChance": 100, "PhysicalHitAverage": 1000, "_texts": []}  # (no elemental, chaos or ailment)


def row(name, hits, index, **kw):
    return {"name": name, "index": index, "hits": hits, "how": f"{name} {hits:.2f} hits/s", **kw}


def uses(name, stats, skills=None):
    return models.glory_plan([name], stats, MAIN, skills or [], [])[0].get(name, (0.0, 0.0))[0]


def test_every_glory_skill_takes_the_other_skills_events_of_its_kind():
    """(MECHC56; KeywordPopups Glory: "actions taken against enemies will generate Glory ... different Skills can generate
    Glory from the same Glorious action") A physical main skill makes no elemental / chaos hit, Ignite, Shock or Chill:
    each Glory skill's uses come from the other skills' rows of its kind, and only those."""
    rows = [row("Frost Bomb", 0.5, 2, types=["Cold"], chill=1.0),
            row("Contagion", 0.4, 3, types=["Chaos"]),
            row("Flame Wall", 0.3, 4, types=["Fire"], ignite=0.5),
            row("Orb of Storms", 0.6, 5, types=["Lightning"], shock=0.25),
            row("Quarterstaff Strike", 1.0, 6, types=["Physical"], attack=True)]
    stats = {**PHYS, models.GLORY_ROWS: rows}
    for name in ("Apocalypse", "Archon of Chayula", "Ancestral Cry", "Spear of Solaris", "Starborn Onslaught",
                 "War Banner"):
        assert uses(name, PHYS) == 0.0 or name == "War Banner"
        assert uses(name, stats) > 0, name
    _, how = models.glory_plan(["Apocalypse"], stats, MAIN, [], [])
    assert "Frost Bomb" in how and "Flame Wall" in how and "Orb of Storms" in how
    assert "Contagion" not in how and "Quarterstaff Strike" not in how
    _, how = models.glory_plan(["Archon of Chayula"], stats, MAIN, [], [])
    assert "Contagion" in how and "Frost Bomb" not in how
    # (only their events: a Fire row that never Ignites feeds Apocalypse, not Ancestral Cry)
    assert uses("Ancestral Cry", {**PHYS, models.GLORY_ROWS: [row("Flame Wall", 0.3, 4, types=["Fire"])]}) == 0.0
    assert uses("Apocalypse", {**PHYS, models.GLORY_ROWS: [row("Flame Wall", 0.3, 4, types=["Fire"])]}) > 0


def test_starborn_onslaught_counts_chills_and_shows_the_freezes_only_reading():
    """(MECHC56) Its description says "Freezing or Chilling"; its only Glory stat is per_freeze: Chills count (the
    headline) and the Freezes-only figure is given beside it."""
    stats = {**PHYS, models.GLORY_ROWS: [row("Frost Bomb", 0.5, 2, types=["Cold"], chill=1.0)]}
    _, how = models.glory_plan(["Starborn Onslaught"], stats, MAIN, [], [])
    assert uses("Starborn Onslaught", stats) > 0
    assert "Freezing or Chilling" in how and "without them 0.00 Glory/s" in how


def test_a_glory_skills_own_hits_never_feed_it():
    """(MECHC56; KeywordPopups Glory: "You cannot gain Glory for a Skill while any instance of that Skill is active")
    Apocalypse's disasters and Chayula's Will come while their buff is up, and rows in the skill's own group too."""
    disasters = [row("Hailstorm", 2.0, 7, types=["Cold"]), row("Meteor", 0.2, 7, types=["Fire"])]
    assert uses("Apocalypse", {**PHYS, models.GLORY_ROWS: disasters}) == 0.0
    assert uses("Archon of Chayula", {**PHYS, models.GLORY_ROWS: [row("Chayula's Will", 1.0, 8, types=["Chaos"])]}) == 0.0
    granted = [row("Volcanic Eruption", 1.0, 9, types=["Fire"], ignite=1.0)]
    cry = [models.Skill(name="Ancestral Cry", group=9)]
    assert uses("Ancestral Cry", {**PHYS, models.GLORY_ROWS: granted}, cry) == 0.0
    assert uses("Ancestral Cry", {**PHYS, models.GLORY_ROWS: granted}, [models.Skill(name="Ancestral Cry", group=3)]) > 0


def test_the_other_skills_uses_join_the_half_second_gain_stream():
    """(MECHC56) The 0.5 s rule binds on the merged stream: two fast feeders don't double the gains past 2 a second."""
    fast = [row("Frost Bomb", 6.0, 2, types=["Cold"]), row("Spark", 6.0, 3, types=["Lightning"])]
    one = models.glory_plan(["Apocalypse"], {**PHYS, models.GLORY_ROWS: fast[:1]}, MAIN, [], [])[0]["Apocalypse"][0]
    both = models.glory_plan(["Apocalypse"], {**PHYS, models.GLORY_ROWS: fast}, MAIN, [], [])[0]["Apocalypse"][0]
    assert one < both <= 2.0 * 20 / 100 + 1e-9  # (at most 2 gains/s x 20 Glory over a 100-Glory use)


def test_the_legacy_attack_figure_still_feeds_the_banners():
    """(MECHC55's OTHER_ATTACK_HITS, kept for callers without rows)"""
    legacy = {**PHYS, "Speed": 0.2, models.OTHER_ATTACK_HITS: (1.0, "Quarterstaff Strike 1.00 hits/s")}
    assert uses("War Banner", legacy) > 3 * uses("War Banner", {**PHYS, "Speed": 0.2})


def test_the_main_skills_own_chills_count_past_the_30_percent_floor():
    """(MECHC56) glory_events' "chill": the main skill's hits x conditions._chill_per_hit (PoB's chance, the floor)."""
    cold = {"Speed": 2.0, "HitChance": 100, "ChillChanceOnHit": 100, "EnemyAilmentThreshold": 1000,
            "ChillHitDamage": 500, "ChillCritDamage": 500, "ColdHitAverage": 500, "CritChance": 0}
    assert models.glory_events(cold, MAIN)["chill"] == pytest.approx(2.0)
    weak = {**cold, "ChillHitDamage": 100, "ChillCritDamage": 100}  # (a 10% Chill: ignored)
    assert models.glory_events(weak, MAIN)["chill"] == 0.0
    assert models.glory_events({"Speed": 2.0, "HitChance": 100}, MAIN)["chill"] == 0.0


def test_side_hit_rows_say_what_each_hit_does_for_glory():
    """(MECHC56) side_hit_rows' types (either hand for an Attack), Ignite / Shock chances and Chill past the floor."""
    groups = [{"index": 2, "enabled": True, "gems": [{"name": "Frost Bomb"}]},
              {"index": 3, "enabled": True, "gems": [{"name": "Quarterstaff Strike"}]}]
    rows = {2: {"name": "Frost Bomb", "triggered": True, "speed": 0.5, "HitChance": 100, "ColdHitAverage": 600,
                "ChillChanceOnHit": 100, "ChillHitDamage": 600, "ChillCritDamage": 600, "IgniteChancePerHit": 50},
            3: {"name": "Quarterstaff Strike", "triggered": True, "speed": 1.0, "HitChance": 100,
                "MainHand.LightningHitAverage": 50, "MainHand.PhysicalHitAverage": 200, "ShockChancePerHit": 30}}
    out = {r["name"]: r for r in conditions.side_hit_rows(rows, groups, {"Frost Bomb", "Quarterstaff Strike"}, 1,
                                                          threshold=1000)}
    assert out["Frost Bomb"]["types"] == ["Cold"] and out["Frost Bomb"]["chill"] == 1.0
    assert out["Frost Bomb"]["ignite"] == 0.0  # (no IgniteDPS: as ignite_others)
    assert out["Quarterstaff Strike"]["types"] == ["Lightning", "Physical"]
    assert out["Quarterstaff Strike"]["shock"] == pytest.approx(0.3) and out["Quarterstaff Strike"]["chill"] == 0.0


def test_glory_rows_mark_attacks_and_leave_out_the_main_skill():
    class Build:
        side_rows = [row("Boneshatter", 1.0, 1, types=["Physical"]), row("Frost Bomb", 0.5, 2, types=["Cold"]),
                     row("Quarterstaff Strike", 0.7, 3, types=["Physical"])]
        combo_feed = ("Quarterstaff Strike", 0.9, "its Strikes 0.90/s")

        def call(self, what, **_):
            assert what == "activeSkills"
            return {"skills": [{"name": n, "disabled": False, "group": g, "attack": a, "spell": not a}
                               for n, g, a in (("Boneshatter", 1, True), ("Frost Bomb", 2, False),
                                               ("Quarterstaff Strike", 3, True))]}

    got = {r["name"]: r for r in conditions.glory_rows(Build(), MAIN)}
    assert "Boneshatter" not in got
    assert got["Frost Bomb"]["attack"] is False
    assert got["Quarterstaff Strike"]["attack"] is True and got["Quarterstaff Strike"]["hits"] == 0.9
    assert got["Quarterstaff Strike"]["types"] == ["Physical"]  # (the builder keeps its row's types)


def test_archon_of_undeath_mixes_the_doubled_temporary_minion_limit_where_it_binds():
    """(MECHC56; KeywordPopups UndeadArchon: "Temporary Minion Skills have 100% more Limit of Minions summoned")"""
    count_at = lambda lim: min(14.0, lim)  # noqa: E731 (a supply that keeps 14 zombies, limit 10)
    count, how = models.undeath_limit_mix(count_at, 10.0, (0.25, 2.0))
    assert count == pytest.approx(0.75 * 10 + 0.25 * 14) and "x2.00" in how
    assert models.undeath_limit_mix(lambda lim: min(6.0, lim), 10.0, (0.25, 2.0)) == (6.0, "")  # (limit not binding)
    assert models.undeath_limit_mix(count_at, 10.0, None) == (10.0, "")


def test_archon_of_undeath_note_carries_its_limit_mix():
    """(MECHC56) The reaver's Enrage every 4 s x 15%: up ~17.6%; the limit x(1 + 100% x 1.5 effect) = x2.5."""
    reaver = models.Skill(name="Raise Zombie: Skeletal Reaver", group=1, summon={"limit": 2, "persistent": True})
    _, notes = models.archon_of_undeath_model([reaver], ["15% chance to gain Archon of Undeath when you use a Command "
                                                         "skill"], effect=50.0)
    up, factor = notes[0][models.UNDEATH_LIMIT_STAT]
    assert up == pytest.approx(10 / (10 + 20 + 1 / (0.15 / 4))) and factor == pytest.approx(2.5)
    assert "Temporary minion limits x2.50" in notes[0]["basis"]


def test_archon_of_chayula_also_takes_poisoning_hits():
    """(MECHC56; RESEARCH51 R51-3: "dealing Chaos Hit damage ... or inflicting a Chaos damage over time Debuff";
    KeywordPopups Poison: Chaos damage over time) A Physical hit that Poisons feeds it, at its Poison chance."""
    poisoning = {**PHYS, "PoisonChanceOnHit": 50, "PoisonChanceOnCrit": 50}
    assert models.glory_events(poisoning, MAIN)["chaos hit"] == pytest.approx(0.5)
    assert models.glory_events(PHYS, MAIN)["chaos hit"] == 0.0
    side = {**PHYS, models.GLORY_ROWS: [row("Lightning Spear", 1.0, 2, types=["Physical"], poison=0.4)]}
    assert uses("Archon of Chayula", side) > 0 and uses("Apocalypse", side) == 0.0


def test_probe_apocalypse_glory_takes_a_side_skills_elemental_hits(engine):
    """(MECHC56; R51-3) Real PoB: a Volcano Shaman with Apocalypse (Bringer of the Apocalypse) and a Frost Bomb on its
    cooldown: Apocalypse's Glory counts Frost Bomb's Cold hits beside Volcano's (glory_caps' how names it), and its uses
    are at least those without the side rows."""
    from builder.request.schema import BuildRequest, Choices
    from builder.search.context import Context, make_payload
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    payload = make_payload(BuildRequest(main_skill="Volcano", ascendancies=["Shaman"],
                                        require=Choices(skills=["Apocalypse"])))
    skeleton = enumerate_skeletons(Context(payload), ["Volcano"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    build.call("allocate", add=[28745], fields=[])  # Bringer of the Apocalypse: "Grants Skill: Apocalypse"
    build.set_group(gems=[{"name": "Frost Bomb", "level": 20, "quality": 0}])
    for _ in range(2):  # (the side rows are published a round behind)
        search.settle()
    rows = [r for r in build.side_rows or [] if r.get("name") == "Frost Bomb"]
    print("PROBE side rows", rows)
    assert rows and "Cold" in rows[0]["types"] and rows[0]["hits"] > 0
    caps = search.assumptions.glory_caps()
    uses, how = caps["Apocalypse"]
    saved = build.side_rows
    build.side_rows = []
    search.assumptions._glory_feed_cache = None
    alone = search.assumptions.glory_caps()["Apocalypse"][0]
    build.side_rows = saved
    print("PROBE Apocalypse", uses, alone, how)
    assert "Frost Bomb" in how and uses >= alone


def test_a_side_skill_never_lowers_a_glory_skills_gains():
    """(MECHC56; probe POE2T7084: Volcano 0.78 regular hits/s + Frost Bomb 0.17/s read LOWER than Volcano alone under
    the random form) merged_gains keeps the main skill's rhythm and adds the side events outside the 0.5 s lock."""
    assert models.merged_gains(0.78, 0.78, 0.0) == pytest.approx(0.78)
    assert models.merged_gains(0.0, 0.0, 1.2) == pytest.approx(1.2 / 1.6)
    assert models.merged_gains(0.78, 0.78, 0.17) > 0.78
    assert models.merged_gains(2.0, 2.0, 5.0) == pytest.approx(2.0)
    volcano = {"Speed": 0.78, "HitChance": 100, "FireHitAverage": 1000, "_texts": []}
    alone = uses("Apocalypse", volcano)
    both = uses("Apocalypse", {**volcano, models.GLORY_ROWS: [row("Frost Bomb", 0.17, 5, types=["Cold"])]})
    assert both > alone
