"""SEARCH15 (SEARCH14's Handover; glory_uses_capped's "not charged here", routed): a Glory skill the build counts a share
from is pressed, and its presses take the main skill's time: uses x 1 / its own speed, in objective.glory_busy
(Objective.busy), except where that time is charged already (the main skill, a pressed Full DPS group: press_model; a
chosen press rate: Search.press_sync) or the player doesn't press it (a meta skill or a totem uses it)."""
from types import SimpleNamespace

import pytest

from builder.search import models
from builder.search.assumptions import Assumptions
from builder.search.objective import Objective

GEMS = {"Molten Blast": {"skillTypes": ["Attack", "Area"], "support": False},
        "Fireball": {"skillTypes": ["Spell", "Projectile"], "support": False},
        "Cast on Ignite": {"skillTypes": ["Meta", "Triggers"], "support": False}}


def gem(name, support=False):
    return {"name": name, "enabled": True, "support": support}


class Build:
    def __init__(self, full=False, meta=False, choices=None):
        self.objective = SimpleNamespace(full=full, press_choices=dict(choices or {}), glory_busy=0.0)
        self.ctx = SimpleNamespace(gems=GEMS)
        blast = [gem("Molten Blast")] + ([gem("Cast on Ignite")] if meta else [])
        self._groups = [{"index": 1, "main": False, "enabled": True, "fullDPS": full, "fromItem": False, "gems": blast},
                        {"index": 2, "main": True, "enabled": True, "fullDPS": True, "fromItem": False,
                         "gems": [gem("Fireball")]}]

    def groups(self):
        return self._groups

    def call(self, name, **kw):
        assert name == "useRates"
        return {"rates": [{"index": 1, "speed": 4.0}]}


def busy(monkeypatch, **kw) -> float:
    monkeypatch.setattr(models, "glory_uses", lambda names, *more: (2.0 if "Molten Blast" in names else 0.0, "x"))
    a = Assumptions.__new__(Assumptions)
    a.build = Build(**kw)
    total, how = a.glory_uses_capped(["Molten Blast", "Fireball"], {}, models.Skill(name="Fireball"))
    assert total == pytest.approx(2.0)
    return a.build.objective.glory_busy


def test_pressing_a_glory_skill_takes_the_main_skills_time(monkeypatch):
    assert busy(monkeypatch) == pytest.approx(2.0 / 4.0)  # (2 uses a second, each 1 / 4 s)


def test_glory_press_time_is_never_charged_twice_or_for_a_skill_nobody_presses(monkeypatch):
    assert busy(monkeypatch, full=True) == 0.0  # (a pressed Full DPS group: press_model charges it)
    assert busy(monkeypatch, choices={"Molten Blast": 1.0}) == 0.0  # (a chosen rate: press_sync charges it)
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
    monkeypatch.setattr(Assumptions, "glory_caps", lambda self: {"Molten Blast": (0.4, "one per 2.5 s")})
    a = Assumptions.__new__(Assumptions)
    a.build = Build(full=True)
    a.build._groups[1]["gems"].append(gem("Molten Blast"))
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
    """(MECHC45) Molten Blast's Glory comes from the main skill's hits (2 uses a second at the whole fight, here
    proportional to the main skill's share), and each press takes 1 / 4 s from it: the busy share g = 2 (1 - g) / 4,
    so g = 1/3 and 4/3 uses a second (before: g = 1/2 at the main skill's full Speed, 2 uses a second)."""
    monkeypatch.setattr(models, "glory_uses", lambda names, stats, *more: (
        2.0 * (1.0 if stats.get("_main_share") is None else stats["_main_share"]) if "Molten Blast" in names else 0.0,
        "x"))
    a = Assumptions.__new__(Assumptions)
    a.build = Build()
    o = SimpleNamespace(full=False, press_choices={}, press_busy=0.0, glory_busy=0.0, press_scales={})
    o.busy = lambda: min(o.press_busy + o.glory_busy, 1.0)  # (as Objective.busy)
    a.build.objective = o
    total, how = a.glory_uses_capped(["Molten Blast", "Fireball"], {}, models.Skill(name="Fireball"))
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

    stats = {"Speed": 1.0, "HitChance": 100.0, "CritChance": 10.0, "_side_hits": (2.0, 0.5, "Side 2.00 hits/s")}
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
    bleeding = "Gain a Frenzy Charge on Hit while Bleeding"
    if (r := c._charge_rate(bleeding, "Frenzy", stats)) is not None:
        assert c._side_hit_gains([(bleeding, "Frenzy", r)], stats) == []
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
    Elemental Infusion duration"; "Infusions do not expire" stops it. Frequent uses: no change (an Infusion waits at most
    cap / uses s, oldest first); rare uses lose the expired ones."""
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
