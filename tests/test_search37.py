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
