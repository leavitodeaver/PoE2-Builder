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

```
uv run python -m builder.data dump     # export game data from the pinned PoB (after setup or a pin change)
uv run python -m builder.data prices   # refresh the poe.ninja price cache
uv run python -m builder.web           # open the local web page
uv run python -m builder.search tests/fixtures/requests/ice_shot_gemling.json --seconds 300
uv run pytest                          # request, constraint and search tests
```

The web page holds the request form and saved requests. **Optimize** runs the search on the
request in the form (progress streams into the page) and shows the result: Requested vs
Delivered, a PoB code verified by reloading it, gear with prices, skills, the conditions the build
sustains and its campaign reward choices. Results are also saved under `data/results/`. The page
also checks any PoB code or poe.ninja / pobb.in share link against the current request. Optional:
set `POE2_BUILDER_CONTACT` to add a contact to the User-Agent sent to poe.ninja, as their API
docs ask.

Gear is uniques plus normal bases for now (rares arrive in Phase 3), so requests with resistance
floors or high DPS gates often come back infeasible, with the reason and the cheapest relaxation.

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
