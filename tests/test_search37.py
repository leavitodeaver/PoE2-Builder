"""SEARCH37: the narrow gear-first (POE2_SEARCH_GEAR_FIRST=empty), the manager's ruling on the 2b75e loss (POE2T7423:
the full form's round-2 tasks each ran a whole cut gear round and never reached supports)."""
from types import SimpleNamespace


def test_the_narrow_gear_first_works_only_the_empty_slots(monkeypatch):
    from builder import clock
    from builder.search import inner

    monkeypatch.setattr(clock, "now", lambda: 0.0)
    monkeypatch.setattr(inner, "PERSIST_LINES", True)

    def run(on, narrow, empty, gear_resume="Helmet"):
        monkeypatch.setattr(inner, "GEAR_FIRST", on)
        monkeypatch.setattr(inner, "GEAR_FIRST_EMPTY", narrow)
        ran = []

        def step(phase, fn, *args):
            ran.append((phase, args))
            return False

        fake = lambda *a, **k: None
        blocks = SimpleNamespace(**{n: fake for n in (
            "gear_round", "ascendancy_round", "optimize_supports", "grow", "attributes_round", "jewel_round", "round",
            "optimize_persistent", "granted_meta_round", "meta_round", "meta_spell_round", "totem_round", "mark_round",
            "mirage_round", "roar_round", "swap_round")}, resume=gear_resume)
        search = SimpleNamespace(passes=0, passes_done=0, line_passes=1, resume_step="supports", resume_sweep=True,
                                 current_step=None, kicked=set(), build=SimpleNamespace(equipped={}),
                                 gear=blocks, tree=blocks, gems=blocks, jewels=blocks, sources=blocks,
                                 conditions_round=fake, weapon_set_round=fake, tree_rebuild=fake, press_round=fake,
                                 skeleton=SimpleNamespace(swap=False), step=step,
                                 gear_census=lambda: {"empty": empty})
        inner.Search.blocks(search, deadline=100.0)
        return ran

    ran = run(True, True, ["Gloves", "Belt"])
    assert [p for p, _ in ran][:2] == ["gear", "supports"]
    assert ran[0][1][1] is True and ran[0][1][2] == ["Gloves", "Belt"]  # (the empty slots alone)
    full = run(True, False, ["Gloves", "Belt"])
    assert [p for p, _ in full][0] == "gear" and len(full[0][1]) == 2  # (the full form: no slot list)
    assert [p for p, _ in run(True, True, [])][0] == "supports"
    assert [p for p, _ in run(True, True, ["Gloves"], gear_resume=None)][0] == "supports"
    assert [p for p, _ in run(False, False, ["Gloves"])][0] == "supports"


def test_a_gear_round_on_some_slots_keeps_the_cut_round_and_skips_the_round_trials(monkeypatch):
    from builder import clock
    from builder.search.gear import Gear

    monkeypatch.setattr(clock, "now", lambda: 0.0)
    seen, trials = [], []
    build = SimpleNamespace(locked_slots=set(), equipped={}, score=1.0, worse=lambda before: False)
    gear = SimpleNamespace(
        build=build, slots=["Weapon 1", "Helmet", "Gloves", "Boots", "Belt"], resume="Helmet",
        sync_flasks=lambda: None, charges=SimpleNamespace(charm_limit=0), shortlist={}, rares={}, rare_tried=set(),
        candidates=lambda slot: seen.append(slot) or [], build_rare=lambda slot, deadline: None,
        deltas=lambda slot, raws: [], adjusted=lambda score, item: score,
        assumption_trials=lambda deadline: trials.append("sources") or False,
        character_rune_round=lambda deadline: trials.append("runes") or False,
        granted_meta_trials=lambda deadline: trials.append("metas") or False, fill_granted=None,
        update_lambda=lambda: trials.append("lambda"), _anoint_screen="kept")
    Gear.gear_round(gear, 100.0, True, ["Gloves", "Belt"])
    assert seen == ["Gloves", "Belt"] and gear.resume == "Helmet" and trials == [] and gear._anoint_screen == "kept"
    seen.clear()
    Gear.gear_round(gear, 100.0, True)  # (the whole round: from the cut slot, with its after-slot trials)
    assert seen == ["Helmet", "Gloves", "Boots", "Belt", "Weapon 1"] and gear.resume is None
    assert trials == ["runes", "lambda"]  # (a cut round going on skips the source-item trials: they ran when it began)


def test_the_gear_goes_last_only_when_the_resumed_pass_runs_out(monkeypatch):
    """POE2_SEARCH_GEAR_FIRST=last: the resumed pass's steps stop GEAR_SHARE of the task early; a pass that ends in time
    is untouched (its next pass starts with the gear step), one that doesn't gets the cut gear round in the time kept."""
    from builder import clock
    from builder.search import inner

    now = [0.0]
    monkeypatch.setattr(clock, "now", lambda: now[0])
    monkeypatch.setattr(inner, "PERSIST_LINES", True)
    monkeypatch.setattr(inner, "GEAR_FIRST", True)
    monkeypatch.setattr(inner, "GEAR_FIRST_EMPTY", False)
    monkeypatch.setattr(inner, "GEAR_FIRST_LAST", True)
    monkeypatch.setattr(inner, "GEAR_SHARE", 0.4)

    def run(per_step, empty=("Gloves",)):
        now[0] = 0.0
        ran = []

        def step(phase, fn, *args):
            ran.append((phase, args))
            now[0] += per_step
            return False

        fake = lambda *a, **k: None
        blocks = SimpleNamespace(**{n: fake for n in (
            "gear_round", "ascendancy_round", "optimize_supports", "grow", "attributes_round", "jewel_round", "round",
            "optimize_persistent", "granted_meta_round", "meta_round", "meta_spell_round", "totem_round", "mark_round",
            "mirage_round", "roar_round", "swap_round")}, resume="Body Armour")
        search = SimpleNamespace(passes=0, passes_done=0, line_passes=1, resume_step="supports", resume_sweep=True,
                                 current_step=None, kicked=set(), build=SimpleNamespace(equipped={}),
                                 gear=blocks, tree=blocks, gems=blocks, jewels=blocks, sources=blocks,
                                 conditions_round=fake, weapon_set_round=fake, tree_rebuild=fake, press_round=fake,
                                 skeleton=SimpleNamespace(swap=False), step=step, idle={}, skipped={},
                                 gear_census=lambda: {"empty": list(empty)})
        inner.Search.blocks(search, deadline=100.0)
        return ran, search

    ran, search = run(25.0)  # (the pass runs out at 75 > 60: the gear gets 75-100)
    phases = [p for p, _ in ran]
    assert phases[0] == "supports" and ran[0][1][0] == 60.0  # (the steps stop at 100 - 0.4 x 100)
    assert phases[-1] == "gear" and ran[-1][1] == (100.0, True) and search.passes_done == 0
    assert phases == ["supports", "tree rebuild", "tree", "gear"] and search.resume_step == "attributes"
    ran, search = run(0.0)  # (the pass ends in time: no gear step, the next pass starts with it)
    assert "gear" not in [p for p, _ in ran] and search.passes_done == 1
    ran, _ = run(25.0, empty=())  # (nothing empty: as gear-first off, the steps keep the whole task)
    assert ran[0][1][0] == 100.0 and "gear" not in [p for p, _ in ran]
