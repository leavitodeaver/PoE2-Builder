"""SEARCH15 (SEARCH14's Handover; glory_uses_capped's "not charged here", routed): a Glory skill the build counts a share
from is pressed, and its presses take the main skill's time: uses x 1 / its own speed, in objective.glory_busy
(Objective.busy), except where that time is charged already (the main skill, a pressed Full DPS group: press_model; a
chosen press rate: Search.press_sync) or the player doesn't press it (a meta skill or a totem uses it)."""
from types import SimpleNamespace

import pytest

from builder.search import models
from builder.search.assumptions import Assumptions
from builder.search.objective import Objective

GEMS = {"Hammer of the Gods": {"skillTypes": ["Attack", "Area"], "support": False},
        "Fireball": {"skillTypes": ["Spell", "Projectile"], "support": False},
        "Cast on Ignite": {"skillTypes": ["Meta", "Triggers"], "support": False}}


def gem(name, support=False):
    return {"name": name, "enabled": True, "support": support}


class Build:
    def __init__(self, full=False, meta=False, choices=None):
        self.objective = SimpleNamespace(full=full, press_choices=dict(choices or {}), glory_busy=0.0)
        self.ctx = SimpleNamespace(gems=GEMS)
        blast = [gem("Hammer of the Gods")] + ([gem("Cast on Ignite")] if meta else [])
        self._groups = [{"index": 1, "main": False, "enabled": True, "fullDPS": full, "fromItem": False, "gems": blast},
                        {"index": 2, "main": True, "enabled": True, "fullDPS": True, "fromItem": False,
                         "gems": [gem("Fireball")]}]

    def groups(self):
        return self._groups

    def call(self, name, **kw):
        assert name == "useRates"
        return {"rates": [{"index": 1, "speed": 4.0}]}


def busy(monkeypatch, **kw) -> float:
    monkeypatch.setattr(models, "glory_uses", lambda names, *more: (2.0 if "Hammer of the Gods" in names else 0.0, "x"))
    a = Assumptions.__new__(Assumptions)
    a.build = Build(**kw)
    total, how = a.glory_uses_capped(["Hammer of the Gods", "Fireball"], {}, models.Skill(name="Fireball"))
    assert total == pytest.approx(2.0)
    return a.build.objective.glory_busy


def test_pressing_a_glory_skill_takes_the_main_skills_time(monkeypatch):
    assert busy(monkeypatch) == pytest.approx(2.0 / 4.0)  # (2 uses a second, each 1 / 4 s)


def test_glory_press_time_is_never_charged_twice_or_for_a_skill_nobody_presses(monkeypatch):
    assert busy(monkeypatch, full=True) == 0.0  # (a pressed Full DPS group: press_model charges it)
    assert busy(monkeypatch, choices={"Hammer of the Gods": 1.0}) == 0.0  # (a chosen rate: press_sync charges it)
    assert busy(monkeypatch, meta=True) == 0.0  # (a meta skill uses it: no press)


def test_objective_busy_adds_the_glory_press_time():
    o = Objective.__new__(Objective)
    o.press_busy, o.glory_busy = 0.25, 0.5
    assert o.busy() == pytest.approx(0.75)
    o.glory_busy = 0.9
    assert o.busy() == 1.0


def test_a_pressed_glory_skill_is_held_to_the_uses_its_glory_pays_for(monkeypatch):
    """(MECHB21, SEARCH15's open OVERCOUNT) press_caps holds a Glory skill outside the main group to models.glory_plan's
    uses, so press_model never counts it at its duration or the search's chosen rate beyond them; the main group's own
    Glory skill is left to glory_main."""
    from builder.search import assumptions as assumptions_mod

    monkeypatch.setattr(assumptions_mod, "usage_rules", lambda build: {})
    monkeypatch.setattr(Assumptions, "glory_caps", lambda self: {"Hammer of the Gods": (0.4, "one per 2.5 s")})
    a = Assumptions.__new__(Assumptions)
    a.build = Build(full=True)
    a.build._groups[1]["gems"].append(gem("Hammer of the Gods"))
    caps = a.press_caps(a.build._groups, {1, 2})
    assert set(caps) == {1} and caps[1].uses == pytest.approx(0.4) and caps[1].how.startswith("Glory")
    real, how, _ = models.press_group_rate({"speed": 4.0}, cap=caps[1], chosen=2.0)
    assert real == pytest.approx(0.4) and "Glory" in how


def test_the_main_skills_glory_events_count_at_its_share_of_the_fight():
    """(MECHC45, an OVERCOUNT before) glory_plan takes the main skill's hits at stats["_main_share"] (main_time_share):
    at half the fight, the events (and so the Glory) are what half its Speed makes; no main skill or no share: unchanged."""
    main = models.Skill(name="Fireball")
    stats = {"Speed": 1.0, "HitChance": 100, "IgniteChanceOnHit": 100}  # (under the one-gain-per-0.5 s cap)
    full, _ = models.glory_plan(["Ancestral Cry"], stats, main, texts=[])
    half, how = models.glory_plan(["Ancestral Cry"], {**stats, "_main_share": 0.5}, main, texts=[])
    slow, _ = models.glory_plan(["Ancestral Cry"], {**stats, "Speed": 0.5}, main, texts=[])
    assert half["Ancestral Cry"][0] == pytest.approx(slow["Ancestral Cry"][0])
    assert half["Ancestral Cry"][0] < full["Ancestral Cry"][0]
    assert "50% of the fight" in how
    same, _ = models.glory_plan(["Ancestral Cry"], {**stats, "_main_share": 0.5}, None, texts=[])
    assert same == models.glory_plan(["Ancestral Cry"], stats, None, texts=[])[0]


def test_glory_presses_and_the_main_skills_share_are_solved_together(monkeypatch):
    """(MECHC45) Hammer of the Gods's Glory comes from the main skill's hits (2 uses a second at the whole fight, here
    proportional to the main skill's share), and each press takes 1 / 4 s from it: the busy share g = 2 (1 - g) / 4,
    so g = 1/3 and 4/3 uses a second (before: g = 1/2 at the main skill's full Speed, 2 uses a second)."""
    monkeypatch.setattr(models, "glory_uses", lambda names, stats, *more: (
        2.0 * (1.0 if stats.get("_main_share") is None else stats["_main_share"]) if "Hammer of the Gods" in names else 0.0,
        "x"))
    a = Assumptions.__new__(Assumptions)
    a.build = Build()
    o = SimpleNamespace(full=False, press_choices={}, press_busy=0.0, glory_busy=0.0, press_scales={})
    o.busy = lambda: min(o.press_busy + o.glory_busy, 1.0)  # (as Objective.busy)
    a.build.objective = o
    total, how = a.glory_uses_capped(["Hammer of the Gods", "Fireball"], {}, models.Skill(name="Fireball"))
    assert o.glory_busy == pytest.approx(1 / 3, abs=1e-5)
    assert total == pytest.approx(4 / 3, abs=1e-4) and "33.3% of the fight" in how


def test_onslaught_and_arcane_surge_on_hit_count_the_main_skill_at_its_share():
    """(MECHC45, an OVERCOUNT before) "N% chance to gain Onslaught / Arcane Surge on Hit / Critical Hit" lines read the
    main skill's hits at its share of the fight (stats["_main_share"], as the charge and Rage readers since MECHC44)."""
    from builder.search import conditions as c

    stats = {"Speed": 2.0, "HitChance": 100, "CritChance": 20}
    half = {**stats, "_main_share": 0.5}
    for f, text in ((c._onslaught_source, "10% chance to gain Onslaught on Hit for 4 seconds"),
                    (c._arcane_surge_source, "10% chance to gain Arcane Surge on Hit for 4 seconds")):
        assert f(text, [text], stats)[0] == pytest.approx(1 - 0.9 ** 8)
        assert f(text, [text], half)[0] == pytest.approx(1 - 0.9 ** 4)
    crit = "Gain Arcane Surge on Critical Hit for 1 second"
    assert c._arcane_surge_source(crit, [crit], half)[0] == pytest.approx(0.2)


def test_unbound_fury_and_arbiters_ignition_count_the_main_skill_at_its_share():
    """(MECHC45) Unbound Avatar's Fury (models.unbound_uptime) and Arbiter's Ignition's Archon wait (archon_sources) read
    the main skill's hits at stats["_main_share"]: the same as its Speed at that share."""
    stats = {"Speed": 2.0, "HitChance": 100, "IgniteChancePerHit": 30, "IgniteChanceOnHit": 30, "CritChance": 0}
    half = models.unbound_uptime({**stats, "_main_share": 0.5}, 8.0, False)
    slow = models.unbound_uptime({**stats, "Speed": 1.0}, 8.0, False)
    assert half[0] == pytest.approx(slow[0]) and half[0] < models.unbound_uptime(stats, 8.0, False)[0]
    wait = lambda st: next(s.wait for s in models.archon_sources(st, {"Arbiter's Ignition"}) if s.name == "Arbiter's Ignition")
    assert wait({**stats, "_main_share": 0.5}) == pytest.approx(2 * wait(stats))


def test_hit_remnants_count_the_main_skills_hits_at_its_share():
    """(MECHC45) Life / Mana Remnants and Sanguine Revelry from the main skill's hits (assumptions.hit_remnant_rate)
    read stats["_main_share"]: the same as its Speed at that share."""
    from builder.search.assumptions import hit_remnant_rate

    stats = {"Speed": 2.0, "HitChance": 100, "CritChance": 0}
    assert hit_remnant_rate("Life Remnants", {**stats, "_main_share": 0.5})[0] == pytest.approx(
        hit_remnant_rate("Life Remnants", {**stats, "Speed": 1.0})[0])
    assert hit_remnant_rate("Life Remnants", {**stats, "_main_share": 0.5})[0] < hit_remnant_rate("Life Remnants", stats)[0]


def test_the_boss_hit_boxes_left_to_pobs_placeholders_stay_unset():
    """(MECHC45, the manager's 08:05) the boss's crit chance / damage, Overwhelm and roll range stay PoB's placeholders
    (5%, +30%, 0, no reader; probe POE2T4829): no preset's config writes them."""
    from builder.search import bosses

    for name in bosses.PRESETS:
        assert not set(bosses.config_vars(name)) & set(bosses.BOSS_HIT_PLACEHOLDERS), name


def test_the_infusion_cap_loses_pickups_and_maximum_lines_raise_it():
    """(MECHC45, the manager's 08:05 from COV47) at most 3 of each Infusion by default (KeywordPopups); pickups at random
    between even uses: at supply = uses, about 85% of uses find one (smoothly, 100%); "+1 to maximum number of Elemental
    Infusions" raises the cap and the share; far apart or far over, the smooth reading."""
    from builder.search.assumptions import infusion_cap, infusion_found

    assert infusion_cap([], "Fire") == 3
    lines = ["+1 to maximum number of Elemental Infusions", "+1 to maximum Fire Infusions"]
    assert infusion_cap(lines, "Fire") == 5 and infusion_cap(lines, "Cold") == 4
    assert infusion_cap([], "Cold", verisium=True) == 6
    assert infusion_found(1.0, 1.0, 0.0, 3) == pytest.approx(0.85, abs=0.01)
    assert infusion_found(1.0, 1.0, 0.0, 3) < infusion_found(1.0, 1.0, 0.0, 4) < 1.0
    assert infusion_found(0.25, 1.0) == pytest.approx(0.25, abs=0.002) and infusion_found(4.0, 1.0) > 0.999
    assert infusion_found(0.0, 1.0) == 0.0 and infusion_found(1.0, 0.0) == 1.0


def test_adaptations_follow_the_bosss_elemental_hits():
    """(MECHC45, for COV-A's Reactive Growth entry) expected matching Adaptations = 3 x the element's share of the boss's
    elemental hits: none against a Physical-only boss, all three against a one-element boss."""
    from builder.search import boss_hits

    assert boss_hits.adaptations_held("Zarokh, the Temporal") == {"Fire": 0.0, "Cold": 0.0, "Lightning": 0.0}
    assert boss_hits.adaptations_held("Arbiter of Ash")["Fire"] == pytest.approx(3.0)
    mixed = boss_hits.adaptations_held("The Raven Trickster")
    assert sum(mixed.values()) == pytest.approx(3.0) and all(v > 0 for v in mixed.values())


def test_a_self_fed_remnant_chain_is_solved_in_one_pass():
    """(MECHC45; the manager's 09:35 from SEARCH32, MECHB45's review) a Transmutation Explosion fed by Life Remnants (3 s
    cooldown, any hit) from its own hits: one steady rate, not r_next = 1 / (3 + 1 / r) pass after pass. Waiting out the
    cooldown: one a cooldown; picked up at once: anchored only by the group's other hits; with neither, 0."""
    from builder.search.assumptions import hit_remnant_share, remnant_chain

    inf = float("inf")
    life = [(3.0, 1.0, 0.69)]  # (cooldown, qualifying share, the cast's own hits a second)
    assert remnant_chain(0.0, life, 1.0, 1.0, inf, wait=True) == pytest.approx(1 / 3, abs=1e-6)
    once = remnant_chain(0.0, life, 1.0, 1.0, inf, wait=False)
    assert 0.2 < once < 1 / 3  # (steady: r = 1 / (3 + 1 / (0.69 + r)))
    assert once == pytest.approx(1 / (3 + 1 / (0.69 + once)), abs=1e-6)
    assert remnant_chain(0.0, [(3.0, 1.0, 0.0)], 1.0, 1.0, inf, wait=False) == 0.0
    assert remnant_chain(0.8, [(3.0, 1.0, 0.0)], 1.0, 1.0, inf, wait=True) == pytest.approx(0.8 + 1 / 3, abs=1e-6)
    assert remnant_chain(0.0, life, 1.0, 2.0, 0.5, wait=True) == pytest.approx(0.5, abs=1e-6)  # (the cooldown cap)
    assert hit_remnant_share("Life Remnants", {"Speed": 0.01, "HitChance": 50, "CritChance": 0}) == pytest.approx(1.0)


def test_a_self_fed_chain_reads_the_bosss_ailments_at_its_own_rate():
    """(MECHC46) Mana Remnants / Sanguine Revelry in a self-fed Transmutation Explosion: the qualifying share (a Bleeding
    boss) is read at the explosion's own solved rate r, not at one explosion a second whatever r is; the solve finds the
    least crossing even where the share grows with r."""
    import time

    from builder.search.assumptions import hit_remnant_share, remnant_chain

    inf = float("inf")
    stats = {"Speed": 1.0, "HitChance": 100.0, "CritChance": 0.0, "BleedChancePerHit": 50.0, "BleedDuration": 5.0}
    slow, fast = hit_remnant_share("Sanguine Revelry", stats, 0.1), hit_remnant_share("Sanguine Revelry", stats, 2.0)
    assert 0.0 < slow < hit_remnant_share("Sanguine Revelry", stats, 1.0) < fast <= 1.0
    assert hit_remnant_share("Sanguine Revelry", stats, 0.0) == 0.0
    share = lambda r: hit_remnant_share("Sanguine Revelry", stats, r)  # noqa: E731
    start = time.perf_counter()
    rate = remnant_chain(0.2, [(2.0, share, 0.0)], 1.0, 1.0, inf, wait=True)
    assert time.perf_counter() - start < 0.5
    # the fixed point: r = 0.2 + min(1/2, q(r) r) (no other hits), with q read at r itself
    assert rate == pytest.approx(0.2 + min(0.5, share(rate) * rate), abs=1e-6)
    # no seed and no ailment at r = 0: the chain never starts (the least fixed point)
    assert remnant_chain(0.0, [(2.0, share, 0.69)], 1.0, 1.0, inf, wait=True) == 0.0
    # a constant share as before (Life Remnants)
    assert remnant_chain(0.0, [(3.0, 1.0, 0.69)], 1.0, 1.0, inf, wait=True) == pytest.approx(1 / 3, abs=1e-6)


def test_generic_on_hit_charge_lines_count_the_other_groups_hits():
    """(MECHC46, an UNDERCOUNT before) "N% chance to gain a <kind> Charge on Hit / on Critical Hit" from an item or a
    passive fires on any of your hits: the other groups' hits (stats["_side_hits"]) add their own entries after the main
    skill's one-a-use join, the lines of one kind joined as one chance a side use; a gem's own line and a conditioned
    line ("on Hit while Bleeding") add none; the main skill's lines still join as before; a side Orb of Storms /
    Galvanic Field isn't a side hitter (their casts aren't the hits)."""
    from builder.search import conditions as c

    side = {"name": "Side", "hits": 2.0, "crits": 0.5, "melee": False, "flagged": False, "how": "Side 2.00 hits/s"}
    stats = {"Speed": 1.0, "HitChance": 100.0, "CritChance": 10.0, "_side_rows": [side]}
    hit = "20% chance to gain a Frenzy Charge on Hit"
    crit = "20% chance to gain a Power Charge on Critical Hit"
    main = c._charge_rate(hit, "Frenzy", stats)
    assert main[0] == pytest.approx(0.2)  # (the main skill's hits alone in the line's own rate)
    got = c._side_hit_gains([(hit, "Frenzy", main)], stats)
    assert len(got) == 1 and got[0][2][0] == pytest.approx(0.2 * 2.0)
    got = c._side_hit_gains([(crit, "Power", c._charge_rate(crit, "Power", stats))], stats)
    assert got[0][2][0] == pytest.approx(0.2 * 0.5)  # (the side crits: 2 hits/s x 25%)
    # two 50% Frenzy lines: one chance a side use, 1 - 0.5 x 0.5 = 0.75 a hit, not 1.0
    two = ["50% chance to gain a Frenzy Charge on Hit", "50% chance to gain a Frenzy Charge on Hit "]
    got = c._side_hit_gains([(t, "Frenzy", c._charge_rate(t, "Frenzy", stats)) for t in two], stats)
    assert sum(g[2][0] for g in got) == pytest.approx(2.0 * 0.75)
    assert c._side_hit_gains([(hit, "Frenzy", main)], {**stats, "_gem_line_texts": {hit}}) == []
    bleeding = "Gain a Frenzy Charge on Hit while Bleeding"  # (side hits x the share you're Bleeding, MECHC46 turn 3)
    assert c._side_hit_gains([(bleeding, "Frenzy", c._charge_rate(bleeding, "Frenzy", stats))], stats) == []
    bled = {**stats, "_you_bleeding": (0.25, "x")}
    got = c._side_hit_gains([(bleeding, "Frenzy", c._charge_rate(bleeding, "Frenzy", bled))], bled)
    assert got[0][2][0] == pytest.approx(2.0 * 0.25)
    # a flagged side skill: each hit may grant, its lines added, not joined: 2 hits/s x (0.5 + 0.5)
    flag = {**stats, "_side_rows": [{**side, "flagged": True}]}
    got = c._side_hit_gains([(t, "Frenzy", c._charge_rate(t, "Frenzy", flag)) for t in two], flag)
    assert sum(g[2][0] for g in got) == pytest.approx(2.0)
    # the main skill's two lines still join (the side hits don't sit inside their rates)
    found = [(t, "Frenzy", c._charge_rate(t, "Frenzy", stats)) for t in two]
    joined, how = c._per_use_joined(found, stats)
    assert sum(f[2][0] for f in joined) == pytest.approx(0.75) and "joined" in joined[0][2][1]
    rows = {1: {"name": "Main", "speed": 5.0}, 2: {"name": "Side", "speed": 9.0, "cooldown": 2.0, "HitChance": 50.0,
                                                   "CritChance": 20.0},
            3: {"name": "Orb of Storms", "speed": 2.0}, 4: {"name": "Galvanic Field", "speed": 2.0}}
    groups = [{"index": 1, "gems": [{"name": "Main"}]}, {"index": 2, "gems": [{"name": "Side"}]},
              {"index": 3, "gems": [{"name": "Orb of Storms"}]}, {"index": 4, "gems": [{"name": "Galvanic Field"}]}]
    hits, crits, how = c.side_hits(rows, groups, {"Main", "Side", "Orb of Storms", "Galvanic Field"}, 1)
    assert hits == pytest.approx(0.25) and crits == pytest.approx(0.05) and "Side" in how and "Orb" not in how


def test_infusions_expire_after_their_duration():
    """(MECHC46, a small OVERCOUNT before) Each Infusion lasts 20 s (KeywordPopups ElementalInfusion), x "N% increased
    Elemental Infusion duration"; "Infusions do not expire" stops it. One shared timer per element, refreshed by each
    pickup (RESEARCH36 R36-8). Frequent uses: no change (the stock drains within cap / uses s of the last pickup); rare
    uses lose the expired ones."""
    import math

    from builder.search.assumptions import infusion_found, infusion_seconds

    assert infusion_seconds([]) == 20.0
    assert infusion_seconds(["30% increased Elemental Infusion duration", "10% reduced Elemental Infusion duration"]) == 24.0
    assert infusion_seconds(["Fire Infusions do not expire"], "Fire") == math.inf
    assert infusion_seconds(["Fire Infusions do not expire"], "Cold") == 20.0
    inf = math.inf
    assert infusion_found(1.0, 1.0, 0.0, 3, 20.0) == infusion_found(1.0, 1.0, 0.0, 3, inf)  # (3 s wait at most)
    rare = infusion_found(0.05, 0.05, 0.0, 3, 20.0)
    assert rare < infusion_found(0.05, 0.05, 0.0, 3, inf) - 0.15
    assert rare < infusion_found(0.05, 0.05, 0.0, 3, 26.0) < infusion_found(0.05, 0.05, 0.0, 3, inf)
    assert infusion_found(0.05, 0.05, 0.0, 3, 20.0) == rare  # (seeded: the same answer every time)


def test_a_charge_on_hit_while_bleeding_reads_the_share_you_bleed():
    """(MECHC46, an OVERCOUNT before: every hit) "Gain a Frenzy Charge on Hit while Bleeding": the main skill's hits x the
    share of the fight you're Bleeding (stats["_you_bleeding"]); not known yet: 0."""
    from builder.search import conditions as c

    line = "Gain a Frenzy Charge on Hit while Bleeding"
    stats = {"Speed": 1.0, "HitChance": 100.0, "CritChance": 0.0}
    assert c._charge_rate(line, "Frenzy", stats)[0] == 0.0
    assert c._charge_rate(line, "Frenzy", {**stats, "_you_bleeding": (0.4, "the boss's Bleeds")})[0] == pytest.approx(0.4)
    assert c._charge_rate("Gain a Frenzy Charge on Hit", "Frenzy", stats)[0] == pytest.approx(1.0)


def test_rage_on_melee_hit_counts_the_other_groups_melee_hits():
    """(MECHC46 turn 3, an UNDERCOUNT before) "Gain 1 Rage on Melee Hit": the main skill's hits plus the other groups'
    Melee hits (stats["_side_rows"]' melee), under the one gain every 0.5 s cap; a non-Melee main skill's own hits out,
    the side Melee hits still counted; "on Hit" takes every side skill."""
    from builder.search import conditions as c

    rows = [{"name": "Slam", "hits": 0.5, "crits": 0.0, "melee": True, "flagged": False, "how": "Slam 0.50 hits/s"},
            {"name": "Bolt", "hits": 0.3, "crits": 0.0, "melee": False, "flagged": False, "how": "Bolt 0.30 hits/s"}]
    stats = {"Speed": 1.0, "HitChance": 100.0, "CritChance": 0.0, "_main_types": {"Melee"}, "_side_rows": rows}
    melee = "Gain 1 Rage on Melee Hit"
    assert c._rage_gain(melee, stats, True)[1] == pytest.approx(1.5)
    assert c._rage_gain(melee, {**stats, "_side_rows": None}, True)[1] == pytest.approx(1.0)
    assert c._rage_gain("Gain 1 Rage on Hit", stats, True)[1] == pytest.approx(1.8)
    spell = {**stats, "_main_types": {"Spell"}}
    assert c._rage_gain(melee, spell, True)[1] == pytest.approx(0.5)
    assert c._rage_gain(melee, {**spell, "_side_rows": rows[1:]}, True)[1] == 0.0
    assert c._rage_gain(melee, {**stats, "Speed": 5.0}, True)[1] == pytest.approx(2.0)  # (the 0.5 s cap)


def test_refracted_infusion_gains_another_different_infusion():
    """(MECHC46, an UNDERCOUNT before) Refracted Infusion's "When collecting an Elemental Infusion, gain another different
    Elemental Infusion" (no chance named: every pickup) reads as the "N% chance ..." lines do: one more, of either other
    element evenly."""
    from builder.search.assumptions import OTHER_INFUSION

    m = OTHER_INFUSION.search("When collecting an Elemental Infusion, gain another different Elemental Infusion")
    assert m and float(m.group(1) or 100.0) == 100.0
    m = OTHER_INFUSION.search("25% chance when collecting an Elemental Infusion to gain another different Elemental "
                              "Infusion")
    assert m and float(m.group(1)) == 25.0


def test_boss_preset_adds_carry_their_life():
    """(MECHC46; RESEARCH36 R36-6) bosses.Kills' Life: Atziri's Royal Acolyte 32,956; the King's headline the Affliction
    Totem 1,318 with the Mystic Fetish 16,478 as a row; no adds: 0; kill_stats carries it as AddLife."""
    from builder.search import bosses

    assert bosses.add_life("Atziri, the Red Queen")[0] == 32956.0
    life, how, rows = bosses.add_life("The King in the Mists")
    assert life == 1318.0 and rows and rows[0][1] == 16478.0
    assert bosses.add_life("Zarokh, the Temporal")[0] == 0.0
    assert bosses.kill_stats("The King in the Mists")[bosses.ADD_LIFE_STAT] == 1318.0


def test_additional_infusion_chance_reads_the_two_line_wording():
    """(MECHC47, an UNDERCOUNT before) additional_infusion_gain_chance_% comes as "N% chance when collecting an Elemental
    Infusion to gain an" / "additional Elemental Infusion of the same type" (the tree's infusion2 / 6 / 7, the Kurgal
    focus suffix): its first line carries the chance, the same element's supply rises by it."""
    from builder.search.assumptions import infusion_supply

    present = {"Living Bomb": {}}
    plain = infusion_supply(present, [], set(), bomb=(0.2, "test"))[0]["Fire"]
    lines = ["5% chance when collecting an Elemental Infusion to gain an", "additional Elemental Infusion of the same type"]
    assert infusion_supply(present, lines, set(), bomb=(0.2, "test"))[0]["Fire"] == pytest.approx(plain * 1.05)
    joined = ["25% chance when collecting an Elemental Infusion to gain an additional Elemental Infusion of the same type"]
    assert infusion_supply(present, joined, set(), bomb=(0.2, "test"))[0]["Fire"] == pytest.approx(plain * 1.25)


def test_infusion_collected_damage_at_its_share_of_the_fight(monkeypatch):
    """(MECHC47, an UNDERCOUNT before: PoB parses it only in part) "50% increased Cold Damage if you've collected a Cold
    Infusion in the last 8 seconds" at 1 - e^(-pickups x 8) of its value; no Cold Infusions: nothing."""
    import math

    from builder.search import assumptions, models

    class FakeBuild:
        def call(self, name, **kw):
            return {"activeSkills": {"skills": [{"name": "Frost Bomb", "disabled": False, "cooldownRecovery": 1.0}]},
                    "sourceLines": {"lines": []}, "getConfig": {}, "stats": {}}.get(name, {})

    monkeypatch.setattr(models, "ailment_up_of", lambda build: ({}, ""))
    a = assumptions.Assumptions.__new__(assumptions.Assumptions)
    a.build = FakeBuild()
    line = "50% increased Cold Damage if you've collected a Cold Infusion in the last 8 seconds"
    mods, notes = a.infusion_collected_mods([line])
    rate = assumptions.REMNANT_SOURCES["Frost Bomb"][0]
    share = 1 - math.exp(-rate * 8)
    assert mods and mods[0]["name"] == "ColdDamage" and mods[0]["value"] == pytest.approx(50 * share, abs=0.01)
    fire = "50% increased Fire Damage if you've collected a Fire Infusion in the last 8 seconds"
    mods, notes = a.infusion_collected_mods([fire])
    assert not mods and "no Fire Infusions" in notes[0]["basis"]


def test_druidic_prowess_and_hateforge_charges():
    """(MECHC47) Druidic Prowess: 1 per 20 Rage spent at 2 Rage/s is 1 stack held (10 s each); 2 a Heavy Stun at 0.05
    stuns/s is E[min(3, 2 x Poisson(0.5))]. Hateforge: the maximum reached every 2 s with a 4 s cooldown gives one random
    Charge every 4 s, a third of them each kind."""
    import math

    from builder.search import conditions

    spent = "Gain 1 Druidic Prowess for every 20 total Rage spent"
    stun = "Gain 2 Druidic Prowess when you Heavy Stun a Rare or Unique Enemy"
    assert conditions.druidic_prowess([spent], (2.0, "x"), None)[0] == pytest.approx(1.0)
    assert conditions.druidic_prowess([spent], (10.0, "x"), None)[0] == pytest.approx(3.0)
    p0, p1 = math.exp(-0.5), 0.5 * math.exp(-0.5)
    assert conditions.druidic_prowess([stun], None, (0.05, "y"))[0] == pytest.approx(p1 * 2 + (1 - p0 - p1) * 3)
    assert conditions.druidic_prowess(["Gain 5 Rage on Hit"], (2.0, "x"), None) == (0.0, "")
    line = "Gain a random Charge on reaching Maximum Rage, no more than once every 4 seconds"
    got = conditions.rage_max_charges([line], {"_rage_max_reaches": (0.5, "test")}, ["Power", "Frenzy"])
    assert [g[1] for g in got] == ["Power", "Frenzy"] and got[0][2][0] == pytest.approx(1 / 4 / 3)
    got = conditions.rage_max_charges([line], {"_rage_max_reaches": None}, ["Power"])
    assert got[0][2][0] == 0.0 and "UNVERIFIED" in got[0][2][1]


def test_the_hammer_of_faiths_shrine_uptime():
    """(MECHC47; RESEARCH37 R37-10) Diamond / Acceleration are picked 30 / 320 a roll (the Shrines table's weights): one
    Hammer 2 rolls a buff's 20 s, up 1 - (1 - 0.09375)^2 = 17.9%; an even pick of 8 (the row) 23.4%; two Hammers 4 rolls;
    +50% Shrine Effect duration 3 rolls."""
    from builder.search import conditions

    line = "Every 10 seconds, gain a random non-damaging Shrine buff for 20 seconds"
    p = 30 / 320
    assert conditions.SHRINE_PICK == pytest.approx(p)
    assert conditions.shrine_uptime([line])[0] == pytest.approx(1 - (1 - p) ** 2)
    assert round(conditions.shrine_uptime([line])[0], 3) == 0.179
    assert conditions.shrine_uptime([line], conditions.SHRINE_PICK_EVEN)[0] == pytest.approx(0.234375)
    assert conditions.shrine_uptime([line, line])[0] == pytest.approx(1 - (1 - p) ** 4)
    assert conditions.shrine_uptime([line, "50% increased Duration of Shrine Effects on you"])[0] == pytest.approx(
        1 - (1 - p) ** 3)
    assert conditions.shrine_uptime(["Gain 5 Rage on Hit"]) == (0.0, "")


def test_demon_stitcher_moves_life_into_energy_shield_per_spell_cast():
    """(MECHC47, an UNDERCOUNT of ES income and of Life spent before) "Sacrifice N% of maximum Life to gain that much
    Energy Shield when you Cast a Spell": N% x Life x the main Spell's casts a second, out of Life, into Energy Shield;
    no Spell cast: 0 both ways, said."""
    from builder.search import recovery

    line = "Sacrifice 10% of maximum Life to gain that much Energy Shield when you Cast a Spell"
    income = recovery.Income()
    recovery.life_sacrifice_income(income, [line], 2000.0, 0.25, 1.5, "test")
    assert income.rates["LifeSacrificed"] == pytest.approx(-300.0)
    assert income.rates["EnergyShieldOnEventRecovery"] == pytest.approx(300.0)
    income = recovery.Income()
    recovery.life_sacrifice_income(income, [line], 2000.0, 0.25, 0.0, "not a Spell")
    assert income.rates["LifeSacrificed"] == 0.0 and "casts no Spell" in income.parts[0]


def test_igniferis_bursts_its_regeneration_back():
    """(MECHC47, an UNDERCOUNT before: PoB zeroes the Regeneration but never adds the bursts) "Every 4 seconds, Recover 1
    Life for every 0.2 Life Recovery per second from Regeneration" at 50 Life Regeneration: 50 / 0.2 / 4 = 62.5 a second."""
    from builder.search import recovery

    income = recovery.Income()
    recovery.regen_burst_income(income, ["Every 4 seconds, Recover 1 Life for every 0.2 Life Recovery per second from "
                                         "Regeneration"], 50.0)
    assert income.rates["LifeOnEventRecovery"] == pytest.approx(62.5)
    income = recovery.Income()
    recovery.regen_burst_income(income, ["Every 4 seconds, Recover 1 Life for every 0.2 Life Recovery per second from "
                                         "Regeneration"], 0.0)
    assert income.rates["LifeOnEventRecovery"] == 0.0


def test_sacred_unitys_soaring_ground_trail_gives_onslaught_at_the_feather_rate():
    """MECHC49 (the manager's 03:05, PLANS6; an UNDERCOUNT: nothing read the line): Sacred Unity's trail of Soaring
    Ground gives you Onslaught (KeywordPopups SoaringGround) at the empowered Projectile uses a second (one per Owl
    Feather from Primal Bounty, 50% faster with The Mhacha's Gift) x (6 s + 1 s Linger); 0 without Primal Bounty or a
    Projectile main skill."""
    from builder.search import conditions as c

    unity = "Central Projectile of Owl Feather-Empowered Skills leaves a trail of Soaring Ground"
    bounty = "Gain a Primal Owl Feather every 4 seconds, up to a maximum of 3"
    stats = {"Speed": 2.0, "HitChance": 100, "_main_types": {"Attack", "Projectile"}}
    up, how = c.sacred_unity_onslaught([unity, bounty], stats)
    assert up == 1.0 and "Soaring Ground" in how  # (0.25 a second x 7 s: the trail covers the fight)
    slow = c.sacred_unity_onslaught([unity, bounty], {**stats, "Speed": 0.1})  # (the main skill's own uses cap it)
    assert slow[0] == pytest.approx(0.1 * 7)
    fast = c.sacred_unity_onslaught([unity, bounty, "Gain Owl Feathers 50% faster"], stats)
    assert fast[0] == 1.0 and "0.375/s" in fast[1] and "every 2.67 s" in fast[1]
    assert c.sacred_unity_onslaught([unity], stats)[0] == 0.0
    assert c.sacred_unity_onslaught([unity, bounty], {**stats, "_main_types": {"Attack"}})[0] == 0.0
    assert c.sacred_unity_onslaught([bounty], stats) is None
    value, basis = c.onslaught_uptime([unity, bounty], stats)[:2]
    assert value is True and "Soaring Ground" in basis


def test_inhibited_skills_are_the_other_gems_of_a_group_with_an_enabled_inhibitor():
    """MECHC49 (R40-2): conditions.inhibited_skills: every other enabled gem of an enabled group holding an enabled
    Inhibitor; a disabled Inhibitor or a disabled group supports nothing."""
    from builder.search import conditions as c

    groups = [{"enabled": True, "gems": [{"name": "Arc"}, {"name": "Inhibitor"}, {"name": "Martial Tempo", "enabled": False}]},
              {"enabled": True, "gems": [{"name": "Fireball"}, {"name": "Inhibitor", "enabled": False}]},
              {"enabled": False, "gems": [{"name": "Ice Nova"}, {"name": "Inhibitor"}]},
              {"enabled": True, "gems": [{"name": "Spark"}]}]
    assert c.inhibited_skills(groups) == {"Arc"}
