# PoE2 Builder

A Path of Exile 2 build optimizer driven by a headless Path of Building (PoB) engine. The spec
and roadmap are in [PLAN.md](PLAN.md). The old Rust attempt lives untracked in `legacy/`.

## Setup

Requires [uv](https://docs.astral.sh/uv/) and git. Windows only for now: the engine runs on
PoB's bundled 64-bit LuaJIT DLL.

```
uv sync
uv run python scripts/fetch_pob.py
```

## Use

Double-click **`PoE2 Builder.bat`** in the project folder: it starts the local server and opens
the page in your browser (keep its window open while you use it; close it to stop). From a
terminal:

```
uv run python -m builder.data dump     # export game data from the pinned PoB (after setup or a pin change)
uv run python -m builder.data prices   # refresh the poe.ninja price cache
uv run python -m builder.web           # open the local web page
uv run python -m builder.search tests/fixtures/requests/ice_shot_gemling.json --seconds 300
uv run pytest                          # request, constraint and search tests
```

The web page holds the request form and saved requests. **Optimize** runs the search on the
request in the form (progress streams into the page) and shows the result: Requested vs
Delivered, a PoB code verified by reloading it, a shopping list (every item with its target affixes,
tiers and roll ranges, an estimated price with its confidence, and the total against the budget),
skills, a test-in-game checklist for every number marked * or not modeled, the conditions the build
sustains and its campaign reward choices. Each shopping-list line has a **Trade** button that makes
an official trade-site search for it when clicked (never automatically) and opens it. For a unique
whose version the optimizer chose (Megalomaniac's notables, From Nothing's keystone, a ring size),
that click also prices the exact version, which poe.ninja can't, and the list keeps that price. Results are
also saved under `data/results/`. The page
also checks any PoB code or poe.ninja / pobb.in share link against the current request. Optional:
set `POE2_BUILDER_CONTACT` to add a contact to the User-Agent sent to poe.ninja and the trade
site, as their API docs ask.

Beside the request form (below it on a narrow screen) is the **passive tree**: scroll to zoom, drag to
move, search for a passive by name or effect, left click a notable or keystone to require it and right
click to ban it. It edits the same Passives lists as the form and follows them (small passives share
names, so only notables and keystones can be picked). It is drawn with the game's own node art, cut
once from the sprite sheets PoB ships and cached under `data/` (Grinding Gear Games' art, for local use
only; without PoB's sheets the nodes are plain dots). Ticking an ascendancy sets the class to match
(Any class when the ticked ones span classes), and changing the class or ascendancy drops required or
banned ascendancy passives that no longer fit, saying which. A few nodes beside the class starts are a
different passive for some classes (Vile Wounds is Eagle Eye for a Huntress; PoB applies that when the
class is selected): they are marked ⇄, the tree shows the version of the chosen class (or whichever you
pick), and requiring or banning one sets the class to match. A result shows the same tree read-only,
with the passives the build allocated. While a run is going, edits to the form reach it about a second and a half
after you stop typing: the search stops, keeps its best build, and continues from that build's PoB code
with the new settings for the time left (an edit with errors is reported and changes nothing). Under the
progress, a summary of the best build found so far updates each time a search round ends.

**Calibrate rare prices** (top of the page) prices about 24 representative rares on the official
trade site, paced by its rate limits (a few minutes), and fits the rare price model to them; the
next optimizer run uses it. Slots with too little data stay labelled as estimates.

Gear is normal bases, affordable uniques and rares built affix by affix, with runes, Soul Cores
and Idols in their sockets and jewels in the tree's sockets. Rare prices come from the calibrated
model (uncalibrated estimates until you calibrate), and flasks and charms aren't modeled yet. Any skill can be the main skill;
where PoB doesn't model a skill's trigger (e.g. Explosive Transmutation's Remnant pickups), the
result marks the assumed rate with an asterisk.

## Engine checks

```
uv run python benchmarks/make_test_builds.py   # regenerate benchmarks/builds/*.xml
uv run python benchmarks/run_gates.py          # fidelity, delta agreement, round trip, timings
uv run python benchmarks/bench_pool.py         # parallel what-if throughput by worker count
uv run python benchmarks/compare_versions.py   # dev snapshot vs PoB release, per build
uv run python benchmarks/run_optimizer.py      # optimizer vs naive baselines on four skeletons
uv run python benchmarks/fuzz_constraints.py   # gate 5: random requests, every result rechecked
```

`scripts/fetch_pob.py` checks out both the pinned dev snapshot (what the optimizer calculates
with) and the matching PoB release (what the PoB app runs, used for cross-checks).

To check fidelity against real builds, save PoB codes exported from the PoB app as
`benchmarks/reference/<name>.txt`. `run_gates.py --pob-dir vendor/PathOfBuilding-PoE2-<release>`
compares them with the release the app runs; `compare_versions.py` explains what the snapshot
changes.

## Layout

- `engine/bootstrap.lua`: boots PoB headless (mirrors PoB's HeadlessWrapper.lua)
- `engine/bridge.lua`: the only code touching PoB internals; JSON API for the Python side
- `engine/pob-pin.json`: the pinned PoB commit
- `builder/pob/`: ctypes host for PoB's LuaJIT, engine wrapper, worker pool (with a watchdog), build codes
- `builder/search/`: the optimizer: skeletons, gems, tree, gear, condition resolver, inner and outer loops
- `builder/web/`: the local web page and optimizer runs
- `benchmarks/`: test builds, gate and throughput scripts, results history
