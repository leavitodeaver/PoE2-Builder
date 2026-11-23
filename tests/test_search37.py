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


class _UnlockBuild:
    """A fake build: the score is a table of (ascendancy node set, regrown) -> score."""

    def __init__(self, table, alloc=(3,)):
        self.table, self.alloc, self.grown = table, frozenset(alloc), False
        self.score = self.real()

    def real(self):
        return self.table.get((self.alloc, self.grown), self.table.get((self.alloc, False), 10.0))

    def allocate(self, remove=(), add=()):
        self.alloc = (self.alloc - set(remove)) | frozenset(add)
        self.score = self.real()

    def snapshot(self):
        return (self.alloc, self.grown, self.score)

    def restore(self, snap):
        self.alloc, self.grown, self.score = snap


def _unlock_tree(build):
    from builder.search.tree import Tree

    tree = Tree.__new__(Tree)
    tree.build, tree.round, tree.resolve = build, 0, None

    def regrow_until(stop):
        build.grown = True
        build.score = build.real()

    tree.regrow_until = regrow_until
    return tree


def test_an_unlocking_ascendancy_notable_is_kept_only_if_its_regrow_beats_the_same_regrow_without_it(monkeypatch):
    """SEARCH37 (THE USER on the Oracle build, 23 Nov: The Unseen Path / Entwined Realities score 0 alone): the trial
    takes the allocation with the unlocking notable (2), regrows the tree, and keeps it only above both the build and the
    same regrow on the best allocation without it (3)."""
    from builder import clock

    monkeypatch.setattr(clock, "now", lambda: 0.0)
    U, C = frozenset({2}), frozenset({3})
    build = _UnlockBuild({(U, False): 10.0, (U, True): 20.0, (C, False): 10.0, (C, True): 12.0})
    assert _unlock_tree(build).unlock_trial([2], [3], [3], 100.0) is True
    assert build.alloc == U and build.score == 20.0  # (the unlock's regrow wins)
    build = _UnlockBuild({(U, True): 20.0, (C, True): 25.0})
    assert _unlock_tree(build).unlock_trial([2], [3], [3], 100.0) is True
    assert build.alloc == C and build.score == 25.0  # (the control's own regrow gains more: it is kept, not the unlock)
    build = _UnlockBuild({(U, True): 9.0})
    assert _unlock_tree(build).unlock_trial([2], [3], [3], 100.0) is False
    assert build.alloc == C and build.score == 10.0 and not build.grown  # (lower: all put back)
    build = _UnlockBuild({(U, True): 20.0})
    assert _unlock_tree(build).unlock_trial([2], [3], [3], 0.0) is False and build.alloc == C  # (no time left)


def test_the_unlock_line_finds_the_tree_unlocking_ascendancy_notables():
    from builder.search.tree import UNLOCK_LINE

    assert UNLOCK_LINE.search("Walk the Paths Not Taken")
    assert UNLOCK_LINE.search("Non-Keystone Passive Skills in Medium Radius of allocated Keystone Passive Skills can be "
                              "allocated without being connected to your tree")
    assert UNLOCK_LINE.search("Can Allocate Passive Skills from the Warrior's starting point")
    assert not UNLOCK_LINE.search("+200 to Armour for each Connected Notable Passive Skill Allocated")
    assert not UNLOCK_LINE.search("Grants Skill: Align Fate")


def test_the_oracles_hidden_passives_are_offered_only_after_the_unseen_path():
    """SEARCH37 (RESEARCH49 R49-9's open check): Oracle's 197 main-tree passives with PoB's unlockConstraint (The Unseen
    Path, node 5571) are on the tree's frontier only once The Unseen Path is allocated; so the tree step can take them
    after the ascendancy's unlock trial allocates it."""
    import json
    from pathlib import Path

    from builder.pob.engine import PobEngine

    tree = json.loads((Path(__file__).resolve().parents[1] / "vendor" / "PathOfBuilding-PoE2" / "src" / "TreeData" / "0_5"
                       / "tree.json").read_text(encoding="utf-8"))["nodes"]
    hidden = {int(k) for k, n in tree.items() if (n.get("unlockConstraint") or {}).get("ascendancy") == "Oracle"}
    assert len(hidden) == 197
    engine = PobEngine()
    try:
        engine.call("newBuild")
        engine.call("setClass", className="Druid", ascendancy="Oracle")
        reach = {"maxDistance": 400, "maxDistanceByType": {"Notable": 400, "Keystone": 400}}
        before = {n["id"] for n in engine.call("frontier", **reach)["nodes"]}
        engine.call("allocate", add=[5571])
        after = {n["id"] for n in engine.call("frontier", **reach)["nodes"]}
    finally:
        engine.close()
    assert not before & hidden
    assert len(after & hidden) > 100  # (every one the frontier can show: nodes with stat lines, not path-only images)


def test_a_chosen_megalomaniac_is_priced_by_its_roll_never_the_feeds_any_version_price():
    """SEARCH37 (THE USER, 23 Nov: a 3-notable Megalomaniac "would run at least 100div"; RESEARCH49 R49-10: poe.ninja
    prices every Megalomaniac at ~0.1 div): the search prices the chosen version at its exact trade price when a Trade
    click found one, else the user's labelled estimate; another versioned unique keeps the feed price marked UNKNOWN."""
    import time
    from types import SimpleNamespace

    from builder.data import unique_versions
    from builder.search.context import ItemOption
    from builder.search.jewels import Jewels
    from builder.search.radius import Variants

    names = ("Cooked", "Marked for Sickness", "Melting Flames")
    variants = Variants(names=names, slots=("a", "b", "c"), selected=(1, 2, 3), usable=(1, 2, 3))
    mega = ItemOption("Megalomaniac", "Diamond", "Jewel", None, "raw", True, 0.097, "low", 1, variants=names)
    jewels = Jewels.__new__(Jewels)
    jewels.ctx = SimpleNamespace(ssf=False, unique_versions={})
    est = jewels.rolled_price(mega, variants)
    assert est.price == 100.0 and est.confidence == "estimate (HIGH)" and "not searched yet" in est.price_note
    unlisted = {unique_versions.key("Megalomaniac", names): {"listed": 0, "price": None, "priced_at": time.time()}}
    jewels.ctx = SimpleNamespace(ssf=False, unique_versions=unlisted)
    assert "not listed when last searched" in jewels.rolled_price(mega, variants).price_note
    listed = {unique_versions.key("Megalomaniac", names): {"listed": 2, "price": 140.0, "priced_at": time.time()}}
    jewels.ctx = SimpleNamespace(ssf=False, unique_versions=listed)
    exact = jewels.rolled_price(mega, variants)
    assert exact.price == 140.0 and exact.confidence.startswith("trade, this version (2 listed")
    other = ItemOption("Against the Darkness", "Time-Lost Diamond", "Jewel", None, "raw", True, 1.0, "low", 1,
                       variants=names[:2])
    jewels.ctx = SimpleNamespace(ssf=False, unique_versions={})
    kept = jewels.rolled_price(other, variants)
    assert kept.price == 1.0 and "price UNKNOWN" in kept.price_note
    jewels.ctx = SimpleNamespace(ssf=True, unique_versions={})
    assert jewels.rolled_price(mega, variants) is mega
