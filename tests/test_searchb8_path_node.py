"""SEARCHB8 (PREVIEW3's lead, 23 Nov): Tree.ascendancy_round on a build whose main-tree passives hang off a Pathfinder Path
node (Path of the Sorceress / Warrior: "Can Allocate Passive Skills from the <class>'s starting point").

PoB's DeallocNode drops a node's dependents with it (bridge.allocate), while a what-if that removes the node alone keeps
them (bridge.nodeDeltas' removeNodes). The round's what-if therefore counted passives a swap would lose, its commit
(remove every current ascendancy node, then add the new set) lost them even when the new set kept the Path node, and its
undo re-added only the ascendancy nodes."""
from types import SimpleNamespace

from builder.search.tree import Tree

# Ascendancy: 1 start, 2 Path Seeker (multiple choice), 3 Path of the Sorceress and 4 Passive Points (its options), 5 and
# 6 notables. Main tree: 100 class start, 101 a passive off it; 10 and 11 hang off node 3 only.
ASC = {1: [2, 5], 2: [1, 3, 4, 6], 3: [2], 4: [2], 5: [1], 6: [2]}
LINKS = {1: [2, 5], 2: [1, 3, 4, 6], 3: [2, 10], 4: [2], 5: [1], 6: [2], 10: [3, 11], 11: [10], 100: [101], 101: [100]}
VALUE = {2: 0.0, 3: 1.0, 4: 0.5, 5: 4.0, 6: 3.0, 10: 2.0, 11: 2.0, 101: 1.0}
MAIN = {100, 101, 10, 11}


class _PathBuild:
    """PoB's ways of taking a node off: allocate's DeallocNode drops its dependents (2 -> 3 -> 10, 11), a what-if
    doesn't, and allocating one option of Path Seeker takes the other off alone (DeallocSingleNode: 10 and 11 stay,
    with no route)."""

    def __init__(self, alloc: set[int]) -> None:
        self.alloc = set(alloc)
        self.score = self.value(self.alloc)
        self.locked_slots: set[str] = set()
        self.calls: list[tuple] = []

    @staticmethod
    def value(alloc: set[int]) -> float:
        return sum(VALUE.get(i, 0.0) for i in alloc)

    def call(self, method: str, **kwargs):
        if method == "conqueredNodes":
            return {"nodes": []}
        assert method == "facts"
        return {"ascendClassName": "Pathfinder", "className": "Ranger",
                "passives": [{"id": i} for i in sorted(self.alloc | {1})]}

    def node_deltas(self, cases):
        return [(self.value((self.alloc - set(c["remove"])) | set(c["add"])), {}) for c in cases]

    def allocate(self, remove=(), add=()):
        self.calls.append(("allocate", tuple(remove), tuple(add)))
        for node in remove:
            if node in self.alloc:
                self.alloc.discard(node)
                if node == 2:
                    self.alloc -= {3}
                if node in (2, 3):
                    self.alloc -= {10, 11}
        for node in add:
            sibling = {3: 4, 4: 3}.get(node)
            if sibling in self.alloc:
                self.alloc.discard(sibling)
            self.alloc.add(node)
        self.score = self.value(self.alloc)

    def snapshot(self):
        return (set(self.alloc), self.score)

    def restore(self, snap):
        self.alloc, self.score = set(snap[0]), snap[1]

    def worse(self, before: float) -> bool:
        return self.score < before - 1e-9


def _tree(build: _PathBuild, allocations: list[list[int]]) -> Tree:
    tree = Tree.__new__(Tree)
    nodes = {i: {"linked": LINKS.get(i, []), "stats": [], **({"ascendancy": "Pathfinder"} if i in ASC else {})}
             for i in set(LINKS)}
    nodes[1]["type"] = "AscendClassStart"
    tree.build, tree.round = build, 0
    tree.ctx = SimpleNamespace(nodes=nodes, ascendancy_graph=lambda name: (1, ASC),
                               data=SimpleNamespace(tree={"classes": [{"name": "Ranger", "startNode": 100}]}))
    tree.required_names, tree.resolve, tree.relevant_skill, tree.fill_granted, tree.enforce = set(), None, None, None, None
    tree.narrowed_allocations = lambda *args: allocations
    return tree


def test_the_passives_hanging_off_a_path_node_are_read_from_the_tree():
    build = _PathBuild({2, 3, 100, 101, 10, 11})
    tree = _tree(build, [])
    assert tree.path_anchored(build.call("facts"), ASC) == {3: {10, 11}}
    build = _PathBuild({2, 3, 100, 101})  # nothing hangs off it
    assert tree.path_anchored(build.call("facts"), ASC) == {}


def test_a_swap_away_from_the_path_node_is_scored_without_the_passives_it_would_drop():
    """Before: the what-if for notable 5 kept 10 and 11 (4 + 4 + 1 = 9 against 1 + 4 + 1 = 6 for the current set), the
    commit dropped them (real 5 < 6), and the undo re-added node 2 and 3 only: the build ended with 10 and 11 lost."""
    build = _PathBuild({2, 3, 100, 101, 10, 11})
    assert not _tree(build, [[2, 3], [5]]).ascendancy_round()
    assert build.alloc == {2, 3, 100, 101, 10, 11}


def test_a_new_set_keeping_the_path_node_keeps_its_passives():
    """Before: the commit took 2 and 3 off (DeallocNode dropped 10 and 11) and put 2, 3, 6 back: 10 and 11 were lost."""
    build = _PathBuild({2, 3, 100, 101, 10, 11})
    assert _tree(build, [[2, 3], [2, 3, 6], [5]]).ascendancy_round()
    assert build.alloc == {2, 3, 6, 100, 101, 10, 11}
    assert ("allocate", (2, 3), (2, 3, 6)) not in build.calls


def test_a_kept_swap_away_from_the_path_node_is_scored_for_real():
    """Notable 5 worth 6 beats the Path node's branch (1 + 4) even without it: kept, and 10 and 11 go with node 3."""
    VALUE[5] = 6.0
    try:
        build = _PathBuild({2, 3, 100, 101, 10, 11})
        assert _tree(build, [[2, 3], [5]]).ascendancy_round()
        assert build.alloc == {5, 100, 101}
    finally:
        VALUE[5] = 4.0


def test_a_build_with_no_passive_hanging_off_its_ascendancy_commits_as_before():
    build = _PathBuild({2, 3, 100, 101})
    assert _tree(build, [[2, 3], [5]]).ascendancy_round()
    assert build.alloc == {5, 100, 101} and build.calls == [("allocate", (2, 3), (5,))]


def test_a_swap_to_the_other_option_takes_the_path_nodes_passives_with_it():
    """Path of the Sorceress for its sibling option, worth 6: the old option goes through DeallocNode first, so 10 and
    11 go with it. Adding the sibling first would have taken node 3 off alone and left 10 and 11 with no route."""
    VALUE[4] = 6.0
    try:
        build = _PathBuild({2, 3, 100, 101, 10, 11})
        assert _tree(build, [[2, 3], [2, 4]]).ascendancy_round()
        assert build.alloc == {2, 4, 100, 101}
        assert build.calls == [("allocate", (3,), (4,))]
    finally:
        VALUE[4] = 0.5
