"""MECH101: Archon of Chayula's Withered on Hit (conditions.CHAYULA_WITHER_STAT), at the Archon's share of the fight."""
import pytest
from builder.search import conditions as c, models

BASE = {"Speed": 2.0, "HitChance": 100.0}



def test_the_archon_cycle_takes_the_later_of_the_glory_refill_and_the_recovery_period():
    instance = models.GLORY_INSTANCE[models.ARCHON_OF_CHAYULA]
    assert instance == 10.0
    # Glory refills in 30 s, past the 20 s recovery: one Archon per 40 s, up 10 / 40
    rate, up, row, _ = models.chayula_cycle(1 / 40)
    assert rate == pytest.approx(1 / 40) and up == pytest.approx(0.25) and row == pytest.approx(0.25)
    # Glory refills in 5 s: the 20 s recovery decides (R48-10): one per 30 s, 1/3; without it (the row) 10 / 15
    rate, up, row, how = models.chayula_cycle(1 / 15)
    assert rate == pytest.approx(1 / 30) and up == pytest.approx(1 / 3) and row == pytest.approx(2 / 3)
    assert "recovery period" in how
    # the build's Archon lines: 50% longer, recovery 100% faster (10 s), no recovery period at all
    assert models.chayula_cycle(1 / 15, 50.0)[1] == pytest.approx(15 / 35)
    assert models.chayula_cycle(1 / 15, 0.0, 100.0)[1] == pytest.approx(10 / 20)
    assert models.chayula_cycle(1 / 15, no_recovery=True)[1] == pytest.approx(2 / 3)
    assert models.chayula_cycle(0.0)[:3] == (0.0, 0.0, 0.0)


def test_chayulas_will_hits_while_the_archon_is_up():
    # a 10 s Archon: tornadoes at 3, 6 and 9 s live 7 + 4 + 1 = 12 s of it, an attack every 0.2 s: 60 in 10 s
    assert c.chayula_will_hits_during(10.0) == pytest.approx(6.0)
    # a 20 s Archon: 6 tornadoes (3..18 s): 8 + 8 + 8 + 8 + 5 + 2 = 39 s, 195 attacks in 20 s
    assert c.chayula_will_hits_during(20.0) == pytest.approx(195 / 20)
    assert c.chayula_will_hits_during(0.0) == 0.0


def test_the_archons_more_damage_at_its_share_of_the_fight():
    mods, notes = models.chayula_more_model(1 / 3, "test")
    assert [(m["line"], m["scale"]) for m in mods] == [("1% more Physical Damage", pytest.approx(6.667, abs=1e-3)),
                                                        ("1% more Chaos Damage", pytest.approx(6.667, abs=1e-3))]
    assert "20% more" in notes[0]["basis"]
    assert models.chayula_more_model(1 / 3, "x", 50.0)[0][0]["scale"] == pytest.approx(10.0)
    assert models.chayula_more_model(0.0, "no Archon")[0] == []


def test_the_tornados_hits_wither_too():
    with_will = c._withered_stacks([], {**BASE, "_chayula_wither": (25.0, 4.0, 1.0, 1.0, "test", 6.0)})
    assert with_will[0] == 8.0  # (25% x (2 + 6) hits/s x 4 s)


def test_the_archon_withers_the_boss_only_while_it_is_up():
    # 25% x 2 hits/s x 4 s = 2 stacks during the Archon
    full = c._withered_stacks([], {**BASE, "_chayula_wither": (25.0, 4.0, 1.0, 1.0, "test")})
    assert full[0] == 2.0 and "Archon of Chayula" in full[1]
    half = c._withered_stacks([], {**BASE, "_chayula_wither": (25.0, 4.0, 0.5, 0.75, "test")})
    assert half[0] == 1.0  # (the time average, 1.0 stacks; PoB's whole count)
    none = c._withered_stacks([], {**BASE, "_chayula_wither": (25.0, 4.0, 0.0, 0.0, "no Archons")})
    assert none[0] == 0.0
    assert c._withered_stacks([], dict(BASE))[0] == 0.0  # (no Archon: as before)


def test_the_archons_stacks_share_the_cap_of_ten_with_the_other_sources():
    lines = ["100% chance to inflict Withered for 4.5 seconds on Hit"]  # 2 hits/s x 4.5 s = 9 stacks
    alone = c._withered_stacks(lines, dict(BASE))
    assert alone[0] == 9.0
    # during the Archon min(10, 9 + 2) = 10, outside it 9: half the fight each, 9.5 on average (floored: 9)
    both = c._withered_stacks(lines, {**BASE, "_chayula_wither": (25.0, 4.0, 0.5, 1.0, "test")})
    assert both[0] == 9.0 and "9.5 stacks kept up" in both[1]
    whole = c._withered_stacks(lines, {**BASE, "_chayula_wither": (25.0, 4.0, 1.0, 1.0, "test")})
    assert whole[0] == 10.0


def test_the_archon_gives_pob_its_can_wither_flag():
    flag = [models.raw_mod("Condition:CanWither", "FLAG", 1)]
    assert c.wither_source_flags([], {"Attack"}, {models.ARCHON_OF_CHAYULA, "Quarterstaff Strike"}) == flag
    assert c.wither_source_flags([], {"Attack"}, {"Quarterstaff Strike"}) == []
    assert c.wither_source_flags([], {"Attack"}) == []


class _Ctx:
    gems = {"Despair": {"skillTypes": ["AppliesCurse", "Spell"]}, "Fireball": {"skillTypes": ["Spell"]},
            "Enfeeble": {"skillTypes": ["AppliesCurse"]}}


class _Build:
    """A stand-in: Despair (gem level 15, cap 74) just added by the search, Assumptions' saved caps from before it."""
    ctx = _Ctx()

    def __init__(self, saved, boss=0):
        self.curse_level_caps, self.curse_boss_level, self.calls = saved, boss, []

    def groups(self):
        return [{"gems": [{"name": "Despair"}, {"name": "Enfeeble"}]}, {"gems": [{"name": "Fireball"}]},
                {"enabled": False, "gems": [{"name": "Vulnerability"}]}]

    def call(self, what, **kw):
        self.calls.append((what, kw))
        if what == "activeSkills":
            return {"skills": [{"name": "Despair", "disabled": False, "levelStats": {models.CURSE_LEVEL_STAT: 74}},
                               {"name": "Enfeeble", "disabled": False, "levelStats": {}}]}
        if what == "stats":
            return {"LedgerEnemyLevel": 82}
        raise KeyError(what)


def test_a_curse_new_since_the_last_assumptions_pass_has_its_cap_read_at_once():
    build = _Build({})
    caps, boss = c.curse_caps_now(build)
    assert caps == {"Despair": 74.0, "Enfeeble": 0.0} and boss == 82
    assert build.calls[0] == ("activeSkills", {"levelStats": ["Despair", "Enfeeble"]})
    assert build.curse_level_caps == caps and build.curse_boss_level == 82  # (saved: not read again this pass)
    build.calls.clear()
    assert c.curse_caps_now(build) == (caps, 82) and build.calls == []


def test_saved_caps_for_every_curse_need_no_call():
    build = _Build({"Despair": 74.0, "Enfeeble": 0.0}, 82)
    assert c.curse_caps_now(build) == ({"Despair": 74.0, "Enfeeble": 0.0}, 82) and build.calls == []


def test_the_new_curse_is_capped_in_curse_uses_first_pass():
    stats = {"Speed": 1.0, "HitChance": 100, "_curse_level_caps": {"Despair": 74.0}, "_boss_level": 82}
    groups = [{"gems": [{"name": "Despair"}]}, {"gems": [{"name": "Fireball"}], "main": True}]
    capped = {u.name: u for u in models.curse_uses(groups, _Ctx.gems, stats, 3.0)}["Despair"]
    assert capped.share == 0.0


# MECH101, the flat-rate sweep: a curse a Spell Totem casts is up while a totem stands (models.totem_curse_up), not 100%
CURSE_GEMS = {"Despair": {"skillTypes": ["AppliesCurse", "Spell"]}, "Spell Totem": {"skillTypes": ["Meta", "SummonsTotem"]},
              "Fireball": {"skillTypes": ["Spell"]}}
TOTEM_STATS = {"Speed": 2.0, "HitChance": 100, "PowerChargesMax": 3, "EnduranceChargesMax": 3}


def _totem_curse(texts):
    skills = [models.Skill("Spell Totem", group=1), models.Skill("Despair", group=1, spell=True),
              models.Skill("Fireball", group=2, spell=True)]
    return models.totem_curse_up(skills, TOTEM_STATS, texts, {"Spell Totem": 20}, CURSE_GEMS)


def test_a_spell_totems_curse_follows_the_totems_the_build_charges_place():
    # a Power Charge every 4 s: 0.25/s x 8 s / 3 = 0.67 totems up on average
    up = _totem_curse(["Gain a Power Charge every 4 seconds"])
    assert up["Despair"][0] == pytest.approx(0.67, abs=0.01)
    # no charge source: no totem, no curse
    assert _totem_curse([])["Despair"][0] == 0.0
    # a curse cast by hand isn't a totem's
    skills = [models.Skill("Despair", group=1, spell=True)]
    assert models.totem_curse_up(skills, TOTEM_STATS, [], {}, CURSE_GEMS) == {}


def test_curse_uses_reads_the_totems_share_instead_of_always_on():
    groups = [{"gems": [{"name": "Spell Totem"}, {"name": "Despair"}]}, {"gems": [{"name": "Fireball"}], "main": True}]
    stats = {"Speed": 1.0, "HitChance": 100}
    old = {u.name: u for u in models.curse_uses(groups, CURSE_GEMS, stats, 3.0)}["Despair"]
    assert old.share == 1.0  # (not read: the old labelled reading stays)
    got = {u.name: u for u in models.curse_uses(groups, CURSE_GEMS, {**stats, "_totem_curse_up": {"Despair": (0.4, "x")}},
                                                3.0)}["Despair"]
    assert got.share == pytest.approx(0.4) and "Spell Totem" in got.how
    none = {u.name: u for u in models.curse_uses(groups, CURSE_GEMS, {**stats, "_totem_curse_up": {"Despair": (0.0, "x")}},
                                                 3.0)}["Despair"]
    assert none.share == 0.0
