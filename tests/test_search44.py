"""SEARCH44: the JUDGE's per-build fit check (the manager's option (iii), 27 Nov): fit probes first, then the additive
slot + gem rule where it fits the build, every flagged case where it doesn't. Pure (no PoB)."""
from __future__ import annotations

from types import SimpleNamespace


def _build(monkeypatch, real: dict, judge_fit=None):
    """A Build with one group (Spark + the support "Old" in slot 2); `real` maps the gem in slot 2 (or "REMOVED") to the
    score our models give it. Returns (build, tried names)."""
    from builder.search import state

    monkeypatch.setenv(state.JUDGE_SWITCH, "1")
    group = {"index": 1, "gems": [{"name": "Spark", "support": False, "enabled": True, "level": 20, "quality": 0},
                                  {"name": "Old", "support": True, "enabled": True, "level": 20, "quality": 0}]}
    names = [n for n in real if n != "REMOVED"] + ["Old"]
    build = object.__new__(state.Build)
    build.ctx = SimpleNamespace(gems={n: {"support": True} for n in names})
    build.groups = lambda: [group]
    build.started, build.judge_seconds, build.stopped, build.stop_at, build.phase = 0.0, 0.0, False, None, "supports"
    build.judge_log, build.gem_rescore_log, build.gem_rescored, build.gem_rescore_restores = [], [], 0, 0
    build.judge_fit = judge_fit if judge_fit is not None else {"n": 0, "fit": 0}
    build.score = 0.0
    now = {"gem": "Old"}
    tried = []
    build.objective = SimpleNamespace(fields=[], prepare=lambda s: s, score=lambda s: real[now["gem"]])
    build.added_stats = lambda: {}
    build.snapshot = lambda: {}
    build.refresh = lambda: 0.0
    build.gem_settle = lambda: None

    def call(method, **kw):
        if method == "stats":
            return {"Speed": 1.0}
        if method == "setGroup":
            slot2 = kw["gems"][1]
            gem = slot2["name"] if slot2.get("enabled", True) else "REMOVED"
            if gem != "Old" or now["gem"] == "Old":
                tried.append(gem)
            now["gem"] = gem
        return {}
    build.call = call
    monkeypatch.setattr(state.clock, "now", lambda: 1000.0)
    return build, tried


def _cases(whatif: dict):
    cases, scored = [], []
    for gem, w in whatif.items():
        cases.append({"group": 1, "slot": 2, "remove": True} if gem == "REMOVED" else {"group": 1, "slot": 2, "gem": gem})
        scored.append((w, {"Speed": 2.0}))  # (every case moves a model input: all flagged, as on 2b75b / 2b75e)
    return cases, scored


def test_judge_uses_the_slot_rule_where_it_fits_and_scores_the_rest_at_its_prediction(monkeypatch):
    """The slot's support is worth 0.6 more on ours than the screen says (a = -0.6 for every replacement); the
    probes (remove + top 2 + 2 spread) fit exactly, so the rule decides the rest: a case predicted within the margin (or
    an unseen gem within the wider net) is tried, the others are scored at screen + a, never at their screen gain."""
    from builder.search import state

    for name, value in (("JUDGE_TOP", 2), ("JUDGE_FIT_SAMPLE", 2), ("JUDGE_FIT_MIN_N", 3)):
        monkeypatch.setattr(state, name, value)
    whatif = {"REMOVED": 0.3, "G1": 1.0, "G2": 0.9, "G3": 0.7, "G4": 0.5, "G5": 0.2, "G6": 0.0, "G7": -0.2, "G8": -0.4}
    real = {"Old": 0.0, **{g: round(w - 0.6, 6) for g, w in whatif.items()}}
    real["REMOVED"] = -0.3
    build, tried = _build(monkeypatch, real)
    cases, scored = _cases(whatif)
    out = build._rescore_model_gems(cases, list(scored), judge=True)
    # probes: REMOVED, G1, G2 (top 2), then 2 spread over the other 6 (G4, G6): 4 fit cases, all within the tolerance
    assert tried[:5] == ["REMOVED", "G1", "G2", "G4", "G6"]
    # the rule: unseen gems need screen - 0.6 > -0.55: G3 (+0.1) and G5 (-0.4) are tried, G7 / G8 are not
    assert tried[5:] == ["G3", "G5"]
    got = {g: round(out[i][0], 6) for i, g in enumerate(whatif)}
    assert got["G7"] == round(-0.2 - 0.6, 6) and got["G8"] == round(-0.4 - 0.6, 6)
    assert got["G1"] == real["G1"] and got["REMOVED"] == -0.3
    row = build.judge_log[-1]
    assert (row["tried"], row["skipped"], row["cut"]) == (7, 2, 0)
    assert row["fit"]["mode"] == "rule" and row["fit"]["share"] == 1.0 and build.judge_fit == {"n": 6, "fit": 6}
    assert sum(1 for r in build.gem_rescore_log if "predicted" in r) == 2


def test_judge_tries_every_flagged_case_where_the_slot_rule_does_not_fit(monkeypatch):
    """The same screen, but ours scatters around screen + a (2b75b's 28% fit): every flagged case gets a real trial."""
    from builder.search import state

    for name, value in (("JUDGE_TOP", 2), ("JUDGE_FIT_SAMPLE", 2), ("JUDGE_FIT_MIN_N", 3)):
        monkeypatch.setattr(state, name, value)
    whatif = {"REMOVED": 0.3, "G1": 1.0, "G2": 0.9, "G3": 0.7, "G4": 0.5, "G5": 0.2, "G6": 0.0, "G7": -0.2, "G8": -0.4}
    noise = {"G1": 0.3, "G2": -0.4, "G4": 0.5, "G7": -0.3, "G3": 0.0, "G5": 0.2, "G6": 0.9, "G8": 0.1}
    real = {"Old": 0.0, "REMOVED": -0.3, **{g: w - 0.6 + noise[g] for g, w in whatif.items() if g != "REMOVED"}}
    build, tried = _build(monkeypatch, real)
    cases, scored = _cases(whatif)
    out = build._rescore_model_gems(cases, list(scored), judge=True)
    assert sorted(tried) == sorted(whatif)  # (every case, the buried gain G6 included)
    assert round(out[list(whatif).index("G6")][0], 6) == round(0.0 - 0.6 + 0.9, 6)
    row = build.judge_log[-1]
    assert row["fit"]["mode"] == "every" and row["skipped"] == 0 and row["cut"] == 0


def test_judge_waits_for_enough_fit_cases_and_keeps_the_tally_across_screens(monkeypatch):
    """Under JUDGE_FIT_MIN_N cases the rule is not used (every case tried); the tally is the build's, so a later screen
    with the tally already full may use it from its first non-probe case."""
    from builder.search import state

    for name, value in (("JUDGE_TOP", 1), ("JUDGE_FIT_SAMPLE", 1), ("JUDGE_FIT_MIN_N", 10)):
        monkeypatch.setattr(state, name, value)
    whatif = {"REMOVED": 0.3, "G1": 1.0, "G2": 0.5, "G3": -0.4, "G4": -0.5}
    real = {"Old": 0.0, "REMOVED": -0.3, **{g: w - 0.6 for g, w in whatif.items() if g != "REMOVED"}}
    build, tried = _build(monkeypatch, real)
    cases, scored = _cases(whatif)
    build._rescore_model_gems(cases, list(scored), judge=True)
    assert sorted(tried) == sorted(whatif) and build.judge_log[-1]["fit"]["mode"] == "every"
    build2, tried2 = _build(monkeypatch, real, judge_fit={"n": 20, "fit": 19})
    build2._rescore_model_gems(cases, list(scored), judge=True)
    assert build2.judge_log[-1]["fit"]["mode"] == "rule" and "G4" not in tried2


def test_a_new_slot_has_no_offset_and_a_slot_without_a_remove_case_is_always_tried():
    from builder.search.state import Build

    group = {"index": 3, "gems": [{"name": "Spark"}, {"name": "Old"}]}
    assert Build._judge_slot({"slot": 3}, group) == (3, 3, None)
    assert Build._judge_slot({"slot": 2}, group) == (3, 2, "Old")
    build = object.__new__(Build)
    build.judge_fit = {"n": 50, "fit": 50}
    fit = {"a": {}, "b": {}, "mode": None}
    assert build._judge_predict(fit, {"slot": 2, "gem": "X"}, group, "X", -3.0) is None  # (no a for "Old": tried)
    assert build._judge_predict(fit, {"slot": 3, "gem": "X"}, group, "X", -3.0) == -3.0  # (new slot: a = 0)


# --- damage flasks are buttons (THE USER, 27 Nov ~10:45) ---------------------------------------------------------------

MAELSTROM_MOD = {"kind": "Derived", "name": "Maelstrom", "fn": "maelstrom", "value": 120000, "args": {}}
MAELSTROM_RAW = ("Rarity: UNIQUE\nMelting Maelstrom\nUltimate Mana Flask\n{variant:3}Every 3 seconds during Effect, deal 100% "
                 "of Mana spent in those seconds as Chaos Damage to Enemies within 3 metres")


def test_a_flask_counted_for_its_damage_is_a_button_and_one_drunk_for_recovery_is_not():
    from builder.search.gems import damage_flask_buttons

    assert damage_flask_buttons([MAELSTROM_MOD]) == ["Flask: Melting Maelstrom"]
    assert damage_flask_buttons([MAELSTROM_MOD, dict(MAELSTROM_MOD)]) == ["Flask: Melting Maelstrom"]
    assert damage_flask_buttons([{**MAELSTROM_MOD, "value": 0}]) == []  # (no Mana spent: no pulse, no damage use)
    assert damage_flask_buttons([{"kind": "Derived", "fn": "manaTempest", "value": 5}]) == [] and damage_flask_buttons(None) == []


def test_a_capped_request_with_maelstrom_in_an_active_slot_uses_one_button_for_it():
    from builder.request.constraints import player_buttons

    spark = {"name": "Spark", "support": False, "enabled": True, "skillTypes": ["Spell", "Damage", "Projectile"]}
    facts = {"skillGroups": [{"enabled": True, "main": True, "fromItem": False, "gems": [spark]}]}
    assert player_buttons(facts) == ["Spark"]
    assert player_buttons({**facts, "damageFlasks": ["Flask: Melting Maelstrom"]}) == ["Spark", "Flask: Melting Maelstrom"]


def test_the_search_counts_the_damage_flask_in_its_own_button_list(monkeypatch):
    from builder.search import gems

    monkeypatch.setattr(gems, "pressed_item_skills", lambda build: set())
    g = object.__new__(gems.Gems)
    g.build = SimpleNamespace(groups=lambda: [])
    g.ctx = SimpleNamespace(any_gems={})
    g.assumptions = SimpleNamespace(mods=[MAELSTROM_MOD])
    assert g.buttons() == ["Flask: Melting Maelstrom"]
    g.assumptions = None
    assert g.buttons() == []


def test_over_the_cap_the_search_may_switch_the_damage_flask_off_and_it_stays_off(monkeypatch):
    """A cap of 1 with Spark and a Maelstrom: the only way out is the flask's slot off (kept off by Gear.counts)."""
    from builder.search import gear as gear_mod
    from builder.search import inner

    synced = []
    build = SimpleNamespace(equipped={"Flask 2": SimpleNamespace(raw=MAELSTROM_RAW)}, score=1.0,
                            groups=lambda: [], objective=SimpleNamespace(score=lambda s: 1.0), stats={},
                            ctx=SimpleNamespace(request=SimpleNamespace(max_buttons=1)))
    # (chargeGain's "active": the slot is on unless the cap switched it off)
    build.call = lambda method, **kw: {"active": {"Flask 2": not getattr(build, gear_mod.CAPPED_FLASKS, None)}}
    g = object.__new__(gear_mod.Gear)
    g.build = build
    g.share = lambda slot, info, raw: 1.0
    g.sync_flasks = lambda: synced.append(dict(getattr(build, gear_mod.CAPPED_FLASKS, {})))
    assert g.counts("Flask 2", {}, MAELSTROM_RAW) is True
    assert g.damage_flask_slots() == ["Flask 2"]
    search = object.__new__(inner.Search)
    search.build, search.gear, search.settle = build, g, lambda: None
    search.assumptions = SimpleNamespace(mods=[MAELSTROM_MOD])

    def buttons():
        off = (getattr(build, gear_mod.CAPPED_FLASKS, None) or {}).get("Flask 2")
        return ["Spark"] + ([] if off else ["Flask: Melting Maelstrom"])
    search.gems = SimpleNamespace(buttons=buttons)
    assert search.fit_buttons() is True
    assert getattr(build, gear_mod.CAPPED_FLASKS) == {"Flask 2": MAELSTROM_RAW}
    assert g.counts("Flask 2", {}, MAELSTROM_RAW) is False and g.counts("Flask 2", {}, "another flask") is True
    assert buttons() == ["Spark"]
    g.cap_flask("Flask 2", False)
    assert getattr(build, gear_mod.CAPPED_FLASKS) == {} and synced[-1] == {}


# --- the upkeep round keeps a skill only when its charged score beats off (the manager, 27 Nov 13:43; COV62) -----------

def _upkeep_search(monkeypatch, effects: dict, rows: dict, required=()):
    """A Search whose score is ln-like: the enabled side skills' effects plus ln(1 - the upkeep busy share)."""
    import math

    from builder.search import inner

    monkeypatch.setattr(inner, "UPKEEP_PRESS", True)
    enabled = {row["index"]: True for row in rows.values()}
    objective = SimpleNamespace(upkeep_busy=0.0, upkeep_rates={}, dps=lambda stats: math.exp(stats["score"]))
    build = SimpleNamespace(objective=objective, stats={"score": 0.0}, score=0.0,
                            ctx=SimpleNamespace(request=SimpleNamespace(require=SimpleNamespace(skills=list(required)))))
    names = {row["index"]: name for name, row in rows.items()}
    build.set_group = lambda index, enabled=True: enabled_set(index, enabled)

    def enabled_set(index, on):
        enabled[index] = on
    build.groups = lambda: [{"main": False, "enabled": enabled[i], "gems": [{"name": names[i]}]} for i in enabled]
    search = object.__new__(inner.Search)
    search.build = build
    search.upkeep_candidates = lambda: {n: dict(r) for n, r in rows.items() if enabled[r["index"]]}

    def rescore():
        score = sum(effects[names[i]] for i, on in enabled.items() if on) + math.log(1 - objective.upkeep_busy)
        build.stats, build.score = {"score": score}, score
        return score
    search._rescore = rescore
    return search, enabled


def test_a_kept_up_skill_whose_casts_cost_more_than_it_gives_is_switched_off(monkeypatch):
    """Disengage-like: worth +0.1 uncharged, but on its cooldown it takes 83% of the fight (ln(0.17) = -1.77): off. Its
    neighbour (worth +0.2 at 5% of the fight) stays charged. The guide names the dropped one."""
    rows = {"Disengage": {"index": 2, "cooldown": 1.2, "speed": 1.0, "_press": None},
            "Thunderstorm": {"index": 3, "duration": 4.0, "speed": 1.0, "_press": None}}
    search, enabled = _upkeep_search(monkeypatch, {"Disengage": 0.1, "Thunderstorm": 0.2}, rows)
    import builder.search.inner as inner
    monkeypatch.setattr(inner, "upkeep_use", lambda row: (1 / 1.2, "on its cooldown", 1.0) if row["index"] == 2
                        else (0.25, "once per its duration", 0.2))
    search.upkeep_round()
    assert enabled == {2: False, 3: True}
    assert set(search.upkeep_rows) == {"Thunderstorm"} and set(search.upkeep_dropped) == {"Disengage"}
    assert abs(search.build.objective.upkeep_busy - 0.05) < 1e-9
    monkeypatch.setattr(inner.play_guide, "PLAYER", "player", raising=False)
    labels = [r["label"] for r in search.upkeep_guide_rows()]
    assert "Disengage: not cast*" in labels


def test_a_kept_up_skill_whose_charged_score_beats_off_is_kept_and_a_required_one_is_never_dropped(monkeypatch):
    import builder.search.inner as inner

    rows = {"Disengage": {"index": 2, "cooldown": 1.2, "speed": 1.0, "_press": None}}
    monkeypatch.setattr(inner, "upkeep_use", lambda row: (1 / 1.2, "on its cooldown", 1.0))
    search, enabled = _upkeep_search(monkeypatch, {"Disengage": 0.1}, rows, required=["Disengage"])
    search.upkeep_round()
    assert enabled == {2: True} and set(search.upkeep_rows) == {"Disengage"}  # (required: stays, charged)
    monkeypatch.setattr(inner, "upkeep_use", lambda row: (0.1, "on its cooldown", 0.5))  # (5% of the fight)
    search, enabled = _upkeep_search(monkeypatch, {"Disengage": 0.1}, rows)
    search.upkeep_round()
    assert enabled == {2: True} and set(search.upkeep_rows) == {"Disengage"} and not search.upkeep_dropped


def test_the_button_check_reads_mech_cs_damage_flask_rows_too():
    from builder.request.constraints import damage_flask_button, player_buttons

    spark = {"name": "Spark", "support": False, "enabled": True, "skillTypes": ["Spell", "Damage", "Projectile"]}
    facts = {"skillGroups": [{"enabled": True, "main": True, "fromItem": False, "gems": [spark]}],
             "damageFlasks": [{"slot": "Flask 2", "name": "Melting Maelstrom", "damage": 1200.0},
                              {"slot": "Flask 1", "name": "Blood of the Warrior", "damage": 0.0},
                              {"slot": "Flask 3", "name": "Stimulant flask", "damage": None},
                              "Flask: Melting Maelstrom"]}
    assert player_buttons(facts) == ["Spark", "Flask: Melting Maelstrom", "Flask: Stimulant flask"]
    assert damage_flask_button("Lavianga's Spirits") == "Flask: Lavianga's Spirits"


def test_a_kept_up_skill_with_a_duration_longer_than_its_cooldown_is_cast_once_per_its_duration(monkeypatch):
    from builder.search import inner

    monkeypatch.setattr(inner, "press_action", lambda row: 0.5)
    uses, how, seconds = inner.upkeep_use({"speed": 2.0, "cooldown": 2.0, "duration": 8.0})
    assert abs(uses - 1 / 8) < 1e-9 and "8.00 s duration" in how and seconds == 0.5
    uses, how, _ = inner.upkeep_use({"speed": 2.0, "cooldown": 6.2, "duration": 4.0})  # (the cooldown is longer)
    assert abs(uses - 1 / 6.2) < 1e-9 and "cooldown" in how
    uses, _, _ = inner.upkeep_use({"speed": 2.0, "cooldown": 1.0, "duration": 3.0, "projectile": True})
    assert abs(uses - 1.0) < 1e-9
